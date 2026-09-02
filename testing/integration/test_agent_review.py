"""Tests for the independent review: the state, the parser, the agent, the
column and the gate.

Five things are being protected and the fifth is the one the feature exists
for.

The RESULT PARSER is the boundary the whole guarantee sits on. Nothing else in
TMT builds a ReviewResult, and `ReviewState.settle` takes nothing else, so
every route to a passing review runs through `parse_result` over text a
reviewer agent actually produced. Every shape of malformed output is here as a
test that it becomes an ERROR rather than a pass.

The STATE is a small machine with strict rules -- a pass that goes stale when
the code moves under it, a cycle limit that releases rather than deadlocks, an
error that never reads as approval -- and each rule is here as a test that
fails when the rule is removed.

The AGENT is the note agent's loop with a different whitelist, so what is
tested here is the difference: that it cannot write, that its refusals name
the reviewer's reason rather than the note's, and that it is refused the
`review` verb itself so a reviewer cannot review its own review.

The COLUMN is asserted with the escapes stripped, because colour is
confirmation here and never the message: every state carries a mark too. The
four colours are asserted as positions on TMT's one gradient rather than as
escape sequences, so the tests describe the rule and not the palette.

The GATE is the one that matters. A final answer without a passing review is
not shown to the user, and that is enforced in the loop rather than asked for
in the prompt. The end-to-end tests drive TMT.main to prove it, including the
full review -> fail -> fix -> review -> pass cycle.

No test may block. There is no per-test timeout in this suite, so a wait that
never returned would hang the whole run rather than fail: every reviewer here
is driven by an injected model, every thread parked on an event is released in
a `finally`, and no test sleeps.

Helpers come from test_agent_menu and test_agent_cli. A second harness for the
same box or the same loop would drift from the first, and the drift would be
silent.
"""

import io
import json
import threading

import agent_actions
import agent_capabilities
import agent_config
import agent_manager
import agent_panel
import agent_plan
import agent_prompt
import agent_review
import agent_session
import agent_subprompts
import agent_worker
import TMT

from test_agent_menu import Terminal, visible
from test_agent_cli import drive_session


# --- building the pieces ----------------------------------------------------

def plan_of(*titles):
    plan = agent_plan.Plan()
    plan.create(list(titles))
    return plan


def worked_plan(*titles):
    """A three-step plan: substantial enough that a review is required."""
    return plan_of(*(titles or ("Implement it", "Test it", "Independent review")))


def state_after_work(paths=("src/thing.py",), ran=True):
    """A ReviewState carrying the evidence a real turn would have left on it."""
    review = agent_review.ReviewState()
    for path in paths:
        review.note_change("write_file", (path,))
    if ran:
        review.note_run("bash", "python run_tests.py")
    return review


def result_json(status="PASS", summary="Looked at it.", issues=(),
                requirements=(), tests="", recommendations=""):
    return {"status": status, "summary": summary, "issues": list(issues),
            "requirements": list(requirements), "tests": tests,
            "recommendations": recommendations}


def issue(severity="MAJOR", identifier="R-001", title="Something is wrong",
          description="The specifics.", **extra):
    entry = {"id": identifier, "severity": severity, "title": title,
             "description": description}
    entry.update(extra)
    return entry


def parsed(**kwargs):
    """A ReviewResult built the only way one can be built."""
    return agent_review.parse_result(json.dumps(result_json(**kwargs)))


def settled(review, **kwargs):
    review.begin()
    return review.settle(parsed(**kwargs))


def reviewer_reply(**kwargs):
    """One scripted reviewer turn: the terminal verb carrying a result object."""
    return json.dumps({"action": "internal_response",
                       "response": json.dumps(result_json(**kwargs))})


class Reviewer:
    """Replaces the reviewer's model with scripted replies, and nothing else.

    The real `agent_worker.run_review` still runs -- the whitelist, the
    refusals, the terminal verb and the manager transitions are all the
    production ones. Only `ask` is injected, which is the seam that module
    exposes for exactly this, so no test here makes a request or needs a key.
    """

    def __init__(self, *replies):
        self.replies = list(replies) or ["{}"]
        self.calls = 0
        self.briefs = []
        self._saved = None

    def ask(self, messages, on_event=None, model=None, max_tokens=None,
            quiet=False):
        self.briefs.append([dict(message) for message in messages])
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return reply

    def brief(self, index=0):
        """The task text the reviewer was given on its `index`-th request."""
        return self.briefs[index][-1]["content"]

    def __enter__(self):
        self._saved = agent_worker.run_review
        real = self._saved
        agent_worker.run_review = lambda record, manager: real(
            record, manager, ask=self.ask,
            execute=agent_actions.execute_action,
            system_prompt="stub review prompt")
        return self

    def __exit__(self, *_exc):
        agent_worker.run_review = self._saved
        return False


def run_review(review, plan=None, manager=None, obj=None, task="Do the thing.",
               capabilities="/review"):
    """One `review` action through `execute_action`, which is the only path.

    Through the dispatcher rather than through `_review` directly, for the
    reason `test_agent_toolflow` gives: an action that works perfectly and is
    not registered is an action that does not exist.

    `capabilities` authorises review by default, because that is what almost
    every test here is about: how the reviewer behaves once it has been asked
    for. It is kept apart from `task` deliberately -- the reviewer reads the
    task as the request to check the work against, and authorisation is a
    different question about the same line. Pass "" to drive it unauthorised.
    """
    context = {"manager": manager if manager is not None else agent_manager.AgentManager(),
               "plan": plan, "review": review, "task": task,
               "capabilities": agent_capabilities.Capabilities(capabilities)}
    return agent_actions.execute_action(dict(obj or {"action": "review"}), context)


# --- the result parser ------------------------------------------------------
#
# The boundary the whole guarantee sits on. Section 15 of the brief -- "review
# should not be easy to game" -- is implemented as the absence of any other
# way in, and these are the tests that the way in is closed.

def test_a_valid_pass_parses_into_a_result_that_blocks_nothing():
    result = parsed(status="PASS", summary="Read the diff and the tests.")
    assert result.verdict == agent_review.PASS
    assert result.passed
    assert result.blocking() == ()
    assert "REVIEW PASSED" in result.headline()


def test_a_valid_fail_carries_its_findings_and_names_what_to_do():
    result = parsed(status="FAIL", summary="Two problems.",
                    issues=[issue(severity="MAJOR", identifier="R-001"),
                            issue(severity="MINOR", identifier="R-002")])
    assert result.verdict == agent_review.FAIL
    assert not result.passed
    assert [one.id for one in result.blocking()] == ["R-001"]
    assert result.counts() == {"CRITICAL": 0, "MAJOR": 1, "MINOR": 1,
                               "SUGGESTION": 0}
    assert "fix R-001" in result.required_action()
    assert "R-002" not in result.required_action()


def test_pass_with_warnings_is_a_pass_and_keeps_its_findings():
    """It is the shape of a review that found real things worth saying and
    nothing worth stopping for, and collapsing it into PASS would throw away
    the distinction the user most wants drawn."""
    result = parsed(status="PASS_WITH_WARNINGS", summary="Two small things.",
                    issues=[issue(severity="MINOR"),
                            issue(severity="SUGGESTION", identifier="R-002")])
    assert result.verdict == agent_review.PASS_WITH_WARNINGS
    assert result.passed
    assert len(result.issues) == 2
    assert result.blocking() == ()
    assert "nothing blocking" in result.required_action()


def test_a_pass_that_lists_a_blocking_finding_is_recorded_as_a_failure():
    """A reviewer's verdict is overruled DOWNWARD and never upward. A reply
    claiming a pass while reporting a CRITICAL has contradicted itself, and
    resolving that in the reviewer's favour would make the severity scale
    advisory."""
    result = parsed(status="PASS", summary="Fine really.",
                    issues=[issue(severity="CRITICAL")])
    assert result.verdict == agent_review.FAIL
    assert result.stated_verdict == agent_review.PASS
    assert result.overruled
    assert not result.passed
    # And it is said out loud rather than quietly applied, because otherwise
    # the main agent reads a FAIL it cannot account for.
    assert "blocking findings decide" in result.describe()


def test_a_fail_with_only_minor_findings_still_fails():
    """The overrule runs one way only. A reviewer is allowed to stop the work
    for an accumulation of small problems, and second-guessing that would be
    this module deciding it read the code better than the agent that did."""
    result = parsed(status="FAIL", summary="Too many small things.",
                    issues=[issue(severity="MINOR"),
                            issue(severity="MINOR", identifier="R-002")])
    assert result.verdict == agent_review.FAIL
    assert not result.overruled
    assert result.blocking() == ()
    assert not result.passed


def test_a_result_wrapped_in_prose_or_a_fence_is_still_read():
    """A reviewer finishes with `internal_response`, whose response is free
    text by contract, so a model will sometimes wrap the object in a sentence.
    Finding where the object starts is not the same as loosening what it must
    contain, which the tests below hold to."""
    body = json.dumps(result_json(summary="Wrapped."))
    for wrapper in ("Here is my review:\n%s",
                    "```json\n%s\n```",
                    "%s\nThat is everything."):
        result = agent_review.parse_result(wrapper % body)
        assert result.summary == "Wrapped.", wrapper


def test_a_brace_inside_a_string_does_not_end_the_object_early():
    """The scanner tracks strings and escapes, because a finding that quotes
    code is exactly where an unbalanced brace turns up."""
    result = agent_review.parse_result(json.dumps(result_json(
        summary='It writes {"action": "done"} without checking.',
        issues=[issue(description='The literal is `{"a": 1}` at line 4.')])))
    assert "without checking" in result.summary
    assert result.issues[0].description.endswith("at line 4.")


def test_output_with_no_json_at_all_is_an_error_and_never_a_pass():
    for text in ("", "   ", "The implementation looks correct to me.",
                 "[1, 2, 3]", "null"):
        try:
            agent_review.parse_result(text)
        except agent_review.ReviewError:
            continue
        raise AssertionError("parsed %r as a review" % (text,))


def test_broken_json_is_an_error():
    try:
        agent_review.parse_result('{"status": "PASS" "summary": "x"}')
    except agent_review.ReviewError as error:
        assert "could not be read" in str(error), error
    else:
        raise AssertionError("parsed malformed JSON")


def test_a_missing_status_is_an_error_and_names_what_was_allowed():
    try:
        agent_review.parse_result(json.dumps({"summary": "x", "issues": []}))
    except agent_review.ReviewError as error:
        assert "no \"status\"" in str(error), error
        assert "PASS_WITH_WARNINGS" in str(error), error
    else:
        raise AssertionError("parsed a result with no status")


def test_an_unknown_status_is_an_error():
    for bad in ("LGTM", "approved", "ok", "", None, 3):
        try:
            agent_review.parse_result(json.dumps(
                {"status": bad, "summary": "x", "issues": []}))
        except agent_review.ReviewError as error:
            assert "not a review status" in str(error), (bad, error)
            continue
        raise AssertionError("parsed status %r" % (bad,))


