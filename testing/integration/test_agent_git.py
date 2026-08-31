"""Tests for TMT's git engine, exercised against real temporary repositories.

Nothing here touches the developer's repository or git configuration: every git
process runs with GIT_CONFIG_NOSYSTEM and a HOME pointed at a throwaway
directory, and the engine's identity is an example.invalid address. Pushes go to
a real bare repository created per test, so the push paths are genuinely
exercised rather than simulated.

TMT is the co-author of the commits it makes, never the author. The human whose
git configuration the repository holds stays in %an, %ae, %cn and %ce, and TMT
is credited by a Co-authored-by trailer beside them. That is one property, not
two, so the tests below assert both halves of it together wherever they can: a
change that put TMT back in the author field while still writing the trailer
would satisfy either half on its own.
"""

import ast
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

TMT_NAME = "TMT code"
TMT_EMAIL = "tmt-code@example.invalid"
REPO_NAME = "Repo Human"
REPO_EMAIL = "repo-human@example.invalid"
GLOBAL_EMAIL = "global-human@example.invalid"

TMT_SIGNATURE = f"{TMT_NAME} <{TMT_EMAIL}>"
TMT_TRAILER = f"Co-authored-by: {TMT_SIGNATURE}"
HUMAN_SIGNATURE = f"{REPO_NAME} <{REPO_EMAIL}>"

# Parsed here rather than by the module under test: a helper that asked
# agent_git to read the trailers it wrote would agree with itself.
_TRAILER_ADDRESS = re.compile(r"<([^<>]+)>")

# Captured before any test mutates os.environ, so the developer's real global
# config can still be read back for comparison.
REAL_ENV = dict(os.environ)
GIT = shutil.which("git")

FORCE_TOKENS = ("--force", "--force-with-lease", "-f", "--mirror")

IMPORT_ERRORS = {}


def _import(name):
    """Import a module under test without letting a partial one abort the suite."""
    try:
        return __import__(name)
    except Exception as error:            # a half-written module is a test failure, not a crash
        IMPORT_ERRORS[name] = repr(error)
        return None


agent_git = _import("agent_git")
agent_config = _import("agent_config")
agent_actions = _import("agent_actions")
agent_prompt = _import("agent_prompt")

# The project root, derived from a MODULE rather than from __file__: this file
# lives in testing/integration/, so __file__.parent is the test directory and
# not the tree these tests read agent_git.py, .tmt_git, .gitignore and TMT.py
# out of. agent_config resolves its own identity files from its own __file__,
# so the directory holding it IS the root. The fallback keeps a half-written
# agent_config a test failure rather than an import error that aborts the run,
# which is the whole point of _import above.
PROJECT_DIR = (Path(agent_config.__file__).resolve().parent
               if agent_config is not None
               else Path(__file__).resolve().parents[2])


def require(module, name):
    assert module is not None, f"cannot import {name}: {IMPORT_ERRORS.get(name)}"


MISSING = object()


def set_module_attrs(module, values):
    previous = {}
    for key, value in values.items():
        previous[key] = getattr(module, key, MISSING)
        setattr(module, key, value)
    return previous


def restore_module_attrs(module, previous):
    for key, value in (previous or {}).items():
        if value is MISSING:
            try:
                delattr(module, key)
            except AttributeError:
                pass
        else:
            setattr(module, key, value)


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


def global_config(env, cwd):
    """`git config --global --list`, or "" when there is no global config."""
    result = subprocess.run(
        [GIT, "config", "--global", "--list"], cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=60,
    )
    return result.stdout if result.returncode == 0 else ""


class Sandbox:
    """A temporary HOME, work repository and (optionally) a bare remote.

    Besides the filesystem, the constructor redirects HOME and the TMT identity
    in this process's own environment and in agent_config, because the engine
    builds its subprocess environment from os.environ at call time. close()
    restores every one of those globals and must run in a finally block.
    """

    def __init__(self, name=TMT_NAME, email=TMT_EMAIL, with_remote=True):
        self._previous_env = None
        self._previous_attrs = None
        self.base = Path(tempfile.mkdtemp(prefix="tmt_git_test_"))
        self.home = self.base / "home"
        self.home.mkdir()
        self.repo = self.base / "work"
        self.repo.mkdir()
        self.bare = None
        self.env = dict(REAL_ENV)
        self.env.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / "xdg"),
            "GIT_TERMINAL_PROMPT": "0",
        })
        for leak in ("GIT_DIR", "GIT_WORK_TREE", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                     "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            self.env.pop(leak, None)
        self._init_repo()
        if with_remote:
            self._init_remote()
        self._previous_env = apply_env({
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / "xdg"),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_DIR": None,
            "GIT_WORK_TREE": None,
            "TMT_GIT_NAME": name,
            "TMT_GIT_EMAIL": email,
            "TMT_GIT_ROOT": str(self.repo),
        })
        self._previous_attrs = set_module_attrs(agent_config, {
            "TMT_GIT_NAME": name or "",
            "TMT_GIT_EMAIL": email or "",
            "TMT_GIT_ROOT": str(self.repo),
            # A developer's own .tmt_git must never decide a test's outcome.
            "GIT_IDENTITY_FILE": self.base / "absent.tmt_git",
        })

    @staticmethod
    def url(path):
        """A local path git accepts as a remote on every platform."""
        return str(path).replace("\\", "/")

    def git(self, args, cwd=None, check=True):
        result = subprocess.run(
            [GIT] + [str(arg) for arg in args], cwd=str(cwd or self.repo), env=self.env,
            capture_output=True, text=True, timeout=120,
        )
        if check:
            assert result.returncode == 0, f"git {' '.join(str(a) for a in args)}: {result.stderr}"
        return result.stdout.strip()

    def bare_git(self, args):
        return self.git(["--git-dir", str(self.bare)] + list(args), cwd=self.base)

    def write(self, name, text):
        target = self.repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def engine(self):
        return agent_git.TMTGit(root=str(self.repo))

    def head(self):
        return self.git(["rev-parse", "HEAD"])

    def _init_repo(self):
        self.git(["init", "-q"])
        self.git(["symbolic-ref", "HEAD", "refs/heads/main"])
        self.git(["config", "user.name", "Repo Human"])
        self.git(["config", "user.email", REPO_EMAIL])
        self.git(["config", "commit.gpgsign", "false"])
        self.write("README.md", "initial\n")
        self.git(["add", "README.md"])
        self.git(["commit", "-q", "-m", "initial"])

    def _init_remote(self):
        self.bare = self.base / "remote.git"
        self.git(["init", "--bare", "-q", self.url(self.bare)], cwd=self.base)
        self.bare_git(["symbolic-ref", "HEAD", "refs/heads/main"])
        self.git(["remote", "add", "origin", self.url(self.bare)])
        self.git(["push", "-q", "-u", "origin", "main"])

    def close(self):
        restore_module_attrs(agent_config, self._previous_attrs)
        restore_env(self._previous_env)
        remove_tree(self.base)


def ready(*modules):
    """Whether this test can run at all: git on PATH and its modules importable."""
    if GIT is None:
        return False
    for name in modules:
        require(globals()[name], name)
    return True


def trailer_lines(sandbox, key=None, ref="HEAD", bare=False):
    """The trailers git itself parses out of one commit's message.

    Asked of git's own trailer parser rather than searched for in the message
    text, because a line that merely looks like a trailer is not one. Only what
    this returns will be read downstream, GitHub's co-author credit included.
    """
    token = key or agent_git.CO_AUTHOR_TOKEN
    args = ["log", "-1", f"--format=%(trailers:key={token})", ref]
    raw = sandbox.bare_git(args) if bare else sandbox.git(args)
    return [line.strip() for line in raw.splitlines() if line.strip()]


def trailer_addresses(lines):
    """The addresses inside trailer lines, lowercased."""
    found = []
    for line in lines:
        match = _TRAILER_ADDRESS.search(line)
        if match:
            found.append(match.group(1).strip().lower())
    return found


def identity_fields(sandbox, ref="HEAD", bare=False):
    """[author name, author email, committer name, committer email]."""
    args = ["log", "-1", "--format=%an|%ae|%cn|%ce", ref]
    raw = sandbox.bare_git(args) if bare else sandbox.git(args)
    return raw.split("|")


