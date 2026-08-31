"""Tests for the workspace root: how it is chosen, and what it bounds.

The root decides every path TMT may touch for a whole session, so these cover
the choosing of it rather than the using of it. They run against real
directories: the guard rails exist to stop a real mistake on a real disk.
"""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import agent_config
import agent_file_ops
import agent_prompt
import TMT

GIT = shutil.which("git")
INSTALL_DIR = Path(agent_config.__file__).resolve().parent


def remove_tree(path):
    """Delete a temp tree, including read-only .git objects on Windows."""
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


class Workspace:
    """A throwaway directory, optionally a git repository, as the root.

    Restores agent_config.ROOT_DIR in close(), which must run in a finally
    block: a leaked root would point every later test at a deleted directory.
    """

    def __init__(self, git=False, files=None):
        self.previous_root = agent_config.ROOT_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_ws_")).resolve()
        for name, body in (files or {}).items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        if git and GIT:
            self.git(["init", "-b", "main"])
            self.git(["config", "user.name", "Human Person"])
            self.git(["config", "user.email", "human@example.invalid"])

    def git(self, args):
        env = dict(os.environ)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                     "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
                     "GIT_DIR", "GIT_WORK_TREE"):
            env.pop(name, None)
        return subprocess.run(["git"] + args, cwd=str(self.path), env=env,
                              capture_output=True, text=True, timeout=60)

    def use(self):
        return agent_config.set_workspace_root(self.path)

    def close(self):
        agent_config.ROOT_DIR = self.previous_root
        agent_prompt.invalidate_prompt()
        remove_tree(self.path)


# --- what may be a workspace ------------------------------------------------

def test_a_filesystem_root_is_refused_outright():
    """Not a prompt. An agent that overwrites and deletes has no business at
    the top of a disk, and no confirmation should be able to allow it."""
    root = Path(Path.cwd().anchor or "/")
    reason = agent_config.workspace_refusal(root)
    assert reason and "filesystem root" in reason, reason


def test_the_home_directory_is_refused_outright():
    reason = agent_config.workspace_refusal(Path.home())
    assert reason and "home directory" in reason, reason


def test_a_missing_directory_is_refused_rather_than_created():
    """--dir selects a workspace; it does not bring one into being."""
    missing = Path(tempfile.gettempdir()) / "tmt_ws_definitely_absent_probe"
    assert not missing.exists()
    reason = agent_config.workspace_refusal(missing)
    assert reason and "does not exist" in reason, reason
    assert not missing.exists()


def test_a_file_is_not_a_workspace():
    handle = tempfile.NamedTemporaryFile(delete=False)
    handle.close()
    try:
        reason = agent_config.workspace_refusal(handle.name)
        assert reason and "not a directory" in reason, reason
    finally:
        os.unlink(handle.name)


def test_an_ordinary_project_directory_is_allowed():
    box = Workspace(files={"main.py": "print(1)\n"})
    try:
        assert agent_config.workspace_refusal(box.path) == ""
    finally:
        box.close()


# --- when to ask ------------------------------------------------------------

def test_a_git_repository_starts_without_being_questioned():
    """Version control is its own undo, so the common case stays quiet."""
    if not GIT:
        return
    box = Workspace(git=True, files={"main.py": "print(1)\n"})
    try:
        assert agent_config.workspace_needs_confirmation(box.path) is False
    finally:
        box.close()


def test_a_populated_directory_without_git_has_to_be_confirmed():
    """Files already there and no way to undo: the shape of a run started from
    the wrong place."""
    box = Workspace(files={"important.txt": "years of work\n"})
    try:
        assert agent_config.workspace_needs_confirmation(box.path) is True
    finally:
        box.close()


def test_an_empty_directory_needs_no_confirmation():
    box = Workspace()
    try:
        assert agent_config.workspace_needs_confirmation(box.path) is False
    finally:
        box.close()


def test_a_subdirectory_of_a_repository_counts_as_versioned():
    if not GIT:
        return
    box = Workspace(git=True)
    try:
        nested = box.path / "src" / "deep"
        nested.mkdir(parents=True)
        (nested / "a.txt").write_text("x", encoding="utf-8")
        assert agent_config.workspace_needs_confirmation(nested) is False
    finally:
        box.close()


# --- resolution at startup --------------------------------------------------

