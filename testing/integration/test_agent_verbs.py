"""The two verbs that talk to the user: `send_message` and `end_conversation`.

`announce` became `send_message`, `respond` became `end_conversation`, `done`
was folded into `end_conversation`, and the `final` flag that used to make a
`respond` mean either one of them is gone. This file is about the properties
that survived that and the ones it created.

Three of them are load-bearing and everything here serves one of the three.

**Only one verb ends a turn.** The loop's terminal test is
`action == END_CONVERSATION`, and `send_message` has no terminal meaning at
all -- so no key on a message can finish a task and no key on an ending can
stop it finishing one. That used to be a flag on the action that DOES end a
task, which fails silently in the worst direction when it is forgotten.

**The three completion gates hold that one verb and never hold the other.**
A plan with outstanding steps, a review that has not passed and a verification
that has not run all refuse an `end_conversation`. None of them touches a
`send_message`, because that is how a model talks to the user WHILE the work
they are waiting on is happening.

**The old names still work and are never taught.** `canonical_action` is the
one place that knows them, it translates the MEANING rather than the spelling,
and no prompt anywhere mentions them. It is invisible when it works, so
nothing will notice if it stops working: these tests are the only thing
keeping it honest.

Nothing here blocks, sleeps, reaches a network, calls a real model or starts a
real agent thread. The loop is driven with `test_agent_cli.drive_session` and
`test_agent_cli.run_turn`; the worker loops are driven synchronously with an
injected `ask`.
"""

import json

import agent_actions
import agent_capabilities
import agent_config
import agent_manager
import agent_plan
import agent_prompt
import agent_review
import agent_subprompts
import agent_verify
import agent_worker
import TMT

from test_agent_cli import drive_session, run_turn
from test_agent_menu import visible

SEND = agent_actions.SEND_MESSAGE
END = agent_actions.END_CONVERSATION

# The names that are gone from every registry and every prompt, and still
# understood by the one function that translates them.
LEGACY = ("announce", "respond", "done")

# Everything authorised, for the prompt tests: an unauthorised prompt leaves
# whole sections out, and a search for a name in a prompt that was never built
# is not evidence about the prompt the model reads.
ALL_CAPABILITIES = "/plan /review /verify"


def obj(action, **keys):
    """One action object, as a model would send it."""
    keys["action"] = action
    return keys


def said(action, **keys):
    """One action object as JSON, for a scripted reply."""
    return json.dumps(obj(action, **keys))


def batch(*entries):
    """A batch reply, in the shape a model actually sends one."""
    return json.dumps({"actions": list(entries)})


class Transcript:
    """The one method `TMT.send_message` calls, and a record of what reached it."""

    def __init__(self):
        self.rows = []

    def emit_kind(self, kind, text):
        self.rows.append((kind, text))


