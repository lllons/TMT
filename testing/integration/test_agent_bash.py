"""The `bash` action as TMT actually offers it: registered, guarded, run.

Everything here goes through `agent_actions.execute_action`, never through
`agent_bash.bash`, for the reason `test_agent_toolflow.py` states and this
change makes sharper than any before it: a tool that works perfectly and is
not registered is a tool that does not exist. Four modules were written from
scratch, two model-facing verbs were deleted, and a dozen registries had to
learn one name. Any one of them left behind gives a command tool that passes
its own unit tests and is unreachable by a model -- or, worse, a guard that
holds in `agent_policy` and is never asked on the path a model takes.

There is exactly one place a direct call is made rather than a dispatch, and
it is named where it happens: `agent_policy.decide`, asked in the approval
tests to establish that the command chosen really is the ASK verdict those
tests are about. Everything with a consequence -- every refusal, every run,
every job -- arrives through the dispatcher.

**Refusals are asserted against the DISK, not against the sentence.** A guard
that returns the right words and lets the command run is the failure worth
testing for, and only the filesystem can tell those apart. Every security test
below either checks that the workspace is unchanged or checks that the file
the command was reaching for is still not there.

Two things about the shape of these tests are worth knowing before changing
them:

**A skipped stage is still REPORTED.** `a && b` after a failing `a` prints a
`[2/2] ... -- not run:` row naming `b`, so the naive check "is `b` in the
output" passes for a stage that never ran. The short-circuit tests therefore
assert on the DISK -- the second stage writes a marker file, and the marker
must not be there -- and they assert the naive check passes as well, so
nobody replaces the real assertion with the one that proves nothing.

**Nothing may be left running.** Every job started here is stopped in a
`finally`, and the registry is emptied with it: `agent_bash._JOBS` is module
state, so a test that left a finished job behind would change what the next
test's `status` says. There is no public way to forget a job -- there should
not be, since forgetting one is how one comes to outlive the session -- so the
list is emptied by name, here, in the one place that has the right to.
"""

import ast
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path

import agent_actions
import agent_bash
import agent_config
import agent_delegation
import agent_manager
import agent_policy
import agent_prompt
import agent_sandbox
import agent_shell
import agent_subprompts
import agent_worker
from agent_config import REQUIRED_KEYS

# Derived from a module rather than from `__file__`: this file lives two
# directories down, and the paths below have to name the repository itself.
REPO = Path(agent_config.__file__).resolve().parent

# `exit 3 in 0.1s | cwd: . | sandbox: policy` -- the one line of a run result
# that carries the verdict. Anchored, because the whole point of reading it is
# that it is TMT's own statement of the exit code and not a word found in a
# program's output.
EXIT_LINE = re.compile(r"^exit (-?\d+) in ", re.M)

# `Started job 4: python sleep.py`. The id is parsed back rather than assumed
# to be 1, because `agent_bash._NEXT_ID` runs for the life of the process and a
# test that hard-coded a number would pass only when it ran first.
STARTED = re.compile(r"^Started job (\S+):", re.M)


# --- the throwaway world ----------------------------------------------------

def remove_tree(path):
    """Delete a temp tree, including anything left read-only on Windows."""
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    try:
        shutil.rmtree(path, onerror=on_error)
    except OSError:
        # A job's log file can still be held for a moment after its process
        # tree has been killed. A temp directory that outlives the run is a
        # smaller failure than a red suite saying nothing about the code.
        shutil.rmtree(path, ignore_errors=True)


def stop_every_job():
    """Leave the job registry exactly as this file found it.

    `shutdown()` is the public promise -- it kills every running job's whole
    process tree -- and emptying `_JOBS` afterwards is the part that has no
    public spelling. It must not have one: a job dropped from the registry is
    a job nothing would stop at the end of the session, which is the single
    thing background execution has to make impossible. A test is the one
    caller that has the right to reach in, because the alternative is every
    later test reading a registry full of somebody else's finished jobs.
    """
    agent_bash.shutdown()
    del agent_bash._JOBS[:]


class Project:
    """A throwaway workspace, with TMT's own state sent somewhere throwaway.

    The same helper `test_agent_toolflow` and `test_agent_grep_glob_wiring`
    use, for the same reason: none of this may pass or fail because of what
    happens to be in this repository today. It matters more here than there,
    because these tests start real processes with this directory as their
    working directory.

    `close()` stops every job first. A test that failed half way through
    having started a server must not leave it running, and a `finally` in each
    test is not enough on its own -- the assertion that fails could be the one
    before the id was even read back.
    """

    def __init__(self, files=None):
        self.previous_root = agent_config.ROOT_DIR
        self.previous_install = agent_config.INSTALL_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_bash_")).resolve()
        self.install = Path(tempfile.mkdtemp(prefix="tmt_bashinst_")).resolve()
        agent_config.ROOT_DIR = self.path
        agent_config.INSTALL_DIR = self.install
        for name, body in (files or {}).items():
            self.write(name, body)

    def write(self, name, body):
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body.encode("utf-8"))
        return target

    def has(self, name):
        return (self.path / name).exists()

    def listing(self):
        """Every path in the workspace, relative, sorted, one separator."""
        return sorted(str(item.relative_to(self.path)).replace("\\", "/")
                      for item in self.path.rglob("*"))

    def close(self):
        stop_every_job()
        agent_config.ROOT_DIR = self.previous_root
        agent_config.INSTALL_DIR = self.previous_install
        remove_tree(self.path)
        remove_tree(self.install)


_DEFAULT_CONTEXT = object()


def run(**keys):
    """One `bash` action, exactly as the loop would run it.

    The action context is passed as `_context` so that `bash`'s own keys --
    `command`, `operation`, `cwd`, `timeout`, `id`, `network` -- all keep their
    names, and so that a test can put an approver in the context the way
    `TMT._session_loop` does. The default carries no `approve`, which is what a
    piped run, the suite and a background agent all look like.
    """
    context = keys.pop("_context", _DEFAULT_CONTEXT)
    if context is _DEFAULT_CONTEXT:
        context = {"push_authorized": False}
    keys["action"] = "bash"
    return str(agent_actions.execute_action(keys, context))


def exit_code(result):
    """The exit code TMT reported, or None when it reported none."""
    found = EXIT_LINE.search(result)
    return int(found.group(1)) if found else None


def job_id(result):
    found = STARTED.search(result)
    assert found, "start did not report an id: %s" % result
    return found.group(1)


