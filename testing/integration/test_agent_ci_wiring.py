"""CI mode as a pipeline actually reaches it: the flags, `main`, and the loop.

`test_agent_ci` drives the contract on its own. This drives the halves that only
exist once it is wired in -- the argument parsing, the branch in `main` that
skips every screen, the credential check that must never open a form, and the
five things `ci` changes inside the session loop.

Every model reply is scripted. Nothing here reaches the network, and the
credential store is the sandbox `test_agent_credentials` built, so a machine
with no key configured runs this file exactly as a machine with one.

What is pinned, and why each is worth a test of its own:

- NOTHING interactive is reached. No splash, no menu, no setup form, no prompt
  box, no stdin read -- a CI run that blocked on any of them would be a
  pipeline hung with no output and no way to see why;
- an approval is refused rather than waited on, and the dangerous-command
  denylist is untouched: `--ci` must not become the documented way to run
  what the interactive agent refuses to run unattended;
- `git push` needs the flag AND the task's own words, because an unattended
  push is the one action nobody can take back;
- the exit code is the signal. Every status maps to a distinct code and only
  success is zero;
- with `--json` the object is the ONLY thing on stdout, because a pipeline
  parses stdout and a human log mixed into it is a parse error.
"""

import contextlib
import io
import json
import os
import sys
from pathlib import Path

import agent_ci
import agent_config
import TMT

from test_agent_credentials import Credentials
from test_agent_workspace import Workspace


def script(*replies):
    """A model that answers with these, in order, and records the requests."""
    seen = []

    def ask(messages, on_event=None):
        seen.append([dict(message) for message in messages])
        return replies[min(len(seen) - 1, len(replies) - 1)]

    return ask, seen


def reply(action, **keys):
    keys["action"] = action
    keys.setdefault("progress", "working")
    return json.dumps(keys)


DONE = reply("end_conversation", message="Fixed it. Tests pass.")


def ci(argv, replies=(DONE,), key="sk-test", files=None, workspace=None):
    """Run `TMT.main(argv)` in CI mode and return (code, stdout, stderr, box).

    The workspace is a git repository, so `resolve_workspace` has nothing to
    ask about -- CI answers its one question with "no" and a non-git directory
    holding files would be refused, which is its own test below.
    """
    box = workspace or Workspace(git=True, files=files or {"app.py": "x = 1\n"})
    out, err = io.StringIO(), io.StringIO()
    ask, seen = script(*replies)
    saved = (TMT.ask_model, sys.stdin)
    previous_cwd = Path.cwd()
    credentials = Credentials(key=key)
    try:
        os.chdir(str(box.path))
        TMT.ask_model = ask
        # Never a terminal, and never readable: a CI run that reached stdin
        # would be one this test could not tell from one that hung.
        sys.stdin = io.StringIO("")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = TMT.main(list(argv))
    finally:
        os.chdir(str(previous_cwd))
        TMT.ask_model, sys.stdin = saved
        credentials.close()
    return code, out.getvalue(), err.getvalue(), box, seen


# --- the flags ---------------------------------------------------------------

def test_the_task_is_the_words_after_the_flags_and_the_workspace_is_not():
    """Two positionals cannot be told apart here, so there is one, read
    differently in the two modes. Without this, `--ci run the tests` would put
    `run` in the workspace slot and work on the wrong directory."""
    args = TMT.parse_args(["--ci", "run", "the", "tests"])
    assert args.ci is True
    assert args.task_words == ["run", "the", "tests"]
    assert args.directory is None
    assert agent_ci.read_task(args.task_words) == "run the tests"


def test_without_ci_the_positional_is_still_the_workspace():
    """The behaviour that was here before, unchanged, asserted rather than
    assumed -- the parser had to be restructured to add the task."""
    args = TMT.parse_args(["/some/project"])
    assert args.ci is False
    assert args.directory == "/some/project"
    assert args.task_words == []
    args = TMT.parse_args(["--dir", "/other"])
    assert args.directory == "/other"
    args = TMT.parse_args([])
    assert args.directory is None


