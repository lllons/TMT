"""How the reviewbot's agenda is wired into the rest of TMT.

`agent_reviewbot` is pure state and is tested on its own. What is tested here
is everything AROUND it: the verb's registration, the two whitelists that
decide who may emit it, the loop that applies it, the register that owns the
object it writes to, and the review state that carries it for `/review`. Those
are five separate modules, and a feature that is correct in each of them and
wrong at one seam does not work at all.

Four properties are being protected, and the fourth is the one the feature
would quietly lose without a test.

The VERB IS THE REVIEWER'S ALONE. It is in `REVIEW_ACTIONS` and absent from
`NOTE_ACTIONS`; it is taught in the reviewer's prompt and in no other; and
`execute_action` -- the path the main agent and every plain worker dispatch
through -- answers it with a sentence saying where it belongs rather than
carrying it out. Three sides of the same isolation, and each of them is here.

THE READOUT IS APPLIED WHERE THE RECORD IS. `agent_worker._run_loop` handles
it directly, on BOTH its single-action and its batch path, because the manager
is deliberately withheld from a background agent's action context. The batch
path is separate code, and this repository's notes are emphatic that a branch
only ever rehearsed in the rare case is a branch nobody has read.

A CORRECTION IS NOT A FAILURE. An agenda operation costs a step and never a
retry: the agenda is a display, and a reviewer that got the shape wrong should
read the correction and carry on reviewing rather than spend its retry budget
proving it can operate a checklist. Both halves are asserted -- that the step
is spent, and that eight malformed operations in a row do not stop a run that
seven ordinary invalid actions would.

NOTHING ABOUT THE AGENDA DECIDES ANYTHING. It gates nothing, it is consulted
by no rule, and a review passes and fails exactly as it did before this
existed. A gate driven by the agenda would be a gate a reviewer could open by
shortening its list.

No test here makes a model call, a network call or a blocking wait. Every
reviewer is driven through the real `agent_worker` loop with `ask` injected,
and the runs that are meant to end without finishing end on the step budget
rather than on a clock.
"""

import json
import re

import agent_actions
import agent_capabilities
import agent_config
import agent_manager
import agent_plan
import agent_prompt
import agent_review
import agent_reviewbot
import agent_subprompts
import agent_worker


# --- building the pieces ----------------------------------------------------

def action(name, **keys):
    """One action object, as JSON, the way a model would send it."""
    keys["action"] = name
    return json.dumps(keys)


def agenda(**keys):
    """One `review_agenda` action, named through the module's own constant.

    Through `AGENDA_ACTION` rather than the literal, so a rename that the
    whitelists followed and the loop did not is a failure here rather than a
    silent pass against a verb nobody uses any more.
    """
    return action(agent_worker.AGENDA_ACTION, **keys)


def result_body(status="PASS", summary="Read the diff end to end."):
    return {"status": status, "summary": summary, "issues": []}


def finish(**kwargs):
    """The reviewer's terminal verb carrying a result `parse_result` accepts."""
    return action("internal_response", response=json.dumps(result_body(**kwargs)))


class Replies:
    """Scripted model replies, handed out in order.

    Falls back to a valid review result once the script runs out, so a test
    that miscounts the reviewer's steps fails on its assertion rather than
    running the loop to its step budget and failing somewhere unrelated.
    """

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def __call__(self, messages, on_event=None, model=None, max_tokens=None,
                 quiet=False, **extra):
        self.calls += 1
        if not self.replies:
            return finish()
        return self.replies.pop(0)


class Executor:
    """A stand-in dispatcher that records every action it is handed.

    What it is for here is the negative assertion: `review_agenda` must never
    arrive, because the dispatcher cannot reach the record the agenda hangs
    off and would answer it with the sentence saying so.
    """

    def __init__(self, result="ok"):
        self.actions = []
        self.result = result

    def __call__(self, obj, context):
        self.actions.append(obj.get("action"))
        return self.result


class Run:
    """One reviewer or note agent driven to completion with everything injected.

    The real `agent_worker` loop runs -- the whitelist, the refusals, the
    agenda branch and the manager transitions are all the production ones.
    Only `ask` and `execute` are injected, which are the seams that module
    exposes for exactly this, so nothing here needs a model or a key.
    """

    def __init__(self, *replies, **kwargs):
        self.manager = kwargs.pop("manager", None) or agent_manager.AgentManager()
        self.execute = kwargs.pop("execute", None) or Executor()
        self.ask = kwargs.pop("ask", None) or Replies(*replies)
        kind = kwargs.pop("kind", "review")
        self.record = self.manager.spawn(
            kwargs.pop("task", "review the diff"), kind=kind)
        runner = (agent_worker.run_note if kind == "note"
                  else agent_worker.run_review)
        self.out = runner(self.record, self.manager, ask=self.ask,
                          execute=self.execute, system_prompt="stub prompt")

    @property
    def agenda(self):
        return self.record.agenda

    def heard(self):
        """Everything the loop said back to the model, as one string."""
        return "\n".join(message["content"]
                         for message in self.record.conversation
                         if message.get("role") == "user")