class Replies:
    """Scripted model replies for a background agent, handed out in order."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, messages, on_event=None, model=None, max_tokens=None,
                 quiet=False, **extra):
        self.calls.append([dict(message) for message in messages])
        if not self.replies:
            return said("internal_response", response="ran out of script")
        return self.replies.pop(0)

    def told(self, index=1):
        """What the model was handed as the user turn of request `index`."""
        return self.calls[index][-1]["content"]


class Executor:
    """A dispatcher that records what a worker actually got to run."""

    def __init__(self, result="ok"):
        self.calls = []
        self.result = result

    def __call__(self, action_obj, context):
        self.calls.append(action_obj.get("action"))
        return self.result


def run_background(runner, replies, task="do the thing", kind="worker"):
    """Drive one background agent loop to completion. Returns (result, ask, execute)."""
    manager = agent_manager.AgentManager()
    record = manager.spawn(task, kind=kind)
    ask, execute = Replies(replies), Executor()
    try:
        result = runner(record, manager, ask=ask, execute=execute,
                        system_prompt="a background prompt")
    finally:
        manager.kill_all()
    return result, ask, execute


FINISH = said("internal_response", response="task complete")


def prompts():
    """Every prompt a model is ever shown, with everything authorised."""
    return {
        "main": agent_prompt.get_system_prompt(
            agent_capabilities.Capabilities(ALL_CAPABILITIES)),
        "worker": agent_subprompts.worker_prompt(),
        "note": agent_subprompts.note_prompt(),
        "review": agent_subprompts.review_prompt(),
    }


def action_names_in(text):
    """Every verb the text offers as an action name.

    Two forms, and only two, because those are the two the prompts use: the
    JSON an example emits, and the `name - keys: ...` line of a reference
    entry. Searching for the bare word instead would find "the work is done"
    and report the prompt as teaching a verb it does not mention.
    """
    import re
    found = set(re.findall(r'"action"\s*:\s*"([a-z_]+)"', text))
    found.update(re.findall(r'(?m)^\s*([a-z_]+) - keys:', text))
    return found


# --- the registry and the names ---------------------------------------------

def test_the_two_verbs_are_registered_and_each_takes_exactly_one_key():
    """A verb the model is taught and the validator does not know is a verb
    that fails every time it is used. Both of these are the ones a turn ENDS
    on, so an unregistered one costs the whole answer rather than one step."""
    assert agent_config.REQUIRED_KEYS[SEND] == ["message"], \
        agent_config.REQUIRED_KEYS[SEND]
    assert agent_config.REQUIRED_KEYS[END] == ["message"], \
        agent_config.REQUIRED_KEYS[END]


def test_the_old_names_are_not_registered_actions():
    """The rename is a rename, not an alias. If `respond` were still in the
    registry the two spellings would drift -- one of them would gain a key or
    a gate the other did not -- and `canonical_action` would be translating
    into a table that already had an answer of its own."""
    for name in LEGACY:
        assert name not in agent_config.REQUIRED_KEYS, name


def test_validate_action_accepts_both_verbs_with_a_message():
    """The shape the prompt teaches has to be the shape the validator takes.
    A rename that moved one and not the other would fail every well-formed
    reply, and these are the two replies a turn cannot do without."""
    assert agent_prompt.validate_action(obj(SEND, message="ok")) is None
    assert agent_prompt.validate_action(obj(END, message="ok")) is None


def test_validate_action_names_the_missing_key_rather_than_refusing_vaguely():
    """The party reading the complaint is a model, and it has to be able to
    fix it from the sentence alone. "Invalid action" is not fixable."""
    for verb in (SEND, END):
        complaint = agent_prompt.validate_action(obj(verb))
        assert complaint, verb
        assert verb in complaint, complaint
        assert "message" in complaint, complaint


def test_validate_action_does_not_know_the_old_names():
    """The compatibility net sits UPSTREAM of validation -- `_adopt_verb` runs
    first on every path -- so the validator never has to know the history.
    Asserted rather than assumed, because a validator that quietly accepted
    `respond` would let an untranslated one reach the loop's terminal test,
    which does not know it either, and the turn would run on past its own
    answer."""
    for name in LEGACY:
        complaint = agent_prompt.validate_action(obj(name, message="ok"))
        assert complaint, name
        assert "Unknown action" in complaint, complaint


def test_no_prompt_the_model_reads_offers_an_old_name():
    """The compatibility net has to stay invisible to the model, or it is not
    a rename -- it is two names for one thing, and models will keep using the
    one this file exists to retire. All four prompts are checked, with every
    capability authorised, because an unauthorised prompt leaves sections out
    and would pass by not containing very much."""
    for name, text in prompts().items():
        offered = action_names_in(text)
        for old in LEGACY:
            assert old not in offered, (name, old)


def test_the_main_prompt_offers_both_new_names():
    """The other half of the same claim: absence of the old names is only
    worth something if the new ones are actually there to be used."""
    offered = action_names_in(prompts()["main"])
    assert SEND in offered, sorted(offered)
    assert END in offered, sorted(offered)


def test_the_main_prompt_states_the_distinction_between_them():
    """Both verbs put text on the user's screen and only one ends the task.
    That is the entire difference and the entire risk, so the prompt has to
    say it rather than leave it to be inferred from two names."""
    text = prompts()["main"]
    assert "=== THE TWO VERBS THAT TALK TO THE USER ===" in text
    assert "Only one of them ends the task" in text, text[:200]
    assert "It never ends the task" in text
    assert "the only action in TMT that ends anything" in text
    # And the mistake it is guarding against, shown rather than described.
    assert '{"action":"end_conversation","message":"I am starting the ' \
           'implementation."}' in text


# --- canonical_action -------------------------------------------------------

def test_announce_becomes_send_message_and_done_becomes_an_ending():
    """The two straight renames. `announce` never ended anything and must not
    start; `done` always did and must not stop."""
    assert agent_actions.canonical_action(obj("announce", message="x")) == SEND
    assert agent_actions.canonical_action(obj("done", message="x")) == END
    assert agent_actions.canonical_action(obj("done")) == END


def test_respond_is_translated_by_what_it_meant_rather_than_by_its_name():
    """The one that could not be a table lookup. `respond` meant either verb
    depending on a flag, so translating the spelling would have turned every
    old progress note into an ending -- exactly the failure the flag was
    deleted for, arriving through the fix for it."""
    assert agent_actions.canonical_action(obj("respond", message="x")) == END
    assert agent_actions.canonical_action(
        obj("respond", message="x", final=False)) == SEND


def test_every_falsey_spelling_of_final_means_it_was_not_the_ending():
    """A model that writes "false" means false. The old flag was read as a
    string as well as a bool, and dropping that would silently turn every
    string-flagged progress note into a task that ended early."""
    for value in (False, "false", "False", "  FALSE  ", "no", "0", ""):
        assert agent_actions.canonical_action(
            obj("respond", message="x", final=value)) == SEND, repr(value)


def test_final_absent_or_true_is_still_the_ending():
    """Absent meant final under the old rules, so every reply written before
    the flag existed still means what it meant."""
    assert agent_actions.canonical_action(obj("respond", message="x")) == END
    for value in (True, "true", "yes", "1"):
        assert agent_actions.canonical_action(
            obj("respond", message="x", final=value)) == END, repr(value)


def test_send_message_is_never_turned_into_an_ending_by_any_key():
    """The property that makes the new shape safe. There is no flag to forget
    because there is no flag at all: the verb decides, and nothing riding on
    the object is read."""
    for extra in ({"final": True}, {"final": "yes"}, {"done": True},
                  {"final": True, "done": True}):
        translated = agent_actions.canonical_action(
            dict(obj(SEND, message="x"), **extra))
        assert translated == SEND, extra


def test_end_conversation_is_never_turned_into_a_message_by_any_key():
    """The mirror of the rule above, and the reason the flag had to go rather
    than be renamed: `end_conversation` with `final: false` is a contradiction
    on its face, and it is answered by not reading the key."""
    for extra in ({"final": False}, {"final": "no"}, {"final": ""}):
        translated = agent_actions.canonical_action(
            dict(obj(END, message="x"), **extra))
        assert translated == END, extra


def test_a_reply_that_is_not_a_usable_object_comes_back_empty():
    """It is called on the boundary, before validation, on whatever the model
    sent. Raising there would turn a malformed reply -- which the loop already
    knows how to hand back -- into a crash out of the turn."""
    for bad in (None, "respond", 7, [], ["respond"], {}, {"message": "x"},
                {"action": None}, {"action": 7}, {"action": ["respond"]}):
        assert agent_actions.canonical_action(bad) == "", repr(bad)


def test_every_other_registered_action_passes_through_untouched():
    """The translation is two names and one flag wide. A `read_file` that came
    back as anything else would be a rename nobody asked for, silently
    dispatching one verb as another."""
    for name in agent_config.REQUIRED_KEYS:
        assert agent_actions.canonical_action({"action": name}) == name, name


def test_the_legacy_table_holds_exactly_the_four_names_a_lookup_can_settle():
    """Two renames' worth of names, and no more. This test exists so the net
    cannot grow silently: every entry is a name the model is no longer taught
    and TMT still answers, so an addition nobody argued for is an old spelling
    quietly staying alive.

    `announce` and `done` are the speaking verbs this file is about.
    `search_files` and `find_text` are the two search actions `grep` replaced,
    and they are here for the same reason the other two are -- a model reaching
    for a name it learned would otherwise spend its retry budget being told the
    name is wrong.

    `respond` is deliberately absent from all four: its meaning depends on a
    key, so it is decided in code above the lookup. A table entry for it would
    be a second answer to the same question, and the wrong one half the time.

    The table settles the SPELLING only. `search_files` was case-insensitive
    and `grep` is not, so half of what that name meant lived in a default
    rather than in a key -- `adopt_verb` is what puts it back, and the wiring
    tests are where that is pinned.
    """
    assert agent_actions._LEGACY_ACTIONS == {
        "announce": SEND,
        "done": END,
        "search_files": "grep",
        "find_text": "grep",
    }


# --- send_message does not finalize -----------------------------------------

def test_is_send_message_reads_the_verb_and_nothing_else():
    """The loop's one question about a reply that talks: does it end the task.
    One shape answers it, and the point of the rename is that there is nothing
    else to look at -- a second condition here would be a second thing a model
    could get wrong."""
    assert TMT.is_send_message(obj(SEND, message="x")) is True
    assert TMT.is_send_message(obj(END, message="x")) is False


def test_is_send_message_ignores_a_final_flag_riding_on_the_message():
    """`final: true` is exactly what would have ended the old verb. It must
    not end this one, because there is nothing here that reads it -- which is
    the whole reason the two meanings became two verbs."""
    assert TMT.is_send_message(obj(SEND, message="x", final=True)) is True
    assert TMT.is_send_message(obj(SEND, message="x", final="yes")) is True


def test_is_send_message_is_false_for_every_other_registered_action():
    """A false positive here would make the loop show an action's object as a
    sentence and step over the work entirely."""
    for name in agent_config.REQUIRED_KEYS:
        if name == SEND:
            continue
        assert TMT.is_send_message({"action": name}) is False, name


def test_is_send_message_is_false_for_things_that_are_not_objects():
    """It is asked on the boundary, about whatever the model sent, so it has
    to answer rather than raise. An AttributeError here would end the session
    over a malformed reply the loop already knows how to hand back."""
    for bad in (None, "send_message", 7, [], ()):
        assert TMT.is_send_message(bad) is False, repr(bad)


def test_a_message_goes_to_the_transcript_as_progress_not_as_an_answer():
    """It is permanent and it belongs in the scrollback -- but it is not the
    answer, so drawing it in the answer's box would put two endings on screen
    for a turn that had one."""
    transcript = Transcript()
    assert TMT.send_message(obj(SEND, message="  Reading  the parser.  "),
                            transcript) is True
    assert transcript.rows == [("progress", "Reading the parser.")], transcript.rows


def test_a_message_with_nothing_in_it_draws_nothing():
    """An empty sentence still costs the step -- the loop charges that -- but
    it must not put a blank row in the scrollback."""
    transcript = Transcript()
    for empty in (obj(SEND), obj(SEND, message=""), obj(SEND, message="   "),
                  obj(SEND, message=None)):
        assert TMT.send_message(empty, transcript) is False, empty
    assert transcript.rows == [], transcript.rows


def test_neither_verb_draws_an_event_of_its_own():
    """`end_conversation` IS the answer and `send_message` was already drawn
    before the work it announced. An event here would print each of them a
    second time, under the row that had just said it."""
    assert agent_actions.action_event(SEND, obj(SEND, message="x"), "x") is None
    assert agent_actions.action_event(END, obj(END, message="x"), "x") is None


def test_execute_action_returns_the_message_for_both_and_raises_for_neither():
    """The dispatcher has to answer them even though the loop handles them
    itself, because a batch's report has to have something to record."""
    assert agent_actions.execute_action(obj(SEND, message="hello"), {}) == "hello"
    assert agent_actions.execute_action(obj(END, message="goodbye"), {}) == "goodbye"
    # A missing message is not an exception out of the dispatcher.
    assert agent_actions.execute_action(obj(SEND), {}) == ""
    assert agent_actions.execute_action(obj(END), {})


