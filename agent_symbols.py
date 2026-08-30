"""Code-aware symbol extraction and search.

Two tiers, and the gap between them is never blurred. Python is parsed with
`ast`, so what comes back is a fact about the program. Everything else is
matched with regexes over raw text, so what comes back is a guess about the
characters. A guess dressed as a fact is worse than no answer at all, because
the model spends the same confidence on it either way -- so every symbol
carries the tier it came from and every rendered line says which one it was.

Languages live in one table. Adding Ruby or Kotlin is an entry in LANGUAGES,
not a new branch in the extractor: the moment support means writing code, the
next language does not get added.
"""

import ast
import re
from pathlib import Path

import agent_config
from agent_file_ops import iter_workspace_files, safe_path, workspace

# The two tiers, spelled once. Callers compare against these rather than
# against the strings, so renaming a tier cannot leave half the codebase
# reading the old word.
TIER_STRUCTURAL = "structural"
TIER_HEURISTIC = "heuristic"

# find_symbol is read by a model with a finite context, so it is capped. It
# says so when it caps -- a truncated list that claims to be complete sends
# the reader off believing a symbol has one definition when it has nine.
MAX_SYMBOL_HITS = 40
MAX_UNPARSED_REPORTED = 12

# A minified bundle or a generated table is technically source and is never
# worth the regex pass. Read as bytes first so a 4MB file is not decoded
# before being rejected.
MAX_FILE_BYTES = 400_000

# Names the lexical tier will never report, whatever a pattern says. Every
# c-like and JS "method" pattern also matches `if (x) {` and `catch (e) {`,
# because at the level of characters those lines are the same shape as a
# method. Filtering the names is one list; teaching each pattern to exclude
# them is the same list copied into eight regexes.
NON_SYMBOL_NAMES = {
    "if", "for", "while", "switch", "catch", "return", "do", "else", "try",
    "with", "new", "typeof", "case", "await", "yield", "elif", "except",
    "finally", "sizeof", "defer", "go", "match", "when", "using", "lock",
    "foreach", "unless", "print",
}


# --- the language table -----------------------------------------------------
#
# One entry per language family. "patterns" is ordered and the FIRST pattern
# that matches a line wins, so the specific forms come before the general
# ones: `const enum Colour` must be read as an enum before the const/arrow
# rule gets to call it a constant.
#
# "reject" is matched against the whole raw line and skips it outright. It
# exists for the line shapes that no pattern should ever be offered.

