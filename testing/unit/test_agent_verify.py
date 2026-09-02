"""The verification state: what can make a check pass, and what cannot.

The whole feature rests on one property, and most of this file is about it:
**only a process exiting zero can put a check into `passed`.** Everything else
-- the result's status, the run's state, the gate on the final answer -- is
derived from that, so if it can be forged anywhere then none of the rest means
anything.

The other half is the gate. Section 18 of the brief asks for a final answer to
be blocked unless the plan is complete AND verification passed AND review
passed, and for verification's own failures to be told apart: FAILED, ERROR
and CANCELLED are three different things and none of them is a pass.

Nothing here runs a subprocess. The engine's tests do that with an injected
runner; this file is about the state machine, and a state machine that needed
a test suite to run in order to be tested would be the wrong shape.
"""

import io

import agent_panel
import agent_plan
import agent_review
import agent_verify as V


# --- helpers ---------------------------------------------------------------

def check(identifier="c1", name="Tests", category=V.TEST, level=V.LEVEL_FULL,
          command=("python", "-c", "pass")):
    return V.VerificationCheck(identifier, name, category, level, command)


def passing(name="Tests", output="43 passed in 1.2s"):
    return check(name.lower(), name).record(0, output)


def failing(name="Tests", output="2 failed, 41 passed"):
    return check(name.lower(), name).record(1, output)


def erroring(name="Tests", reason="it did not finish within 300 seconds"):
    return check(name.lower(), name).fail_to_run(reason)


def skipped(name="Lint", reason="'ruff' is not installed"):
    return check(name.lower(), name).skip(reason)


def state_after(*checks):
    """A VerificationState that has run once and settled on these checks."""
    state = V.VerificationState()
    state.note_change("write_file", ("a.py",))
    state.begin()
    state.settle(V.VerificationResult(checks=list(checks)))
    return state


def complete_plan(*titles):
    plan = agent_plan.Plan(list(titles) or ["Implement", "Test", "Explain"])
    for step in plan.steps:
        plan.update(step.position, "completed")
    return plan


def passed_review():
    review = agent_review.ReviewState()
    # The change is recorded BEFORE the review settles, which is the order the
    # loop uses. Recording it afterwards would make the review stale the
    # moment it passed, which is correct behaviour and the wrong setup.
    review.note_change("write_file", ("a.py",))
    review.begin()
    review.settle(agent_review.parse_result(
        '{"status":"PASS","summary":"Read the diff.","issues":[]}'))
    return review


class Tty(io.StringIO):
    def isatty(self):
        return True


# --- the one property everything rests on ----------------------------------

def test_a_check_can_only_be_passed_by_a_process_that_exited_zero():
    """Section 32, implemented as an absence. There is no setter, no
    constructor argument and no operation that reaches `passed` without a real
    exit code -- and `record` refuses anything that is not an integer, because
    the failure being guarded against is exactly somebody wiring model text
    into it."""
    one = check()
    assert one.status == V.CHECK_PENDING
    assert not one.passed

    # Every non-integer shape a caller might reach for, refused.
    for forged in ("0", "PASS", "passed", None, 0.0, [0], {"exit": 0}):
        raised = None
        try:
            one.record(forged)
        except TypeError as error:
            raised = error
        assert raised is not None, "record(%r) was accepted" % (forged,)
        assert "actually ran" in str(raised), str(raised)
    assert one.status == V.CHECK_PENDING, one.status

    # True is an int in Python and reads like a pass. It is refused by name,
    # because as an exit code it is 1, which is a FAILURE.
    raised = None
    try:
        one.record(True)
    except TypeError as error:
        raised = error
    assert raised is not None, "record(True) was accepted"

    # Neither of the other two transitions can reach it either.
    assert check().skip("nothing to do").status == V.CHECK_SKIPPED
    assert check().fail_to_run("no").status == V.CHECK_ERROR

    # And the only thing that does.
    assert check().record(0).status == V.CHECK_PASSED
    assert check().record(1).status == V.CHECK_FAILED
    assert check().record(137).status == V.CHECK_FAILED