class Listener:
    """Records the (event, record) pairs the manager announces."""

    def __init__(self):
        self.events = []

    def __call__(self, name, record):
        self.events.append((name, record))

    def names(self):
        return [name for name, _record in self.events]


def review_after_work(paths=("src/thing.py",)):
    """A ReviewState carrying the evidence a real turn would have left."""
    review = agent_review.ReviewState()
    for path in paths:
        review.note_change("write_file", (path,))
    return review


def worked_plan():
    plan = agent_plan.Plan()
    plan.create(["Implement it", "Test it", "Independent review"])
    return plan


def settled(review, status="PASS"):
    review.begin()
    return review.settle(agent_review.parse_result(
        json.dumps(result_body(status=status))))


def with_agenda(review, items=("Read the diff", "Check the tests")):
    review.agenda = agent_reviewbot.Agenda(list(items))
    return review.agenda


# --- the verb is registered -------------------------------------------------

def test_the_agenda_action_is_registered_with_exactly_the_key_it_carries():
    """An action that works perfectly and is not registered is an action that
    does not exist: `validate_action` refuses anything `REQUIRED_KEYS` has
    never heard of, so the loop would reject the reviewer's very first move."""
    assert "review_agenda" in agent_config.REQUIRED_KEYS
    assert agent_config.REQUIRED_KEYS["review_agenda"] == ["operation"]
    assert agent_prompt.validate_action(
        {"action": "review_agenda", "operation": "show"}) is None
    assert agent_prompt.validate_action(
        {"action": "review_agenda", "operation": "create",
         "items": ["Read the diff"]}) is None


def test_validate_action_names_the_missing_operation_rather_than_guessing_one():
    """The party reading the complaint is a model, and a bare rejection is one
    it retries unchanged. `operation` is the only required key, so a schema
    that quietly defaulted it would let `{"action":"review_agenda"}` through
    and leave the loop deciding what the reviewer meant."""
    problem = agent_prompt.validate_action({"action": "review_agenda"})
    assert problem is not None
    assert "operation" in problem, problem
    assert "review_agenda" in problem, problem


def test_the_agenda_writes_nothing_so_it_does_not_invalidate_the_prompt():
    """It updates a readout. Listing it as mutating would make every tick of
    the checklist rebuild the cached workspace snapshot -- roughly eight
    thousand tokens of walking -- to describe a tree that did not move."""
    assert "review_agenda" not in agent_config.MUTATING_ACTIONS


# --- the verb belongs to the reviewer and to nobody else --------------------

def test_the_agenda_is_the_reviewers_verb_and_never_the_note_agents():
    """The two whitelists were named separately precisely so a change to one
    is not silently a change to the other, and this is the one verb that tells
    them apart. A note agent with an agenda would be writing into a readout
    for a review that is not running."""
    assert agent_worker.AGENDA_ACTION == "review_agenda"
    assert agent_worker.AGENDA_ACTION in agent_worker.REVIEW_ACTIONS
    assert agent_worker.AGENDA_ACTION not in agent_worker.NOTE_ACTIONS
    # And it is not forbidden outright, or the reviewer's own whitelist could
    # never let it through: `_refusal` checks `forbidden` first.
    assert agent_worker.AGENDA_ACTION not in agent_worker.WORKER_FORBIDDEN
    assert agent_worker.AGENDA_ACTION not in agent_worker.WORKER_NEEDS_TERMINAL


def test_the_note_agent_is_refused_the_agenda_and_the_reviewer_is_not():
    """A whitelist checked before dispatch, not a blacklist, and the refusal
    has to carry the note agent's own reason: a model told the wrong reason
    reasonably looks for another route to the same effect."""
    refused = agent_worker._refusal(agent_worker.AGENDA_ACTION,
                                    agent_worker.NOTE_ACTIONS,
                                    agent_worker.WORKER_FORBIDDEN)
    assert refused.startswith("REFUSED:"), refused
    assert "review_agenda" in refused, refused
    assert "answering one question by reading" in refused, refused
    # The reviewer's whitelist lets exactly the same verb through.
    assert agent_worker._refusal(agent_worker.AGENDA_ACTION,
                                 agent_worker.REVIEW_ACTIONS,
                                 agent_worker.WORKER_FORBIDDEN,
                                 agent_worker._NOT_A_REVIEW_VERB) == ""
    # And a plain worker, which has no whitelist at all, is refused by the
    # dispatcher's sentence rather than by this -- see the dispatcher tests.
    assert agent_worker._refusal(agent_worker.AGENDA_ACTION, None,
                                 agent_worker.WORKER_FORBIDDEN) == ""


