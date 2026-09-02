"""The startup updater: what it takes, what it refuses, and what it can never do.

Two properties are worth more than the rest of this file put together, and
most of it exists to hold them down:

1. **Nothing here may destroy the user's work.** A dirty checkout is refused
   before anything is even fetched, a diverged one is refused after, and the
   only command in the module that changes a repository is a fast-forward that
   fails rather than merges. Two tests read the module's own source and assert
   that no history-destroying git flag appears in it at all -- one over the
   raw text for phrases, one over the parsed argument literals, because a
   docstring is allowed to say "this never overwrites anything" and an argv
   list is not allowed to mean it.
2. **Nothing here may stop TMT launching.** Every failure -- no repository, no
   network, no upstream, a git that will not run, an exception nobody
   predicted -- has to come back as an UpdateResult. There is a test per
   failure shape, because the one thing a broken updater must never do is
   take the agent down with it.

The git-backed tests build real temporary repositories with a real local
"remote" and never touch the developer's checkout or any network: HOME and the
git config search are redirected for the length of each one, the remote is a
bare repository in the same temp directory, and everything is removed
afterwards. They skip cleanly when git is not on PATH, so a machine without it
reports a smaller suite rather than a broken one.

The rest is driven through a FakeGit, for the cases a real repository cannot
reach without being damaged first: a fetch that fails with a credential in its
error text, a fast-forward git refuses, and an engine that raises something
nobody wrote a handler for.
"""

import ast
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import agent_config
import agent_git
import agent_update as U

GIT = shutil.which("git")

# Captured before any test edits the environment, so a sandbox inherits the
# developer's PATH (git has to be findable) without inheriting anything a
# previous test left behind.
REAL_ENV = dict(os.environ)

MISSING = object()

# The strings that must never appear in the module in any form, prose
# included. Each one is a way to throw away work that is not committed
# anywhere, which is the failure this whole module is arranged around.
BANNED_PHRASES = (
    "reset --hard",
    "clean -fd",
    "checkout --force",
    "--force",
    "--hard",
    "git pull",
    "shell=true",
    "os.system",
)

# Words that may not be an element of any list or tuple literal, because that
# is how an argv is built in this module and in agent_git. Prose is exempt --
# a headline saying "this checkout has commits of its own" contains the word
# `checkout` and is not a command -- which is exactly why the scan reads
# sequences rather than every string it can find.
BANNED_ARGUMENTS = {
    "push", "reset", "rebase", "checkout", "switch", "config", "clean",
    "-f", "--force", "--force-with-lease", "--hard", "-fd", "--mirror", "-d",
}

# These are not words in any sentence. No string literal anywhere in the
# module may BE one, wherever it sits.
BANNED_FLAGS = {"-f", "--force", "--force-with-lease", "--hard", "-fd", "--mirror"}


# --- helpers ---------------------------------------------------------------

def apply_env(overrides):
    """Set (or with a None value, unset) environment keys; returns the originals."""
    previous = {key: os.environ.get(key, MISSING) for key in overrides}
    for key, value in overrides.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    return previous


def restore_env(previous):
    for key, value in (previous or {}).items():
        if value is MISSING:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def remove_tree(path):
    """Delete a temp tree. Git marks objects read-only, which Windows refuses to
    unlink, so failures are retried after clearing the flag and never raised."""

    def handle(function, target, _exception):
        try:
            os.chmod(target, stat.S_IWRITE)
            function(target)
        except OSError:
            pass

    if not Path(path).exists():
        return
    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(str(path), onexc=lambda f, t, e: handle(f, t, e))
        else:
            shutil.rmtree(str(path), onerror=lambda f, t, e: handle(f, t, e))
    except OSError:
        pass


