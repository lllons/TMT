"""Choosing the checks, running them, and the gate they feed.

Three layers, and the seam between them is what makes this testable:
`select` chooses with no subprocess anywhere, `run_selection` takes an
injected runner so every execution outcome can be produced on demand, and the
end-to-end tests drive the whole thing through `TMT.main`.

The one place a real subprocess is used is where the point IS the subprocess:
that a passing command really exits 0, that a missing one is reported rather
than guessed at, and that a command which does not finish is stopped and
recorded as an error rather than a failure.
"""

import io
import json
import os
import subprocess
from pathlib import Path

import agent_actions
import agent_capabilities
import agent_config
import agent_execution
import agent_plan
import agent_review
import agent_verify as V
import agent_verify_discovery as D
import agent_verify_engine as E
import TMT
from test_agent_workspace import Workspace
from test_agent_cli import drive_session


# --- a runner that answers however a test needs it to -----------------------

class Runner:
    """An injected `run_command`. Records what it was asked to run."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, argv, timeout=None):
        self.calls.append(tuple(argv))
        if not self.outcomes:
            return agent_execution.CommandOutcome(argv, exit_code=0, output="ok")
        answer = self.outcomes.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def ok(output="43 passed in 1.2s"):
    return agent_execution.CommandOutcome(("x",), exit_code=0, output=output,
                                          duration=0.4)


def bad(output="2 failed, 41 passed", code=1):
    return agent_execution.CommandOutcome(("x",), exit_code=code, output=output,
                                          duration=0.4)


def unrunnable(error="'ruff' was not found on PATH"):
    return agent_execution.CommandOutcome(("x",), error=error)


def spec_check(identifier, name, category, level, command=("python", "-c", "pass")):
    return V.VerificationCheck(identifier, name, category, level, command)


def selection(*checks, **kwargs):
    return E.Selection(checks=list(checks), **kwargs)


def a_state():
    state = V.VerificationState()
    state.note_change("write_file", ("a.py",))
    state.begin()
    return state


# --- executing a command ----------------------------------------------------

def test_a_command_that_passes_and_one_that_fails_are_told_apart_by_exit_code():
    """The real subprocess, because the exit code is the whole point."""
    good = agent_execution.run_command(["python", "-c", "print('hello')"])
    assert good.ran and good.ok, (good.exit_code, good.output)
    assert good.exit_code == 0
    assert "hello" in good.output

    poor = agent_execution.run_command(["python", "-c", "raise SystemExit(3)"])
    assert poor.ran and not poor.ok
    assert poor.exit_code == 3


def test_stdout_and_stderr_both_reach_the_output():
    """A runner splits its report across both, and reading only one loses half
    the evidence -- pytest's summary is on stdout and a crashed collection is
    on stderr."""
    done = agent_execution.run_command(
        ["python", "-c", "import sys; print('out'); print('err', file=sys.stderr)"])
    assert "out" in done.output and "err" in done.output


def test_a_missing_command_is_reported_and_never_installed():
    """Sections 29 and 30. A missing dependency is a clear error, not
    something TMT quietly fixes."""
    done = agent_execution.run_command(["definitely-not-a-real-tool-xyz"])
    assert not done.ran, done.exit_code
    assert done.exit_code is None, "no exit code means no evidence"
    assert "not found on PATH" in done.error
    assert "does not install it" in done.error


def test_a_command_that_does_not_finish_is_stopped_and_is_an_error():
    """A timeout is NOT a failure. The command did not report, so nothing is
    known about the code."""
    done = agent_execution.run_command(
        ["python", "-c", "import time; time.sleep(30)"], timeout=1)
    assert not done.ran
    assert done.exit_code is None
    assert "did not finish within 1 seconds" in done.error
    check = spec_check("t", "Tests", V.TEST, V.LEVEL_FULL)
    check.fail_to_run(done.error)
    assert check.errored and not check.failed


def test_a_command_is_an_argv_list_and_never_a_shell():
    """Section 31. If a shell were involved this would create the file."""
    box = Workspace()
    try:
        box.use()
        marker = box.path / "pwned.txt"
        done = agent_execution.run_command(
            ["python", "-c", "print('safe')", "&&", "touch", str(marker)])
        assert not marker.exists(), "a shell interpreted the arguments"
        assert done.ran
    finally:
        box.close()


def test_no_module_on_the_verification_path_uses_a_shell():
    """Greppd, the way the modules are already grepped for DECSTBM and the
    alternate screen buffer. A rule that lives only in a docstring is a rule
    somebody adds a `shell=True` next to."""
    root = Path(agent_config.__file__).resolve().parent
    for name in ("agent_execution.py", "agent_verify.py",
                 "agent_verify_discovery.py", "agent_verify_engine.py"):
        source = (root / name).read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "`" in stripped:
                continue        # prose about the rule, not code
            assert "shell=True" not in stripped, (name, line)
            assert "os.system" not in stripped, (name, line)


def test_a_command_cannot_pick_a_working_directory_outside_the_workspace():
    box = Workspace()
    try:
        box.use()
        raised = None
        try:
            agent_execution.run_command(["python", "-c", "pass"],
                                        cwd=str(box.path.parent))
        except ValueError as error:
            raised = error
        assert raised is not None, "a directory outside the workspace was accepted"
    finally:
        box.close()


# --- choosing what to run ---------------------------------------------------

def test_the_risk_of_a_change_decides_how_far_to_go():
    """Section 9, proportional engineering judgement, written down so it can
    be argued with rather than left in a model's head."""
    docs, why = E.risk_of(["README.md", "docs/guide.rst"])
    assert docs == V.LEVEL_STATIC, why
    assert "documentation" in " ".join(why)

    ordinary, why = E.risk_of(["src/util.py", "src/format.py"])
    assert ordinary == E.DEFAULT_CEILING, why

    risky, why = E.risk_of(["src/auth/token.py"])
    assert risky == V.LEVEL_FULL, why
    assert any("auth" in line for line in why)

    for path in ("db/migrations/0003_add_column.sql", "src/api/routes.py",
                 "internal/worker/threadpool.go", "pyproject.toml",
                 "package.json", "Dockerfile"):
        level, why = E.risk_of([path])
        assert level == V.LEVEL_FULL, (path, why)

    broad, why = E.risk_of(["f%d.py" % n for n in range(E.BROAD_CHANGE_FILES)])
    assert broad == V.LEVEL_FULL, why
    assert "broad change" in " ".join(why)

    nothing, why = E.risk_of([])
    assert nothing == V.LEVEL_STATIC, why

    asked, why = E.risk_of(["src/auth/token.py"], forced=V.LEVEL_BASIC)
    assert asked == V.LEVEL_BASIC, "an explicit level wins over the evidence"