def test_the_reviewers_prompt_and_the_reviewers_loop_both_carry_the_agenda():
    """There is already a test that the two sets agree. This one pins the
    membership itself, so dropping the verb from BOTH -- which keeps the sets
    equal and silently removes the feature -- fails here."""
    assert set(agent_subprompts.REVIEW_VERBS) == set(agent_worker.REVIEW_ACTIONS)
    assert agent_worker.AGENDA_ACTION in agent_subprompts.REVIEW_VERBS
    assert agent_worker.AGENDA_ACTION in agent_worker.REVIEW_ACTIONS


# --- the dispatcher answers it and does not carry it out --------------------

def test_the_dispatcher_answers_the_agenda_with_where_it_belongs():
    """`execute_action` cannot reach the record the agenda hangs off -- a
    background agent's context withholds the manager on purpose -- so the one
    honest answer is a sentence. It must name the verb and say nothing
    changed, because a model that reads "Unknown action" goes looking for a
    spelling mistake in a verb it spelled correctly."""
    out = agent_actions.execute_action(
        {"action": "review_agenda", "operation": "show"},
        {"capabilities": agent_capabilities.Capabilities("/plan /review /verify"),
         "manager": agent_manager.AgentManager(),
         "review": agent_review.ReviewState()})
    assert "review_agenda" in out, out
    assert "belongs to the independent reviewer" in out, out
    assert "Nothing was changed" in out, out
    assert "Unknown action" not in out, out
    assert out == agent_actions._REVIEW_AGENDA_ELSEWHERE


def test_a_background_agents_context_gets_the_same_sentence():
    """A plain worker has no whitelist to be refused by, so the dispatcher is
    the only thing standing between it and somebody else's readout. It must
    answer with the sentence and never reach an agenda."""
    record = agent_manager.AgentRecord("1", 1, "worker", "do the work")
    out = agent_actions.execute_action(
        {"action": "review_agenda", "operation": "create",
         "items": ["Read the diff"]},
        agent_worker._context(record))
    assert out == agent_actions._REVIEW_AGENDA_ELSEWHERE, out
    assert record.agenda is None
    # And with no context at all, which is what a bare call looks like.
    assert agent_actions.execute_action(
        {"action": "review_agenda", "operation": "show"},
        None) == agent_actions._REVIEW_AGENDA_ELSEWHERE


def test_the_agenda_is_not_behind_the_capability_gate():
    """`plan`, `review` and `verify` are opt-in per prompt and are refused
    with a capability sentence when they are not. The agenda must not join
    them: it is not a capability the user grants, it is a readout inside a
    review they already authorised, and putting it behind that gate would
    replace the "belongs to the reviewer" sentence with one about `/review`
    that tells a reviewer nothing it can act on."""
    assert "review_agenda" not in agent_actions._CAPABILITY_ACTIONS
    unauthorised = agent_actions.execute_action(
        {"action": "review_agenda", "operation": "show"},
        {"capabilities": agent_capabilities.Capabilities("")})
    assert unauthorised == agent_actions._REVIEW_AGENDA_ELSEWHERE, unauthorised


# --- who is taught it -------------------------------------------------------

def test_the_main_agents_prompt_never_mentions_the_agenda():
    """Asked with every capability authorised, because the question is who is
    TAUGHT the verb and not whether this turn may use it. A main agent that
    read about the agenda would spend a round emitting one and get a sentence
    back saying it has no checklist."""
    main = agent_prompt.get_system_prompt(
        agent_capabilities.Capabilities("/plan /review /verify"))
    assert "review_agenda" not in main
    assert agent_subprompts.REVIEW_AGENDA_REFERENCE not in main


def test_only_the_reviewers_prompt_teaches_the_agenda():
    """The same two-sided isolation the review verb itself has: absent from
    the whitelists of the other agents AND absent from their prompts, so the
    separation does not rest on either half alone."""
    assert agent_subprompts.REVIEW_AGENDA_REFERENCE in agent_subprompts.review_prompt()
    assert "review_agenda" in agent_subprompts.review_prompt()
    for other in (agent_subprompts.worker_prompt(), agent_subprompts.note_prompt()):
        assert "review_agenda" not in other
        assert agent_subprompts.REVIEW_AGENDA_REFERENCE not in other