class FakeResult:
    """What subprocess hands back, with nothing else attached."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeGit:
    """A TMTGit-shaped engine that answers from a script.

    Here for the states a real repository cannot be put into without being
    broken first -- a remote whose error text carries a credential, a
    fast-forward git refuses, an engine that raises. It records every call, so
    a test can assert not only what came back but what was never asked.
    """

    def __init__(self, clean=True, upstream="origin/main", ahead=0, behind=0,
                 fetch=None, merge=None, head="aaaaaaa", after_head=None,
                 remotes=("origin",), raise_on=None, status_error=None):
        self.root = Path(tempfile.gettempdir()) / "tmt-fake-install"
        self.calls = []
        self.clean = clean
        self.upstream = upstream
        self.ahead = ahead
        self.behind = behind
        self.fetch = fetch or FakeResult(0, "")
        self.merge = merge or FakeResult(0, "Fast-forward")
        self.head = head
        self.after_head = after_head or head
        self._remotes = list(remotes)
        self.raise_on = raise_on
        self.status_error = status_error
        self._merged = False

    def subcommands(self):
        return [call[0] for call in self.calls]

    def _run(self, args, env=None, timeout=None, check=True):
        args = [str(argument) for argument in args]
        self.calls.append(args)
        if self.raise_on and args[0] == self.raise_on:
            raise RuntimeError("git exploded in a way nobody wrote a handler for")
        if args[0] == "rev-parse":
            if "@{u}" in args:
                if not self.upstream:
                    return FakeResult(128, "", "fatal: no upstream configured")
                return FakeResult(0, self.upstream + "\n")
            return FakeResult(0, (self.after_head if self._merged else self.head) + "\n")
        if args[0] == "rev-list":
            return FakeResult(0, "%d\t%d\n" % (self.ahead, self.behind))
        if args[0] == "fetch":
            return self.fetch
        if args[0] == "merge":
            if self.merge.returncode == 0:
                self._merged = True
            return self.merge
        return FakeResult(0, "")

    def status(self):
        if self.status_error is not None:
            raise self.status_error
        return {
            "branch": "main", "clean": self.clean,
            "staged": [], "unstaged": [] if self.clean else ["agent_ui.py"],
            "untracked": [], "root": str(self.root),
        }

    def remotes(self):
        return list(self._remotes)


def check(engine, **kwargs):
    """check_and_update against a fake, with a launch that has restarted zero times.

    The empty environment is not decoration: `restarts_used` reads the real
    one by default, so a machine that happened to have the counter set -- or a
    test above that left it behind -- would silently turn every fast-forward
    below into a DISABLED and the assertions would be about the wrong rule.
    """
    kwargs.setdefault("env", {})
    return U.check_and_update(git=engine, **kwargs)


class Sandbox:
    """A temporary HOME, a TMT "installation" checkout, and a bare remote.

    The installation is an ordinary clone-shaped repository whose `main`
    tracks `origin/main`, which is the shape a real install has. Besides the
    filesystem it redirects HOME and the git config search in this process's
    OWN environment, because agent_git builds its subprocess environment from
    os.environ at call time. close() restores all of it and must run in a
    finally block.
    """

    def __init__(self):
        self.base = Path(tempfile.mkdtemp(prefix="tmt_update_test_"))
        self.home = self.base / "home"
        self.home.mkdir()
        self.install = self.base / "install"
        self.install.mkdir()
        self.bare = self.base / "remote.git"
        self.author_dir = self.base / "author"
        self._author = None
        self.env = dict(REAL_ENV)
        self.env.update(self._isolation())
        self.env.pop("GIT_DIR", None)
        self.env.pop("GIT_WORK_TREE", None)
        # The identity comes from the environment rather than from three
        # `git config` calls per repository. It is the same answer, and this
        # file builds a dozen repositories: a subprocess saved here is saved
        # twenty-four times, and the suite it joins is long enough already.
        self.env.update({
            "GIT_AUTHOR_NAME": "Repo Human",
            "GIT_AUTHOR_EMAIL": "repo-human@example.invalid",
            "GIT_COMMITTER_NAME": "Repo Human",
            "GIT_COMMITTER_EMAIL": "repo-human@example.invalid",
        })
        overrides = dict(self._isolation())
        overrides.update({
            "GIT_DIR": None, "GIT_WORK_TREE": None,
            # A counter left over from another test would silently turn the
            # restart guard on for every case below.
            U.RESTART_ENV_VAR: None,
        })
        self._previous_env = apply_env(overrides)
        self._build()

    def _isolation(self):
        return {
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / "xdg"),
            "GIT_TERMINAL_PROMPT": "0",
            # git walks up looking for a repository; the temp directory must
            # not be allowed to find one above itself.
            "GIT_CEILING_DIRECTORIES": str(self.base),
        }

    @staticmethod
    def url(path):
        """A local path git accepts as a remote on every platform."""
        return str(path).replace("\\", "/")

    def git(self, args, cwd=None, check=True):
        result = subprocess.run(
            [GIT] + [str(argument) for argument in args],
            cwd=str(cwd or self.install), env=self.env,
            capture_output=True, text=True, timeout=120,
        )
        if check:
            assert result.returncode == 0, (
                "git %s: %s" % (" ".join(str(a) for a in args), result.stderr))
        return result.stdout.strip()

    def _build(self):
        self.git(["init", "-q"])
        self.git(["symbolic-ref", "HEAD", "refs/heads/main"])
        self.write("README.md", "installed\n")
        self.git(["add", "README.md"])
        self.git(["commit", "-q", "-m", "the version that is installed"])
        self.git(["init", "--bare", "-q", self.url(self.bare)], cwd=self.base)
        self.git(["--git-dir", str(self.bare), "symbolic-ref", "HEAD",
                  "refs/heads/main"], cwd=self.base)
        self.git(["remote", "add", "origin", self.url(self.bare)])
        self.git(["push", "-q", "-u", "origin", "main"])

    def write(self, name, text):
        target = self.install / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def head(self, cwd=None):
        return self.git(["rev-parse", "HEAD"], cwd=cwd)

    def commit_here(self, name="local.txt", text="the user's own work\n"):
        """A commit in the installation itself, which is what makes it diverge."""
        self.write(name, text)
        self.git(["add", name])
        self.git(["commit", "-q", "-m", "local work: " + name])
        return self.head()

    def advance_remote(self, name="upstream.txt", text="from the remote\n"):
        """A commit that exists on the remote and not in the installation."""
        if self._author is None:
            self.git(["clone", "-q", self.url(self.bare), self.url(self.author_dir)],
                     cwd=self.base)
            self._author = self.author_dir
        target = self._author / name
        target.write_text(text, encoding="utf-8")
        self.git(["add", name], cwd=self._author)
        self.git(["commit", "-q", "-m", "remote work: " + name], cwd=self._author)
        self.git(["push", "-q", "origin", "main"], cwd=self._author)
        return self.git(["rev-parse", "HEAD"], cwd=self._author)

    def engine(self):
        return SpyGit(self.install)

    def close(self):
        restore_env(self._previous_env)
        remove_tree(self.base)


class SpyGit(agent_git.TMTGit):
    """The real engine, with every command it was asked to run written down.

    A real repository and a real git, so what is asserted about the calls is
    what actually happened rather than what a stand-in agreed to say.
    """

    def __init__(self, root):
        agent_git.TMTGit.__init__(self, root=str(root))
        self.calls = []

    def _run(self, args, **kwargs):
        self.calls.append([str(argument) for argument in args])
        return agent_git.TMTGit._run(self, args, **kwargs)

    def subcommands(self):
        return [call[0] for call in self.calls]


def git_ready():
    """Whether the git-backed half of this file can run at all."""
    return GIT is not None


def module_source():
    return Path(U.__file__).read_text(encoding="utf-8")


# --- the happy paths, against real repositories ----------------------------

def test_a_checkout_level_with_its_remote_is_current_and_nothing_is_merged():
    """The common case, and the one that must cost nothing.

    Most launches are this. If a fast-forward were attempted anyway it would
    be a no-op today and a way to lose a race tomorrow, so the assertion is
    not only on the status but on the absence of the merge: the module has to
    stop at the comparison rather than run a command and then discover it had
    nothing to do.
    """
    if not git_ready():
        return
    box = Sandbox()
    try:
        before = box.head()
        engine = box.engine()
        result = U.check_and_update(git=engine)
        assert result.status == U.CURRENT, result.as_dict()
        assert not result.applied and not result.should_restart
        assert not result.is_failure
        assert "merge" not in engine.subcommands(), engine.calls
        assert box.head() == before
    finally:
        box.close()


def test_a_remote_that_has_moved_ahead_is_fast_forwarded_onto():
    """The reason the module exists, asserted on the repository and not on the
    report: HEAD has to actually be the remote's commit afterwards. A version
    that returned UPDATED without moving anything would satisfy any assertion
    made about the result alone."""
    if not git_ready():
        return
    box = Sandbox()
    try:
        before = box.head()
        remote_head = box.advance_remote()
        engine = box.engine()
        result = U.check_and_update(git=engine)
        assert result.status == U.UPDATED, result.as_dict()
        assert result.applied and result.should_restart
        assert box.head() == remote_head
        assert box.head() != before
        assert result.before and result.after and result.before != result.after
        assert (box.install / "upstream.txt").exists()
    finally:
        box.close()


def test_the_only_command_that_moves_the_checkout_is_a_fast_forward():
    """`--ff-only` is the whole safety argument for updating at startup: it
    cannot produce a merge commit and it cannot rewrite history, so the worst
    it can do is refuse. A change to the porcelain that fetches and merges in
    one step would still pass every other test in this file, and would put a
    merge conflict in front of a user who had only pressed Enter on a splash
    screen."""
    if not git_ready():
        return
    box = Sandbox()
    try:
        box.advance_remote()
        engine = box.engine()
        assert U.check_and_update(git=engine).status == U.UPDATED
        merges = [call for call in engine.calls if call[0] == "merge"]
        assert len(merges) == 1, engine.calls
        assert "--ff-only" in merges[0], merges
    finally:
        box.close()


def test_a_run_never_pushes_switches_branch_or_writes_configuration():
    """The runtime complement to the source scans below.

    Those read the module; this reads what a whole successful run actually
    handed git. A command assembled at runtime out of pieces no literal scan
    would recognise still has to show up here.
    """
    if not git_ready():
        return
    box = Sandbox()
    try:
        box.advance_remote()
        engine = box.engine()
        U.check_and_update(git=engine)
        for call in engine.calls:
            assert call[0] not in ("push", "reset", "checkout", "switch",
                                   "rebase", "config", "clean", "branch"), call
            for token in call:
                assert token != "-f", call
                assert not token.startswith("--force"), call
                assert token != "--hard", call
    finally:
        box.close()


# --- the refusals ----------------------------------------------------------

def test_a_diverged_checkout_is_refused_and_keeps_its_own_commit():
    """Both sides have moved, so every way forward is somebody's decision:
    rewriting the local commits away, or making a merge. A startup screen
    makes neither. The local commit still being reachable afterwards is the
    assertion that matters -- BLOCKED_DIVERGED with the work gone would be the
    worst possible pass."""
    if not git_ready():
        return
    box = Sandbox()
    try:
        box.advance_remote()
        mine = box.commit_here()
        engine = box.engine()
        result = U.check_and_update(git=engine)
        assert result.status == U.BLOCKED_DIVERGED, result.as_dict()
        assert not result.applied
        assert box.head() == mine
        assert (box.install / "local.txt").read_text(encoding="utf-8") == \
            "the user's own work\n"
        assert "merge" not in engine.subcommands(), engine.calls
        assert result.ahead >= 1 and result.behind >= 1
    finally:
        box.close()


def test_a_dirty_checkout_is_told_what_is_waiting_and_is_never_written_to():
    """The user's edit survives, and they are told an update exists.

    The second half is the reversal of 2026-09-02. This test used to assert
    that NOTHING was fetched, which bought a launch with no network call in a
    checkout somebody was editing -- and cost that checkout its place in the
    update loop, silently and for as long as the file stayed modified. It
    fetches now, so every install asks the same question on every launch and
    reports the same answer; what a dirty tree still refuses is the APPLY.

    `merge` is the assertion that matters and it is unchanged. Nothing may be
    written to a tree with uncommitted work in it.
    """
    if not git_ready():
        return
    box = Sandbox()
    try:
        box.advance_remote()
        box.write("README.md", "the user was in the middle of something\n")
        head = box.head()
        engine = box.engine()
        result = U.check_and_update(git=engine)
        assert result.status == U.BLOCKED_DIRTY, result.as_dict()
        # It looked, which is the change...
        assert "fetch" in engine.subcommands(), engine.calls
        assert result.behind == 1, result.as_dict()
        # ...and it did not touch anything, which is not.
        assert "merge" not in engine.subcommands(), engine.calls
        assert box.head() == head
        assert (box.install / "README.md").read_text(encoding="utf-8") == \
            "the user was in the middle of something\n"
        # The refusal names both halves: what is waiting, and what is in the
        # way. A user who is told neither cannot act on either.
        assert "README.md" in result.detail, result.detail
        assert "1 new commit" in result.detail, result.detail
        assert "stash" in result.detail, result.detail
    finally:
        box.close()


def test_a_dirty_checkout_that_is_level_says_so_rather_than_blaming_the_edit():
    """Before the reversal every launch of an edited checkout said "update
    skipped", whether or not there was an update to skip -- which reads as
    something being withheld when the true answer is that there is nothing to
    withhold. Now the tree is only mentioned when it is actually in the way."""
    if not git_ready():
        return
    box = Sandbox()
    try:
        box.write("README.md", "edited, but level with the remote\n")
        result = U.check_and_update(root=box.install)
        assert result.status == U.CURRENT, result.as_dict()
    finally:
        box.close()


def test_an_untracked_file_counts_as_dirty_too():
    """`clean` is not "no modified tracked files": a fast-forward that brings
    in a path the user already has as an untracked file fails in the middle,
    and the refusal is cheaper than the recovery.

    The remote is advanced because since 2026-09-02 the tree is only judged
    once there is something to apply -- a checkout with a stray file and
    nothing waiting for it is simply up to date, which is the true answer.
    """
    if not git_ready():
        return
    box = Sandbox()
    try:
        box.advance_remote()
        box.write("scratch.txt", "notes\n")
        result = U.check_and_update(root=box.install)
        assert result.status == U.BLOCKED_DIRTY, result.as_dict()
        assert (box.install / "scratch.txt").exists()
    finally:
        box.close()


def test_a_branch_with_no_upstream_is_reported_rather_than_guessed_at():
    """A branch of the user's own tracks nothing, and there is no honest
    default: fetching origin/main into it would be TMT deciding what the
    branch meant. This is the everyday case for anyone developing TMT."""
    if not git_ready():
        return
    box = Sandbox()
    try:
        box.git(["checkout", "-q", "-b", "my-own-work"])
        head = box.head()
        result = U.check_and_update(root=box.install)
        assert result.status == U.NO_UPSTREAM, result.as_dict()
        assert not result.is_failure
        assert box.head() == head
    finally:
        box.close()


def test_a_detached_head_is_no_upstream_and_not_a_crash():
    """There is no branch, so there is nothing tracking anything, so the
    answer is the same one. Worth its own test because the code path is
    different: agent_git raises for the branch name here, and an updater that
    let that escape would turn a perfectly ordinary state -- somebody looking
    at an old commit -- into a launch failure."""
    if not git_ready():
        return
    box = Sandbox()
    try:
        box.git(["checkout", "-q", "--detach", "HEAD"])
        head = box.head()
        result = U.check_and_update(root=box.install)
        assert result.status == U.NO_UPSTREAM, result.as_dict()
        assert box.head() == head
    finally:
        box.close()


def test_an_installation_that_is_not_a_checkout_says_so_and_carries_on():
    """TMT can be installed by copying a folder. That install can never
    update itself, and it must still start: NOT_A_REPO is a fact to report,
    not a failure to raise."""
    if not git_ready():
        return
    base = Path(tempfile.mkdtemp(prefix="tmt_update_plain_"))
    previous = apply_env({"GIT_CEILING_DIRECTORIES": str(base)})
    try:
        folder = base / "tmt"
        folder.mkdir()
        (folder / "TMT.py").write_text("# a copy, not a clone\n", encoding="utf-8")
        result = U.check_and_update(root=folder)
        assert result.status == U.NOT_A_REPO, result.as_dict()
        assert result.headline and not result.applied
    finally:
        restore_env(previous)
        remove_tree(base)


def test_a_fetch_that_cannot_reach_the_remote_is_an_error_that_continues():
    """The single most likely failure in the field -- someone starts TMT on a
    train. It has to come back as a result with git's own words in it, and the
    checkout has to be untouched."""
    if not git_ready():
        return
    box = Sandbox()
    try:
        head = box.head()
        box.git(["remote", "set-url", "origin",
                 box.url(box.base / "there-is-nothing-here.git")])
        engine = box.engine()
        result = U.check_and_update(git=engine)
        assert result.status == U.ERROR, result.as_dict()
        assert result.is_failure
        assert result.detail, "the error must carry git's own words"
        assert "merge" not in engine.subcommands(), engine.calls
        assert box.head() == head
    finally:
        box.close()


# --- the failures a real repository cannot be put into safely --------------

def test_a_fast_forward_that_git_refuses_is_an_error_and_changes_nothing():
    """`--ff-only` failing is the guard doing its job, so the module has to
    treat it as an answer rather than as a surprise. ERROR, git's words, and a
    result the caller can print."""
    engine = FakeGit(behind=2, merge=FakeResult(
        1, "", "fatal: Not possible to fast-forward, aborting."))
    result = check(engine)
    assert result.status == U.ERROR, result.as_dict()
    assert "fast-forward" in result.detail.lower(), result.detail
    assert engine.subcommands().count("merge") == 1


def test_an_unexpected_exception_becomes_a_result_and_never_escapes():
    """The outer guard, which is the reason this module can be called from
    startup at all. Anything raising anywhere inside -- an engine that throws,
    a git that will not run -- has to arrive as ERROR carrying the exception,
    because an updater that can raise is an updater that can stop the agent
    from starting."""
    engines = [
        FakeGit(raise_on="fetch"),
        FakeGit(raise_on="rev-list", behind=1),
        FakeGit(status_error=ValueError("the status object was nonsense")),
        FakeGit(raise_on="rev-parse"),
    ]
    for engine in engines:
        result = check(engine)
        assert result.status == U.ERROR, result.as_dict()
        assert result.is_failure
        assert result.headline, "a failure still needs a line for the splash"


def test_git_output_carrying_a_credential_is_scrubbed_out_of_the_detail():
    """A fetch failure quotes the remote, and a remote URL can carry the
    userinfo half of a credential. The detail is written to a log and shown in
    a diagnostic, so the secret must not survive the trip -- and the module
    borrows agent_git's own scrubber rather than inventing a second one that
    could disagree with it."""
    engine = FakeGit(behind=1, fetch=FakeResult(
        128, "", "fatal: unable to access "
                 "'https://tmt-user:s3cr3t-token@example.invalid/tmt.git/': failed"))
    result = check(engine)
    assert result.status == U.ERROR
    assert "s3cr3t-token" not in result.detail, result.detail
    assert "tmt-user" not in result.detail, result.detail
    assert "example.invalid" in result.detail, result.detail


def test_an_upstream_no_configured_remote_owns_is_reported_not_split_blindly():
    """A ref whose first path segment is not a remote cannot be fetched, and
    splitting it on the first slash anyway would hand git a remote name that
    does not exist. Saying so is the honest answer."""
    engine = FakeGit(upstream="somewhere/else/main", remotes=("origin",), behind=3)
    result = check(engine)
    assert result.status == U.NO_UPSTREAM, result.as_dict()
    assert "fetch" not in engine.subcommands(), engine.calls


def test_a_logging_failure_can_never_be_what_stops_a_launch():
    """Every log call in the module is guarded. A full disk, a read-only
    install directory or a log file somebody deleted mid-run is not a reason
    for TMT not to start, and this is the test that says the guards are
    real."""
    def explode(*_args, **_kwargs):
        raise OSError("the log could not be written")

    real = agent_git.log
    agent_git.log = explode
    try:
        assert check(FakeGit()).status == U.CURRENT
        assert check(FakeGit(behind=1)).status == U.UPDATED
    finally:
        agent_git.log = real


# --- where it looks --------------------------------------------------------

def test_the_update_source_is_the_install_directory_and_not_the_workspace():
    """The one confusion that would be catastrophic and silent.

    ROOT_DIR is the user's project; INSTALL_DIR is where TMT's own code lives.
    Fast-forwarding the first would move somebody else's repository under
    them. agent_git's own discovery honours TMT_GIT_ROOT -- which points at
    the workspace -- so the default has to be written out here rather than
    inherited, and this test drives the real default with the two directories
    deliberately different.
    """
    seen = []

    class Recorder:
        def __init__(self, root=None, identity=None):
            seen.append(Path(str(root)))
            self.root = Path(str(root))

        def _run(self, args, **kwargs):
            return FakeResult(128, "", "fatal: not a git repository")

    elsewhere = Path(tempfile.mkdtemp(prefix="tmt_update_workspace_"))
    real_engine, real_root = agent_git.TMTGit, agent_config.ROOT_DIR
    agent_git.TMTGit = Recorder
    agent_config.ROOT_DIR = elsewhere
    try:
        result = U.check_and_update()
        assert result.status == U.NOT_A_REPO, result.as_dict()
        assert seen, "check_and_update never built an engine"
        assert seen[0] == Path(agent_config.INSTALL_DIR), seen
        assert seen[0] != elsewhere, seen
    finally:
        agent_git.TMTGit = real_engine
        agent_config.ROOT_DIR = real_root
        remove_tree(elsewhere)


def test_how_tmt_was_installed_makes_no_difference_to_where_it_updates_from():
    """npm, `pip install -e .` and a bare clone are one path, not three.

    Each of them leaves TMT's modules in a git checkout and each runs this
    same function against `INSTALL_DIR`, which is derived from where the code
    actually sits -- so `~/.tmtcode` (npm), `C:\\Coding\\TMT` (a clone) and
    anywhere else are the same case with a different path in it. There is no
    npm-specific update path and this asserts the absence: the only thing that
    decides where the update comes from is where TMT is.
    """
    if not git_ready():
        return
    # Two installations at two paths, standing in for the two install methods.
    # Neither is told which it is, because nothing in the updater asks.
    for attempt in ("as npm would leave it", "as a clone would"):
        box = Sandbox()
        try:
            box.advance_remote()
            result = U.check_and_update(root=box.install)
            assert result.status == U.UPDATED, (attempt, result.as_dict())
        finally:
            box.close()


def test_an_install_that_cannot_update_says_what_would():
    """The one status a user can act on, so it has to say how.

    A copied folder and a non-editable `pip install .` both land here, and
    both stay on whatever version they were made from forever. Reporting the
    fact without the remedy leaves somebody wondering why their agent never
    changes.
    """
    plain = Path(tempfile.mkdtemp(prefix="tmt_not_a_checkout_"))
    try:
        result = U.check_and_update(root=plain)
        assert result.status == U.NOT_A_REPO, result.as_dict()
        assert "npm install -g tmtcode" in result.detail, result.detail
        assert "pip install -e ." in result.detail, result.detail
    finally:
        remove_tree(plain)


# --- the pure decisions ----------------------------------------------------

def test_compare_status_answers_the_three_cases_and_no_fourth():
    """The whole apply/refuse decision, isolated from git so it can be read.

    Nothing behind is CURRENT however far ahead the checkout is: a local
    commit the remote has not got is work in progress, not staleness, and
    there is nothing to take.
    """
    assert U.compare_status(0, 0) == U.CURRENT
    assert U.compare_status(4, 0) == U.CURRENT
    assert U.compare_status(0, 3) == U.AVAILABLE
    assert U.compare_status(2, 3) == U.BLOCKED_DIVERGED
    assert U.compare_status("0", "1") == U.AVAILABLE
    for ahead in range(0, 3):
        for behind in range(0, 3):
            assert U.compare_status(ahead, behind) in (
                U.CURRENT, U.AVAILABLE, U.BLOCKED_DIVERGED)


def test_split_upstream_uses_the_real_remote_names_not_the_first_slash():
    """A branch name may contain slashes, so the only reliable separator is a
    remote that actually exists. Longest first, or a repository with remotes
    called `up` and `upstream` would fetch from the wrong one."""
    assert U.split_upstream("origin/main", ["origin"]) == ("origin", "main")
    assert U.split_upstream("origin/feature/login", ["origin"]) == \
        ("origin", "feature/login")
    assert U.split_upstream("upstream/main", ["up", "upstream"]) == \
        ("upstream", "main")
    assert U.split_upstream("nowhere/main", ["origin"]) == ("", "")
    assert U.split_upstream("", ["origin"]) == ("", "")
    assert U.split_upstream("origin/main", []) == ("", "")


def test_the_result_answers_applied_should_restart_and_is_failure():
    """The three questions the caller actually asks. A refusal is an answer
    and is not a failure -- only ERROR is, because only ERROR means TMT did
    not find out."""
    updated = U.UpdateResult(U.UPDATED, "moved", before="aaa", after="bbb")
    assert updated.applied and updated.should_restart and not updated.is_failure
    for status in (U.CURRENT, U.BLOCKED_DIRTY, U.BLOCKED_DIVERGED,
                   U.NO_UPSTREAM, U.NOT_A_REPO, U.DISABLED):
        result = U.UpdateResult(status, "a line")
        assert not result.applied, status
        assert not result.should_restart, status
        assert not result.is_failure, status
    failed = U.UpdateResult(U.ERROR, "no")
    assert failed.is_failure and not failed.applied
    assert set(U.UpdateResult(U.CURRENT, "x").as_dict()) == {
        "status", "headline", "detail", "before", "after", "ahead", "behind"}


def test_every_status_is_distinct_and_listed():
    """The caller switches on these, so two that collided would make one
    outcome unreachable and nobody would see an error."""
    assert len(set(U.STATUSES)) == len(U.STATUSES)
    for status in U.STATUSES:
        assert isinstance(status, str) and status


def test_every_result_carries_a_headline_short_enough_for_a_splash_line():
    """The headline is printed as it is, under a wordmark, on a terminal that
    may be narrow. A paragraph there would wrap into the layout the caller
    drew."""
    results = [
        check(FakeGit()),
        check(FakeGit(behind=2)),
        check(FakeGit(clean=False)),
        check(FakeGit(ahead=1, behind=1)),
        check(FakeGit(upstream="")),
        check(FakeGit(raise_on="fetch")),
    ]
    seen = set()
    for result in results:
        assert result.status in U.STATUSES, result.status
        assert result.headline.strip(), result.as_dict()
        assert "\n" not in result.headline, result.headline
        assert len(result.headline) <= 78, result.headline
        seen.add(result.status)
    assert len(seen) >= 5, seen


# --- restarting ------------------------------------------------------------

def test_restart_command_runs_a_script_with_the_current_interpreter():
    """`python TMT.py --dir X` has to come back with the same interpreter in
    front of it. Running the .py file directly would depend on the file being
    executable and on a shebang, neither of which exists on Windows."""
    assert U.restart_command(["TMT.py", "--dir", "X"], executable="/usr/bin/py") \
        == ["/usr/bin/py", "TMT.py", "--dir", "X"]
    assert U.restart_command([r"C:\Coding\TMT\TMT.PY"], executable="py.exe") \
        == ["py.exe", r"C:\Coding\TMT\TMT.PY"]


def test_restart_command_runs_a_console_script_directly():
    """The installed entry point IS an executable. Putting an interpreter in
    front of `tmtcode` would hand python a binary and fail on the one launch
    shape most users have."""
    assert U.restart_command(["/usr/local/bin/tmtcode", "--dir", "X"],
                             executable="/usr/bin/py") \
        == ["/usr/local/bin/tmtcode", "--dir", "X"]
    assert U.restart_command([r"C:\Python\Scripts\tmtcode.exe", "--effort", "high"],
                             executable="py.exe") \
        == [r"C:\Python\Scripts\tmtcode.exe", "--effort", "high"]


def test_restart_command_preserves_every_argument_it_was_given():
    """An update that quietly dropped `--dir` would move the user out of the
    workspace they chose, which is the kind of surprise that makes an
    automatic update feel like something done TO somebody."""
    arguments = ["--dir", "C:\\Some Project", "--effort", "high", "-x", ""]
    for first in ("TMT.py", "/usr/local/bin/tmtcode"):
        command = U.restart_command([first] + arguments, executable="py")
        assert command[-len(arguments):] == arguments, command
    assert U.restart_command([], executable="py") == ["py"]
    assert U.restart_command(["  "], executable="py") == ["py"]


def test_is_python_script_decides_between_the_two_launch_shapes():
    """A small pure function precisely so both shapes can be driven without
    spawning anything, and so the decision is readable rather than buried in
    an if."""
    assert U.is_python_script("TMT.py")
    assert U.is_python_script("/opt/tmt/TMT.PY")
    assert U.is_python_script("run.pyw")
    assert not U.is_python_script("tmtcode")
    assert not U.is_python_script("/usr/local/bin/tmtcode")
    assert not U.is_python_script("tmtcode.exe")
    assert not U.is_python_script("")


def test_restart_replaces_this_process_on_posix():
    """`os.execv` keeps one pid, one console and one exit status, which is
    what makes the handover invisible to whoever is watching the terminal.
    Injected, so no process is created here."""
    calls = []
    environ = {"PATH": "/usr/bin"}
    before = os.environ.get(U.RESTART_ENV_VAR, MISSING)
    U.restart(argv=["/opt/bin/tmtcode", "--dir", "X"], executable="/usr/bin/py",
              environ=environ, windows=False,
              execv=lambda path, args: calls.append((path, args)),
              spawn=_never("spawn"), exit_with=_never("exit"))
    assert calls == [("/opt/bin/tmtcode",
                      ["/opt/bin/tmtcode", "--dir", "X"])], calls
    # The caller's own mapping is never written into, and neither is this
    # process's: a test that handed in an environment must not leave one
    # behind for whatever runs next.
    assert environ == {"PATH": "/usr/bin"}
    assert os.environ.get(U.RESTART_ENV_VAR, MISSING) is before


def test_restart_spawns_and_exits_on_windows():
    """os.execv is unreliable for a console application on Windows -- the
    parent can exit while the child is still attached to the console, which
    puts a live TMT behind a returned prompt. Spawning and exiting with the
    child's code is the shape that behaves."""
    spawned, exits = [], []
    U.restart(argv=["TMT.py", "--dir", "X"], executable="py.exe",
              environ={"PATH": "C:\\bin"}, windows=True,
              execv=_never("execv"),
              spawn=lambda command, env=None: (spawned.append((command, env)), 3)[1],
              exit_with=exits.append)
    assert spawned[0][0] == ["py.exe", "TMT.py", "--dir", "X"], spawned
    assert spawned[0][1][U.RESTART_ENV_VAR] == "1", spawned[0][1]
    assert exits == [3], exits