def test_three_messages_then_an_ending_all_reach_the_user():
    """The requirement, through the real loop. Every one of the messages is
    drawn, none of them ends the turn, the model is asked again after each,
    and the last text is the answer.

    Four model calls is the assertion that matters: three would mean a message
    ended the turn early, and five would mean the ending did not end it."""
    texts = ["Reading the parser first.", "The retry count is in two places.",
             "Both are changed; running the tests."]
    answer = "Changed both retry loops and the suite is green."
    drawn, seen, _ = drive_session(
        ["fix the retries", "quit"],
        [said(SEND, message=text) for text in texts] + [said(END, message=answer)])

    assert len(seen) == 4, len(seen)
    screen = visible(drawn)
    for text in texts:
        assert text in screen, (text, screen[-3000:])
    assert answer in screen, screen[-3000:]
    # The answer is last, so the messages really did come before the ending
    # rather than the ending having been shown and the turn run on past it.
    assert max(screen.index(text) for text in texts) < screen.index(answer), screen[-3000:]


def test_the_model_is_told_after_a_message_that_the_task_is_not_finished():
    """The one mistake this verb invites is a model reading "I have told the
    user" as "I am done". The nudge rides on the next request rather than on a
    gate, so it costs nothing and cannot fail a turn."""
    _drawn, seen, _ = drive_session(
        ["do the thing", "quit"],
        [said(SEND, message="Starting."), said(END, message="Finished.")])

    handed_back = seen[1][-1]["content"]
    assert handed_back == TMT._MESSAGE_SENT, handed_back
    assert "did NOT end the task" in handed_back, handed_back
    assert "send_message never ends anything" in handed_back, handed_back
    # And what the model wrote is put back as its own turn, so it is looking
    # at the sentence it is being told about.
    assert seen[1][-2]["role"] == "assistant", seen[1][-2]
    assert "Starting." in seen[1][-2]["content"], seen[1][-2]