def test_a_task_without_ci_is_refused_rather_than_read_as_two_directories():
    for argv in (["run", "the", "tests"], ["/a", "/b"]):
        code = None
        with contextlib.redirect_stderr(io.StringIO()) as err:
            try:
                TMT.parse_args(argv)
            except SystemExit as error:
                code = error.code
        assert code == 2, argv
        assert "needs --ci" in err.getvalue() or "one directory" in err.getvalue()


def test_ci_defaults_are_the_documented_ones():
    args = TMT.parse_args(["--ci", "do it"])
    assert agent_ci.check_turns(args.max_turns) == agent_ci.DEFAULT_MAX_TURNS
    assert agent_ci.check_timeout(args.timeout) == agent_ci.DEFAULT_TIMEOUT
    assert args.as_json is False
    assert args.allow_push is False, "a push must be opted into, never out of"


# --- refusing to start -------------------------------------------------------

def test_ci_with_no_task_says_how_to_give_one_and_exits_on_usage():
    code, out, err, box, _ = ci(["--ci"])
    try:
        assert code == agent_ci.EXIT_USAGE, code
        assert "needs a task" in err, err
        assert out == "", out
    finally:
        box.close()


def test_a_missing_credential_is_a_usage_error_and_never_opens_the_form():
    """`ensure_api_key` runs the interactive first-launch setup when there is
    no key. In CI there is nobody to fill it in, so the check is made directly
    and the run stops with something a pipeline can act on."""
    opened = []
    saved = TMT.ensure_api_key
    TMT.ensure_api_key = lambda *a, **k: opened.append(True) or True
    try:
        code, out, err, box, _ = ci(["--ci", "fix the tests"], key=None)
    finally:
        TMT.ensure_api_key = saved
    try:
        assert code == agent_ci.EXIT_USAGE, code
        assert opened == [], "the setup form must never be reached"
        assert "No API key" in err, err
        for name in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
            assert name in err, "the fix has to name the variable"
    finally:
        box.close()


def test_a_directory_that_would_need_a_human_to_adopt_is_refused():
    """`resolve_workspace` asks out loud before adopting a directory that
    holds files and has no version control, and what it warns about --
    "nothing it does will be recoverable" -- is exactly what an unattended run
    must not agree to on somebody's behalf."""
    box = Workspace(git=False, files={"theirs.txt": "not mine\n"})
    code, out, err, box, _ = ci(["--ci", "fix it"], workspace=box)
    try:
        assert code == agent_ci.EXIT_USAGE, code
        assert "unattended" in err, err
        assert (box.path / "theirs.txt").exists(), "nothing may be touched"
    finally:
        box.close()


# --- nothing interactive -----------------------------------------------------

def test_no_screen_and_no_prompt_is_reached_at_all():
    """A CI run that blocked on any of these would be a pipeline hung with no
    output. Asserted by replacing each with something that records having been
    called, because "it did not hang" is not evidence that it was skipped."""
    reached = []
    saved = (TMT.run_splash, TMT.run_startup, TMT.ensure_api_key,
             TMT.ensure_git_identity, TMT.clear_screen)
    TMT.run_splash = lambda *a, **k: reached.append("splash") or "start"
    TMT.run_startup = lambda *a, **k: reached.append("startup") or "start"
    TMT.ensure_api_key = lambda *a, **k: reached.append("key") or True
    TMT.ensure_git_identity = lambda *a, **k: reached.append("identity")
    TMT.clear_screen = lambda *a, **k: reached.append("clear")
    try:
        code, out, err, box, seen = ci(["--ci", "fix the tests"])
    finally:
        (TMT.run_splash, TMT.run_startup, TMT.ensure_api_key,
         TMT.ensure_git_identity, TMT.clear_screen) = saved
    try:
        assert reached == [], reached
        assert code == agent_ci.EXIT_OK, (code, err)
        assert len(seen) == 1, "one task, one request"
    finally:
        box.close()