def test_a_handover_that_cannot_be_made_falls_back_instead_of_raising():
    """If execv cannot replace the process the update has already been
    applied, so raising would leave the user with new code, an old process and
    a traceback. Spawning is the fallback, and it is the same one Windows
    uses."""
    spawned, exits = [], []

    def refuse(_path, _args):
        raise OSError("execv is not available here")

    U.restart(argv=["tmtcode"], executable="py", environ={}, windows=False,
              execv=refuse,
              spawn=lambda command, env=None: (spawned.append(command), 0)[1],
              exit_with=exits.append)
    assert spawned == [["tmtcode"]], spawned
    assert exits == [0], exits


def test_restart_env_raises_the_counter_on_a_copy():
    """The counter travels in the environment because that is the only channel
    a fresh interpreter is guaranteed to read. It is a copy so the process
    about to be replaced can still report a failure with its own environment
    intact."""
    source = {"PATH": "/bin"}
    prepared = U.restart_env(source)
    assert prepared[U.RESTART_ENV_VAR] == "1"
    assert prepared["PATH"] == "/bin"
    assert source == {"PATH": "/bin"}
    assert U.restart_env(prepared)[U.RESTART_ENV_VAR] == "2"


def test_restarts_used_reads_the_counter_and_fails_safe_on_anything_else():
    """The value comes from the environment, so a user can set it to anything.
    No value they can set may crash the launch, and none may buy an extra
    restart: everything unreadable reads as "already restarted"."""
    assert U.restarts_used({}) == 0
    assert U.restarts_used({U.RESTART_ENV_VAR: ""}) == 0
    assert U.restarts_used({U.RESTART_ENV_VAR: " 2 "}) == 2
    for hostile in ("nonsense", "1; rm -rf /", "-1", "-99", "1.5", "0x2",
                    "\x00", "99999999999999999999999999a"):
        used = U.restarts_used({U.RESTART_ENV_VAR: hostile})
        assert used >= U.MAX_UPDATE_RESTARTS, (hostile, used)
    assert U.restarts_used(object()) >= U.MAX_UPDATE_RESTARTS