def test_a_message_carrying_final_true_still_does_not_end_the_turn():
    """Driven through the loop rather than only through `is_send_message`,
    because the property being claimed is about the turn and not about one
    function: the key that used to end the old verb reaches the real loop and
    changes nothing."""
    drawn, files = run_turn([
        said(SEND, message="Starting now.", final=True),
        said("write_file", path="after.txt", content="q"),
        said(END, message="Ran to the end."),
    ])
    assert "Starting now." in drawn, drawn
    assert files.get("after.txt") == "q", files
    assert "Ran to the end." in drawn, drawn


# --- end_conversation finalizes ---------------------------------------------

def test_nothing_runs_after_an_ending():
    """The other half of the contract. The turn stops there: the model is not
    asked again, and the action it would have sent next never happens.

    Two drives because the two halves are visible from different places --
    `drive_session` counts the requests and `run_turn` reads the workspace --
    and the file is the half that cannot be argued with."""
    replies = [said(END, message="All finished."),
               said("write_file", path="after.txt", content="q")]

    _drawn, seen, _ = drive_session(["do the thing", "quit"], replies)
    assert len(seen) == 1, len(seen)

    drawn, files = run_turn(replies)
    assert "All finished." in drawn, drawn
    assert "after.txt" not in files, files


def test_an_ending_inside_a_batch_stops_the_entries_after_it():
    """A batch is a list of actions and the ending can be anywhere in it. The
    entries before it are work and must run; the entries after it are work the
    turn has already declared finished."""
    drawn, files = run_turn([
        batch(obj("write_file", path="before.txt", content="1"),
              obj(END, message="Wrote the first file."),
              obj("write_file", path="after.txt", content="2")),
    ])
    assert files.get("before.txt") == "1", files
    assert "after.txt" not in files, files
    assert "Wrote the first file." in drawn, drawn