def test_the_task_reaches_the_model_and_the_loop_stops_after_one():
    code, out, err, box, seen = ci(["--ci", "fix", "the", "tests"])
    try:
        assert code == agent_ci.EXIT_OK, (code, err)
        assert len(seen) == 1, len(seen)
        assert seen[0][-1]["content"].strip().endswith("fix the tests"), \
            seen[0][-1]["content"][-80:]
    finally:
        box.close()


def test_a_run_that_writes_reports_the_files_it_named():
    code, out, err, box, _ = ci(
        ["--ci", "--json", "add a module"],
        [reply("write_file", path="new.py", content="x = 2\n"), DONE])
    try:
        body = json.loads(out)
        assert body["status"] == "completed", body
        assert body["changed_files"] == ["new.py"], body["changed_files"]
        assert (box.path / "new.py").read_text().strip() == "x = 2"
    finally:
        box.close()


# --- the bounds --------------------------------------------------------------

def test_max_turns_stops_the_run_with_its_own_status_and_code():
    """A model that never ends the conversation must not run forever."""
    code, out, err, box, seen = ci(
        ["--ci", "--json", "--max-turns", "3", "keep going"],
        [reply("read_file", path="app.py")])
    try:
        assert code == agent_ci.EXIT_LIMIT, (code, err)
        body = json.loads(out)
        assert body["status"] == "max_turns", body
        assert body["ok"] is False
        assert body["turns"] == 3, body["turns"]
        assert len(seen) == 3, len(seen)
    finally:
        box.close()


def test_the_wall_clock_stops_the_run_between_actions():
    """Enforced to the nearest action rather than to the second: it cannot
    interrupt a command already running, which `docs/ci.md` says rather than
    implying otherwise."""
    saved = agent_ci.Run.expired
    agent_ci.Run.expired = lambda self: True
    try:
        code, out, err, box, seen = ci(
            ["--ci", "--json", "--timeout", "30", "do something long"],
            [reply("read_file", path="app.py")])
    finally:
        agent_ci.Run.expired = saved
    try:
        assert code == agent_ci.EXIT_LIMIT, (code, err)
        body = json.loads(out)
        assert body["status"] == "timeout", body
        assert seen == [] or len(seen) <= 1, "it must stop before working"
    finally:
        box.close()


# --- policy ------------------------------------------------------------------

def test_an_approval_is_refused_without_anything_blocking_on_input():
    """The CI decision function stands where the terminal would, and stdin is
    a closed stream here -- so a run that tried to read one would fail rather
    than pass by luck."""
    code, out, err, box, _ = ci(
        ["--ci", "--json", "tidy up"],
        [reply("bash", command="rm -rf build"), DONE])
    try:
        body = json.loads(out)
        # The command did not run, and the run was not hung waiting to ask.
        assert body["status"] in ("completed", "blocked"), body
        assert body["blocked_reason"], "a refusal must be reported"
    finally:
        box.close()


def test_a_safe_command_still_runs_and_a_dangerous_one_still_does_not():
    """CI must not become a way round the denylist, and must not refuse
    everything either: what `agent_policy` ALLOWS is unchanged."""
    code, out, err, box, _ = ci(
        ["--ci", "--json", "look around"],
        [reply("bash", command="git status"), DONE])
    try:
        body = json.loads(out)
        assert body["status"] == "completed", body
        assert not body["blocked_reason"], body["blocked_reason"]
    finally:
        box.close()


def test_a_push_needs_the_flag_as_well_as_the_task_s_own_words():
    """Both, not either. An unattended push is the one action nobody can take
    back, so it wants two statements of intent -- one in the pipeline's
    configuration and one in the task."""
    from agent_actions import PUSH_BLOCKED

    # The words, and no flag.
    code, out, err, box, seen = ci(
        ["--ci", "commit and push to main"],
        [reply("git_push"), DONE])
    try:
        handed = seen[1][-1]["content"]
        assert PUSH_BLOCKED.split(".")[0] in handed, handed
    finally:
        box.close()

    # The flag, and no words in the task.
    code, out, err, box, seen = ci(
        ["--ci", "--allow-push", "fix the tests"],
        [reply("git_push"), DONE])
    try:
        handed = seen[1][-1]["content"]
        assert PUSH_BLOCKED.split(".")[0] in handed, handed
    finally:
        box.close()