def test_dir_selects_the_workspace_and_resolves_it_absolutely():
    box = Workspace(git=True)
    try:
        resolved = TMT.resolve_workspace(str(box.path), ask=lambda _: "y")
        assert resolved == box.path.resolve()
        assert resolved.is_absolute()
        assert agent_config.ROOT_DIR == resolved
    finally:
        box.close()


def test_a_refused_workspace_stops_the_run_before_anything_is_touched():
    previous = agent_config.ROOT_DIR
    try:
        assert TMT.resolve_workspace(str(Path.home())) is None
        assert agent_config.ROOT_DIR == previous     # never moved
    finally:
        agent_config.ROOT_DIR = previous


def test_declining_the_confirmation_stops_the_run():
    box = Workspace(files={"important.txt": "years of work\n"})
    try:
        assert TMT.resolve_workspace(str(box.path), ask=lambda _: "n") is None
        assert (box.path / "important.txt").read_text(encoding="utf-8") == "years of work\n"
    finally:
        box.close()


def test_accepting_the_confirmation_continues():
    box = Workspace(files={"important.txt": "years of work\n"})
    try:
        assert TMT.resolve_workspace(str(box.path), ask=lambda _: "y") == box.path
    finally:
        box.close()


def test_with_no_dir_the_workspace_is_the_directory_tmt_was_started_in():
    """The whole point of the change: run TMT somewhere, work on that place."""
    box = Workspace(git=True, files={"main.py": "print(1)\n"})
    previous_cwd = Path.cwd()
    try:
        os.chdir(box.path)
        resolved = TMT.resolve_workspace(None, ask=lambda _: "n")
        assert resolved == box.path.resolve(), resolved
        assert agent_config.ROOT_DIR == box.path.resolve()
    finally:
        os.chdir(previous_cwd)
        box.close()


def test_the_default_is_read_at_startup_not_at_import():
    """A root fixed when the module loaded would be decided before the
    arguments that are supposed to decide it."""
    box = Workspace(git=True)
    previous_cwd = Path.cwd()
    try:
        os.chdir(box.path)
        assert agent_config.default_workspace() == box.path.resolve()
    finally:
        os.chdir(previous_cwd)
        box.close()


def test_importing_agent_config_creates_no_directory():
    """Import must not touch the disk. It used to mkdir the workspace."""
    probe = Path(tempfile.mkdtemp(prefix="tmt_import_probe_"))
    previous_cwd = Path.cwd()
    try:
        os.chdir(probe)
        subprocess.run([sys.executable, "-c", "import agent_config"],
                       cwd=str(probe), timeout=60,
                       env={**os.environ, "PYTHONPATH": str(INSTALL_DIR)},
                       capture_output=True, text=True)
        assert list(probe.iterdir()) == [], list(probe.iterdir())
    finally:
        os.chdir(previous_cwd)
        remove_tree(probe)


def test_the_argument_parser_defaults_to_no_explicit_directory():
    assert TMT.parse_args([]).directory is None
    assert TMT.parse_args(["--dir", "somewhere"]).directory == "somewhere"


# --- what the root actually bounds ------------------------------------------

def test_moving_the_root_moves_the_sandbox_with_it():
    """Every module has to read the root at call time; one that bound it on
    import would keep writing into the directory TMT started in."""
    box = Workspace(files={"here.txt": "inside\n"})
    try:
        box.use()
        assert agent_file_ops.safe_path("here.txt").parent == box.path
        assert "here.txt" in agent_file_ops.list_files()
        outside = False
        try:
            agent_file_ops.safe_path("../escape.txt")
        except ValueError:
            outside = True
        assert outside, "a path above the root must still be refused"
    finally:
        box.close()


def test_installation_state_does_not_follow_the_workspace():
    """The key and TMT's identity belong to the install, so that TMT is the
    same agent in every directory and credentials stay in one place."""
    box = Workspace(git=True)
    try:
        box.use()
        for path in (agent_config.KEY_FILE, agent_config.GIT_IDENTITY_FILE,
                     agent_config.GIT_IDENTITY_LOCAL_FILE,
                     agent_config.EFFORT_FILE):
            assert Path(path).resolve().parent == INSTALL_DIR, path
            assert box.path not in Path(path).resolve().parents
    finally:
        box.close()


# --- ceilings ---------------------------------------------------------------