def test_the_agenda_examples_in_the_reviewers_prompt_are_real_actions():
    """An example that broke a rule would teach breaking it, and the reviewer
    is taught the shape by example three times over. Every one of them,
    including the ones labelled BAD -- those are bad judgement, never bad
    JSON, and a reviewer copying one must be corrected by the agenda's own
    rules rather than rejected by the schema."""
    decoder = json.JSONDecoder()
    examples = re.findall(r'(\{"action".*)$',
                          agent_subprompts.REVIEW_AGENDA_REFERENCE,
                          re.MULTILINE)
    assert len(examples) >= 7, examples
    for text in examples:
        obj, _end = decoder.raw_decode(text.strip())
        assert agent_prompt.validate_action(obj) is None, text
        assert obj["action"] == agent_worker.AGENDA_ACTION, text
        assert obj["operation"] in agent_reviewbot.OPERATIONS, text


# --- the manager owns the object --------------------------------------------

def test_a_fresh_record_of_every_kind_starts_with_no_agenda():
    """None and not an empty Agenda, because an empty one is a reviewer that
    declared nothing and the strip must be able to tell those apart: the
    runtime never supplies a list of its own."""
    manager = agent_manager.AgentManager()
    for kind in ("worker", "note", "review"):
        record = manager.spawn("a task", kind=kind)
        assert record.agenda is None, kind
    assert agent_manager.AgentRecord("9", 9, "review", "t").agenda is None


def test_an_agenda_for_an_agent_nobody_registered_is_a_sentence():
    """Everything comes back as a string, refusals included, because the
    caller is a step loop feeding a model. An exception here would escape the
    agenda branch of `_run_loop`, which does not catch, and end a review over
    a readout."""
    manager = agent_manager.AgentManager()
    out = manager.apply_agenda("no-such-agent", {"operation": "show"})
    assert isinstance(out, str)
    assert "not registered" in out, out


def test_the_first_operation_builds_the_agenda_on_the_record():
    """The record starts with none, so something has to make one, and it is
    the manager rather than the reviewer's first action being a special
    case."""
    manager = agent_manager.AgentManager()
    record = manager.spawn("review it", kind="review")
    out = manager.apply_agenda(record.id, {
        "operation": "create",
        "items": ["Read the diff", "Check the tests"]})
    assert isinstance(record.agenda, agent_reviewbot.Agenda)
    assert [item.title for item in record.agenda.items] == [
        "Read the diff", "Check the tests"]
    assert "Agenda set" in out, out
    assert "0 of 2 checked" in out, out


def test_the_record_keeps_one_agenda_object_across_operations():
    """One object with two references -- the record and the review state --
    is the whole design, and rebinding it on any operation would leave the
    strip drawing one list while `/review` printed another."""
    manager = agent_manager.AgentManager()
    record = manager.spawn("review it", kind="review")
    manager.apply_agenda(record.id, {"operation": "create",
                                     "items": ["Read the diff", "Read tests"]})
    first = record.agenda
    manager.apply_agenda(record.id, {"operation": "update", "item": 1,
                                     "status": "done"})
    manager.apply_agenda(record.id, {"operation": "add",
                                     "items": ["Check the callers"]})
    assert record.agenda is first
    assert [item.status for item in record.agenda.items][0] == agent_reviewbot.DONE
    assert len(record.agenda) == 3


def test_a_bad_operation_comes_back_as_a_failed_string():
    """An AgendaError raised out of here would be raised on the reviewer's own
    thread, inside a branch that does not catch. The caller is a model, and
    what it needs is the sentence that corrects it."""
    manager = agent_manager.AgentManager()
    record = manager.spawn("review it", kind="review")
    out = manager.apply_agenda(record.id, {"operation": "delete"})
    assert out.startswith("FAILED:"), out
    assert "not an agenda operation" in out, out
    assert "create, update, add, show" in out, out
    assert record.agenda is not None      # built, and left empty
    assert len(record.agenda) == 0


