"""Tests for the directory tree.

Every one of these builds its own throwaway workspace and points
agent_config.ROOT_DIR at it, because a test that read the real TMT repository
would pass or fail on whatever happened to be checked out that day.
"""

import os
import shutil
import stat
import tempfile
from pathlib import Path

import agent_config
import agent_tree


def remove_tree(path):
    """Delete a temp tree, including anything left read-only on Windows."""
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


class Workspace:
    """A throwaway directory as the workspace root.

    close() restores agent_config.ROOT_DIR and must run in a finally block: a
    leaked root points every later test at a directory that has been deleted.
    """

    def __init__(self, files=None):
        self.previous_root = agent_config.ROOT_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_tree_")).resolve()
        for name, body in (files or {}).items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            # Bytes, not text: write_text turns "\n" into "\r\n" on Windows, and
            # every size assertion below would then be off by one per line.
            target.write_bytes(body.encode("utf-8"))
        agent_config.ROOT_DIR = self.path

    def close(self):
        agent_config.ROOT_DIR = self.previous_root
        remove_tree(self.path)


def summary_of(rendered):
    """The counts line: the last line that names directories."""
    for line in reversed(rendered.splitlines()):
        if "director" in line:
            return line
    raise AssertionError("no summary line in:\n" + rendered)


# --- shape ------------------------------------------------------------------

def test_nested_directories_render_with_tree_connectors():
    box = Workspace(files={"src/deep/leaf.txt": "x\n", "src/top.py": "y\n",
                           "readme.md": "z\n"})
    try:
        rendered = agent_tree.tree()
        assert "├── " in rendered, rendered
        assert "└── " in rendered, rendered
        assert "│   " in rendered, rendered
        lines = rendered.splitlines()
        # Directories before files at every level, each sorted by name.
        assert lines[0] == ".", lines[0]
        assert lines[1] == "├── src/", lines[1]
        names = [line for line in lines if "src/" in line or "readme.md" in line]
        assert names.index("├── src/") < len(names) - 1, names
        assert names[-1].endswith("readme.md  (2 B)"), names[-1]
        # leaf.txt sits two levels down, so its row carries a continuation.
        leaf = [line for line in lines if "leaf.txt" in line][0]
        assert leaf.startswith("│   ") or leaf.startswith("    "), leaf
    finally:
        box.close()


def test_a_subtree_is_labelled_with_its_relative_path():
    box = Workspace(files={"src/pkg/mod.py": "x\n", "other/junk.txt": "y\n"})
    try:
        rendered = agent_tree.tree(path="src")
        assert rendered.splitlines()[0] == "src/", rendered
        assert "mod.py" in rendered, rendered
        assert "junk.txt" not in rendered, rendered
    finally:
        box.close()


def test_file_sizes_are_shown_next_to_each_file():
    box = Workspace(files={"small.txt": "abc", "bigger.txt": "x" * 4300})
    try:
        rendered = agent_tree.tree()
        assert "small.txt  (3 B)" in rendered, rendered
        assert "bigger.txt  (4.2 KB)" in rendered, rendered
    finally:
        box.close()


def test_an_empty_directory_says_so_and_counts_nothing():
    box = Workspace()
    try:
        rendered = agent_tree.tree()
        assert "(empty)" in rendered, rendered
        assert "0 directories, 0 files, 0 B total." in rendered, rendered
    finally:
        box.close()


# --- honest counts ----------------------------------------------------------

def test_the_summary_counts_what_is_really_there():
    box = Workspace(files={"a/one.txt": "1" * 10, "a/two.txt": "2" * 20,
                           "b/c/three.txt": "3" * 30, "root.txt": "4" * 40})
    try:
        rendered = agent_tree.tree()
        line = summary_of(rendered)
        assert line == "3 directories, 4 files, 100 B total.", line
        assert "not the whole tree" not in rendered, rendered
    finally:
        box.close()


def test_a_single_directory_and_file_are_not_pluralised():
    box = Workspace(files={"a/one.txt": "12345"})
    try:
        line = summary_of(agent_tree.tree())
        assert line == "1 directory, 1 file, 5 B total.", line
    finally:
        box.close()