def test_a_status_is_forgiven_its_shape_but_not_its_meaning():
    for spelling in ("pass", "Pass", " PASS ", "pass_with_warnings",
                     "PASS WITH WARNINGS", "pass-with-warnings"):
        result = agent_review.parse_result(json.dumps(
            {"status": spelling, "summary": "x", "issues": []}))
        assert result.verdict in agent_review.VERDICTS, spelling


def test_a_missing_summary_is_an_error():
    try:
        agent_review.parse_result(json.dumps({"status": "PASS", "issues": []}))
    except agent_review.ReviewError as error:
        assert "no \"summary\"" in str(error), error
    else:
        raise AssertionError("parsed a result with no summary")


def test_an_unknown_severity_is_an_error_rather_than_a_guess():
    """Mapping an unknown severity onto the nearest one would decide for the
    reviewer whether its finding blocks the task, which is the one judgement
    this parser must never make."""
    try:
        agent_review.parse_result(json.dumps(result_json(
            issues=[issue(severity="BLOCKER")])))
    except agent_review.ReviewError as error:
        assert "not a severity" in str(error), error
        assert "SUGGESTION" in str(error), error
    else:
        raise AssertionError("parsed an unknown severity")


def test_a_severity_is_forgiven_its_case():
    for spelling in ("major", "Major", " MAJOR "):
        result = agent_review.parse_result(json.dumps(result_json(
            status="FAIL", issues=[issue(severity=spelling)])))
        assert result.issues[0].severity == agent_review.MAJOR, spelling


def test_an_issue_without_a_title_or_a_description_is_an_error():
    for entry, expected in (
            ({"severity": "MAJOR", "description": "d"}, "no \"title\""),
            ({"severity": "MAJOR", "title": "t"}, "no \"description\""),
            ("just a string", "must be an object")):
        try:
            agent_review.parse_result(json.dumps(result_json(issues=[entry])))
        except agent_review.ReviewError as error:
            assert expected in str(error), (entry, error)
            continue
        raise AssertionError("parsed issue %r" % (entry,))


def test_issues_must_be_a_list():
    try:
        agent_review.parse_result(json.dumps(
            {"status": "PASS", "summary": "x", "issues": "none"}))
    except agent_review.ReviewError as error:
        assert "must be a list" in str(error), error
    else:
        raise AssertionError("parsed a string as the issue list")


def test_a_missing_issues_key_is_read_as_no_findings():
    """An empty list is the right answer when there was nothing, and a
    reviewer that simply omitted the key meant the same thing. Being strict
    here would turn a clean review into an error, which is the one direction
    this must never fail in."""
    result = agent_review.parse_result(json.dumps(
        {"status": "PASS", "summary": "Nothing to report."}))
    assert result.issues == ()
    assert result.passed


def test_an_issue_keeps_a_real_line_number_and_drops_an_unusable_one():
    """Do not fabricate line numbers. Anything that is not a positive integer
    becomes no line number at all, and the finding is reported against its
    file alone."""
    result = agent_review.parse_result(json.dumps(result_json(
        status="FAIL",
        issues=[issue(identifier="R-1", file="a.py", line=148),
                issue(identifier="R-2", file="a.py", line="about 40"),
                issue(identifier="R-3", file="a.py", line=0),
                issue(identifier="R-4", file="a.py", line=-3),
                issue(identifier="R-5", file="a.py", line=None),
                issue(identifier="R-6", line=12)])))
    lines = [one.line for one in result.issues]
    assert lines == [148, None, None, None, None, 12], lines
    locations = [one.location for one in result.issues]
    # A line with no file points at nothing, so it is not shown at all.
    assert locations == ["a.py:148", "a.py", "a.py", "a.py", "a.py", ""], locations


def test_an_issue_id_is_supplied_when_the_reviewer_did_not_give_one():
    result = agent_review.parse_result(json.dumps(result_json(
        status="FAIL",
        issues=[{"severity": "MAJOR", "title": "t", "description": "d"},
                {"severity": "MINOR", "title": "t", "description": "d"}])))
    assert [one.id for one in result.issues] == ["R-001", "R-002"]


def test_requirements_carry_their_state_and_the_unmet_ones_are_countable():
    result = agent_review.parse_result(json.dumps(result_json(
        requirements=[{"text": "Dark mode exists", "status": "satisfied"},
                      {"text": "Theme persists", "status": "partial"},
                      {"text": "Defaults preserved", "status": "not_satisfied"},
                      "Settings UI exists"])))
    assert [one.status for one in result.requirements] == [
        "satisfied", "partial", "not_satisfied", "satisfied"]
    assert [one.text for one in result.unmet()] == ["Theme persists",
                                                    "Defaults preserved"]
    # A requirement nobody met is visible in the text the main agent reads.
    assert "[ ] Defaults preserved" in result.describe()
    assert "[x] Dark mode exists" in result.describe()


def test_an_over_long_field_is_bounded_rather_than_refused():
    """A reviewer having a bad day must not be able to crowd the task out of
    its own context, and must not lose its review to a length check either."""
    result = agent_review.parse_result(json.dumps(result_json(
        status="FAIL", summary="s" * 9000,
        issues=[issue(description="d" * 9000)])))
    assert len(result.summary) <= agent_review.MAX_SUMMARY_CHARS
    assert len(result.issues[0].description) <= agent_review.MAX_FIELD_CHARS
    assert result.verdict == agent_review.FAIL


def test_the_report_lists_the_blocking_findings_and_bounds_the_rest():
    many = [issue(severity="MINOR", identifier="R-%03d" % n)
            for n in range(agent_review.MAX_ISSUES_REPORTED + 5)]
    text = parsed(status="FAIL", issues=many).describe()
    assert "further finding(s) not listed" in text


# --- the issue and its description -----------------------------------------

def test_only_critical_and_major_block():
    blocking = {severity: agent_review.parse_result(json.dumps(result_json(
        status="FAIL", issues=[issue(severity=severity)]))).blocking()
        for severity in agent_review.SEVERITIES}
    assert [severity for severity, found in blocking.items() if found] == [
        agent_review.CRITICAL, agent_review.MAJOR]
    assert agent_review.BLOCKING_SEVERITIES == (agent_review.CRITICAL,
                                                agent_review.MAJOR)


def test_a_finding_describes_itself_with_every_field_it_was_given():
    text = agent_review.parse_result(json.dumps(result_json(
        status="FAIL",
        issues=[issue(file="src/auth/token.py", line=148,
                      evidence="validate_refresh never reads expires_at",
                      why_it_matters="Expired tokens stay usable",
                      suggested_fix="Check expires_at before issuing")]
    ))).issues[0].describe()
    assert "R-001 MAJOR" in text
    assert "src/auth/token.py:148" in text
    assert "Evidence: validate_refresh" in text
    assert "Why: Expired tokens stay usable" in text
    assert "Suggested direction: Check expires_at" in text


def test_a_finding_prints_no_heading_over_a_field_it_was_not_given():
    text = agent_review.parse_result(json.dumps(result_json(
        status="FAIL", issues=[issue()]))).issues[0].describe()
    for absent in ("Evidence:", "Why:", "Suggested direction:"):
        assert absent not in text, text


def test_the_headline_carries_the_count_the_transcript_shows():
    """`agent_actions` describes a review by its result's FIRST line, so a
    bare "REVIEW FAILED" scrolling past would say a review failed and not by
    how much."""
    failed = parsed(status="FAIL", issues=[issue(), issue(identifier="R-2")])
    assert failed.headline() == "REVIEW FAILED - 2 blocking issues"
    assert parsed(status="FAIL", issues=[issue()]).headline() == \
        "REVIEW FAILED - 1 blocking issue"
    # Both passing forms begin with the prefix action_event tests for.
    assert parsed().headline().startswith("REVIEW PASSED")
    assert parsed(status="PASS_WITH_WARNINGS",
                  issues=[issue(severity="MINOR")]).headline().startswith(
                      "REVIEW PASSED")


# --- the state machine ------------------------------------------------------

def test_a_fresh_state_is_idle_and_shows_nothing():
    review = agent_review.ReviewState()
    assert review.state == agent_review.IDLE
    assert not review
    assert not review.passed
    assert review.cycles == 0
    assert review.last is None


def test_begin_moves_to_running_and_settle_takes_the_verdict():
    review = agent_review.ReviewState()
    assert review.begin() == ""
    assert review.state == agent_review.RUNNING
    assert bool(review)
    assert not review.passed          # running is not passed
    settled(agent_review.ReviewState())   # and the same for a fresh one
    review.settle(parsed(status="PASS"))
    assert review.state == agent_review.PASSED
    assert review.passed
    assert review.cycles == 1


def test_each_verdict_takes_its_own_state():
    for verdict, expected in ((agent_review.PASS, agent_review.PASSED),
                              (agent_review.PASS_WITH_WARNINGS,
                               agent_review.WARNINGS),
                              (agent_review.FAIL, agent_review.FAILED)):
        review = agent_review.ReviewState()
        settled(review, status=verdict,
                issues=[issue()] if verdict == agent_review.FAIL else [])
        assert review.state == expected, verdict
        assert review.passed == (expected in agent_review.SETTLED_PASS), verdict


def test_settle_refuses_anything_that_is_not_a_parsed_result():
    """The failure being guarded against is somebody wiring model text into
    this. There is no route from a string to a passing review."""
    review = agent_review.ReviewState()
    review.begin()
    for text in ("PASS", json.dumps(result_json()), {"status": "PASS"}, None):
        try:
            review.settle(text)
        except TypeError as error:
            assert "parse_result" in str(error), error
            continue
        raise AssertionError("settled on %r" % (text,))
    assert review.state == agent_review.RUNNING
    assert not review.passed


def test_a_failed_review_never_reads_as_passed():
    review = agent_review.ReviewState()
    settled(review, status="FAIL", issues=[issue()])
    assert review.state == agent_review.FAILED
    assert not review.passed
    assert review.blocking_count() == 1


def test_a_review_that_did_not_complete_is_an_error_and_not_a_pass():
    """Section 21 of the brief in one assertion. A reviewer that crashed,
    timed out or returned nonsense must never be mistaken for one that
    approved the work."""
    review = agent_review.ReviewState()
    review.begin()
    review.fail("the reviewer stopped without reviewing")
    assert review.state == agent_review.ERROR
    assert not review.passed
    assert agent_review.ERROR not in agent_review.SETTLED_PASS
    assert "stopped without reviewing" in review.error


def test_a_review_that_errored_does_not_spend_a_cycle():
    """The limit exists to stop the review/fix loop running forever, and a
    review that never reported has not been round that loop. Charging it would
    let two provider hiccups exhaust a task's whole review budget without a
    single finding being made."""
    review = agent_review.ReviewState()
    for _ in range(5):
        review.begin()
        review.fail("provider error")
    assert review.cycles == 0
    assert not review.limit_reached
    assert review.begin() == ""