def test_the_snapshot_stops_at_its_limits_and_says_so():
    """Silence would read as completeness, and the model would then act
    confidently on a file it was never shown."""
    files = {f"file_{i:03}.txt": ("x" * 400 + "\n") for i in range(400)}
    box = Workspace(files=files)
    try:
        box.use()
        agent_prompt.invalidate_prompt()
        prompt = agent_prompt.get_system_prompt()
        assert "stopped at the" in prompt
        assert "more files in the workspace than appear below" in prompt
        shown = prompt.count("\n--- file_")
        assert shown <= agent_config.SNAPSHOT_MAX_FILES, shown
        assert len(prompt) < 200000, len(prompt)
    finally:
        box.close()


def test_list_files_truncates_independently_of_the_snapshot():
    box = Workspace(files={f"f{i:04}.txt": "x\n" for i in range(agent_config.LIST_FILES_MAX + 25)})
    try:
        box.use()
        listing = agent_file_ops.list_files()
        lines = listing.splitlines()
        assert lines[-1].startswith("... truncated:"), lines[-1]
        assert len(lines) == agent_config.LIST_FILES_MAX + 1
    finally:
        box.close()


def test_a_large_workspace_is_walked_without_descending_into_machinery():
    """Pruning during the walk, not filtering after it. A node_modules that is
    merely filtered out has already cost the time it took to read."""
    box = Workspace(files={"src/a.py": "a\n", "node_modules/pkg/index.js": "junk\n",
                           ".git/config": "[core]\n"})
    try:
        box.use()
        found = {str(rel).replace("\\", "/") for rel, _ in agent_file_ops.iter_workspace_files()}
        assert "src/a.py" in found
        assert not [name for name in found if "node_modules" in name or ".git" in name]
    finally:
        box.close()


# --- git follows the resolved root ------------------------------------------

def test_git_acts_on_the_resolved_workspace_including_commit_all():
    """The high-consequence one. git_commit with all=True runs git add --all
    in whichever repository the root selected, so which repository that is has
    to be pinned down rather than assumed.
    """
    if not GIT:
        return
    import agent_actions
    box = Workspace(git=True, files={"seed.txt": "seed\n"})
    saved = {name: getattr(agent_config, name, None)
             for name in ("TMT_GIT_ROOT", "TMT_GIT_EMAIL", "TMT_GIT_NAME")}
    saved_env = {name: os.environ.get(name)
                 for name in ("TMT_GIT_ROOT", "TMT_GIT_EMAIL", "TMT_GIT_NAME")}
    try:
        box.git(["add", "seed.txt"])
        box.git(["commit", "-m", "seed"])
        box.use()
        # Discovery must follow the root, so the override is deliberately empty.
        agent_config.TMT_GIT_ROOT = ""
        os.environ.pop("TMT_GIT_ROOT", None)
        os.environ["TMT_GIT_NAME"] = "TMT code"
        os.environ["TMT_GIT_EMAIL"] = "tmt-code@example.invalid"

        (box.path / "new.txt").write_text("added\n", encoding="utf-8")
        report = str(agent_actions.execute_action({"action": "git_status"}))
        assert str(box.path) in report.replace("/", os.sep), report

        result = str(agent_actions.execute_action(
            {"action": "git_commit", "message": "from the selected workspace", "all": True},
            context={"push_authorized": False}))
        assert "Committed" in result, result
        landed = box.git(["log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%s"]).stdout
        # The human who owns this repository stays the author and the committer
        # of the commit TMT made in it; TMT is credited only in the trailer.
        assert landed.count("Human Person <human@example.invalid>") == 2, landed
        assert "TMT code" not in landed, landed
        assert "tmt-code@example.invalid" not in landed, landed
        assert "from the selected workspace" in landed, landed
        # git's own trailer parser, not a substring of the message: only what
        # it returns is read as a co-author anywhere downstream.
        trailer = box.git(
            ["log", "-1", "--format=%(trailers:key=Co-authored-by)"]).stdout.strip()
        assert trailer == "Co-authored-by: TMT code <tmt-code@example.invalid>", trailer
        assert "new.txt" in box.git(["show", "--name-only", "--format=", "HEAD"]).stdout
    finally:
        for name, value in saved.items():
            if value is not None:
                setattr(agent_config, name, value)
        for name, value in saved_env.items():
            os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value
        box.close()