# --- ceilings ---------------------------------------------------------------

def test_the_depth_limit_stops_the_walk_and_names_itself():
    box = Workspace(files={"a/b/c/deep.txt": "x\n", "a/shallow.txt": "y\n"})
    try:
        rendered = agent_tree.tree(depth=2)
        assert "shallow.txt" in rendered, rendered
        assert "deep.txt" not in rendered, rendered
        assert "Stopped at depth 2" in rendered, rendered
        assert "not the whole tree" in rendered, rendered
    finally:
        box.close()


def test_a_depth_that_reaches_the_bottom_claims_no_truncation():
    box = Workspace(files={"a/b/leaf.txt": "x\n"})
    try:
        rendered = agent_tree.tree(depth=3)
        assert "leaf.txt" in rendered, rendered
        assert "Stopped at depth" not in rendered, rendered
        assert "total." in rendered, rendered
    finally:
        box.close()


def test_a_directory_holding_only_machinery_is_not_called_truncated():
    """The depth note says more exists, so it is checked rather than assumed.
    A folder whose only child is pruned machinery has nothing more to show,
    and claiming otherwise sends the model back for a deeper tree for nothing."""
    box = Workspace(files={"a/node_modules/pkg.js": "j\n", "top.txt": "x\n"})
    try:
        rendered = agent_tree.tree(depth=1)
        assert "a/" in rendered and "top.txt" in rendered, rendered
        assert "Stopped at depth" not in rendered, rendered
        assert "not the whole tree" not in rendered, rendered
    finally:
        box.close()


def test_the_entry_limit_caps_the_rows_and_names_itself():
    box = Workspace(files={"f%02d.txt" % i: "x\n" for i in range(20)})
    try:
        rendered = agent_tree.tree(limit=5)
        rows = [line for line in rendered.splitlines() if ".txt" in line]
        assert len(rows) == 5, rows
        assert "Stopped at the 5 entry limit" in rendered, rendered
        assert summary_of(rendered).startswith("0 directories, 5 files"), rendered
        assert "not the whole tree" in rendered, rendered
    finally:
        box.close()


def test_string_limits_from_the_model_are_accepted_and_nonsense_is_refused():
    box = Workspace(files={"a/b/c/d/e.txt": "x\n"})
    try:
        assert "Stopped at depth 1" in agent_tree.tree(depth="1")
        assert agent_tree.tree(depth="deep") == "depth and limit must be whole numbers"
        assert agent_tree.tree(limit="many") == "depth and limit must be whole numbers"
    finally:
        box.close()


# --- what is never walked ---------------------------------------------------

def test_machinery_directories_are_pruned_exactly_as_the_walker_prunes_them():
    box = Workspace(files={"src/a.py": "a\n", "node_modules/pkg/index.js": "junk\n",
                           ".git/config": "[core]\n", "__pycache__/a.pyc": "b\n"})
    try:
        rendered = agent_tree.tree()
        assert "a.py" in rendered, rendered
        for absent in ("node_modules", ".git", "__pycache__", "index.js", "config"):
            assert absent not in rendered, (absent, rendered)
        assert summary_of(rendered) == "1 directory, 1 file, 2 B total.", rendered
    finally:
        box.close()


def test_a_path_outside_the_workspace_raises_rather_than_rendering():
    box = Workspace(files={"inside.txt": "x\n"})
    try:
        refused = False
        try:
            agent_tree.tree(path="../..")
        except ValueError:
            refused = True
        assert refused, "a path above the root must be refused"
    finally:
        box.close()


def test_a_missing_path_is_reported_not_raised():
    box = Workspace()
    try:
        assert "Path not found" in agent_tree.tree(path="nowhere")
    finally:
        box.close()


def test_a_file_given_as_the_path_renders_as_one_row():
    box = Workspace(files={"only.txt": "hello"})
    try:
        rendered = agent_tree.tree(path="only.txt")
        assert "only.txt  (5 B)" in rendered, rendered
        assert "0 directories, 1 file, 5 B total." in rendered, rendered
    finally:
        box.close()


