"""Diff-aware test selection: which tests a change plausibly reaches.

Running a whole suite for a one-line change is the cost this exists to remove,
and the honesty of the answer is the whole of its value. The report is in three
tiers and the separation between them is the point:

  Changed             -- what the diff says. Facts.
  Direct matches      -- a test that names a changed module, symbol or file,
                         with the evidence quoted beside it.
  Possibly affected   -- reachability guesses. Labelled as guesses, always.

A guess presented as a measurement is worse than no selection at all, because
whoever reads it then skips the tests it never had evidence for. Nothing here
invents a file name: a changed module with no test file is reported as having
none rather than pointed at a test_*.py that does not exist.

The diff comes from agent_git.TMTGit -- the same machinery git_diff uses -- so
there is one place in this project that decides how git is run.
"""

import re
from pathlib import Path

from agent_file_ops import WORKSPACE_SKIP, iter_workspace_files, safe_path, workspace

# Ceilings. A selector that returns a hundred lines has reproduced the problem
# it was built to solve, so every section is capped and the cap is announced.
MAX_OUTPUT_LINES = 70
MAX_CHANGED_FILES = 30
MAX_SYMBOLS_PER_FILE = 10
MAX_TEST_FILES = 500
MAX_TESTS_PER_FILE = 8
MAX_HEURISTICS = 12
MAX_REASONS = 3
# Two-character names ("id", "os") match half of every file they are searched
# in, so a match on one is not evidence of anything.
MIN_SYMBOL_LENGTH = 3

NO_REPOSITORY = (
    "No test selection: there is no git repository here, so there is no diff to "
    "read. Nothing is claimed about which tests are affected."
)
NO_CHANGES = (
    "No test selection: the working tree matches the index and HEAD, so the diff "
    "is empty. There is no changed file to map a test to."
)


# --- reading the diff -------------------------------------------------------

_FILE_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_NEW_PATH = re.compile(r"^\+\+\+ b/(.*)$")
_HUNK = re.compile(r"^@@ -(?:\d+)(?:,\d+)? \+(\d+)(?:,(\d+))? @@(.*)$")

# Declarations, read off the changed lines themselves. Python is what this repo
# is; the others are here so a mixed workspace degrades to something rather
# than to nothing.
_DECLARATIONS = (
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)"),
    re.compile(r"^\s*class\s+([A-Za-z_]\w*)"),
    re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)"),
    re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?(?:function\b|\()"),
)


def _declared(line):
    """Every symbol a single line declares, in the order the patterns run."""
    found = []
    for pattern in _DECLARATIONS:
        match = pattern.search(line)
        if match and match.group(1) not in found:
            found.append(match.group(1))
    return found


def _quoted(path):
    """git quotes a path containing unusual bytes; the quotes are not the name."""
    text = path.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    return text.replace("\\", "/")


class ChangedFile:
    """One file the diff names, and what the diff says about it.

    `symbols` maps a name to the reason it is believed changed. The reason is
    kept because it is what the report prints: a symbol with no account of how
    it was found is exactly the unevidenced claim this module exists to avoid.
    """

    def __init__(self, path):
        self.path = path
        self.hunks = 0
        self.new_lines = []
        self.symbols = {}

    def note(self, name, reason):
        # First reason wins. They are recorded strongest first -- a declaration
        # on a changed line beats a hunk header, which beats an enclosure --
        # so overwriting would trade a fact for an inference.
        if len(name) < MIN_SYMBOL_LENGTH or name in self.symbols:
            return
        if len(self.symbols) < MAX_SYMBOLS_PER_FILE:
            self.symbols[name] = reason

    @property
    def stem(self):
        return Path(self.path).stem

    @property
    def name(self):
        return Path(self.path).name