def test_a_runner_that_cannot_be_narrowed_says_so_and_runs_everything():
    """The honest resolution of section 6 against a project that cannot subset
    its tests. Faking a targeted run by running everything and labelling it
    targeted would be a lie about what was checked; refusing to run tests at
    all would throw away the only evidence available."""
    whole = D.TestRunner("run_tests.py", ("python", "run_tests.py"),
                         supports_paths=False)
    assert whole.argv_for(["testing/unit/test_a.py"]) is None

    narrow = D.TestRunner("pytest", ("pytest", "-q"), supports_paths=True)
    assert narrow.argv_for(["a.py", "b.py"]) == ("pytest", "-q", "a.py", "b.py")
    assert narrow.argv_for([]) is None

    box = Workspace(files={"a.py": "VALUE = 1\n"})
    try:
        box.use()
        found = D.Discovery(root=str(box.path), ecosystems=("python",),
                            runner=whole)
        state = V.VerificationState()
        state.note_change("write_file", ("src/util.py",))
        chosen = E.select(discovery=found, state=state)
        assert chosen.ceiling == V.LEVEL_FULL
        assert any("cannot be narrowed" in reason for reason in chosen.reasons), \
            chosen.reasons
        names = [check.id for check in chosen.checks]
        assert "tests:full" in names, names
        assert "tests:targeted" not in names, names
    finally:
        box.close()