LANGUAGES = {
    "python": {
        "extensions": (".py", ".pyw", ".pyi"),
        # The only structural entry. The flag is here rather than implied by
        # the name so a second parsed language is a table change like the rest.
        "structural": True,
        "patterns": (
            ("import", r"^\s*(?:from\s+(?P<name>[\w\.]+)\s+import\b|import\s+(?P<name2>[\w\.]+))"),
            ("class", r"^\s*class\s+(?P<name>\w+)"),
            ("function", r"^\s*(?:async\s+)?def\s+(?P<name>\w+)"),
            ("constant", r"^(?P<name>[A-Z_][A-Z0-9_]*)\s*(?::[^=]+)?="),
        ),
    },
    "javascript": {
        # TypeScript shares every pattern here; the extra kinds it has
        # (interface, type, enum) simply never fire in a .js file.
        "extensions": (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"),
        "patterns": (
            ("import", r"^\s*import\s+(?:[^'\"]*?\s+from\s+)?['\"](?P<name>[^'\"]+)['\"]"),
            ("import", r"^\s*(?:export\s+)?(?:const|let|var)?[^=]*=?\s*require\(\s*['\"](?P<name>[^'\"]+)['\"]\s*\)"),
            ("import", r"^\s*export\s+(?:\*|\{[^}]*\})\s+from\s+['\"](?P<name>[^'\"]+)['\"]"),
            ("interface", r"^\s*(?:export\s+)?(?:declare\s+)?interface\s+(?P<name>[A-Za-z_$][\w$]*)"),
            ("enum", r"^\s*(?:export\s+)?(?:declare\s+)?(?:const\s+)?enum\s+(?P<name>[A-Za-z_$][\w$]*)"),
            ("type", r"^\s*(?:export\s+)?(?:declare\s+)?type\s+(?P<name>[A-Za-z_$][\w$]*)"),
            ("class", r"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)"),
            ("function", r"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>[A-Za-z_$][\w$]*)"),
            ("function", r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?::[^=]*)?=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=]*)?=>"),
            ("constant", r"^\s*(?:export\s+)?const\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?::[^=]*)?="),
            ("method", r"^[ \t]+(?:(?:public|private|protected|static|readonly|abstract|override|async|get|set)\s+)*(?P<name>[A-Za-z_$][\w$]*)\s*\([^;()]*\)\s*(?::[^{;]+)?\{"),
        ),
        "reject": r"^\s*(?://|/\*|\*)",
    },
    "go": {
        "extensions": (".go",),
        "patterns": (
            ("method", r"^func\s+\([^)]*\)\s*(?P<name>\w+)"),
            ("function", r"^func\s+(?P<name>\w+)"),
            ("struct", r"^\s*type\s+(?P<name>\w+)\s+struct\b"),
            ("interface", r"^\s*type\s+(?P<name>\w+)\s+interface\b"),
            ("type", r"^\s*type\s+(?P<name>\w+)\b"),
            ("constant", r"^(?:const|var)\s+(?P<name>\w+)"),
            # Inside a `import (...)` block each line is nothing but an
            # optional alias and a quoted path, which is specific enough to
            # match on its own -- an assignment carries a `:=` or an `=` and
            # so cannot reach this.
            ("import", r"^\s*(?:[\w\.]+\s+)?\"(?P<name>[\w\./\-]+)\"\s*$"),
        ),
        "reject": r"^\s*//",
    },
    "rust": {
        "extensions": (".rs",),
        "patterns": (
            ("import", r"^\s*(?:pub\s+)?use\s+(?P<name>[\w:]+)"),
            ("function", r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:default\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?(?:extern\s+\"[^\"]*\"\s+)?fn\s+(?P<name>\w+)"),
            ("struct", r"^\s*(?:pub(?:\([^)]*\))?\s+)?struct\s+(?P<name>\w+)"),
            ("enum", r"^\s*(?:pub(?:\([^)]*\))?\s+)?enum\s+(?P<name>\w+)"),
            ("trait", r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:unsafe\s+)?trait\s+(?P<name>\w+)"),
            # `impl Trait for Type` names the type, because that is what a
            # reader searching for `impl Foo` is looking for.
            ("impl", r"^\s*impl(?:<[^>]*>)?\s+(?:[\w:<>]+\s+for\s+)?(?P<name>\w+)"),
            ("module", r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+(?P<name>\w+)"),
            ("type", r"^\s*(?:pub(?:\([^)]*\))?\s+)?type\s+(?P<name>\w+)"),
            ("constant", r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const|static)\s+(?:mut\s+)?(?P<name>\w+)"),
        ),
        "reject": r"^\s*(?://|#\[)",
    },
    "clike": {
        # Java, C#, C, C++ and their headers. They differ in keywords, not in
        # the shape of a definition, so one entry covers them and the kinds a
        # given language lacks simply never match.
        "extensions": (".java", ".cs", ".c", ".h", ".cc", ".cpp", ".cxx",
                       ".hpp", ".hh", ".hxx", ".m", ".mm"),
        "patterns": (
            ("import", r"^\s*import\s+(?:static\s+)?(?P<name>[\w\.\*]+)\s*;"),
            ("import", r"^\s*#include\s*[<\"](?P<name>[^>\"]+)[>\"]"),
            ("import", r"^\s*using\s+(?:static\s+)?(?P<name>[\w\.]+)\s*;"),
            ("namespace", r"^\s*namespace\s+(?P<name>[\w\.:]+)"),
            ("class", r"^\s*(?:(?:public|private|protected|internal|abstract|final|sealed|static|partial|template|export)\s+)*class\s+(?P<name>\w+)"),
            ("interface", r"^\s*(?:(?:public|private|protected|internal|abstract|partial)\s+)*interface\s+(?P<name>\w+)"),
            ("enum", r"^\s*(?:(?:public|private|protected|internal)\s+)*enum(?:\s+class)?\s+(?P<name>\w+)"),
            ("struct", r"^\s*(?:(?:public|private|protected|internal|typedef)\s+)*struct\s+(?P<name>\w+)"),
            ("record", r"^\s*(?:(?:public|private|protected|internal|sealed)\s+)*record\s+(?P<name>\w+)"),
            # Last, and the loosest: a return type, a name, a parenthesised
            # list, and nothing after the paren that would make it a call.
            # Anything ending in `;` inside the parens (a `for` header) or
            # opening with a control keyword is already gone by here.
            ("function", r"^[ \t]*(?:(?:public|private|protected|internal|static|final|abstract|virtual|override|sealed|synchronized|inline|extern|unsafe|async|explicit|friend|constexpr)\s+)*(?:[\w:<>\[\]\*&,\.]+\s+)+(?P<name>~?\w+)\s*\([^;]*\)\s*(?:const\s*)?(?:noexcept\s*)?(?:override\s*)?(?:throws\s+[\w,\s\.]+)?\{?\s*$"),
        ),
        "reject": r"^\s*(?://|/\*|\*|#(?!include)|return\b|else\b|case\b|goto\b)",
    },
    "generic": {
        # The fallback. It knows the three spellings of "definition" that most
        # scripting languages share and nothing else, which is exactly the
        # right amount for a language nobody has written an entry for yet.
        "extensions": (".rb", ".php", ".sh", ".bash", ".zsh", ".lua", ".pl",
                       ".pm", ".r", ".jl", ".kt", ".kts", ".swift", ".scala",
                       ".groovy", ".dart", ".ex", ".exs", ".vb", ".ps1"),
        "patterns": (
            ("class", r"^\s*(?:(?:public|private|open|final|abstract|data|sealed|internal)\s+)*class\s+(?P<name>[\w\.]+)"),
            ("function", r"^\s*(?:(?:public|private|open|final|static|override|suspend|async|export|local)\s+)*(?:def|fun|func|function|sub|defp)\s+(?P<name>[\w\.!?]+)"),
            ("function", r"^\s*(?P<name>\w+)\s*\(\s*\)\s*\{"),
            ("constant", r"^(?P<name>[A-Z_][A-Z0-9_]*)\s*="),
        ),
        "reject": r"^\s*(?:#|//)",
    },
}


def _build_extension_map():
    """Extension -> language name, derived from the table.

    Derived rather than written out, so a language whose extensions are added
    to LANGUAGES is reachable immediately instead of being extracted correctly
    and then never looked up.
    """
    mapping = {}
    for language, entry in LANGUAGES.items():
        for extension in entry["extensions"]:
            mapping[extension] = language
    return mapping


EXTENSIONS = _build_extension_map()


def _compiled(language):
    """The compiled patterns for one language, compiled once and kept.

    A full workspace pass runs these over every line of every file; compiling
    them per file turned a scan into a re.compile benchmark.
    """
    entry = LANGUAGES[language]
    compiled = entry.get("_compiled")
    if compiled is None:
        compiled = tuple((kind, re.compile(pattern))
                         for kind, pattern in entry["patterns"])
        entry["_compiled"] = compiled
        reject = entry.get("reject")
        entry["_reject"] = re.compile(reject) if reject else None
    return compiled, entry["_reject"]


def detect_language(path):
    """The language of a path by extension, or "" when it is not code.

    "" is the honest answer for a .png or a .lock, and callers use it to skip
    the file entirely rather than run a regex pass over a binary.
    """
    return EXTENSIONS.get(Path(str(path)).suffix.lower(), "")


def is_code_file(path):
    return bool(detect_language(path))


def is_structural(language):
    return bool(LANGUAGES.get(language, {}).get("structural"))


# --- the structural tier ----------------------------------------------------

def _python_symbols(tree, relative, lines):
    """Every definition, assignment and import in a parsed Python module.

    Methods are qualified as Class.method because an unqualified `run` in a
    project with nine classes names nothing. The qualifier is built on the way
    down rather than searched for afterwards.
    """
    found = []

    def text_at(lineno):
        return lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""

    def add(name, kind, lineno, extra=None):
        symbol = {
            "name": name,
            "kind": kind,
            "line": lineno,
            "path": relative,
            "language": "python",
            "tier": TIER_STRUCTURAL,
            "text": text_at(lineno),
        }
        if extra:
            symbol.update(extra)
        found.append(symbol)

    def walk(body, prefix, inside_class):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + node.name
                add(name, "method" if inside_class else "function", node.lineno)
                # Nested defs keep the enclosing function out of the prefix: a
                # closure is not addressable from outside, so qualifying it
                # would invent a name that cannot be imported.
                walk(node.body, prefix, False)
            elif isinstance(node, ast.ClassDef):
                name = prefix + node.name
                add(name, "class", node.lineno)
                walk(node.body, name + ".", True)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    add(alias.name, "import", node.lineno,
                        {"module": alias.name.split(".")[0]})
            elif isinstance(node, ast.ImportFrom):
                module = node.module or "."
                for alias in node.names:
                    add(module + "." + alias.name, "import", node.lineno,
                        {"module": module.split(".")[0]})
            elif isinstance(node, ast.Assign) and not inside_class and not prefix:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        add(target.id,
                            "constant" if target.id.isupper() else "variable",
                            node.lineno)
            elif isinstance(node, ast.AnnAssign) and not inside_class and not prefix:
                if isinstance(node.target, ast.Name):
                    add(node.target.id,
                        "constant" if node.target.id.isupper() else "variable",
                        node.lineno)

    walk(tree.body, "", False)
    return found


# --- the lexical tier -------------------------------------------------------

def _lexical_symbols(language, relative, lines):
    """Regex matches, one pass, first pattern wins per line.

    First-wins is deliberate: a line matching both the enum rule and the
    const rule is one definition, and reporting it twice would make the
    header's match count a lie.
    """
    patterns, reject = _compiled(language)
    found = []
    for index, line in enumerate(lines, 1):
        if not line.strip():
            continue
        if reject is not None and reject.match(line):
            continue
        for kind, pattern in patterns:
            match = pattern.match(line)
            if not match:
                continue
            name = match.groupdict().get("name") or match.groupdict().get("name2")
            if not name or name in NON_SYMBOL_NAMES:
                break
            found.append({
                "name": name,
                "kind": kind,
                "line": index,
                "path": relative,
                "language": language,
                "tier": TIER_HEURISTIC,
                "text": line.strip()[:200],
            })
            break
    return found


# --- reading and scanning ---------------------------------------------------

def _relative(target):
    try:
        return str(Path(target).relative_to(workspace())).replace("\\", "/")
    except ValueError:
        return str(target).replace("\\", "/")


def _read_source(target):
    """(lines, error). A file that cannot be read is an error, never silence.

    Returned rather than raised: one unreadable file in a workspace scan must
    not end the scan, but it must still be visible in the report.
    """
    try:
        raw = Path(target).read_bytes()
    except OSError as error:
        return [], "unreadable: %s" % error
    if len(raw) > MAX_FILE_BYTES:
        return [], "skipped: %d bytes, over the %d byte ceiling" % (len(raw), MAX_FILE_BYTES)
    if b"\x00" in raw:
        return [], "skipped: looks binary"
    return raw.decode("utf-8", errors="replace").splitlines(), ""


def scan_file(path):
    """One file's symbols plus why any of them are missing.

    Returns {"path", "language", "tier", "symbols", "error"}. A Python file
    that will not parse falls back to the lexical patterns AND keeps the
    SyntaxError in "error": half an answer plus the reason is more use than
    either half alone, and the tier on each symbol still says which it is.
    """
    target = safe_path(path)
    relative = _relative(target)
    language = detect_language(target)
    if not language:
        return {"path": relative, "language": "", "tier": "",
                "symbols": [], "error": "not a recognised source extension"}

    lines, error = _read_source(target)
    if error:
        return {"path": relative, "language": language, "tier": "",
                "symbols": [], "error": error}

    if is_structural(language):
        source = "\n".join(lines)
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError) as parse_error:
            return {"path": relative, "language": language,
                    "tier": TIER_HEURISTIC,
                    "symbols": _lexical_symbols(language, relative, lines),
                    "error": "unparsed: %s" % parse_error}
        return {"path": relative, "language": language, "tier": TIER_STRUCTURAL,
                "symbols": _python_symbols(tree, relative, lines), "error": ""}

    return {"path": relative, "language": language, "tier": TIER_HEURISTIC,
            "symbols": _lexical_symbols(language, relative, lines), "error": ""}