def test_an_operation_the_rules_refuse_comes_back_as_a_failed_string():
    """The rules live in `agent_reviewbot` and the manager holds none of its
    own. What is asserted here is that every one of them arrives as a string:
    re-declaring an agenda that has already reported, and naming an item on an
    agenda that does not exist yet."""
    manager = agent_manager.AgentManager()
    record = manager.spawn("review it", kind="review")
    early = manager.apply_agenda(record.id, {"operation": "update",
                                             "item": 1, "status": "done"})
    assert early.startswith("FAILED:"), early
    assert "no agenda yet" in early, early
    manager.apply_agenda(record.id, {"operation": "create",
                                     "items": ["Read the diff", "Read tests"]})
    manager.apply_agenda(record.id, {"operation": "update", "item": 1,
                                     "status": "done"})
    replaced = manager.apply_agenda(record.id, {"operation": "create",
                                                "items": ["Something else"]})
    assert replaced.startswith("FAILED:"), replaced
    assert "cannot be replaced" in replaced, replaced
    # And the reported item is still on the record, which is the whole reason
    # that operation is refused.
    assert [item.title for item in record.agenda.items][0] == "Read the diff"


def test_applying_an_agenda_announces_it_so_the_strip_repaints():
    """Without the emission the strip holds the frame it had when the review
    started and catches up only when something else forces a repaint. A review
    blocks the main loop for minutes, so "something else" is a long time away:
    the ticks would all arrive at once, after the review they describe."""
    manager = agent_manager.AgentManager()
    record = manager.spawn("review it", kind="review")
    listener = Listener()
    manager.subscribe(listener)
    try:
        manager.apply_agenda(record.id, {"operation": "create",
                                         "items": ["Read the diff"]})
        assert agent_manager.AGENT_ACTIVITY_CHANGED in listener.names(), \
            listener.names()
        assert listener.events[-1][1] is record
        before = len(listener.events)
        manager.apply_agenda(record.id, {"operation": "update", "item": 1,
                                         "status": "done"})
        assert len(listener.events) > before
    finally:
        manager.unsubscribe(listener)


def test_a_refused_operation_announces_nothing():
    """Nothing moved, so there is no new frame to draw, and repainting the
    live region for a frame identical to the one on screen is the cost the
    manager already refuses to pay for an unchanged activity label."""
    manager = agent_manager.AgentManager()
    record = manager.spawn("review it", kind="review")
    listener = Listener()
    manager.subscribe(listener)
    try:
        out = manager.apply_agenda(record.id, {"operation": "delete"})
        assert out.startswith("FAILED:"), out
        assert listener.events == []
    finally:
        manager.unsubscribe(listener)


def test_a_read_only_operation_announces_nothing_either():
    """`show` changes nothing, so there is no new frame to draw. Emitting for
    it repaints the live region for a frame identical to the one already on
    screen -- the exact cost `set_activity` declines to pay a few methods
    away when a label has not moved, and a review sits on this bus for
    minutes at a time.

    The list of read-only operations lives in `agent_reviewbot`, not here, so
    the knowledge of what an operation DOES stays in the module that knows."""
    manager = agent_manager.AgentManager()
    record = manager.spawn("review it", kind="review")
    manager.apply_agenda(record.id, {"operation": "create",
                                     "items": ["Read the diff"]})
    listener = Listener()
    manager.subscribe(listener)
    try:
        for operation in agent_reviewbot.READ_ONLY_OPERATIONS:
            out = manager.apply_agenda(record.id, {"operation": operation})
            assert not out.startswith("FAILED:"), out
        assert listener.events == [], listener.events
        # And an operation that DOES move something still announces it, so
        # this is a narrowing rather than the emission being lost.
        manager.apply_agenda(record.id, {"operation": "update", "item": 1,
                                         "status": "done"})
        assert listener.events, "a real change was never announced"
    finally:
        manager.unsubscribe(listener)


def test_the_agenda_verb_has_a_label_like_every_other_registered_action():
    """`agent_actions.action_event` draws a transcript row from ACTION_LABELS.
    A registered verb with no entry shows the reader `review_agenda` where
    every neighbouring row says `Review Agenda`. Only reachable on the path
    that returns the refusal sentence, and cosmetic, but every other
    registered action has one."""
    assert agent_actions.ACTION_LABELS.get("review_agenda") == "Review Agenda"


def test_a_listener_that_raises_does_not_break_the_agenda():
    """The observer of a worker must never be able to kill it. A renderer that
    threw while painting a tick would otherwise take down the reviewer's
    thread from the far side of the event bus."""
    manager = agent_manager.AgentManager()
    record = manager.spawn("review it", kind="review")
    seen = []

    def exploding(name, rec):
        seen.append(name)
        raise RuntimeError("the renderer fell over")

    manager.subscribe(exploding)
    try:
        out = manager.apply_agenda(record.id, {"operation": "create",
                                               "items": ["Read the diff"]})
        assert "Agenda set" in out, out
        assert seen, "the listener was never called"
        assert len(record.agenda) == 1
    finally:
        manager.unsubscribe(exploding)


