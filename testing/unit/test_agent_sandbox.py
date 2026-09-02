"""The one place TMT creates a process, tested against the operating system.

`agent_sandbox` is the module a reader will actually rely on, so almost nothing
here asks it what it thinks it did. A credential is planted in this process's
own environment and a real child is made to print its whole environment back; a
real grandchild is started and the OS is asked -- `tasklist` on Windows,
`os.kill(pid, 0)` on POSIX -- whether it is still there afterwards; the program
a child reports having been launched from is compared against the path the
curated PATH resolved. Where a property can only be checked against the
module's own bookkeeping, that is said so in the test.

Three disciplines every test here keeps, because the suite has no per-test
timeout and no isolation:

  * nothing is left running. Anything started is killed in a `finally`, and
    every wait is bounded.
  * nothing is written into the developer's workspace. `Box` redirects
    `agent_config.ROOT_DIR` and `agent_config.INSTALL_DIR` -- the second
    because `sandbox_home` lives under the install -- and restores both.
  * nothing sleeps for longer than it has to. The one test that waits out a
    real timeout uses three seconds, which is twenty times the measured
    0.15s a spawned grandchild needs to register itself.

Whole-module cost on the machine this was written on: about eight seconds,
almost all of it real process starts.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import agent_config
import agent_memory
import agent_sandbox as S

MODULE_SOURCE = Path(S.__file__).read_text(encoding="utf-8")


# --- the workspace, and putting it back -------------------------------------

class Box:
    """A throwaway workspace and a throwaway install directory.

    Both are redirected: the workspace because a test must never write into
    the developer's own tree, and the install because `sandbox_home` -- and so
    every HOME, TEMP and package cache `build_env` hands a child -- is derived
    from `agent_config.INSTALL_DIR` at call time.
    """

    def __init__(self, **files):
        self.previous_root = agent_config.ROOT_DIR
        self.previous_install = agent_config.INSTALL_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_sbox_")).resolve()
        self.install = Path(tempfile.mkdtemp(prefix="tmt_sboxi_")).resolve()
        agent_config.ROOT_DIR = self.path
        agent_config.INSTALL_DIR = self.install
        for name, body in files.items():
            self.write(name, body)

    def write(self, name, body):
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return target

    def read(self, name):
        return (self.path / name).read_text(encoding="utf-8")

    def close(self):
        agent_config.ROOT_DIR = self.previous_root
        agent_config.INSTALL_DIR = self.previous_install
        shutil.rmtree(str(self.path), ignore_errors=True)
        shutil.rmtree(str(self.install), ignore_errors=True)


def python_name():
    """The name a bare `python` goes by on this host, or "" if none does.

    Asked of `resolve_program` rather than of `sys.executable`, because these
    tests are about what the curated PATH can reach by NAME.
    """
    for name in ("python", "python3", "py"):
        if S.resolve_program(name):
            return name
    return ""


PYTHON = python_name()


def can_run_processes():
    """Whether this host can be asked the questions these tests ask."""
    if not PYTHON:
        return False
    if os.name == "nt":
        return bool(S.resolve_program("tasklist"))
    return hasattr(os, "kill")


def alive(pid):
    """Whether the OS still has this pid. Asked of the OS, never of Python."""
    if os.name == "nt":
        found = subprocess.run(
            ["tasklist", "/FI", "PID eq %d" % int(pid), "/NH"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True)
        return str(pid) in (found.stdout or "")
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except OSError:
        # EPERM: it exists and belongs to somebody else.
        return True
    return True


def wait_until(predicate, seconds=10.0, step=0.05):
    """Bounded polling. Nothing in this file may wait without a ceiling."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


def reap(*pids):
    """Last-resort cleanup, so a failed assertion cannot leave a process."""
    for pid in pids:
        if not pid:
            continue
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=10)
            else:
                os.kill(int(pid), 9)
        except Exception:
            pass


class Redirect:
    """What `agent_shell.Redirect` looks like from here.

    Duck-typed on purpose: `agent_sandbox._stage_parts` reads `argv`,
    `redirects`, `kind` and `target` off whatever it is given, and importing
    the parser would tie this module's tests to another module's health.
    """

    __slots__ = ("kind", "target", "fd")

    def __init__(self, kind, target="", fd=None):
        self.kind = kind
        self.target = target
        self.fd = fd


class Command:
    __slots__ = ("argv", "redirects")

    def __init__(self, argv, redirects=()):
        self.argv = list(argv)
        self.redirects = tuple(redirects)


# --- the scripts the children run -------------------------------------------

DUMP_ENV = (
    "import os\n"
    "for name in sorted(os.environ):\n"
    "    print('%s=%s' % (name, os.environ[name]))\n"
)