def process_alive(pid):
    """Whether the OPERATING SYSTEM still knows this pid. None if unaskable.

    Asked of the OS rather than of the handle TMT is holding, because the
    handle's answer is TMT reporting on itself and the promise under test is
    about the machine. `subprocess` is used here, in a test, deliberately: the
    grep further down forbids it in the shipped modules, not in the thing
    checking up on them.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if os.name == "nt":
        try:
            done = subprocess.run(
                ["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        return str(pid).encode("ascii") in (done.stdout or b"")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return None
    return True


def wait_until_gone(pid, seconds=5.0):
    """Wait, briefly and boundedly, for the OS to stop knowing a pid.

    Bounded because this suite has no per-test timeout, and a wait that could
    not end would hang every module after this one. An orphaned grandchild is
    reparented and reaped, which takes a moment on some systems and no time at
    all on others.
    """
    deadline = time.monotonic() + seconds
    answer = process_alive(pid)
    while answer is True and time.monotonic() < deadline:
        time.sleep(0.1)
        answer = process_alive(pid)
    return answer


# --- what the throwaway project contains ------------------------------------
#
# Everything a command is pointed at here is a python script, because `python`
# is the one program TMT can be sure of: `agent_sandbox.PROGRAMS` probes for
# forty names and this suite has to pass wherever it runs. `ls` and `cat` are
# reached for in exactly one test, which says so.

MARK = (
    "import os\n"
    "here = os.path.dirname(os.path.abspath(__file__))\n"
    "with open(os.path.join(here, 'mark.txt'), 'w') as handle:\n"
    "    handle.write('the stage ran\\n')\n"
    "print('marked')\n"
)

TICKER = (
    "import os\n"
    "import time\n"
    "here = os.path.dirname(os.path.abspath(__file__))\n"
    "target = os.path.join(here, 'ticks.txt')\n"
    "while True:\n"
    "    with open(target, 'a') as handle:\n"
    "        handle.write('tick\\n')\n"
    "    time.sleep(0.05)\n"
)

SPAWNER = (
    "import os\n"
    "import subprocess\n"
    "import sys\n"
    "import time\n"
    "here = os.path.dirname(os.path.abspath(__file__))\n"
    "child = subprocess.Popen([sys.executable, os.path.join(here, 'ticker.py')])\n"
    "print('grandchild', child.pid, flush=True)\n"
    "time.sleep(120)\n"
)

# A test that PASSES while printing the vocabulary of failure. It is the whole
# of what "the verdict is the exit code" means: every marker word
# `agent_actions._FAILURE_MARKERS` knows is in this test's own output, and the
# run still has to be reported as exit 0.
PASSING_TEST = (
    "import unittest\n"
    "\n"
    "from calc import total\n"
    "\n"
    "\n"
    "class TestTotal(unittest.TestCase):\n"
    "    def test_total_adds_up(self):\n"
    "        print('FAILED: 3 checks failed, 1 error, aborted')\n"
    "        self.assertEqual(total([1, 2, 3]), 6)\n"
)

FAILING_TEST = (
    "import unittest\n"
    "\n"
    "from calc import total\n"
    "\n"
    "\n"
    "class TestBroken(unittest.TestCase):\n"
    "    def test_total_is_wrong(self):\n"
    "        self.assertEqual(total([1, 2, 3]), 7)\n"
)

PROJECT = {
    "calc.py": "def total(items):\n    return sum(items)\n",
    "emit.py": "print('alpha')\nprint('beta')\n",
    "upper.py": ("import sys\n"
                 "for line in sys.stdin:\n"
                 "    print(line.strip().upper())\n"),
    "argv.py": "import sys\nprint('ARGS:', ' '.join(sys.argv[1:]))\n",
    "fail.py": "import sys\nprint('nope')\nsys.exit(3)\n",
    "mark.py": MARK,
    "sleep.py": "import time\ntime.sleep(120)\n",
    "ticker.py": TICKER,
    "spawner.py": SPAWNER,
    "test_good.py": PASSING_TEST,
    "test_bad.py": FAILING_TEST,
    "a.txt": "one\n",
    "b.txt": "two\n",
    "notes.md": "# notes\n",
    "sub/inner.py": "print('inner')\n",
}


# --- functionality: a real command, really run ------------------------------

def test_a_command_reaches_a_real_process_through_the_dispatcher():
    """The first thing that has to be true, and the thing a registry left
    behind would break. The result names the command as TMT parsed it, the
    exit code, the working directory and the sandbox level it actually ran
    under -- the last because a result that did not say would leave a reader
    unable to tell a confined run from an unconfined one."""
    box = Project(files=PROJECT)
    try:
        result = run(command="python emit.py")
        assert "Unknown action" not in result, result
        assert "is unavailable" not in result, result
        assert exit_code(result) == 0, result
        assert "alpha" in result and "beta" in result, result
        assert result.startswith("$ python emit.py"), result
        assert "cwd: ." in result, result
        assert "sandbox: %s" % agent_sandbox.sandbox_level() in result, result
    finally:
        box.close()


def test_a_failing_command_reports_its_own_exit_code():
    """Not "it failed": the number. A model deciding what to do next reasons
    about the code, and 3 and 1 mean different things to the program that
    produced them."""
    box = Project(files=PROJECT)
    try:
        result = run(command="python fail.py")
        assert exit_code(result) == 3, result
        assert "nope" in result, result
    finally:
        box.close()


def test_a_pipeline_is_wired_between_two_real_processes():
    """`|` works and no shell was asked to make it work. The second stage sees
    the first stage's stdout, which is only true if TMT put a real pipe
    between two processes it started itself."""
    box = Project(files=PROJECT)
    try:
        result = run(command="python emit.py | python upper.py")
        assert exit_code(result) == 0, result
        assert "ALPHA" in result and "BETA" in result, result
        # The lower-case originals never appear: they went down the pipe, not
        # to the terminal, which is what makes this a pipeline rather than two
        # commands that both printed.
        assert "alpha" not in result.replace("$ python emit.py", ""), result
    finally:
        box.close()


def test_a_redirect_writes_into_the_workspace_and_the_file_holds_it():
    """Asserted on the bytes on disk. A redirect that reports success and
    writes nothing is exactly the failure a result-only check would miss."""
    box = Project(files=PROJECT)
    try:
        result = run(command="python emit.py > out.txt")
        assert exit_code(result) == 0, result
        written = (box.path / "out.txt").read_text(encoding="utf-8")
        assert written.splitlines() == ["alpha", "beta"], written
        # The output went to the file and therefore not to the model.
        assert "--- no output ---" in result, result
    finally:
        box.close()


def test_a_star_is_expanded_by_tmt_and_a_pattern_with_no_match_is_left_alone():
    """Globbing is TMT's, against the workspace, before the policy decides --
    so what the policy read is what the program was handed. The echoed command
    line shows the expansion, which is how a reader knows what actually ran.

    The no-match half is the shell convention and it is not decoration: a
    pattern left as written is a pattern the program receives verbatim, and
    inventing an empty argument list for it would silently change what the
    command means."""
    box = Project(files=PROJECT)
    try:
        result = run(command="python argv.py *.txt")
        assert exit_code(result) == 0, result
        assert "ARGS: a.txt b.txt" in result, result
        assert "notes.md" not in result, result
        # The rendered line is the expanded one, not the pattern.
        assert result.startswith("$ python argv.py a.txt b.txt"), result

        missing = run(command="python argv.py *.nothinghere")
        assert exit_code(missing) == 0, missing
        assert "ARGS: *.nothinghere" in missing, missing
    finally:
        box.close()


def test_cwd_names_a_subdirectory_and_the_command_runs_in_it():
    """`sub/inner.py` is reachable as `inner.py` only from `sub`, so the
    output is the evidence that the working directory was really moved rather
    than merely reported."""
    box = Project(files=PROJECT)
    try:
        result = run(command="python inner.py", cwd="sub")
        assert exit_code(result) == 0, result
        assert "inner" in result, result
        assert "cwd: sub" in result, result
    finally:
        box.close()


def test_a_real_projects_tests_run_and_the_verdict_is_the_code_not_the_text():
    """A project with a passing test and a failing one, run the way a model
    would run them, and the two answers have to differ.

    The passing test prints `FAILED: 3 checks failed, 1 error, aborted` on its
    way to succeeding. That is deliberate: it is every word
    `agent_actions._FAILURE_MARKERS` knows, in the output of a run that
    exited 0. Scanning output text for "failed" is what once called a green
    suite a failure in this repository, and a command tool is where that
    mistake would do the most damage -- so the exit code is the only thing
    read, and the misleading words are quoted back unchanged."""
    box = Project(files=PROJECT)
    try:
        good = run(command="python -m unittest test_good")
        bad = run(command="python -m unittest test_bad")

        assert exit_code(good) == 0, good
        assert exit_code(bad) is not None and exit_code(bad) != 0, bad
        assert exit_code(good) != exit_code(bad), (good, bad)

        # The passing run said all of that and was still exit 0.
        assert "FAILED: 3 checks failed" in good, good
        assert "OK" in good, good
        # The failing run's own words, quoted rather than interpreted.
        assert "AssertionError: 6 != 7" in bad, bad
    finally:
        box.close()


def test_and_short_circuits_after_a_failure_and_the_second_stage_never_runs():
    """`&&` after a failing stage. The stage that did not run is REPORTED --
    it has a row saying so, with the reason -- and the naive check below
    passes because of it, which is why the real assertion is the one after.

    Getting this wrong is not cosmetic. A model that writes `a && b` and is
    handed `b`'s output after `a` failed concludes something false about its
    own change."""
    box = Project(files=PROJECT)
    try:
        result = run(command="python fail.py && python mark.py")

        # The naive check, made deliberately, so nobody replaces the real one
        # with it: the skipped stage IS in the output.
        assert "python mark.py" in result, result

        # What actually distinguishes ran from listed.
        assert not box.has("mark.txt"), "the second stage of `&&` ran"
        assert "marked" not in result, result
        assert "-- not run:" in result, result
        assert "&& runs only after a success" in result, result
        # The line's code is the last stage that RAN. A skipped stage has no
        # code to give and must not be allowed to supply one.
        assert exit_code(result) == 3, result
    finally:
        box.close()


def test_or_short_circuits_after_a_success_and_the_second_stage_never_runs():
    """The mirror, and it needs its own test: the two operators are two
    branches, and a short-circuit implemented for one only is a short-circuit
    nobody notices is missing until a model writes the other."""
    box = Project(files=PROJECT)
    try:
        result = run(command="python emit.py || python mark.py")

        assert "python mark.py" in result, result            # listed
        assert not box.has("mark.txt"), "the second stage of `||` ran"
        assert "|| runs only after a failure" in result, result
        assert exit_code(result) == 0, result
    finally:
        box.close()


def test_a_semicolon_runs_the_second_stage_whatever_the_first_one_did():
    """The control: the same shape of line, with the operator that does not
    short-circuit. Without it, a `bash` that never ran a second stage at all
    would pass both tests above."""
    box = Project(files=PROJECT)
    try:
        result = run(command="python fail.py ; python mark.py")
        assert box.has("mark.txt"), "the second stage of `;` did not run"
        assert "marked" in result, result
        assert "-- not run:" not in result, result
        # The code is the LAST stage that ran, which is what a shell reports.
        assert exit_code(result) == 0, result
    finally:
        box.close()


def test_the_reads_a_shell_is_mostly_used_for_work():
    """`ls` and `cat`, the two commands a model reaches for first.

    The one test here that depends on a program other than `python`. They are
    resolved first and the assertion says so, because a machine without them
    is a machine this test cannot answer about -- and a test that quietly
    passed in that case would be proving nothing about the tool."""
    box = Project(files=PROJECT)
    try:
        for name in ("ls", "cat"):
            assert agent_sandbox.resolve_program(name), (
                "`%s` is not on the curated PATH on this host, so this test "
                "cannot say whether the tool runs it" % name)
        listed = run(command="ls")
        assert exit_code(listed) == 0, listed
        assert "calc.py" in listed, listed
        shown = run(command="cat calc.py")
        assert exit_code(shown) == 0, shown
        assert "def total(items):" in shown, shown
    finally:
        box.close()


# --- security: every one of these is asserted against the disk --------------

def test_inline_code_is_refused_for_every_interpreter_that_offers_it():
    """The load-bearing refusal. Every other guard in the policy works by
    reading a command's arguments, and inline code is the argument that
    cannot be read -- so it is refused whatever the code says, and the
    sentence sends the model to write a file and run it.

    A bare interpreter with no operand is the same hole reached through
    stdin, and is refused with it."""
    box = Project(files=PROJECT)
    before = box.listing()
    try:
        for command in ("python -c \"print(1)\"",
                        "python3 -c pass",
                        "node -e \"require('fs')\"",
                        "python"):
            result = run(command=command)
            assert result.startswith("REFUSED:"), (command, result)
            assert "Traceback" not in result, (command, result)
            # The correction, not a way round the check.
            assert ("file" in result.lower()), (command, result)
        assert box.listing() == before, box.listing()
    finally:
        box.close()


def test_a_nested_shell_is_refused_by_name():
    """`bash -c`, `sh -c`, and the family they belong to. TMT parses the line
    and runs each program itself, so a nested shell is neither needed nor
    available -- and it is the one program that would make every argument in
    this file unreadable."""
    box = Project(files=PROJECT)
    before = box.listing()
    try:
        for command in ("bash -c ls", "sh -c ls", "cmd /c dir",
                        "powershell -Command ls"):
            result = run(command=command)
            assert result.startswith("REFUSED:"), (command, result)
            assert "Rule: %s" % agent_policy.RULE_DENIED in result, (command, result)
        assert box.listing() == before, box.listing()
    finally:
        box.close()


def test_substitution_and_backticks_are_refused_by_the_parser():
    """A substitution is a second command hiding inside an argument, and every
    guard here works by looking at arguments. Refused before anything is
    decided, because there is nothing to decide about yet."""
    box = Project(files=PROJECT)
    before = box.listing()
    try:
        for command in ("python argv.py $(whoami)",
                        "python argv.py `whoami`",
                        "python argv.py ${HOME}",
                        "python argv.py $HOME"):
            result = run(command=command)
            assert result.startswith("FAILED:"), (command, result)
            assert "Traceback" not in result, (command, result)
        assert box.listing() == before, box.listing()
    finally:
        box.close()


def test_an_ampersand_is_refused_and_names_the_operation_that_replaces_it():
    """Background execution goes through the `start` operation, where it is
    registered, limited and killed at the end of the session. A `&` would put
    a process outside all three."""
    box = Project(files=PROJECT)
    try:
        result = run(command="python sleep.py &")
        assert result.startswith("FAILED:"), result
        assert "start" in result, result
        assert not agent_bash.jobs(), agent_bash.jobs()
    finally:
        box.close()


def test_an_absolute_path_to_a_program_is_refused_before_anything_else():
    """A policy about program NAMES is walked round by writing a path, so the
    shape of the executable is asked about first, ahead of every other rule.
    Both spellings, because a repository is not a platform."""
    box = Project(files=PROJECT)
    before = box.listing()
    try:
        for command in ("/usr/bin/python calc.py",
                        "C:/Windows/System32/cmd.exe /c dir",
                        "./calc.py",
                        "../python emit.py"):
            result = run(command=command)
            assert result.startswith("REFUSED:"), (command, result)
            assert "Rule: %s" % agent_policy.RULE_SHAPE in result, (command, result)
        assert box.listing() == before, box.listing()
    finally:
        box.close()


def test_a_path_argument_outside_the_workspace_is_refused():
    """Containment is `agent_file_ops`'s and is asked of every argument, so
    what a command can reach is the same question as what this workspace
    holds. Both ways out: climbing with `..` and naming an absolute path."""
    box = Project(files=PROJECT)
    before = box.listing()
    try:
        for command in ("python ../escape.py",
                        "python /tmp/escape.py",
                        "python sub/../../escape.py"):
            result = run(command=command)
            assert result.startswith("REFUSED:"), (command, result)
            assert "Rule: %s" % agent_policy.RULE_PATH in result, (command, result)
        assert box.listing() == before, box.listing()
    finally:
        box.close()


def test_a_redirect_target_outside_the_workspace_is_refused_and_writes_nothing():
    """The sharpest of the escapes, because the program is innocent: `python
    emit.py` is fine and the `>` is the whole of the attack. Checked outside
    the workspace, on the real filesystem, because that is where the file
    would be if the guard had not held."""
    box = Project(files=PROJECT)
    escaped = box.path.parent / "tmt_bash_escaped.txt"
    if escaped.exists():
        escaped.unlink()
    try:
        result = run(command="python emit.py > ../tmt_bash_escaped.txt")
        assert result.startswith("REFUSED:"), result
        assert "redirect" in result, result
        assert not escaped.exists(), "a redirect wrote outside the workspace"
    finally:
        if escaped.exists():
            escaped.unlink()
        box.close()


def test_a_cwd_outside_the_workspace_is_refused_and_nothing_runs():
    """The working directory is confined by the same one containment test the
    arguments are, and a command that cannot be placed is not run at all."""
    box = Project(files=PROJECT)
    try:
        for cwd in ("..", "/", "sub/../..", str(box.path.parent)):
            result = run(command="python emit.py", cwd=cwd)
            assert result.startswith("FAILED:"), (cwd, result)
            assert "cwd" in result, (cwd, result)
            assert "alpha" not in result, (cwd, result)
    finally:
        box.close()


def test_the_three_guarded_git_operations_are_refused_and_name_what_to_use():
    """Each of these is a rule TMT already keeps somewhere else, and a command
    line is how it would be walked round: a push is authorised by the user's
    own words, a commit is `TMTGit.commit`'s to make, and TMT never writes git
    configuration. The refusals name the action to use instead, because a
    model told only "denied" tries the same thing with different flags."""
    box = Project(files=PROJECT)
    before = box.listing()
    try:
        cases = (("git push origin main", "git_push"),
                 ("git commit -m done", "git_commit"),
                 ("git config user.email x", "git_identity"),
                 ("git -c user.email=x commit -m done", "configuration"))
        for command, expected in cases:
            result = run(command=command)
            assert result.startswith("REFUSED:"), (command, result)
            assert "Rule: %s" % agent_policy.RULE_GIT in result, (command, result)
            assert expected in result, (command, result)
        # Not an approval question, and not a repository either: nothing here
        # created one on the way past.
        assert box.listing() == before, box.listing()
        assert not box.has(".git"), "a refused git command made a repository"
    finally:
        box.close()


def test_the_network_is_refused_offline_for_a_fetch_and_for_an_install():
    """`offline` is what the session grants, and the model cannot widen it --
    the `network` key exists only because the dispatcher forwards every key
    the model wrote, and it is narrowed against the grant rather than adopted.
    So the refusal stands whatever the model asks for."""
    box = Project(files=PROJECT)
    before = box.listing()
    try:
        for command in ("curl http://example.com", "wget http://example.com"):
            result = run(command=command)
            assert result.startswith("REFUSED:"), (command, result)
            assert "Rule: %s" % agent_policy.RULE_NETWORK in result, (command, result)

        install = run(command="npm install left-pad")
        assert install.startswith("REFUSED:"), install
        assert "Rule: %s" % agent_policy.RULE_NETWORK in install, install
        assert "deps" in install, install

        # The model asking for more does not get more.
        widened = run(command="curl http://example.com", network="open")
        assert widened.startswith("REFUSED:"), widened

        assert box.listing() == before, box.listing()
        assert not box.has("node_modules"), "an offline install fetched something"
        assert not box.has("package-lock.json"), "an offline install wrote a lockfile"
    finally:
        box.close()


# --- approval ---------------------------------------------------------------
#
# `rm` is the command these three use. It is the one program TMT can be sure
# of that reaches the ASK verdict at all: every development tool is ALLOW and
# every unknown program is an ASK that could not then be observed running.
# `agent_policy.decide` is asked directly at the top of the first test -- the
# one direct call in this file -- so that a later change to the destructive
# list turns these into a clear failure about the premise rather than three
# confusing ones about approval.


class Approver:
    """A caller's approval callable, recording what it was asked.

    Two shapes are accepted by `agent_bash`; this is the two-argument one,
    which is what `TMT._session_loop` supplies. What it records is the whole
    question, because half of what is being tested is that the user is shown
    the command, the directory and the rule before being asked anything.
    """

    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def __call__(self, question, pattern=""):
        self.asked.append((question, pattern))
        return self.answer


def test_the_command_the_approval_tests_use_really_is_an_ask():
    """The premise, stated once. `agent_policy.decide` is called directly
    here and nowhere else in this file: it is not a dispatch, it makes nothing
    happen, and it is what stops the three tests below quietly becoming tests
    of an ALLOW."""
    box = Project(files=PROJECT)
    try:
        stages = agent_shell.parse("rm a.txt")
        decision = agent_policy.decide(stages, cwd=box.path, root=box.path,
                                       network=agent_policy.OFFLINE)
        assert decision.verdict == agent_policy.ASK, decision.verdict
        assert decision.rule == agent_policy.RULE_DESTRUCTIVE, decision.rule
    finally:
        box.close()


def test_an_ask_with_no_approver_is_refused_and_says_how_it_could_be_allowed():
    """The standing rule about raw terminal input, arriving where it matters
    most: any doubt that a human is there means no. A piped run, this suite
    and every background agent all arrive with no approver, and the refusal
    has to name the rule that asked -- otherwise the model cannot tell the
    user what it needed."""
    box = Project(files=PROJECT)
    try:
        result = run(command="rm a.txt")
        assert result.startswith("REFUSED:"), result
        assert "Rule: %s" % agent_policy.RULE_DESTRUCTIVE in result, result
        assert "nobody to ask" in result, result
        assert "Nothing ran and nothing was changed." in result, result
        assert box.has("a.txt"), "an unapproved rm deleted the file"
    finally:
        box.close()


def test_an_approver_that_says_yes_is_consulted_and_the_command_then_runs():
    """Both halves. The approver has to be ASKED -- with the command, the
    working directory, the rule and its reason, and the pattern the answer
    would be remembered as -- and the yes has to actually let go of the gate,
    which only the disk can confirm."""
    box = Project(files=PROJECT)
    approver = Approver(True)
    try:
        result = run(command="rm a.txt", _context={"approve": approver})

        assert len(approver.asked) == 1, approver.asked
        question, pattern = approver.asked[0]
        assert "rm a.txt" in question, question
        assert "in ." in question, question
        assert agent_policy.RULE_DESTRUCTIVE in question, question
        assert pattern == "rm", pattern

        assert "REFUSED" not in result, result
        assert exit_code(result) == 0, result
        assert not box.has("a.txt"), "the approved command did not run"
    finally:
        box.close()


def test_an_approver_that_says_no_refuses_and_nothing_runs():
    """A no is a refusal with its own sentence -- not the same one a missing
    terminal gets, because "you were asked and declined" and "there was nobody
    to ask" are different facts and lead a model to do different things."""
    box = Project(files=PROJECT)
    approver = Approver(False)
    try:
        result = run(command="rm b.txt", _context={"approve": approver})
        assert len(approver.asked) == 1, approver.asked
        assert result.startswith("REFUSED:"), result
        assert "said no" in result, result
        assert "Do not ask again" in result, result
        assert box.has("b.txt"), "a declined rm deleted the file"
    finally:
        box.close()