def test_a_second_review_replaces_the_first_and_both_are_kept():
    review = state_after_work()
    settled(review, status="FAIL", issues=[issue()])
    settled(review, status="PASS", summary="Fixed.")
    assert review.cycles == 2
    assert review.passed
    assert [one.verdict for one in review.history] == ["FAIL", "PASS"]
    assert [one.number for one in review.history] == [1, 2]


def test_a_review_already_running_is_not_started_again():
    review = agent_review.ReviewState()
    review.begin()
    held = review.begin()
    assert "already running" in held
    assert review.state == agent_review.RUNNING


def test_retire_empties_everything_and_is_never_refused():
    """The lesson `Plan.retire` was written for. `Session.begin_turn` and
    `Session.clear` both call this, neither catches anything, and a retirement
    that could raise would take the session with it."""
    review = state_after_work()
    review.note_user_choice(True)
    settled(review, status="FAIL", issues=[issue(severity="CRITICAL")])
    review.retire()
    assert review.state == agent_review.IDLE
    assert review.cycles == 0
    assert review.history == ()
    assert review.changed_paths == ()
    assert review.verification == ()
    assert review.user_choice is None
    assert not review
    assert not review.is_required(worked_plan())
    # And again on an already empty one, which is what a conversational turn
    # does on every question.
    review.retire()
    assert review.state == agent_review.IDLE


def test_retire_is_not_an_operation_the_model_can_reach():
    """A model that could ask for this would have exactly the bypass the
    review rules say does not exist. It is not a `review` argument, and the
    action takes no operation at all."""
    assert agent_config.REQUIRED_KEYS["review"] == []
    prompt = agent_prompt.get_system_prompt()
    assert "retire" not in agent_prompt.REVIEW_REFERENCE
    assert "retire" not in agent_prompt.REVIEW_RULES
    review = state_after_work()
    settled(review, status="FAIL", issues=[issue()])
    for attempt in ({"action": "review", "operation": "retire"},
                    {"action": "review", "status": "PASS"},
                    {"action": "review", "scope": "retire"}):
        out = run_review(review, plan=worked_plan(), obj=attempt)
        assert not review.passed, (attempt, out)


# --- what the runtime observed ---------------------------------------------

def test_changed_paths_are_recorded_once_each_and_in_order():
    review = agent_review.ReviewState()
    review.note_change("write_file", ("a.py",))
    review.note_change("patch_file", ("b.py", "a.py"))
    review.note_change("write_file", ("a.py",))
    assert review.changed_paths == ("a.py", "b.py")


def test_a_change_that_named_no_path_still_counts_as_a_change():
    """A change nobody can name is still a change, and treating it as nothing
    would let a whole class of edit slip past the requirement test."""
    review = agent_review.ReviewState()
    review.note_change("replace_across", ())
    assert review.changed_paths == ("(unnamed)",)
    assert review.is_required(worked_plan())


def test_what_ran_is_recorded_as_an_observation_and_not_as_a_verdict():
    """TMT does not read a program's output for whether it succeeded -- that
    is the rule that once called a green test run a failure. What is recorded
    is that something ran and what it was."""
    review = agent_review.ReviewState()
    review.note_run("bash", "python run_tests.py")
    review.note_run("bash", "python run_tests.py")
    review.note_run("bash", "python check.py")
    assert review.verification == ("bash python run_tests.py",
                                   "bash python check.py")
    for word in ("pass", "fail", "ok", "green"):
        assert word not in " ".join(review.verification).lower()


# --- when a review is required ---------------------------------------------

def test_a_conversational_turn_needs_no_review():
    review = agent_review.ReviewState()
    assert not review.is_required(None)
    assert not review.is_required(agent_plan.Plan())
    assert agent_review.refusal(review, None, "end_conversation") == ""


def test_reading_a_lot_and_changing_nothing_needs_no_review():
    """A long plan that changed nothing was research. Both halves are needed
    and neither alone is enough."""
    review = agent_review.ReviewState()
    assert not review.is_required(worked_plan("One", "Two", "Three", "Four"))


def test_a_small_change_with_no_plan_needs_no_review():
    review = state_after_work()
    assert not review.is_required(None)
    assert not review.is_required(plan_of("Patch it"))
    assert not review.is_required(plan_of("Patch it", "Check it"))


def test_a_substantial_plan_with_real_changes_requires_a_review():
    review = state_after_work()
    plan = worked_plan()
    assert len(plan.steps) >= agent_review.REVIEW_MIN_PLAN_STEPS
    assert review.is_required(plan)
    assert agent_review.refusal(review, plan, "end_conversation") != ""


def test_the_user_can_ask_for_a_review_or_ask_for_none():
    """Their tool. A quality gate that cannot be declined is a quality gate
    somebody works around instead of using."""
    review = agent_review.ReviewState()          # nothing changed at all
    review.note_user_choice(True)
    assert review.is_required(None)
    worked = state_after_work()                  # everything a gate wants
    worked.note_user_choice(False)
    assert not worked.is_required(worked_plan())
    assert agent_review.refusal(worked, worked_plan(), "end_conversation") == ""


def test_the_wording_that_asks_for_a_review_and_the_wording_that_declines_it():
    for text in ("fix the bug and review the changes",
                 "add the feature, then review it",
                 "do a code review afterwards",
                 "run an independent review when you are done",
                 "implement it and request review",
                 "add caching, and review it carefully"):
        assert agent_review.requests_review(text) is True, text
    for text in ("commit it, no review needed",
                 "just do it, skip the review",
                 "no code review please",
                 "a review is not required here",
                 "there is no need for a review"):
        assert agent_review.requests_review(text) is False, text
    # Silence is the common case and must not be read as either instruction.
    for text in ("", "fix the retry bug", "what does zip do?",
                 "add a percent operator to Calc.py",
                 "update the README", None):
        assert agent_review.requests_review(text) is None, text


def test_asking_tmt_to_read_something_is_not_asking_for_a_review():
    """"Review the README" is a request to READ, and turning an audit on for
    it would gate a two-minute task behind a whole review cycle."""
    for text in ("review the README and tell me what it says",
                 "review the docs folder",
                 "have a look at the reviewer notes in CONTRIBUTING"):
        assert agent_review.requests_review(text) is None, text


def test_declining_beats_asking_when_the_user_wrote_both():
    for text in ("review the changes -- actually no review needed",
                 "do a code review? no, skip the review"):
        assert agent_review.requests_review(text) is False, text


# --- the gate ---------------------------------------------------------------

def test_the_gate_only_holds_the_one_terminal_action():
    """Holding a read or a patch would stop the model doing the very thing it
    is being told to do -- and `send_message` is on this list for a sharper
    reason still: it is how the model tells the user what the reviewer
    objected to. Gating it would silence the explanation of the very failure
    that is holding the answer back."""
    review = state_after_work()
    plan = worked_plan()
    for action in ("read_file", "patch_file", "git_diff", "plan", "review",
                   "send_message", "spawn_agent"):
        assert agent_review.refusal(review, plan, action) == "", action
    assert agent_review.refusal(review, plan, "end_conversation") != ""


def test_no_review_at_all_blocks_and_says_how_to_start_one():
    review = state_after_work(paths=("a.py", "b.py"))
    held = agent_review.refusal(review, worked_plan(), "end_conversation")
    assert held.startswith("BLOCKED:")
    assert "2 file(s)" in held
    assert '{"action":"review"}' in held


def test_a_running_review_blocks():
    review = state_after_work()
    review.begin()
    held = agent_review.refusal(review, worked_plan(), "end_conversation")
    assert "running now" in held


def test_a_failed_review_blocks_and_names_the_findings():
    review = state_after_work()
    settled(review, status="FAIL",
            issues=[issue(identifier="R-001", title="Expiry is not enforced",
                          file="token.py", line=148),
                    issue(identifier="R-002", severity="CRITICAL",
                          title="Health check is behind auth")])
    held = agent_review.refusal(review, worked_plan(), "end_conversation")
    assert "R-001 MAJOR: Expiry is not enforced (token.py:148)" in held
    assert "R-002 CRITICAL: Health check is behind auth" in held
    assert "cannot mark the review passed yourself" in held


def test_a_review_that_errored_blocks():
    review = state_after_work()
    review.begin()
    review.fail("the reviewer produced no result")
    held = agent_review.refusal(review, worked_plan(), "end_conversation")
    assert "not a review that passed" in held
    assert "produced no result" in held


def test_a_passing_review_lets_the_answer_out():
    review = state_after_work()
    settled(review, status="PASS")
    assert agent_review.refusal(review, worked_plan(), "end_conversation") == ""


def test_a_pass_with_warnings_lets_the_answer_out():
    review = state_after_work()
    settled(review, status="PASS_WITH_WARNINGS",
            issues=[issue(severity="MINOR"), issue(severity="SUGGESTION")])
    assert agent_review.refusal(review, worked_plan(), "end_conversation") == ""


def test_a_pass_goes_stale_the_moment_the_code_moves_under_it():
    """The review was of a diff. Editing after it means the thing that passed
    is not the thing that would ship, and this is what makes the fix and
    re-review loop close rather than being a suggestion."""
    review = state_after_work()
    settled(review, status="PASS")
    assert review.passed
    review.note_change("patch_file", ("src/other.py",))
    assert review.stale
    assert not review.passed
    held = agent_review.refusal(review, worked_plan(), "end_conversation")
    assert "1 file(s) have been changed since it ran" in held
    assert "src/other.py" in held
    # And a fresh review clears it.
    settled(review, status="PASS", summary="Reviewed again.")
    assert not review.stale
    assert review.passed
    assert agent_review.refusal(review, worked_plan(), "end_conversation") == ""


def test_marking_the_plan_or_answering_does_not_make_a_pass_stale():
    """Only a change to the workspace does. Otherwise the last two steps of
    every task -- complete the plan, answer -- would invalidate the review
    that had just approved them."""
    review = state_after_work()
    settled(review, status="PASS")
    review.note_run("bash", "python run_tests.py")
    assert not review.stale
    assert review.passed


def test_a_broken_review_object_lets_the_answer_through():
    """Every other guard in that loop fails in this direction, and a broken
    review object holding finished work hostage would be the worst outcome
    available."""

    class Broken:
        def is_required(self, plan=None):
            raise RuntimeError("boom")

    assert agent_review.refusal(Broken(), worked_plan(), "end_conversation") == ""
    assert agent_review.held_line(Broken()) != ""
    assert agent_review.limit_release(Broken()) == ""
    assert agent_panel.review_rows(Broken(), 30, stream=Terminal()) == []


def test_a_missing_review_state_gates_nothing():
    assert agent_review.refusal(None, worked_plan(), "end_conversation") == ""
    assert agent_panel.review_rows(None, 30, stream=Terminal()) == []


# --- the loop limit ---------------------------------------------------------

def test_the_cycle_limit_stops_another_review_being_started():
    review = state_after_work()
    for _ in range(agent_review.MAX_REVIEW_CYCLES):
        settled(review, status="FAIL", issues=[issue()])
    assert review.limit_reached
    held = review.begin()
    assert "REVIEW LOOP LIMIT REACHED" in held
    assert review.state == agent_review.FAILED     # not moved to running


