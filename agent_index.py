"""A cached map of the workspace: what each file defines and what it imports.

The point of this module is that it does NOT rescan. Asking "who imports
agent_ui" by walking and parsing the whole tree costs the same as asking it
once per question, so the third question costs three times as much as it
should and the model stops asking. The index parses a file once, keys the
result on that file's (size, mtime), and on the next question re-parses only
what actually moved.

The cache lives under agent_config.INSTALL_DIR, never in the workspace. TMT
writes nothing into the project it is working on -- there is a test asserting
exactly that, and an index dropped in the user's repository would be the first
thing to break it. Different projects are kept apart by hashing the workspace
path into the filename, so opening a second project does not silently answer
with the first one's map.
"""

import hashlib
import json
import time
from pathlib import Path

import agent_config
from agent_file_ops import iter_workspace_files, safe_path, workspace
from agent_symbols import (TIER_HEURISTIC, TIER_STRUCTURAL, is_code_file,
                           scan_file)

# Bumped whenever the shape of a cache entry changes. An old file is discarded
# rather than migrated: the cache is derived data and rebuilding it costs one
# scan, so there is nothing here worth the risk of reading a stale shape as if
# it were the current one.
CACHE_VERSION = 3
CACHE_DIRNAME = ".tmt_index"

# What one code_map answer may contain. Same reasoning as find_symbol's cap:
# a list long enough to fill a context window is not an answer.
MAX_MAP_ROWS = 25
MAX_REFERENCE_FILES = 400

RELATIONS = ("all", "defines", "imports", "importers", "references")


def _cache_dir():
    """Read INSTALL_DIR at call time, never bound at import.

    The rest of the project does the same with ROOT_DIR and for the same
    reason: a value fixed when the module loaded is a value the tests cannot
    move, and this one has to be movable or the tests write into the repo.
    """
    return Path(agent_config.INSTALL_DIR) / CACHE_DIRNAME


def cache_path(root=None):
    """Where this workspace's index file lives.

    Named by a hash of the workspace path rather than by its last component:
    two checkouts of the same project have the same folder name, and one
    answering for the other is the kind of wrong that looks right.
    """
    root = Path(root or workspace()).resolve()
    digest = hashlib.sha1(str(root).encode("utf-8", "replace")).hexdigest()[:16]
    return _cache_dir() / ("index_%s.json" % digest)


def clear_cache(root=None):
    """Delete this workspace's cache. Safe at any time; it rebuilds itself."""
    try:
        cache_path(root).unlink()
        return True
    except OSError:
        return False


def _load_cache(root):
    """The stored index, or {} for anything that is not usable.

    Corrupt JSON, a version from an older build, a file written for another
    workspace: all the same answer, an empty dict, because every one of them
    is repaired by the rebuild that follows. Raising here would turn a stale
    file on disk into a dead tool.
    """
    try:
        raw = cache_path(root).read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("version") != CACHE_VERSION:
        return {}
    if data.get("workspace") != str(root):
        return {}
    files = data.get("files")
    if not isinstance(files, dict):
        return {}
    return data