def test_a_result_status_is_computed_from_its_checks_and_cannot_be_given():
    """There is no `status` parameter to pass the wrong thing to. A caller
    that wanted to assert a pass has nowhere to assert it."""
    assert "status" not in V.VerificationResult.__slots__
    raised = None
    try:
        V.VerificationResult(checks=[failing()], status=V.PASSED)
    except TypeError as error:
        raised = error
    assert raised is not None, "a status could be passed in"

    assert V.VerificationResult(checks=[passing()]).status == V.PASSED
    assert V.VerificationResult(checks=[passing(), failing()]).status == V.FAILED
    # ERROR outranks FAILED: a run that does not know what the tests would
    # have said is not a run that found a type error.
    assert V.VerificationResult(
        checks=[failing(), erroring()]).status == V.ERROR
    # Every check skipped is not a pass. There is no evidence in it at all.
    empty = V.VerificationResult(checks=[skipped()])
    assert empty.status == V.ERROR
    assert empty.nothing_to_run
    assert V.VerificationResult(checks=[]).nothing_to_run


def test_settle_takes_a_result_and_nothing_else():
    """`agent_review.settle`'s guard, applied to the other half of the gate.
    Four shapes of "verification passed" that a model might produce, and none
    of them moves the state."""
    state = V.VerificationState()
    state.begin()
    for forged in ("VERIFY PASSED", "passed", {"status": "passed"},
                   [passing()], V.PASSED):
        raised = None
        try:
            state.settle(forged)
        except TypeError as error:
            raised = error
        assert raised is not None, "settle(%r) was accepted" % (forged,)
        assert "actually ran" in str(raised), str(raised)
    assert state.state == V.PLANNING, state.state
    assert not state.passed


def test_a_reused_result_is_carried_evidence_and_never_minted():
    """The cache cannot manufacture a pass: `reuse` takes a check that really
    passed, and refuses anything else."""
    fresh = check()
    fresh.reuse(passing())
    assert fresh.passed and fresh.reused
    assert fresh.detail() == "reused"
    for forged in (failing(), erroring(), skipped(), check(), None, "passed"):
        raised = None
        try:
            check().reuse(forged)
        except TypeError as error:
            raised = error
        assert raised is not None, "reuse(%r) was accepted" % (forged,)


# --- the four statuses stay four -------------------------------------------

def test_pass_fail_error_and_skipped_are_never_collapsed():
    """Section 32. A failure is evidence that the code is wrong; an error is
    the absence of evidence; a skip is a hole with a stated shape. Telling a
    model to go and fix a bug that nothing found would be the cost of
    collapsing them."""
    assert failing().status != erroring().status != skipped().status
    result = V.VerificationResult(checks=[passing(), failing(), erroring(),
                                          skipped()])
    counts = result.counts()
    assert counts == {V.CHECK_PASSED: 1, V.CHECK_FAILED: 1,
                      V.CHECK_ERROR: 1, V.CHECK_SKIPPED: 1}, counts
    assert len(result.ran()) == 2, "only the two that executed actually ran"
    text = result.describe()
    assert "is not installed" in text or "not installed" in text
    assert "did not finish" in text


def test_an_errored_check_says_what_stopped_it_rather_than_blaming_the_code():
    timed_out = erroring(reason="it did not finish within 300 seconds")
    assert timed_out.errored and not timed_out.failed
    assert "did not finish" in timed_out.detail()
    advice = V.VerificationResult(checks=[timed_out]).recommendations()
    assert any("could not be run" in line for line in advice), advice


# --- what a check reports ---------------------------------------------------

def test_a_command_that_says_nothing_about_itself_is_not_quoted_at_it():
    """The bug this rule exists for, found by running it: `python -m
    py_compile` exits 0 and prints nothing about itself, so its last line on
    this repository was a SyntaxWarning quoting somebody's regex -- which read
    as a finding on a check that had passed silently."""
    quiet = check().record(0, 'ANSI_RE = re.compile("\\033\\[[0-9;]*m")')
    assert quiet.passed
    assert quiet.detail() == "passed", quiet.detail()
    assert "it reported" not in quiet.describe()

    # A real summary still comes through, in the runner's own words.
    loud = check().record(0, "collected 43 items\n\n43 passed in 1.20s")
    assert loud.detail() == "43 passed in 1.20s", loud.detail()
    assert "it reported: 43 passed" in loud.describe()

    assert V.looks_like_a_summary("2 failed, 41 passed")
    assert V.looks_like_a_summary("ok  4 tests")
    assert not V.looks_like_a_summary("1.2.3")
    assert not V.looks_like_a_summary("Traceback (most recent call last):")