def symbols_in(path):
    """The symbols defined in one file.

    The contract other modules build on: a list of dicts carrying at least
    name, kind, line, path (workspace-relative) and language, plus the tier
    the symbol came from. Empty for a file with no symbols and for one that
    could not be read -- scan_file is the call that also returns the reason.
    """
    return scan_file(path)["symbols"]


# --- search -----------------------------------------------------------------

def _match_rank(symbol_name, wanted):
    """0 exact, 1 exact on the last dotted part, 2 substring, None no match.

    Ranked rather than filtered so `find_symbol("run")` puts `run` above
    `Runner.run_all`, which is the order a reader wants and not the order the
    walk produces.
    """
    lowered = symbol_name.lower()
    if lowered == wanted:
        return 0
    if lowered.rsplit(".", 1)[-1] == wanted:
        return 1
    if wanted in lowered:
        return 2
    return None


def find_symbol(name, kind=None, path=None, limit=None):
    """Search the workspace for a symbol and render what was found.

    `kind` narrows to one kind ("class", "function", "method", ...). `path`
    narrows to a file or a subtree and goes through safe_path, so a path
    outside the workspace raises rather than being quietly clamped.
    """
    wanted = str(name or "").strip().lower()
    if not wanted:
        return "find_symbol needs a name to look for."
    kind_filter = str(kind or "").strip().lower()
    try:
        cap = int(limit) if limit not in (None, "") else MAX_SYMBOL_HITS
    except (TypeError, ValueError):
        cap = MAX_SYMBOL_HITS
    cap = max(1, min(cap, MAX_SYMBOL_HITS))

    root = safe_path(path) if path else workspace()
    if not Path(root).exists():
        return "Path not found: %s" % path

    if Path(root).is_file():
        targets = [Path(root)]
    else:
        targets = [absolute for _, absolute in iter_workspace_files(root)
                   if is_code_file(absolute)]

    matches = []
    troubled = []
    for target in targets:
        report = scan_file(target)
        if report["error"]:
            troubled.append((report["path"], report["error"]))
        for symbol in report["symbols"]:
            if kind_filter and symbol["kind"] != kind_filter:
                continue
            rank = _match_rank(symbol["name"], wanted)
            if rank is None:
                continue
            matches.append((rank, symbol["path"], symbol["line"], symbol))

    matches.sort(key=lambda row: (row[0], row[1], row[2]))
    return _render_matches(name, kind_filter, matches, cap, troubled)