def parse_diff(diff):
    """Split unified diff text into ChangedFile records, in path order.

    Reads the hunk headers as well as the changed lines: git puts the enclosing
    declaration after the second `@@`, which names the function a body-only
    edit sits in when nothing on the changed lines does.
    """
    files = {}
    current = None
    new_line = 0
    for raw in (diff or "").splitlines():
        header = _FILE_HEADER.match(raw)
        if header:
            path = _quoted(header.group(2))
            current = files.setdefault(path, ChangedFile(path))
            new_line = 0
            continue
        renamed = _NEW_PATH.match(raw)
        if renamed:
            path = _quoted(renamed.group(1))
            # /dev/null is a deletion: there is no new-side file to name.
            if path and path != "/dev/null" and current is not None and path != current.path:
                current = files.setdefault(path, ChangedFile(path))
            continue
        if raw.startswith("--- "):
            continue
        hunk = _HUNK.match(raw)
        if hunk:
            if current is None:
                continue
            current.hunks += 1
            new_line = int(hunk.group(1))
            for name in _declared(hunk.group(3)):
                current.note(name, "named in the hunk header git wrote")
            continue
        if current is None:
            continue
        if raw.startswith("+"):
            for name in _declared(raw[1:]):
                current.note(name, "declared on a changed line")
            if len(current.new_lines) < 400:
                current.new_lines.append(new_line)
            new_line += 1
        elif raw.startswith("-"):
            for name in _declared(raw[1:]):
                current.note(name, "declared on a changed line")
        elif raw.startswith(" "):
            new_line += 1
    return [files[key] for key in sorted(files)]


# --- naming the symbol a changed line sits in -------------------------------

def _symbol_reader():
    """agent_symbols.symbols_in, if that module is importable right now.

    Imported here rather than at module scope, and on every call rather than
    once: it is built separately from this file, so it may be absent, and it
    may be half-written at the moment this module first loads. Absent is not a
    failure -- _scan_definitions does the same job less well, and the report
    never depends on which of the two answered.
    """
    try:
        import agent_symbols
    except Exception:
        return None
    reader = getattr(agent_symbols, "symbols_in", None)
    return reader if callable(reader) else None


_SCAN_DEFINITION = re.compile(
    r"^(?:\s*)(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)"
)