def test_the_ending_is_the_only_verb_the_loop_stops_on():
    """Asserted against TMT.py's own source, because the property is about
    that comparison. It was a two-name tuple and is one constant now; a second
    name creeping back in is a second way for a turn to end, and only one of
    them would be gated."""
    from pathlib import Path
    source = (Path(TMT.__file__).resolve()).read_text(encoding="utf-8")
    assert "if action == END_CONVERSATION:" in source
    assert "if sub_action == END_CONVERSATION:" in source
    for name in LEGACY:
        assert '"%s"' % name not in source, name


# --- the gates hold the ending and never hold the message -------------------

def unfinished_plan():
    return agent_plan.Plan(["Implement it", "Run the tests"])


def failing_review():
    review = agent_review.ReviewState()
    review.note_user_choice(True)
    review.note_change("write_file", ("a.py",))
    review.begin()
    review.settle(agent_review.parse_result(json.dumps({
        "status": "FAIL", "summary": "The expiry is never checked.",
        "issues": [{"id": "R-001", "severity": "MAJOR",
                    "title": "Expiry is not enforced",
                    "description": "token.py reads the claim and ignores it.",
                    "file": "token.py", "line": 148}]})))
    return review


def unrun_verification():
    state = agent_verify.VerificationState()
    state.note_user_choice(True)
    state.note_change("write_file", ("a.py",))
    return state


def test_the_plan_gate_holds_the_ending_and_names_the_outstanding_step():
    """The gate keys on the new verb, so this is the test that the rename did
    not quietly switch it off: a gate looking for a name nothing sends any
    more would return "" for every reply and let every answer through, with
    no error anywhere to notice. "Finish the plan" is also not actionable, so
    the refusal has to say which step."""
    held = agent_plan.refusal(unfinished_plan(), END)
    assert held.startswith("BLOCKED"), held
    assert "S1: Implement it" in held, held


def test_the_review_gate_holds_the_ending_and_names_the_finding():
    """The same claim for the second gate, and it has to be made separately:
    the three gates test the verb independently, so one of them silently
    off is one third of the guarantee gone with nothing else failing."""
    held = agent_review.refusal(failing_review(), None, END)
    assert held.startswith("BLOCKED"), held
    assert "R-001" in held, held


def test_the_verification_gate_holds_the_ending_when_nothing_has_run():
    """And the third, for the same reason. This one is the gate that produces
    evidence rather than judgement, so an answer that walked past it would be
    an unverified answer with nothing on screen saying so."""
    held = agent_verify.refusal(unrun_verification(), None, END)
    assert held.startswith("BLOCKED"), held
    assert "verif" in held.lower(), held


def test_none_of_the_three_gates_holds_a_message():
    """The sharpest requirement of the rename, and it is asserted against all
    three at once because it is one rule rather than three coincidences.

    Gating `send_message` would leave the user watching silence during exactly
    the work they most want narrated: a plan being worked step by step, a
    review being fixed, a test suite running. The verb that talks is the verb
    that has to keep working while the verb that finishes is held."""
    plan, review, verification = (unfinished_plan(), failing_review(),
                                  unrun_verification())
    # Each gate is genuinely refusing something, or "" would prove nothing.
    assert agent_plan.refusal(plan, END)
    assert agent_review.refusal(review, None, END)
    assert agent_verify.refusal(verification, None, END)

    assert agent_plan.refusal(plan, SEND) == ""
    assert agent_review.refusal(review, None, SEND) == ""
    assert agent_verify.refusal(verification, None, SEND) == ""


def test_none_of_the_three_gates_holds_an_ordinary_action():
    """Holding a read or a patch would stop the model doing the very thing the
    refusal is asking it to do."""
    plan, review, verification = (unfinished_plan(), failing_review(),
                                  unrun_verification())
    for action in ("read_file", "patch_file", "run_file", "git_diff", "plan",
                   "review", "verify", "spawn_agent", "internal_response"):
        assert agent_plan.refusal(plan, action) == "", action
        assert agent_review.refusal(review, None, action) == "", action
        assert agent_verify.refusal(verification, None, action) == "", action


def test_an_ending_is_refused_while_the_plan_is_unfinished_and_never_shown():
    """The plan gate through the real loop. The refused answer does not reach
    the user at all -- it comes back as the model's own next input -- and a
    `send_message` still works while the refusal stands, which is the pair of
    behaviours the whole rename is for."""
    early, narration = "Everything is finished.", "Still working on step two."
    answer = "Implemented it and the tests pass."
    drawn, seen, _ = drive_session(
        ["build the feature /plan", "quit"],
        [said("plan", operation="create", steps=["Implement it", "Run the tests"]),
         said(END, message=early),
         said(SEND, message=narration),
         said("plan", operation="update", step=1, status="completed"),
         said("plan", operation="update", step=2, status="completed"),
         said(END, message=answer)])

    assert len(seen) == 6, len(seen)
    # The refusal went back to the model, naming the work rather than scolding.
    refused = seen[2][-1]["content"]
    assert refused.startswith("BLOCKED"), refused
    assert "S1: Implement it" in refused, refused

    screen = visible(drawn)
    assert early not in screen, screen[-3000:]        # never reached the user
    assert narration in screen, screen[-3000:]        # the message was not gated
    assert answer in screen, screen[-3000:]           # and the gate let go