def assert_human_authored_with_tmt_co_author(sandbox, ref="HEAD", bare=False):
    """The property that matters, asserted as the single thing it is.

    The human on all four identity fields and TMT credited in a trailer git
    parses are checked on the same commit, because either half alone would
    still pass if TMT were put back into the author field.
    """
    fields = identity_fields(sandbox, ref=ref, bare=bare)
    assert fields == [REPO_NAME, REPO_EMAIL, REPO_NAME, REPO_EMAIL], (ref, fields)
    for field in fields:
        assert TMT_NAME not in field, (ref, fields)
        assert TMT_EMAIL not in field, (ref, fields)
    addresses = trailer_addresses(trailer_lines(sandbox, ref=ref, bare=bare))
    assert TMT_EMAIL in addresses, (ref, addresses)


# Case 1
def test_commit_records_the_human_as_author_and_committer_with_tmt_as_co_author():
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox()
    try:
        assert agent_git.TMTGitIdentity.DEFAULT_NAME == TMT_NAME
        sandbox.write("a.txt", "one\n")
        commit = sandbox.engine().commit("add a", paths=["a.txt"])
        fields = sandbox.git(["log", "-1", "--format=%an|%ae|%cn|%ce"]).split("|")
        assert fields == [REPO_NAME, REPO_EMAIL, REPO_NAME, REPO_EMAIL], fields
        assert_human_authored_with_tmt_co_author(sandbox)
        assert trailer_lines(sandbox) == [TMT_TRAILER], trailer_lines(sandbox)
        assert commit["author"] == HUMAN_SIGNATURE, commit
        assert commit["committer"] == HUMAN_SIGNATURE, commit
        assert commit["co_author"] == TMT_SIGNATURE, commit
        assert commit["branch"] == "main"
        assert commit["sha"] == sandbox.head()
        assert commit["sha"].startswith(commit["short"])
        assert "a.txt" in commit["files"]
    finally:
        sandbox.close()


# Case 2
def test_the_repo_local_user_stays_the_author_and_tmt_is_only_the_co_author():
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox()
    try:
        assert sandbox.git(["config", "user.email"]) == REPO_EMAIL
        assert REPO_EMAIL != TMT_EMAIL
        human = sandbox.git(["log", "-1", "--format=%ae"])
        assert human == REPO_EMAIL          # the repo commits as the human, and keeps doing so

        sandbox.write("a.txt", "one\n")
        sandbox.engine().commit("add a", paths=["a.txt"])
        assert sandbox.git(["log", "-1", "--format=%ae"]) == REPO_EMAIL
        assert sandbox.git(["log", "-1", "--format=%ce"]) == REPO_EMAIL
        # TMT is credited beside the human rather than in place of them.
        assert trailer_lines(sandbox) == [TMT_TRAILER], trailer_lines(sandbox)
        # And it did not reach the author field by rewriting the repo's setting.
        assert sandbox.git(["config", "user.email"]) == REPO_EMAIL
    finally:
        sandbox.close()


# Case 3
def test_the_global_git_config_is_byte_identical_after_a_commit_and_push():
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox()
    try:
        sandbox.git(["config", "--global", "user.name", "Global Human"], cwd=sandbox.base)
        sandbox.git(["config", "--global", "user.email", GLOBAL_EMAIL], cwd=sandbox.base)
        before_isolated = global_config(sandbox.env, sandbox.base)
        before_real = global_config(REAL_ENV, sandbox.base)
        assert GLOBAL_EMAIL in before_isolated

        sandbox.write("a.txt", "one\n")
        engine = sandbox.engine()
        engine.commit("add a", paths=["a.txt"])
        engine.push()

        assert global_config(sandbox.env, sandbox.base) == before_isolated
        assert global_config(REAL_ENV, sandbox.base) == before_real
    finally:
        sandbox.close()


# Case 4
def test_the_repo_local_git_config_is_byte_identical_after_a_commit_and_push():
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox()
    try:
        before = sandbox.git(["config", "--local", "--list"])
        sandbox.write("a.txt", "one\n")
        engine = sandbox.engine()
        engine.commit("add a", paths=["a.txt"])
        engine.push()
        assert sandbox.git(["config", "--local", "--list"]) == before
    finally:
        sandbox.close()


# Case 5
def test_a_missing_tmt_git_email_raises_and_leaves_no_commit():
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox(email=None)
    try:
        before = sandbox.head()
        sandbox.write("a.txt", "one\n")
        raised = None
        try:
            sandbox.engine().commit("add a", paths=["a.txt"])
        except agent_git.GitError as error:
            raised = error
        assert raised is not None, "an unset TMT_GIT_EMAIL must not commit as the human"
        assert "email" in str(raised).lower(), str(raised)
        assert sandbox.head() == before
        assert "a.txt" in sandbox.git(["status", "--porcelain"])
        # No commit, and so no credit: an unusable address must leave no trace
        # of TMT in the history, neither as an author nor as a co-author.
        assert TMT_EMAIL not in sandbox.git(["log", "--format=%B%n%an%n%ae%n%cn%n%ce"])
        assert trailer_lines(sandbox) == [], trailer_lines(sandbox)
    finally:
        sandbox.close()


# Case 6
def test_commit_stages_only_the_paths_it_was_given():
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox()
    try:
        sandbox.write("tmt.txt", "written by tmt\n")
        sandbox.write("human.txt", "written by the human\n")
        sandbox.engine().commit("tmt's own change", paths=["tmt.txt"])

        committed = sandbox.git(["log", "-1", "--name-only", "--format="]).split()
        assert committed == ["tmt.txt"], committed
        assert "human.txt" in sandbox.git(["status", "--porcelain"])
        assert "human.txt" not in sandbox.git(["log", "--name-only", "--format="])
    finally:
        sandbox.close()


# Case 7
def test_stage_all_commits_every_dirty_file():
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox()
    try:
        sandbox.write("tmt.txt", "written by tmt\n")
        sandbox.write("human.txt", "written by the human\n")
        commit = sandbox.engine().commit("everything", stage_all=True)

        committed = sorted(sandbox.git(["log", "-1", "--name-only", "--format="]).split())
        assert committed == ["human.txt", "tmt.txt"], committed
        assert sorted(commit["files"]) == ["human.txt", "tmt.txt"], commit
        assert sandbox.git(["status", "--porcelain"]) == ""
    finally:
        sandbox.close()


# Case 8
def test_a_pushed_commit_reaches_the_bare_remote_with_the_human_author_and_the_trailer():
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox()
    try:
        sandbox.write("a.txt", "one\n")
        engine = sandbox.engine()
        commit = engine.commit("add a", paths=["a.txt"])
        pushed = engine.push()

        assert {"remote", "branch", "remote_url_host", "summary"} <= set(pushed), pushed
        assert pushed["remote"] == "origin"
        assert pushed["branch"] == "main"
        assert sandbox.bare_git(["rev-parse", "refs/heads/main"]) == commit["sha"]
        # What reached the remote is what matters: whoever pulls this sees the
        # human as the author and TMT only in a trailer git can parse.
        assert_human_authored_with_tmt_co_author(sandbox, ref="refs/heads/main", bare=True)
        assert trailer_lines(sandbox, ref="refs/heads/main", bare=True) == [TMT_TRAILER]
        who = sandbox.bare_git(["log", "-1", "--format=%an|%ae", "refs/heads/main"]).split("|")
        assert who == [REPO_NAME, REPO_EMAIL], who
    finally:
        sandbox.close()


# Case 9
def test_a_push_without_a_remote_fails_and_keeps_the_commit_local():
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox(with_remote=False)
    try:
        sandbox.write("a.txt", "one\n")
        engine = sandbox.engine()
        commit = engine.commit("add a", paths=["a.txt"])
        assert engine.remotes() == []

        raised = None
        try:
            engine.push()
        except agent_git.GitError as error:
            raised = error
        assert raised is not None, "a push with no remote must raise"
        assert str(raised).strip() != ""
        assert sandbox.head() == commit["sha"]
        assert commit["sha"] in sandbox.git(["log", "--format=%H"])
    finally:
        sandbox.close()


