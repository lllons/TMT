"""Finding files and directories by path pattern.

Path discovery is the one question nothing else here answers. `list_files`
hands back everything and `tree` draws a shape, so a model looking for one
file by name has to read a wall of paths and pick it out of it -- and a model
that instead searches file CONTENTS to find a filename has spent a whole turn
answering the wrong question. This walks the workspace once, matches paths
against a glob, and names only what matched. Nothing is ever opened: every row
below costs a stat and no read at all.

The walk, the pruning, the scan ceiling and the containment test all belong to
`agent_file_ops`. Nothing here is a second copy of any of them, because a
second traversal is a second set of rules about what counts as machinery and
what counts as inside the workspace, and only one of the two would be updated
the day either changes.
"""

from pathlib import Path

import agent_file_ops
from agent_config import WORKSPACE_MAX_SCAN

# Enough rows to answer "which files are these" in one action, and few enough
# that a pattern matching half a repository does not arrive as a wall. The
# hard ceiling is the most a caller may ask for: past it the result stops
# being something a model reads and becomes something it has to search, which
# is the problem this action exists to remove.
GLOB_MAX_RESULTS = 200
GLOB_HARD_MAX = 1000

# The spelling a model reaches for is not always the one this module uses, and
# a refused action costs a whole round to recover from. Accepting the obvious
# synonyms is cheaper than that retry; anything else is named back in the
# refusal, so the correction is one word rather than a guess.
_KINDS = {
    "file": "files", "files": "files",
    "dir": "dirs", "dirs": "dirs", "directory": "dirs",
    "directories": "dirs", "folder": "dirs", "folders": "dirs",
    "any": "any", "all": "any", "both": "any",
}

# The empty result says which of the three questions was actually asked, so
# "nothing matched" cannot be read as "there are no files like that" when the
# search was for directories.
_NOTHING = {
    "files": "No files match",
    "dirs": "No directories match",
    "any": "No paths match",
}

_MACHINERY_NOTE = ("Machinery directories (.git, node_modules, __pycache__, "
                   ".venv and similar) are never searched.")


def _canonical_kind(kind):
    """One of files/dirs/any, or None when the word is not one of them."""
    if kind in (None, ""):
        return "files"
    return _KINDS.get(str(kind).strip().lower())


def _result_limit(limit):
    """The clamped row cap, or None when what arrived was not a number.

    A bool is refused by name: `int(True)` is 1, so `"limit": true` would come
    back as a single row plus a truncation notice for a cap nobody chose.
    """
    if isinstance(limit, bool):
        return None
    if limit in (None, ""):
        return GLOB_MAX_RESULTS
    try:
        return max(1, min(GLOB_HARD_MAX, int(limit)))
    except (TypeError, ValueError):
        return None


def _is_dir(p):
    """True for a directory, False for anything that cannot be measured.

    An entry that vanished mid-walk or that nobody may stat is a fact about
    that one entry, never a reason to lose the whole result -- the same rule
    the tree walk follows. It is asked of every entry rather than inferred
    from `include_dirs` so that the kind filter is enforced here, where the
    caller's `kind` is known, instead of resting on what the walk happened to
    yield.
    """
    try:
        return p.is_dir()
    except OSError:
        return False


def _scan_note():
    """Said whenever the walk stopped early, matches or not.

    A result that hid this would be claiming the whole workspace was examined,
    and the empty result is where that claim does the most damage: the path
    being looked for may be exactly the one the walk never reached.
    """
    return (f"(The walk stopped at {WORKSPACE_MAX_SCAN} entries, so paths "
            "beyond that were never examined.)")