def required_review():
    """A review state that is wanted and has not happened."""
    review = agent_review.ReviewState()
    review.note_user_choice(True)
    review.note_change("write_file", ("a.py",))
    return review


def every_refusal():
    """One refusal from each gate, in every state that produces one."""
    running = required_review()
    running.begin()
    errored = required_review()
    errored.begin()
    errored.fail("the reviewer produced no result")
    return {
        "plan": agent_plan.refusal(unfinished_plan(), END),
        "review not run": agent_review.refusal(required_review(), None, END),
        "review running": agent_review.refusal(running, None, END),
        "review errored": agent_review.refusal(errored, None, END),
        "review failed": agent_review.refusal(failing_review(), None, END),
        "verification not run": agent_verify.refusal(unrun_verification(), None, END),
    }


def test_no_refusal_sentence_still_names_an_old_verb():
    """The half of a rename that is easiest to miss, because these are not
    action names in a registry -- they are prose. All three refusals used to
    end "Do not respond again until...", and grepping the modules for a
    quoted `"respond"` finds none of them.

    It matters more here than almost anywhere: a refusal is read by a model
    that has just been stopped and is looking for the way forward, so a
    sentence naming a verb the validator no longer knows would send it
    straight into a retry loop over a word."""
    refusals = every_refusal()
    assert len(refusals) == 6, sorted(refusals)
    for label, text in refusals.items():
        assert text, label                       # each one really is refusing
        assert not action_names_in(text) & set(LEGACY), (label, text)
        for old in LEGACY:
            assert "call %s again" % old not in text, (label, old)
            assert "a %s action" % old not in text, (label, old)
    # And where a refusal does say which verb to stop calling, it is the one
    # that still exists.
    naming = [text for text in refusals.values() if "call " in text]
    assert naming, refusals
    assert any("call end_conversation again" in text for text in naming), naming


def test_a_message_after_a_refusal_does_not_spend_the_refusal_away():
    """A gate that a `send_message` could clear would be no gate at all: the
    model would say something and answer. The refusal is about the plan's
    state, and talking changes nothing about it."""
    plan = unfinished_plan()
    assert agent_plan.refusal(plan, SEND) == ""
    assert agent_plan.refusal(plan, END), "talking cleared the gate"


# --- the compatibility net --------------------------------------------------

def test_the_old_announce_still_speaks_without_ending_the_turn():
    """A rename is not worth a lost answer. A model reaching for the old name
    would otherwise spend its retries being told the name was wrong and finish
    having said nothing at all."""
    drawn, files = run_turn([
        said("announce", message="I'll inspect the files first."),
        said("write_file", path="made.txt", content="z"),
        said(END, message="Inspected and written."),
    ])
    assert "I'll inspect the files first." in drawn, drawn
    assert files.get("made.txt") == "z", files
    assert "Inspected and written." in drawn, drawn


def test_the_old_respond_with_final_false_still_speaks_without_ending():
    """The translation is of the MEANING, so the flag that used to say "and
    keep going" still says it -- through a verb that cannot say anything
    else."""
    drawn, files = run_turn([
        said("respond", message="About to write.", final=False),
        said("write_file", path="made.txt", content="z"),
        said(END, message="Written."),
    ])
    assert "About to write." in drawn, drawn
    assert files.get("made.txt") == "z", files
    assert "Written." in drawn, drawn


def test_the_old_respond_still_ends_the_turn():
    """A bare `respond` always meant the ending, and it still does. One model
    call is the whole assertion: a second would mean the translation had
    landed on `send_message` and the turn had run on past its own answer."""
    _drawn, seen, _ = drive_session(
        ["do the thing", "quit"],
        [said("respond", message="All finished."),
         said("write_file", path="after.txt", content="q")])
    assert len(seen) == 1, len(seen)


def test_the_old_done_still_ends_the_turn():
    """`done` was always terminal and was folded into the one ending. It is
    also the verb `agent_model` used to fabricate for a failed call, so a
    model or an old log replaying it must still finish rather than loop."""
    _drawn, seen, _ = drive_session(
        ["do the thing", "quit"],
        [said("done", message="All finished."),
         said("write_file", path="after.txt", content="q")])
    assert len(seen) == 1, len(seen)


def test_an_old_name_inside_a_batch_is_translated_entry_by_entry():
    """A batch is a list of actions and an old name can be in any of them, so
    the translation is applied per entry rather than to the object around
    them. Without that, the batch path would be the one route by which an
    untranslated verb reached the loop."""
    drawn, files = run_turn([
        batch(obj("announce", message="Writing both files now."),
              obj("write_file", path="one.txt", content="1"),
              obj("write_file", path="two.txt", content="2"),
              obj("done", message="Both written.")),
    ])
    assert "Writing both files now." in drawn, drawn
    assert files.get("one.txt") == "1" and files.get("two.txt") == "2", files
    assert "Both written." in drawn, drawn