def test_an_approver_that_raises_is_answered_no():
    """A security question that could not be put is a question that was not
    answered. The one direction an unreadable answer can fail in."""
    def broken(question, pattern=""):
        raise RuntimeError("the terminal went away")

    box = Project(files=PROJECT)
    try:
        result = run(command="rm b.txt", _context={"approve": broken})
        assert result.startswith("REFUSED:"), result
        assert "the terminal went away" in result, result
        assert box.has("b.txt"), "a failed approval deleted the file"
    finally:
        box.close()


# --- isolation: background agents may not run commands ----------------------

def test_bash_is_forbidden_to_workers_and_is_not_a_read_only_verb():
    """Two lists, and the second is the security whitelist: a verb absent from
    `agent_delegation.READ_ONLY_ACTIONS` is refused to a read-only delegation,
    and `bash` has to be absent from it. A read-only worker running a build is
    not read-only, and a worker that cannot be asked for approval must not
    hold the verb that needs asking."""
    assert "bash" in agent_worker.WORKER_FORBIDDEN
    assert "bash" not in agent_delegation.READ_ONLY_ACTIONS
    assert "bash" not in agent_actions.READ_ONLY_ACTIONS
    assert "bash" not in agent_worker.NOTE_ACTIONS
    assert "bash" not in agent_worker.REVIEW_ACTIONS
    assert "bash" not in agent_subprompts.NOTE_VERBS
    assert "bash" not in agent_subprompts.REVIEW_VERBS
    # The two verbs it replaced are gone from all of them, rather than being
    # left behind as names a whitelist would go on admitting.
    for names in (agent_worker.WORKER_FORBIDDEN,
                  agent_delegation.READ_ONLY_ACTIONS,
                  agent_actions.READ_ONLY_ACTIONS,
                  agent_worker.NOTE_ACTIONS, agent_worker.REVIEW_ACTIONS):
        assert "run_file" not in names, names
        assert "run_python" not in names, names