def test_a_long_output_keeps_its_END_because_that_is_where_the_failure_is():
    """The opposite of what TMT does with a diff, and right for the opposite
    reason: a diff's first lines say what file it is about, and a runner's
    last lines say what happened."""
    body = "\n".join("noise line %d" % n for n in range(4000))
    one = check().record(1, body + "\nAssertionError: expected 401, got 200")
    assert "AssertionError: expected 401" in one.output
    assert "earlier character(s) of output omitted" in one.output
    assert len(one.output) < len(body)
    assert "AssertionError: expected 401" in one.failure_report()


def test_a_failure_report_carries_what_another_cycle_needs():
    """Section 20: actionable. The command, the exit code and the output."""
    one = check("t", "Targeted tests").record(
        2, "tests/auth/test_refresh.py::test_expired_token FAILED")
    text = one.failure_report()
    assert "Targeted tests FAILED" in text
    assert "command: python -c pass" in text
    assert "exit code: 2" in text
    assert "test_expired_token" in text


# --- the state machine ------------------------------------------------------

def test_every_state_in_the_brief_is_reachable_and_only_one_is_a_pass():
    state = V.VerificationState()
    assert state.state == V.IDLE
    assert not state

    assert state.begin() == ""
    assert state.state == V.PLANNING
    assert state.running

    state.running_now([check()])
    assert state.state == V.RUNNING
    assert state.running

    state.settle(V.VerificationResult(checks=[passing()]))
    assert state.state == V.PASSED and state.passed

    state = V.VerificationState()
    state.begin()
    state.settle(V.VerificationResult(checks=[failing()]))
    assert state.state == V.FAILED and not state.passed

    state = V.VerificationState()
    state.begin()
    state.fail("the checks could not be chosen")
    assert state.state == V.ERROR and not state.passed

    state = V.VerificationState()
    state.begin()
    state.cancel("the user stopped it with Ctrl-C")
    assert state.state == V.CANCELLED and not state.passed
    assert V.SETTLED_PASS == (V.PASSED,), "exactly one state is a pass"


def test_an_error_costs_no_cycle_and_a_settled_run_does():
    """The limit bounds the fix/verify LOOP. A run that never reported has not
    been round it, and charging it would let two broken invocations exhaust a
    task's whole budget without a single check being made."""
    state = V.VerificationState()
    state.begin()
    state.fail("the runner could not start")
    assert state.cycles == 0
    state.begin()
    state.fail("again")
    assert state.cycles == 0
    state.begin()
    state.settle(V.VerificationResult(checks=[failing()]))
    assert state.cycles == 1


def test_a_second_run_cannot_start_while_one_is_going():
    state = V.VerificationState()
    assert state.begin() == ""
    held = state.begin()
    assert "already running" in held, held


def test_the_cycle_limit_refuses_a_fourth_run_and_says_so():
    state = V.VerificationState()
    for _ in range(V.MAX_VERIFY_CYCLES):
        assert state.begin() == ""
        state.settle(V.VerificationResult(checks=[failing()]))
    assert state.limit_reached
    held = state.begin()
    assert "LOOP LIMIT REACHED" in held, held


def test_retire_is_total_and_empties_in_place():
    """The lesson `Plan.retire` was written for. `begin_turn` and
    `Session.clear` both call it, neither catches anything, and a retirement
    that could raise would take the session with it."""
    state = V.VerificationState()
    state.note_change("write_file", ("a.py",))
    state.begin()
    state.settle(V.VerificationResult(checks=[passing()]))
    state.note_user_choice(True)
    identity = id(state)
    state.retire()
    assert id(state) == identity
    assert state.state == V.IDLE
    assert state.cycles == 0
    assert state.changed_paths == ()
    assert state.user_choice is None
    assert not state