# --- the worker loop applies it ---------------------------------------------

def test_a_reviewer_declares_an_agenda_and_ticks_it_off():
    """The whole feature, end to end through the production loop: the first
    action declares the checklist, a later one reports an item finished, and
    the record carries both by the time the review returns its result."""
    run = Run(agenda(operation="create",
                     items=["Read the diff", "Check the tests"]),
              agenda(operation="update", item=1, status="done"),
              finish(summary="Read it all."))
    assert run.agenda is not None
    assert [item.title for item in run.agenda.items] == [
        "Read the diff", "Check the tests"]
    assert [item.status for item in run.agenda.items] == [
        agent_reviewbot.DONE, agent_reviewbot.ACTIVE]
    assert run.agenda.counts() == (1, 2)
    # And the run finished normally, with a result the parser accepts.
    assert agent_review.parse_result(run.out).summary == "Read it all."
    # It ended on its own terminal verb rather than being stopped: `_stop` is
    # the only thing in this loop that records a failure.
    assert run.record.status != agent_manager.Status.FAILED
    assert run.ask.calls == 3


def test_an_agenda_operation_spends_a_step_of_the_reviewers_budget():
    """It is a whole request to a model. Pretending otherwise would let a
    reviewer spend its budget on the readout and run out before it reviewed
    anything, which is the failure this display exists to make visible."""
    run = Run(agenda(operation="create", items=["Read the diff", "Read tests"]),
              agenda(operation="update", item=1, status="done"),
              finish())
    # `set_steps` is reported at the top of each step, so the count on the
    # record is the number of steps that had already been spent when the
    # terminal reply arrived: one per agenda operation.
    assert run.record.steps == 2, run.record.steps
    assert run.ask.calls == 3


def test_a_reviewer_that_only_works_its_checklist_runs_out_of_steps():
    """The sharp end of the same rule. A reviewer that never reads anything
    must stop on its step budget rather than operating a checklist forever,
    and it must stop as a failure -- a sentence explaining why there is no
    result is not a result."""

    class OnlyAgendas:
        def __init__(self):
            self.calls = 0

        def __call__(self, messages, **kwargs):
            self.calls += 1
            # Distinct every time, or the loop's circuit breaker stops it at
            # three identical replies and the step budget is never reached.
            return agenda(operation="show", progress="looking again %d" % self.calls)

    ask = OnlyAgendas()
    run = Run(ask=ask)
    assert ask.calls == agent_worker.WORKER_ROUNDS, ask.calls
    assert "ran out of steps" in run.out, run.out
    assert run.record.status == agent_manager.Status.FAILED
    assert run.execute.actions == []


def test_a_malformed_agenda_does_not_spend_a_retry():
    """The agenda is a display. A reviewer that got the shape wrong should
    read the correction and carry on reviewing, not spend its retry budget
    proving it can operate a checklist -- so these go back as ordinary results
    rather than through `hand_back`.

    Eight of them, which is more than `MAX_INVALID_RETRIES`, and the control
    below shows that eight ordinary invalid actions do end the run."""
    bad = [agenda(operation=word) for word in
           ("delete", "remove", "reset", "drop", "erase", "wipe", "purge",
            "retire")]
    run = Run(*(bad + [finish(summary="Reviewed anyway.")]))
    assert agent_review.parse_result(run.out).summary == "Reviewed anyway."
    assert run.ask.calls == len(bad) + 1
    assert run.record.status != agent_manager.Status.FAILED
    # The control: the same number of ordinary invalid actions, each distinct
    # so the circuit breaker is not what stops it.
    invalid = [action("read_file", progress="attempt %d" % n) for n in range(8)]
    control = Run(*(invalid + [finish()]))
    assert "could not produce a valid action" in control.out, control.out
    assert control.record.status == agent_manager.Status.FAILED


def test_the_correction_reaches_the_model_as_an_ordinary_result():
    """A refusal the reviewer never sees is a refusal it repeats. What goes
    back has to carry both halves: what was wrong, and that the readout
    reviewed nothing, so the next action is about the code again."""
    run = Run(agenda(operation="delete"), finish())
    heard = run.heard()
    assert "FAILED:" in heard, heard
    assert "not an agenda operation" in heard, heard
    assert "updated the readout only" in heard, heard
    assert "reviewed nothing" in heard, heard


