"""Tests for the uninstall: the one operation in TMT that destroys TMT.

Everything here runs against a temporary git repository built for the test.
Nothing points at the real installation, and the one test that could is the
one that checks the default -- it reads the plan and never executes it.

The properties worth having, in the order they matter:

  What git tracks goes; what git ignores stays.  That is the whole rule, and
      it is what makes this safe to aim at a directory somebody works in.
      Asserted on the disk afterwards rather than on the plan's own account of
      itself: the plan is a claim, and the files are the fact.

  Nothing outside the installation is touched.  A sibling directory is put
      beside the sandbox in the tests that delete, and it has to survive.

  It cannot be aimed at a home directory or a filesystem root.  Those are the
      two mistakes that would be unrecoverable rather than annoying.

  A failure is reported, never raised.  This runs from a menu, after the point
      of no return, with half the program already deleted -- an exception
      there would be the last thing a user ever saw of TMT.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import agent_config
import agent_uninstall as U

INSTALL_DIR = Path(agent_config.__file__).resolve().parent


def remove_tree(path):
    """Delete a temp tree, including anything a test made read-only."""
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(str(path), onerror=on_error)


def git_ready():
    """Whether real git can be driven here. The suite must not need it."""
    try:
        done = subprocess.run(["git", "--version"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=30)
        return done.returncode == 0
    except Exception:
        return False


class Sandbox:
    """A temporary directory shaped like a TMT installation.

    Tracked files stand in for TMT's own source, ignored ones for everything
    a user accumulates beside it -- their notes, TMT's saved key, its logs.
    A sibling directory sits outside it so a test can prove the uninstall
    stayed inside.
    """

    TRACKED = ("TMT.py", "agent_config.py", "testing/unit/test_thing.py",
               ".gitignore")
    IGNORED = ("CLAUDE.local.md", ".tmt_key", "logs/session.log")

    def __init__(self):
        self.base = Path(tempfile.mkdtemp(prefix="tmt_uninstall_"))
        self.install = self.base / "install"
        self.install.mkdir()
        self.sibling = self.base / "not_tmt"
        self.sibling.mkdir()
        (self.sibling / "someone_elses.txt").write_text("keep me\n",
                                                        encoding="utf-8")
        self.write(".gitignore", "CLAUDE.local.md\n.tmt_key\nlogs/\n")
        for name in self.TRACKED:
            if name != ".gitignore":
                self.write(name, "# %s\n" % name)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "the installation")
        for name in self.IGNORED:
            self.write(name, "mine\n")

    def write(self, name, text):
        target = self.install / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def git(self, *args):
        return subprocess.run(["git"] + list(args), cwd=str(self.install),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=60)

    def exists(self, name):
        return (self.install / name).exists()

    def close(self):
        remove_tree(self.base)


class Runner:
    """A stand-in for `run_command` that records instead of running."""

    def __init__(self, exit_code=0, error=""):
        self.calls = []
        self.exit_code = exit_code
        self.error = error

    def __call__(self, argv, timeout=None):
        self.calls.append((list(argv), timeout))
        return Outcome(self.exit_code, self.error)


class Outcome:
    def __init__(self, exit_code, error=""):
        self.exit_code = exit_code
        self.error = error
        self.output = ""


# --- the plan ---------------------------------------------------------------

def test_the_plan_separates_what_tmt_shipped_from_what_the_user_kept():
    """The rule the whole feature rests on, read from git rather than from a
    list kept here: tracked is TMT's, ignored is theirs."""
    if not git_ready():
        return
    box = Sandbox()
    try:
        plan = U.plan(box.install)
        assert plan.checkout and plan.possible, plan.refusal
        assert set(plan.tracked) == set(box.TRACKED), plan.tracked
        assert set(plan.kept) >= {"CLAUDE.local.md", ".tmt_key"}, plan.kept
        assert "logs/session.log" in plan.kept, plan.kept
        # And the two never overlap, which is what stops a file being both
        # promised and taken.
        assert not (set(plan.tracked) & set(plan.kept)), plan.tracked
    finally:
        box.close()