# --- the disk misbehaving ---------------------------------------------------

def test_an_unreadable_directory_is_marked_rather_than_raising():
    """chmod does not deny a listing on Windows, so the refusal is injected at
    os.scandir instead. The point under test is the handling, not the OS."""
    box = Workspace(files={"locked/hidden.txt": "x\n", "open/seen.txt": "y\n"})
    real_scandir = agent_tree.os.scandir

    def refusing_scandir(target):
        if os.path.basename(str(target)) == "locked":
            raise PermissionError(13, "Permission denied")
        return real_scandir(target)

    try:
        agent_tree.os.scandir = refusing_scandir
        rendered = agent_tree.tree()
        assert "[unreadable]" in rendered, rendered
        assert "locked/" in rendered, rendered
        assert "seen.txt" in rendered, rendered
        assert "hidden.txt" not in rendered, rendered
        assert "marked [unreadable]" in rendered, rendered
    finally:
        agent_tree.os.scandir = real_scandir
        box.close()


def test_a_file_that_vanishes_mid_walk_is_marked_rather_than_raising():
    box = Workspace(files={"here.txt": "x\n"})
    saved = agent_tree._entry_size

    def gone(_entry):
        return None

    try:
        agent_tree._entry_size = gone
        rendered = agent_tree.tree()
        assert "here.txt  ([unreadable])" in rendered, rendered
        assert "marked [unreadable]" in rendered, rendered
        # A size nobody could read is never guessed at in the total.
        assert "0 B total" in rendered or "0 B shown" in rendered, rendered
    finally:
        agent_tree._entry_size = saved
        box.close()


def test_a_broken_symlink_is_shown_and_does_not_raise():
    box = Workspace(files={"real.txt": "x\n"})
    try:
        try:
            os.symlink(str(box.path / "absent.txt"), str(box.path / "dangling.txt"))
        except (OSError, NotImplementedError, AttributeError):
            return          # Windows without the privilege: nothing to test.
        rendered = agent_tree.tree()
        assert "dangling.txt" in rendered, rendered
        assert "link" in rendered, rendered
    finally:
        box.close()


def test_a_directory_symlink_is_shown_but_never_descended_into():
    """A link pointing at its own parent is the loop this guards against."""
    box = Workspace(files={"real/inside.txt": "x\n"})
    try:
        try:
            os.symlink(str(box.path), str(box.path / "loop"), target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError, TypeError):
            return
        rendered = agent_tree.tree()
        assert "loop" in rendered, rendered
        assert rendered.count("inside.txt") == 1, rendered
    finally:
        box.close()


# --- format_size ------------------------------------------------------------

def test_format_size_boundaries():
    assert agent_tree.format_size(0) == "0 B"
    assert agent_tree.format_size(1) == "1 B"
    assert agent_tree.format_size(812) == "812 B"
    assert agent_tree.format_size(1023) == "1023 B"
    assert agent_tree.format_size(1024) == "1.0 KB"
    assert agent_tree.format_size(4300) == "4.2 KB"
    # 1023.999 KB must round up into megabytes rather than print "1024.0 KB".
    assert agent_tree.format_size(1024 * 1024 - 1) == "1.0 MB"
    assert agent_tree.format_size(1024 * 1024) == "1.0 MB"
    assert agent_tree.format_size(1153434) == "1.1 MB"
    assert agent_tree.format_size(3 * 1024 ** 3) == "3.0 GB"
    assert agent_tree.format_size(2 * 1024 ** 4) == "2.0 TB"


def test_format_size_never_raises_on_rubbish():
    assert agent_tree.format_size(None) == "? B"
    assert agent_tree.format_size("nine") == "? B"


# --- the module's own promise ----------------------------------------------

def test_the_tree_never_opens_a_file():
    """Sizes come from stat. A tree that read contents would spend a large
    repository's worth of time and tokens answering a question about shape."""
    source = Path(agent_tree.__file__).read_text(encoding="utf-8")
    for forbidden in ("read_text", "open(", "read_bytes", "readlines"):
        assert forbidden not in source, forbidden