def test_the_net_cannot_walk_round_a_gate():
    """The property that keeps the compatibility layer from being a bypass.
    Every old name lands on a NEW verb and then goes through exactly that
    verb's gates -- so a legacy `respond` is refused by an unfinished plan
    precisely as an `end_conversation` is, and for the same reason."""
    early, answer = "Everything is finished.", "Implemented it."
    drawn, seen, _ = drive_session(
        ["build the feature /plan", "quit"],
        [said("plan", operation="create", steps=["Implement it", "Run the tests"]),
         said("respond", message=early),
         said("plan", operation="update", step=1, status="completed"),
         said("plan", operation="update", step=2, status="completed"),
         said("done", message=answer)])

    assert len(seen) == 5, len(seen)
    refused = seen[2][-1]["content"]
    assert refused.startswith("BLOCKED"), refused
    screen = visible(drawn)
    assert early not in screen, screen[-3000:]
    assert answer in screen, screen[-3000:]


def test_the_net_is_not_taught_anywhere_a_model_can_read_it():
    """It works by being invisible. A prompt, a tool list or an error message
    that named an old verb would make it a second supported spelling, and the
    rename would have bought nothing. The prompts are covered above; this is
    the sentence a model is handed after a read, which used to name the old
    verb and is the kind of place a rename misses."""
    follow_up = agent_actions.build_result_message("read_file", "some text")
    assert END in follow_up, follow_up
    # The result text is fixed above and carries none of these words, so a
    # bare-word search here is exact rather than approximate.
    for name in LEGACY:
        assert name not in follow_up, (name, follow_up)


# --- background agents ------------------------------------------------------

def test_the_worker_spells_the_two_verbs_the_same_way_agent_actions_does():
    """`agent_worker` imports `agent_actions` inside its functions on purpose
    -- a frozen module list must not be able to stop a worker loading -- so it
    spells these two rather than importing them. Two spellings that drifted
    apart would leave a worker refusing a verb nobody sends and obeying one
    nobody may."""
    assert agent_worker.SEND_MESSAGE == agent_actions.SEND_MESSAGE
    assert agent_worker.END_CONVERSATION == agent_actions.END_CONVERSATION


def test_a_background_agent_may_speak_and_may_not_end_a_conversation():
    """The isolation, read off the lists themselves so a verb added by mistake
    is caught even if nobody writes a test that drives it."""
    assert SEND in agent_worker.NOTE_ACTIONS
    assert SEND in agent_worker.REVIEW_ACTIONS
    assert END in agent_worker.WORKER_FORBIDDEN
    assert END not in agent_worker.NOTE_ACTIONS
    assert END not in agent_worker.REVIEW_ACTIONS


def test_no_old_name_appears_on_any_of_the_worker_lists():
    """They are not on any list, and that is deliberate rather than an
    oversight: the translation happens before the lists are consulted, so a
    list naming an old verb would be a second, drifting copy of the same
    fact."""
    lists = (agent_worker.NOTE_ACTIONS, agent_worker.REVIEW_ACTIONS,
             agent_worker.WORKER_FORBIDDEN, agent_worker.WORKER_NEEDS_TERMINAL)
    for name in LEGACY:
        for names in lists:
            assert name not in names, (name, sorted(names))


def test_a_worker_that_sends_a_message_is_told_nobody_read_it():
    """Valid, allowed, and pointless: a background agent has no user. It costs
    a step and the model is told why, rather than being refused a verb it is
    on the whitelist for and left looking for another way to say the same
    thing."""
    result, ask, execute = run_background(
        agent_worker.run_worker,
        [said(SEND, message="I'll read the parser first."), FINISH])

    assert result == "task complete", result
    assert execute.calls == [], execute.calls        # nothing was dispatched
    told = ask.told()
    assert "Nothing you write reaches anybody" in told, told
    assert "costs a step" in told, told
    assert "not finished" in told, told


def test_a_worker_that_reaches_for_the_ending_is_refused():
    """It is how a turn ends for a user, and a worker has no user. Refused
    before dispatch, so it holds whatever the prompt said."""
    result, ask, execute = run_background(
        agent_worker.run_worker,
        [said(END, message="Here is your answer."), FINISH])

    assert result == "task complete", result
    assert execute.calls == [], execute.calls
    told = ask.told()
    assert "REFUSED: '%s'" % END in told, told
    assert "internal_response" in told, told


def test_a_worker_emitting_the_legacy_announce_is_translated_not_refused():
    """The reason the worker translates at all. `announce` is on no whitelist,
    so without the translation a reviewer or a note agent reaching for the old
    name would be REFUSED a verb it is actually allowed -- and it would be
    told, wrongly, that talking is something it may not do."""
    result, ask, execute = run_background(
        agent_worker.run_worker,
        [said("announce", message="Starting now."), FINISH])

    assert result == "task complete", result
    assert execute.calls == [], execute.calls
    told = ask.told()
    assert "REFUSED" not in told, told
    assert "Nothing you write reaches anybody" in told, told


