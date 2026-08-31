"""Tests for diff-aware test selection.

Each builds a real git repository in a throwaway directory and makes a real
change in it, because the thing under test reads an actual diff. Nothing here
ever runs git against the TMT checkout itself: a test that did would report on
whatever the developer happened to have uncommitted.

If git is not on PATH the tests return early rather than fail. A missing
toolchain is not a defect in this module.
"""

import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import agent_config
import agent_testsel


def remove_tree(path):
    """Delete a temp tree, including anything left read-only on Windows."""
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


def git_available():
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


class Repo:
    """A throwaway git repository as the workspace root.

    close() restores agent_config.ROOT_DIR and must run in a finally block: a
    leaked root points every later test at a directory that has been deleted.
    """

    def __init__(self, files=None):
        self.previous_root = agent_config.ROOT_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_tsel_")).resolve()
        agent_config.ROOT_DIR = self.path
        for name, body in (files or {}).items():
            self.write(name, body)

    def write(self, name, body):
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        # Bytes, so the diff the tool reads has the endings this test wrote.
        target.write_bytes(body.encode("utf-8"))
        return target

    def git(self, *args):
        return subprocess.run(["git"] + list(args), cwd=str(self.path),
                              capture_output=True, text=True, timeout=60)

    def init(self):
        self.git("init", "-q", ".")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        self.git("config", "commit.gpgsign", "false")

    def commit(self, message="init"):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def close(self):
        agent_config.ROOT_DIR = self.previous_root
        remove_tree(self.path)


SOURCE = (
    "def replace_text(value):\n"
    "    return value\n"
    "\n"
    "\n"
    "def read_file(path):\n"
    "    return path\n"
)

TESTS = (
    "def test_replace_text():\n"
    "    assert replace_text(1) == 1\n"
    "\n"
    "\n"
    "def test_something_else():\n"
    "    assert True\n"
)


def changed_repo():
    """A repository with one committed module and one real edit on top."""
    box = Repo(files={"agent_file_ops.py": SOURCE,
                      "test_agent_file_ops.py": TESTS,
                      "test_agent_cli.py": "def test_cli():\n    assert True\n"})
    box.init()
    box.commit()
    box.write("agent_file_ops.py", SOURCE.replace("return value", "return value + 1"))
    return box


# --- what the diff actually proves -------------------------------------------

def test_a_changed_module_is_read_from_the_diff_with_its_symbol():
    """The first section is the only one allowed to be stated as fact, so it
    has to come from the diff rather than from the filename."""
    if not git_available():
        return
    box = changed_repo()
    try:
        rendered = agent_testsel.related_tests()
        assert "agent_file_ops.py" in rendered, rendered
        # The symbol is worked out from the changed line, not guessed from the
        # file name -- read_file is in the same file and was NOT touched.
        assert "replace_text" in rendered, rendered
        head = rendered.split("Direct matches")[0]
        assert "replace_text" in head, head
        assert "read_file" not in head, head
    finally:
        box.close()


def test_a_test_that_names_a_changed_symbol_is_a_direct_match_with_its_reason():
    if not git_available():
        return
    box = changed_repo()
    try:
        rendered = agent_testsel.related_tests()
        direct = rendered.split("Direct matches")[1].split("Possibly affected")[0]
        assert "test_agent_file_ops.py" in direct, direct
        assert "test_replace_text" in direct, direct
        # The evidence is stated, because a claim without one is a guess
        # wearing a fact's clothes.
        assert "replace_text" in direct and "names" in direct, direct
    finally:
        box.close()


def test_guesses_are_kept_apart_from_evidence():
    """The separation is the feature. This module is mostly heuristics, and a
    heuristic presented as a measurement is the one thing forbidden here."""
    if not git_available():
        return
    box = changed_repo()
    try:
        rendered = agent_testsel.related_tests()
        assert "Changed" in rendered, rendered
        assert "Direct matches" in rendered, rendered
        assert "Possibly affected" in rendered, rendered
        lowered = rendered.lower()
        assert "guess" in lowered, rendered
        assert "fact" in lowered or "measured" in lowered, rendered
        # Order matters: facts first, guesses last.
        assert rendered.index("Changed") < rendered.index("Direct matches")
        assert rendered.index("Direct matches") < rendered.index("Possibly affected")
    finally:
        box.close()


