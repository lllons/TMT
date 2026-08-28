"""Tests for TMT's git engine, exercised against real temporary repositories.

Nothing here touches the developer's repository or git configuration: every git
process runs with GIT_CONFIG_NOSYSTEM and a HOME pointed at a throwaway
directory, and the engine's identity is an example.invalid address. Pushes go to
a real bare repository created per test, so the push paths are genuinely
exercised rather than simulated.
"""

import ast
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

TMT_NAME = "TMT code"
TMT_EMAIL = "tmt-code@example.invalid"
REPO_EMAIL = "repo-human@example.invalid"
GLOBAL_EMAIL = "global-human@example.invalid"

# Captured before any test mutates os.environ, so the developer's real global
# config can still be read back for comparison.
REAL_ENV = dict(os.environ)
GIT = shutil.which("git")
PROJECT_DIR = Path(__file__).resolve().parent

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


# Case 1
def test_commit_author_and_committer_are_both_the_tmt_identity():
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox()
    try:
        assert agent_git.TMTGitIdentity.DEFAULT_NAME == TMT_NAME
        sandbox.write("a.txt", "one\n")
        commit = sandbox.engine().commit("add a", paths=["a.txt"])
        fields = sandbox.git(["log", "-1", "--format=%an|%ae|%cn|%ce"]).split("|")
        assert fields == [TMT_NAME, TMT_EMAIL, TMT_NAME, TMT_EMAIL], fields
        assert commit["author"] == f"{TMT_NAME} <{TMT_EMAIL}>", commit
        assert commit["committer"] == f"{TMT_NAME} <{TMT_EMAIL}>", commit
        assert commit["branch"] == "main"
        assert commit["sha"] == sandbox.head()
        assert commit["sha"].startswith(commit["short"])
        assert "a.txt" in commit["files"]
    finally:
        sandbox.close()


# Case 2
def test_the_tmt_identity_wins_over_the_repo_local_user_email():
    if not ready("agent_git", "agent_config"):
        return
    sandbox = Sandbox()
    try:
        assert sandbox.git(["config", "user.email"]) == REPO_EMAIL
        assert REPO_EMAIL != TMT_EMAIL
        human = sandbox.git(["log", "-1", "--format=%ae"])
        assert human == REPO_EMAIL          # the repo really would commit as the human

        sandbox.write("a.txt", "one\n")
        sandbox.engine().commit("add a", paths=["a.txt"])
        assert sandbox.git(["log", "-1", "--format=%ae"]) == TMT_EMAIL
        assert sandbox.git(["log", "-1", "--format=%ce"]) == TMT_EMAIL
        # The environment override must not have rewritten the repo's own setting.
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
def test_a_pushed_commit_reaches_the_bare_remote_under_the_tmt_identity():
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
        who = sandbox.bare_git(["log", "-1", "--format=%an|%ae", "refs/heads/main"]).split("|")
        assert who == [TMT_NAME, TMT_EMAIL], who
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
    git_actions = ("git_status", "git_identity", "git_commit", "git_push")
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