def test_the_cycle_limit_releases_the_answer_rather_than_deadlocking_it():
    """Holding it further would spend the turn's rounds and end with no answer
    at all. "Here is the work and here is what review still says" is worth
    more to the user than silence."""
    review = state_after_work()
    for _ in range(agent_review.MAX_REVIEW_CYCLES):
        settled(review, status="FAIL", issues=[issue()])
    assert agent_review.refusal(review, worked_plan(), "end_conversation") == ""
    warning = agent_review.limit_release(review)
    assert "Review did not pass" in warning
    assert "3 reviews ran" in warning
    assert "1 blocking issue still open" in warning


def test_nothing_is_released_when_the_review_passed_inside_the_limit():
    review = state_after_work()
    settled(review, status="FAIL", issues=[issue()])
    settled(review, status="PASS")
    assert agent_review.limit_release(review) == ""
    assert not review.limit_reached


def test_reaching_the_limit_with_a_pass_releases_nothing():
    review = state_after_work()
    settled(review, status="FAIL", issues=[issue()])
    settled(review, status="FAIL", issues=[issue()])
    settled(review, status="PASS")
    assert review.limit_reached
    assert review.passed
    assert agent_review.limit_release(review) == ""


# --- the plan's review step -------------------------------------------------

def test_a_review_step_is_recognised_by_its_title_and_a_documentation_step_is_not():
    for title in ("Independent review", "Review the changes", "review",
                  "Get it reviewed", "Reviewing the diff"):
        assert agent_review.is_review_step(title), title
    for title in ("Reviewer notes in the README", "Preview the output",
                  "Run the tests", "Implement it", ""):
        assert not agent_review.is_review_step(title) or "Review" in title, title
    assert not agent_review.is_review_step("Preview the output")
    assert not agent_review.is_review_step("Run the tests")


def test_completing_a_review_step_is_refused_while_the_review_has_not_passed():
    review = state_after_work()
    plan = worked_plan("Implement it", "Test it", "Independent review")
    settled(review, status="FAIL", issues=[issue()])
    held = agent_review.plan_veto(
        review, plan, {"operation": "update", "step": 3, "status": "completed"})
    assert "S3 (Independent review) is the review step" in held
    assert "1 blocking issue(s)" in held


def test_the_veto_reaches_the_model_through_the_plan_action():
    """Refused BEFORE the operation runs, so a vetoed update leaves no trace of
    having happened -- the same rule the loop's own gate follows."""
    review = state_after_work()
    plan = worked_plan("Implement it", "Test it", "Independent review")
    settled(review, status="FAIL", issues=[issue()])
    out = agent_actions.execute_action(
        {"action": "plan", "operation": "update", "step": 3,
         "status": "completed"},
        {"plan": plan, "review": review, "manager": None,
         "capabilities": agent_capabilities.Capabilities("/plan /review")})
    assert out.startswith("FAILED:")
    assert plan.steps[2].status != "completed"


def test_the_veto_catches_a_review_step_inside_a_batched_update():
    review = state_after_work()
    plan = worked_plan("Implement it", "Test it", "Independent review")
    settled(review, status="FAIL", issues=[issue()])
    out = agent_actions.execute_action(
        {"action": "plan", "operation": "update",
         "steps": [{"step": 2, "status": "completed"},
                   {"step": 3, "status": "completed"}]},
        {"plan": plan, "review": review, "manager": None,
         "capabilities": agent_capabilities.Capabilities("/plan /review")})
    assert out.startswith("FAILED:")
    # All or nothing: neither step moved.
    assert plan.steps[1].status != "completed"
    assert plan.steps[2].status != "completed"


def test_the_veto_lifts_once_the_review_passes():
    review = state_after_work()
    plan = worked_plan("Implement it", "Test it", "Independent review")
    settled(review, status="FAIL", issues=[issue()])
    settled(review, status="PASS", summary="Fixed.")
    plan.update(1, "completed")
    plan.update(2, "completed")
    out = agent_actions.execute_action(
        {"action": "plan", "operation": "update", "step": 3,
         "status": "completed"},
        {"plan": plan, "review": review, "manager": None,
         "capabilities": agent_capabilities.Capabilities("/plan /review")})
    assert not out.startswith("FAILED:"), out
    assert plan.steps[2].status == "completed"


def test_the_veto_does_not_touch_an_ordinary_step():
    review = state_after_work()
    plan = worked_plan("Implement it", "Run the tests", "Independent review")
    settled(review, status="FAIL", issues=[issue()])
    out = agent_actions.execute_action(
        {"action": "plan", "operation": "update", "step": 1,
         "status": "completed"},
        {"plan": plan, "review": review, "manager": None,
         "capabilities": agent_capabilities.Capabilities("/plan /review")})
    assert not out.startswith("FAILED:"), out
    assert plan.steps[0].status == "completed"


def test_the_veto_is_a_refinement_and_the_gate_is_the_guarantee():
    """It rests on the step's title naming review, which a model can avoid --
    and that is exactly why it is not the guarantee. The gate is driven by
    ReviewState and does not care what any step is called."""
    review = state_after_work()
    plan = worked_plan("Implement it", "Test it", "Check the work")
    settled(review, status="FAIL", issues=[issue()])
    # The plan can be finished, because nothing here is named "review"...
    out = agent_actions.execute_action(
        {"action": "plan", "operation": "update",
         "steps": [{"step": n, "status": "completed"} for n in (1, 2, 3)]},
        {"plan": plan, "review": review, "manager": None,
         "capabilities": agent_capabilities.Capabilities("/plan /review")})
    assert not out.startswith("FAILED:"), out
    assert plan.is_complete()
    assert agent_plan.refusal(plan, "end_conversation") == ""
    # ...and the answer is still refused.
    assert agent_review.refusal(review, plan, "end_conversation") != ""


def test_a_plan_action_still_works_with_no_review_state_at_all():
    plan = worked_plan()
    out = agent_actions.execute_action(
        {"action": "plan", "operation": "update", "step": 1,
         "status": "completed"},
        {"plan": plan, "capabilities": agent_capabilities.Capabilities("/plan /review")})
    assert not out.startswith("FAILED:"), out


# --- the action -------------------------------------------------------------

def test_the_action_is_registered_the_way_every_tmt_action_is():
    """An action that works perfectly and is not registered is an action that
    does not exist."""
    assert "review" in agent_config.REQUIRED_KEYS
    assert agent_prompt.validate_action({"action": "review"}) is None
    assert "review" in agent_actions.ACTION_LABELS
    assert agent_actions._EVENT_KIND_FOR_ACTION["review"] == "milestone"
    # It writes nothing, so it does not invalidate the cached workspace.
    assert "review" not in agent_config.MUTATING_ACTIONS
    # And it must not be told to answer the user with what came back.
    assert "review" not in agent_actions.READ_ONLY_ACTIONS


def test_a_review_runs_a_real_reviewer_and_hands_back_what_it_found():
    review = state_after_work()
    manager = agent_manager.AgentManager()
    with Reviewer(reviewer_reply(
            status="FAIL", summary="Expiry is not enforced.",
            issues=[issue(file="token.py", line=148)])) as reviewer:
        out = run_review(review, plan=worked_plan(), manager=manager)
    assert out.startswith("REVIEW FAILED - 1 blocking issue"), out
    assert "token.py:148" in out
    assert review.state == agent_review.FAILED
    assert review.cycles == 1
    assert reviewer.calls == 1
    # It really was a background agent, of its own kind, and it is not in the
    # worker fleet.
    record = manager.review()
    assert record is not None and record.kind == "review"
    assert record.status == agent_manager.Status.COMPLETED
    assert manager.list() == ()
    assert manager.totals()["agents"] == 0


def test_the_reviewer_is_given_the_request_the_plan_the_status_and_the_diff():
    """Stage one of the staged context: the request, the plan, the status and
    the diff, and nothing else. The rest it fetches itself."""
    review = state_after_work(paths=("src/auth/token.py",))
    plan = worked_plan()
    plan.update(1, "completed")
    with Reviewer(reviewer_reply()) as reviewer:
        run_review(review, plan=plan, manager=agent_manager.AgentManager(),
                   task="Add authentication with refresh-token support.")
    brief = reviewer.brief()
    assert "THE USER'S ORIGINAL REQUEST" in brief
    assert "Add authentication with refresh-token support." in brief
    assert "THE PLAN THE IMPLEMENTING AGENT WROTE" in brief
    assert "S1: Implement it [completed]" in brief
    assert "GIT STATUS" in brief
    assert "GIT DIFF" in brief
    assert "PATHS THE IMPLEMENTING AGENT WROTE TO" in brief
    assert "src/auth/token.py" in brief
    assert "WHAT VERIFICATION ACTUALLY RAN" in brief
    assert "bash python run_tests.py" in brief


def test_the_brief_says_nothing_ran_when_nothing_ran():
    review = state_after_work(ran=False)
    with Reviewer(reviewer_reply()) as reviewer:
        run_review(review, plan=worked_plan(),
                   manager=agent_manager.AgentManager())
    brief = reviewer.brief()
    assert "Nothing was executed in this session" in brief
    assert "treat it as unverified" in brief


def test_the_implementing_agents_note_is_carried_as_a_claim_to_be_checked():
    review = state_after_work()
    with Reviewer(reviewer_reply()) as reviewer:
        run_review(review, plan=worked_plan(),
                   manager=agent_manager.AgentManager(),
                   obj={"action": "review",
                        "notes": "The retry loop is the risky part."})
    brief = reviewer.brief()
    assert "The implementing agent says: The retry loop is the risky part." in brief
    assert "its own claim about its own work" in brief
    assert "rather than accepting it" in brief


def test_an_unknown_scope_is_refused_and_changes_nothing():
    review = state_after_work()
    out = run_review(review, plan=worked_plan(),
                     obj={"action": "review", "scope": "changed_files"})
    assert out.startswith("FAILED:")
    assert "not a review scope" in out
    assert "current_task" in out
    assert review.state == agent_review.IDLE
    assert review.cycles == 0


def test_the_documented_scope_is_accepted():
    review = state_after_work()
    with Reviewer(reviewer_reply()):
        out = run_review(review, plan=worked_plan(),
                         manager=agent_manager.AgentManager(),
                         obj={"action": "review", "scope": "current_task"})
    assert out.startswith("REVIEW PASSED"), out


def test_a_review_with_no_state_reports_it_and_never_claims_a_pass():
    out = agent_actions.execute_action(
        {"action": "review"},
        {"manager": None, "capabilities": agent_capabilities.Capabilities("/plan /review")})
    assert "not available" in out
    assert "Do not claim the work was reviewed" in out


def test_a_review_with_no_manager_reports_it_and_never_claims_a_pass():
    review = state_after_work()
    out = agent_actions.execute_action(
        {"action": "review"},
        {"review": review, "plan": worked_plan(), "task": "x",
         "capabilities": agent_capabilities.Capabilities("/plan /review")})
    assert "needs background agents" in out
    assert "Do not claim the work was reviewed" in out
    assert review.state == agent_review.IDLE
    assert not review.passed