# --- the honest empty answers ------------------------------------------------

def test_no_repository_is_said_plainly_rather_than_invented():
    if not git_available():
        return
    box = Repo(files={"a.py": "x = 1\n"})       # never git init'd
    try:
        rendered = agent_testsel.related_tests()
        assert "a.py" not in rendered, rendered
        lowered = rendered.lower()
        assert "repositor" in lowered or "git" in lowered, rendered
    finally:
        box.close()


def test_an_empty_diff_is_said_plainly():
    if not git_available():
        return
    box = Repo(files={"agent_file_ops.py": SOURCE})
    box.init()
    box.commit()
    try:
        rendered = agent_testsel.related_tests()
        lowered = rendered.lower()
        assert "no change" in lowered or "nothing" in lowered or "empty" in lowered, rendered
    finally:
        box.close()


def test_a_changed_file_with_no_test_does_not_have_one_invented():
    if not git_available():
        return
    box = Repo(files={"lonely.py": "def alone():\n    return 1\n"})
    box.init()
    box.commit()
    box.write("lonely.py", "def alone():\n    return 2\n")
    try:
        rendered = agent_testsel.related_tests()
        assert "lonely.py" in rendered, rendered
        direct = rendered.split("Direct matches")[1].split("Possibly affected")[0]
        # No test file exists at all, so nothing may be claimed as direct. The
        # tool says so in words and names the file it did NOT invent, so the
        # assertion is that nothing was claimed, not that the name is absent.
        assert "None" in direct, direct
        assert "invented" in direct or "no test_lonely.py exists" in direct, direct
    finally:
        box.close()


# --- the pieces, tested without needing a repository -------------------------

def test_the_diff_parser_reads_files_and_hunks():
    diff = (
        "diff --git a/agent_file_ops.py b/agent_file_ops.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/agent_file_ops.py\n"
        "+++ b/agent_file_ops.py\n"
        "@@ -1,4 +1,5 @@\n"
        " def replace_text(value):\n"
        "-    return value\n"
        "+    return value + 1\n"
        "+def newly_added(value):\n"
    )
    changed = agent_testsel.parse_diff(diff)
    assert len(changed) == 1, changed
    entry = changed[0]
    assert "agent_file_ops.py" in entry.path, entry.path
    assert entry.hunks == 1, entry.hunks
    # A declaration ON a changed line is read straight out of the diff. The
    # body line above it is not one, and resolving THAT to its enclosing
    # function needs the file on disk, which a synthetic diff does not have --
    # so the parser alone is right to report only what it can see.
    assert "newly_added" in entry.symbols, entry.symbols
    # And the reason it is believed changed is recorded, because a symbol with
    # no account of how it was found is the unevidenced claim to avoid.
    assert entry.symbols["newly_added"], entry.symbols


def test_test_functions_are_read_out_of_a_file():
    found = dict(agent_testsel.test_functions(TESTS))
    assert "test_replace_text" in found, sorted(found)
    assert "test_something_else" in found, sorted(found)
    # The module function is not a test, so it is not collected as one.
    assert "replace_text" not in found, sorted(found)


def test_output_is_capped_so_it_cannot_flood_the_context():
    """A result too large for the context is the same as no result."""
    if not git_available():
        return
    files = {"mod_%02d.py" % n: "def fn_%02d():\n    return %d\n" % (n, n)
             for n in range(60)}
    box = Repo(files=files)
    box.init()
    box.commit()
    for name in files:
        box.write(name, files[name].replace("return", "return 1 +"))
    try:
        rendered = agent_testsel.related_tests()
        assert len(rendered.splitlines()) <= agent_testsel.MAX_OUTPUT_LINES + 5, \
            len(rendered.splitlines())
    finally:
        box.close()