def test_the_guard_stops_a_second_restart_but_not_a_second_check():
    """The distinction the whole restart design rests on.

    The process that has already restarted is the one the user is looking at,
    and it is expected to check again and say "up to date" -- that is the
    screen the flow produces. What it may not do is apply another update and
    restart again, because that is the loop. So a bounded restart still
    fetches and still compares; it simply refuses to move.
    """
    if not git_ready():
        return
    box = Sandbox()
    try:
        box.advance_remote()
        head = box.head()
        spent = {U.RESTART_ENV_VAR: str(U.MAX_UPDATE_RESTARTS)}
        engine = box.engine()
        blocked = U.check_and_update(git=engine, env=spent)
        assert blocked.status == U.DISABLED, blocked.as_dict()
        assert box.head() == head, "a bounded launch must not move the checkout"
        assert "fetch" in engine.subcommands(), engine.calls
        assert "merge" not in engine.subcommands(), engine.calls
        assert blocked.behind >= 1, blocked.as_dict()

        # The same guard, on a checkout that has nothing to take: the check
        # still happens and still reports.
        fresh = Sandbox()
        try:
            current = U.check_and_update(root=fresh.install, env=spent)
            assert current.status == U.CURRENT, current.as_dict()
        finally:
            fresh.close()
    finally:
        box.close()