def _store_cache(index):
    """Write the index, and treat a failed write as a slow index not an error.

    A read-only install directory must degrade to "no caching" rather than to
    "no code_map": the answer is still correct without the cache, it just
    costs a scan.
    """
    try:
        path = cache_path(index.get("workspace"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(index), encoding="utf-8")
        return True
    except (OSError, TypeError, ValueError):
        return False


def _stamp(absolute):
    """(size, mtime_ns), the pair that decides whether a file was re-parsed.

    mtime in nanoseconds rather than seconds because two edits inside one
    filesystem tick are common while a task is running, and a one-second
    resolution would report the first version of the file after the second
    was written.
    """
    try:
        info = Path(absolute).stat()
    except OSError:
        return None
    return [info.st_size, info.st_mtime_ns]


def build_index(force=False):
    """Build or refresh the workspace index and return it.

    Reuses every cached entry whose (size, mtime) still matches, so the usual
    cost of a query is a stat per file rather than a parse per file.
    `force=True` throws the cache away and parses everything, which is the
    escape hatch for the day the stamps lie.
    """
    root = Path(workspace()).resolve()
    cached = {} if force else _load_cache(root)
    cached_files = cached.get("files", {}) if cached else {}

    files = {}
    reused = scanned = 0
    for relative, absolute in iter_workspace_files(root):
        if not is_code_file(absolute):
            continue
        key = str(relative).replace("\\", "/")
        stamp = _stamp(absolute)
        previous = cached_files.get(key)
        if previous and stamp is not None and previous.get("stamp") == stamp:
            files[key] = previous
            reused += 1
            continue
        report = scan_file(absolute)
        scanned += 1
        files[key] = {
            "stamp": stamp,
            "language": report["language"],
            "tier": report["tier"],
            "error": report["error"],
            # Stored flat rather than as the full symbol dicts: path and
            # language are already known from the entry they sit in, and
            # repeating them per symbol tripled the size of the cache file.
            "symbols": [[symbol["name"], symbol["kind"], symbol["line"]]
                        for symbol in report["symbols"]
                        if symbol["kind"] != "import"],
            "imports": [[symbol["name"], symbol["line"]]
                        for symbol in report["symbols"]
                        if symbol["kind"] == "import"],
        }

    index = {
        "version": CACHE_VERSION,
        "workspace": str(root),
        "built": time.time(),
        "files": files,
        "stats": {"files": len(files), "reused": reused, "scanned": scanned,
                  "dropped": max(0, len(cached_files) - reused)},
    }
    _store_cache(index)
    return index


# --- naming -----------------------------------------------------------------

def _module_names(relative):
    """The names a file can be imported or referred to by.

    "pkg/mod.py" answers to "pkg.mod", "pkg/mod" and "mod". All three get
    asked, because which one a reader types depends on whether they are
    looking at an import line, a path or a stack trace.
    """
    path = Path(relative)
    dotted = ".".join(path.with_suffix("").parts)
    return {dotted, path.with_suffix("").as_posix(), path.stem, relative}


def _import_names(name):
    """The names an import statement can be said to satisfy.

    "pathlib.Path" satisfies a query for "pathlib", for "pathlib.Path" and for
    "Path"; "./utils/date" satisfies "date". Import spellings differ per
    language and the query does not know which language it will land in, so
    the widening happens here once.
    """
    cleaned = str(name).replace("\\", "/").strip("./")
    parts = [part for part in cleaned.replace("::", ".").replace("/", ".").split(".") if part]
    names = {cleaned, name}
    if parts:
        names.add(parts[0])
        names.add(parts[-1])
        names.add(".".join(parts))
    return names


def _looks_like_a_path(target):
    text = str(target)
    return "/" in text or "\\" in text or bool(Path(text).suffix)


# --- the query --------------------------------------------------------------

def code_map(target, relation="all"):
    """Answer one question about how `target` sits in the project.

    relation is one of defines, imports, importers, references, all. A target
    that looks like a path goes through safe_path first, so asking about a
    file above the workspace raises instead of being answered.
    """
    wanted = str(target or "").strip()
    if not wanted:
        return "code_map needs a symbol, module or file name."
    relation = str(relation or "all").strip().lower() or "all"
    if relation not in RELATIONS:
        return "Unknown relation '%s'. Use one of: %s." % (
            relation, ", ".join(RELATIONS))
    if _looks_like_a_path(wanted):
        # Deliberately not caught. A path outside the workspace is a mistake
        # worth stopping on, not a question worth answering with "no results".
        safe_path(wanted)

    index = build_index()
    if not index["files"]:
        return "No source files found in the workspace, so there is nothing to map."

    sections = []
    if relation in ("all", "defines"):
        sections.append(_defines_section(index, wanted))
    if relation in ("all", "imports"):
        sections.append(_imports_section(index, wanted))
    if relation in ("all", "importers"):
        sections.append(_importers_section(index, wanted))
    if relation in ("all", "references"):
        sections.append(_references_section(index, wanted))

    head = "code_map for '%s' (%s) across %d source file%s:" % (
        wanted, relation, len(index["files"]),
        "" if len(index["files"]) == 1 else "s")
    return "\n".join([head, ""] + sections).rstrip() + "\n" + _tier_note(index, wanted)


def _tier_note(index, wanted):
    """One line saying how much of this answer was parsed and how much guessed."""
    languages = {entry["language"] for entry in index["files"].values()}
    parsed = {entry["tier"] for entry in index["files"].values()}
    if parsed == {TIER_STRUCTURAL}:
        return "All results parsed (%s)." % ", ".join(sorted(languages))
    return ("Results marked %s are parsed; %s results are pattern matches over "
            "text and may be wrong." % (TIER_STRUCTURAL, TIER_HEURISTIC))


def _rows(title, rows, empty):
    if not rows:
        return "%s\n  %s\n" % (title, empty)
    body = ["%s (%d)" % (title, len(rows))]
    for row in rows[:MAX_MAP_ROWS]:
        body.append("  " + row)
    if len(rows) > MAX_MAP_ROWS:
        body.append("  ... capped: showing %d of %d. Ask for one relation, or "
                    "a narrower name." % (MAX_MAP_ROWS, len(rows)))
    return "\n".join(body) + "\n"


def _defining_files(index, wanted):
    """Files that define `wanted`, either as a symbol or as the module itself."""
    lowered = wanted.lower()
    hits = []
    for relative, entry in sorted(index["files"].items()):
        for name, kind, line in entry["symbols"]:
            if name.lower() == lowered or name.lower().rsplit(".", 1)[-1] == lowered:
                hits.append((relative, name, kind, line, entry))
        if any(candidate.lower() == lowered for candidate in _module_names(relative)):
            hits.append((relative, relative, "module", 1, entry))
    return hits


def _defines_section(index, wanted):
    rows = ["%s  [%s, %s]  %s:%d" % (name, kind, entry["tier"] or "unknown",
                                     relative, line)
            for relative, name, kind, line, entry in _defining_files(index, wanted)]
    return _rows("defines", rows, "not defined anywhere in this workspace.")


def _imports_section(index, wanted):
    """What the file that owns `wanted` imports.

    Keyed on the defining file rather than on the name, because "what does
    this import" is a question about a file however it was asked.
    """
    owners = {relative for relative, _, _, _, _ in _defining_files(index, wanted)}
    if not owners:
        owners = {relative for relative in index["files"]
                  if any(name.lower() == wanted.lower()
                         for name in _module_names(relative))}
    rows = []
    for relative in sorted(owners):
        entry = index["files"][relative]
        for name, line in entry["imports"]:
            rows.append("%s imports %s  (line %d)" % (relative, name, line))
    if not owners:
        return _rows("imports", [], "no file in the workspace owns that name.")
    return _rows("imports", rows, "that file imports nothing.")


def _importers_section(index, wanted):
    lowered = wanted.lower()
    rows = []
    for relative, entry in sorted(index["files"].items()):
        for name, line in entry["imports"]:
            if any(candidate.lower() == lowered for candidate in _import_names(name)):
                rows.append("%s:%d  imports %s" % (relative, line, name))
                break
    return _rows("importers", rows, "nothing in this workspace imports that.")


def _references_section(index, wanted):
    """Where the bare word appears, definitions excluded.

    Lexical and labelled lexical. It cannot tell a call from a comment or from
    the same word in a docstring, and pretending otherwise would be the exact
    failure the two tiers exist to prevent.
    """
    definitions = {(relative, line)
                   for relative, _, _, line, _ in _defining_files(index, wanted)}
    rows = []
    scanned = 0
    for relative in sorted(index["files"]):
        if scanned >= MAX_REFERENCE_FILES:
            break
        scanned += 1
        try:
            absolute = safe_path(relative)
            text = absolute.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        if wanted not in text:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if wanted not in line or (relative, number) in definitions:
                continue
            if not _is_word_hit(line, wanted):
                continue
            rows.append("%s:%d  %s" % (relative, number, line.strip()[:120]))
    return _rows("references (lexical, %s)" % TIER_HEURISTIC, rows,
                 "the name appears nowhere outside its definition.")


def _is_word_hit(line, wanted):
    """A whole-word check without a regex, so a name with a dot still works.

    re.escape plus \\b would refuse to match "Class.method" the way a reader
    means it, and building a per-name regex for every line of every file was
    measurably the slowest thing in this module.
    """
    start = 0
    while True:
        found = line.find(wanted, start)
        if found < 0:
            return False
        before = line[found - 1] if found else " "
        after_index = found + len(wanted)
        after = line[after_index] if after_index < len(line) else " "
        if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
            return True
        start = found + 1