def test_retire_is_not_an_operation_a_model_can_ask_for():
    """`Plan.retire` is kept out of OPERATIONS and out of the prompt for
    exactly this reason. There is no `verify` operation at all, so the
    equivalent property here is that nothing in the action's own vocabulary
    reaches the state -- checked by the action tests -- and that the prompt
    never names it."""
    import agent_prompt
    assert "retire" not in agent_prompt.VERIFY_REFERENCE
    assert "retire" not in agent_prompt.VERIFY_RULES


# --- staleness --------------------------------------------------------------

def test_a_pass_goes_stale_the_moment_the_code_moves_under_it():
    """What makes the fix/verify loop close rather than being a suggestion:
    without it a model could pass verification, then edit freely, then
    answer."""
    state = state_after(passing())
    assert state.passed and not state.stale

    state.note_change("write_file", ("b.py",))
    assert state.stale
    assert not state.passed, "a stale pass is not a pass"
    assert state.state == V.PASSED, "the run really did pass; that is recorded"
    assert state.display == V.STALE, "but it is not DRAWN as a pass"

    # A fresh run accounts for everything that had changed.
    state.begin()
    state.settle(V.VerificationResult(checks=[passing()]))
    assert not state.stale and state.passed


def test_a_stale_pass_cannot_be_reused_as_a_cache():
    """Section 28's must: a result may never remain usable after changes that
    could invalidate it."""
    state = state_after(passing("Lint"))
    assert list(state.reusable()) == ["lint"]
    state.note_change("write_file", ("b.py",))
    assert state.reusable() == {}, "nothing is reused once anything has moved"


# --- what makes a task need verifying ---------------------------------------

def test_verification_is_required_from_evidence_the_model_cannot_argue_with():
    plan = complete_plan("a", "b", "c")
    state = V.VerificationState()
    assert not state.is_required(plan), "no file written, no verification"
    state.note_change("write_file", ("a.py",))
    assert state.is_required(plan)
    assert not state.is_required(agent_plan.Plan(["one", "two"])), \
        "a two-step plan is not substantial work"
    assert not state.is_required(None)


def test_the_users_own_words_win_in_both_directions():
    plan = complete_plan("a", "b", "c")
    state = V.VerificationState()
    state.note_user_choice(True)
    assert state.is_required(plan), "asked for, with nothing written"
    state.note_change("write_file", ("a.py",))
    state.note_user_choice(False)
    assert not state.is_required(plan), "declined, with work done"
    state.note_user_choice(None)
    assert state.is_required(plan), "silence falls back to the evidence"


def test_requests_verification_answers_three_ways():
    assert V.requests_verification("add the feature and run the tests") is True
    assert V.requests_verification("verify it works") is True
    assert V.requests_verification("make sure it works") is True
    assert V.requests_verification("commit it, no verification needed") is False
    assert V.requests_verification("do not run the tests") is False
    assert V.requests_verification("skip verification") is False
    assert V.requests_verification("what does zip do?") is None
    assert V.requests_verification("") is None
    # Declining is checked first, so a sentence with both halves in it is
    # read as the one the user actually wrote.
    assert V.requests_verification("run the tests? no, verification is not "
                                   "needed") is False


# --- the gate ---------------------------------------------------------------

def test_the_gate_holds_every_state_that_is_not_a_pass():
    """Section 18, one row per line. The plan is complete in all of them, so
    what is being measured is verification alone."""
    plan = complete_plan()

    def blocked(state):
        return bool(V.refusal(state, plan, "end_conversation"))

    idle = V.VerificationState()
    idle.note_change("write_file", ("a.py",))
    assert blocked(idle), "required and never run"

    running = V.VerificationState()
    running.note_change("write_file", ("a.py",))
    running.begin()
    assert blocked(running)

    assert blocked(state_after(failing()))
    assert not blocked(state_after(passing()))

    errored = V.VerificationState()
    errored.note_change("write_file", ("a.py",))
    errored.begin()
    errored.fail("the runner could not start")
    assert blocked(errored)

    cancelled = V.VerificationState()
    cancelled.note_change("write_file", ("a.py",))
    cancelled.begin()
    cancelled.cancel("Ctrl-C")
    assert blocked(cancelled)

    stale = state_after(passing())
    stale.note_change("write_file", ("b.py",))
    assert blocked(stale)


