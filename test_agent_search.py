"""Tests for the exact search and the bulk replace.

These two actions are the ones a model reaches for when it is about to edit
something, so what they get wrong it then writes down. The tests run against
real directories on a real disk, because every hazard here -- a binary file, a
CRLF file, a path pointing out of the workspace -- is a property of the disk
and not of a mock.
"""

import os
import shutil
import stat
import tempfile
from pathlib import Path

import agent_config
import agent_file_ops


def remove_tree(path):
    """Delete a temp tree, including read-only files on Windows."""
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


class Workspace:
    """A throwaway directory pointed at by agent_config.ROOT_DIR.

    close() restores the previous root and must run in a finally block: a
    leaked root would point every later test at a deleted directory.
    """

    def __init__(self, files=None):
        self.previous_root = agent_config.ROOT_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_search_")).resolve()
        for name, body in (files or {}).items():
            self.write(name, body)
        agent_config.set_workspace_root(self.path)

    def write(self, name, body):
        """Bytes go down exactly as given -- the line-ending tests depend on
        nothing translating on the way in either."""
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            target.write_bytes(body)
        else:
            target.write_bytes(body.encode("utf-8"))
        return target

    def read(self, name):
        return (self.path / name).read_bytes()

    def close(self):
        agent_config.ROOT_DIR = self.previous_root
        remove_tree(self.path)


# --- find_text: locating exact code -----------------------------------------

def test_find_text_reports_the_path_and_the_line_number():
    box = Workspace(files={"src/a.py": "one\ntwo\nTARGET here\nfour\n"})
    try:
        out = agent_file_ops.find_text("TARGET")
        assert "src/a.py:3:" in out, out
        assert "     3 > TARGET here" in out, out
        assert "1 match in 1 file" in out, out
    finally:
        box.close()


def test_find_text_matches_a_block_spanning_several_lines():
    """The stated reason this exists: search_files works a line at a time and
    cannot find the five lines a model is about to replace."""
    body = "head\ndef f():\n    a = 1\n    return a\ntail\n"
    box = Workspace(files={"m.py": body, "other.py": "def f():\n    pass\n"})
    try:
        out = agent_file_ops.find_text("def f():\n    a = 1\n    return a")
        assert "1 match in 1 file" in out, out
        assert "m.py:2:" in out, out
        # every line of the block is marked as match, not as context
        assert "     2 > def f():" in out, out
        assert "     3 >     a = 1" in out, out
        assert "     4 >     return a" in out, out
        assert "other.py" not in out, out
    finally:
        box.close()


def test_find_text_context_lines_are_marked_apart_from_the_match():
    """Colour is never the message, so the two have to read apart in plain
    text: `>` is the match, `|` is the surrounding."""
    box = Workspace(files={"a.txt": "one\ntwo\nHIT\nfour\nfive\n"})
    try:
        out = agent_file_ops.find_text("HIT", context=1)
        assert "     2 | two" in out, out
        assert "     3 > HIT" in out, out
        assert "     4 | four" in out, out
        assert "one" not in out, out
        assert "five" not in out, out
    finally:
        box.close()


def test_find_text_with_no_context_shows_only_the_match():
    box = Workspace(files={"a.txt": "one\nHIT\nthree\n"})
    try:
        out = agent_file_ops.find_text("HIT")
        assert "HIT" in out
        assert "one" not in out, out
        assert "three" not in out, out
    finally:
        box.close()


def test_find_text_filters_by_glob():
    box = Workspace(files={"src/deep/a.py": "NEEDLE\n",
                           "src/b.py": "NEEDLE\n",
                           "docs/c.md": "NEEDLE\n",
                           "top.py": "NEEDLE\n"})
    try:
        out = agent_file_ops.find_text("NEEDLE", glob="src/**/*.py")
        assert "src/deep/a.py" in out, out
        assert "src/b.py" in out, out
        assert "docs/c.md" not in out, out
        assert "top.py" not in out, out
        assert "2 matches in 2 files" in out, out
        # a bare pattern means "anywhere", which is what a model intends by it
        every = agent_file_ops.find_text("NEEDLE", glob="*.py")
        assert "3 matches in 3 files" in every, every
        assert "docs/c.md" not in every, every
    finally:
        box.close()


def test_find_text_never_dumps_a_binary_file():
    box = Workspace(files={"good.txt": "NEEDLE\n",
                           "blob.bin": b"NEEDLE\x00\x01\x02rubbish"})
    try:
        out = agent_file_ops.find_text("NEEDLE")
        assert "good.txt" in out, out
        assert "blob.bin" not in out, out
        assert "rubbish" not in out, out
        assert "1 match in 1 file" in out, out
        assert "1 binary file skipped" in out, out
    finally:
        box.close()