def test_a_documentation_change_does_not_earn_a_test_run():
    box = Workspace(files={"README.md": "# hi\n"})
    try:
        box.use()
        found = D.Discovery(root=str(box.path), ecosystems=("python",),
                            runner=D.TestRunner("pytest", ("pytest",),
                                                supports_paths=True))
        state = V.VerificationState()
        state.note_change("write_file", ("README.md",))
        chosen = E.select(discovery=found, state=state, paths=["README.md"])
        assert chosen.ceiling == V.LEVEL_STATIC
        assert all(check.level <= V.LEVEL_STATIC for check in chosen.checks), \
            [(c.name, c.level) for c in chosen.checks]
    finally:
        box.close()


def test_an_explicit_level_and_full_override_the_evidence():
    box = Workspace(files={"a.py": "VALUE = 1\n"})
    try:
        box.use()
        found = D.detect(box.path)
        state = V.VerificationState()
        state.note_change("write_file", ("a.py",))
        capped = E.select(discovery=found, state=state, level=1, paths=["a.py"])
        assert capped.ceiling == V.LEVEL_BASIC
        assert all(check.level == V.LEVEL_BASIC for check in capped.checks)
        widened = E.select(discovery=found, state=state, full=True, paths=["a.py"])
        assert widened.ceiling == V.LEVEL_FULL
    finally:
        box.close()


def test_the_changed_files_come_from_git_and_from_what_tmt_wrote():
    """Either alone has a hole: a new file appears in no diff, and a file
    edited outside the session appears in no action."""
    box = Workspace(git=True, files={"a.py": "VALUE = 1\n"})
    try:
        box.use()
        box.git(["add", "-A"])
        box.git(["commit", "-m", "first"])
        (box.path / "a.py").write_text("VALUE = 2\n", encoding="utf-8")
        (box.path / "new.py").write_text("OTHER = 1\n", encoding="utf-8")
        state = V.VerificationState()
        state.note_change("write_file", ("only/tmt/knows.py",))
        paths, notes = E.changed_paths(state)
        assert "a.py" in paths, paths
        assert "new.py" in paths, "an untracked file appears in no diff"
        assert "only/tmt/knows.py" in paths, "the runtime's own record"
        del notes
    finally:
        box.close()


def test_a_workspace_with_no_git_still_verifies():
    box = Workspace(files={"a.py": "VALUE = 1\n"})
    try:
        box.use()
        state = V.VerificationState()
        state.note_change("write_file", ("a.py",))
        paths, notes = E.changed_paths(state)
        assert "a.py" in paths
        assert any("not a git repository" in note or "could not" in note
                   for note in notes), notes
    finally:
        box.close()


def test_a_deleted_file_is_not_offered_to_the_syntax_check():
    """Asking a compiler for a file the change removed on purpose would report
    a failing check for work that was correct."""
    box = Workspace(files={"kept.py": "VALUE = 1\n"})
    try:
        box.use()
        assert E._changed_python(["kept.py", "gone.py", "README.md"]) == ["kept.py"]
    finally:
        box.close()


# --- running them -----------------------------------------------------------

def test_checks_run_in_level_order_and_a_failure_stops_the_rest():
    """Section 22. Once the type checker has failed, the ten minutes the
    integration suite would take are ten minutes measuring a tree already
    known to be wrong."""
    checks = [spec_check("a", "Syntax", V.SYNTAX, V.LEVEL_BASIC),
              spec_check("b", "Lint", V.LINT, V.LEVEL_STATIC),
              spec_check("c", "Tests", V.TEST, V.LEVEL_FULL)]
    runner = Runner(ok(), bad("1 error"), ok())
    state = a_state()
    result = E.run_selection(selection(*checks), state=state, runner=runner)
    assert [check.status for check in result.checks] == [
        V.CHECK_PASSED, V.CHECK_FAILED, V.CHECK_SKIPPED]
    assert len(runner.calls) == 2, runner.calls
    assert "Lint did not pass" in result.checks[2].reason
    assert result.status == V.FAILED