# Case 10
def test_a_diverged_remote_rejects_the_push_and_is_never_force_updated():
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox()
    calls = []
    real_run = subprocess.run

    def recording_run(args, **kwargs):
        calls.append(args)
        return real_run(args, **kwargs)

    try:
        clone = sandbox.base / "clone"
        sandbox.git(["clone", "-q", sandbox.url(sandbox.bare), sandbox.url(clone)],
                    cwd=sandbox.base)
        sandbox.git(["config", "user.name", "Other Human"], cwd=clone)
        sandbox.git(["config", "user.email", "other-human@example.invalid"], cwd=clone)
        (clone / "other.txt").write_text("from another clone\n", encoding="utf-8")
        sandbox.git(["add", "other.txt"], cwd=clone)
        sandbox.git(["commit", "-q", "-m", "the remote moves on"], cwd=clone)
        sandbox.git(["push", "-q", "origin", "main"], cwd=clone)
        diverged = sandbox.bare_git(["rev-parse", "refs/heads/main"])

        sandbox.write("a.txt", "one\n")
        engine = sandbox.engine()
        commit = engine.commit("local work", paths=["a.txt"])
        assert commit["sha"] != diverged

        raised = None
        agent_git.subprocess.run = recording_run
        try:
            engine.push()
        except agent_git.GitError as error:
            raised = error
        finally:
            agent_git.subprocess.run = real_run

        assert raised is not None, "a non-fast-forward push must raise"
        message = str(raised).lower()
        assert any(word in message for word in
                   ("fast-forward", "fast forward", "diverg", "behind", "reject", "pull", "fetch")), message

        assert sandbox.head() == commit["sha"], "a failed push must leave the commit intact"
        assert commit["sha"] in sandbox.git(["log", "--format=%H"])
        assert sandbox.bare_git(["rev-parse", "refs/heads/main"]) == diverged
        for args in calls:
            assert isinstance(args, (list, tuple)), args
            for token in [str(item) for item in args]:
                assert token not in FORCE_TOKENS, args
                assert not token.startswith("--force"), args
                assert not token.startswith("+refs/"), args
    finally:
        agent_git.subprocess.run = real_run
        sandbox.close()


# Case 11
def test_authorizes_push_matches_a_push_request_and_nothing_else():
    require(agent_actions, "agent_actions")
    assert hasattr(agent_actions, "authorizes_push"), "agent_actions.authorizes_push is missing"
    authorizes = agent_actions.authorizes_push

    for task in ("commit and push to main", "push this to git", "push the finished changes",
                 "push it up", "send it to github", "Push to main please"):
        assert authorizes(task), task

    for task in ("fix the authentication bug", "commit this", "don't push it",
                 "do not push", "no push", "clean up the readme"):
        assert not authorizes(task), task


# Case 12
def test_the_action_layer_blocks_a_push_the_user_did_not_ask_for():
    if not ready("agent_git", "agent_config", "agent_actions"):
        return
    sandbox = Sandbox()
    try:
        remote_before = sandbox.bare_git(["rev-parse", "refs/heads/main"])
        sandbox.write("a.txt", "one\n")
        commit = sandbox.engine().commit("add a", paths=["a.txt"])
        assert commit["sha"] != remote_before

        blocked = str(agent_actions.execute_action(
            {"action": "git_push"}, context={"push_authorized": False}))
        assert blocked.startswith("BLOCKED"), blocked
        assert sandbox.bare_git(["rev-parse", "refs/heads/main"]) == remote_before

        # The same action goes through once the user's own words allowed it.
        allowed = agent_actions.execute_action(
            {"action": "git_push"}, context={"push_authorized": True})
        assert sandbox.bare_git(["rev-parse", "refs/heads/main"]) == commit["sha"], allowed
    finally:
        sandbox.close()


# Case 13
def test_every_git_example_in_the_system_prompt_parses_and_validates():
    require(agent_prompt, "agent_prompt")
    require(agent_config, "agent_config")
    git_actions = ("git_status", "git_diff", "git_identity", "git_commit", "git_push")
    for action in git_actions:
        assert action in agent_config.REQUIRED_KEYS, action
    assert agent_config.REQUIRED_KEYS["git_commit"] == ["message"]

    prompt = agent_prompt.get_system_prompt()
    # The workspace snapshot is user data, not prompt text; only the rules count.
    reference = prompt.split("=== CURRENT WORKSPACE FILES")[0]
    counts = {action: 0 for action in git_actions}
    examples = 0
    for line in reference.splitlines():
        line = line.strip()
        if not line.startswith("{") or "git_" not in line:
            continue
        examples += 1
        obj = json.loads(line)              # an unparseable example is a broken example
        for entry in obj.get("actions", [obj]):
            error = agent_prompt.validate_action(entry)
            assert error is None, f"{line}: {error}"
            if entry.get("action") in counts:
                counts[entry["action"]] += 1
    assert examples, "the prompt contains no git examples"
    for action, count in counts.items():
        assert count >= 2, f"{action} needs two working examples, found {count}"


# Case 14
def test_no_code_path_in_the_git_module_can_force_push():
    source_path = PROJECT_DIR / "agent_git.py"
    assert source_path.exists(), f"{source_path} is missing"
    source = source_path.read_text(encoding="utf-8")

    def check(value, where):
        text = value.strip()
        assert text not in FORCE_TOKENS, f"force flag {text!r} in {where}"
        assert not text.startswith("--force"), f"force flag {text!r} in {where}"
        assert not text.startswith("+refs/"), f"forcing refspec {text!r} in {where}"

    tree = ast.parse(source)
    for node in ast.walk(tree):
        # Argument literals, not prose: a docstring may say the module never
        # force-pushes, but no string the module can hand git may be a force flag.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            check(node.value, "a string literal")
        elif isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    check(part.value, "an f-string")
        elif isinstance(node, ast.keyword) and node.arg:
            assert "force" not in node.arg.lower(), f"force keyword argument {node.arg}"
        elif isinstance(node, ast.arg):
            assert "force" not in node.arg.lower(), f"force parameter {node.arg}"
        elif isinstance(node, ast.Name):
            assert "force" not in node.id.lower(), f"force identifier {node.id}"
        elif isinstance(node, ast.Attribute):
            assert "force" not in node.attr.lower(), f"force attribute {node.attr}"


def test_a_push_fails_closed_when_no_authority_was_passed_at_all():
    """Absent context must read as "not authorized", never as "unrestricted".

    The gate protects the only action that leaves the machine, so a caller that
    forgets to thread the task's authority through has to lose the push, not
    win it. Left unasserted, a later refactor could silently invert this.
    """
    for context in (None, {}, {"push_authorized": False}, {"push_authorized": 0}):
        result = str(agent_actions.execute_action({"action": "git_push"}, context=context))
        assert result.startswith("BLOCKED"), (context, result[:80])
    # The default argument has to fail closed too, not just an explicit None.
    assert str(agent_actions.execute_action({"action": "git_push"})).startswith("BLOCKED")


# ---------------------------------------------------------------------------
# Round two: the portable identity files, the shipped placeholder, and git_diff.
#
# The identity now ships with the project in a tracked .tmt_git, overridden per
# machine by .tmt_git.local and per process by the environment. Precedence is
# decided inside agent_config from its own __file__, so the file tests import a
# real copy of that module beside real files rather than asserting on internals.
# ---------------------------------------------------------------------------

import importlib.util
import re

TRACKED_IDENTITY_FILE = PROJECT_DIR / ".tmt_git"
GITIGNORE_FILE = PROJECT_DIR / ".gitignore"
IDENTITY_ENV = ("TMT_GIT_NAME", "TMT_GIT_EMAIL", "TMT_GIT_ROOT")

# Credential shapes that are never legitimate anywhere in an identity file.
STRONG_CREDENTIAL_MARKERS = (
    "ssh-rsa", "ssh-ed25519", "ssh-dss", "ecdsa-sha2-", "private key",
    "-----begin", "-----end", "ghp_", "gho_", "ghu_", "ghs_", "ghr_",
    "github_pat_", "xoxb-", "xoxp-", "aws_secret",
)
# Words that belong in the file's own warning comment but never in a value.
VALUE_CREDENTIAL_MARKERS = (
    "token", "password", "passwd", "secret", "credential", "api_key",
    "apikey", "api-key", "bearer ", "private key",
)
ALLOWED_IDENTITY_KEYS = {"name", "email"}

# Signs that an address is a stand-in rather than a mailbox anyone owns.
PLACEHOLDER_MARKERS = (
    "replace_with", "replace-with", "replace with", "placeholder", "changeme",
    "change_me", "change-me", "your_email", "your-email", "your.email",
    "youremail", "your_name", "todo", "tbd", "fixme", "xxx", "<", ">",
)

_config_probe_count = 0


def read_identity_file(path):
    """Parse a .tmt_git style file into {'name': ..., 'email': ...}.

    Accepts both the TMT_GIT_NAME=/TMT_GIT_EMAIL= and the older name=/email=
    spellings, case-insensitively, because the shipped file may use either.
    """
    values = {}
    try:
        contents = Path(path).read_text(encoding="utf-8")
    except OSError:
        return values
    for line in contents.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        if key.startswith("tmt_git_"):
            key = key[len("tmt_git_"):]
        values[key] = value.strip()
    return values