def test_find_text_does_not_search_pruned_directories():
    """Same rule as the walk: machinery is never descended into, so a match
    inside node_modules is not a match at all."""
    box = Workspace(files={"src/a.py": "NEEDLE\n",
                           "node_modules/pkg/index.js": "NEEDLE\n",
                           ".git/config": "NEEDLE\n",
                           "__pycache__/x.txt": "NEEDLE\n"})
    try:
        out = agent_file_ops.find_text("NEEDLE")
        assert "1 match in 1 file" in out, out
        assert "src/a.py" in out, out
        assert "node_modules" not in out, out
        assert ".git" not in out, out
        assert "__pycache__" not in out, out
    finally:
        box.close()


def test_find_text_states_the_real_total_and_says_when_it_capped():
    """The header counts everything found; the cap only governs what is shown.
    A header reporting the shown figure would understate the work left every
    single time it truncated."""
    box = Workspace(files={"a.txt": "hit\nhit\nhit\nhit\nhit\n"})
    try:
        out = agent_file_ops.find_text("hit", limit=2)
        assert "5 matches in 1 file" in out, out
        assert "showing the first 2" in out, out
        assert out.count("> hit") == 2, out
    finally:
        box.close()


def test_find_text_totals_are_real_across_files():
    box = Workspace(files={"a.txt": "hit x hit\n", "b.txt": "hit\n",
                           "c.txt": "nothing\n"})
    try:
        out = agent_file_ops.find_text("hit")
        assert "3 matches in 2 files" in out, out
    finally:
        box.close()


def test_find_text_is_case_sensitive_where_search_files_is_not():
    """The whole reason for a second search action. search_files stays as it
    was -- these are two different questions, not two spellings of one."""
    box = Workspace(files={"a.py": "Alpha = 1\n"})
    try:
        missed = agent_file_ops.find_text("alpha")
        assert "No exact match" in missed, missed
        assert "case-insensitive" in missed, missed
        assert "a.py" in agent_file_ops.find_text("Alpha")
        assert "a.py" in agent_file_ops.search_files("alpha")
    finally:
        box.close()


def test_find_text_refuses_a_path_outside_the_workspace():
    box = Workspace(files={"a.txt": "x\n"})
    try:
        refused = False
        try:
            agent_file_ops.find_text("x", path="../..")
        except ValueError:
            refused = True
        assert refused, "a path above the root must be refused, not searched"
    finally:
        box.close()


def test_find_text_can_be_pointed_at_one_subtree():
    box = Workspace(files={"src/a.txt": "NEEDLE\n", "other/b.txt": "NEEDLE\n"})
    try:
        out = agent_file_ops.find_text("NEEDLE", path="src")
        assert "src/a.txt" in out, out
        assert "other/b.txt" not in out, out
        assert "1 match in 1 file" in out, out
    finally:
        box.close()


def test_find_text_refuses_an_empty_query():
    box = Workspace(files={"a.txt": "x\n"})
    try:
        assert "nothing to look for" in agent_file_ops.find_text("")
    finally:
        box.close()


# --- replace_across: the bulk edit ------------------------------------------

def test_replace_across_previews_by_default_and_writes_nothing():
    """The asymmetry that keeps a repository intact: a bulk edit nobody looked
    at first is how everything gets rewritten at once."""
    box = Workspace(files={"a.txt": "old and old\n", "b.txt": "old\n"})
    try:
        before = (box.read("a.txt"), box.read("b.txt"))
        out = agent_file_ops.replace_across("old", "new")
        assert "Preview only, nothing written" in out, out
        assert "3 occurrences in 2 files would change" in out, out
        assert "a.txt: 2 occurrences would change" in out, out
        assert "b.txt: 1 occurrence would change" in out, out
        assert '"apply": true' in out, out
        assert (box.read("a.txt"), box.read("b.txt")) == before
    finally:
        box.close()


def test_replace_across_apply_changes_exactly_what_it_reported():
    box = Workspace(files={"a.txt": "old and old\n", "b.txt": "old\n",
                           "c.txt": "untouched\n"})
    try:
        preview = agent_file_ops.replace_across("old", "new")
        out = agent_file_ops.replace_across("old", "new", apply=True)
        assert "3 occurrences in 2 files changed" in out, out
        assert "Preview" not in out, out
        assert box.read("a.txt") == b"new and new\n"
        assert box.read("b.txt") == b"new\n"
        assert box.read("c.txt") == b"untouched\n"
        # preview promised the same set of files it then changed
        for name in ("a.txt", "b.txt"):
            assert name in preview and name in out
        assert "c.txt" not in preview and "c.txt" not in out
    finally:
        box.close()


def test_replace_across_counts_every_occurrence_not_every_line():
    box = Workspace(files={"a.txt": "xx xx\nxx\n"})
    try:
        out = agent_file_ops.replace_across("xx", "y", apply=True)
        assert "3 occurrences in 1 file changed" in out, out
        assert box.read("a.txt") == b"y y\ny\n"
    finally:
        box.close()


def test_replace_across_handles_a_multi_line_search():
    box = Workspace(files={"m.py": "def f():\n    return 1\n\nz = 2\n"})
    try:
        out = agent_file_ops.replace_across("def f():\n    return 1",
                                           "def f():\n    return 2",
                                           apply=True)
        assert "1 occurrence in 1 file changed" in out, out
        assert box.read("m.py") == b"def f():\n    return 2\n\nz = 2\n"
    finally:
        box.close()