def test_a_worker_emitting_the_legacy_respond_meets_the_ending_refusal():
    """The other direction, and the one that matters for the net not being a
    bypass: an old name that MEANT the ending is refused by the entry for the
    new one, rather than slipping past a list that never heard of it."""
    result, ask, _execute = run_background(
        agent_worker.run_worker,
        [said("respond", message="Here is your answer."), FINISH])

    assert result == "task complete", result
    told = ask.told()
    assert "REFUSED: '%s'" % END in told, told
    assert "REFUSED: 'respond'" not in told, told


def test_the_note_agent_may_send_a_message_and_may_not_end_a_conversation():
    """The note agent runs on a whitelist rather than a blacklist, so this is
    the check that the rename did not quietly drop the talking verb from it.
    Both halves in one test, because the whitelist is what decides both."""
    result, ask, execute = run_background(
        agent_worker.run_note,
        [said(SEND, message="Looking."), said(END, message="Here it is."),
         said("internal_response", response="Twenty-four modules.")],
        task="how many modules are there?", kind="note")

    assert result == "Twenty-four modules.", result
    assert execute.calls == [], execute.calls
    assert "Nothing you write reaches anybody" in ask.told(1), ask.told(1)
    assert "REFUSED: '%s'" % END in ask.told(2), ask.told(2)


def test_a_message_inside_a_workers_batch_is_answered_rather_than_dispatched():
    """The batch path has its own copy of the decision, so it needs its own
    test: a message there must not reach `execute_action`, and must not stop
    the entries after it from running."""
    result, _ask, execute = run_background(
        agent_worker.run_worker,
        [batch(obj(SEND, message="Starting."), obj("read_file", path="a.py")),
         FINISH])

    assert result == "task complete", result
    assert execute.calls == ["read_file"], execute.calls


# --- the hole the rename opened in its own safety net -----------------------

def test_a_bare_legacy_done_still_ends_the_turn_on_the_first_try():
    """`done` required NO keys and `end_conversation` requires `message`, so
    renaming alone left the commonest legacy ending shape half-translated: the
    verb was adopted and the reply was then REFUSED for a key it never had to
    carry. A net that only catches the replies that were already nearly right
    is not a net -- it turns a rename into a lost round for exactly the model
    the net exists for.

    `adopt_verb` supplies the word `execute_action` has always answered a bare
    `done` with, which is the same default moved one step earlier to where
    validation can see it, rather than TMT inventing an answer."""
    adopted = agent_actions.adopt_verb({"action": "done"})
    assert adopted["action"] == END, adopted
    assert adopted["message"] == agent_actions.LEGACY_EMPTY_MESSAGE, adopted
    assert agent_prompt.validate_action(adopted) is None, adopted
    assert agent_actions.execute_action(adopted, {}) == "done"


def test_a_legacy_ending_that_carried_its_own_words_keeps_them():
    """The default is a floor, never an overwrite. A model that said something
    must have said it: filling in a message it did wrote would be TMT putting
    words in its mouth, which is the one thing this whole net must not do."""
    for legacy in ({"action": "done", "message": "I finished."},
                   {"action": "respond", "message": "I finished."}):
        adopted = agent_actions.adopt_verb(dict(legacy))
        assert adopted["action"] == END, adopted
        assert adopted["message"] == "I finished.", adopted


def test_a_bare_legacy_respond_is_filled_in_too():
    """`respond` required a message, so a bare one was always invalid -- but it
    reaches the same ending and is filled in by the same rule rather than by a
    second one. One rule for one hole."""
    adopted = agent_actions.adopt_verb({"action": "respond"})
    assert adopted["action"] == END and adopted["message"], adopted


def test_the_default_is_not_applied_to_a_message_or_to_the_new_names():
    """It exists for one verb that took no keys. A bare `send_message` and a
    bare `end_conversation` are ordinary mistakes and must still be reported
    as missing a message, or the net would be quietly answering for the model
    on the path where the model simply forgot."""
    for bare in ({"action": "announce"}, {"action": SEND},
                 {"action": END}):
        adopted = agent_actions.adopt_verb(dict(bare))
        assert "message" not in adopted, adopted
        assert agent_prompt.validate_action(adopted) is not None, adopted


def test_a_bare_legacy_done_ends_a_real_turn_without_spending_a_retry():
    """The property that actually matters, through the real loop: one reply,
    one model call, the turn over. Before the fill-in this cost a retry and a
    second request to say the same thing."""
    drawn, seen, _console = drive_session(
        ["finish up", "quit"],
        [json.dumps({"action": "done"}),
         json.dumps(obj(END, message="unreached"))])
    assert len(seen) == 1, len(seen)
    assert "unreached" not in visible(drawn), "the turn ran on past the ending"