def looks_like_a_placeholder_address(email):
    """This suite's own judgement of whether an address is a stand-in.

    Deliberately not agent_git's validate(): asking the validator whether the
    validator is right could never catch a placeholder it has learned to
    accept, which is the exact regression the shipped-file test guards against.
    """
    text = (email or "").strip()
    if not text:
        return True
    lowered = text.lower()
    if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
        return True
    if "@" not in text or any(character.isspace() for character in text):
        return True
    local, _, domain = text.partition("@")
    if not local or not domain or "." not in domain:
        return True
    letters = [character for character in local if character.isalpha()]
    # SHOUTING_LOCAL_PARTS are how placeholders are written, not addresses.
    return bool(letters) and all(character.isupper() for character in letters)


def load_config_copy(directory, tracked=None, local=None, env=None):
    """Import a copy of agent_config from `directory` and return the module.

    agent_config resolves its identity files from its own __file__, so the only
    faithful way to test the file precedence is to run the real code beside
    real files in a throwaway directory. The identity environment variables are
    cleared unless `env` sets them, so the developer's shell cannot decide the
    result. The resolved sources are captured while that environment is still
    in place, since resolution happens at call time.
    """
    global _config_probe_count
    directory = Path(directory)
    shutil.copyfile(str(PROJECT_DIR / "agent_config.py"),
                    str(directory / "agent_config.py"))
    if tracked is not None:
        (directory / ".tmt_git").write_text(tracked, encoding="utf-8")
    if local is not None:
        (directory / ".tmt_git.local").write_text(local, encoding="utf-8")
    overrides = {key: None for key in IDENTITY_ENV}
    overrides.update(env or {})
    previous = apply_env(overrides)
    _config_probe_count += 1
    probe_name = "tmt_config_probe_%d" % _config_probe_count
    try:
        spec = importlib.util.spec_from_file_location(
            probe_name, str(directory / "agent_config.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[probe_name] = module
        spec.loader.exec_module(module)
        resolver = getattr(module, "resolve_git_identity", None)
        module._probe_sources = resolver() if callable(resolver) else None
        return module
    finally:
        sys.modules.pop(probe_name, None)
        restore_env(previous)


def config_from_files(tracked=None, local=None, env=None):
    """load_config_copy in a temp directory that is removed afterwards."""
    base = Path(tempfile.mkdtemp(prefix="tmt_cfg_test_"))
    try:
        return load_config_copy(base, tracked=tracked, local=local, env=env)
    finally:
        remove_tree(base)


def reported_source(module, kind):
    """The module's own label for where `kind` ('email'/'name') came from.

    Returns None when nothing reports it: the contract fixes the labels but not
    the API carrying them, so a miss is "not reported", not a failure.
    """
    sources = getattr(module, "_probe_sources", None)
    if isinstance(sources, dict):
        label = sources.get(kind + "_source")
        if isinstance(label, str):
            return label
    return None


def isolate_identity_files(base):
    """Point every *_IDENTITY_FILE setting at a path that does not exist.

    The tracked .tmt_git is a real file in this repository and a developer may
    also hold a .tmt_git.local; neither may decide the outcome of a test that
    sets the identity itself. Returns the previous values, for restoring.
    """
    targets = {}
    for key in dir(agent_config):
        if "IDENTITY_FILE" not in key or key.startswith("_"):
            continue
        if isinstance(getattr(agent_config, key, None), (str, Path)):
            targets[key] = Path(base) / ("absent_" + key.lower())
    return set_module_attrs(agent_config, targets)


def scan_project_text_files():
    """Yield (repo-relative path, text) for every readable text file.

    .git and __pycache__ are skipped as VCS and build noise; anything that is
    not decodable UTF-8, or large enough to be a bundled binary, is skipped
    because only source and documentation can meaningfully name a module.
    """
    for directory, subdirectories, filenames in os.walk(str(PROJECT_DIR)):
        subdirectories[:] = [name for name in subdirectories
                             if name not in (".git", "__pycache__", ".venv", "venv")]
        for filename in filenames:
            path = Path(directory) / filename
            try:
                if path.stat().st_size > 2000000:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            yield path.relative_to(PROJECT_DIR).as_posix(), text


# Round two, case 1
def test_the_entry_point_is_tmt_and_nothing_still_names_the_old_module():
    entry = PROJECT_DIR / "TMT.py"
    assert entry.exists(), f"{entry} is missing: the entry point is now TMT.py"
    try:
        module = __import__("TMT")
    except Exception as error:          # a broken entry point is a failure, not a crash
        raise AssertionError(f"cannot import TMT: {error!r}")
    for name in ("main", "stream_handler"):
        assert callable(getattr(module, name, None)), f"TMT.{name} is missing or not callable"

    # Assembled rather than written out, so the scanner never matches itself.
    legacy = "agent" + "1"
    pattern = re.compile(r"\b" + legacy + r"\b")
    offenders = []
    for relative, text in scan_project_text_files():
        if not pattern.search(text):
            continue
        number = next((index for index, line in enumerate(text.splitlines(), 1)
                       if pattern.search(line)), 0)
        offenders.append(f"{relative}:{number}")
    assert not offenders, "the old module name survives in: " + ", ".join(offenders)


# Round two, case 2
def test_the_tracked_identity_file_loads_the_tmt_git_prefixed_spelling():
    module = config_from_files(tracked=(
        "# TMT's own git identity, committed on purpose.\n"
        "TMT_GIT_NAME=Tracked Name\n"
        "TMT_GIT_EMAIL=tracked@example.invalid\n"
    ))
    assert module.TMT_GIT_NAME == "Tracked Name", module.TMT_GIT_NAME
    assert module.TMT_GIT_EMAIL == "tracked@example.invalid", module.TMT_GIT_EMAIL
    source = reported_source(module, "email")
    if source is not None:
        assert source == ".tmt_git", source

    # Keys are matched case-insensitively, so a hand-edited file still loads.
    mixed = config_from_files(tracked=(
        "\n"
        "  tmt_git_name = Mixed Case Name  \n"
        "Tmt_Git_Email=mixed@example.invalid\n"
    ))
    assert mixed.TMT_GIT_NAME == "Mixed Case Name", mixed.TMT_GIT_NAME
    assert mixed.TMT_GIT_EMAIL == "mixed@example.invalid", mixed.TMT_GIT_EMAIL


# Round two, case 3
def test_the_tracked_identity_file_still_loads_the_legacy_spelling():
    module = config_from_files(tracked="name=Legacy Name\nemail=legacy@example.invalid\n")
    assert module.TMT_GIT_NAME == "Legacy Name", module.TMT_GIT_NAME
    assert module.TMT_GIT_EMAIL == "legacy@example.invalid", module.TMT_GIT_EMAIL

    # A file written in both spellings must still yield one usable identity.
    both = config_from_files(tracked=(
        "# a file half-migrated to the new spelling\n"
        "name=Legacy Name\n"
        "TMT_GIT_EMAIL=new@example.invalid\n"
    ))
    assert both.TMT_GIT_NAME == "Legacy Name", both.TMT_GIT_NAME
    assert both.TMT_GIT_EMAIL == "new@example.invalid", both.TMT_GIT_EMAIL


# Round two, case 4
def test_the_local_identity_file_overrides_the_tracked_one():
    module = config_from_files(
        tracked="TMT_GIT_NAME=Tracked Name\nTMT_GIT_EMAIL=tracked@example.invalid\n",
        local="TMT_GIT_NAME=Local Name\nTMT_GIT_EMAIL=local@example.invalid\n",
    )
    assert module.TMT_GIT_NAME == "Local Name", module.TMT_GIT_NAME
    assert module.TMT_GIT_EMAIL == "local@example.invalid", module.TMT_GIT_EMAIL
    source = reported_source(module, "email")
    if source is not None:
        assert source == ".tmt_git.local", source

    # Per key, not per file: overriding one value keeps the other.
    partial = config_from_files(
        tracked="TMT_GIT_NAME=Tracked Name\nTMT_GIT_EMAIL=tracked@example.invalid\n",
        local="email=local@example.invalid\n",
    )
    assert partial.TMT_GIT_NAME == "Tracked Name", partial.TMT_GIT_NAME
    assert partial.TMT_GIT_EMAIL == "local@example.invalid", partial.TMT_GIT_EMAIL


# Round two, case 5
def test_the_environment_overrides_both_identity_files():
    module = config_from_files(
        tracked="TMT_GIT_NAME=Tracked Name\nTMT_GIT_EMAIL=tracked@example.invalid\n",
        local="TMT_GIT_NAME=Local Name\nTMT_GIT_EMAIL=local@example.invalid\n",
        env={"TMT_GIT_NAME": "Environment Name",
             "TMT_GIT_EMAIL": "environment@example.invalid"},
    )
    assert module.TMT_GIT_NAME == "Environment Name", module.TMT_GIT_NAME
    assert module.TMT_GIT_EMAIL == "environment@example.invalid", module.TMT_GIT_EMAIL
    source = reported_source(module, "email")
    if source is not None:
        assert source == "environment", source

    # Overriding the email alone must not drag the name along with it.
    email_only = config_from_files(
        tracked="TMT_GIT_NAME=Tracked Name\nTMT_GIT_EMAIL=tracked@example.invalid\n",
        local="TMT_GIT_NAME=Local Name\nTMT_GIT_EMAIL=local@example.invalid\n",
        env={"TMT_GIT_EMAIL": "environment@example.invalid"},
    )
    assert email_only.TMT_GIT_NAME == "Local Name", email_only.TMT_GIT_NAME
    assert email_only.TMT_GIT_EMAIL == "environment@example.invalid", email_only.TMT_GIT_EMAIL


# Round two, case 6
def test_the_name_falls_back_to_tmt_code_and_the_email_never_does():
    bare = config_from_files()
    assert bare.TMT_GIT_NAME == TMT_NAME, bare.TMT_GIT_NAME
    # The one value with no default: inventing an email would let TMT author
    # commits under an address nobody owns, or fall through to the human's.
    assert bare.TMT_GIT_EMAIL == "", bare.TMT_GIT_EMAIL

    # An identity file naming only an address still gets the default name.
    email_only = config_from_files(tracked="TMT_GIT_EMAIL=tracked@example.invalid\n")
    assert email_only.TMT_GIT_NAME == TMT_NAME, email_only.TMT_GIT_NAME
    name_source = reported_source(email_only, "name")
    if name_source is not None:
        assert name_source == "built-in default", name_source

    # A file naming only a name still leaves the address unset.
    name_only = config_from_files(tracked="TMT_GIT_NAME=Tracked Name\n")
    assert name_only.TMT_GIT_NAME == "Tracked Name", name_only.TMT_GIT_NAME
    assert name_only.TMT_GIT_EMAIL == "", name_only.TMT_GIT_EMAIL

    if agent_git is not None:
        assert agent_git.TMTGitIdentity.DEFAULT_NAME == TMT_NAME


# Round two, case 7
def test_the_address_shipped_in_the_tracked_identity_file_cannot_author_a_commit():
    """The standing guard on the identity file every clone receives.

    The address is read from the real .tmt_git rather than hardcoded, so this
    keeps working once the placeholder is replaced: a genuine address must
    validate, and anything this suite reads as a stand-in must be refused with
    no commit created. A placeholder the validator learns to accept fails here.
    """
    if not ready("agent_git", "agent_config"):
        return
    assert TRACKED_IDENTITY_FILE.exists(), (
        f"{TRACKED_IDENTITY_FILE} must be tracked in the repository so that every "
        "clone starts with the same TMT identity")
    shipped = read_identity_file(TRACKED_IDENTITY_FILE)
    assert "email" in shipped, (
        f"{TRACKED_IDENTITY_FILE} declares no email; the shipped file carries the "
        "identity even while the address is still a placeholder")
    email = shipped["email"]

    if not looks_like_a_placeholder_address(email):
        # The placeholder has been replaced with a real address: it must work.
        agent_git.TMTGitIdentity(shipped.get("name") or TMT_NAME, email).validate()
        return

    sandbox = Sandbox(email=None)
    previous = None
    try:
        # The engine reads the real file layout, so the shipped file is copied
        # into the sandbox and pointed at rather than its value being injected.
        shipped_copy = sandbox.base / "shipped.tmt_git"
        shutil.copyfile(str(TRACKED_IDENTITY_FILE), str(shipped_copy))
        previous = isolate_identity_files(sandbox.base)
        previous.update(set_module_attrs(agent_config, {
            "GIT_IDENTITY_FILE": shipped_copy,
            "TMT_GIT_EMAIL": email,
            "TMT_GIT_NAME": shipped.get("name") or TMT_NAME,
        }))
        before = sandbox.head()
        sandbox.write("a.txt", "one\n")

        raised = None
        try:
            sandbox.engine().commit("add a", paths=["a.txt"])
        except agent_git.GitError as error:
            raised = error
        assert raised is not None, (
            f"the address shipped in {TRACKED_IDENTITY_FILE.name} ({email!r}) reads "
            "as a placeholder but the validator accepted it")

        assert sandbox.head() == before, "a refused identity must create no commit"
        assert "a.txt" in sandbox.git(["status", "--porcelain"])
        assert sandbox.git(["diff", "--cached", "--name-only"]) == ""
        assert email not in sandbox.git(["log", "--format=%ae%n%ce"])

        message = str(raised)
        lowered = message.lower()
        assert "email" in lowered or "address" in lowered, message
        assert "github" in lowered, (
            "the refusal must say the address has to be a real one on the TMT "
            "GitHub account: " + message)
        assert ".tmt_git" in message, (
            "the refusal must say where to put the address: " + message)

        described = agent_git.TMTGitIdentity.resolve().describe()
        assert "refused" in described.lower(), described
        assert ".tmt_git" in described, described
        for marker in STRONG_CREDENTIAL_MARKERS:
            assert marker not in described.lower(), marker
    finally:
        restore_module_attrs(agent_config, previous)
        sandbox.close()


# Round two, case 8
def test_a_malformed_tmt_email_is_refused_and_creates_no_commit():
    if not ready("agent_git", "agent_config"):
        return
    malformed = (
        "tmt-code.example.invalid",              # no @ at all
        "<tmt-code@example.invalid>",            # angle brackets
        "tmt-code@example.invalid>",
        "tmt code@example.invalid",              # whitespace inside the address
        "",
    )
    for bad in malformed:
        raised = None
        try:
            agent_git.TMTGitIdentity(TMT_NAME, bad).validate()
        except agent_git.GitError as error:
            raised = error
        assert raised is not None, f"{bad!r} was accepted as a git email"
        assert str(raised).strip() != ""

    sandbox = Sandbox(email=None)
    previous = None
    env_previous = None
    try:
        previous = isolate_identity_files(sandbox.base)
        before = sandbox.head()
        for index, bad in enumerate(malformed):
            env_previous = apply_env({"TMT_GIT_EMAIL": bad or None})
            set_module_attrs(agent_config, {"TMT_GIT_EMAIL": bad})
            sandbox.write("a.txt", f"attempt {index}\n")
            raised = None
            try:
                sandbox.engine().commit("add a", paths=["a.txt"])
            except agent_git.GitError as error:
                raised = error
            restore_env(env_previous)
            env_previous = None
            assert raised is not None, f"{bad!r} produced a commit"
            assert sandbox.head() == before, f"{bad!r} moved HEAD"
        # The index is untouched too: the identity is checked before anything
        # is staged, so a refusal leaves the repository as it was found.
        assert sandbox.git(["diff", "--cached", "--name-only"]) == ""
    finally:
        restore_env(env_previous)
        restore_module_attrs(agent_config, previous)
        sandbox.close()


# Round two, case 9
def test_two_successive_commits_carry_the_identical_human_author_and_tmt_co_author():
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox()
    try:
        engine = sandbox.engine()
        sandbox.write("first.txt", "one\n")
        first = engine.commit("first change", paths=["first.txt"])
        sandbox.write("second.txt", "two\n")
        second = engine.commit("second change", paths=["second.txt"])
        assert first["sha"] != second["sha"]

        assert first["author"] == second["author"] == HUMAN_SIGNATURE
        assert first["committer"] == second["committer"] == HUMAN_SIGNATURE
        assert first["co_author"] == second["co_author"] == TMT_SIGNATURE

        rows = sandbox.git(["log", "-2", "--format=%an|%ae|%cn|%ce"]).splitlines()
        assert len(rows) == 2, rows
        for row in rows:
            assert row.split("|") == [REPO_NAME, REPO_EMAIL, REPO_NAME, REPO_EMAIL], row
        for ref in ("HEAD", "HEAD~1"):
            assert_human_authored_with_tmt_co_author(sandbox, ref=ref)
            assert trailer_lines(sandbox, ref=ref) == [TMT_TRAILER], ref
        # The repository's own user decided both commits, and TMT never stood in.
        assert sandbox.git(["config", "user.email"]) == REPO_EMAIL
        assert TMT_EMAIL not in sandbox.git(["log", "-2", "--format=%ae %ce"])
        assert TMT_NAME not in sandbox.git(["log", "-2", "--format=%an %cn"])
    finally:
        sandbox.close()


# Round two, case 10
def test_git_log_format_fuller_names_the_human_on_both_the_author_and_commit_lines():
    """The verification the user asked for, on git's own rendered output.

    --format=fuller is the view that separates author from committer, so it is
    the one place a silently substituted committer would show itself. TMT must
    appear nowhere in that header and only in the trailer of the raw message.
    """
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox()
    try:
        sandbox.env["LC_ALL"] = "C"         # the labels asserted below are English
        sandbox.write("a.txt", "one\n")
        commit = sandbox.engine().commit("add a", paths=["a.txt"])
        text = sandbox.git(["log", "--format=fuller", "-1"])

        # "Author:" and "Commit:" only, never the AuthorDate:/CommitDate: lines.
        authors = re.findall(r"(?m)^Author:[ \t]+(.+?)[ \t]*$", text)
        committers = re.findall(r"(?m)^Commit:[ \t]+(.+?)[ \t]*$", text)
        assert authors == [HUMAN_SIGNATURE], f"Author line was {authors}, in:\n{text}"
        assert committers == [HUMAN_SIGNATURE], f"Commit line was {committers}, in:\n{text}"

        # The header block, before the indented message: TMT belongs in neither
        # half of it, however the trailer below reads.
        header = text.split("\n\n", 1)[0]
        assert TMT_NAME not in header, header
        assert TMT_EMAIL not in header, header
        assert header.count(REPO_NAME) == 2, header
        assert header.count(REPO_EMAIL) == 2, header
        assert "AuthorDate:" in text and "CommitDate:" in text, text
        assert commit["sha"] in text, text

        # The other half of the same property: the trailer is there, and it is
        # a trailer git parses rather than a line of the message that reads
        # like one.
        raw = sandbox.git(["log", "-1", "--format=%B"])
        assert TMT_TRAILER in raw, raw
        assert trailer_lines(sandbox) == [TMT_TRAILER], trailer_lines(sandbox)
        assert TMT_TRAILER in text, text
    finally:
        sandbox.close()


# Round two, case 11
def test_the_tracked_identity_file_holds_no_credential_shaped_content():
    """.tmt_git is public by design, so nothing secret may ever land in it."""
    assert TRACKED_IDENTITY_FILE.exists(), f"{TRACKED_IDENTITY_FILE} is missing"
    raw = TRACKED_IDENTITY_FILE.read_text(encoding="utf-8")
    lowered = raw.lower()
    for marker in STRONG_CREDENTIAL_MARKERS:
        assert marker not in lowered, f"{marker!r} appears in {TRACKED_IDENTITY_FILE.name}"

    for number, line in enumerate(raw.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert "=" in stripped, f"line {number} is neither comment nor key=value: {stripped!r}"
        key, _, value = stripped.partition("=")
        key = key.strip().lower()
        if key.startswith("tmt_git_"):
            key = key[len("tmt_git_"):]
        assert key in ALLOWED_IDENTITY_KEYS, (
            f"line {number} sets {key!r}; the identity file carries only "
            f"{sorted(ALLOWED_IDENTITY_KEYS)}")
        value = value.strip()
        # The word markers are tested against values only: the file's own
        # warning comment names tokens and passwords precisely to forbid them.
        for marker in VALUE_CREDENTIAL_MARKERS:
            assert marker not in value.lower(), (
                f"line {number} value looks like a credential ({marker!r})")
        assert len(value) <= 120, f"line {number} value is {len(value)} characters long"

    entries = {line.strip() for line in GITIGNORE_FILE.read_text(encoding="utf-8").splitlines()}
    assert ".tmt_git" not in entries, (
        ".gitignore still ignores .tmt_git, so a fresh clone would get no identity")
    assert ".tmt_git.local" in entries, (
        ".gitignore must ignore .tmt_git.local, the per-machine override")


# Round two, case 12
def test_git_diff_returns_the_changed_content_and_needs_no_push_authorization():
    if not ready("agent_git", "agent_config", "agent_actions"):
        return
    assert "git_diff" in agent_config.REQUIRED_KEYS, "git_diff is not a known action"
    assert agent_config.REQUIRED_KEYS["git_diff"] == [], agent_config.REQUIRED_KEYS["git_diff"]
    assert "git_diff" in agent_actions.ACTION_LABELS
    assert "git_diff" in agent_actions.READ_ONLY_ACTIONS, (
        "a read-only result must be fed back so the model can answer from it")
    assert "git_diff" not in agent_config.MUTATING_ACTIONS

    sandbox = Sandbox()
    try:
        sandbox.write("other.md", "one\n")
        sandbox.git(["add", "other.md"])
        sandbox.git(["commit", "-q", "-m", "seed"])
        sandbox.write("README.md", "initial\nchanged by tmt\n")
        sandbox.write("other.md", "one\nchanged elsewhere\n")

        # Reading a diff is not pushing, so it passes with no authority at all,
        # by any of the three routes a caller can arrive without one.
        for context in (None, {}, {"push_authorized": False}):
            result = str(agent_actions.execute_action({"action": "git_diff"}, context=context))
            assert not result.startswith("BLOCKED"), (context, result[:120])
            assert not result.lower().startswith("git error"), (context, result[:200])
            assert "changed by tmt" in result, (context, result[:400])
            assert "changed elsewhere" in result, (context, result[:400])
            assert "README.md" in result, (context, result[:400])
        # The default argument reaches the same place.
        assert "changed by tmt" in str(agent_actions.execute_action({"action": "git_diff"}))

        scoped = str(agent_actions.execute_action(
            {"action": "git_diff", "paths": ["README.md"]}, context=None))
        assert "changed by tmt" in scoped, scoped[:400]
        assert "changed elsewhere" not in scoped, scoped[:400]

        # One enormous diff must not be handed to the model whole.
        sandbox.write("README.md", "".join(f"line {number}\n" for number in range(20000)))
        huge = str(agent_actions.execute_action({"action": "git_diff"}, context=None))
        assert len(huge) < 100000, len(huge)
        assert "truncat" in huge.lower(), huge[-400:]

        follow_up = agent_actions.build_result_message("git_diff", "some diff")
        assert "respond" in follow_up, follow_up
    finally:
        sandbox.close()


def test_the_workspace_snapshot_never_carries_a_nested_git_directory():
    """A checkout inside the workspace must not put its .git in the prompt.

    It once did, and the model read the nested config, took that remote for the
    one it acts on, and reported a push as impossible against a repository it
    was never pointed at. The hook samples alone tripled the prompt.
    """
    if not ready("agent_prompt", "agent_config"):
        return
    # A workspace of its own: the root is now wherever TMT was started, and a
    # test must not plant files there.
    previous_root = agent_config.ROOT_DIR
    holder = Path(tempfile.mkdtemp(prefix="tmt_snapshot_probe_"))
    agent_config.set_workspace_root(holder)
    planted = holder / "nested_repo_probe" / ".git"
    try:
        planted.mkdir(parents=True, exist_ok=True)
        (planted / "config").write_text(
            '[remote "origin"]\n\turl = https://example.invalid/other.git\n',
            encoding="utf-8")
        (planted / "HEAD").write_text("ref: refs/heads/probe\n", encoding="utf-8")
        agent_prompt.invalidate_prompt()
        prompt = agent_prompt.get_system_prompt()
        assert "example.invalid/other.git" not in prompt
        assert "refs/heads/probe" not in prompt
        listed = re.findall(r"^--- (.+?) ---$", prompt, re.M)
        leaked = [n for n in listed if ".git" in n.replace("\\", "/").split("/")]
        assert not leaked, leaked
    finally:
        agent_config.ROOT_DIR = previous_root
        remove_tree(holder)
        agent_prompt.invalidate_prompt()


def test_the_prompt_states_which_repository_the_git_actions_use():
    """The workspace and the repository are different directories here, and the
    model cannot work that out from the files it is shown."""
    if not ready("agent_prompt", "agent_config"):
        return
    agent_prompt.invalidate_prompt()
    prompt = agent_prompt.get_system_prompt()
    assert "Git repository:" in prompt
    assert "not to the workspace" in prompt
    # And it must never be told to repair git itself or to ask for secrets.
    assert "Never tell the user to run git config" in prompt
    assert "never ask them for a token" in prompt


def test_git_status_names_the_paths_it_found():
    """Counts cannot be committed. An untracked file appears in no diff, so the
    name reported here is the only way the model can ever learn it."""
    if not ready("agent_actions", "agent_git", "agent_config"):
        return
    box = Sandbox()
    try:
        box.write("tracked.txt", "one\n")
        box.git(["add", "tracked.txt"])
        box.git(["commit", "-m", "seed"])
        box.write("tracked.txt", "changed\n")
        box.write("brand_new.txt", "untracked\n")
        report = str(agent_actions.execute_action({"action": "git_status"}))
        assert "tracked.txt" in report, report
        assert "brand_new.txt" in report, report
        assert "Untracked" in report and "Modified" in report, report
    finally:
        box.close()


def test_every_module_imports_and_is_clean_utf8_text():
    """A corrupted module degrades quietly rather than loudly.

    agent_actions catches an agent_git import failure and turns it into an
    action result, which is right for a missing dependency but means a broken
    source file looks like a git problem to everyone downstream. A stray NUL
    byte, which Python refuses to parse at all, once reached a module this way.
    """
    import importlib
    # PROJECT_DIR, not __file__.parent: this test file lives in
    # testing/integration/, whose *.py are all test modules. Globbing there
    # would hit the startswith("test_") continue on every one of them and the
    # test would pass having checked nothing -- the guard against a NUL byte in
    # a module, silently switched off. The count assertion below is the belt to
    # that braces: it can never go vacuous again without failing.
    root = PROJECT_DIR
    checked = 0
    for source in sorted(root.glob("*.py")):
        raw = source.read_bytes()
        assert b"\x00" not in raw, f"{source.name} contains a NUL byte"
        raw.decode("utf-8")                       # raises if not valid UTF-8
        if source.name.startswith("test_") or source.name == "run_tests.py":
            continue
        importlib.import_module(source.stem)      # raises if it cannot load
        checked += 1
    assert checked > 10, (
        f"only {checked} modules were checked in {root}: this test has gone "
        "vacuous and is no longer guarding anything")


# ---------------------------------------------------------------------------
# Round three: TMT as co-author rather than author.
#
# The commits are made by the engine and then read back with git's own trailer
# parser, because the question these answer is not "does the message contain
# this line" but "will anything downstream read this as a co-author", and only
# git can answer that.
# ---------------------------------------------------------------------------


# Round three, case 1
def test_tmt_appears_only_in_the_trailer_and_never_in_an_identity_field():
    """The human is not displaced. Asserted as one property, not two.

    Each identity field is read separately so a failure names the one that was
    taken over, and the trailer is checked on the same commit: a change that
    restored TMT as the author while still writing the trailer would pass
    either half of this on its own.
    """
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox()
    try:
        assert agent_git.CO_AUTHOR_TOKEN == "Co-authored-by", agent_git.CO_AUTHOR_TOKEN
        sandbox.write("a.txt", "one\n")
        result = sandbox.engine().commit("add a", paths=["a.txt"])

        for placeholder, field in (("%an", "author name"), ("%ae", "author email"),
                                   ("%cn", "committer name"), ("%ce", "committer email")):
            value = sandbox.git(["log", "-1", "--format=" + placeholder])
            assert TMT_NAME not in value, (field, value)
            assert TMT_EMAIL not in value, (field, value)
        assert identity_fields(sandbox) == [REPO_NAME, REPO_EMAIL, REPO_NAME, REPO_EMAIL]
        assert trailer_lines(sandbox) == [TMT_TRAILER], trailer_lines(sandbox)

        assert result["author"] == HUMAN_SIGNATURE, result
        assert result["committer"] == HUMAN_SIGNATURE, result
        assert result["co_author"] == TMT_SIGNATURE, result
        # The engine kept no way to set an author at all: the environment it
        # once built for that was removed rather than merely left unused.
        assert not hasattr(agent_git.TMTGit, "commit_environment")
    finally:
        sandbox.close()


# Round three, case 2
def test_the_trailer_is_one_git_parses_not_a_line_that_merely_looks_like_one():
    """A substring search cannot tell a trailer from prose. git can.

    The control commit carries the exact trailer text inside a paragraph that
    continues past it, which every substring search reads as a trailer and git
    reads as prose. GitHub and everything else downstream see what git sees.
    """
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox()
    try:
        lookalike = ("looks like a trailer\n\n" + TMT_TRAILER +
                     "\nand a following line of prose that ends the paragraph.")
        sandbox.write("control.txt", "one\n")
        sandbox.git(["add", "control.txt"])
        sandbox.git(["commit", "-q", "-m", lookalike])
        assert TMT_TRAILER in sandbox.git(["log", "-1", "--format=%B"])
        assert trailer_lines(sandbox) == [], (
            "git must not read a line buried in a paragraph as a trailer")

        sandbox.write("real.txt", "two\n")
        sandbox.engine().commit("a real one", paths=["real.txt"])
        assert trailer_lines(sandbox) == [TMT_TRAILER], trailer_lines(sandbox)
        valueonly = sandbox.git([
            "log", "-1",
            f"--format=%(trailers:key={agent_git.CO_AUTHOR_TOKEN},valueonly=true)"])
        assert valueonly.strip() == TMT_SIGNATURE, valueonly
        assert_human_authored_with_tmt_co_author(sandbox)
    finally:
        sandbox.close()


# Round three, case 3
def test_the_co_author_address_is_read_from_the_tmt_git_file():
    """The address reaches a real commit from the file, not only from the
    environment, because the file is what every clone actually gets.

    Both identity files are pointed at the sandbox, so neither the tracked
    .tmt_git in this repository nor a developer's own .tmt_git.local can decide
    the outcome.
    """
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox(name=None, email=None)
    previous = None
    try:
        tracked = sandbox.base / "identity.tmt_git"
        local = sandbox.base / "identity.tmt_git.local"
        tracked.write_text(
            "# an identity file of the shape every clone receives\n"
            "TMT_GIT_NAME=Filed Name\n"
            "TMT_GIT_EMAIL=filed@example.invalid\n", encoding="utf-8")
        previous = set_module_attrs(agent_config, {
            "GIT_IDENTITY_FILE": tracked,
            "GIT_IDENTITY_LOCAL_FILE": local,
        })

        identity = agent_git.TMTGitIdentity.resolve()
        assert identity.email == "filed@example.invalid", identity.email
        assert identity.name == "Filed Name", identity.name
        assert identity.email_source == agent_config.GIT_SOURCE_TRACKED_FILE, (
            identity.email_source)

        sandbox.write("a.txt", "one\n")
        sandbox.engine().commit("add a", paths=["a.txt"])
        assert trailer_lines(sandbox) == [
            "Co-authored-by: Filed Name <filed@example.invalid>"], trailer_lines(sandbox)
        assert identity_fields(sandbox) == [REPO_NAME, REPO_EMAIL, REPO_NAME, REPO_EMAIL]

        # The per-machine override sits above the tracked file, and the value
        # it wins with has to reach a commit rather than only the resolver.
        local.write_text("TMT_GIT_EMAIL=machine@example.invalid\n", encoding="utf-8")
        assert agent_git.TMTGitIdentity.resolve().email_source == \
            agent_config.GIT_SOURCE_LOCAL_FILE
        sandbox.write("b.txt", "two\n")
        sandbox.engine().commit("add b", paths=["b.txt"])
        assert trailer_lines(sandbox) == [
            "Co-authored-by: Filed Name <machine@example.invalid>"], trailer_lines(sandbox)
        assert identity_fields(sandbox) == [REPO_NAME, REPO_EMAIL, REPO_NAME, REPO_EMAIL]
    finally:
        restore_module_attrs(agent_config, previous)
        sandbox.close()


# Round three, case 4
def test_a_message_already_crediting_tmt_ends_with_exactly_one_trailer():
    """A retry, or a model that wrote the trailer itself, must not accumulate
    credit lines. The match is on the address alone, so the same co-author
    under a different display name or a different case is still the same one.
    """
    if not ready("agent_git", "agent_config"):
        return
    variants = (
        ("identical", TMT_TRAILER),
        ("a different case", "co-authored-by: TMT code <TMT-Code@Example.Invalid>"),
        ("a different display name", f"Co-authored-by: The TMT Agent <{TMT_EMAIL}>"),
    )
    for label, existing in variants:
        assert agent_git.has_co_author("add a\n\n" + existing, TMT_EMAIL), label
        sandbox = Sandbox()
        try:
            sandbox.write("a.txt", label + "\n")
            sandbox.engine().commit("add a\n\n" + existing, paths=["a.txt"])
            lines = trailer_lines(sandbox)
            assert len(lines) == 1, (label, lines)
            assert trailer_addresses(lines) == [TMT_EMAIL], (label, lines)
            body = sandbox.git(["log", "-1", "--format=%B"])
            assert body.lower().count("co-authored-by") == 1, (label, body)
            assert_human_authored_with_tmt_co_author(sandbox)
        finally:
            sandbox.close()


# Round three, case 5
def test_an_existing_different_co_author_is_kept_and_tmt_is_credited_beside_it():
    """Co-authorship is not exclusive. Someone already credited stays credited."""
    if not ready("agent_git", "agent_config"):
        return
    other = "Co-authored-by: Other Person <other@example.invalid>"
    sandbox = Sandbox()
    try:
        sandbox.write("a.txt", "one\n")
        sandbox.engine().commit("add a\n\nSome body\n\n" + other, paths=["a.txt"])
        lines = trailer_lines(sandbox)
        assert lines == [other, TMT_TRAILER], lines
        assert trailer_addresses(lines) == ["other@example.invalid", TMT_EMAIL], lines
        assert "Some body" in sandbox.git(["log", "-1", "--format=%B"])
        assert_human_authored_with_tmt_co_author(sandbox)
    finally:
        sandbox.close()


# Round three, case 6
def test_an_existing_signed_off_by_block_is_preserved_and_tmt_joins_it():
    """A trailer that is not a co-author is still a trailer.

    TMT has to join the block rather than start a paragraph after it: a blank
    line between the two would end the block, and git would then read neither
    line as a trailer.
    """
    if not ready("agent_git", "agent_config"):
        return
    signoff = f"Signed-off-by: {HUMAN_SIGNATURE}"
    sandbox = Sandbox()
    try:
        sandbox.write("a.txt", "one\n")
        sandbox.engine().commit("add a\n\nbody text here\n\n" + signoff, paths=["a.txt"])
        body = sandbox.git(["log", "-1", "--format=%B"])
        assert signoff + "\n" + TMT_TRAILER in body, body
        assert signoff + "\n\n" + TMT_TRAILER not in body, body
        assert "body text here" in body, body
        assert trailer_lines(sandbox, key="Signed-off-by") == [signoff]
        assert trailer_lines(sandbox) == [TMT_TRAILER]
        assert_human_authored_with_tmt_co_author(sandbox)
    finally:
        sandbox.close()


# Round three, case 7
def test_a_commit_the_user_makes_themselves_never_gains_a_trailer():
    """TMT adds the trailer to commits it creates and to nothing else.

    A commit the user makes with plain git is left exactly as they wrote it,
    before and after TMT commits alongside it.
    """
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox()
    try:
        sandbox.write("human.txt", "the human's own work\n")
        sandbox.git(["add", "human.txt"])
        sandbox.git(["commit", "-q", "-m", "a commit made with plain git"])
        manual = sandbox.head()
        assert trailer_lines(sandbox) == [], trailer_lines(sandbox)
        assert agent_git.co_authors(sandbox.git(["log", "-1", "--format=%B"])) == []

        sandbox.write("tmt.txt", "work tmt did\n")
        sandbox.engine().commit("a commit made by tmt", paths=["tmt.txt"])
        assert trailer_lines(sandbox) == [TMT_TRAILER], trailer_lines(sandbox)

        # The user's commit, and the one that predates TMT here, are untouched.
        assert trailer_lines(sandbox, ref=manual) == [], manual
        assert sandbox.git(["log", "-1", "--format=%B", manual]) == \
            "a commit made with plain git"
        assert trailer_lines(sandbox, ref="HEAD~2") == []
    finally:
        sandbox.close()


# Round three, case 8
def test_every_message_shape_keeps_its_text_and_gains_one_parsed_trailer():
    """The shapes that decide where git puts a trailer: nothing to attach to,
    a body to leave alone, a colon in prose that is not a token, and several
    body lines."""
    if not ready("agent_git", "agent_config"):
        return
    shapes = (
        ("subject only", "add a"),
        ("subject and body", "add a\n\nSome explanation of the change."),
        ("a colon in prose", "fix the parser\n\nThe bug was here: the lexer dropped it."),
        ("a multi-line body", "add a\n\nline one\nline two\nline three"),
    )
    for label, message in shapes:
        sandbox = Sandbox()
        try:
            sandbox.write("a.txt", label + "\n")
            sandbox.engine().commit(message, paths=["a.txt"])
            body = sandbox.git(["log", "-1", "--format=%B"])
            for line in message.splitlines():
                assert line in body, (label, line, body)
            assert trailer_lines(sandbox) == [TMT_TRAILER], (label, body)
            # Every trailer git finds, not just the co-author ones: a body line
            # read as a trailer would show up here and nowhere else.
            everything = [line.strip() for line in
                          sandbox.git(["log", "-1", "--format=%(trailers)"]).splitlines()
                          if line.strip()]
            assert everything == [TMT_TRAILER], (label, everything)
            if label == "a colon in prose":
                # Prose keeps the blank line that stops it being a trailer block.
                assert "the lexer dropped it.\n\n" + TMT_TRAILER in body, body
            assert_human_authored_with_tmt_co_author(sandbox)
        finally:
            sandbox.close()


# Round three, case 9
def test_a_tmt_commit_adds_to_history_without_rewriting_any_of_it():
    """Every existing commit stays byte-identical.

    Adding the trailer by amending would be a rewrite of a commit other clones
    may already hold, so the engine appends and never amends -- including when
    it finds the trailer missing after the fact.
    """
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox()
    try:
        for index in range(3):
            name = f"history_{index}.txt"
            sandbox.write(name, f"{index}\n")
            sandbox.git(["add", name])
            sandbox.git(["commit", "-q", "-m", f"human commit {index}"])
        before = sandbox.git(["log", "--format=%H"]).splitlines()
        assert len(before) >= 4, before
        objects = {sha: sandbox.git(["cat-file", "commit", sha]) for sha in before}

        sandbox.write("new.txt", "new\n")
        commit = sandbox.engine().commit("a tmt commit", paths=["new.txt"])

        assert sandbox.git(["log", "--format=%H"]).splitlines() == [commit["sha"]] + before
        for sha in before:
            assert sandbox.git(["cat-file", "commit", sha]) == objects[sha], sha
            assert trailer_lines(sandbox, ref=sha) == [], sha
        assert trailer_lines(sandbox) == [TMT_TRAILER]
        assert_human_authored_with_tmt_co_author(sandbox)
    finally:
        sandbox.close()


# Round three, case 10
def test_a_failed_push_leaves_the_commit_message_and_trailer_untouched():
    """A push that fails changes nothing: not the sha, not the message, and not
    the trailer that carries the credit."""
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox()
    try:
        sandbox.write("a.txt", "one\n")
        engine = sandbox.engine()
        commit = engine.commit("add a\n\nwith a body", paths=["a.txt"])
        body_before = sandbox.git(["log", "-1", "--format=%B"])
        # A remote that is not there, so the failure comes from git having
        # tried rather than from TMT refusing before it started.
        sandbox.git(["remote", "set-url", "origin",
                     sandbox.url(sandbox.base / "absent.git")])

        raised = None
        try:
            engine.push()
        except agent_git.GitError as error:
            raised = error
        assert raised is not None, "a push to a missing remote must raise"
        assert "local repository" in str(raised), str(raised)

        assert sandbox.head() == commit["sha"]
        assert sandbox.git(["log", "-1", "--format=%B"]) == body_before
        assert trailer_lines(sandbox) == [TMT_TRAILER], trailer_lines(sandbox)
        assert_human_authored_with_tmt_co_author(sandbox)
    finally:
        sandbox.close()


# Round three, case 11
def test_the_manual_trailer_fallback_matches_git_interpret_trailers():
    """The fallback is only reached when interpret-trailers cannot run, so it
    is compared against the real thing rather than assumed equivalent.

    The one deliberate divergence is an exact duplicate, which git drops and
    the fallback repeats. commit() never asks either of them in that case:
    has_co_author short-circuits it, which is what keeps the count at one.
    """
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox()
    try:
        shapes = (
            "add a",
            "add a\n\nSome explanation of the change.",
            "fix the parser\n\nThe bug was here: the lexer dropped it.",
            "add a\n\nline one\nline two\nline three",
            "add a\n\nbody\n\nSigned-off-by: " + HUMAN_SIGNATURE,
            "add a\n\nbody\n\nCo-authored-by: Other Person <other@example.invalid>",
            "add a\n\nNote: this line is trailer-shaped.",
        )
        for message in shapes:
            by_git = agent_git.add_co_author(message, TMT_TRAILER, sandbox.repo)
            manual = agent_git._append_trailer_manually(message, TMT_TRAILER)
            assert by_git == manual, (message, by_git, manual)
            assert by_git.endswith(TMT_TRAILER + "\n"), (message, by_git)

        duplicate = "add a\n\n" + TMT_TRAILER
        assert agent_git.add_co_author(
            duplicate, TMT_TRAILER, sandbox.repo).count(TMT_TRAILER) == 1
        assert agent_git.has_co_author(duplicate, TMT_EMAIL), (
            "an already-credited message must never reach either trailer path")
    finally:
        sandbox.close()