def test_the_refusal_a_worker_reads_makes_sense_without_the_verb_being_taught():
    """A background agent is never told `bash` exists, so the sentence has to
    stand on its own: what happened, why it is the main agent's, and what to
    do instead. Told only "not permitted", a model reasonably looks for
    another route to the same effect -- which is the mistake
    `WORKER_NEEDS_TERMINAL` was given its own sentence to avoid."""
    said = agent_worker._refusal("bash", None, agent_worker.WORKER_FORBIDDEN)
    assert said, "a worker is not refused bash at all"
    assert "internal_response" in said, said
    assert "main agent" in said, said
    assert "terminal" in said, said

    contract = agent_delegation.DelegationConstraints(read_only=True)
    refused = agent_delegation.refusal(contract, "bash")
    assert refused, "a read-only delegation is not refused bash"
    assert agent_delegation.VIOLATION_HEADER in refused, refused
    assert "approval" in refused, refused


def test_a_real_worker_asking_for_bash_is_refused_and_runs_nothing():
    """The loop, not the list. Driven through the real `agent_worker` step
    loop with the REAL dispatcher over a real workspace, because the refusal
    that matters is the one before dispatch and the only way to know it is
    there is to try to walk past it."""
    box = Project(files=PROJECT)
    try:
        manager = agent_manager.AgentManager()
        record = manager.spawn("run the tests and report what happened")
        replies = [json.dumps({"action": "bash", "command": "python mark.py"}),
                   json.dumps({"action": "internal_response",
                               "response": "I would have run python mark.py."})]
        seen = []

        def ask(messages, **extra):
            seen.append([dict(message) for message in messages])
            return replies.pop(0) if replies else json.dumps(
                {"action": "internal_response", "response": "done"})

        answer = agent_worker.run_worker(
            record, manager, ask=ask,
            execute=agent_actions.execute_action,
            system_prompt="worker prompt")

        assert "would have run" in answer, answer
        assert not box.has("mark.txt"), "a worker ran a command"
        # The refusal reached the model as its next user turn, which is what
        # makes it a correction rather than a dead end.
        conversation = "\n".join(m.get("content", "") for m in seen[-1])
        assert "not available to you" in conversation, conversation
    finally:
        box.close()