def _scan_definitions(path):
    """[(line, name)] for a file, by regex. The fallback, not the preference."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        match = _SCAN_DEFINITION.match(line)
        if match:
            found.append((number, match.group(2)))
    return found


def definitions(path):
    """[(line, name)] for a file, from agent_symbols when it is there."""
    reader = _symbol_reader()
    if reader is not None:
        try:
            entries = reader(str(path))
        except Exception:
            entries = None
        if isinstance(entries, list):
            found = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                name, line = entry.get("name"), entry.get("line")
                if isinstance(name, str) and isinstance(line, int):
                    found.append((line, name))
            if found:
                return sorted(found)
    return _scan_definitions(path)


def _enclosing(record):
    """Attribute changed line numbers to the definition that precedes them.

    Only reached when neither the changed lines nor the hunk header named
    anything, which is the body-only edit in a language git has no funcname
    pattern for. The attribution is an inference and says so in its reason:
    the nearest preceding definition is not necessarily the enclosing one.
    """
    try:
        target = safe_path(record.path)
    except ValueError:
        return                              # the diff reaches outside the workspace
    if not target.is_file():
        return
    table = definitions(target)
    if not table:
        return
    for line in record.new_lines:
        candidate = None
        for start, name in table:
            if start <= line:
                candidate = name
            else:
                break
        if candidate:
            record.note(candidate, f"nearest definition above changed line {line}")


# --- the workspace's test files ---------------------------------------------

def _is_test_file(name):
    return name.startswith("test_") and name.endswith(".py")


def test_files():
    """{relative posix path: absolute path} for every test_*.py in the workspace."""
    found = {}
    for relative, absolute in iter_workspace_files():
        if len(found) >= MAX_TEST_FILES:
            break
        if _is_test_file(absolute.name):
            found[relative.as_posix()] = absolute
    return found


_TOP_LEVEL = re.compile(r"^(?:async\s+def|def|class)\b", re.MULTILINE)
_TEST_DEF = re.compile(r"^def\s+(test_\w+)", re.MULTILINE)


def test_functions(text):
    """[(name, body)] for the module-level def test_* in a file.

    Column-zero definitions only, because that is exactly what run_tests.py
    collects: a nested helper called test_something is never run by the suite
    and naming it would send the reader after a test that does not exist.
    """
    boundaries = [match.start() for match in _TOP_LEVEL.finditer(text)]
    blocks = []
    for match in _TEST_DEF.finditer(text):
        start = match.start()
        end = len(text)
        for position in boundaries:
            if position > start:
                end = position
                break
        blocks.append((match.group(1), text[start:end]))
    return blocks


def _mentions(text, name):
    return re.search(r"\b" + re.escape(name) + r"\b", text) is not None


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# --- matching ---------------------------------------------------------------

class Match:
    """A candidate test file, its reasons, and the tests inside it that named
    a changed symbol. A Match with no reasons is never built."""

    def __init__(self, path):
        self.path = path
        self.reasons = []
        self.tests = []

    def because(self, reason):
        if reason not in self.reasons:
            self.reasons.append(reason)


def _direct_matches(changed, candidates):
    """Test files with evidence, keyed by path.

    Every reason recorded here is checkable by the reader: a file exists under
    the name the convention predicts, a test file imports or names the changed
    module, or a named test function mentions a changed symbol. Nothing enters
    this tier on plausibility alone.
    """
    matches = {}

    def match_for(path):
        return matches.setdefault(path, Match(path))

    changed_paths = {record.path for record in changed}
    by_name = {}
    for path in candidates:
        by_name.setdefault(Path(path).name, path)

    for record in changed:
        if _is_test_file(record.name):
            if record.path in candidates:
                match_for(record.path).because("is itself changed by this diff")
            continue
        if not record.path.endswith(".py"):
            continue
        conventional = "test_" + record.name
        path = by_name.get(conventional)
        if path:
            match_for(path).because(
                f"named {conventional} for the changed {record.name}, "
                "which is this repo's test naming convention"
            )

    modules = sorted({record.stem for record in changed if record.path.endswith(".py")})
    symbols = {}
    for record in changed:
        for name in record.symbols:
            symbols.setdefault(name, record.name)

    for path, absolute in sorted(candidates.items()):
        if path in changed_paths and path not in matches:
            continue
        text = _read(absolute)
        if not text:
            continue
        for module in modules:
            if module == Path(path).stem:
                continue                    # already covered by the convention
            if re.search(r"^\s*(?:import|from)\s+" + re.escape(module) + r"\b",
                         text, re.MULTILINE):
                match_for(path).because(f"imports {module}, which changed")
            elif _mentions(text, module):
                match_for(path).because(f"names {module}, which changed")
        if not symbols:
            continue
        for name, body in test_functions(text):
            named = [symbol for symbol in sorted(symbols) if _mentions(body, symbol)]
            if not named:
                continue
            entry = match_for(path)
            if len(entry.tests) < MAX_TESTS_PER_FILE:
                quoted = ", ".join("`%s`" % symbol for symbol in named)
                entry.tests.append((name, "names " + quoted))
    return {path: entry for path, entry in matches.items() if entry.reasons or entry.tests}


_IMPORTS = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_]\w*)", re.MULTILINE)


def _heuristics(changed, candidates, direct):
    """Guesses: a test whose own module imports something that changed.

    One hop, no further. This is reachability, not evidence -- test_agent_ui
    can import a module that changed and never execute a line of it -- so it is
    reported apart from the direct tier and labelled every time it is printed.
    """
    modules = {record.stem for record in changed if record.path.endswith(".py")}
    if not modules:
        return []
    by_name = {}
    for path in candidates:
        by_name.setdefault(Path(path).name, path)
    guesses = []
    seen = set()
    for relative, absolute in iter_workspace_files():
        if len(guesses) >= MAX_HEURISTICS:
            break
        name = absolute.name
        if not name.endswith(".py") or _is_test_file(name):
            continue
        stem = Path(name).stem
        if stem in modules:
            continue                        # it changed; that is a fact, not a guess
        target = by_name.get("test_" + name)
        if not target or target in direct or target in seen:
            continue
        imported = set(_IMPORTS.findall(_read(absolute)))
        reached = sorted(imported & modules)
        if not reached:
            continue
        seen.add(target)
        guesses.append((
            target,
            f"{name} imports {', '.join(reached)}, which changed -- whether any "
            "test in this file reaches that code is unverified",
        ))
    return guesses


# --- the report -------------------------------------------------------------

def _cap(lines):
    """Trim to MAX_OUTPUT_LINES and say so. Silence would read as completeness."""
    if len(lines) <= MAX_OUTPUT_LINES:
        return "\n".join(lines)
    shown = lines[:MAX_OUTPUT_LINES]
    omitted = len(lines) - len(shown)
    shown.append(
        f"... output capped at {MAX_OUTPUT_LINES} lines; {omitted} further lines "
        "omitted. Narrow it with a path to see the rest."
    )
    return "\n".join(shown)


def _read_diff(target):
    """(diff, error). Reuses agent_git so there is one way git is run here.

    agent_git is imported inside the call for the reason agent_actions._run_git
    imports it there: a missing or broken git module must come back as a result
    the caller can print, not as an exception out of a reporting tool.
    """
    try:
        import agent_git
    except Exception as error:
        return None, f"No test selection: git support is unavailable ({error})."
    try:
        repository = agent_git.TMTGit.discover()
        return repository.diff(paths=[str(target)] if target else None), None
    except Exception as error:
        text = str(error)
        if "not inside a git repository" in text or "no repository" in text:
            return None, NO_REPOSITORY
        if "was not found on PATH" in text:
            return None, "No test selection: git was not found on PATH."
        return None, f"No test selection: the diff could not be read ({text})."


def related_tests(path=None):
    """Tests that a change reaches, in three tiers: fact, evidence, guess.

    `path` narrows the diff to one file or directory and goes through
    safe_path, whose ValueError propagates -- a path outside the workspace is
    the caller's mistake to see, not something to quietly widen.

    Returns a plain report and never raises for the ordinary failures: no
    repository and no changes are both answers, and both say so in words.
    """
    target = safe_path(path) if path else None
    diff, error = _read_diff(target)
    if error:
        return error
    if not diff or diff.strip() == "(no changes)":
        if target:
            return NO_CHANGES + f" (looked only at {path})"
        return NO_CHANGES

    changed = parse_diff(diff)
    if not changed:
        return NO_CHANGES + " No file header appeared in the diff git returned."
    truncated = "... (truncated)" in diff
    for record in changed:
        if not record.symbols:
            _enclosing(record)

    candidates = test_files()
    direct = _direct_matches(changed, candidates)
    guesses = _heuristics(changed, candidates, direct)

    lines = ["Test selection for " + (path if path else "the whole working tree") + "."]
    if truncated:
        lines.append(
            "The diff itself was truncated before it was read, so files below "
            "the cut are missing from all three sections."
        )

    lines.append("")
    lines.append("Changed (read from the diff -- facts):")
    for record in changed[:MAX_CHANGED_FILES]:
        detail = f"  {record.path} -- {record.hunks} hunk(s)"
        if record.symbols:
            named = ", ".join(
                f"{name} ({reason})" for name, reason in sorted(record.symbols.items())
            )
            detail += f"; symbols: {named}"
        else:
            detail += "; no symbol could be read from the diff for this file"
        lines.append(detail)
    if len(changed) > MAX_CHANGED_FILES:
        lines.append(
            f"  ... {len(changed) - MAX_CHANGED_FILES} further changed file(s) not listed."
        )

    lines.append("")
    lines.append("Direct matches (the evidence is stated for each):")
    if direct:
        for path_name in sorted(direct):
            entry = direct[path_name]
            if entry.reasons:
                lines.append(f"  {path_name} -- " + "; ".join(entry.reasons[:MAX_REASONS]))
            else:
                lines.append(f"  {path_name}")
            for test_name, reason in entry.tests:
                lines.append(f"      {test_name} -- {reason}")
    else:
        lines.append("  None. No test file names a changed module or symbol.")

    missing = [
        record.name for record in changed
        if record.path.endswith(".py") and not _is_test_file(record.name)
        and not any("test_" + record.name == Path(p).name for p in direct)
    ]
    for name in missing[:MAX_CHANGED_FILES]:
        lines.append(
            f"  (no test_{name} exists in this workspace; none was invented for it)"
        )

    lines.append("")
    lines.append("Possibly affected (guesses, not evidence -- verify before trusting):")
    if guesses:
        for path_name, reason in guesses:
            lines.append(f"  {path_name} -- guess: {reason}")
    else:
        lines.append("  None suggested.")

    lines.append("")
    lines.append(
        "Only the first section is measured. The second is name evidence, not "
        "proof of coverage, and the third is a guess."
    )
    return _cap(lines)