def test_the_plan_reads_and_changes_nothing():
    """It is drawn on a screen before anybody has agreed to anything."""
    if not git_ready():
        return
    box = Sandbox()
    try:
        U.plan(box.install)
        for name in box.TRACKED + box.IGNORED:
            assert box.exists(name), name
        assert box.exists(".git")
    finally:
        box.close()


def test_a_directory_that_is_not_a_checkout_has_nothing_to_remove():
    """A copied folder or a `pip install .` owns no git history, so there is
    no honest way to tell TMT's files from anybody else's -- and the answer is
    to remove none of them and say so, not to guess."""
    plain = Path(tempfile.mkdtemp(prefix="tmt_uninstall_plain_"))
    try:
        (plain / "something.txt").write_text("mine\n", encoding="utf-8")
        plan = U.plan(plain)
        assert not plan.checkout
        assert plan.tracked == ()
        assert any("not a git checkout" in note for note in plan.notes), plan.notes
        report = U.execute(plan, run=Runner())
        assert (plain / "something.txt").exists()
        assert report.removed == 0, report.lines()
    finally:
        remove_tree(plain)


def test_the_two_places_it_must_never_be_aimed_are_refused():
    """A home directory and a filesystem root. Both are recoverable from
    nothing, and both are one wrong default away."""
    home = Path(os.path.expanduser("~"))
    refused = U.plan(home)
    assert refused.refusal, refused.notes
    assert not refused.possible
    root = Path(home.anchor or os.sep)
    assert U.plan(root).refusal, root
    # A path that does not exist is refused too, rather than being treated as
    # an empty installation that "succeeded".
    missing = Path(tempfile.mkdtemp(prefix="tmt_uninstall_gone_"))
    remove_tree(missing)
    assert U.plan(missing).refusal, missing


def test_a_refused_plan_is_refused_again_when_it_is_executed():
    """The screen checks and the execution checks. Two answers to one
    question is one answer too few in the place that cannot be undone."""
    report = U.execute(U.plan(Path(os.path.expanduser("~"))), run=Runner())
    assert report.removed == 0
    assert report.notes and "home directory" in " ".join(report.notes)


def test_the_plan_says_when_the_installation_is_also_the_workspace():
    """Running TMT on TMT makes the two one directory, and uninstalling then
    takes the project you are working in with it. Nothing here forbids that --
    it is what the user asked for -- but they have to be told."""
    if not git_ready():
        return
    box = Sandbox()
    real = agent_config.ROOT_DIR
    try:
        agent_config.ROOT_DIR = box.install
        assert U.plan(box.install).is_workspace
        assert any("workspace" in note for note in U.plan(box.install).notes)
    finally:
        agent_config.ROOT_DIR = real
        box.close()


# --- doing it ---------------------------------------------------------------

def test_the_tracked_files_go_and_the_ignored_ones_stay():
    """Asserted on the disk, not on the report: the report is a claim about
    what happened and the files are what happened."""
    if not git_ready():
        return
    box = Sandbox()
    try:
        report = U.execute(U.plan(box.install), run=Runner())
        for name in box.TRACKED:
            assert not box.exists(name), name
        for name in box.IGNORED:
            assert box.exists(name), name
        assert box.exists("CLAUDE.local.md"), "the user's own notes"
        assert not box.exists(".git"), "an install is not a checkout afterwards"
        assert box.install.exists(), "the directory itself stays"
        assert report.removed >= len(box.TRACKED), report.lines()
        assert report.kept >= len(box.IGNORED), report.lines()
        assert report.clean, report.lines()
    finally:
        box.close()


def test_nothing_outside_the_installation_is_touched():
    """The sibling directory is the whole test. An uninstall that reached one
    level up would be indistinguishable from one that worked, right up until
    somebody noticed what else had gone."""
    if not git_ready():
        return
    box = Sandbox()
    try:
        U.execute(U.plan(box.install), run=Runner())
        assert (box.sibling / "someone_elses.txt").exists()
        assert box.sibling.exists()
    finally:
        box.close()


def test_a_directory_left_empty_goes_and_one_still_holding_something_stays():
    """`testing/unit/` has nothing left in it; `logs/` still has the user's
    log. Removing the first is tidiness, keeping the second is the promise."""
    if not git_ready():
        return
    box = Sandbox()
    try:
        U.execute(U.plan(box.install), run=Runner())
        assert not (box.install / "testing").exists()
        assert (box.install / "logs" / "session.log").exists()
    finally:
        box.close()