def test_a_read_only_delegation_is_refused_bash_at_the_dispatcher_too():
    """The second of the two layers. `agent_worker` refuses before dispatch;
    this is the dispatcher's own copy of the same rule, asked with the same
    function, and it is what refuses a call that reached `execute_action`
    another way."""
    box = Project(files=PROJECT)
    try:
        blocked = run(command="python mark.py",
                      _context={"push_authorized": False, "read_only": True})
        assert agent_delegation.VIOLATION_HEADER in blocked, blocked
        assert not box.has("mark.txt"), "a read-only worker ran a command"
    finally:
        box.close()


def _teaching_only(prompt):
    """A prompt with the workspace listing cut off the end of it.

    Every background prompt ends with the SHAPE OF THE WORKSPACE section, which
    is a listing of the repository TMT happens to be pointed at. Asking whether
    a verb is TAUGHT has to exclude it, because the answer there is about
    whatever the files are called rather than about the prompt.

    That is not hypothetical. `test_bash_is_taught_in_the_main_prompt_and_in_no_background_one`
    asserted the substring "bash" was absent from these prompts, and the day a
    `docs/bash.md` was added the test failed for everyone -- reporting that the
    worker prompt taught the verb, when what it contained was a filename. It is
    the same trap this repository already met with the tips catalogue: "is this
    string in the prompt" is always yes for anything written in any file here.

    The listing is REMOVED rather than the prompt being truncated at it. An
    earlier version cut everything from the marker onwards, and a mutation
    that leaked the bash reference AFTER the workspace section survived it --
    a blind spot at the end of the very prompt being checked.

    The span is found IN THE PROMPT rather than by rebuilding the section and
    subtracting it. Rebuilding looked tidier and was order-dependent: the
    background prompts are CACHED, so one built while a fixture had moved
    `ROOT_DIR` to a temporary workspace carries that workspace's listing,
    while `_shape_section()` called at assert time reads whatever the root is
    by then. When the two differ the subtraction silently removes nothing, the
    listing stays in, and the test fails on a filename -- which is the very
    thing it was rewritten to stop doing. It passed alone and failed in a full
    run, which is what that class of bug always looks like.
    """
    marker = "=== THE SHAPE OF THE WORKSPACE ==="
    start = prompt.find(marker)
    if start == -1:
        return prompt
    # The listing runs to the closing reminder, which is the last thing every
    # background prompt says. Without that anchor the tail would be lost again.
    end = prompt.find("Reminder: reply with one JSON object only", start)
    return prompt[:start] + (prompt[end:] if end != -1 else "")