def test_a_background_agents_context_cannot_reach_the_review():
    """A worker's context has no review key at all AND no capabilities key, so
    a worker that somehow emitted the verb is told it changed nothing rather
    than raising.

    The capability guard is what answers now, and it answers first. That is
    strictly the safer of the two: a worker is not authorised by anybody, so
    it is refused before the missing state is even reached. `WORKER_FORBIDDEN`
    refuses the verb ahead of both, so this is the third of three.
    """
    out = agent_actions.execute_action({"action": "review"},
                                       agent_worker._context(
                                           agent_manager.AgentRecord(
                                               "1", 1, "worker", "t")))
    assert out.startswith("REFUSED:"), out
    assert "/review" in out, out
    assert "not enabled" in out, out
    assert "review" in agent_worker.WORKER_FORBIDDEN


def test_a_review_is_refused_while_background_workers_are_running():
    """Section 22: a reviewer reading a tree another agent is writing to is
    reviewing a state that never existed."""
    manager = agent_manager.AgentManager()
    release = threading.Event()
    started = threading.Event()

    def slow(record, mgr):
        started.set()
        release.wait(5)
        return "done"

    record = manager.spawn("write some files")
    manager.start(record, slow)
    try:
        assert started.wait(5)
        review = state_after_work()
        out = run_review(review, plan=worked_plan(), manager=manager)
        assert out.startswith("REFUSED:")
        assert "#%s" % record.id in out
        assert "wait_for_agents" in out
        assert review.state == agent_review.IDLE
    finally:
        release.set()
        manager.wait([record.id], timeout=5)


def test_a_review_runs_once_the_workers_have_finished():
    manager = agent_manager.AgentManager()
    record = manager.spawn("write some files")
    manager.start(record, lambda rec, mgr: "done")
    manager.wait([record.id], timeout=5)
    review = state_after_work()
    with Reviewer(reviewer_reply()):
        out = run_review(review, plan=worked_plan(), manager=manager)
    assert out.startswith("REVIEW PASSED"), out


def test_a_reviewer_that_returns_prose_leaves_the_task_in_error():
    review = state_after_work()
    with Reviewer(json.dumps({"action": "internal_response",
                              "response": "It all looks correct to me."})):
        out = run_review(review, plan=worked_plan(),
                         manager=agent_manager.AgentManager())
    assert out.startswith("FAILED:")
    assert "could not be read" in out
    assert review.state == agent_review.ERROR
    assert not review.passed
    assert agent_review.refusal(review, worked_plan(), "end_conversation") != ""


def test_a_reviewer_that_returns_an_invalid_result_leaves_the_task_in_error():
    for response in (json.dumps({"status": "LGTM", "summary": "x"}),
                     json.dumps({"summary": "no status"}),
                     json.dumps({"status": "PASS"}),
                     json.dumps({"status": "PASS", "summary": "x",
                                 "issues": [{"severity": "HUGE",
                                             "title": "t", "description": "d"}]})):
        review = state_after_work()
        with Reviewer(json.dumps({"action": "internal_response",
                                  "response": response})):
            out = run_review(review, plan=worked_plan(),
                             manager=agent_manager.AgentManager())
        assert review.state == agent_review.ERROR, response
        assert not review.passed, response
        assert out.startswith("FAILED:"), out


def test_a_reviewer_that_produced_nothing_leaves_the_task_in_error():
    review = state_after_work()
    with Reviewer(json.dumps({"action": "internal_response", "response": ""})):
        out = run_review(review, plan=worked_plan(),
                         manager=agent_manager.AgentManager())
    assert "produced no result" in out
    assert review.state == agent_review.ERROR


def test_a_reviewer_that_failed_leaves_the_task_in_error_with_the_reason():
    review = state_after_work()

    def exploding(messages, **kwargs):
        raise RuntimeError("the provider fell over")

    saved = agent_worker.run_review
    agent_worker.run_review = lambda record, manager: saved(
        record, manager, ask=exploding, execute=agent_actions.execute_action,
        system_prompt="stub")
    try:
        out = run_review(review, plan=worked_plan(),
                         manager=agent_manager.AgentManager())
    finally:
        agent_worker.run_review = saved
    assert out.startswith("FAILED:")
    assert "stopped without reviewing" in out
    assert "provider fell over" in out
    assert review.state == agent_review.ERROR
    assert not review.passed


def test_a_reviewer_that_does_not_report_in_time_is_stopped_and_recorded():
    """Nothing here sleeps: the timeout is zero and the reviewer is released
    in a finally, so a wait that never returned cannot hang the suite."""
    manager = agent_manager.AgentManager()
    release = threading.Event()
    review = state_after_work()

    def slow(messages, **kwargs):
        release.wait(5)
        return reviewer_reply()

    saved = agent_worker.run_review
    agent_worker.run_review = lambda record, mgr: saved(
        record, mgr, ask=slow, execute=agent_actions.execute_action,
        system_prompt="stub")
    try:
        out = run_review(review, plan=worked_plan(), manager=manager,
                         obj={"action": "review", "timeout": 0})
        assert out.startswith("FAILED:")
        assert "did not report within" in out
        assert review.state == agent_review.ERROR
        assert not review.passed
        # Killed rather than abandoned: a reviewer still reading while the main
        # agent resumes editing would report on a tree that has moved.
        assert manager.review().status == agent_manager.Status.KILLED
    finally:
        release.set()
        manager.wait([manager.review().id], timeout=5)
        agent_worker.run_review = saved


def test_a_review_at_the_cycle_limit_refuses_to_start_another_reviewer():
    review = state_after_work()
    for _ in range(agent_review.MAX_REVIEW_CYCLES):
        settled(review, status="FAIL", issues=[issue()])
    manager = agent_manager.AgentManager()
    with Reviewer(reviewer_reply()) as reviewer:
        out = run_review(review, plan=worked_plan(), manager=manager)
    assert "REVIEW LOOP LIMIT REACHED" in out
    assert reviewer.calls == 0
    assert manager.review() is None
    assert review.cycles == agent_review.MAX_REVIEW_CYCLES


def test_the_review_action_is_never_terminal_in_the_main_loop():
    """It has to be able to run in the middle of a turn and hand its findings
    back, which is the whole loop the feature is."""
    assert "review" != agent_actions.END_CONVERSATION
    review = state_after_work()
    with Reviewer(reviewer_reply()):
        result = run_review(review, plan=worked_plan(),
                            manager=agent_manager.AgentManager())
    assert agent_actions.action_event("review", {"action": "review"},
                                      result) is not None


# --- the reviewer agent -----------------------------------------------------

def test_the_reviewer_cannot_write_and_the_refusal_names_the_reviewers_reason():
    """A model told the wrong reason reasonably looks for another route to the
    same effect, which is the mistake WORKER_NEEDS_TERMINAL was given its own
    sentence to avoid."""
    for action in ("write_file", "patch_file", "append_file", "replace_lines",
                   "delete_file", "rename_file", "bash", "git_commit",
                   "replace_across", "create_folder"):
        assert action not in agent_worker.REVIEW_ACTIONS, action
    refusal = agent_worker._refusal("patch_file", agent_worker.REVIEW_ACTIONS,
                                    agent_worker.WORKER_FORBIDDEN,
                                    agent_worker._NOT_A_REVIEW_VERB)
    assert "no longer independent" in refusal
    assert "the implementing agent makes the change" in refusal.lower()
    # And the note agent's own sentence is unchanged.
    note = agent_worker._refusal("patch_file", agent_worker.NOTE_ACTIONS,
                                 agent_worker.WORKER_FORBIDDEN)
    assert "answering one question by reading" in note


def test_a_reviewer_asking_to_write_is_refused_before_dispatch():
    """A whitelist checked before dispatch, never a blacklist: a blacklist
    silently admits every action added to TMT after it was written."""
    manager = agent_manager.AgentManager()
    record = manager.spawn("review it", kind="review")
    seen = []

    def ask(messages, **kwargs):
        if not seen:
            seen.append(1)
            return json.dumps({"action": "patch_file", "path": "a.py",
                               "search": "x", "replace": "y"})
        return reviewer_reply()

    def execute(obj, context):
        raise AssertionError("a writing action reached the dispatcher")

    out = agent_worker.run_review(record, manager, ask=ask, execute=execute,
                                  system_prompt="stub")
    assert agent_review.parse_result(out).passed


def test_the_reviewer_cannot_start_a_review_of_its_own():
    """A reviewer that could review its own review would be exactly the thing
    this feature exists to replace."""
    assert "review" in agent_worker.WORKER_FORBIDDEN
    assert "review" not in agent_worker.REVIEW_ACTIONS
    assert "review" not in agent_worker.NOTE_ACTIONS
    refusal = agent_worker._refusal("review", agent_worker.REVIEW_ACTIONS,
                                    agent_worker.WORKER_FORBIDDEN)
    assert refusal.startswith("REFUSED:")


def test_a_worker_and_a_note_agent_are_refused_the_review_verb_too():
    for allowed in (None, agent_worker.NOTE_ACTIONS):
        assert agent_worker._refusal("review", allowed,
                                     agent_worker.WORKER_FORBIDDEN) != ""


def test_the_reviewer_ends_on_internal_response_like_every_background_agent():
    manager = agent_manager.AgentManager()
    record = manager.spawn("review it", kind="review")
    out = agent_worker.run_review(
        record, manager, ask=lambda messages, **kw: reviewer_reply(
            summary="Nothing to report."),
        execute=agent_actions.execute_action, system_prompt="stub")
    assert agent_review.parse_result(out).summary == "Nothing to report."


def test_killing_a_reviewer_stops_it_taking_further_actions():
    """The guarantee is narrow and exact: after the flag is set, no further
    tool call executes."""
    manager = agent_manager.AgentManager()
    record = manager.spawn("review it", kind="review")
    ran = []

    def ask(messages, **kwargs):
        manager.kill(record.id)
        return json.dumps({"action": "read_file", "path": "a.py"})

    def execute(obj, context):
        ran.append(obj)
        return "x"

    try:
        agent_worker.run_review(record, manager, ask=ask, execute=execute,
                                system_prompt="stub")
    except agent_manager.WorkerCancelled:
        pass
    else:
        raise AssertionError("a killed reviewer kept going")
    assert ran == []


def test_the_reviewer_does_not_count_against_the_worker_cap():
    """It is the quality gate on the task, and a gate that can be crowded out
    by the work it is gating is not a gate."""
    manager = agent_manager.AgentManager(max_workers=1)
    held = threading.Event()
    worker = manager.spawn("work")
    manager.start(worker, lambda rec, mgr: held.wait(5) and "done")
    try:
        record = manager.spawn("review it", kind="review")
        assert record is not None
        assert manager.review() is record
        assert manager.active_count() == 1        # the reviewer is not in it
        assert record not in manager.list()
    finally:
        held.set()
        manager.wait([worker.id], timeout=5)


