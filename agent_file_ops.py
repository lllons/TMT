"""Workspace-safe file operations exposed by the agent."""

import ast
import os
import re
import shutil
import threading
from pathlib import Path

import agent_config
from agent_config import LIST_FILES_MAX, WORKSPACE_MAX_SCAN

# One lock for every write this module performs, so that a single write is
# atomic however many threads are working at once. Background workers run the
# same primitives the main agent does, and several of them are read-modify-
# write -- patch_file and replace_lines read a file, work out the new text and
# write it back, and two workers interleaving there produce a file in which
# one of the two edits has silently vanished.
#
# It is deliberately ONE lock rather than one per path. A lock manager keyed by
# file is a transaction system in miniature, and it buys nothing here: writes
# are short, and the case it would speed up -- many workers writing many
# different files at the same instant -- is not a case that happens. What the
# system needs instead is to be told afterwards that two workers wrote the same
# file, and the manager records the paths for that.
#
# RLock, not Lock, because write_files calls write_file for each entry and
# would otherwise deadlock against itself on the second one.
#
# It guards writes only. It is NOT a substitute for safe_path, which still
# refuses an escape from the workspace for every caller, and it makes no claim
# about a file being unchanged between two separate actions.
WRITE_LOCK = threading.RLock()

# Directories that are machinery rather than work. Pruned during the walk, not
# filtered afterwards, so a node_modules or a .git is never descended into at
# all -- that descent is the whole cost on a real project.
WORKSPACE_SKIP = {
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".venv", "venv", "env", "node_modules", ".idea",
    ".vscode", ".gradle", "target", "dist", "build", ".next", ".cache",
    ".terraform", "vendor", ".DS_Store", ".eggs",
}


def workspace():
    """The workspace root, read at call time.

    Startup resolves it and the tests move it, so binding it at import would
    freeze whichever value happened to exist when this module first loaded.
    """
    return agent_config.ROOT_DIR


def iter_workspace_files(root=None, limit=WORKSPACE_MAX_SCAN):
    """Yield (relative, absolute) for workspace files, pruning machinery.

    Returns early once `limit` entries have been examined. The caller is told
    by comparing what it received against the limit: a truncated view that
    claims to be complete is worse than no view.
    """
    root = Path(root or workspace())
    scanned = 0
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in WORKSPACE_SKIP)
        for name in sorted(filenames):
            scanned += 1
            if scanned > limit:
                return
            path = Path(current) / name
            try:
                yield path.relative_to(root), path
            except ValueError:
                continue

def safe_path(user_path):
    root = workspace()
    target = (root / user_path).resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"Blocked unsafe path: {user_path}")
    return target

def _decode_content(content):
    return content.replace("\\n", "\n").replace("\\t", "\t")

def write_file(path, content):
    # The whole body is inside the lock, not just the write call: the
    # existence check below is read back out in the answer, and a concurrent
    # writer between the check and the write would make that answer wrong.
    with WRITE_LOCK:
        p = safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        content = _decode_content(content)
        if p.suffix == ".py":
            try:
                ast.parse(content)
            except SyntaxError as error:
                return f"SyntaxError in generated code: {error}. File NOT written. Please fix."
        # Asked before the write, because afterwards there is no way to tell.
        # The two cases are genuinely different facts and the interface reports
        # them differently: a file that did not exist gained every line it now
        # has and lost none, and both halves of that are known. A file that did
        # exist lost a number of lines nobody can count any more, so only what
        # was written is ever claimed for it.
        existed = p.exists()
        p.write_text(content, encoding="utf-8")
        return f"Wrote file: {path}" if existed else f"Created file: {path}"

def append_file(path, content):
    # Read-modify-write, so the read and the write have to be one step. Two
    # appends interleaving here would each read the same original text and the
    # second would write over the first's addition.
    with WRITE_LOCK:
        p = safe_path(path)
        if not p.exists():
            return f"File not found: {path}"
        existing = p.read_text(encoding="utf-8")
        separator = "\n" if existing and not existing.endswith("\n") else ""
        p.write_text(existing + separator + _decode_content(content), encoding="utf-8")
        return f"Appended to: {path}"

def write_files(files):
    if not isinstance(files, list):
        return "Error: 'files' must be a list of {path, content} objects"
    # Held across the whole batch so a multi-file write lands as one set rather
    # than as several that another worker can be interleaved between. This is
    # the re-entrant case WRITE_LOCK is an RLock for: write_file takes it again
    # on every entry.
    with WRITE_LOCK:
        results = []
        for entry in files:
            path = entry.get("path", "")
            if path:
                results.append(write_file(path, entry.get("content", "")))
            else:
                results.append("Skipped entry with no path")
        return "\n".join(results)