def test_the_gate_only_ever_holds_a_terminal_action():
    """Holding a read or a patch would stop the model doing the very thing it
    is being told to do."""
    state = state_after(failing())
    plan = complete_plan()
    assert V.refusal(state, plan, "end_conversation")
    # `send_message` is on this list rather than beside the ending, and that
    # is the point of the rename: it talks to the user without finishing, so
    # gating it would silence the model through exactly the wait -- a whole
    # test suite running -- that the user most wants narrated.
    for action in ("read_file", "write_file", "verify", "review", "plan",
                   "bash", "git_commit", "send_message"):
        assert V.refusal(state, plan, action) == "", action


def test_the_gate_releases_rather_than_trapping_a_session():
    """Two releases, each for its own reason, and both say so out loud."""
    plan = complete_plan()

    # The cycle limit. Three rounds and the answer goes out carrying what the
    # last run objected to -- silence would be the worse failure.
    limited = V.VerificationState()
    limited.note_change("write_file", ("a.py",))
    for _ in range(V.MAX_VERIFY_CYCLES):
        limited.begin()
        limited.settle(V.VerificationResult(checks=[failing()]))
    assert V.refusal(limited, plan, "end_conversation") == ""
    assert "did not pass" in V.limit_release(limited)

    # A repository with nothing to run. There is no evidence to be had, and a
    # verifier that cannot verify holding finished work hostage is the worst
    # outcome available.
    barren = V.VerificationState()
    barren.note_change("write_file", ("a.py",))
    barren.begin()
    barren.settle(V.VerificationResult(checks=[skipped()]))
    assert V.refusal(barren, plan, "end_conversation") == ""
    assert "nothing it could run" in V.limit_release(barren)


def test_a_barren_repository_is_gated_again_once_something_changes():
    """The release is about the run that happened, not a licence for the rest
    of the task: an edit after it makes the answer unverified again."""
    plan = complete_plan()
    state = V.VerificationState()
    state.note_change("write_file", ("a.py",))
    state.begin()
    state.settle(V.VerificationResult(checks=[skipped()]))
    assert V.refusal(state, plan, "end_conversation") == ""
    state.note_change("write_file", ("b.py",))
    assert V.refusal(state, plan, "end_conversation") != "", \
        "an edit after a released run is not covered by it"


def test_a_state_object_that_raises_lets_the_answer_through():
    """The direction every other guard in that loop fails in. A broken
    verifier holding finished work hostage is the worst outcome available."""
    class Broken:
        def is_required(self, plan=None):
            raise RuntimeError("boom")

    assert V.refusal(Broken(), complete_plan(), "end_conversation") == ""
    assert V.limit_release(Broken()) == ""
    assert "not finished" in V.held_line(Broken())
    assert V.refusal(None, complete_plan(), "end_conversation") == ""


def test_the_held_line_names_the_reason_the_user_can_see():
    plan = complete_plan()
    assert "not yet run" in V.held_line(V.VerificationState(), plan)
    assert "failing check" in V.held_line(state_after(failing()), plan)
    stale = state_after(passing())
    stale.note_change("write_file", ("b.py",))
    assert "stale" in V.held_line(stale, plan)


# --- the plan's verification step -------------------------------------------

def test_a_verification_step_cannot_be_completed_by_saying_it_is():
    plan = agent_plan.Plan(["Implement it", "Verify implementation",
                            "Explain the change"])
    plan.update(1, "completed")
    state = V.VerificationState()
    state.note_change("write_file", ("a.py",))
    update = {"operation": "update", "step": 2, "status": "completed"}
    vetoed = V.plan_veto(state, plan, update)
    assert "is the verification step" in vetoed, vetoed
    assert "not been run" in vetoed

    state.begin()
    state.settle(V.VerificationResult(checks=[passing()]))
    assert V.plan_veto(state, plan, update) == "", "a passed run allows it"