READ_STDIN = (
    "import sys\n"
    "print('READ[%d]' % len(sys.stdin.read()))\n"
)

WHOAMI = "import sys\nprint(sys.executable)\n"

OK = "print('ok')\n"

EXIT_THREE = "import sys\nsys.exit(3)\n"

FLOOD_LINES = 2000
FLOOD_WIDTH = 47                      # "%06d " plus forty x's
FLOOD = (
    "for index in range(%d):\n"
    "    print('%%06d ' %% index + 'x' * 40)\n" % FLOOD_LINES
)

EMIT = "print('alpha')\nprint('beta')\n"

UPPER = (
    "import sys\n"
    "for line in sys.stdin:\n"
    "    print(line.strip().upper())\n"
)

SHOUT = (
    "import sys\n"
    "print('to stdout')\n"
    "sys.stderr.write('to stderr\\n')\n"
)

COUNT_WORDS = (
    "import sys\n"
    "print('WORDS=%d' % len(sys.stdin.read().split()))\n"
)

SLEEPER = "import time\ntime.sleep(120)\n"

# The grandchild is started with its own handles pointing at nothing, so it
# does not hold the pipeline's pipe open: what is being tested is whether the
# process is killed, not how long a pump waits for EOF.
SPAWN_AND_WAIT = (
    "import subprocess, sys, time\n"
    "from pathlib import Path\n"
    "child = subprocess.Popen([sys.executable, 'sleeper.py'],\n"
    "                         stdin=subprocess.DEVNULL,\n"
    "                         stdout=subprocess.DEVNULL,\n"
    "                         stderr=subprocess.DEVNULL)\n"
    "Path('grandchild.pid').write_text(str(child.pid))\n"
    "time.sleep(120)\n"
)

SPAWN_AND_EXIT = (
    "import subprocess, sys\n"
    "from pathlib import Path\n"
    "child = subprocess.Popen([sys.executable, 'sleeper.py'],\n"
    "                         stdin=subprocess.DEVNULL,\n"
    "                         stdout=subprocess.DEVNULL,\n"
    "                         stderr=subprocess.DEVNULL)\n"
    "Path('orphan.pid').write_text(str(child.pid))\n"
)


# --- credentials never reach a child ----------------------------------------

PLANTED = {
    "TMT_TEST_SANDBOX_API_KEY": "sk-planted-key-11111",
    "TMT_TEST_SANDBOX_TOKEN": "planted-token-22222",
    "TMT_TEST_SANDBOX_PASSWORD": "planted-password-33333",
    "AWS_SECRET_ACCESS_KEY": "planted-cloud-secret-44444",
}


def test_a_credential_in_this_process_environment_never_reaches_a_child():
    """The property proved by a real process, not by reading `build_env`.

    Four credential shapes are planted in TMT's own environment -- an API key,
    a token, a password and a cloud secret -- and a child is made to print its
    whole environment back. Neither the names nor the values may appear. The
    allow-listed TERM is checked in the same run, so the test cannot pass by
    the child's environment having been empty.
    """
    if not can_run_processes():
        return
    box = Box(**{"dumpenv.py": DUMP_ENV})
    saved = {name: os.environ.get(name) for name in PLANTED}
    saved["TERM"] = os.environ.get("TERM")
    try:
        os.environ.update(PLANTED)
        os.environ["TERM"] = "tmt-sandbox-probe"
        outcome = S.run_argv([PYTHON, "dumpenv.py"], cwd=box.path, timeout=30)
        assert outcome.exit_code == 0, (outcome.exit_code, outcome.error,
                                        outcome.output[-400:])
        printed = outcome.output
        for name, value in PLANTED.items():
            assert name not in printed, "%s reached the child" % name
            assert value not in printed, "%s's value reached the child" % name
        assert "TERM=tmt-sandbox-probe" in printed, printed[:400]

        # Not a filter that happened to catch four names: nothing the child was
        # given is credential-shaped at all, asked in agent_memory's vocabulary.
        seen = [line.split("=", 1)[0] for line in printed.splitlines()
                if "=" in line]
        assert len(seen) > 5, seen
        leaked = [name for name in seen if S.name_is_secretish(name)]
        assert leaked == [], leaked
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        box.close()