def test_bash_is_taught_in_the_main_prompt_and_in_no_background_one():
    """Two-sided isolation, exactly as `plan`, `review` and `project_context`
    have it: a worker is neither taught the verb nor allowed it. The composed
    prompts are asked rather than the modules, because the background prompts
    are built out of the main prompt's own constants and a section leaking
    through `_common` would not show in a grep of `agent_subprompts`.

    What is asserted is that the TEACHING is absent -- the reference section,
    the tool-choice row and any worked example -- rather than that the four
    characters are absent from the whole prompt. A file in the workspace is
    not a lesson, and the substring form of this test could not tell the two
    apart."""
    main = agent_prompt.get_system_prompt()
    assert agent_prompt.BASH_REFERENCE in main, (
        "the main prompt does not teach the one command verb")
    assert agent_prompt.BASH_TOOL_ROW in main, (
        "the main prompt does not offer bash in the tool-choice table")
    for name in ("worker_prompt", "note_prompt", "review_prompt"):
        text = _teaching_only(getattr(agent_subprompts, name)())
        assert agent_prompt.BASH_REFERENCE not in text, "%s teaches bash" % name
        assert agent_prompt.BASH_TOOL_ROW not in text, (
            "%s offers bash in the tool-choice table" % name)
        assert '"action":"bash"' not in text, (
            "%s carries a worked example that emits bash" % name)
        assert "run_file" not in text, "%s still names run_file" % name
        assert "run_python" not in text, "%s still names run_python" % name


def test_the_two_deleted_verbs_are_named_in_no_prompt_at_all():
    """The compatibility net is deliberately taught nowhere: no prompt
    mentions the old names, no tool list offers them, no error suggests them.
    A prompt that named one would be teaching a verb whose only purpose is to
    catch a reply written before it was deleted."""
    main = agent_prompt.get_system_prompt()
    assert "run_file" not in main, "the main prompt still names run_file"
    assert "run_python" not in main, "the main prompt still names run_python"


# --- wiring: the registries a verb has to be in to exist --------------------

def test_bash_is_registered_and_the_two_verbs_it_replaced_are_gone():
    """`REQUIRED_KEYS` is the whole of what `validate_action` knows. `bash` is
    registered with NO required key, following `plan`'s precedent: `command`
    is needed for `run` and `start` and meaningless for `status`, so the
    module says which key is missing for the operation actually asked for
    rather than the schema demanding one for all five."""
    assert REQUIRED_KEYS.get("bash") == [], REQUIRED_KEYS.get("bash")
    assert "run_file" not in REQUIRED_KEYS
    assert "run_python" not in REQUIRED_KEYS


def test_validate_action_accepts_every_operation_and_names_no_missing_key():
    """Success is reported as None, not as an empty string -- a test asserting
    == "" fails on a perfectly valid action, which is a trap this repository
    has walked into before."""
    for obj in ({"action": "bash", "command": "python run_tests.py"},
                {"action": "bash", "command": "pytest", "cwd": "sub",
                 "timeout": 60, "network": "offline"},
                {"action": "bash", "operation": "start", "command": "python app.py"},
                {"action": "bash", "operation": "status"},
                {"action": "bash", "operation": "logs", "id": "1"},
                {"action": "bash", "operation": "stop", "id": "1"},
                {"action": "bash"}):
        assert agent_prompt.validate_action(obj) is None, obj
    # And the old verbs are not merely unused: they are unknown, so a reply
    # reaching for one is told so with the current list.
    unknown = agent_prompt.validate_action({"action": "run_file", "path": "x.py"})
    assert unknown and "run_file" in unknown, unknown
    assert "bash" in unknown, unknown


def test_the_missing_command_is_named_by_the_action_rather_than_the_schema():
    """The other half of registering `bash` with no required keys. The schema
    lets it through and the module has to say which key the operation it was
    given actually needs -- otherwise registering none would mean nobody says
    anything at all."""
    box = Project(files=PROJECT)
    try:
        for operation in ("run", "start"):
            result = run(operation=operation)
            assert result.startswith("FAILED:"), (operation, result)
            assert "`command`" in result, (operation, result)
            assert operation in result, (operation, result)
        for operation in ("logs", "stop"):
            result = run(operation=operation)
            assert result.startswith("FAILED:"), (operation, result)
            assert "`id`" in result, (operation, result)
        unknown = run(operation="frobnicate", command="python emit.py")
        assert unknown.startswith("FAILED:"), unknown
        assert "frobnicate" in unknown, unknown
        for name in agent_bash.OPERATIONS:
            assert name in unknown, (name, unknown)
    finally:
        box.close()


def test_the_interface_registries_name_the_one_command_verb():
    """A derived label, so `Bash` rather than the raw verb, and the `command`
    event kind the two deleted verbs held before it. A command is not a file
    operation whatever it does to files: what the user is watching is a
    process starting."""
    assert agent_actions.ACTION_LABELS.get("bash") == "Bash"
    assert "run_file" not in agent_actions.ACTION_LABELS
    assert "run_python" not in agent_actions.ACTION_LABELS
    assert agent_actions._EVENT_KIND_FOR_ACTION.get("bash") == "command"
    assert "run_file" not in agent_actions._EVENT_KIND_FOR_ACTION
    assert "run_python" not in agent_actions._EVENT_KIND_FOR_ACTION
    event = agent_actions.action_event(
        "bash", {"action": "bash", "command": "python run_tests.py"},
        "$ python run_tests.py\nexit 0 in 4.0s | cwd: . | sandbox: policy")
    assert event.kind == "command", event.kind
    assert "python run_tests.py" in event.message, event.message


def test_a_command_result_is_data_and_is_never_read_for_whether_it_worked():
    """`bash` is deliberately absent from `_REPORTED_ACTIONS`. Its result is
    whatever a program printed, which is the definition of data here -- and it
    is the very output the false alarm that rule exists for was found in. A
    result full of the word FAILED still draws the ordinary command row."""
    output = ("$ python run_tests.py\nexit 0 in 4.0s | cwd: . | sandbox: policy\n"
              "--- output ---\n1683 passed, 0 failed\nFAILED: nothing\n")
    event = agent_actions.action_event(
        "bash", {"action": "bash", "command": "python run_tests.py"}, output)
    assert event.kind == "command", event.kind
    assert "bash" not in agent_actions._REPORTED_ACTIONS


def test_the_four_new_modules_are_in_the_frozen_module_list():
    """An editable install writes `py-modules` at install time, so a module
    that is not in it is invisible to `tmtcode` however well it works in a
    test that puts the repository on sys.path. `_run_tool` would answer
    "agent_bash is unavailable" and the model would work around a tool sitting
    on disk."""
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    for name in ("agent_bash", "agent_policy", "agent_sandbox", "agent_shell"):
        assert '"%s"' % name in text, "%s is not in pyproject.toml" % name
        assert (REPO / ("%s.py" % name)).is_file(), name


def test_a_legacy_run_file_becomes_the_command_its_extension_names():
    """The widest of the compatibility nets. `run_file` ran ONE FILE by its
    extension and `bash` runs a command line, so the translation is not a
    rename -- it is the runner table. `x.py` meant `python x.py`, and that is
    what has to come out."""
    for path, expected in (("x.py", "python x.py"),
                           ("sub/thing.py", "python sub/thing.py"),
                           ("main.go", "go run main.go"),
                           ("app.js", "node app.js")):
        for verb in ("run_file", "run_python"):
            obj = agent_actions.adopt_verb({"action": verb, "path": path})
            assert obj["action"] == "bash", obj
            assert obj["command"] == expected, obj
    # A path that needs quoting gets it, because TMT parses this line itself a
    # moment later and an unquoted space is two arguments.
    spaced = agent_actions.adopt_verb({"action": "run_file", "path": "my file.py"})
    assert spaced["command"] == "python 'my file.py'", spaced