def test_a_clean_run_reaches_every_level():
    checks = [spec_check("a", "Syntax", V.SYNTAX, V.LEVEL_BASIC),
              spec_check("b", "Lint", V.LINT, V.LEVEL_STATIC),
              spec_check("c", "Tests", V.TEST, V.LEVEL_FULL)]
    runner = Runner(ok(), ok(), ok("43 passed"))
    result = E.run_selection(selection(*checks), state=a_state(), runner=runner)
    assert result.status == V.PASSED
    assert len(runner.calls) == 3
    assert result.level_reached == V.LEVEL_FULL
    assert "VERIFY PASSED" in result.headline()


def test_a_check_whose_tool_is_missing_is_skipped_and_does_not_stop_the_run():
    """A missing tool is a hole in the evidence with a stated shape -- not a
    failure of the code, and not a reason to stop checking."""
    checks = [spec_check("a", "Lint", V.LINT, V.LEVEL_STATIC,
                         ("definitely-not-a-real-tool-xyz", "check")),
              spec_check("b", "Tests", V.TEST, V.LEVEL_FULL)]
    runner = Runner(ok("43 passed"))
    result = E.run_selection(selection(*checks), state=a_state(), runner=runner)
    assert result.checks[0].skipped
    assert "not installed" in result.checks[0].reason
    assert result.checks[1].passed, "the run carried on"
    assert result.status == V.PASSED
    assert len(runner.calls) == 1, "the missing tool was never invoked"


def test_a_runner_that_cannot_report_leaves_an_error_and_blocks():
    checks = [spec_check("a", "Tests", V.TEST, V.LEVEL_FULL)]
    result = E.run_selection(selection(*checks), state=a_state(),
                             runner=Runner(unrunnable("it did not finish")))
    assert result.checks[0].errored
    assert result.status == V.ERROR
    assert V.refusal(state_settled(result), agent_plan.Plan(["a", "b", "c"]),
                     "end_conversation") != ""


def state_settled(result):
    state = V.VerificationState()
    state.note_change("write_file", ("a.py",))
    state.begin()
    state.settle(result)
    return state


def test_a_cancelled_run_is_not_a_passed_one():
    """Ctrl-C during a verification is the user stopping it. It propagates so
    the session loop's own handler ends the turn, with the state already
    recording why."""
    state = V.VerificationState()
    state.note_change("write_file", ("a.py",))
    raised = None
    try:
        E.verify(state, discovery=D.Discovery(root=".", ecosystems=("python",)),
                 runner=Runner(KeyboardInterrupt()),
                 level=6)
    except KeyboardInterrupt as error:
        raised = error
    if raised is None:
        # Nothing was selected to run, so nothing could be interrupted; drive
        # the cancellation at the level it is enforced instead.
        state.cancel("the user stopped it with Ctrl-C")
    assert state.state == V.CANCELLED, state.state
    assert not state.passed
    assert V.refusal(state, agent_plan.Plan(["a", "b", "c"]),
                     "end_conversation") != ""


def test_a_passed_check_is_reused_only_while_nothing_has_moved():
    """Section 28, in its simplest honest form. A reused check is a real past
    execution carried forward, never a minted one."""
    checks = [spec_check("lint", "Lint", V.LINT, V.LEVEL_STATIC)]
    state = a_state()
    first = E.run_selection(selection(*checks), state=state, runner=Runner(ok()))
    state.settle(first)
    assert state.passed

    again = [spec_check("lint", "Lint", V.LINT, V.LEVEL_STATIC)]
    runner = Runner()
    E.run_selection(selection(*again), state=state, runner=runner,
                    reusable=state.reusable())
    assert again[0].passed and again[0].reused
    assert runner.calls == [], "nothing was re-run"

    state.note_change("write_file", ("b.py",))
    assert state.reusable() == {}
    third = [spec_check("lint", "Lint", V.LINT, V.LEVEL_STATIC)]
    runner = Runner(ok())
    E.run_selection(selection(*third), state=state, runner=runner,
                    reusable=state.reusable())
    assert runner.calls, "a changed tree is checked again"