def test_replace_across_leaves_crlf_as_crlf_and_lf_as_lf():
    """core.autocrlf is set in this repo. A whole-file ending flip buries the
    one real change under a diff of every line in the file."""
    box = Workspace(files={"win.txt": b"one\r\ntwo\r\nthree\r\n",
                           "unix.txt": b"one\ntwo\nthree\n"})
    try:
        out = agent_file_ops.replace_across("two", "2", apply=True)
        assert "2 occurrences in 2 files changed" in out, out
        assert box.read("win.txt") == b"one\r\n2\r\nthree\r\n"
        assert box.read("unix.txt") == b"one\n2\nthree\n"
    finally:
        box.close()


def test_a_multi_line_search_matches_a_crlf_file_and_keeps_its_endings():
    """The needle arrives with LF in it whatever the file uses, so it is
    re-expressed in the file's own endings before anything is matched."""
    box = Workspace(files={"m.py": b"a = 1\r\ndef f():\r\n    return 1\r\n"})
    try:
        out = agent_file_ops.replace_across("def f():\n    return 1",
                                           "def f():\n    return 2",
                                           apply=True)
        assert "1 occurrence in 1 file changed" in out, out
        assert box.read("m.py") == b"a = 1\r\ndef f():\r\n    return 2\r\n"
    finally:
        box.close()


def test_replace_across_skips_a_replacement_that_would_break_python():
    """Same rule write_file and patch_file already follow: unparseable Python
    is never written, and the file that would have got it is named."""
    box = Workspace(files={"broken.py": "value = 1\n", "fine.txt": "value = 1\n"})
    try:
        out = agent_file_ops.replace_across("value = 1", "value = (", apply=True)
        assert "SKIPPED broken.py" in out, out
        assert "would not parse" in out, out
        assert box.read("broken.py") == b"value = 1\n"
        # the .txt was never at risk and is still changed
        assert box.read("fine.txt") == b"value = (\n"
        assert "1 occurrence in 1 file changed" in out, out
    finally:
        box.close()


def test_the_preview_names_the_same_skip_the_apply_would():
    box = Workspace(files={"broken.py": "value = 1\n"})
    try:
        preview = agent_file_ops.replace_across("value = 1", "value = (")
        assert "SKIPPED broken.py" in preview, preview
        assert box.read("broken.py") == b"value = 1\n"
    finally:
        box.close()


def test_replace_across_refuses_an_empty_search():
    box = Workspace(files={"a.txt": "content\n"})
    try:
        out = agent_file_ops.replace_across("", "x", apply=True)
        assert "non-empty 'search'" in out, out
        assert box.read("a.txt") == b"content\n"
    finally:
        box.close()


def test_replace_across_never_touches_a_binary_file():
    box = Workspace(files={"a.txt": "old\n", "blob.bin": b"old\x00\x01old"})
    try:
        out = agent_file_ops.replace_across("old", "new", apply=True)
        assert "1 occurrence in 1 file changed" in out, out
        assert "1 binary or non-UTF-8 file skipped" in out, out
        assert box.read("blob.bin") == b"old\x00\x01old"
        assert box.read("a.txt") == b"new\n"
    finally:
        box.close()


def test_replace_across_honours_the_glob():
    box = Workspace(files={"src/a.py": "old\n", "src/deep/b.py": "old\n",
                           "docs/c.md": "old\n"})
    try:
        out = agent_file_ops.replace_across("old", "new", glob="src/**/*.py",
                                            apply=True)
        assert "2 occurrences in 2 files changed" in out, out
        assert box.read("docs/c.md") == b"old\n"
        assert box.read("src/a.py") == b"new\n"
        assert box.read("src/deep/b.py") == b"new\n"
    finally:
        box.close()


def test_replace_across_refuses_a_path_outside_the_workspace():
    box = Workspace(files={"a.txt": "old\n"})
    try:
        refused = False
        try:
            agent_file_ops.replace_across("old", "new", path="../..", apply=True)
        except ValueError:
            refused = True
        assert refused, "nothing outside the workspace may ever be written"
        assert box.read("a.txt") == b"old\n"
    finally:
        box.close()


def test_replace_across_says_plainly_when_nothing_matched():
    box = Workspace(files={"a.txt": "content\n"})
    try:
        out = agent_file_ops.replace_across("absent", "x", apply=True)
        assert "0 occurrences" in out, out
        assert box.read("a.txt") == b"content\n"
    finally:
        box.close()


def test_replace_across_leaves_pruned_directories_alone():
    box = Workspace(files={"src/a.py": "old\n",
                           "node_modules/pkg/index.js": "old\n"})
    try:
        out = agent_file_ops.replace_across("old", "new", apply=True)
        assert "1 occurrence in 1 file changed" in out, out
        assert box.read("node_modules/pkg/index.js") == b"old\n"
    finally:
        box.close()