def test_the_reviewers_prompt_offers_exactly_the_verbs_the_loop_allows():
    """A prompt that offered something the dispatcher refuses would spend the
    reviewer's steps on refusals."""
    assert set(agent_subprompts.REVIEW_VERBS) == set(agent_worker.REVIEW_ACTIONS)


def test_the_reviewers_prompt_teaches_the_result_shape_and_the_process():
    prompt = agent_subprompts.review_prompt()
    for required in ("independent code reviewer", "YOU DID NOT WRITE IT",
                     "THE REVIEW RESULT YOU MUST RETURN",
                     "PASS_WITH_WARNINGS", "CRITICAL", "SUGGESTION",
                     "UNDERSTAND THE REQUEST", "INSPECT THE CHANGESET",
                     "CHECK EVERY REQUIREMENT", "LOOK FOR REGRESSIONS",
                     "PRECISION OVER VOLUME",
                     "Do not assume the implementation is correct because the "
                     "tests pass"):
        assert required in prompt, required


def test_the_reviewer_is_not_taught_to_plan_to_spawn_or_to_review():
    """The same two-sided isolation the plan has, and for a sharper reason."""
    prompt = agent_subprompts.review_prompt()
    assert "spawn_agent" not in prompt
    assert agent_prompt.PLAN_REFERENCE not in prompt
    assert agent_prompt.REVIEW_REFERENCE not in prompt


def test_the_main_agent_alone_is_taught_the_review_lifecycle():
    # Authorised, because the question is WHO is taught the review and not
    # WHETHER this turn may use it. Both isolations are real and separate.
    main = agent_prompt.get_system_prompt(
        agent_capabilities.Capabilities("/review"))
    assert agent_prompt.REVIEW_REFERENCE in main
    assert agent_prompt.REVIEW_RULES in main
    for other in (agent_subprompts.worker_prompt(),
                  agent_subprompts.note_prompt(),
                  agent_subprompts.review_prompt()):
        assert agent_prompt.REVIEW_REFERENCE not in other
        assert agent_prompt.REVIEW_RULES not in other


def test_the_review_examples_in_the_prompts_are_real_actions():
    """An example that broke a rule would teach breaking it."""
    import re
    for prompt in (agent_prompt.REVIEW_REFERENCE,
                   agent_subprompts.REVIEWER_EXAMPLES):
        for match in re.findall(r'^\s*(?:You emit:\s*)?(\{"action".*)$',
                                prompt, re.MULTILINE):
            obj = json.loads(match)
            assert agent_prompt.validate_action(obj) is None, match


# --- the column -------------------------------------------------------------

def rows_of(review, width=34, height=None, stream=None):
    stream = Terminal() if stream is None else stream
    return [visible(row) for row in
            agent_panel.review_rows(review, width, height=height,
                                    stream=stream)]


def test_a_task_with_no_review_draws_no_review_block():
    """A heading over "no review yet" beside every conversational question
    would be a permanent empty box in a column two other things want."""
    assert rows_of(agent_review.ReviewState()) == []
    assert rows_of(None) == []


def test_a_running_review_says_so():
    review = state_after_work()
    review.begin()
    rows = rows_of(review)
    assert rows[0] == "REVIEW 1/3", rows
    assert "Running independent review" in rows[1], rows
    # Nothing to count yet, so no third row saying zero of everything.
    assert len(rows) == 2, rows


def test_a_passed_review_says_so_and_counts_nothing_twice():
    review = state_after_work()
    settled(review, status="PASS")
    rows = rows_of(review)
    assert rows[0] == "REVIEW 1/3"
    assert "Review passed" in rows[1]
    assert len(rows) == 2, rows


def test_a_failed_review_shows_the_blocking_count():
    review = state_after_work()
    settled(review, status="FAIL",
            issues=[issue(), issue(identifier="R-2", severity="CRITICAL"),
                    issue(identifier="R-3", severity="SUGGESTION")])
    rows = rows_of(review)
    assert rows[0] == "REVIEW 1/3"
    assert "2 blocking issues" in rows[1], rows
    assert "2 blocking, 1 suggestion(s)" in rows[2], rows


def test_a_review_with_warnings_says_none_of_them_block():
    review = state_after_work()
    settled(review, status="PASS_WITH_WARNINGS",
            issues=[issue(severity="MINOR"),
                    issue(severity="SUGGESTION", identifier="R-2")])
    rows = rows_of(review)
    assert "Review passed with warnings" in rows[1], rows
    assert "2 finding(s), none blocking" in rows[2], rows


def test_a_review_that_errored_says_it_did_not_complete():
    review = state_after_work()
    review.begin()
    review.fail("the reviewer produced no result")
    rows = rows_of(review)
    assert "Review did not complete" in rows[1], rows


def test_a_stale_review_says_so_rather_than_saying_it_passed():
    review = state_after_work()
    settled(review, status="PASS")
    review.note_change("patch_file", ("a.py", "b.py"))
    rows = rows_of(review, width=60)
    assert "stale" in rows[1], rows
    assert "2 file(s) changed since" in rows[1], rows
    # And NOT with the mark that means passed. A green tick beside work the
    # gate is refusing is the one way this column can actively mislead.
    marks = agent_panel.review_marks(Terminal())
    assert not rows[1].startswith(marks["passed"]), rows
    assert rows[1].startswith(marks["warnings"]), rows
    assert review.display == agent_review.WARNINGS
    assert review.state == agent_review.PASSED


def test_the_block_counts_the_review_in_flight_as_the_one_being_done():
    review = state_after_work()
    settled(review, status="FAIL", issues=[issue()])
    assert rows_of(review)[0] == "REVIEW 1/3"
    review.begin()
    assert rows_of(review)[0] == "REVIEW 2/3"


def test_every_state_carries_a_mark_so_colour_is_never_the_message():
    marks = agent_panel.review_marks(Terminal())
    assert marks is agent_panel._REVIEW_MARKS
    for state in ("passed", "running", "warnings", "failed", "error", "idle"):
        assert marks.get(state), state
    # Read with the escapes stripped and nothing is lost but confirmation.
    review = state_after_work()
    settled(review, status="FAIL", issues=[issue()])
    assert rows_of(review)[1].startswith(marks["failed"])


def test_the_column_degrades_where_the_terminal_cannot_draw():
    """The console is cp1252 on Windows through a pipe, and a terminal that
    draws a box rule but not a filled circle would put a replacement character
    on the one row the user is looking for."""

    class Cp1252(io.StringIO):
        def isatty(self):
            return True

        @property
        def encoding(self):
            return "cp1252"

    marks = agent_panel.review_marks(Cp1252())
    assert marks is agent_panel._REVIEW_ASCII_MARKS
    review = state_after_work()
    settled(review, status="PASS")
    rows = rows_of(review, stream=Cp1252())
    assert rows[1].startswith("+"), rows
    assert "?" not in "".join(rows), rows


def test_the_four_states_take_four_positions_on_the_one_gradient():
    """Green for passed, orange for running, amber for warnings, red for
    failed -- and every one of them is a position an existing event kind
    already holds. A new element takes a place on the one scale; it never gets
    a palette."""
    assert agent_panel.REVIEW_PASSED_POSITION == agent_panel.PLAN_DONE_POSITION
    assert agent_panel.REVIEW_RUNNING_POSITION == agent_panel.PLAN_ACTIVE_POSITION
    assert agent_panel.REVIEW_WARNING_POSITION == agent_panel.PLAN_BLOCKED_POSITION
    assert agent_panel.REVIEW_FAILED_POSITION == agent_panel.PLAN_PENDING_POSITION
    from agent_ui import _gradient

    def escape(position):
        return "\033[38;2;%d;%d;%dm" % _gradient(position)

    review = state_after_work()
    settled(review, status="PASS")
    painted = agent_panel.review_rows(review, 30, stream=Terminal())
    assert escape(agent_panel.REVIEW_PASSED_POSITION) in painted[1], painted[1]

    failed = state_after_work()
    settled(failed, status="FAIL", issues=[issue()])
    painted = agent_panel.review_rows(failed, 30, stream=Terminal())
    assert escape(agent_panel.REVIEW_FAILED_POSITION) in painted[1], painted[1]


def test_the_block_never_takes_more_than_its_ceiling():
    review = state_after_work()
    settled(review, status="FAIL",
            issues=[issue(identifier="R-%d" % n) for n in range(10)])
    assert len(rows_of(review)) <= agent_panel.REVIEW_MAX_ROWS
    for height in range(2, 10):
        assert len(rows_of(review, height=height)) <= min(
            agent_panel.REVIEW_MAX_ROWS, height), height


def test_the_block_draws_nothing_below_two_rows():
    review = state_after_work()
    settled(review, status="PASS")
    assert rows_of(review, height=1) == []
    assert rows_of(review, height=0) == []
    assert len(rows_of(review, height=2)) == 2


def test_the_rows_are_measured_and_never_exceed_the_width():
    from agent_ui import display_width
    review = state_after_work()
    settled(review, status="FAIL",
            issues=[issue(identifier="R-%d" % n) for n in range(4)])
    for width in (18, 20, 26, 34):
        for row in rows_of(review, width=width):
            assert display_width(row) <= width, (width, row)


# --- the column shared with the plan and the panel --------------------------

def panel_state(plan=None, review=None, manager=None):
    return agent_panel.PanelState(manager, stream=Terminal(), plan=plan,
                                  review=review)


def test_the_review_is_drawn_under_the_plan_in_the_same_column():
    review = state_after_work()
    settled(review, status="FAIL", issues=[issue()])
    state = panel_state(plan=worked_plan(), review=review)
    frame = state.frame(100, rows=24)
    assert frame is not None
    left, join = frame
    rows = [visible(row) for row in join([""] * 6)]
    text = "\n".join(rows)
    assert "PLAN 0/3" in text, text
    assert "REVIEW 1/3" in text, text
    assert text.index("PLAN") < text.index("REVIEW"), text


def test_a_review_with_no_plan_still_gets_the_column():
    review = state_after_work()
    settled(review, status="PASS")
    frame = panel_state(review=review).frame(100, rows=24)
    assert frame is not None
    rows = [visible(row) for row in frame[1]([""] * 4)]
    assert any("REVIEW" in row for row in rows), rows


def test_a_plan_with_no_review_is_unchanged():
    """The whole point of the callable: a turn that never reviewed anything
    gets exactly the column it got before this feature existed."""
    plan = worked_plan()
    with_review = panel_state(plan=plan, review=agent_review.ReviewState())
    without = panel_state(plan=plan)
    assert ([visible(r) for r in with_review.frame(100, rows=24)[1]([""] * 4)] ==
            [visible(r) for r in without.frame(100, rows=24)[1]([""] * 4)])


def test_the_column_is_refused_below_the_two_column_width():
    """A block that swallowed the prompt box would take away the thing the
    user was using. /review is the way in at that width."""
    review = state_after_work()
    settled(review, status="FAIL", issues=[issue()])
    state = panel_state(plan=worked_plan(), review=review)
    assert state.frame(agent_panel.TWO_COLUMN_MIN - 1, rows=24) is None
    assert state.frame(agent_panel.TWO_COLUMN_MIN, rows=24) is not None