def test_a_legacy_run_file_with_no_known_runner_refuses_rather_than_guesses():
    """Handing `bash` a command TMT invented for a file type it does not know
    would be fabricating the one thing a model then acts on -- and it is the
    kind of invention that runs rather than merely misleads. Driven all the
    way through the dispatcher, because a refusal parked on the object is only
    a refusal if something reads it."""
    box = Project(files=PROJECT)
    try:
        for path in ("notes.md", "data", "archive.zzz"):
            obj = agent_actions.adopt_verb({"action": "run_file", "path": path})
            assert obj["action"] == "bash", obj
            assert "command" not in obj, obj
            result = str(agent_actions.execute_action(
                obj, {"push_authorized": False}))
            assert result.startswith("FAILED:"), (path, result)
            assert "bash" in result, (path, result)
            assert "run_file and run_python are gone" in result, (path, result)
        assert not box.has("mark.txt")
    finally:
        box.close()


def test_a_legacy_run_file_runs_for_real_once_the_loop_has_adopted_it():
    """End to end on the path both step loops take: adopt the verb, then
    dispatch. Anything less proves the translation and not that the
    translation runs."""
    box = Project(files=PROJECT)
    try:
        obj = agent_actions.adopt_verb({"action": "run_file", "path": "emit.py"})
        result = str(agent_actions.execute_action(obj, {"push_authorized": False}))
        assert exit_code(result) == 0, result
        assert "alpha" in result, result
        # And it went through everything a `bash` action goes through: a
        # legacy verb reaches nothing a current one does not.
        refused = agent_actions.adopt_verb(
            {"action": "run_file", "path": "../outside.py"})
        blocked = str(agent_actions.execute_action(
            refused, {"push_authorized": False}))
        assert blocked.startswith("REFUSED:"), blocked
    finally:
        box.close()


# --- background jobs --------------------------------------------------------

def test_a_job_starts_reports_status_reads_its_log_and_stops():
    """The four operations in the order a model uses them. Each one is asked
    through the dispatcher, and the job's own log is the evidence it really
    ran -- a status line saying "running" proves only that a record exists."""
    box = Project(files=PROJECT)
    try:
        started = run(operation="start", command="python ticker.py", timeout=30)
        identifier = job_id(started)
        assert "sandbox: %s" % agent_sandbox.sandbox_level() in started, started
        assert "logs" in started and "stop" in started, started

        listed = run(operation="status")
        assert "job %s  running" % identifier in listed, listed
        assert "of %d background job slots" % agent_bash.MAX_JOBS in listed, listed

        # The ticker writes into the workspace, so the file is what says a
        # process is really running rather than merely registered.
        deadline = time.monotonic() + 10.0
        while not box.has("ticks.txt") and time.monotonic() < deadline:
            time.sleep(0.05)
        assert box.has("ticks.txt"), "the job never ran"

        stopped = run(operation="stop", id=identifier)
        assert "Stopped job %s" % identifier in stopped, stopped

        after = run(operation="status")
        assert "job %s  stopped" % identifier in after, after
        assert "0 of %d" % agent_bash.MAX_JOBS in after, after
    finally:
        box.close()


def test_a_jobs_log_is_readable_while_it_runs_and_after_it_has_exited():
    """After is when it matters most: a server that died two seconds after it
    started has printed exactly the thing worth reading, and a log readable
    only while the process lived would lose it."""
    box = Project(files=PROJECT)
    try:
        started = run(operation="start", command="python emit.py", timeout=30)
        identifier = job_id(started)

        deadline = time.monotonic() + 10.0
        text = ""
        while time.monotonic() < deadline:
            text = run(operation="logs", id=identifier)
            if "alpha" in text:
                break
            time.sleep(0.05)
        assert "alpha" in text and "beta" in text, text

        # Wait for the process to be OVER before asking what it exited with.
        # The loop above stops as soon as the text has been printed, and a
        # process that has printed its last line has not necessarily returned
        # yet -- on a loaded machine there is real time between the two. The
        # first version asserted straight through and failed once inside a
        # full-suite run and never on its own, which is that gap exactly.
        deadline = time.monotonic() + 10.0
        after_status = ""
        while time.monotonic() < deadline:
            after_status = run(operation="status", id=identifier)
            if "exit 0" in after_status:
                break
            time.sleep(0.05)
        assert "exit 0" in after_status, after_status
        after_logs = run(operation="logs", id=identifier)
        assert "alpha" in after_logs, after_logs
        assert "exit 0" in after_logs, after_logs

        gone = run(operation="stop", id=identifier)
        assert "already ended" in gone, gone
    finally:
        box.close()


def test_the_job_limit_is_enforced_and_says_there_is_no_queue():
    """Bounded, and the refusal says plainly that nothing is queued. TMT has
    no scheduler to put a job in, and claiming queued execution the code does
    not implement is the fabrication this program refuses everywhere else."""
    box = Project(files=PROJECT)
    try:
        for index in range(agent_bash.MAX_JOBS):
            started = run(operation="start", command="python sleep.py", timeout=30)
            assert "Started job" in started, (index, started)
        refused = run(operation="start", command="python sleep.py", timeout=30)
        assert refused.startswith("FAILED:"), refused
        assert "which is the limit" in refused, refused
        assert "no queue" in refused, refused
        assert len(agent_bash.jobs()) == agent_bash.MAX_JOBS, agent_bash.jobs()

        # A slot freed by stopping one is a slot the next start can have: the
        # count is derived from the registry, never maintained as a number.
        run(operation="stop", id=agent_bash.jobs()[0].id)
        again = run(operation="start", command="python sleep.py", timeout=30)
        assert "Started job" in again, again
    finally:
        box.close()


def test_a_job_that_does_not_exist_is_named_back_with_the_ones_that_do():
    box = Project(files=PROJECT)
    try:
        started = run(operation="start", command="python sleep.py", timeout=30)
        identifier = job_id(started)
        missing = run(operation="logs", id="no-such-job")
        assert missing.startswith("FAILED:"), missing
        assert "no-such-job" in missing, missing
        assert identifier in missing, missing
    finally:
        box.close()


def test_stop_kills_the_whole_process_tree_and_the_os_agrees_it_is_gone():
    """The promise background execution stands or falls on. The job starts a
    child which starts a grandchild, and `stop` has to take both -- a
    `Popen.kill()` stops one process and leaves its children reparented and
    alive.

    Three kinds of evidence, because each on its own can be misread. The OS is
    asked about the grandchild's pid directly; the file the grandchild was
    writing has to stop growing; and TMT's own handle has to report an exit
    status, which is the OS answering rather than TMT deciding."""
    box = Project(files=PROJECT)
    try:
        # If the OS query cannot answer at all, everything below is vacuous.
        assert process_alive(os.getpid()) is True, (
            "the pid query cannot answer on this host, so this test cannot "
            "say whether anything was really killed")

        started = run(operation="start", command="python spawner.py", timeout=30)
        identifier = job_id(started)
        job = [item for item in agent_bash.jobs() if item.id == identifier][0]

        grandchild = None
        deadline = time.monotonic() + 15.0
        while grandchild is None and time.monotonic() < deadline:
            found = re.search(r"grandchild (\d+)",
                              run(operation="logs", id=identifier))
            grandchild = int(found.group(1)) if found else None
            if grandchild is None:
                time.sleep(0.1)
        assert grandchild is not None, "the job never reported a grandchild"
        assert process_alive(grandchild) is True, "the grandchild never started"

        run(operation="stop", id=identifier)

        assert wait_until_gone(grandchild) is False, (
            "the grandchild outlived the job it was started by")
        assert wait_until_gone(job.process.pid) is False, (
            "the job's own process outlived the stop")
        assert job.process.poll() is not None, "no exit status was ever reported"

        # And it really stopped doing what it was doing.
        size = (box.path / "ticks.txt").stat().st_size
        time.sleep(0.5)
        assert (box.path / "ticks.txt").stat().st_size == size, (
            "the grandchild was still writing after stop")
    finally:
        box.close()