def test_a_caller_that_cannot_restart_is_told_rather_than_updated_behind_it():
    """Applying an update the caller cannot restart into would leave this
    process running code that is no longer on disk -- and TMT imports some
    modules lazily, so the next lazy import would load half of a different
    version. Looking is still allowed; moving is not."""
    engine = FakeGit(behind=4)
    result = check(engine, allow_restart=False)
    assert result.status == U.DISABLED, result.as_dict()
    assert "merge" not in engine.subcommands(), engine.calls
    assert "fetch" in engine.subcommands(), engine.calls
    assert result.behind == 4


def test_the_restart_ceiling_is_one_and_is_the_number_the_guard_uses():
    """One automatic restart per launch. Stated as a constant so the caller
    can say so on screen, and asserted here so a change to it is a deliberate
    edit to a test rather than a silent widening of the loop."""
    assert U.MAX_UPDATE_RESTARTS == 1
    assert U.RESTART_ENV_VAR == "TMT_UPDATE_RESTARTS"
    assert U.restarts_used({U.RESTART_ENV_VAR: "1"}) >= U.MAX_UPDATE_RESTARTS
    assert U.restarts_used({U.RESTART_ENV_VAR: "0"}) < U.MAX_UPDATE_RESTARTS


# --- what the module may never contain -------------------------------------