def test_both_package_managers_are_asked_and_the_argv_is_exact():
    """No shell, no string, and the same package name both installs publish."""
    if not git_ready():
        return
    box = Sandbox()
    runner = Runner()
    try:
        plan = U.plan(box.install)
        # The detection is the machine's; the argv is asserted whichever it
        # found, because a wrong argument here uninstalls something else.
        U.execute(plan, run=runner)
        for argv, timeout in runner.calls:
            assert U.PACKAGE in argv, argv
            assert timeout == U.COMMAND_TIMEOUT
            if argv[0] == "npm":
                assert argv == ["npm", "uninstall", "-g", U.PACKAGE]
            else:
                assert argv[1:] == ["-m", "pip", "uninstall", "-y", U.PACKAGE]
        assert len(runner.calls) == len(plan.commands)
    finally:
        box.close()


def test_a_package_manager_that_fails_is_reported_and_stops_nothing():
    """The files are already gone by then. A non-zero exit from npm has to
    become a line the user can act on, not an exception in a half-removed
    installation."""
    if not git_ready():
        return
    box = Sandbox()
    try:
        plan = U.plan(box.install)
        report = U.execute(plan, run=Runner(exit_code=1, error="npm said no"))
        assert not box.exists("TMT.py"), "the files still went"
        if plan.commands:
            assert not report.clean
            assert any("not removed" in line for line in report.lines())
    finally:
        box.close()


def test_a_runner_that_raises_is_caught():
    """Anything at all, from a missing npm to a broken PATH."""
    if not git_ready():
        return
    box = Sandbox()

    def explode(argv, timeout=None):
        raise RuntimeError("no such program")

    try:
        plan = U.plan(box.install)
        report = U.execute(plan, run=explode)
        assert report.removed >= 1
        if plan.commands:
            assert not report.clean
    finally:
        box.close()


def test_the_report_says_what_was_kept_and_where_it_is():
    """The last thing a user reads before TMT closes. It has to answer "is my
    key gone" without them having to go and look."""
    if not git_ready():
        return
    box = Sandbox()
    try:
        report = U.execute(U.plan(box.install), run=Runner())
        text = " ".join(report.lines())
        assert "kept" in text, text
        assert str(box.install) in text, text
    finally:
        box.close()


# --- what it is pointed at by default ---------------------------------------

def test_the_default_target_is_the_installation_and_not_the_workspace():
    """The confusion that would be catastrophic and silent. ROOT_DIR is the
    user's project; INSTALL_DIR is TMT's own code. This reads the plan and
    never executes it."""
    real = agent_config.ROOT_DIR
    elsewhere = Path(tempfile.mkdtemp(prefix="tmt_uninstall_workspace_"))
    try:
        agent_config.ROOT_DIR = elsewhere
        assert U.plan().root == Path(agent_config.INSTALL_DIR)
        assert U.plan().root != elsewhere
    finally:
        agent_config.ROOT_DIR = real
        remove_tree(elsewhere)


def test_the_package_name_is_the_one_both_installs_publish():
    """Three files name this program and they have to agree, or an uninstall
    politely removes something that is not TMT -- or nothing at all."""
    data = json.loads((INSTALL_DIR / "package.json").read_text(encoding="utf-8"))
    assert data["name"] == U.PACKAGE
    pyproject = (INSTALL_DIR / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^name = "%s"' % U.PACKAGE, pyproject, re.M), U.PACKAGE
    assert re.search(r"^%s = " % U.PACKAGE, pyproject, re.M), U.PACKAGE


def test_this_module_starts_no_process_of_its_own():
    """Git goes through agent_git and the uninstallers through
    agent_execution, which are two of the four modules allowed to start
    anything. A `subprocess` here would be a fifth, in the module whose whole
    job is deleting things."""
    source = (INSTALL_DIR / "agent_uninstall.py").read_text(encoding="utf-8")
    for forbidden in ("subprocess", "os.system", "os.popen", "os.exec",
                      "os.spawn", "shell=True"):
        assert forbidden not in source, forbidden