def patch_file(path, search_text, replace_text):
    # Read-modify-write again, and the one where a race is least visible: the
    # search text is found in a version of the file that a concurrent write
    # may already have replaced, and the patch then rewrites the whole file
    # from that stale copy.
    with WRITE_LOCK:
        p = safe_path(path)
        if not p.exists():
            return f"File not found: {path}"
        content = p.read_text(encoding="utf-8")
        search_text, replace_text = _decode_content(search_text), _decode_content(replace_text)
        if search_text not in content:
            return f"Search text not found in {path}"
        new_content = content.replace(search_text, replace_text, 1)
        if p.suffix == ".py":
            try:
                ast.parse(new_content)
            except SyntaxError as error:
                return f"SyntaxError introduced by patch: {error}. Patch aborted."
        p.write_text(new_content, encoding="utf-8")
        return f"Patched file: {path}"

def delete_file(path):
    p = safe_path(path)
    if not p.exists():
        return f"File not found: {path}"
    if p.is_dir():
        return f"Refusing to delete directory: {path}"
    if input(f"Delete {path}? (y/N): ").strip().lower() != "y":
        return "Delete cancelled"
    # The lock is taken for the removal and deliberately not for the
    # confirmation above. A lock held across a read of stdin is a lock held
    # until a human answers it, and every other write in the process would
    # queue behind that prompt.
    with WRITE_LOCK:
        p.unlink()
        return f"Deleted file: {path}"

def read_file(path):
    p = safe_path(path)
    return p.read_text(encoding="utf-8") if p.exists() else f"File not found: {path}"

def list_files():
    """Every file in the workspace, up to a fixed ceiling.

    Capped independently of the prompt snapshot: this is the action a model
    reaches for when it wants a complete picture, so it must say plainly when
    the picture it is handing back is not complete.
    """
    names = [str(relative) for relative, _ in iter_workspace_files()]
    if not names:
        return "(empty workspace)"
    shown = names[:LIST_FILES_MAX]
    if len(names) <= LIST_FILES_MAX:
        return "\n".join(shown)
    return "\n".join(shown) + (
        f"\n... truncated: {len(shown)} of {len(names)}+ files shown. "
        "Use search_files, or list a subfolder, to see the rest."
    )

def create_folder(path):
    p = safe_path(path)
    if p.exists():
        return f"Already exists: {path}"
    p.mkdir(parents=True, exist_ok=True)
    return f"Created folder: {path}"

MAX_SEARCH_HITS = 100

def search_files(query, regex=False, path=None):
    try:
        pattern = re.compile(query if regex else re.escape(query), re.IGNORECASE)
    except re.error as error:
        return f"Invalid regex: {error}"
    root = safe_path(path) if path else workspace()
    if not root.exists():
        return f"Path not found: {path}"
    targets = [root] if root.is_file() else [p for _, p in iter_workspace_files(root)]
    hits = []
    for p in targets:
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{p.relative_to(workspace())}:{lineno}: {line.strip()[:200]}")
                if len(hits) >= MAX_SEARCH_HITS:
                    hits.append(f"... stopped at {MAX_SEARCH_HITS} matches — narrow the query.")
                    return "\n".join(hits)
    return "\n".join(hits) if hits else f"No matches for: {query}"

def read_lines(path, start=1, end=None):
    p = safe_path(path)
    if not p.exists():
        return f"File not found: {path}"
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    try:
        start = max(1, int(start))
        end = len(lines) if end in (None, "") else min(len(lines), int(end))
    except (TypeError, ValueError):
        return "start and end must be whole numbers"
    if start > len(lines):
        return f"{path} has only {len(lines)} lines"
    return "\n".join(f"{i:>5} | {lines[i - 1]}" for i in range(start, end + 1))

def replace_lines(path, start, end, content):
    # The line numbers are only meaningful against the version of the file
    # that was read, so the read and the write have to be one step. A write
    # landing in between would move every line the range names.
    with WRITE_LOCK:
        p = safe_path(path)
        if not p.exists():
            return f"File not found: {path}"
        lines = p.read_text(encoding="utf-8").splitlines()
        try:
            start, end = int(start), int(end)
        except (TypeError, ValueError):
            return "start and end must be whole numbers"
        if start < 1 or start > end or end > len(lines):
            return f"Invalid range {start}-{end}; {path} has {len(lines)} lines"
        new_content = "\n".join(lines[:start - 1] + _decode_content(content).splitlines() + lines[end:]) + "\n"
        if p.suffix == ".py":
            try:
                ast.parse(new_content)
            except SyntaxError as error:
                return f"SyntaxError introduced by replace_lines: {error}. Aborted."
        p.write_text(new_content, encoding="utf-8")
        return f"Replaced lines {start}-{end} in {path}"