def test_no_history_destroying_git_command_appears_anywhere_in_the_module():
    """A source scan, in the precedent of the one over agent_git.

    Every phrase here throws away work that exists nowhere else, and the
    module runs unattended, at startup, before the user has typed anything.
    The scan covers prose as well as code deliberately: a comment describing
    how to reset the checkout is a copy-paste away from being run, and there
    is no reason for any of these to appear in this file at all.
    """
    source = module_source().lower()
    for phrase in BANNED_PHRASES:
        assert phrase not in source, "%r appears in agent_update.py" % phrase
    # The fast-forward is the one command that may change anything.
    assert "--ff-only" in source, "the fast-forward guard is missing"


def test_no_argument_literal_in_the_module_can_destroy_work():
    """The stricter half, and the one a rewrite is likely to trip.

    Every git command in this module and in agent_git is a list of strings, so
    the elements of the list and tuple literals are exactly the vocabulary
    that can reach git -- and they are read here as though each one were about
    to be handed to it. Prose is judged separately and more narrowly: a
    headline saying "this checkout has commits of its own" contains the word
    `checkout` and is not a command, but no string anywhere may BE a flag that
    throws work away.
    """
    tree = ast.parse(module_source())
    arguments = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        for element in node.elts:
            if not (isinstance(element, ast.Constant)
                    and isinstance(element.value, str)):
                continue
            arguments += 1
            token = element.value.strip().lower()
            assert token not in BANNED_ARGUMENTS, (
                "%r is an element of a command literal" % element.value)
            assert not token.startswith("--force"), element.value
    assert arguments > 10, "the command scan read almost nothing; check the walk"

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value.strip().lower() not in BANNED_FLAGS, node.value
        elif isinstance(node, ast.keyword) and node.arg:
            assert "force" not in node.arg.lower(), node.arg
        elif isinstance(node, ast.Name):
            assert "force" not in node.id.lower(), node.id