def test_a_batched_agenda_is_applied_too():
    """`_run_batch` is separate code from the single-action path, and a branch
    only ever rehearsed in the rare case is a branch nobody has read. Without
    this the reviewer that put its agenda in a batch would be told the verb
    belongs somewhere else while looking at the one place it does belong."""
    run = Run(json.dumps({"actions": [
        {"action": "review_agenda", "operation": "create",
         "items": ["Read the diff", "Check the tests"]},
        {"action": "read_file", "path": "src/thing.py"}]}),
        agenda(operation="update", item=1, status="done"),
        finish())
    assert run.agenda is not None
    assert [item.title for item in run.agenda.items] == [
        "Read the diff", "Check the tests"]
    assert run.agenda.counts() == (1, 2)
    heard = run.heard()
    assert "review_agenda:" in heard, heard
    assert "Agenda set" in heard, heard
    # The other entry in the batch went to the dispatcher; the agenda did not.
    assert run.execute.actions == ["read_file"], run.execute.actions


def test_a_batched_agenda_that_is_refused_still_comes_back_as_a_line():
    """The batch path must answer a bad operation the same way the single one
    does -- as text in the results the model reads -- rather than raising out
    of the loop or dropping the entry silently."""
    run = Run(json.dumps({"actions": [
        {"action": "review_agenda", "operation": "delete"},
        {"action": "git_diff"}]}),
        finish())
    heard = run.heard()
    assert "review_agenda: FAILED:" in heard, heard
    assert "not an agenda operation" in heard, heard
    assert run.execute.actions == ["git_diff"], run.execute.actions
    # The batch was not thrown out: a refused agenda entry is a line in the
    # results, never an "invalid batch" that costs the whole reply.
    assert "INVALID:" not in run.heard(), run.heard()
    assert run.record.status != agent_manager.Status.FAILED


def test_the_note_agent_is_refused_the_agenda_and_the_refusal_reaches_it():
    """Driven through the real `run_note` rather than through `_refusal`,
    because the question is not whether the sentence exists but whether the
    loop asks for it before it reaches the agenda branch. A note agent that
    got past it would write into a readout no review owns."""
    run = Run(agenda(operation="create", items=["Read the diff"]),
              action("internal_response", response="the prompt is assembled in "
                     "agent_prompt"),
              kind="note")
    heard = run.heard()
    assert "REFUSED:" in heard, heard
    assert "review_agenda" in heard, heard
    assert "answering one question by reading" in heard, heard
    assert run.record.agenda is None
    assert run.out == "the prompt is assembled in agent_prompt"


def test_the_agenda_never_reaches_the_dispatcher():
    """It is applied in the loop, where the record and the manager are, and
    the dispatcher would answer it with the sentence saying so -- which the
    reviewer would then read as a refusal of the one verb it was told to open
    with."""
    run = Run(agenda(operation="create", items=["Read the diff"]),
              action("read_file", path="a.py"),
              agenda(operation="update", item=1, status="done"),
              json.dumps({"actions": [
                  {"action": "review_agenda", "operation": "show"},
                  {"action": "git_status"}]}),
              finish())
    assert "review_agenda" not in run.execute.actions, run.execute.actions
    assert run.execute.actions == ["read_file", "git_status"], run.execute.actions
    assert run.agenda is not None and len(run.agenda) == 1


def test_a_reviewer_that_declares_nothing_still_finishes():
    """There is no default agenda and no fallback list. A reviewer that never
    declares one gets no agenda rows at all, and the review itself is
    unchanged -- the readout is an addition to a review, never a requirement
    of one."""
    run = Run(action("read_file", path="a.py"), finish(summary="No checklist."))
    assert run.record.agenda is None
    assert agent_review.parse_result(run.out).summary == "No checklist."
    assert run.record.status != agent_manager.Status.FAILED


# --- agent_actions attaches it ----------------------------------------------

def test_attaching_an_agenda_puts_one_object_on_the_record_and_the_review():
    """Two references to one object rather than two objects. The record is
    what the reviewer's own thread writes through and the review state is what
    outlives it, so a copy would leave the strip drawing one and `/review`
    printing the other."""
    record = agent_manager.AgentRecord("1", 1, "review", "brief")
    review = agent_review.ReviewState()
    attached = agent_actions._attach_agenda(record, review)
    assert isinstance(attached, agent_reviewbot.Agenda)
    assert record.agenda is attached
    assert review.agenda is attached
    assert len(attached) == 0
    # And writing through one reference is visible through the other.
    record.agenda.create(["Read the diff"])
    assert len(review.agenda) == 1