# --- exact search, and bulk replace -----------------------------------------
#
# search_files above is the fuzzy one: case-insensitive, one line at a time.
# These two are the exact ones. A model looking for the precise five lines it
# is about to edit cannot use a case-insensitive line matcher to find them, and
# it certainly cannot use one to decide what to rewrite.

FIND_TEXT_MAX_HITS = 100        # match blocks rendered before find_text stops
FIND_TEXT_HARD_MAX = 1000       # ceiling on a caller-supplied limit
FIND_TEXT_MAX_CONTEXT = 10      # context lines either side, per match
FIND_TEXT_MAX_LINE = 400        # characters of any one line put in the result
REPLACE_LIST_MAX = 200          # per-file rows listed; the counts stay whole
BINARY_SNIFF_BYTES = 4096       # how much of a file is examined for a NUL


def _looks_binary(data):
    """A NUL byte near the front, which is what actually separates the two.

    Cheap and good enough: a .png or a .pyc rendered into a search result is
    unreadable noise, and one handed to a bulk replace is a corrupted file.
    """
    return b"\x00" in data[:BINARY_SNIFF_BYTES]


def _to_lf(text):
    """Newlines flattened so a multi-line needle can be matched at all.

    A block copied out of a CRLF file and pasted into a query arrives with the
    carriage returns still in it, or with them already gone; either way the
    match has to happen. Nothing is written from this form -- replace_across
    re-expresses its needle in the file's own endings before touching it.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _glob_to_regex(pattern):
    """A path glob compiled once, with `**` meaning what people expect.

    fnmatch cannot do this: its `*` crosses directory separators, so
    `src/*.py` would quietly match `src/deep/nested/thing.py`. Here `*` stops
    at a separator and `**/` is the "any depth, including none" segment, so
    `src/**/*.py` covers `src/a.py` as well as `src/deep/a.py`.
    """
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _glob_filter(pattern):
    """A predicate over workspace-relative paths, or one that lets all through."""
    if not pattern:
        return lambda relative: True
    matcher = _glob_to_regex(str(pattern).replace("\\", "/"))
    # A pattern with no separator in it is meant as "anywhere", not "in the
    # workspace root only" -- `*.py` from a model means every Python file.
    anywhere = "/" not in str(pattern).replace("\\", "/")

    def keep(relative):
        text = str(relative).replace("\\", "/")
        if matcher.match(text):
            return True
        return anywhere and bool(matcher.match(text.rsplit("/", 1)[-1]))
    return keep


def _search_targets(path, glob):
    """(files, hit_the_scan_ceiling) as (relative, absolute) pairs.

    safe_path's ValueError is allowed straight out: a caller that asked for
    somewhere outside the workspace must be refused, not handed an empty
    result set that reads as "there is nothing there".
    """
    root = safe_path(path) if path else workspace()
    if not root.exists():
        return None, False
    keep = _glob_filter(glob)
    here = workspace()
    if root.is_file():
        found = [root]
        capped = False
    else:
        found = [p for _, p in iter_workspace_files(root)]
        # iter_workspace_files stops dead at its ceiling, so a full basket is
        # the only signal that the walk may have been cut short.
        capped = len(found) >= WORKSPACE_MAX_SCAN
    targets = []
    for p in found:
        try:
            relative = p.relative_to(here)
        except ValueError:
            continue
        if keep(relative):
            targets.append((relative, p))
    return targets, capped


def _plural(count, word, plural=None):
    """Counted noun. `plural` exists because "4 matchs" reads as a bug in the
    tool rather than as an English mistake, and the header is the first thing
    anyone reads."""
    if count == 1:
        return f"{count} {word}"
    return f"{count} {plural or word + 's'}"


def _posix(relative):
    """One separator in every result, whatever platform produced the path."""
    return str(relative).replace("\\", "/")


def find_text(query, path=None, glob=None, context=0, limit=None):
    """Exact, case-SENSITIVE search, including for a block spanning lines.

    The tool for "where is this precise code", where search_files is the tool
    for "where is something roughly like this". The distinction is the point:
    a case-insensitive line matcher cannot tell `Path` from `path`, and cannot
    find a five-line block at all, so a model that only has one of those ends
    up editing on a guess.

    Totals in the header are counted over every file examined, not over the
    part that fitted in the result, because a header that reported the shown
    figure would understate the work still out there every time it capped.
    """
    if not query:
        return "find_text needs a 'query' -- there is nothing to look for."
    needle = _to_lf(_decode_content(query))
    if not needle:
        return "find_text needs a 'query' -- there is nothing to look for."
    try:
        context = max(0, min(FIND_TEXT_MAX_CONTEXT, int(context or 0)))
    except (TypeError, ValueError):
        return "context must be a whole number of lines"
    try:
        cap = FIND_TEXT_MAX_HITS if limit in (None, "") else int(limit)
    except (TypeError, ValueError):
        return "limit must be a whole number of matches"
    cap = max(1, min(FIND_TEXT_HARD_MAX, cap))

    targets, scan_capped = _search_targets(path, glob)
    if targets is None:
        return f"Path not found: {path}"

    blocks, total, files_hit, skipped_binary = [], 0, 0, 0
    for relative, p in targets:
        if not p.is_file():
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if _looks_binary(data):
            skipped_binary += 1
            continue
        text = _to_lf(data.decode("utf-8", "replace"))
        if needle not in text:
            continue
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        files_hit += 1
        at = text.find(needle)
        while at != -1:
            total += 1
            if total <= cap:
                start = text.count("\n", 0, at) + 1
                end = text.count("\n", 0, at + len(needle) - 1) + 1
                blocks.append(_render_match(relative, lines, start, end, context))
            at = text.find(needle, at + 1)

    if not total:
        head = f"No exact match for: {needle.splitlines()[0][:120]}"
        notes = ["Matching here is case-sensitive; search_files is the "
                 "case-insensitive one."]
        if glob:
            notes.append(f"Only paths matching {glob} were examined.")
        if skipped_binary:
            notes.append(f"{_plural(skipped_binary, 'binary file')} skipped.")
        return "\n".join([head] + notes)

    head = f"{_plural(total, 'match', 'matches')} in {_plural(files_hit, 'file')}"
    if total > cap:
        head += (f" -- showing the first {cap}, the limit for one result. "
                 "Narrow the query, or pass a smaller 'glob'.")
    out = [head, ""] + blocks
    if skipped_binary:
        out.append(f"({_plural(skipped_binary, 'binary file')} skipped.)")
    if scan_capped:
        out.append(f"(The walk stopped at {WORKSPACE_MAX_SCAN} entries, so "
                   "files beyond that were never examined.)")
    return "\n".join(out).rstrip()


def _render_match(relative, lines, start, end, context):
    """One match block: a locator line, then the matched lines and any context.

    `>` marks the match and `|` marks context, so the two read apart with ANSI
    stripped -- colour is never allowed to be the only thing carrying it.
    """
    first = max(1, start - context)
    last = min(len(lines), end + context)
    out = [f"{_posix(relative)}:{start}:"]
    for n in range(first, last + 1):
        body = lines[n - 1]
        if len(body) > FIND_TEXT_MAX_LINE:
            body = body[:FIND_TEXT_MAX_LINE] + " ..."
        marker = ">" if start <= n <= end else "|"
        out.append(f"{n:>6} {marker} {body}")
    out.append("")
    return "\n".join(out)


def replace_across(search, replace, glob=None, path=None, apply=False):
    """Literal find-and-replace over many files. Preview unless apply is true.

    The asymmetry is deliberate and is the whole safety of the thing: a bulk
    edit nobody looked at first is how a repository gets wrecked in one action.
    Preview computes exactly what apply would compute -- including the syntax
    check that refuses a file -- so the two reports describe the same set of
    files, and a preview that lists a file is a promise apply will change it.
    """
    if not search:
        return ("replace_across needs a non-empty 'search'. Refusing rather "
                "than guessing: an empty needle matches between every pair of "
                "characters in every file.")
    needle = _to_lf(_decode_content(search))
    if not needle:
        return ("replace_across needs a non-empty 'search'. Refusing rather "
                "than guessing: an empty needle matches between every pair of "
                "characters in every file.")
    payload = _to_lf(_decode_content(replace or ""))

    targets, scan_capped = _search_targets(path, glob)
    if targets is None:
        return f"Path not found: {path}"

    changed, broken, total, skipped_binary = [], [], 0, 0
    for relative, p in targets:
        if not p.is_file():
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        # Bytes in, bytes out. Text mode with universal newlines would rewrite
        # every line ending in the file on the way through, and this repo has
        # core.autocrlf set: a whole-file ending flip buries the one real
        # change under a diff of every line.
        if _looks_binary(data):
            skipped_binary += 1
            continue
        try:
            raw = data.decode("utf-8")
        except UnicodeDecodeError:
            skipped_binary += 1
            continue

        # The file's own endings decide the shape of the needle, so everything
        # outside the replaced span comes back byte for byte -- a mixed file
        # is left mixed rather than tidied.
        crlf = raw.count("\r\n")
        uses_crlf = crlf > 0 and crlf >= raw.count("\n") - crlf
        wanted = needle.replace("\n", "\r\n") if uses_crlf else needle
        putting = payload.replace("\n", "\r\n") if uses_crlf else payload
        count = raw.count(wanted)
        if not count:
            continue
        updated = raw.replace(wanted, putting)
        if p.suffix == ".py":
            try:
                ast.parse(_to_lf(updated))
            except SyntaxError as error:
                broken.append((relative, error))
                continue
        if apply:
            # Built whole first, then written in one call: a failure while
            # working the text out leaves the file exactly as it was, rather
            # than half rewritten.
            #
            # The lock is taken per file rather than around the whole walk.
            # A bulk replace can touch hundreds of files, and holding the one
            # process-wide write lock for the length of that would stall every
            # other worker for as long as the scan takes. Each individual file
            # is still written atomically, which is the guarantee that matters:
            # nobody sees a half-written file, and the count reported is the
            # count written.
            with WRITE_LOCK:
                p.write_bytes(updated.encode("utf-8"))
        changed.append((relative, count))
        total += count

    verb = "changed" if apply else "would change"
    if not changed:
        head = "0 occurrences: no file was changed."
        if broken:
            head = ("0 occurrences changed. Every file that matched was "
                    "skipped; see below.")
    else:
        head = (f"{_plural(total, 'occurrence')} in "
                f"{_plural(len(changed), 'file')} {verb}.")
        if not apply:
            head = "Preview only, nothing written. " + head
    out = [head]
    for relative, count in changed[:REPLACE_LIST_MAX]:
        out.append(f"  {_posix(relative)}: "
                   f"{_plural(count, 'occurrence')} {verb}")
    if len(changed) > REPLACE_LIST_MAX:
        out.append(f"  ... and {len(changed) - REPLACE_LIST_MAX} more files, "
                   f"listed to the {REPLACE_LIST_MAX}-file limit. The counts "
                   "above the list cover all of them.")
    for relative, error in broken:
        out.append(f"  SKIPPED {_posix(relative)}: the replacement would not "
                   f"parse ({error}). Not written.")
    if skipped_binary:
        out.append(f"  {_plural(skipped_binary, 'binary or non-UTF-8 file')} "
                   "skipped.")
    if scan_capped:
        out.append(f"  The walk stopped at {WORKSPACE_MAX_SCAN} entries, so "
                   "files beyond that were never examined.")
    if changed and not apply:
        out.append('Nothing on disk was touched. Re-run with "apply": true to '
                   "make these changes.")
    return "\n".join(out)


def copy_file(path, dest):
    if not dest:
        return "copy_file needs a 'to' path"
    # The refusal to overwrite is the reason the whole body is locked: checked
    # outside, it is a promise another thread can break between the check and
    # the copy, and the one thing this function says it will never do is
    # overwrite a file that already existed.
    with WRITE_LOCK:
        src, dst = safe_path(path), safe_path(dest)
        if not src.exists():
            return f"File not found: {path}"
        if src.is_dir():
            return f"Refusing to copy a directory: {path}"
        if dst.exists():
            return f"Refusing to overwrite existing file: {dest}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return f"Copied {path} to {dest}"

def delete_folder(path, recursive=False):
    p = safe_path(path)
    if p == workspace():
        return "Refusing to delete the workspace root"
    if not p.exists():
        return f"Folder not found: {path}"
    if not p.is_dir():
        return f"Not a folder: {path} — use delete_file instead"
    contents = list(p.rglob("*"))
    if contents and not recursive:
        return f"{path} is not empty ({len(contents)} items). Retry with \"recursive\": true to delete everything inside."
    label = f"{path} and {len(contents)} items inside" if contents else path
    if input(f"Delete {label}? (y/N): ").strip().lower() != "y":
        return "Delete cancelled"
    # Same reasoning as delete_file: the removal is locked, the confirmation
    # is not, because a lock held across a prompt is held until it is answered.
    with WRITE_LOCK:
        shutil.rmtree(p)
        return f"Deleted folder: {path}"