def test_the_module_neither_prints_nor_reads_input_nor_starts_a_thread():
    """The division this module is built on: pure logic and git, and the
    caller owns every character on screen. A print here would land in the
    middle of somebody's splash screen, and an input() would block a launch on
    a prompt drawn under a live region that knows nothing about it."""
    source = module_source()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ("print", "input"), node.func.id
    for forbidden in ("import threading", "from threading",
                      "agent_ui", "agent_menu", "agent_panel"):
        assert forbidden not in source, forbidden


def test_the_module_imports_cleanly_and_exposes_the_api_the_caller_needs():
    """The splash screen is wired to these names. A rename is a deliberate
    edit to this list, not something a caller discovers at startup."""
    for name in ("check_and_update", "restart", "restart_command", "restart_env",
                 "restarts_used", "compare_status", "split_upstream",
                 "is_python_script", "UpdateResult", "MAX_UPDATE_RESTARTS",
                 "RESTART_ENV_VAR", "STATUSES", "FETCH_TIMEOUT"):
        assert hasattr(U, name), "agent_update.%s is missing" % name
    assert callable(U.check_and_update)
    assert U.FETCH_TIMEOUT <= 30, "a startup fetch must not be able to hang a launch"


def _never(name):
    """A stand-in for the path a test says must not be taken."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("%s was called and should not have been" % name)

    return refuse