def test_a_question_to_the_user_is_answered_nobody_is_here():
    """`ask_user` in CI must not block. The model is told there was nobody to
    ask -- not that a person declined -- so it decides and says what it
    assumed."""
    import agent_ask
    code, out, err, box, seen = ci(
        ["--ci", "set up the database"],
        [reply("ask_user", question="Which stack?",
               options=["node", "python"]), DONE])
    try:
        handed = seen[1][-1]["content"]
        assert agent_ask.NO_TERMINAL.split(".")[0] in handed, handed
        assert "dismissed" not in handed, handed
    finally:
        box.close()


# --- what a pipeline reads ---------------------------------------------------

def test_with_json_the_object_is_the_only_thing_on_stdout():
    """A pipeline parses stdout. A human log mixed into it is a parse error,
    so everything a person would read goes to stderr instead."""
    code, out, err, box, _ = ci(["--ci", "--json", "fix the tests"])
    try:
        body = json.loads(out)          # the whole of stdout, or this raises
        assert body["ok"] is True and body["status"] == "completed"
        assert body["task"] == "fix the tests"
        assert body["workspace"] == str(box.path)
        assert out.count("{") >= 1 and out.strip().startswith("{")
        assert out.strip().endswith("}")
    finally:
        box.close()


def test_without_json_the_exit_code_is_still_the_signal():
    """Without `--json` there is nothing to parse, so the session's own log is
    left on stdout where a person running this by hand expects it. What does
    not move is the summary and the code: the summary goes to stderr in both
    modes, and the code is what a pipeline branches on either way."""
    code, out, err, box, _ = ci(["--ci", "fix the tests"])
    try:
        assert code == agent_ci.EXIT_OK
        assert "TMT CI: completed" in err, err
        # And no JSON has been printed anywhere, so a caller cannot half-parse
        # a run it did not ask for a machine answer from.
        assert '"status"' not in out and '"status"' not in err
    finally:
        box.close()


def test_a_model_that_reports_a_failure_is_not_reported_as_success():
    """The exit code has to mean something. A turn that ended without an
    answer is a failure whatever else happened."""
    code, out, err, box, _ = ci(
        ["--ci", "--json", "--max-turns", "2", "do it"],
        ["this is not json at all"])
    try:
        assert code != agent_ci.EXIT_OK, code
        body = json.loads(out)
        assert body["ok"] is False, body
    finally:
        box.close()


def test_the_interactive_path_is_untouched_when_ci_is_absent():
    """The parser was restructured and the loop gained a parameter. Neither
    may change what a run without --ci does."""
    import inspect
    signature = inspect.signature(TMT._session_loop)
    assert list(signature.parameters) == ["root", "ci"], signature
    assert signature.parameters["ci"].default is None, \
        "no ci means the loop that was here before it existed"
    source = Path(TMT.__file__).read_text(encoding="utf-8", errors="replace")
    assert "if getattr(args, \"ci\", False):" in source
    # The two callables a CI run substitutes are still built for an
    # interactive one.
    assert "_command_approval(prompt_box, live_panel, pad)" in source
    assert "_question_asker(prompt_box, live_panel, pad)" in source


def test_the_exit_code_reaches_the_shell_from_the_file_as_well_as_the_script():
    """`bin/tmtcode.js` execs TMT.py directly, so the npm install -- the one
    the README leads with -- goes through `__main__` and not through the
    console-script wrapper. It used to throw the return value away."""
    source = Path(TMT.__file__).read_text(encoding="utf-8", errors="replace")
    assert "sys.exit(main())" in source, \
        "__main__ must propagate the exit code"


def test_the_module_is_declared_where_an_editable_install_can_see_it():
    import re
    text = (Path(agent_config.__file__).resolve().parent
            / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^\s*"agent_ci",\s*$', text, re.M), \
        "agent_ci is missing from pyproject.toml's py-modules"