def test_the_veto_is_a_refinement_and_the_gate_is_the_guarantee():
    """`is_verify_step` matches the title, which a model can avoid by naming
    the step something else. That is exactly why it is not the guarantee: the
    answer is still refused by `refusal`, which does not care what any step is
    called."""
    plan = agent_plan.Plan(["Implement it", "Check the work", "Explain"])
    state = V.VerificationState()
    state.note_change("write_file", ("a.py",))
    update = {"operation": "update", "step": 2, "status": "completed"}
    assert V.plan_veto(state, plan, update) == "", "the title dodges the veto"
    for step in plan.steps:
        plan.update(step.position, "completed")
    assert V.refusal(state, plan, "end_conversation") != "", \
        "and the gate refuses the answer anyway"

    assert V.is_verify_step("Verify implementation")
    assert V.is_verify_step("Final verification")
    assert not V.is_verify_step("Add tests"), \
        "adding tests is implementation, and completing it must not be refused"
    assert not V.is_verify_step("Run the tests")


def test_the_veto_never_stops_a_model_keeping_its_plan_current():
    """Every failure lets the update through. A veto is a refinement on the
    plan's display; a broken one must not stop the plan being maintained."""
    plan = agent_plan.Plan(["Verify it"])
    assert V.plan_veto(None, plan, {"operation": "update", "step": 1,
                                    "status": "completed"}) == ""
    assert V.plan_veto(V.VerificationState(), None, {}) == ""
    assert V.plan_veto(V.VerificationState(), plan, "not an object") == ""
    # A non-update operation is never vetoed.
    state = V.VerificationState()
    state.note_change("write_file", ("a.py",))
    assert V.plan_veto(state, plan, {"operation": "create",
                                     "steps": ["Verify it"]}) == ""


def test_a_barren_repository_does_not_leave_a_plan_unfinishable():
    """Released by the veto for the reason it is released by the gate: there
    is nothing the model can do to make it pass, and refusing the step would
    leave the plan permanently outstanding."""
    plan = agent_plan.Plan(["Implement", "Verify it", "Explain"])
    state = V.VerificationState()
    state.note_change("write_file", ("a.py",))
    state.begin()
    state.settle(V.VerificationResult(checks=[skipped()]))
    assert V.plan_veto(state, plan, {"operation": "update", "step": 2,
                                     "status": "completed"}) == ""


# --- what the column draws --------------------------------------------------

def test_the_column_draws_nothing_for_a_task_that_never_verified():
    """The bargain the plan and review blocks already strike: the column is
    shared, and a permanent empty heading beside every conversational question
    would be worth less than the rows it costs."""
    assert agent_panel.verify_rows(None, 30) == []
    assert agent_panel.verify_rows(V.VerificationState(), 30) == []


def test_the_column_shows_a_row_per_check_with_its_own_mark():
    stream = Tty()
    state = state_after(passing("Lint"), failing("Tests"), skipped("Types"))
    rows = agent_panel.verify_rows(state, 40, stream=stream)
    text = "\n".join(agent_panel.strip_ansi(row) for row in rows)
    assert text.splitlines()[0].startswith("VERIFY 1/3"), text
    assert "✓ Lint" in text
    assert "✗ Tests" in text
    assert "– Types" in text
    assert "2 failed, 41 passed" in text, "the runner's own words"


def test_a_stale_pass_never_draws_the_tick():
    """The one way this column could actively mislead, and the bug
    `review_rows` shipped with before a test found it. A tick is the single
    glyph a reader scans for."""
    state = state_after(passing())
    stream = Tty()
    good = "\n".join(agent_panel.strip_ansi(row)
                     for row in agent_panel.verify_rows(state, 40, stream=stream))
    assert "✓" in good
    state.note_change("write_file", ("b.py",))
    assert state.state == V.PASSED, "the state machine still says passed"
    assert state.display == V.STALE, "the column asks a different question"
    stale = "\n".join(agent_panel.strip_ansi(row)
                      for row in agent_panel.verify_rows(state, 40, stream=stream))
    # The checks themselves still passed and still show their own marks; what
    # must not happen is the block reading as a verified task.
    assert "VERIFY" in stale