def test_the_plan_keeps_the_column_when_there_is_no_room_for_both():
    """The plan is the primary task display, and it keeps the column outright
    rather than being shortened to nothing."""
    review = state_after_work()
    settled(review, status="PASS")
    state = panel_state(plan=worked_plan(), review=review)
    rows = [visible(row) for row in
            state._task_block(state.plan_now(), state.review_now(),
                              state.verify_now(), 30, 4)]
    assert any("PLAN" in row for row in rows), rows
    assert not any("REVIEW" in row for row in rows), rows


def test_both_are_kept_and_shortened_when_there_is_just_room():
    review = state_after_work()
    settled(review, status="FAIL",
            issues=[issue(identifier="R-%d" % n) for n in range(3)])
    plan = worked_plan("One", "Two", "Three", "Four", "Five", "Six")
    state = panel_state(plan=plan, review=review)
    rows = [visible(row) for row in
            state._task_block(state.plan_now(), state.review_now(),
                              state.verify_now(), 30, 6)]
    assert any("PLAN" in row for row in rows), rows
    assert any("REVIEW" in row for row in rows), rows
    assert len(rows) <= 6, rows


def test_the_panel_takes_the_column_when_it_is_open_and_the_window_is_narrow():
    """The panel is the thing with focus in panel-only mode; a review squeezed
    in above it would cost the cards the rows that make them readable."""
    manager = agent_manager.AgentManager()
    manager.spawn("work")
    review = state_after_work()
    settled(review, status="PASS")
    state = panel_state(plan=worked_plan(), review=review, manager=manager)
    assert state.open_panel(agent_panel.PANEL_ONLY_MIN + 1)
    left, join = state.frame(agent_panel.PANEL_ONLY_MIN + 1, rows=24)
    rows = [visible(row) for row in join([""] * 4)]
    assert not any("REVIEW" in row for row in rows), rows
    assert any("AGENTS" in row for row in rows), rows


def test_the_review_shares_the_column_with_an_open_panel_when_it_is_wide():
    manager = agent_manager.AgentManager()
    manager.spawn("work")
    review = state_after_work()
    settled(review, status="FAIL", issues=[issue()])
    state = panel_state(plan=worked_plan(), review=review, manager=manager)
    assert state.open_panel(120)
    left, join = state.frame(120, rows=30)
    text = "\n".join(visible(row) for row in join([""] * 10))
    assert "PLAN" in text and "REVIEW" in text and "AGENTS" in text, text


def test_a_review_provider_that_raises_is_drawn_as_no_review():
    def boom():
        raise RuntimeError("no")

    state = panel_state(plan=worked_plan(), review=boom)
    assert state.review_now() is None
    assert state.frame(100, rows=24) is not None      # the plan still draws


# --- the command ------------------------------------------------------------

def test_the_review_command_reports_the_state_and_the_history():
    import agent_commands
    session = agent_session.Session()
    settled(session.review, status="FAIL",
            issues=[issue(title="Expiry is not enforced")])
    settled(session.review, status="PASS", summary="Fixed it.")
    result = agent_commands.dispatch("/review", session)
    text = "\n".join(str(row) for row in result.rows)
    assert "Review #1" in text and "FAIL" in text
    assert "Review #2" in text and "PASS" in text
    assert "Fixed it." in text


def test_the_review_command_says_so_when_nothing_has_been_reviewed():
    import agent_commands
    session = agent_session.Session()
    session.review.note_change("write_file", ("a.py",))
    text = "\n".join(str(row) for row in
                     agent_commands.dispatch("/review", session).rows)
    assert "Nothing has been reviewed" in text
    assert "a.py" in text


def test_the_review_command_works_with_no_session_at_all():
    import agent_commands
    assert agent_commands.dispatch("/review", None) is not None


def test_the_review_command_cannot_change_the_state():
    """A user cannot pass or fail a review from here any more than the model
    can: the state moves only when a reviewer agent reports."""
    import agent_commands
    session = agent_session.Session()
    settled(session.review, status="FAIL", issues=[issue()])
    for text in ("/review", "/review pass", "/review PASS"):
        agent_commands.dispatch(text, session)
        assert not session.review.passed, text


# --- the session ------------------------------------------------------------

def test_a_session_holds_one_review_and_empties_it_in_place():
    """The loop puts this object in the action context BEFORE it calls
    begin_turn, so a new object there would leave the review action writing
    into state the gate no longer reads -- the gate silently off, with nothing
    anywhere to notice it by."""
    session = agent_session.Session()
    review = session.review
    settled(review, status="FAIL", issues=[issue()])
    session.begin_turn("something else", "prompt")
    assert session.review is review
    assert session.review.state == agent_review.IDLE
    assert session.review.cycles == 0


def test_clearing_the_conversation_retires_the_review_before_it_empties():
    """A session told to forget the conversation that would still refuse to
    answer until an invisible review had passed would be the worst of both."""
    session = agent_session.Session()
    session.record("a task", "an answer")
    settled(session.review, status="FAIL", issues=[issue()])
    session.clear()
    assert len(session) == 0
    assert session.review.state == agent_review.IDLE
    assert not session.review


def test_a_finished_review_survives_until_the_next_question_starts():
    """Retired at the START of the next turn rather than at the end of this
    one, so a review that passed stays on screen beside the answer it let
    out."""
    session = agent_session.Session()
    settled(session.review, status="PASS")
    session.record("a task", "an answer")
    assert session.review.passed
    session.begin_turn("the next question", "prompt")
    assert not session.review


def test_review_state_does_not_leak_between_sessions():
    first = agent_session.Session()
    settled(first.review, status="FAIL", issues=[issue()])
    second = agent_session.Session()
    assert second.review is not first.review
    assert second.review.state == agent_review.IDLE
    assert not second.review.passed


# --- the loop ---------------------------------------------------------------

def test_the_loop_records_a_change_and_a_run_against_the_review():
    """`bash` is the one verb that is BOTH, and that is why `note_work` asks
    the two questions with two `if`s rather than an `if/elif`.

    A command changes the tree -- `make`, a formatter, `> file` -- so it makes
    a passed review stale, which is what putting `bash` in MUTATING_ACTIONS
    buys. It is also the run the reviewer is shown. An `elif` here let the
    first branch swallow it and every command silently stopped appearing under
    "WHAT VERIFICATION ACTUALLY RAN".

    Its changed path is `(unnamed)`, the convention `agent_review` already
    uses for a mutating action whose paths TMT cannot name. That is the honest
    entry rather than the tidy one: TMT knows a command ran and does not know
    what it touched, and saying so beats both inventing a path and claiming
    nothing changed. `agent_verify_engine` already filters the marker out when
    it decides what to check."""
    session = agent_session.Session()
    TMT.note_work(session, "write_file", {"path": "src/a.py"})
    TMT.note_work(session, "patch_file", {"path": "src/b.py"})
    TMT.note_work(session, "read_file", {"path": "src/c.py"})
    TMT.note_work(session, "bash", {"command": "python run_tests.py"})
    assert session.review.changed_paths == ("src/a.py", "src/b.py", "(unnamed)")
    assert session.review.verification == ("bash python run_tests.py",)


def test_a_read_only_command_still_makes_a_passed_review_stale():
    """The cost of `bash` being in MUTATING_ACTIONS by name: an `ls` re-gates
    an answer it did not need to. Pinned rather than left to be discovered,
    because it is the trade that was chosen -- the other way round leaves a
    review standing over a tree that `make` has since rewritten, and TMT
    cannot tell the two commands apart from the verb alone."""
    session = agent_session.Session()
    settled(session.review, status="PASS")
    assert session.review.passed
    TMT.note_work(session, "bash", {"command": "ls"})
    assert not session.review.passed


def test_a_legacy_run_file_is_recorded_by_the_command_it_became():
    """`bash` is the only verb `note_work` records a run for, and a model
    written against `run_file` never emits it -- so what has to reach the
    review state is the object AFTER `adopt_verb`, which is the order the loop
    dispatches in. Recorded here through the real translation rather than by
    hand, because a hand-written `{"action": "bash"}` would be testing the
    recorder against a shape nothing produces.

    The translated object still carries the `path` the model wrote, beside the
    `command` built from it. The command is what is recorded: it is what would
    actually have run, and it is what the reviewer's brief is quoting under
    "WHAT VERIFICATION ACTUALLY RAN".
    """
    obj = {"action": "run_file", "path": "run_tests.py"}
    agent_actions.adopt_verb(obj)
    assert obj["action"] == "bash", obj
    session = agent_session.Session()
    TMT.note_work(session, obj["action"], obj)
    assert session.review.verification == ("bash python run_tests.py",)


def test_note_work_survives_a_session_without_a_review():
    class Bare:
        pass

    TMT.note_work(Bare(), "write_file", {"path": "a.py"})
    TMT.note_work(None, "write_file", {"path": "a.py"})


def test_the_completion_gate_asks_the_plan_first_and_then_the_review():
    session = agent_session.Session()
    session.plan.create(["Implement it", "Test it", "Independent review"])
    session.review.note_change("write_file", ("a.py",))
    respond = {"action": "end_conversation", "message": "done"}

    held, line = TMT.completion_block(session, respond)
    assert "plan you made" in held
    assert "Plan not finished" in line

    session.plan.update(updates=[{"step": n, "status": "completed"}
                                 for n in (1, 2, 3)])
    held, line = TMT.completion_block(session, respond)
    assert "needs an independent review" in held
    assert "Review required and not yet run" in line

    settled(session.review, status="PASS")
    assert TMT.completion_block(session, respond) == ("", "")


def test_a_passing_review_does_not_excuse_an_incomplete_plan():
    session = agent_session.Session()
    session.plan.create(["Implement it", "Test it", "Independent review"])
    session.review.note_change("write_file", ("a.py",))
    settled(session.review, status="PASS")
    held, line = TMT.completion_block(session, {"action": "end_conversation",
                                                "message": "done"})
    assert "plan you made" in held
    assert "Plan not finished" in line


def test_the_gate_is_silent_on_a_synthetic_reply():
    """A report this program made up about a failed call. There is no model
    behind it to send back to, and refusing it would hide a provider failure
    behind a review the model never had the chance to run."""
    import agent_model
    session = agent_session.Session()
    session.plan.create(["Implement it", "Test it", "Independent review"])
    session.plan.update(updates=[{"step": n, "status": "completed"}
                                 for n in (1, 2, 3)])
    session.review.note_change("write_file", ("a.py",))
    synthetic = {"action": "end_conversation", "message": "the provider failed",
                 agent_model.SYNTHETIC_KEY: True,
                 agent_model.SYNTHETIC_REASON: "provider"}
    assert TMT.review_block(session, synthetic) == ""
    assert TMT.completion_block(session, synthetic) == ("", "")