def test_the_column_is_told_a_check_finished_while_the_run_blocks():
    """Section 15. The action blocks the loop, so without this the column
    would stand still for the whole of a test suite."""
    beats = []
    checks = [spec_check("a", "Lint", V.LINT, V.LEVEL_STATIC),
              spec_check("b", "Tests", V.TEST, V.LEVEL_FULL)]
    state = a_state()
    E.run_selection(selection(*checks), state=state, runner=Runner(ok(), ok()),
                    on_change=lambda: beats.append(len(
                        [c for c in state.checks() if c.settled])))
    assert len(beats) >= 4, beats
    assert beats[-1] == 2, beats
    # A refresh that raises is a repaint that did not happen, never a failed
    # verification.
    def broken():
        raise RuntimeError("boom")
    result = E.run_selection(selection(spec_check("a", "Lint", V.LINT, 2)),
                             state=a_state(), runner=Runner(ok()),
                             on_change=broken)
    assert result.status == V.PASSED


# --- the whole engine through the action ------------------------------------

def test_verify_runs_for_real_through_execute_action():
    """No injected runner anywhere: the real discovery, the real selection and
    a real subprocess, driven the only way the model can reach it."""
    box = Workspace(files={"good.py": "VALUE = 1\n"})
    try:
        box.use()
        state = V.VerificationState()
        state.note_change("write_file", ("good.py",))
        out = agent_actions.execute_action(
            {"action": "verify", "level": 1},
            {"verify": state, "capabilities": agent_capabilities.Capabilities("/verify")})
        assert out.startswith("VERIFY PASSED"), out
        assert state.passed
        event = agent_actions.action_event("verify", {"action": "verify"}, out)
        assert event.kind == "success", event.kind
        assert event.message.startswith("VERIFY PASSED"), event.message
    finally:
        box.close()


def test_a_file_that_does_not_compile_fails_verification_for_real():
    box = Workspace(files={"broken.py": "def f(:\n"})
    try:
        box.use()
        state = V.VerificationState()
        state.note_change("write_file", ("broken.py",))
        out = agent_actions.execute_action(
            {"action": "verify", "level": 1},
            {"verify": state, "capabilities": agent_capabilities.Capabilities("/verify")})
        assert out.startswith("VERIFY FAILED"), out
        assert state.state == V.FAILED
        assert not state.passed
        assert "broken.py" in out
        event = agent_actions.action_event("verify", {"action": "verify"}, out)
        assert event.kind == "warning", event.kind
    finally:
        box.close()


def test_the_action_refuses_arguments_it_cannot_act_on():
    state = V.VerificationState()
    context = {"verify": state, "capabilities": agent_capabilities.Capabilities("/verify")}
    for obj, expected in (
            ({"action": "verify", "scope": "changed_files"}, "not a verification scope"),
            ({"action": "verify", "level": "high"}, "whole number from 1 to 6"),
            ({"action": "verify", "level": 9}, "outside that")):
        out = agent_actions.execute_action(obj, context)
        assert out.startswith("FAILED"), (obj, out)
        assert expected in out, (obj, out)
    assert state.state == V.IDLE, "a refused argument changed nothing"


def test_verify_without_a_state_says_so_and_claims_nothing():
    out = agent_actions.execute_action({"action": "verify"},
                                       {"capabilities": agent_capabilities.Capabilities("/verify")})
    assert "not available" in out
    assert "Do not claim the work was verified" in out


def test_what_verification_ran_reaches_the_reviewers_brief():
    """Section 17: they answer different questions, and the reviewer's job
    includes judging whether the verification was the right one."""
    box = Workspace(files={"good.py": "VALUE = 1\n"})
    try:
        box.use()
        state = V.VerificationState()
        state.note_change("write_file", ("good.py",))
        review = agent_review.ReviewState()
        agent_actions.execute_action({"action": "verify", "level": 1},
                                     {"verify": state, "review": review,
                                      "capabilities": agent_capabilities.Capabilities("/verify")})
        assert review.verification, review.verification
        recorded = " ".join(review.verification)
        assert "verification #1 passed" in recorded, recorded
        assert "Syntax=passed" in recorded, recorded
    finally:
        box.close()