def _context_for(symbol):
    """The definition line and the one after it, when it can be re-read.

    Two lines rather than ten: enough to see a signature, little enough that
    forty matches still fit in a reply.
    """
    lines = [symbol.get("text", "")]
    try:
        target = safe_path(symbol["path"])
        source, error = _read_source(target)
        if not error:
            start = symbol["line"] - 1
            lines = [text.strip()[:200] for text in source[start:start + 2] if text.strip()]
    except (ValueError, OSError):
        pass
    return [line for line in lines if line]


def _render_matches(name, kind_filter, matches, cap, troubled):
    narrowed = " of kind '%s'" % kind_filter if kind_filter else ""
    if not matches:
        body = ["No symbol named '%s'%s found in the workspace." % (name, narrowed)]
        return "\n".join(body + _render_troubled(troubled))

    total = len(matches)
    shown = matches[:cap]
    header = "Found %d symbol%s named like '%s'%s:" % (
        total, "" if total == 1 else "s", name, narrowed)
    body = [header, ""]
    for _, _, _, symbol in shown:
        body.append("%s  [%s, %s]" % (symbol["name"], symbol["kind"], symbol["tier"]))
        body.append("  %s:%d  (%s)" % (symbol["path"], symbol["line"], symbol["language"]))
        for line in _context_for(symbol):
            body.append("    | " + line)
        body.append("")
    if total > cap:
        body.append("... capped: showing %d of %d matches. Narrow with a kind "
                    "or a path." % (cap, total))
    body.append("'%s' results are exact; '%s' results are pattern matches and "
                "may be wrong." % (TIER_STRUCTURAL, TIER_HEURISTIC))
    return "\n".join(body + _render_troubled(troubled))


def _render_troubled(troubled):
    """Files that could not be read or parsed, named out loud.

    Dropping them reads as "there are no symbols there", which is a different
    claim entirely and the one that sends a model looking somewhere else.
    """
    if not troubled:
        return []
    lines = ["", "Not fully read (%d file%s):" % (
        len(troubled), "" if len(troubled) == 1 else "s")]
    for relative, error in troubled[:MAX_UNPARSED_REPORTED]:
        lines.append("  %s -- %s" % (relative, error))
    if len(troubled) > MAX_UNPARSED_REPORTED:
        lines.append("  ... capped: %d of %d listed."
                     % (MAX_UNPARSED_REPORTED, len(troubled)))
    return lines
