"""The background agents under plain-text tags (HANDOFF 7 steps 6 and 7).

The worker, the note agent and the reviewer read their model's replies the way
the main agent's are read: a reply with no action blocks is the final answer
and ends the run, text before an action block is that action's progress line,
and a verdict from the reviewer is either the structured object (accepted
unchanged) or the plain shape, `VERDICT: approve` or `VERDICT: revise` on the
first line with the notes after it. Model replies are scripted; nothing here
makes a request.
"""

import json

import agent_manager
import agent_review
import agent_worker
from test_agent_agents import Clock, Executor
from test_agent_agents import run as run_agent
from test_agent_reply_format import ReplyFormat
from test_agent_review import Reviewer, issue, result_json, run_review, state_after_work, worked_plan


class Asked:
    """Scripted replies that also record the keywords the loop passed `ask`."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, messages, on_event=None, model=None, max_tokens=None,
                 quiet=False, **extra):
        self.calls.append({"messages": [dict(m) for m in messages], "extra": extra})
        return self.replies.pop(0)

    def last_user(self, index):
        return self.calls[index]["messages"][-1]["content"]


READ_A = "/read_file/ /path/ a.py //path/ //read_file/"


# --- the worker ----------------------------------------------------------------------

def test_a_worker_reply_with_no_action_blocks_is_its_final_answer():
    manager = agent_manager.AgentManager(clock=Clock())
    record = manager.spawn("check a.py")
    ask = Asked(["Nothing needed changing: a.py already has the fix."])
    with ReplyFormat("tags"):
        answer = run_agent(record, manager, ask, Executor("x"))
    assert answer == "Nothing needed changing: a.py already has the fix.", answer
    assert len(ask.calls) == 1
    assert ask.calls[0]["extra"] == {"plain_text": True}, ask.calls[0]["extra"]


def test_a_worker_runs_an_action_block_then_ends_on_a_text_only_reply():
    manager = agent_manager.AgentManager(clock=Clock())
    record = manager.spawn("read a.py and report")
    execute = Executor("x = 1")
    ask = Asked([READ_A, "a.py holds one assignment, x = 1."])
    with ReplyFormat("tags"):
        answer = run_agent(record, manager, ask, execute)
    assert answer == "a.py holds one assignment, x = 1.", answer
    assert execute.actions == ["read_file"], execute.actions
    told = ask.last_user(1)
    assert "x = 1" in told and "final answer" in told, told
    assert "internal_response" not in told, told


def test_text_before_a_worker_action_is_its_progress_and_not_its_answer():
    manager = agent_manager.AgentManager(clock=Clock())
    record = manager.spawn("read a.py")
    execute = Executor("x = 1")
    ask = Asked(["Reading a.py now.\n" + READ_A, "Read it."])
    with ReplyFormat("tags"):
        answer = run_agent(record, manager, ask, execute)
    assert answer == "Read it.", answer
    assert execute.actions == ["read_file"], execute.actions


def test_a_worker_batch_runs_and_the_run_goes_on_until_a_text_only_reply():
    manager = agent_manager.AgentManager(clock=Clock())
    record = manager.spawn("read both")
    execute = Executor("body")
    batch = READ_A + "\n/read_file/ /path/ b.py //path/ //read_file/"
    ask = Asked([batch, "Both read."])
    with ReplyFormat("tags"):
        answer = run_agent(record, manager, ask, execute)
    assert answer == "Both read.", answer
    assert execute.actions == ["read_file", "read_file"], execute.actions
    assert "final answer" in ask.last_user(1)


def test_a_worker_still_accepts_the_old_internal_response_block():
    manager = agent_manager.AgentManager(clock=Clock())
    record = manager.spawn("anything")
    ask = Asked(["/internal_response/ /response/ done the old way //response/ //internal_response/"])
    with ReplyFormat("tags"):
        assert run_agent(record, manager, ask, Executor("x")) == "done the old way"


def test_a_worker_with_an_unclosed_block_is_corrected_in_plain_words():
    manager = agent_manager.AgentManager(clock=Clock())
    record = manager.spawn("anything")
    ask = Asked(["/write_file/ /path/ a.py //path/ /content/ x //write_file/",
                 "Gave up on the write."])
    with ReplyFormat("tags"):
        answer = run_agent(record, manager, ask, Executor("x"))
    assert answer == "Gave up on the write.", answer
    complaint = ask.last_user(1)
    assert complaint.startswith("INVALID: that reply could not be read."), complaint
    for word in ("JSON", "{", "internal_response"):
        assert word not in complaint, (word, complaint)


def test_a_refusal_tells_a_plain_worker_to_give_its_final_answer():
    manager = agent_manager.AgentManager(clock=Clock())
    record = manager.spawn("anything")
    execute = Executor("x")
    ask = Asked(["/git_push/ //git_push/", "Could not push."])
    with ReplyFormat("tags"):
        assert run_agent(record, manager, ask, execute) == "Could not push."
    refused = ask.last_user(1)
    assert "REFUSED" in refused and "final answer" in refused, refused
    assert "internal_response" not in refused, refused
    assert execute.actions == [], execute.actions


def test_a_worker_under_json_is_unchanged_and_is_never_asked_for_plain_text():
    manager = agent_manager.AgentManager(clock=Clock())
    record = manager.spawn("anything")
    ask = Asked([json.dumps({"action": "read_file", "path": "a.py"}),
                 json.dumps({"action": "internal_response", "response": "done"})])
    with ReplyFormat("json"):
        assert run_agent(record, manager, ask, Executor("x")) == "done"
    assert all(call["extra"] == {} for call in ask.calls), ask.calls
    assert "internal_response when the task is done" in ask.last_user(1)


# --- the note agent ------------------------------------------------------------------

def test_the_note_agent_answers_with_a_text_only_reply():
    manager = agent_manager.AgentManager(clock=Clock())
    record = manager.spawn("where is the limit?")
    execute = Executor("src/net.py:12: MAX_RETRIES = 3")
    ask = Asked(["/grep/ /query/ MAX_RETRIES //query/ //grep/",
                 "The limit is MAX_RETRIES = 3 at src/net.py line 12."])
    with ReplyFormat("tags"):
        answer = run_agent(record, manager, ask, execute, note=True)
    assert answer == "The limit is MAX_RETRIES = 3 at src/net.py line 12.", answer
    assert execute.actions == ["grep"], execute.actions
    assert ask.calls[0]["extra"] == {"plain_text": True}


def test_the_note_agent_is_refused_a_write_in_plain_words_and_then_answers():
    manager = agent_manager.AgentManager(clock=Clock())
    record = manager.spawn("set the timeout to 30")
    execute = Executor("x")
    ask = Asked(["/patch_file/ /path/ a.py //path/ /search/ 5 //search/ /replace/ 30 //replace/ //patch_file/",
                 "I can only read; it is still 5."])
    with ReplyFormat("tags"):
        answer = run_agent(record, manager, ask, execute, note=True)
    assert answer == "I can only read; it is still 5.", answer
    assert execute.actions == [], execute.actions
    refused = ask.last_user(1)
    assert "REFUSED" in refused and "final answer" in refused, refused
    assert "internal_response" not in refused, refused


# --- the reviewer, through the loop and through the dispatcher ----------------------------

def test_a_plain_reviewer_reply_is_returned_whole_as_the_verdict_text():
    manager = agent_manager.AgentManager(clock=Clock())
    record = manager.spawn("review it")
    verdict = "VERDICT: revise\nsrc/a.py:3 - the loop never ends. Add a bound."
    ask = Asked([verdict])
    with ReplyFormat("tags"):
        answer = agent_worker.run_review(record, manager, ask=ask, execute=Executor("x"),
                                         system_prompt="p")
    assert answer == verdict, answer


def test_a_review_dispatched_under_tags_passes_on_a_plain_approve():
    review = state_after_work()
    with ReplyFormat("tags"):
        with Reviewer("VERDICT: approve\nRead the diff and the tests; both cover the change.") as reviewer:
            out = run_review(review, plan=worked_plan(), manager=agent_manager.AgentManager())
    assert out.startswith("REVIEW PASSED"), out
    assert review.state == agent_review.PASSED
    assert reviewer.calls == 1


def test_a_review_dispatched_under_tags_fails_on_a_plain_revise_and_names_the_notes():
    review = state_after_work()
    notes = "src/auth/token.py:148 - expiry is never checked. Compare expires_at to now."
    with ReplyFormat("tags"):
        with Reviewer("VERDICT: revise\n" + notes):
            out = run_review(review, plan=worked_plan(), manager=agent_manager.AgentManager())
    assert out.startswith("REVIEW FAILED - 1 blocking issue"), out
    assert "src/auth/token.py:148" in out and "expiry is never checked" in out, out
    assert review.state == agent_review.FAILED


def test_a_review_dispatched_under_tags_still_accepts_the_structured_object():
    review = state_after_work()
    reply = json.dumps(result_json(status="FAIL", summary="Expiry is not enforced.",
                                   issues=[issue(file="token.py", line=148)]))
    with ReplyFormat("tags"):
        with Reviewer(reply):
            out = run_review(review, plan=worked_plan(), manager=agent_manager.AgentManager())
    assert out.startswith("REVIEW FAILED - 1 blocking issue"), out
    assert "token.py:148" in out, out


def test_a_malformed_plain_verdict_is_a_review_that_did_not_happen():
    for reply in ("The implementation looks correct.",
                  "VERDICT: approve",
                  "VERDICT: LGTM\nlooks fine"):
        review = state_after_work()
        with ReplyFormat("tags"):
            with Reviewer(reply):
                out = run_review(review, plan=worked_plan(), manager=agent_manager.AgentManager())
        assert out.startswith("FAILED:") and "could not be read" in out, (reply, out)
        assert review.state == agent_review.ERROR, (reply, review.state)


# --- parse_result, both shapes ------------------------------------------------------------

def test_the_plain_approve_is_a_pass_with_the_notes_as_its_summary():
    result = agent_review.parse_result("VERDICT: approve\nChecked the diff.\nTests cover it.",
                                       number=2, plain_text=True)
    assert result.verdict == agent_review.PASS and result.passed
    assert result.number == 2 and result.issues == ()
    assert result.summary == "Checked the diff.\nTests cover it."


def test_the_plain_revise_is_one_blocking_finding_carrying_the_notes_whole():
    notes = "a.py:1 - wrong.\nb.py:2 - also wrong.\nTests: none."
    result = agent_review.parse_result("VERDICT: revise\n" + notes, plain_text=True)
    assert result.verdict == agent_review.FAIL and not result.passed
    assert len(result.blocking()) == 1
    assert result.issues[0].description == notes
    assert result.summary == "a.py:1 - wrong."
    assert "Required action: fix R-001" in result.describe()


def test_the_verdict_line_is_forgiving_about_case_spacing_and_emphasis_but_not_the_word():
    for first in ("verdict: Approve", "  VERDICT :   approve  ", "**VERDICT: approve**",
                  "`VERDICT: approve`"):
        result = agent_review.parse_result(first + "\nfine", plain_text=True)
        assert result.verdict == agent_review.PASS, first
    for first in ("VERDICT: approved", "VERDICT: pass", "VERDICT:", "VERDICT: approve please"):
        try:
            agent_review.parse_result(first + "\nfine", plain_text=True)
        except agent_review.ReviewError as error:
            assert "approve" in str(error) and "revise" in str(error), (first, error)
        else:
            raise AssertionError("accepted a verdict line: %r" % first)


def test_a_plain_verdict_needs_its_notes_and_a_word_in_the_middle_is_not_a_verdict():
    for text in ("VERDICT: approve", "VERDICT: revise\n   \n"):
        try:
            agent_review.parse_result(text, plain_text=True)
        except agent_review.ReviewError as error:
            assert "no notes" in str(error), error
        else:
            raise AssertionError("accepted a verdict with no notes: %r" % text)
    try:
        agent_review.parse_result("I think so.\nVERDICT: approve\nfine", plain_text=True)
    except agent_review.ReviewError as error:
        assert "VERDICT: approve" in str(error) and "revise" in str(error), error
    else:
        raise AssertionError("read a verdict that was not on the first line")


def test_the_structured_object_is_accepted_unchanged_with_or_without_plain_text():
    text = json.dumps(result_json(status="PASS_WITH_WARNINGS", summary="Fine.",
                                  issues=[issue(severity="MINOR")]))
    plain = agent_review.parse_result(text, plain_text=True)
    old = agent_review.parse_result(text)
    assert plain.as_dict() == old.as_dict()
    assert old.verdict == agent_review.PASS_WITH_WARNINGS
    # Wrapped in a sentence, as it always could be.
    wrapped = agent_review.parse_result("My review: " + text + " Thanks.", plain_text=True)
    assert wrapped.as_dict() == old.as_dict()


def test_the_plain_shape_is_not_read_without_plain_text_and_the_old_error_is_unchanged():
    try:
        agent_review.parse_result("VERDICT: approve\nfine")
    except agent_review.ReviewError as error:
        assert str(error) == ("The reviewer returned no JSON object. A review must "
                              "end with one object carrying \"status\", \"summary\" "
                              "and \"issues\"."), error
    else:
        raise AssertionError("read the plain shape under JSON")
    try:
        agent_review.parse_result("looks fine", plain_text=True)
    except agent_review.ReviewError as error:
        assert "VERDICT: approve" in str(error) and "VERDICT: revise" in str(error), error
    else:
        raise AssertionError("accepted a reply with no verdict")