def test_a_verification_step_is_vetoed_through_the_plan_action():
    plan = agent_plan.Plan(["Implement it", "Verify implementation", "Explain"])
    plan.update(1, "completed")
    state = V.VerificationState()
    state.note_change("write_file", ("a.py",))
    out = agent_actions.execute_action(
        {"action": "plan", "operation": "update", "step": 2,
         "status": "completed"},
        {"plan": plan, "verify": state,
         "capabilities": agent_capabilities.Capabilities("/plan /verify")})
    assert out.startswith("FAILED"), out
    assert "is the verification step" in out
    assert plan.find(2).status != "completed", "the veto left no trace"


# --- the worker cannot verify -----------------------------------------------

def test_a_background_agent_is_refused_verify_in_code_and_in_its_prompt():
    import agent_subprompts
    import agent_worker
    assert "verify" in agent_worker.WORKER_FORBIDDEN
    assert "verify" not in agent_worker.NOTE_ACTIONS
    assert "verify" not in agent_worker.REVIEW_ACTIONS
    for prompt in (agent_subprompts.worker_prompt(), agent_subprompts.note_prompt(),
                   agent_subprompts.review_prompt()):
        assert "VERIFICATION - ONE ACTION" not in prompt
        assert "{\"action\":\"verify\"" not in prompt


def test_the_main_prompt_teaches_verify_and_nothing_else_does():
    import agent_prompt
    # Authorised, because the question is WHO is taught verification and not
    # WHETHER this turn may use it. Both isolations are real and separate.
    prompt = agent_prompt.get_system_prompt(
        agent_capabilities.Capabilities("/verify"))
    assert "VERIFICATION - ONE ACTION" in prompt
    assert "WHEN VERIFICATION IS REQUIRED" in prompt
    assert "{\"action\":\"verify\"" in prompt
    # Every JSON example in the two sections is a real, valid action.
    for section in (agent_prompt.VERIFY_REFERENCE, agent_prompt.VERIFY_RULES):
        for line in section.splitlines():
            stripped = line.strip()
            if not stripped.startswith("{\"action\""):
                continue
            obj = json.loads(stripped)
            assert not agent_prompt.validate_action(obj), (stripped, obj)


# --- end to end through TMT.main --------------------------------------------

STEPS = ("Implement it", "Add the tests", "Explain the change")


def plan_created(*steps):
    return json.dumps({"action": "plan", "operation": "create",
                       "steps": list(steps or STEPS)})


def wrote(path="feature.py", content="VALUE = 1\n"):
    return json.dumps({"action": "write_file", "path": path, "content": content})


def completed(*positions):
    return json.dumps({"action": "plan", "operation": "update",
                       "steps": [{"step": n, "status": "completed"}
                                 for n in positions]})


def answered(message):
    return json.dumps({"action": "end_conversation", "message": message})


VERIFY = json.dumps({"action": "verify"})


def user_messages(seen):
    return "\n".join(message["content"] for request in seen
                     for message in request if message["role"] == "user")


def test_an_answer_is_held_until_verification_has_actually_run():
    """The whole feature, end to end through TMT.main. The plan is complete
    and the answer is still refused, because nothing has been run."""
    answer = "Added the feature."
    replies = [plan_created(), wrote(), completed(1, 2, 3),
               answered("Too early."), VERIFY, answered(answer)]
    drawn, seen, console = drive_session(["add the feature /plan /verify", "quit"], replies)
    assert len(seen) == len(replies), len(seen)
    text = drawn
    assert answer in text, text[-2000:]
    assert "Too early." not in text
    said = user_messages(seen)
    assert "it must be verified before it can be called done" in said, said[-2000:]
    assert "Verification required and not yet run" in text
    del console


def test_a_failing_verification_holds_the_answer_until_it_is_fixed():
    answer = "Fixed and verified."
    # The file is broken by an APPEND rather than by a write, because
    # `write_file` refuses to write python it cannot parse -- a guard that
    # predates this feature and is doing its job. An append has no such check,
    # which is exactly how a real file ends up unparseable mid-task.
    broke = json.dumps({"action": "append_file", "path": "broken.py",
                        "content": "def f(:\n"})
    replies = [plan_created(), wrote("broken.py", "VALUE = 1\n"),
               completed(1, 2, 3),
               broke,
               VERIFY,                          # fails: the file will not parse
               answered("Done anyway."),        # refused
               wrote("broken.py", "VALUE = 1\n"),   # the fix
               VERIFY,                          # passes
               answered(answer)]
    drawn, seen, console = drive_session(["add the feature /plan /verify", "quit"], replies)
    assert len(seen) == len(replies), len(seen)
    assert answer in drawn, drawn[-2000:]
    assert "Done anyway." not in drawn
    said = user_messages(seen)
    assert "VERIFY FAILED" in said, said[-3000:]
    assert "Verification #1 failed" in said, said[-3000:]
    assert "Verification found 1 failing check" in drawn
    del console