def test_the_environment_is_built_from_empty_rather_than_filtered():
    """Every name in a built environment is one this module chose to set.

    The difference matters: a filtered copy admits every variable added to the
    machine after the filter was written, and a built one admits none.
    """
    box = Box()
    saved = os.environ.get("TMT_TEST_SANDBOX_API_KEY")
    try:
        os.environ["TMT_TEST_SANDBOX_API_KEY"] = "sk-planted-key-11111"
        env = S.build_env(box.path, S.NETWORK_OFFLINE)
        assert "TMT_TEST_SANDBOX_API_KEY" not in env
        known = set(S.ENV_ALLOWLIST) | {"PATH", "PYTHONIOENCODING"}
        for _subdir, names in S._HOME_SUBDIRS:
            known.update(names)
        known.update(name for name, _value in S._OFFLINE_ENV)
        assert set(env) <= known, sorted(set(env) - known)
        assert "PYTHONPATH" not in env, "a child python must not inherit TMT's"
    finally:
        if saved is None:
            os.environ.pop("TMT_TEST_SANDBOX_API_KEY", None)
        else:
            os.environ["TMT_TEST_SANDBOX_API_KEY"] = saved
        box.close()


def test_a_name_that_looks_like_a_credential_is_recognised_in_agent_memorys_words():
    """The vocabulary is borrowed rather than copied, and this pins the seam.

    A second list of what a secret looks like is a second thing to keep
    current, and the failure mode of the copy falling behind is a real key
    reaching a child.
    """
    for name in ("MY_API_KEY", "SERVICE_TOKEN", "DB_PASSWORD",
                 "SOME_CREDENTIAL", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN",
                 "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        assert S.name_is_secretish(name), name
    for name in ("PATH", "TERM", "LANG", "SYSTEMROOT", "NUMBER_OF_PROCESSORS"):
        assert not S.name_is_secretish(name), name
    assert not S.name_is_secretish("")
    # The borrowing itself: agent_memory redacts the same shape.
    cleaned, _count = agent_memory.scrub("MY_API_KEY=abcdef")
    assert agent_memory.REDACTED in cleaned


def test_every_allow_listed_name_is_still_asked_whether_it_is_a_credential():
    """So extending ENV_ALLOWLIST carelessly later cannot quietly add one."""
    for name in S.ENV_ALLOWLIST:
        assert not S.name_is_secretish(name), name


# --- the sandbox home and the network mode ----------------------------------

def test_home_temp_and_every_package_cache_point_at_the_sandbox_home():
    """A child cannot read the user's npm credentials or cargo token by
    looking where those live, and cannot leave anything in the user's home."""
    box = Box()
    try:
        home = S.sandbox_home(box.path)
        assert str(home).startswith(str(box.install)), home
        env = S.build_env(box.path, S.NETWORK_OFFLINE)
        for name in ("HOME", "USERPROFILE", "TEMP", "TMP", "TMPDIR",
                     "XDG_CACHE_HOME", "npm_config_cache", "CARGO_HOME",
                     "GOPATH", "PIP_CACHE_DIR"):
            assert name in env, name
            assert str(env[name]).startswith(str(home)), (name, env[name])
        assert env["HOME"] == env["USERPROFILE"]
        assert env["TEMP"] == env["TMP"] == env["TMPDIR"]
        # Two workspaces never share a home: the key is a hash of the path.
        other = S.sandbox_home(box.install)
        assert str(other) != str(home)
    finally:
        box.close()


def test_the_offline_flags_are_set_offline_and_absent_when_the_network_is_open():
    box = Box()
    try:
        offline = S.build_env(box.path, S.NETWORK_OFFLINE)
        for name, value in S._OFFLINE_ENV:
            assert offline.get(name) == value, name
        assert offline["PIP_NO_INDEX"] == "1"
        assert offline["NPM_CONFIG_OFFLINE"] == "true"
        assert offline["CARGO_NET_OFFLINE"] == "true"
        assert offline["GOPROXY"] == "off"
        assert offline["http_proxy"] == "http://127.0.0.1:9"
        assert offline["no_proxy"] == ""

        opened = S.build_env(box.path, S.NETWORK_OPEN)
        for name, _value in S._OFFLINE_ENV:
            assert name not in opened, name

        # The mode is recovered from what was actually set, not carried in a
        # variable of TMT's own that the child would see.
        assert S.network_of(offline) == S.NETWORK_OFFLINE
        assert S.network_of(opened) == S.NETWORK_OPEN
    finally:
        box.close()


def test_a_default_run_gets_the_curated_path_and_the_utf8_correction():
    box = Box()
    try:
        env = S.build_env(box.path, S.NETWORK_OFFLINE)
        assert env["PATH"] == S.curated_path()
        assert env["PYTHONIOENCODING"] == "utf-8"
        assert env["PATH"] != os.environ.get("PATH", ""), (
            "the host's full PATH must never be handed to a child")
    finally:
        box.close()


# --- the curated PATH is load-bearing, not decoration ------------------------

def test_the_program_that_actually_ran_is_the_one_the_curated_path_resolved():
    """The Windows trap, pinned against the OS rather than against a string.

    `CreateProcess` searches the CALLER's PATH, not the environment block
    handed to the child, so a test that only inspected `env["PATH"]` would
    prove nothing at all. What is asserted here is what the child says about
    ITSELF: the interpreter it is running as is the file `resolve_program`
    named, which can only be true if TMT resolved the program and launched an
    absolute path.
    """
    if not can_run_processes():
        return
    box = Box(**{"whoami.py": WHOAMI})
    try:
        resolved = S.resolve_program(PYTHON)
        assert os.path.isabs(resolved), resolved
        outcome = S.run_argv([PYTHON, "whoami.py"], cwd=box.path, timeout=30)
        assert outcome.exit_code == 0, (outcome.error, outcome.output[-400:])
        reported = outcome.output.strip().splitlines()[-1]
        assert os.path.normcase(reported) == os.path.normcase(resolved), (
            reported, resolved)
    finally:
        box.close()


def test_a_program_absent_from_the_curated_path_is_not_reached_through_the_hosts():
    """A PATH with nothing in it refuses `python`, on a machine that has one.

    This is the same property from the other side: if the curated PATH were
    decoration, the host's PATH would find the interpreter anyway and the
    command would run.
    """
    if not can_run_processes():
        return
    box = Box(**{"whoami.py": WHOAMI})
    try:
        empty = box.path / "nothing_here"
        empty.mkdir()
        assert S.resolve_program(PYTHON), "the host really does have python"
        env = S.build_env(box.path, S.NETWORK_OFFLINE)
        env["PATH"] = str(empty)
        outcome = S.run_argv([PYTHON, "whoami.py"], cwd=box.path, env=env,
                             timeout=30)
        assert outcome.exit_code is None, outcome.exit_code
        assert not outcome.ran
        assert PYTHON in outcome.error and "sandbox PATH" in outcome.error, \
            outcome.error
        assert S.resolve_program(PYTHON, str(empty)) == ""
    finally:
        box.close()


def test_the_curated_path_holds_only_directories_development_tools_live_in():
    path = S.curated_path()
    assert path, "this host has no development tools at all?"
    directories = [part for part in path.split(os.pathsep) if part]
    assert directories
    for directory in directories:
        assert os.path.isabs(directory), directory
    # No duplicate, however the host spells its own case.
    keys = [d.lower() if os.name == "nt" else d for d in directories]
    assert len(keys) == len(set(keys)), directories
    assert S.curated_path() is path or S.curated_path() == path, "cached"


# --- an exit code is evidence; its absence is the absence of evidence --------

def test_a_program_that_is_not_installed_gives_no_exit_code_rather_than_one():
    box = Box()
    try:
        outcome = S.run_argv(["tmt_no_such_program_9x8y7z"], cwd=box.path,
                             timeout=20)
        assert outcome.exit_code is None
        assert outcome.ran is False
        assert not outcome.ok
        assert "tmt_no_such_program_9x8y7z" in outcome.error
        # And the same refusal from the primitive, as an exception rather than
        # an outcome, so a caller cannot mistake it for a failing command.
        raised = None
        try:
            S.launch(["tmt_no_such_program_9x8y7z"], cwd=box.path, timeout=5)
        except S.SandboxError as error:
            raised = error
        assert raised is not None, "launch must refuse what it cannot start"
    finally:
        box.close()


def test_a_command_that_runs_and_fails_reports_its_real_exit_code():
    if not can_run_processes():
        return
    box = Box(**{"fail.py": EXIT_THREE})
    try:
        outcome = S.run_argv([PYTHON, "fail.py"], cwd=box.path, timeout=30)
        assert outcome.exit_code == 3, (outcome.exit_code, outcome.error)
        assert outcome.ran is True
        assert outcome.ok is False
        assert outcome.killed is False
        assert outcome.error == ""
    finally:
        box.close()


def test_an_empty_pipeline_is_refused_and_reports_no_exit_code():
    outcome = S.run_pipeline([])
    assert outcome.exit_code is None
    assert not outcome.ran
    assert outcome.error
    assert outcome.level in (S.LEVEL_OS, S.LEVEL_POLICY)


def test_nothing_reads_the_output_to_decide_whether_a_command_succeeded():
    """A program that prints the word FAILED and exits 0 succeeded.

    The rule the verification engine already lives by, pinned here because
    this is the module that would be the tempting place to break it.
    """
    if not can_run_processes():
        return
    box = Box(**{"noisy.py": "print('FAILED: 3 checks failed')\n"})
    try:
        outcome = S.run_argv([PYTHON, "noisy.py"], cwd=box.path, timeout=30)
        assert outcome.exit_code == 0
        assert outcome.ok is True
        assert "FAILED" in outcome.output
    finally:
        box.close()


# --- output caps ------------------------------------------------------------

def test_more_output_than_the_cap_keeps_the_tail_and_reports_the_real_total():
    if not can_run_processes():
        return
    box = Box(**{"flood.py": FLOOD})
    try:
        outcome = S.run_argv([PYTHON, "flood.py"], cwd=box.path, timeout=60)
        assert outcome.exit_code == 0, (outcome.error, outcome.output[-300:])
        expected = FLOOD_LINES * (FLOOD_WIDTH + 1)
        assert expected > S.MAX_OUTPUT, expected
        assert outcome.truncated is True
        assert len(outcome.output) == S.MAX_OUTPUT, len(outcome.output)
        # The real figure, not the kept one: a truncation notice that cannot
        # say how much was dropped is a notice nobody can act on.
        assert outcome.total_output == expected, outcome.total_output
        # Tail-biased: the END of a failing run is its useful part.
        assert "%06d" % (FLOOD_LINES - 1) in outcome.output
        assert "%06d" % 0 not in outcome.output
    finally:
        box.close()


def test_output_under_the_cap_is_kept_whole_and_is_not_marked_truncated():
    if not can_run_processes():
        return
    box = Box(**{"emit.py": EMIT})
    try:
        outcome = S.run_argv([PYTHON, "emit.py"], cwd=box.path, timeout=30)
        assert outcome.output == "alpha\nbeta\n", repr(outcome.output)
        assert outcome.truncated is False
        assert outcome.total_output == len("alpha\nbeta\n")
    finally:
        box.close()


def test_the_kept_output_costs_a_bounded_amount_of_memory():
    """Bookkeeping rather than the OS, and the only way to ask it: what is
    being pinned is that a runaway producer costs a fixed buffer rather than
    the machine's memory, which no observable output can show."""
    tail = S._Tail(limit=100)
    for _ in range(5000):
        tail.feed("y" * 100)
    assert tail.total == 500000
    assert tail.truncated
    assert len(tail.text) == 100
    assert len(tail._text) <= 200, len(tail._text)
    quiet = S._Tail(limit=100)
    quiet.feed("short")
    assert quiet.text == "short"
    assert not quiet.truncated


# --- stdin ------------------------------------------------------------------

def test_stdin_is_closed_so_an_interactive_tool_gets_eof_instead_of_hanging():
    """The reason this file cannot hang the suite, proved by a real read."""
    if not can_run_processes():
        return
    box = Box(**{"readstdin.py": READ_STDIN})
    try:
        outcome = S.run_argv([PYTHON, "readstdin.py"], cwd=box.path, timeout=30)
        assert outcome.exit_code == 0, (outcome.error, outcome.output)
        assert "READ[0]" in outcome.output, outcome.output
        assert outcome.killed is False, "it must reach EOF, not the timeout"
    finally:
        box.close()


# --- limits degrade rather than block ---------------------------------------

def test_a_limit_this_platform_cannot_apply_is_recorded_and_the_command_runs():
    if not can_run_processes():
        return
    box = Box(**{"fail.py": EXIT_THREE})
    try:
        ordinary = S.run_argv([PYTHON, "fail.py"], cwd=box.path, timeout=30)
        assert ordinary.exit_code == 3
        assert ordinary.degraded == (), ordinary.degraded

        if os.name == "nt":
            # A maximum file size is not something a job object can express.
            asked = S.Limits(file_bytes=1024, user_processes=4)
        else:
            # A job object's ActiveProcessLimit has no POSIX equivalent; see
            # the note in the report about what this platform does with it.
            asked = S.Limits(memory_bytes=8 * 1024 * 1024 * 1024 * 1024)
        outcome = S.run_argv([PYTHON, "fail.py"], cwd=box.path, timeout=30,
                             limits=asked)
        assert outcome.exit_code == 3, (outcome.exit_code, outcome.error)
        if os.name == "nt":
            assert outcome.degraded, "what was not applied must be reported"
            joined = " ".join(outcome.degraded)
            assert "file size" in joined, joined
    finally:
        box.close()


def test_default_limits_ask_only_for_what_the_running_platform_can_express():
    """Which is what keeps `degraded` empty on an ordinary run, and so keeps
    anything in it real news."""
    limits = S.default_limits()
    if os.name == "nt":
        assert limits.processes, limits
        assert limits.file_bytes is None, limits
    else:
        assert limits.file_bytes, limits
        assert limits.processes is None, limits
    # Off by default on every platform: a per-user process limit bounds the
    # desktop rather than the command, which would block rather than degrade.
    assert limits.user_processes is None
    assert limits.cpu_seconds is None
    assert S.NO_LIMITS.memory_bytes is None


def test_a_timeout_is_clamped_to_the_modules_own_ceiling():
    if not can_run_processes():
        return
    box = Box(**{"ok.py": OK})
    handles = []
    try:
        for asked, expected in ((99999, S.MAX_TIMEOUT),
                                (None, S.DEFAULT_TIMEOUT)):
            # Its output goes nowhere: `launch` inherits the caller's streams
            # when it is given none, and a test that printed into the suite's
            # own output would be printing past whatever is on screen.
            handle = S.launch([PYTHON, "ok.py"], cwd=box.path, timeout=asked,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
            handles.append(handle)
            assert handle.timeout == expected, (asked, handle.timeout)
            handle.wait(timeout=30)
            handle.release()
    finally:
        for handle in handles:
            try:
                if handle.poll() is None:
                    handle.kill_tree()
            except Exception:
                pass
        box.close()


# --- the process tree -------------------------------------------------------

def test_a_timeout_kills_the_grandchild_as_well_as_the_child():
    """Asked of the OS: `tasklist` on Windows, `os.kill(pid, 0)` on POSIX.

    The child records its grandchild's pid in a file before it settles down to
    sleep, so the test knows a pid the module never saw. Three seconds of
    timeout against a measured 0.15s to spawn and register.
    """
    if not can_run_processes():
        return
    box = Box(**{"sleeper.py": SLEEPER, "spawner.py": SPAWN_AND_WAIT})
    grandchild = 0
    try:
        started = time.monotonic()
        outcome = S.run_pipeline([[PYTHON, "spawner.py"]], cwd=box.path,
                                 timeout=3.0)
        elapsed = time.monotonic() - started
        assert outcome.killed is True, outcome.error
        assert outcome.exit_code is None, "a killed command reports no code"
        assert not outcome.ran
        assert "process tree" in outcome.error, outcome.error
        assert elapsed < 30, elapsed

        recorded = box.path / "grandchild.pid"
        assert recorded.exists(), "the child never got as far as spawning"
        grandchild = int(recorded.read_text().strip())
        assert wait_until(lambda: not alive(grandchild), seconds=10), (
            "the grandchild outlived the timeout that killed its parent")
    finally:
        reap(grandchild)
        box.close()


def test_a_grandchild_orphaned_by_a_child_that_exits_at_once_is_still_reached():
    """The harder case: there is no tree left to walk.

    The child spawns a grandchild and returns immediately, so by the time
    anything looks the grandchild has been reparented and no parent-pid walk
    can find it. On Windows the job object is the only thing that can reach it,
    and closing the job is what does. On POSIX there is no job object and the
    command completed normally, so nothing is claimed here -- see the report.
    """
    if not can_run_processes() or os.name != "nt":
        return
    box = Box(**{"sleeper.py": SLEEPER, "orphaner.py": SPAWN_AND_EXIT})
    orphan = 0
    try:
        outcome = S.run_pipeline([[PYTHON, "orphaner.py"]], cwd=box.path,
                                 timeout=30)
        assert outcome.exit_code == 0, (outcome.error, outcome.output[-300:])
        recorded = box.path / "orphan.pid"
        assert recorded.exists(), "the child never spawned anything"
        orphan = int(recorded.read_text().strip())
        assert wait_until(lambda: not alive(orphan), seconds=10), (
            "an orphaned grandchild survived the command that started it")
    finally:
        reap(orphan)
        box.close()


def test_kill_tree_stops_the_child_and_the_grandchild_it_started():
    """The primitive the timeout path leans on, driven directly so a failure
    here is not confused with a failure of the timing above."""
    if not can_run_processes():
        return
    box = Box(**{"sleeper.py": SLEEPER, "spawner.py": SPAWN_AND_WAIT})
    handle = None
    child = grandchild = 0
    try:
        handle = S.launch([PYTHON, "spawner.py"], cwd=box.path, timeout=60)
        child = handle.pid
        recorded = box.path / "grandchild.pid"
        assert wait_until(recorded.exists, seconds=20), "no grandchild appeared"
        grandchild = int(recorded.read_text().strip())
        assert alive(child) and alive(grandchild), (child, grandchild)

        handle.kill_tree()
        assert handle.killed is True
        assert wait_until(lambda: not alive(child), seconds=10), child
        assert wait_until(lambda: not alive(grandchild), seconds=10), grandchild
    finally:
        if handle is not None:
            try:
                if handle.poll() is None:
                    handle.kill_tree()
            except Exception:
                pass
        reap(child, grandchild)
        box.close()


def test_kill_all_leaves_nothing_this_module_started_running():
    """The session-end hook. `kill_all` is what the session calls on the way
    out, and it is registered with atexit as well so an exception on the way
    out cannot be what decides it."""
    if not can_run_processes():
        return
    box = Box(**{"sleeper.py": SLEEPER})
    pids = []
    try:
        handles = [S.launch([PYTHON, "sleeper.py"], cwd=box.path, timeout=60)
                   for _ in range(2)]
        pids = [handle.pid for handle in handles]
        assert all(alive(pid) for pid in pids), pids
        assert any(handle in S.live_processes() for handle in handles)

        S.kill_all()
        for pid in pids:
            assert wait_until(lambda pid=pid: not alive(pid), seconds=10), pid
        assert all(handle.poll() is not None for handle in handles)

        # Registered for the end of the process as well as called by the
        # session: read off the module's own source, because atexit's registry
        # has no public reader.
        assert "atexit.register(kill_all)" in MODULE_SOURCE
    finally:
        reap(*pids)
        box.close()


# --- pipelines and redirects ------------------------------------------------

def test_a_pipeline_wires_a_real_pipe_between_two_stages():
    """`a | b` leaves this module as two processes with a descriptor between
    them, never as a string handed to a shell."""
    if not can_run_processes():
        return
    box = Box(**{"emit.py": EMIT, "upper.py": UPPER})
    try:
        outcome = S.run_pipeline([[PYTHON, "emit.py"], [PYTHON, "upper.py"]],
                                 cwd=box.path, timeout=60)
        assert outcome.exit_code == 0, (outcome.error, outcome.output[-300:])
        assert outcome.output == "ALPHA\nBETA\n", repr(outcome.output)
        # The exit code is the LAST stage's, and the argv reported is the
        # FIRST stage's -- the one a reader recognises the command by.
        assert list(outcome.argv) == [PYTHON, "emit.py"], outcome.argv
    finally:
        box.close()


def test_a_failing_last_stage_is_what_the_pipeline_reports():
    if not can_run_processes():
        return
    box = Box(**{"emit.py": EMIT, "fail.py": EXIT_THREE})
    try:
        outcome = S.run_pipeline([[PYTHON, "emit.py"], [PYTHON, "fail.py"]],
                                 cwd=box.path, timeout=60)
        assert outcome.exit_code == 3, (outcome.exit_code, outcome.error)
        assert outcome.ran
    finally:
        box.close()


def test_redirects_write_append_read_and_split_stderr_against_real_files():
    if not can_run_processes():
        return
    box = Box(**{"shout.py": SHOUT, "count.py": COUNT_WORDS,
                 "words.txt": "one two three\n"})
    try:
        written = S.run_pipeline(
            [Command([PYTHON, "shout.py"], (Redirect(">", "out.txt"),))],
            cwd=box.path, timeout=60)
        assert written.exit_code == 0, written.error
        assert box.read("out.txt") == "to stdout\n", repr(box.read("out.txt"))
        # stdout went to the file, so only stderr came back.
        assert "to stdout" not in written.output, repr(written.output)
        assert "to stderr" in written.output

        appended = S.run_pipeline(
            [Command([PYTHON, "shout.py"], (Redirect(">>", "out.txt"),))],
            cwd=box.path, timeout=60)
        assert appended.exit_code == 0
        assert box.read("out.txt") == "to stdout\nto stdout\n"

        errors = S.run_pipeline(
            [Command([PYTHON, "shout.py"], (Redirect("2>", "err.txt"),))],
            cwd=box.path, timeout=60)
        assert errors.exit_code == 0
        assert box.read("err.txt") == "to stderr\n"
        assert "to stderr" not in errors.output

        read = S.run_pipeline(
            [Command([PYTHON, "count.py"], (Redirect("<", "words.txt"),))],
            cwd=box.path, timeout=60)
        assert read.exit_code == 0, read.error
        assert "WORDS=3" in read.output, read.output

        merged = S.run_pipeline(
            [Command([PYTHON, "shout.py"], (Redirect("2>&1"),))],
            cwd=box.path, timeout=60)
        assert merged.exit_code == 0
        assert "to stdout" in merged.output and "to stderr" in merged.output
    finally:
        box.close()


def test_a_redirect_kind_the_runner_does_not_know_is_refused_before_anything_runs():
    if not can_run_processes():
        return
    box = Box(**{"shout.py": SHOUT})
    try:
        outcome = S.run_pipeline(
            [Command([PYTHON, "shout.py"], (Redirect("<<<", "here"),))],
            cwd=box.path, timeout=30)
        assert outcome.exit_code is None
        assert not outcome.ran
        assert "redirect" in outcome.error, outcome.error
        assert outcome.output == "", "nothing may have run"
    finally:
        box.close()


def test_a_relative_redirect_target_lands_beside_the_commands_cwd():
    if not can_run_processes():
        return
    box = Box(**{"emit.py": EMIT})
    try:
        (box.path / "sub").mkdir()
        outcome = S.run_pipeline(
            [Command([PYTHON, "../emit.py"], (Redirect(">", "here.txt"),))],
            cwd=box.path / "sub", timeout=60)
        assert outcome.exit_code == 0, outcome.error
        assert (box.path / "sub" / "here.txt").exists()
        assert not (box.path / "here.txt").exists()
    finally:
        box.close()


# --- honesty about what is enforced -----------------------------------------

def test_this_host_reports_the_level_it_can_actually_enforce():
    """LEVEL_OS is claimed for a helper that exists, and only for that."""
    real = S._os_helper
    try:
        S._os_helper = lambda: ""
        assert S.sandbox_level(refresh=True) == S.LEVEL_POLICY
        S._os_helper = lambda: "/usr/bin/bwrap"
        assert S.sandbox_level(refresh=True) == S.LEVEL_OS
    finally:
        S._os_helper = real
        S.sandbox_level(refresh=True)
    assert S.sandbox_level() in (S.LEVEL_OS, S.LEVEL_POLICY)
    if not S._os_helper():
        assert S.sandbox_level() == S.LEVEL_POLICY, (
            "a host with no sandbox helper must say policy")


def test_a_host_with_no_helper_builds_no_sandbox_prefix():
    box = Box()
    try:
        if S._os_helper():
            return                    # this host has one; nothing to say here
        assert S.os_sandbox_prefix(box.path, box.path, S.NETWORK_OFFLINE) == []
    finally:
        box.close()


def test_a_run_reports_the_level_it_ran_under_not_the_one_the_host_could_manage():
    """The silent downgrade this repository would call a fabrication.

    A host whose helper exists but whose profile cannot be built runs under
    policy, and the outcome must say policy.
    """
    if not can_run_processes():
        return
    box = Box(**{"ok.py": OK})
    real_level = S.sandbox_level
    real_prefix = S.os_sandbox_prefix
    try:
        S.sandbox_level = lambda *a, **k: S.LEVEL_OS
        S.os_sandbox_prefix = lambda *a, **k: []
        outcome = S.run_argv([PYTHON, "ok.py"], cwd=box.path, timeout=30)
        assert outcome.exit_code == 0, outcome.error
        assert outcome.level == S.LEVEL_POLICY, outcome.level
    finally:
        S.sandbox_level = real_level
        S.os_sandbox_prefix = real_prefix
        box.close()


def test_the_module_says_plainly_that_policy_level_does_not_confine_writes():
    """The rule the specification puts above every other in this change: never
    claim confinement that did not happen."""
    doc = S.__doc__ or ""
    assert "LEVEL_POLICY" in doc
    assert "NOT confined" in doc, doc[:200]
    assert "writes are confined" not in doc
    lowered = MODULE_SOURCE.lower()
    assert "no inspection" in lowered, (
        "the module must name what policy level cannot see")


def test_no_process_on_this_path_is_created_through_a_shell():
    """Read off the module's own source, which is the only way to ask it."""
    assert "shell=True" not in MODULE_SOURCE
    assert "os.system" not in MODULE_SOURCE
    assert "os.popen" not in MODULE_SOURCE
    # A shell is named in this module only in prose -- the comment explaining
    # that the Windows system directory puts cmd.exe within a child's reach
    # and why that changes nothing. Never on a line of code.
    for line in MODULE_SOURCE.splitlines():
        if "cmd.exe" in line or "/bin/sh" in line or "powershell" in line:
            assert line.lstrip().startswith("#"), line
    # Every launch this module makes takes a list. The two places it starts
    # anything are `subprocess.Popen(` in launch and the taskkill cleanup.
    for marker in ("subprocess.Popen(\n", "subprocess.run([\"taskkill\""):
        assert marker in MODULE_SOURCE, marker


def test_an_outcome_reports_what_ran_rather_than_what_was_asked_for():
    """The six fields the specification names, plus the three that keep the
    report honest: the level, what degraded, and the argv."""
    outcome = S.ExecOutcome(exit_code=0, output="x", duration=1.5)
    assert outcome.ran and outcome.ok
    assert outcome.level in (S.LEVEL_OS, S.LEVEL_POLICY)
    assert outcome.degraded == ()
    assert outcome.total_output == 0
    for field in ("exit_code", "output", "duration", "error", "killed",
                  "truncated", "level", "degraded", "argv", "total_output"):
        assert field in S.ExecOutcome.__slots__, field
    assert not S.ExecOutcome(exit_code=None).ran
    assert not S.ExecOutcome(exit_code=1).ok