def test_the_shutdown_hook_leaves_nothing_running():
    """A job that outlives the session is the one thing this must not produce.
    `shutdown()` is registered with `atexit` at import AND is public, because
    the session calls it on its own way out -- and the registration is a line
    of module-level code, which a test can only see by reading it."""
    source = (REPO / "agent_bash.py").read_text(encoding="utf-8")
    assert "atexit.register(shutdown)" in source, (
        "agent_bash does not register its shutdown with atexit")
    sandbox_source = (REPO / "agent_sandbox.py").read_text(encoding="utf-8")
    assert "atexit.register(kill_all)" in sandbox_source, (
        "agent_sandbox does not register its second net with atexit")

    box = Project(files=PROJECT)
    try:
        started = run(operation="start", command="python spawner.py", timeout=30)
        identifier = job_id(started)
        job = [item for item in agent_bash.jobs() if item.id == identifier][0]

        deadline = time.monotonic() + 15.0
        grandchild = None
        while grandchild is None and time.monotonic() < deadline:
            found = re.search(r"grandchild (\d+)",
                              run(operation="logs", id=identifier))
            grandchild = int(found.group(1)) if found else None
            if grandchild is None:
                time.sleep(0.1)
        assert grandchild is not None, "the job never reported a grandchild"

        stopped = agent_bash.shutdown()
        assert stopped == 1, stopped
        assert wait_until_gone(job.process.pid) is False, "the job survived shutdown"
        assert wait_until_gone(grandchild) is False, (
            "a grandchild survived shutdown")
        # Idempotent: a second call has nothing left to stop.
        assert agent_bash.shutdown() == 0
    finally:
        box.close()


# --- there is no second way to run anything ---------------------------------
#
# Read with `ast` rather than with a grep, deliberately. Every module here
# discusses `subprocess` in its prose -- `agent_execution` says the word four
# times in comments explaining that it no longer runs anything -- so a text
# search answers a question about documentation. The parse answers the
# question actually being asked: which module contains code that creates a
# process.

_PROCESS_CALLS = {
    "subprocess": frozenset({
        "Popen", "run", "call", "check_call", "check_output", "getoutput",
        "getstatusoutput"}),
    "os": frozenset({
        "system", "popen", "startfile", "fork", "forkpty", "posix_spawn",
        "posix_spawnp",
        "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp",
        "execvpe",
        "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve",
        "spawnvp", "spawnvpe"}),
}

# The modules allowed to contain any of the above, and why each one is. This
# list is written out rather than collected from what happens to be there
# today: a test that exempted whatever it found would pass forever and prove
# nothing, which is the whole failure it exists to catch.
_MAY_CREATE_A_PROCESS = {
    # The one place a process is created on a model's behalf. Everything the
    # model can reach -- `bash`, and `agent_execution.run_command` behind
    # `verify` -- comes through here, so the curated PATH, the constructed
    # environment, the limits and the process-tree kill are applied once
    # rather than in each caller.
    "agent_sandbox",
    # Structured git: an argv list built by TMT from a fixed shape, never a
    # string a model wrote, and never a shell. The model reaches it through
    # `git_status`, `git_commit` and the rest, which decide what git is asked
    # rather than passing anything through.
    "agent_git",
    # TMT restarting its own process after a fast-forward. Nothing model-facing
    # is on that path at all: it runs before the session, it replaces this
    # interpreter with the same command line, and the guard that bounds it is
    # an environment variable rather than anything a model can write.
    "agent_update",
    # `open_app`, and only `open_app`: a closed two-entry registry of
    # applications, where the model supplies a PATH through `safe_path` and
    # never an executable. The verbs that used to run code from this module --
    # `run_file` and `run_python` -- are gone.
    "agent_execution",
}


def _module_sources():
    """Every shipped module, as (name, parsed tree).

    `run_tests.py` is excluded: it is the test runner, not part of the program
    that is installed, and it starts nothing today either.
    """
    for path in sorted(REPO.glob("*.py")):
        if path.name == "run_tests.py":
            continue
        yield path.stem, ast.parse(path.read_text(encoding="utf-8"), str(path))


def _process_creators(tree):
    """The names in this tree that create a process. Comments cannot reach it."""
    found = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.attr in _PROCESS_CALLS.get(node.value.id, ())):
            found.add("%s.%s" % (node.value.id, node.attr))
        elif isinstance(node, ast.ImportFrom) and node.module in _PROCESS_CALLS:
            for alias in node.names:
                if alias.name in _PROCESS_CALLS[node.module]:
                    found.add("from %s import %s" % (node.module, alias.name))
    return found


def test_only_the_documented_modules_create_a_process():
    """The definition of done for this change, asserted rather than believed.

    Equality, not containment, in both directions. A new module that starts a
    process fails this and has to be argued for in the list above; a module
    that stops starting one fails it too, which is the honest cost of a list
    that means something."""
    creators = {}
    for name, tree in _module_sources():
        found = _process_creators(tree)
        if found:
            creators[name] = sorted(found)
    assert set(creators) == _MAY_CREATE_A_PROCESS, creators


def test_the_four_new_modules_start_nothing_between_them():
    """Named individually as well, because the set comparison above would
    still pass if one of these grew a `subprocess` call and another lost one.
    The parser, the policy and the action decide; only the sandbox runs."""
    starters = {name for name, tree in _module_sources()
                if _process_creators(tree)}
    for name in ("agent_shell", "agent_policy", "agent_bash", "agent_actions",
                 "agent_worker"):
        assert name not in starters, "%s creates a process" % name


def test_no_module_ever_asks_for_a_shell():
    """`shell=True` is the one keyword that would make every argument
    inspection in `agent_policy` decoration, because there would be a second
    parser downstream reading the same string differently. Asked of call
    sites, so the sentences about it in the comments are not what answers."""
    offenders = []
    for name, tree in _module_sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "shell":
                    continue
                value = keyword.value
                if isinstance(value, ast.Constant) and not value.value:
                    continue
                offenders.append("%s:%d" % (name, getattr(node, "lineno", 0)))
    assert not offenders, offenders


def test_the_model_facing_path_never_reaches_a_shell_by_another_name():
    """The programs a shell would be reached through are denied by name, and
    the denial is a BOUNDARY rule -- one a saved rule can never switch back
    on. Asked of the policy directly here because there is nothing to run: the
    point is which rule answers, not what happens afterwards."""
    for name in ("bash", "sh", "zsh", "cmd", "powershell", "pwsh", "env",
                 "xargs", "sudo", "ssh"):
        assert name in agent_policy.DENIED_PROGRAMS, name
    assert agent_policy.RULE_DENIED in agent_policy.BOUNDARY_RULES
    assert agent_policy.RULE_INLINE in agent_policy.BOUNDARY_RULES
    assert agent_policy.RULE_SHAPE in agent_policy.BOUNDARY_RULES