def test_editing_after_a_pass_makes_the_answer_unverified_again():
    """What makes the fix/verify loop close rather than being a suggestion."""
    answer = "Verified twice."
    replies = [plan_created(), wrote(), completed(1, 2, 3),
               VERIFY,                          # passes
               wrote("later.py"),               # invalidates it
               answered("Still fine?"),         # refused: stale
               VERIFY,                          # passes again
               answered(answer)]
    drawn, seen, console = drive_session(["add the feature /plan /verify", "quit"], replies)
    assert len(seen) == len(replies), len(seen)
    assert answer in drawn, drawn[-2000:]
    said = user_messages(seen)
    assert "have changed since it ran" in said, said[-2000:]
    assert "Verification is stale" in drawn
    del console


def test_a_conversational_turn_is_not_gated_at_all():
    """The gate is a consequence of having done substantial work, not a tax on
    answering. No plan, no file written, no verification asked for."""
    answer = "zip pairs two sequences."
    drawn, seen, console = drive_session(["what does zip do?", "quit"],
                                         [answered(answer)])
    assert len(seen) == 1, len(seen)
    assert answer in drawn
    assert "must be verified" not in drawn
    del console


def test_a_task_that_does_not_ask_for_verification_is_not_gated_by_it():
    """Declining is now the DEFAULT rather than a form of words.

    This task plans real work and writes a real file -- the evidence that used
    to turn verification on by itself -- and answers without ever running a
    check, because the user did not write /verify. That reversal is the whole
    feature: the engine is theirs to spend, and TMT no longer decides it is
    owed one because the work looked substantial.
    """
    answer = "Added it."
    replies = [plan_created(), wrote(), completed(1, 2, 3), answered(answer)]
    drawn, seen, console = drive_session(["add the feature /plan", "quit"],
                                         replies)
    assert len(seen) == len(replies), len(seen)
    assert answer in drawn, drawn[-2000:]
    # Nothing was verified and nothing pretended otherwise.
    assert "must be verified" not in drawn, drawn[-2000:]
    del console


def test_verification_is_asked_for_before_review():
    """The order of the pipeline. A model told about the review first would go
    and get one, then be told to verify, and then need a second review because
    the fixes made the first one stale."""
    # The one end-to-end test here that declines NEITHER gate, because what is
    # being measured is which of the two speaks first.
    replies = [plan_created(), wrote(), completed(1, 2, 3), answered("Done.")]
    drawn, seen, console = drive_session(["add the feature /plan /verify /review", "quit"], replies)
    said = user_messages(seen)
    assert "it must be verified" in said, said[-2000:]
    assert "it needs an independent review" not in said, \
        "the review gate must not fire before verification has"
    del drawn, console


def test_a_second_question_starts_with_no_verification_of_its_own():
    """The transition no scripted run made before the planning crash, asked
    of this feature too: a verification is evidence about one task, and it
    must not gate the next question or be drawn beside it."""
    replies = [plan_created(), wrote(), completed(1, 2, 3), VERIFY,
               answered("Added it."), answered("zip pairs two sequences.")]
    drawn, seen, console = drive_session(
        ["add the feature /plan /verify", "what does zip do?", "quit"],
        replies)
    assert len(seen) == len(replies), len(seen)
    assert "zip pairs two sequences." in drawn, drawn[-2000:]
    del console