def glob(pattern, path=None, kind=None, limit=None):
    """Workspace paths matching `pattern`, files unless `kind` says otherwise.

    `path` goes through safe_path, so anything outside the workspace raises
    ValueError and the agent loop hands the model a correction rather than an
    empty result that reads as "there is nothing there".

    The header counts everything that matched, not the part that fitted in the
    result: a header reporting the shown figure would understate what is still
    out there every time it capped, and the number in it is the one a model
    decides its next action on.
    """
    if not isinstance(pattern, str) or not pattern.strip():
        return "glob needs a 'pattern' -- there is nothing to look for."
    # Answered in words rather than left to raise, for the reason `agent_grep`
    # gives at the same guard: `safe_path` builds `root / user_path` and that
    # is a TypeError for anything not path-like, while `_run_tool` catches only
    # the ValueError a refusal is made of.
    if path is not None and not isinstance(path, str):
        return "path must be a folder path written as text"
    wanted = _canonical_kind(kind)
    if wanted is None:
        return f"kind must be files, dirs or any -- got: {kind}"
    cap = _result_limit(limit)
    if cap is None:
        return "limit must be a whole number of results"
    try:
        keep = agent_file_ops.glob_filter(pattern)
    except Exception as error:
        # glob_filter escapes everything it does not itself treat as a
        # wildcard, so no pattern should be able to reach this. Guarded all
        # the same, because the alternative is one stray character ending the
        # turn instead of being answered.
        return f"Invalid pattern: {error}"

    root = agent_file_ops.safe_path(path) if path else Path(agent_file_ops.workspace())
    if not root.exists():
        return f"Path not found: {path if path else root}"

    here = agent_file_ops.workspace()
    matches, scanned = [], 0
    for _, p in agent_file_ops.iter_workspace_entries(
            root, include_dirs=wanted != "files"):
        scanned += 1
        try:
            relative = p.relative_to(here)
        except ValueError:
            continue
        directory = _is_dir(p)
        if wanted == "files" and directory:
            continue
        if wanted == "dirs" and not directory:
            continue
        # Matched against the path from the WORKSPACE root rather than from
        # `path`, which is what makes a pattern full of `..` harmless: it
        # describes somewhere no workspace-relative path can ever be written,
        # so it simply matches nothing.
        if not keep(relative):
            continue
        # Asked only of what matched. resolve() is a syscall per entry, and a
        # link out of the tree is rare enough that paying for it on all twenty
        # thousand paths of a walk would be the wrong trade -- what matters is
        # that nothing outside the workspace is ever NAMED.
        if not agent_file_ops.within_workspace(p):
            continue
        text = agent_file_ops.posix(relative)
        # The trailing separator is how the two kinds read apart under
        # `kind: any`, and it survives having the colour stripped out.
        matches.append(text + "/" if directory else text)

    # iter_workspace_entries stops dead at its ceiling, so a full basket is the
    # only signal there was that the walk may have been cut short.
    capped = scanned >= WORKSPACE_MAX_SCAN
    matches.sort()
    total = len(matches)

    if not total:
        out = [f"{_NOTHING[wanted]}: {pattern}"]
        if path:
            out.append(f"Only paths under {path} were searched.")
        # Only said when a walk really happened. A `path` naming a single file
        # was never pruned by anything, and offering pruning as the reason
        # nothing matched would send the reader looking in the wrong place.
        if _is_dir(root):
            out.append(_MACHINERY_NOTE)
        if capped:
            out.append(_scan_note())
        return "\n".join(out)

    head = f"{agent_file_ops.plural(total, 'match', 'matches')} for `{pattern}`"
    if total > cap:
        # The sentence ends the header on its own, so the colon that would
        # introduce the list is dropped rather than left stranded after a full
        # stop. Sorting happens before the cut, so "the first {cap}" names the
        # first rows of one stable ordering and not whatever the walk reached
        # first.
        head += (f" -- showing the first {cap}, the limit for one result. "
                 "Narrow the pattern, or pass a 'path'.")
    else:
        head += ":"
    out = [head] + matches[:cap]
    if capped:
        out.append(_scan_note())
    return "\n".join(out)
