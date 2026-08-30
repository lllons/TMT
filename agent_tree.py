"""A drawing of the workspace's shape, so it can be read before it is opened.

The model otherwise learns a repository by reading files, which costs a whole
turn per guess. A tree is one action that answers "what is here" without ever
looking inside anything: every size below comes from stat(), never from a read.
"""

import os
from pathlib import Path

from agent_file_ops import WORKSPACE_SKIP, safe_path, workspace

# Deep enough to show how a project is organised, shallow enough that a big
# repository does not arrive as a wall. Both are ceilings the caller can raise,
# and both say so in the output when they are the reason something is missing.
TREE_MAX_DEPTH = 4
TREE_MAX_ENTRIES = 400

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")

BRANCH = "├── "
LAST = "└── "
PIPE = "│   "
BLANK = "    "


def format_size(n):
    """A byte count a person can read at a glance."""
    try:
        size = float(n)
    except (TypeError, ValueError):
        return "? B"
    if size < 1024:
        return "%d B" % int(size)
    for unit in _UNITS[1:]:
        size /= 1024.0
        # Rounded before the comparison, because 1048575 bytes is 1023.999 KB
        # and would otherwise print as "1024.0 KB" instead of "1.0 MB".
        if round(size, 1) < 1024 or unit == _UNITS[-1]:
            return "%.1f %s" % (size, unit)
    return "%.1f %s" % (size, _UNITS[-1])


def _children(directory):
    """(entries, readable). Never raises.

    A directory nobody may list is a fact about the tree, so it becomes a row
    with a marker. Raising here would lose the whole tree over one folder.
    """
    try:
        with os.scandir(directory) as scan:
            return list(scan), True
    except OSError:
        return [], False


def _is_dir(entry):
    """A real directory, not a link to one.

    follow_symlinks=False is what stops a link pointing at its own parent from
    being walked forever.
    """
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


def _is_link(entry):
    try:
        return entry.is_symlink()
    except OSError:
        return False


def _entry_size(entry):
    """Bytes on disk, or None when the entry cannot be measured.

    lstat, so a broken symlink reports the link itself instead of failing on a
    target that is not there.
    """
    try:
        return entry.stat(follow_symlinks=False).st_size
    except OSError:
        return None


def _has_children(directory):
    """True only when something really is below this point.

    The depth note tells the caller more exists. That claim gets checked rather
    than assumed, and machinery does not count towards it: a folder holding
    nothing but node_modules has nothing more to show.
    """
    entries, readable = _children(directory)
    if not readable:
        return False
    return any(not (_is_dir(entry) and entry.name in WORKSPACE_SKIP)
               for entry in entries)


def _walk(directory, prefix, depth_left, state, lines):
    entries, readable = _children(directory)
    if not readable:
        lines.append(prefix + LAST + "[unreadable]")
        state["unreadable"] += 1
        return
    # Pruned during the walk exactly as iter_workspace_files prunes, so .git and
    # node_modules are never descended into at all. Directories only, which is
    # what that function does too.
    dirs = sorted((e for e in entries if _is_dir(e) and e.name not in WORKSPACE_SKIP),
                  key=lambda e: e.name)
    files = sorted((e for e in entries if not _is_dir(e)), key=lambda e: e.name)
    rows = dirs + files
    for index, entry in enumerate(rows):
        if state["count"] >= state["limit"]:
            state["hit_limit"] = True
            return
        state["count"] += 1
        last = index == len(rows) - 1
        connector = LAST if last else BRANCH
        if index < len(dirs):
            state["dirs"] += 1
            lines.append(prefix + connector + entry.name + "/")
            child_prefix = prefix + (BLANK if last else PIPE)
            if depth_left > 1:
                _walk(entry.path, child_prefix, depth_left - 1, state, lines)
            elif _has_children(entry.path):
                state["hit_depth"] = True
        else:
            state["files"] += 1
            size = _entry_size(entry)
            if size is None:
                state["unreadable"] += 1
                detail = "[unreadable]"
            else:
                state["bytes"] += size
                detail = format_size(size)
            if _is_link(entry):
                detail += ", link"
            lines.append("%s%s%s  (%s)" % (prefix, connector, entry.name, detail))


def _plural(count, singular, plural):
    return "%d %s" % (count, singular if count == 1 else plural)


def _as_int(value, default, floor):
    if value in (None, ""):
        return default
    try:
        return max(floor, int(value))
    except (TypeError, ValueError):
        return None


def _path_size(target):
    """Size of a path handed in directly rather than reached through scandir."""
    try:
        return target.lstat().st_size
    except OSError:
        return None


def _label_for(root):
    try:
        relative = str(root.relative_to(workspace())).replace("\\", "/")
    except ValueError:
        relative = str(root)
    return "." if relative in ("", ".") else relative


def tree(path=None, depth=None, limit=None):
    """Render the directory tree under `path`, sizes included, contents not.

    `path` goes through safe_path, so anything outside the workspace raises
    ValueError and the agent loop hands the model a correction. Both ceilings
    name themselves in the output when they are the reason the tree stops --
    a truncated tree that reads as complete is worse than no tree at all.
    """
    depth_limit = _as_int(depth, TREE_MAX_DEPTH, 1)
    entry_limit = _as_int(limit, TREE_MAX_ENTRIES, 1)
    if depth_limit is None or entry_limit is None:
        return "depth and limit must be whole numbers"

    root = safe_path(path) if path else Path(workspace())
    if not root.exists():
        return "Path not found: %s" % (path if path else root)

    label = _label_for(root)
    if not root.is_dir():
        # A file is a legitimate answer to "show me this path", and a one-row
        # tree with the same summary shape is less surprising than an error.
        size = _path_size(root)
        measured = "[unreadable]" if size is None else format_size(size)
        return "%s  (%s)\n\n0 directories, 1 file, %s total." % (
            label, measured, format_size(size or 0))

    state = {"count": 0, "dirs": 0, "files": 0, "bytes": 0, "unreadable": 0,
             "limit": entry_limit, "hit_limit": False, "hit_depth": False}
    lines = ["." if label == "." else label + "/"]
    _walk(root, "", depth_limit, state, lines)
    if state["count"] == 0:
        lines.append("(empty)")

    truncated = state["hit_limit"] or state["hit_depth"]
    totals = "%s, %s, %s" % (_plural(state["dirs"], "directory", "directories"),
                             _plural(state["files"], "file", "files"),
                             format_size(state["bytes"]))
    lines.append("")
    if truncated:
        lines.append(totals + " shown -- this is not the whole tree.")
    else:
        lines.append(totals + " total.")
    if state["hit_limit"]:
        lines.append("Stopped at the %d entry limit; more entries exist. Raise "
                     "'limit', or point 'path' at a subfolder." % entry_limit)
    if state["hit_depth"]:
        lines.append("Stopped at depth %d; directories below it were not opened. "
                     "Raise 'depth' to see them." % depth_limit)
    if state["unreadable"]:
        lines.append("%s marked [unreadable]: permission, or gone mid-walk."
                     % _plural(state["unreadable"], "entry", "entries"))
    return "\n".join(lines)