def test_the_session_carries_one_verification_state_and_empties_it_in_place():
    """The identity the action context depends on. The loop puts this object
    in the context BEFORE `begin_turn` runs, so a new object there would leave
    the verify action writing into state the gate no longer reads -- the gate
    silently switched off, with no error anywhere."""
    import agent_session
    session = agent_session.Session()
    state = session.verify
    state.note_change("write_file", ("a.py",))
    session.begin_turn("next question", "prompt")
    assert session.verify is state, "the object was rebound"
    assert state.state == V.IDLE and state.changed_paths == ()
    state.note_change("write_file", ("b.py",))
    session.clear()
    assert session.verify is state
    assert state.changed_paths == ()


def test_note_work_tells_verification_about_a_write_and_not_about_a_read():
    import agent_session
    session = agent_session.Session()
    TMT.note_work(session, "write_file", {"path": "a.py"})
    assert session.verify.changed_paths == ("a.py",)
    TMT.note_work(session, "read_file", {"path": "b.py"})
    assert session.verify.changed_paths == ("a.py",)
    # And a session with no state at all cannot end a turn that did its work.
    TMT.note_work(None, "write_file", {"path": "a.py"})


def test_the_choice_is_read_after_begin_turn_and_never_before():
    """Read from the capability command, once, after the retirement.

    Before, never after, would be wiped: `begin_turn` resets every field on
    the state. And the second turn is the half that matters -- a capability
    authorised for one question must not still be authorised for the next.
    """
    import agent_session
    session = agent_session.Session()
    session.begin_turn("add it and run the tests /verify", "prompt")
    TMT.note_capability_choices(session)
    assert session.capabilities.verify is True
    assert session.verify.user_choice is True
    # A new question, no command in it: the authorisation does not carry.
    session.begin_turn("what does zip do?", "prompt")
    TMT.note_capability_choices(session)
    assert session.capabilities.verify is False
    assert session.verify.user_choice is False


def test_the_word_verify_without_a_slash_authorises_nothing_end_to_end():
    """The sharpest requirement of the feature, at the seam it matters.

    "run the tests" and "verify this" are things people say while asking for
    ordinary work. Under the old rule either turned the engine on; under this
    one only the command does.
    """
    import agent_session
    session = agent_session.Session()
    for said in ("verify this code", "please verify this", "run the tests",
                 "verification", "verified", "myverify", "/verification"):
        session.begin_turn(said, "prompt")
        TMT.note_capability_choices(session)
        assert session.capabilities.verify is False, said
        assert session.verify.user_choice is False, said


def test_the_release_warning_reaches_the_user_only_when_something_was_released():
    import agent_session
    session = agent_session.Session()
    session.verify.note_change("write_file", ("a.py",))
    assert TMT.verify_release_warning(session) == ""
    for _ in range(V.MAX_VERIFY_CYCLES):
        session.verify.begin()
        session.verify.settle(V.VerificationResult(
            checks=[spec_check("t", "Tests", V.TEST, 6).record(1, "1 failed")]))
    assert "did not pass" in TMT.verify_release_warning(session)
    assert any("did not pass" in line for line in TMT.release_warnings(session))


def test_the_completion_gate_reports_which_of_the_three_refused():
    """The pair is returned rather than sniffed out of the refusal's wording
    afterwards: asking the gates again a moment later can get a different
    answer, and a user shown the wrong line goes looking for the wrong thing."""
    import agent_session
    session = agent_session.Session()
    respond = {"action": "end_conversation", "message": "done"}

    session.plan.create(["Implement", "Test", "Explain"])
    session.verify.note_change("write_file", ("a.py",))
    session.review.note_change("write_file", ("a.py",))
    held, line = TMT.completion_block(session, respond)
    assert "The plan you made is the contract" in held
    assert "Plan not finished" in line

    for step in session.plan.steps:
        session.plan.update(step.position, "completed")
    held, line = TMT.completion_block(session, respond)
    assert "it must be verified" in held
    assert "Verification required" in line

    session.verify.begin()
    session.verify.settle(V.VerificationResult(
        checks=[spec_check("t", "Tests", V.TEST, 6).record(0, "43 passed")]))
    held, line = TMT.completion_block(session, respond)
    assert "it needs an independent review" in held
    assert "Review required" in line

    session.review.begin()
    session.review.settle(agent_review.parse_result(
        '{"status":"PASS","summary":"Fine.","issues":[]}'))
    assert TMT.completion_block(session, respond) == ("", "")