def test_the_block_gives_up_its_detail_before_its_names_on_a_narrow_column():
    """Section 33. The column never gives up the mark, gives up the detail
    next, and elides the name only when there is nothing else left."""
    state = state_after(passing("Targeted tests", "43 passed in 1.2s"))
    stream = Tty()
    wide = agent_panel.strip_ansi(agent_panel.verify_rows(state, 60, stream=stream)[1])
    narrow = agent_panel.strip_ansi(agent_panel.verify_rows(state, 22, stream=stream)[1])
    assert "43 passed" in wide, wide
    assert "43 passed" not in narrow, narrow
    assert "Targeted" in narrow, narrow
    for width in (12, 18, 22, 30, 45, 60, 120):
        for row in agent_panel.verify_rows(state, width, stream=stream):
            assert agent_panel.display_width(agent_panel.strip_ansi(row)) <= width, \
                (width, row)


class Cp1252(io.StringIO):
    """The encoding a plain Windows console actually reports.

    It can carry none of TMT's decoration, which is the case the ASCII
    fallbacks exist for. Kept here rather than imported so this file stays a
    unit test of one module and does not reach into another test's helpers.
    """

    @property
    def encoding(self):
        return "cp1252"

    def isatty(self):
        return False


def test_the_block_reads_with_the_colour_stripped_and_the_glyphs_gone():
    """Colour is never the message. Every status carries a mark, the marks
    stay distinct with the escapes stripped, and a console that cannot draw a
    filled circle gets a mark it can draw rather than a replacement character
    on the one row the user is looking for."""
    state = state_after(passing("Lint"), failing("Tests"), skipped("Types"),
                        erroring("Build"))

    # A stream that is not a terminal gets no colour at all.
    quiet = io.StringIO()
    text = "\n".join(agent_panel.verify_rows(state, 40, stream=quiet))
    assert "\033[" not in text
    assert "Lint" in text and "Tests" in text

    # And one that cannot encode the marks gets the ascii table. `verify_marks`
    # asks about the marks themselves, not only about decoration generally.
    console = Cp1252()
    marks = agent_panel.verify_marks(console)
    assert marks == agent_panel._VERIFY_ASCII_MARKS, marks
    plain = "\n".join(agent_panel.verify_rows(state, 40, stream=console))
    assert "✓" not in plain and "✗" not in plain, plain
    assert "+ Lint" in plain and "x Tests" in plain, plain
    for table in (agent_panel._VERIFY_MARKS, agent_panel._VERIFY_ASCII_MARKS):
        assert len(set(table.values())) == len(table), table


def test_a_state_that_raises_is_drawn_as_no_verification():
    """Decoration is never allowed to end a turn."""
    class Broken:
        def __bool__(self):
            return True

        @property
        def display(self):
            raise RuntimeError("boom")

    assert agent_panel.verify_rows(Broken(), 30) == []
    assert "could not be read" in agent_panel.verify_report(Broken())


def test_the_report_is_the_way_in_at_any_width():
    assert "not available" in agent_panel.verify_report(None)
    state = state_after(failing("Tests", "2 failed, 41 passed"))
    text = agent_panel.verify_report(state)
    assert "VERIFY FAILED" in text
    assert "2 failed, 41 passed" in text
    assert "Verification 1 of at most 3" in text


# --- the three gates side by side -------------------------------------------

def test_no_gate_excuses_another():
    """Section 18: plan complete AND verification passed AND review passed.
    Each one alone holds the answer."""
    unfinished = agent_plan.Plan(["Implement", "Test", "Explain"])
    done = complete_plan()

    assert agent_plan.refusal(unfinished, "end_conversation") != ""
    assert agent_plan.refusal(done, "end_conversation") == ""

    verified = state_after(passing())
    unverified = state_after(failing())
    assert V.refusal(unverified, done, "end_conversation") != ""
    assert V.refusal(verified, done, "end_conversation") == ""

    reviewed = passed_review()
    unreviewed = agent_review.ReviewState()
    unreviewed.note_change("write_file", ("a.py",))
    assert agent_review.refusal(unreviewed, done, "end_conversation") != ""
    assert agent_review.refusal(reviewed, done, "end_conversation") == ""

    # Only all three together let it out.
    assert (agent_plan.refusal(done, "end_conversation")
            or V.refusal(verified, done, "end_conversation")
            or agent_review.refusal(reviewed, done, "end_conversation")) == ""