def test_the_gate_is_silent_with_no_session_or_a_non_object():
    assert TMT.review_block(None, {"action": "end_conversation"}) == ""
    assert TMT.review_block(agent_session.Session(), "end_conversation") == ""
    assert TMT.completion_block(None, {"action": "end_conversation"}) == ("", "")


def test_the_users_own_words_are_read_once_after_the_turn_begins():
    """After begin_turn and never before: retiring the review resets every
    field on it, so a choice recorded earlier would be wiped by the retirement
    that runs between.

    The words are the COMMAND now rather than a phrase. "review the changes"
    is a thing somebody says while asking for an opinion; `/review` is a
    request for the gated, independent, cycle-limited reviewer, and only the
    second one buys it.
    """
    session = agent_session.Session()
    session.begin_turn("add the feature /review", "prompt")
    TMT.note_capability_choices(session)
    assert session.capabilities.review is True
    assert session.review.user_choice is True
    assert session.review.is_required(None)
    # A new question with no command in it: nothing carries over.
    session.begin_turn("what does zip do?", "prompt")
    TMT.note_capability_choices(session)
    assert session.capabilities.review is False
    assert session.review.user_choice is False
    assert not session.review.is_required(None)


def test_asking_for_a_review_in_prose_no_longer_buys_one():
    """The reversal, stated where somebody will look for it. Every phrase here
    used to turn an independent review on; none of them does now, because the
    capability is the user's to spend and spending it is a command.
    """
    session = agent_session.Session()
    for said in ("add the feature and review the changes",
                 "please review my code", "do a code review afterwards",
                 "reviewing", "/reviewing", "peer review this"):
        session.begin_turn(said, "prompt")
        TMT.note_capability_choices(session)
        assert session.capabilities.review is False, said
        assert session.review.user_choice is False, said


def test_the_release_warning_reaches_the_user_only_at_the_limit():
    session = agent_session.Session()
    session.review.note_change("write_file", ("a.py",))
    assert TMT.review_release_warning(session) == ""
    for _ in range(agent_review.MAX_REVIEW_CYCLES):
        settled(session.review, status="FAIL", issues=[issue()])
    assert "Review did not pass" in TMT.review_release_warning(session)


# --- end to end through TMT.main -------------------------------------------
#
# Every task text here asks for `/plan /review` and deliberately does NOT ask
# for `/verify`. Each test below is about the REVIEW gate, and a turn that had
# authorised verification too would be held by the verify gate first and would
# be testing two gates at once. The interaction between them is tested where it
# belongs, in test_agent_verify_engine.test_verification_is_asked_for_before_review.
#
# This used to read "no verification needed", declining in prose, because
# verification was required on exactly the evidence a review was and had to be
# turned off. Not asking is the whole of it now: a capability nobody wrote the
# command for was never on, so these tests also stand as the plainest statement
# of the reversal -- three plan steps and a written file, and no verification.

PLAN_STEPS = ["Implement it", "Run the tests", "Independent review"]


def _plan_created(*steps):
    return json.dumps({"action": "plan", "operation": "create",
                       "steps": list(steps) or PLAN_STEPS})


# A plan whose last step is NOT named review, so every step can be completed
# and the review gate is the only thing left holding the answer. That is the
# half of the pair that did not exist before this feature, and it needs a plan
# the plan gate is finished with.
FLAT_STEPS = ("Implement it", "Run the tests", "Explain the change")


def _wrote(path="feature.py"):
    return json.dumps({"action": "write_file", "path": path,
                       "content": "VALUE = 1\n"})


def _completed(*positions):
    return json.dumps({"action": "plan", "operation": "update",
                       "steps": [{"step": n, "status": "completed"}
                                 for n in positions]})


def _answered(message):
    return json.dumps({"action": "end_conversation", "message": message})


def test_an_answer_is_held_until_a_review_has_actually_passed():
    """The whole feature, end to end through TMT.main. All three enforcements
    fire, each in its own place: the plan will not let the turn answer with a
    step outstanding, the review STEP will not be completed while the review
    has not passed, and the review itself will not pass until the finding is
    fixed. Only then does the answer reach the user."""
    answer = "Added the feature."
    review = json.dumps({"action": "review"})
    replies = [
        _plan_created(),
        _wrote(),
        _completed(1, 2),
        _answered("Too early."),         # refused: S3 is still outstanding
        _completed(3),                   # refused: the review has not passed
        review,                          # fails
        _wrote("feature.py"),            # the fix
        review,                          # passes
        _completed(3),                   # now allowed
        _answered(answer),
    ]
    with Reviewer(
            reviewer_reply(status="FAIL", summary="Expiry is not enforced.",
                           issues=[issue(title="Expiry is not enforced",
                                         file="feature.py", line=1)]),
            reviewer_reply(status="PASS", summary="The fix is right.")):
        drawn, seen, console = drive_session(
            ["add the feature /plan /review", "quit"], replies)

    assert len(seen) == len(replies), len(seen)
    text = visible(drawn)
    assert answer in text, text[-3000:]
    assert "Too early." not in text
    # Every refusal reached the model, in its own next input.
    joined = "\n".join(message["content"] for request in seen
                       for message in request if message["role"] == "user")
    assert "The plan you made is the contract" in joined
    assert "is the review step and the review has not passed" in joined
    assert "Expiry is not enforced" in joined
    # And the user saw why the answer was held.
    assert "Plan not finished" in text


def test_the_review_gate_holds_an_answer_a_complete_plan_would_have_let_out():
    """The two conditions are separate and neither excuses the other. Here the
    plan is finished and the answer is still refused, which is the half that
    did not exist before this feature."""
    answer = "Added the feature."
    review = json.dumps({"action": "review"})
    replies = [
        _plan_created(*FLAT_STEPS),
        _wrote(),
        _completed(1, 2, 3),
        _answered("Too early."),         # refused: no review has been run
        review,                          # fails
        _wrote("feature.py"),            # the fix
        _answered("Still too early."),   # refused: the review failed
        review,                          # passes
        _answered(answer),
    ]
    with Reviewer(
            reviewer_reply(status="FAIL", summary="Expiry is not enforced.",
                           issues=[issue(title="Expiry is not enforced")]),
            reviewer_reply(status="PASS", summary="The fix is right.")):
        drawn, seen, console = drive_session(
            ["add the feature /plan /review", "quit"], replies)

    assert len(seen) == len(replies), len(seen)
    text = visible(drawn)
    assert answer in text, text[-3000:]
    assert "Too early." not in text and "Still too early." not in text
    joined = "\n".join(message["content"] for request in seen
                       for message in request if message["role"] == "user")
    assert "needs an independent review" in joined
    assert "Expiry is not enforced" in joined
    assert "cannot mark the review passed yourself" in joined
    assert "Review required and not yet run" in text
    assert "Review found 1 blocking issue" in text


def test_an_answer_goes_out_when_no_review_was_required():
    """Most turns. The gate is a consequence of having done substantial work,
    not a tax on answering."""
    answer = "The retry limit is 3."
    drawn, seen, console = drive_session(
        ["where is the retry limit?", "quit"], [_answered(answer)])
    assert len(seen) == 1
    assert answer in visible(drawn)


def test_a_small_change_with_no_plan_is_not_gated():
    answer = "Patched it."
    drawn, seen, console = drive_session(
        ["set the timeout to 30", "quit"], [_wrote("net.py"), _answered(answer)])
    assert len(seen) == 2
    assert answer in visible(drawn)


def test_a_review_that_errors_holds_the_answer_and_says_so():
    """A review that did not complete is not a review that passed, and this is
    the path that proves the loop treats it that way rather than the state
    object alone."""
    review = json.dumps({"action": "review"})
    replies = [
        _plan_created(*FLAT_STEPS),
        _wrote(),
        _completed(1, 2, 3),
        review,                          # comes back unreadable
        _answered("Reviewed, honest."),  # refused
        review,                          # this one works
        _answered("Added the feature."),
    ]
    with Reviewer(json.dumps({"action": "internal_response",
                              "response": "Looks fine to me."}),
                  reviewer_reply(status="PASS", summary="Read it properly.")):
        drawn, seen, console = drive_session(
            ["add the feature /plan /review", "quit"], replies)

    assert len(seen) == len(replies), len(seen)
    text = visible(drawn)
    assert "Added the feature." in text, text[-2500:]
    assert "Reviewed, honest." not in text
    joined = "\n".join(message["content"] for request in seen
                       for message in request if message["role"] == "user")
    assert "not a review that passed" in joined
    assert "Review did not complete" in text


def test_the_cycle_limit_releases_the_answer_and_tells_the_user():
    """It must not silently continue forever, and it must not silently stop
    caring either. The answer goes out carrying the reason it was let out."""
    review = json.dumps({"action": "review"})
    replies = [_plan_created(*FLAT_STEPS), _wrote(), _completed(1, 2, 3)]
    for number in range(agent_review.MAX_REVIEW_CYCLES):
        replies += [review, _wrote("fix%d.py" % number)]
    replies += [_answered("Done what I could.")]
    with Reviewer(reviewer_reply(status="FAIL", summary="Still wrong.",
                                 issues=[issue(title="Still wrong")])):
        drawn, seen, console = drive_session(
            ["add the feature /plan /review", "quit"], replies)
    assert len(seen) == len(replies), len(seen)
    text = visible(drawn)
    assert "Done what I could." in text, text[-2500:]
    assert "Review did not pass" in text
    assert "3 reviews ran" in text


def test_the_next_question_is_not_gated_by_the_last_ones_review():
    """A plan and a review both belong to one task. Left standing, the
    previous task's failed review would refuse the answer to a question that
    has nothing to do with it."""
    replies = [
        _plan_created(*FLAT_STEPS), _wrote(), _completed(1, 2, 3),
        json.dumps({"action": "review"}),
        _answered("First answer."),
        _answered("Second answer."),
    ]
    with Reviewer(reviewer_reply(status="PASS", summary="Fine.")):
        drawn, seen, console = drive_session(
            ["add the feature /plan /review", "and now something unrelated",
             "quit"], replies)
    assert len(seen) == len(replies), len(seen)
    text = visible(drawn)
    assert "First answer." in text and "Second answer." in text
    assert seen[-1][-1]["content"] == "and now something unrelated"


def test_clear_after_a_failed_review_does_not_end_the_session():
    """`/clear` is what a user reaches for when a session is behaving oddly,
    and it must not be the gesture that kills it."""
    replies = [
        _plan_created(*FLAT_STEPS), _wrote(), _completed(1, 2, 3),
        json.dumps({"action": "review"}),
        _answered("held"),
        _answered("after the clear"),
    ]
    with Reviewer(reviewer_reply(status="FAIL", summary="No.",
                                 issues=[issue()])):
        drawn, seen, console = drive_session(
            ["add the feature /plan /review", "/clear", "a fresh question",
             "quit"], replies)
    text = visible(drawn)
    assert "after the clear" in text, text[-2000:]
    assert [m["role"] for m in seen[-1]] == ["system", "user"], seen[-1]