def test_every_review_gets_its_own_agenda():
    """A new one per review, not one per task. A second cycle is a second
    reviewer with its own list, and carrying the first one's ticked items over
    would draw a review as most of the way through work it has not started."""
    review = agent_review.ReviewState()
    first_record = agent_manager.AgentRecord("1", 1, "review", "brief")
    second_record = agent_manager.AgentRecord("2", 2, "review", "brief")
    first = agent_actions._attach_agenda(first_record, review)
    first.create(["Read the diff"])
    second = agent_actions._attach_agenda(second_record, review)
    assert second is not first
    assert len(second) == 0
    assert review.agenda is second
    assert first_record.agenda is first        # the finished one is untouched


# --- the review state carries it, and is not steered by it ------------------

def test_a_fresh_review_state_has_no_agenda_and_retire_drops_it():
    """`retire` is called from `Session.begin_turn` and `Session.clear`,
    neither of which catches anything. A leftover agenda would draw a finished
    reviewer's checklist beside an unrelated question."""
    review = agent_review.ReviewState()
    assert review.agenda is None
    with_agenda(review)
    assert review.agenda is not None
    review.retire()
    assert review.agenda is None
    # And again on an already empty one, which is what a conversational turn
    # does on every question.
    review.retire()
    assert review.agenda is None


def test_the_review_report_carries_the_agenda_when_there_is_one():
    """`/review` is the permanent surface for this. The column can only ever
    show a window onto the list; the report is where the whole of it is
    readable, including the items that scrolled past."""
    review = review_after_work()
    review.begin()
    with_agenda(review, ["Read the diff", "Check the tests"]).update(1, "done")
    text = review.describe()
    assert "What the reviewer set out to check:" in text, text
    assert "A1: Read the diff [done]" in text, text
    assert "A2: Check the tests [active]" in text, text
    assert "1 of 2 checked." in text, text


def test_the_review_report_omits_the_agenda_when_there_is_none_or_it_is_empty():
    """A heading over nothing is a heading the reader has to rule out. An
    empty agenda is a reviewer that declared nothing, which is exactly the
    case the runtime must not paper over with a list of its own."""
    absent = review_after_work()
    absent.begin()
    assert absent.agenda is None
    assert "What the reviewer set out to check:" not in absent.describe()
    empty = review_after_work()
    empty.begin()
    empty.agenda = agent_reviewbot.Agenda()
    assert len(empty.agenda) == 0
    assert "What the reviewer set out to check:" not in empty.describe()


def test_a_readout_that_raises_never_takes_down_the_report():
    """The same bargain the column strikes, applied to the permanent surface:
    an agenda that cannot be described costs `/review` those rows and never
    the findings under them. Both ways in are covered, because the report asks
    the object its length before it asks it to describe itself."""

    class RaisesOnLength:
        def __len__(self):
            raise RuntimeError("boom")

        def describe(self):
            return "never reached"

    class RaisesOnDescribe:
        def __len__(self):
            return 3

        def describe(self):
            raise RuntimeError("boom")

    for broken in (RaisesOnLength(), RaisesOnDescribe(), object()):
        review = review_after_work()
        settled(review, status="FAIL")
        review.agenda = broken
        text = review.describe()
        assert "REVIEW" in text, text
        assert "Review 1 of at most" in text, text
        assert "What the reviewer set out to check:" not in text, text


def test_the_agenda_decides_nothing_about_whether_a_review_passes():
    """A gate driven by the agenda would be a gate a reviewer could open by
    shortening its list, and one that could be jammed shut by a reviewer that
    forgot to tick the last item. The verdict comes from `parse_result` and
    from nothing else, so an empty agenda and a half-finished one must give
    the identical answer."""
    plan = worked_plan()
    empty = review_after_work()
    empty.agenda = agent_reviewbot.Agenda()
    half = review_after_work()
    with_agenda(half, ["Read the diff", "Check the tests", "Read the callers"])
    half.agenda.update(1, "done")
    for review in (empty, half):
        settled(review, status="PASS")
    assert empty.passed is half.passed is True
    assert agent_review.refusal(empty, plan, "end_conversation") == ""
    assert agent_review.refusal(half, plan, "end_conversation") == ""
    assert not half.agenda.is_complete()
    # And a failing review is refused whatever its checklist says, including a
    # checklist that is complete.
    failed = review_after_work()
    done = with_agenda(failed, ["Read the diff"])
    settled(failed, status="FAIL")
    done.update(1, "done")
    assert done.is_complete()
    assert not failed.passed
    assert agent_review.refusal(failed, plan, "end_conversation") != ""
