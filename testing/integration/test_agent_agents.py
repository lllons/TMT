"""The background agents: the register that tracks them and the loop they run.

Everything here runs without a model, without a network and without a real
terminal, which is the whole reason `agent_manager` and `agent_worker` were
built with the clock, the model call and the dispatcher all injectable. Two
consequences worth stating, because they are what makes the file trustworthy:

**No test sleeps.** The retention window is driven by advancing an injected
clock, the way `test_agent_live_renderer.drain()` drives the glitch stream by
handing it the moment to reason from. A test that spent five real seconds
proving a five-second rule would be proving something about `time.sleep`.

**Every wait has a short real timeout.** There is no per-test timeout in this
suite, so a test that blocked on an event nobody sets would hang the whole
run rather than fail.

The kill-safety tests are the ones that earn their keep. Each of the three
cancellation boundaries is tested by setting the flag at the one moment only
that boundary can catch, so removing any one of them fails a specific test
rather than being covered by the other two.
"""

import io
import json
import sys
import threading

import agent_manager
import agent_worker
from agent_manager import (
    AGENT_ACTIVITY_CHANGED, AGENT_COMPLETED, AGENT_CREATED, AGENT_KILLED,
    AGENT_REMOVED_FROM_UI, AGENT_RESULT_AVAILABLE, AGENT_STARTED,
    RETENTION_SECONDS, AgentManager, CapacityError, Status, WorkerCancelled,
    clip_activity,
)

# Every blocking wait in this file gets one of these. Long enough that a slow
# machine does not fail a passing test, short enough that a genuinely broken
# one fails the run instead of hanging it.
WAIT = 5.0


class Clock:
    """A clock the test moves by hand. Never waited on, only advanced."""

    def __init__(self, now=0.0):
        self.now = float(now)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)
        return self.now


def action(name, **keys):
    """One action object, as JSON, the way a model would send it."""
    keys["action"] = name
    return json.dumps(keys)


FINISH = action("internal_response", response="task complete")


class Replies:
    """Scripted model replies, handed out in order.

    The messages are copied at the moment of the call rather than kept by
    reference, because the loop appends to that list as it goes and a test
    asking what the second request contained would otherwise be shown the
    fourth.
    """

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, messages, on_event=None, model=None, max_tokens=None,
                 quiet=False, **extra):
        self.calls.append({"messages": [dict(m) for m in messages],
                           "model": model, "max_tokens": max_tokens,
                           "quiet": quiet})
        if not self.replies:
            return action("internal_response", response="ran out of script")
        return self.replies.pop(0)


class Executor:
    """A stand-in dispatcher that records what it was asked to run."""

    def __init__(self, result="ok"):
        self.calls = []
        self.result = result

    def __call__(self, obj, context):
        self.calls.append((obj.get("action"), dict(obj), dict(context or {})))
        return self.result

    @property
    def actions(self):
        return [name for name, _obj, _ctx in self.calls]


def run(record, manager, ask, execute=None, prompt="worker prompt", note=False):
    """Run a worker or the note agent with everything injected."""
    runner = agent_worker.run_note if note else agent_worker.run_worker
    return runner(record, manager, ask=ask, execute=execute or Executor(),
                  system_prompt=prompt)


# --- the manager: creating, counting, refusing ---------------------------


def test_a_spawned_worker_carries_the_task_it_was_given_verbatim():
    """The main AI writes the task; a worker that received a paraphrase would
    be doing a different job from the one that was delegated."""
    manager = AgentManager(clock=Clock())
    task = "Rewrite agent_ui.render_response so it wraps at the panel width."
    record = manager.spawn(task, model="some/model", effort="high")
    assert record.task == task, record.task
    assert record.id == "1" and record.number == 1, record
    assert record.kind == "worker" and record.status == Status.CREATED, record
    assert record.model == "some/model" and record.effort == "high", record
    assert manager.inspect("1") is record
    assert manager.list() == (record,)


def test_the_sixth_worker_is_refused_with_a_sentence_and_not_with_silence():
    """A model handed a bare failure retries it forever. The refusal has to
    say what to do instead, because the party reading it is a model."""
    manager = AgentManager(clock=Clock())
    for index in range(agent_manager.MAX_WORKERS):
        manager.spawn("task %d" % index)
    assert manager.active_count() == agent_manager.MAX_WORKERS
    try:
        manager.spawn("one too many")
    except CapacityError as error:
        said = str(error)
    else:
        raise AssertionError("a sixth worker was accepted")
    assert "maximum" in said, said
    assert "wait_for_agents" in said and "kill_agent" in said, said
    # And the refusal did not half-register it.
    assert len(manager.list()) == agent_manager.MAX_WORKERS


def test_finishing_a_worker_makes_room_for_another():
    manager = AgentManager(clock=Clock())
    records = [manager.spawn("task %d" % i) for i in range(agent_manager.MAX_WORKERS)]
    manager.complete(records[0].id, "done")
    assert manager.active_count() == agent_manager.MAX_WORKERS - 1
    sixth = manager.spawn("now there is room")
    assert sixth.id == "6", sixth.id


def test_the_note_agent_never_counts_against_the_worker_cap():
    """It is one read-only question the user asked directly. Refusing it
    because five unrelated workers are busy would refuse the wrong thing."""
    manager = AgentManager(clock=Clock())
    for index in range(agent_manager.MAX_WORKERS):
        manager.spawn("task %d" % index)
    note = manager.spawn("where is the prompt assembled?", kind="note")
    assert note.kind == "note"
    assert manager.note() is note
    assert manager.active_count() == agent_manager.MAX_WORKERS
    assert note not in manager.list(), "a note agent appeared in the worker list"


def test_ids_are_unique_across_workers_and_notes():
    """`kill("2")` addresses a record by that string. Two records sharing an
    id would kill the wrong agent, which is worse than a gap in numbering."""
    manager = AgentManager(clock=Clock())
    first = manager.spawn("one")
    note = manager.spawn("a question", kind="note")
    second = manager.spawn("two")
    ids = [first.id, note.id, second.id]
    assert len(set(ids)) == 3, ids
    assert manager.inspect(note.id) is note
    assert manager.inspect(second.id) is second


# --- the manager: transitions and the event bus --------------------------


class Watcher:
    def __init__(self, manager=None):
        self.seen = []
        if manager is not None:
            manager.subscribe(self)

    def __call__(self, name, record):
        self.seen.append((name, record.id))

    def names(self):
        return [name for name, _id in self.seen]


def test_every_state_transition_is_announced_in_order():
    manager = AgentManager(clock=Clock())
    watcher = Watcher(manager)
    record = manager.spawn("do the thing")
    manager.set_activity(record.id, "Reading agent_ui.py")
    manager.add_tokens(record.id, tokens_out=120, output_exact=True)
    manager.complete(record.id, "the thing is done")
    assert watcher.names() == [AGENT_CREATED, AGENT_ACTIVITY_CHANGED,
                               agent_manager.AGENT_TOKEN_UPDATE,
                               AGENT_COMPLETED, AGENT_RESULT_AVAILABLE], watcher.names()
    assert record.status == Status.COMPLETED
    assert record.finished_at is not None
    assert manager.result(record.id) == "the thing is done"
    assert manager.active_count() == 0


def test_a_failed_worker_reports_its_reason_where_a_result_would_be():
    """A failure returning "" tells the main AI nothing about a worker it is
    waiting on. The recorded error stands in for the missing result."""
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    manager.fail(record.id, "the provider refused the request")
    assert record.status == Status.FAILED
    assert manager.result(record.id) == "the provider refused the request"
    assert record.result == "", "an error was written into the result field"


def test_a_terminal_agent_cannot_be_moved_again():
    """A worker killed halfway through its last action must not be resurrected
    as COMPLETED by whatever its runner happened to return afterwards."""
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    assert manager.kill(record.id) is True
    assert manager.complete(record.id, "but I finished!") is None
    assert manager.fail(record.id, "or did I fail?") is None
    assert manager.kill(record.id) is False
    assert record.status == Status.KILLED
    assert record.result == "" and record.error == ""


def test_a_listener_that_raises_cannot_kill_the_agent_it_watches():
    def broken(name, record):
        raise RuntimeError("this listener is broken")

    manager = AgentManager(clock=Clock())
    manager.subscribe(broken)
    watcher = Watcher(manager)
    record = manager.spawn("do the thing")
    manager.complete(record.id, "done anyway")
    assert record.status == Status.COMPLETED
    assert AGENT_COMPLETED in watcher.names(), watcher.names()


def test_an_unsubscribed_listener_stops_hearing():
    manager = AgentManager(clock=Clock())
    watcher = Watcher(manager)
    manager.unsubscribe(watcher)
    manager.spawn("do the thing")
    assert watcher.seen == []


# --- the manager: waiting ------------------------------------------------


def test_wait_returns_the_finished_worker_and_omits_the_running_one():
    """A worker still running is absent rather than present with an empty
    result: the caller reports it as still running instead of being handed
    something that claims to be an answer."""
    manager = AgentManager(clock=Clock())
    done = manager.spawn("finished work")
    running = manager.spawn("unfinished work")
    manager.complete(done.id, "here it is")
    got = manager.wait([done.id, running.id], timeout=0.05)
    assert list(got) == [done.id], got
    assert got[done.id] is done


def test_wait_wakes_as_soon_as_the_last_worker_finishes():
    """The main AI waking on completion. It blocks on the records' own events,
    so this costs nothing while it waits and returns the moment they land."""
    manager = AgentManager(clock=Clock())
    records = [manager.spawn("task %d" % i) for i in range(3)]
    ready = threading.Event()

    def finisher():
        ready.wait(WAIT)
        for index, record in enumerate(records):
            manager.complete(record.id, "result %d" % index)

    thread = threading.Thread(target=finisher, daemon=True)
    thread.start()
    ready.set()
    got = manager.wait_all(timeout=WAIT)
    thread.join(WAIT)
    assert len(got) == 3, got
    assert [manager.result(r.id) for r in records] == ["result 0", "result 1", "result 2"]


def test_waiting_on_an_id_that_does_not_exist_returns_nothing_and_does_not_hang():
    manager = AgentManager(clock=Clock())
    assert manager.wait(["nope"], timeout=0.05) == {}
    assert manager.wait("nope", timeout=0.05) == {}


# --- the manager: retention (an injected clock, never a sleep) -----------


def test_a_finished_card_is_visible_for_exactly_the_retention_window():
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = manager.spawn("do the thing")
    assert manager.visible_agents() == (record,)
    manager.complete(record.id, "done")
    # Still on screen the instant it finished, and for every moment inside
    # the window. The window is measured from when the work ended, not from
    # when the panel last repainted.
    assert manager.visible_agents() == (record,)
    clock.advance(RETENTION_SECONDS - 0.01)
    assert manager.visible_agents() == (record,)
    clock.advance(0.01)
    assert manager.visible_agents() == (), "the card outlived its window"


def test_the_card_survives_the_panel_being_closed_and_reopened_inside_the_window():
    """Nothing about drawing resets it: it is a stamp on the record, not a
    timer owned by whatever happened to be on screen."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = manager.spawn("do the thing")
    manager.complete(record.id, "done")
    clock.advance(2.0)
    # The panel is closed for a moment and reopened -- no calls at all in
    # between -- and then repainted several times.
    for _ in range(5):
        assert manager.visible_agents() == (record,)
    clock.advance(RETENTION_SECONDS)
    assert manager.visible_agents() == ()


def test_the_result_outlives_the_card():
    """Ageing out is a fact about the card. The main AI may ask for the answer
    minutes later, and it has to still be there."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = manager.spawn("do the thing")
    manager.complete(record.id, "the answer the main AI is waiting for")
    clock.advance(RETENTION_SECONDS * 10)
    assert manager.visible_agents() == ()
    assert manager.list() == (record,), "the record was deleted with its card"
    assert manager.result(record.id) == "the answer the main AI is waiting for"
    assert manager.inspect(record.id) is record


def test_removal_from_the_ui_is_announced_once_and_the_counter_follows():
    """`visible_agents` runs on every repaint. Announcing removal each time
    would fire the event many times a second for the rest of the session."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    watcher = Watcher(manager)
    first = manager.spawn("one")
    second = manager.spawn("two")
    manager.complete(first.id, "done")
    clock.advance(RETENTION_SECONDS + 0.1)
    for _ in range(4):
        visible = manager.visible_agents()
    assert visible == (second,), visible
    removals = [name for name in watcher.names() if name == AGENT_REMOVED_FROM_UI]
    assert removals == [AGENT_REMOVED_FROM_UI], removals


def test_a_running_worker_is_never_aged_out_however_long_it_runs():
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = manager.spawn("a long job")
    clock.advance(RETENTION_SECONDS * 100)
    assert manager.visible_agents() == (record,)


def test_elapsed_stops_when_the_work_does():
    """A finished card that went on counting through its retention window
    would be reporting time the work did not take."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = manager.spawn("do the thing")
    record.started_at = clock.now
    clock.advance(3.0)
    assert abs(record.elapsed(clock.now) - 3.0) < 1e-6
    manager.complete(record.id, "done")
    clock.advance(100.0)
    assert abs(record.elapsed(clock.now) - 3.0) < 1e-6, record.elapsed(clock.now)


# --- the manager: conflicts ----------------------------------------------


def test_conflicts_names_every_worker_that_wrote_the_same_file():
    """The whole of the concurrent-write story: enough to tell the main AI
    where to look, without inventing a transaction system."""
    manager = AgentManager(clock=Clock())
    first = manager.spawn("one")
    second = manager.spawn("two")
    third = manager.spawn("three")
    manager.note_paths(first.id, ("agent_ui.py", "agent_menu.py"))
    manager.note_paths(second.id, ("agent_ui.py",))
    manager.note_paths(third.id, ("agent_tree.py",))
    assert manager.conflicts() == (("agent_ui.py", (first.id, second.id)),)
    manager.note_paths(third.id, ("agent_menu.py", "agent_menu.py"))
    assert manager.conflicts() == (
        ("agent_menu.py", (first.id, third.id)),
        ("agent_ui.py", (first.id, second.id)),
    ), manager.conflicts()
    assert third.paths == ("agent_tree.py", "agent_menu.py"), third.paths


# --- killing --------------------------------------------------------------


def test_killing_one_worker_leaves_every_other_agent_alone():
    manager = AgentManager(clock=Clock())
    first = manager.spawn("one")
    second = manager.spawn("two")
    note = manager.spawn("a question", kind="note")
    assert manager.kill(first.id) is True
    assert first.status == Status.KILLED and first.cancel.is_set()
    assert second.status == Status.CREATED and not second.cancel.is_set()
    assert note.status == Status.CREATED and not note.cancel.is_set()
    assert manager.active_count() == 1


def test_kill_all_stops_the_workers_and_leaves_the_note_agent_running():
    manager = AgentManager(clock=Clock())
    records = [manager.spawn("task %d" % i) for i in range(3)]
    note = manager.spawn("a question", kind="note")
    manager.complete(records[0].id, "already done")
    killed = manager.kill_all()
    assert killed == 2, killed
    assert [r.status for r in records] == [Status.COMPLETED, Status.KILLED, Status.KILLED]
    assert note.status == Status.CREATED and not note.cancel.is_set()
    assert manager.kill_note() is True
    assert note.status == Status.KILLED


def test_no_tool_call_runs_when_the_flag_was_already_set():
    """Boundary one, at the top of the step. The model is not even asked."""
    manager = AgentManager(clock=Clock())
    record = manager.spawn("write some files")
    manager.kill(record.id)
    ask = Replies([action("write_file", path="a.txt", content="a"), FINISH])
    execute = Executor()
    try:
        run(record, manager, ask, execute)
    except WorkerCancelled:
        pass
    else:
        raise AssertionError("a cancelled worker ran to completion")
    assert ask.calls == [], "a killed worker still called the model"
    assert execute.actions == [], execute.actions


def test_the_stream_stops_being_read_once_the_flag_is_set():
    """Boundary two, inside the stream handler between chunks. The flag is set
    partway through the response, which no other check can see."""
    manager = AgentManager(clock=Clock())
    record = manager.spawn("write some files")
    chunks = []
    execute = Executor()

    def ask(messages, on_event=None, model=None, max_tokens=None, quiet=False):
        for index in range(20):
            chunks.append(index)
            if index == 2:
                # A kill arriving from another thread while the reply streams.
                manager.kill(record.id)
            on_event(("output", 40))
        return action("write_file", path="a.txt", content="a")

    try:
        run(record, manager, ask, execute)
    except WorkerCancelled:
        pass
    else:
        raise AssertionError("the worker read a stream it had been told to stop")
    assert chunks == [0, 1, 2], chunks
    assert execute.actions == [], execute.actions


def test_no_tool_call_runs_when_the_flag_lands_at_the_last_possible_instant():
    """Boundary three, and the guarantee the design actually rests on.

    The kill arrives after the step began, after the model answered and after
    the action was validated -- the one gap none of the earlier checks can
    cover. The check on the line above `execute(...)` is what catches it, and
    this test is what notices if that line is ever removed.
    """
    manager = AgentManager(clock=Clock())
    record = manager.spawn("write two files")
    execute = Executor()

    def kill_on_activity(name, killed_record):
        if name == AGENT_ACTIVITY_CHANGED:
            manager.kill(killed_record.id)

    manager.subscribe(kill_on_activity)
    ask = Replies([action("write_file", path="a.txt", content="a"),
                   action("write_file", path="b.txt", content="b"), FINISH])
    try:
        run(record, manager, ask, execute)
    except WorkerCancelled:
        pass
    else:
        raise AssertionError("a cancelled worker ran to completion")
    assert execute.actions == [], (
        "a tool call ran after the flag was set: %r" % (execute.actions,))
    assert len(ask.calls) == 1, ask.calls


def test_a_killed_worker_is_recorded_killed_and_not_failed():
    """Through `start`, on a real thread, which is where the two could be
    confused: WorkerCancelled leaving a runner is not a failure."""
    manager = AgentManager(clock=Clock())
    record = manager.spawn("write some files")
    entered = threading.Event()
    execute = Executor()

    def ask(messages, on_event=None, model=None, max_tokens=None, quiet=False):
        entered.set()
        # Block until the test kills it, then answer as if nothing happened.
        record.cancel.wait(WAIT)
        return action("write_file", path="a.txt", content="a")

    thread = manager.start(record, lambda rec, mgr: run(rec, mgr, ask, execute))
    assert entered.wait(WAIT), "the worker never started"
    manager.kill(record.id)
    thread.join(WAIT)
    assert not thread.is_alive(), "the worker thread did not unwind"
    assert record.status == Status.KILLED, record.status
    assert record.error == "", record.error
    assert execute.actions == [], execute.actions


def test_worker_threads_are_daemons_so_an_abandoned_one_cannot_hold_the_process():
    manager = AgentManager(clock=Clock())
    record = manager.spawn("a job")
    thread = manager.start(record, lambda rec, mgr: run(
        rec, mgr, Replies([FINISH])))
    assert thread.daemon is True
    assert thread.name == "tmt-agent-%s" % record.id, thread.name
    assert record.done.wait(WAIT), "the worker never finished"
    thread.join(WAIT)
    assert record.status == Status.COMPLETED, record.status


# --- activity labels ------------------------------------------------------


def test_a_label_exists_from_the_moment_an_agent_is_spawned():
    """A card is drawn as soon as the agent exists. A blank one would be a
    card that says the program is doing nothing while it works."""
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    assert record.activity.strip(), repr(record.activity)


def test_an_activity_label_is_never_more_than_five_words():
    assert clip_activity("") == ""
    assert clip_activity("one two three") == "one two three"
    assert clip_activity("a b c d e f g h") == "a b c d e"
    # Collapsed as well as clipped: a newline inside a live region is not a
    # long label, it is a broken frame.
    assert clip_activity("Read\nLines  agent_ui.py\t") == "Read Lines agent_ui.py"
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    manager.set_activity(record.id, "one two three four five six seven")
    assert len(record.activity.split()) <= agent_manager.MAX_ACTIVITY_WORDS
    assert "\n" not in record.activity


def test_the_label_follows_the_work_and_ends_on_completed():
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    labels = []
    manager.subscribe(lambda name, rec: labels.append(rec.activity)
                      if name == AGENT_ACTIVITY_CHANGED else None)
    ask = Replies([
        action("read_lines", path="src/agent_ui.py", start=1, end=40, content=""),
        action("find_text", query="render_response"),
        FINISH,
    ])
    execute = Executor()
    assert run(record, manager, ask, execute) == "task complete"
    manager.complete(record.id, "task complete")
    assert labels[:1] != [], labels
    assert any("agent_ui.py" in label for label in labels), labels
    assert any("render_response" in label for label in labels), labels
    for label in labels:
        assert len(label.split()) <= agent_manager.MAX_ACTIVITY_WORDS, label
    assert record.activity == "Completed", record.activity


def test_a_killed_agent_says_so_on_its_card():
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    manager.kill(record.id)
    assert record.activity == "Killed", record.activity
    failed = manager.spawn("another thing")
    manager.fail(failed.id, "it broke")
    assert failed.activity == "Failed", failed.activity


def test_an_unchanged_label_is_not_announced_again():
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    watcher = Watcher(manager)
    manager.set_activity(record.id, "Reading agent_ui.py")
    manager.set_activity(record.id, "Reading agent_ui.py")
    assert watcher.names().count(AGENT_ACTIVITY_CHANGED) == 1, watcher.names()


# --- isolation ------------------------------------------------------------


def test_one_workers_conversation_never_appears_in_anothers():
    manager = AgentManager(clock=Clock())
    first = manager.spawn("Rename old_function_name everywhere.")
    second = manager.spawn("Write the README section about the panel.")
    first_ask = Replies([action("find_text", query="old_function_name"), FINISH])
    second_ask = Replies([action("read_file", path="README.md"), FINISH])
    run(first, manager, first_ask, Executor("first result"))
    run(second, manager, second_ask, Executor("second result"))
    first_text = json.dumps(first.conversation)
    second_text = json.dumps(second.conversation)
    assert "old_function_name" in first_text
    assert "old_function_name" not in second_text, second_text
    assert "README" in second_text
    assert "README" not in first_text, first_text
    assert first.conversation is not second.conversation


def test_each_worker_is_asked_exactly_the_task_it_was_given():
    manager = AgentManager(clock=Clock())
    task = "Add a --json flag to the report command, and nothing else."
    record = manager.spawn(task, model="anthropic/some-model")
    ask = Replies([FINISH])
    run(record, manager, ask, prompt="THE WORKER PROMPT")
    sent = ask.calls[0]["messages"]
    assert sent[0] == {"role": "system", "content": "THE WORKER PROMPT"}, sent[0]
    assert sent[1] == {"role": "user", "content": task}, sent[1]
    # And the worker's own model and silence are what the call was made with.
    assert ask.calls[0]["model"] == "anthropic/some-model"
    assert ask.calls[0]["quiet"] is True, "a worker asked for a spinner"


def test_a_worker_with_no_model_of_its_own_leaves_the_choice_alone():
    """`model=None` is how `ask_model` is told to use the session's model.
    Sending "" instead would be a request for a model nobody has."""
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    ask = Replies([FINISH])
    run(record, manager, ask)
    assert ask.calls[0]["model"] is None, ask.calls[0]["model"]


# --- the worker loop ------------------------------------------------------


def test_the_worker_finishes_on_internal_response_and_returns_it():
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    ask = Replies([action("read_file", path="a.py"),
                   action("internal_response", response="I read a.py; it is empty.")])
    execute = Executor("")
    assert run(record, manager, ask, execute) == "I read a.py; it is empty."
    assert execute.actions == ["read_file"], execute.actions
    assert len(ask.calls) == 2, ask.calls


def test_internal_response_terminates_a_worker_even_inside_a_batch():
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    ask = Replies([json.dumps({"actions": [
        {"action": "read_file", "path": "a.py"},
        {"action": "internal_response", "response": "batch answer"},
        {"action": "read_file", "path": "never.py"},
    ]})])
    execute = Executor("")
    assert run(record, manager, ask, execute) == "batch answer"
    assert execute.actions == ["read_file"], execute.actions


def test_a_batch_runs_every_entry_and_reports_them_all_back():
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    ask = Replies([json.dumps({"actions": [
        {"action": "read_file", "path": "a.py"},
        {"action": "read_file", "path": "b.py"},
    ]}), FINISH])
    execute = Executor("file contents")
    run(record, manager, ask, execute)
    assert execute.actions == ["read_file", "read_file"], execute.actions
    fed_back = ask.calls[1]["messages"][-1]["content"]
    assert fed_back.count("read_file: file contents") == 2, fed_back


def test_a_worker_may_not_end_a_conversation():
    """`end_conversation` ends a turn for a user, and a worker has no user.
    Refused before dispatch, and the model is told which verb to use.

    `respond` is driven beside it because it is the name a model trained on
    the old shape reaches for: it is translated to `end_conversation` before
    the whitelist is consulted, so the single refusal covers both spellings
    and there is no old name that walks past it."""
    manager = AgentManager(clock=Clock())
    for forbidden in ("end_conversation", "respond"):
        record = manager.spawn("do the thing")
        ask = Replies([action(forbidden, message="here is your answer"), FINISH])
        execute = Executor()
        assert run(record, manager, ask, execute) == "task complete"
        assert execute.actions == [], (forbidden, execute.actions)
        complaint = ask.calls[1]["messages"][-1]["content"]
        assert "REFUSED" in complaint and "internal_response" in complaint, complaint


def test_a_worker_is_refused_git_push_before_it_is_dispatched():
    manager = AgentManager(clock=Clock())
    record = manager.spawn("commit and push the work")
    ask = Replies([action("git_push"), FINISH])
    execute = Executor()
    run(record, manager, ask, execute)
    assert execute.actions == [], execute.actions


def test_a_worker_that_reached_the_dispatcher_would_still_be_blocked_from_pushing():
    """Belt and braces. The whitelist is the belt; this is the part the user's
    safety actually rests on, and it is asserted against the real dispatcher."""
    import agent_actions
    context = agent_worker._context(manager_record())
    assert "push_authorized" not in context or not context["push_authorized"]
    assert agent_actions.execute_action({"action": "git_push"}, context) == \
        agent_actions.PUSH_BLOCKED
    # And the main AI, with the user's own words behind it, is not affected.
    assert agent_actions.execute_action(
        {"action": "git_push"}, {"push_authorized": True}) != agent_actions.PUSH_BLOCKED


def manager_record():
    manager = AgentManager(clock=Clock())
    return manager.spawn("commit and push the work")


def test_a_worker_cannot_spawn_workers_of_its_own():
    """No manager reaches a worker's context, so the orchestration actions
    report themselves unavailable rather than letting the fleet breed."""
    record = manager_record()
    assert "manager" not in agent_worker._context(record), agent_worker._context(record)


# --- the note agent -------------------------------------------------------


def test_the_note_agent_can_read_and_search():
    manager = AgentManager(clock=Clock())
    record = manager.spawn("where is the prompt assembled?", kind="note")
    ask = Replies([action("find_text", query="get_system_prompt"),
                   action("read_lines", path="agent_prompt.py", start=1, end=40),
                   action("internal_response", response="In agent_prompt.get_system_prompt.")])
    execute = Executor("agent_prompt.py:426")
    answer = run(record, manager, ask, execute, note=True)
    assert answer == "In agent_prompt.get_system_prompt."
    assert execute.actions == ["find_text", "read_lines"], execute.actions


def test_the_note_agent_is_refused_every_verb_that_changes_anything():
    """A whitelist, checked before dispatch. A blacklist would silently admit
    every action added to TMT after it was written."""
    writing = [
        ("write_file", {"path": "a.py", "content": "x"}),
        ("append_file", {"path": "a.py", "content": "x"}),
        ("patch_file", {"path": "a.py", "search": "a", "replace": "b"}),
        ("delete_file", {"path": "a.py"}),
        ("delete_folder", {"path": "src"}),
        ("create_folder", {"path": "src"}),
        ("rename_file", {"path": "a.py", "new_name": "b.py"}),
        ("replace_across", {"search": "a", "replace": "b", "apply": True}),
        ("run_file", {"path": "run_tests.py"}),
        ("git_commit", {"message": "x"}),
        ("git_push", {}),
        ("remember", {"note": "x"}),
    ]
    for name, keys in writing:
        manager = AgentManager(clock=Clock())
        record = manager.spawn("a question", kind="note")
        ask = Replies([action(name, **keys),
                       action("internal_response", response="answered")])
        execute = Executor()
        assert run(record, manager, ask, execute, note=True) == "answered"
        assert execute.actions == [], (name, execute.actions)
        complaint = ask.calls[1]["messages"][-1]["content"]
        assert "REFUSED" in complaint, (name, complaint)


def test_the_note_whitelist_holds_no_verb_that_changes_the_workspace():
    """Asserted against the list itself as well as through the loop, so a verb
    added to the whitelist by mistake is caught even if nobody writes a test
    driving it."""
    import agent_config
    for name in agent_worker.NOTE_ACTIONS:
        assert name not in agent_config.MUTATING_ACTIONS, name
    for name in ("run_file", "run_python", "git_commit", "git_push", "open_app",
                 "remember", "replace_across", "end_conversation",
                 # The old spellings are not on it either. They are not on any
                 # list: the translation happens first, so a whitelist naming
                 # them would be a second, drifting copy of the same fact.
                 "respond", "done", "announce"):
        assert name not in agent_worker.NOTE_ACTIONS, name


def test_the_note_agent_returns_exactly_one_response_object():
    manager = AgentManager(clock=Clock())
    record = manager.spawn("how many modules are there?", kind="note")
    ask = Replies([action("internal_response", response="Twenty-four."),
                   action("internal_response", response="No, twenty-five.")])
    assert run(record, manager, ask, note=True) == "Twenty-four."
    assert len(ask.calls) == 1, "the note agent was asked again after answering"


# --- the worker loop: recovering, and knowing when it has not ------------


def test_unreadable_json_is_handed_back_and_does_not_spend_a_round():
    """Two budgets, and they are not the same budget. A reply with a comma out
    of place is not a step of the work."""
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    ask = Replies(["{not json at all", "still not json", FINISH])
    assert run(record, manager, ask) == "task complete"
    assert len(ask.calls) == 3, ask.calls
    complaint = ask.calls[1]["messages"][-1]["content"]
    assert "INVALID" in complaint and "JSON" in complaint, complaint


def test_a_worker_that_never_sends_readable_json_stops_and_says_so():
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    # Each one differently broken, so it is the retry budget being tested and
    # not the circuit breaker, which would otherwise get there first.
    ask = Replies(["{bad %d" % index
                   for index in range(agent_worker.MAX_INVALID_RETRIES + 3)])
    said = run(record, manager, ask)
    assert "could not be read" in said, said
    assert record.status == Status.FAILED, record.status
    assert manager.result(record.id) == said
    assert len(ask.calls) == agent_worker.MAX_INVALID_RETRIES + 1, len(ask.calls)


def test_three_identical_replies_stop_the_worker_before_the_retry_budget_does():
    """A model repeating itself is not correcting anything, and confirming
    that with six more requests is waste."""
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    ask = Replies(["{bad"] * 10)
    said = run(record, manager, ask)
    assert "same reply three times" in said, said
    assert len(ask.calls) == 3, len(ask.calls)
    assert record.status == Status.FAILED


def test_an_invalid_action_is_handed_back_rather_than_ending_the_agent():
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    ask = Replies([action("write_file", path="a.py"),  # no content
                   action("write_file", path="a.py", content="x"), FINISH])
    execute = Executor()
    assert run(record, manager, ask, execute) == "task complete"
    assert execute.actions == ["write_file"], execute.actions
    complaint = ask.calls[1]["messages"][-1]["content"]
    assert "INVALID" in complaint, complaint


def test_an_action_that_raises_is_handed_back_and_does_not_end_the_agent():
    """`execute_action` reads its arguments straight off the object, so a key
    of the wrong type used to raise out of the loop entirely."""
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    calls = []

    def execute(obj, context):
        calls.append(obj["action"])
        if len(calls) == 1:
            raise TypeError("'>' not supported between instances of 'str' and 'int'")
        return "ok"

    ask = Replies([action("read_lines", path="a.py", start="1"),
                   action("read_lines", path="a.py", start=1), FINISH])
    assert run(record, manager, ask, execute) == "task complete"
    complaint = ask.calls[1]["messages"][-1]["content"]
    assert "TypeError" in complaint, complaint
    assert calls == ["read_lines", "read_lines"], calls


def test_a_worker_that_runs_out_of_steps_is_recorded_failed_with_a_reason():
    manager = AgentManager(clock=Clock())
    record = manager.spawn("an endless job")
    # A different path each time: identical replies would trip the circuit
    # breaker long before the round budget ran out.
    ask = Replies([action("read_file", path="file%d.py" % index)
                   for index in range(agent_worker.WORKER_ROUNDS + 5)])
    said = run(record, manager, ask, Executor("contents"))
    assert "ran out of steps" in said, said
    assert record.status == Status.FAILED, record.status
    assert len(ask.calls) == agent_worker.WORKER_ROUNDS, len(ask.calls)


def test_a_message_costs_a_step_and_tells_the_model_nobody_saw_it():
    """`send_message` is on a worker's whitelist and is answered rather than
    refused -- but a worker has no user, so the sentence it wrote reached
    nobody. It has to be told that in as many words, or a worker that thinks
    it has reported something spends its budget narrating to an empty room.

    The legacy `announce` is driven beside it: the worker translates it before
    the whitelist is consulted, so it is the same verb and gets the same
    answer rather than being refused as an unknown one."""
    for verb in ("send_message", "announce"):
        manager = AgentManager(clock=Clock())
        record = manager.spawn("do the thing")
        ask = Replies([action(verb, message="I will read the parser first."),
                       FINISH])
        execute = Executor()
        assert run(record, manager, ask, execute) == "task complete", verb
        assert execute.actions == [], execute.actions
        told = ask.calls[1]["messages"][-1]["content"]
        assert "Nothing you write reaches anybody" in told, (verb, told)
        assert "costs a step" in told, (verb, told)


def test_prose_after_real_work_is_kept_as_the_agents_own_report():
    """`ask_model` turns a sentence into a prose-marked `done`. Discarding it
    would throw away the only account of work that actually happened."""
    import agent_model
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    prose = json.dumps({"action": "end_conversation", "message": "I renamed it in three files.",
                        agent_model.PROSE_KEY: True})
    ask = Replies([action("write_file", path="a.py", content="x"), prose])
    assert run(record, manager, ask, Executor()) == "I renamed it in three files."


def test_prose_before_any_work_is_handed_back_rather_than_accepted():
    import agent_model
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    prose = json.dumps({"action": "end_conversation",
                        "message": "I'll start by reading it.",
                        agent_model.PROSE_KEY: True})
    ask = Replies([prose, FINISH])
    assert run(record, manager, ask) == "task complete"
    told = ask.calls[1]["messages"][-1]["content"]
    assert "prose" in told, told


def test_a_provider_failure_stops_the_agent_instead_of_being_retried():
    """Nobody's to correct: the call did not land, and asking the same
    provider again would not land either."""
    import agent_model
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    broken = json.dumps({"action": "end_conversation", "message": "OpenRouter error - 502",
                         agent_model.SYNTHETIC_KEY: True,
                         agent_model.SYNTHETIC_REASON: agent_model.PROVIDER_FAILURE})
    ask = Replies([broken, FINISH])
    said = run(record, manager, ask)
    assert "502" in said, said
    assert record.status == Status.FAILED
    assert len(ask.calls) == 1, "a provider failure was retried"


def test_a_parse_failure_is_handed_back_rather_than_shown_as_an_answer():
    import agent_model
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    unreadable = json.dumps({"action": "end_conversation", "message": "no JSON object found",
                             agent_model.SYNTHETIC_KEY: True,
                             agent_model.SYNTHETIC_REASON: agent_model.PARSE_FAILURE})
    ask = Replies([unreadable, FINISH])
    assert run(record, manager, ask) == "task complete", "a synthetic reply became the answer"


# --- tokens ---------------------------------------------------------------


def test_a_provider_figure_is_exact_and_an_estimate_says_it_is_not():
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")

    def ask(messages, on_event=None, model=None, max_tokens=None, quiet=False):
        on_event(("output", 400))
        # Mid-stream: an estimate, and it must not claim to be more.
        assert record.tokens_out > 0
        assert record.tokens_out_exact is False, "an estimate was marked exact"
        on_event(("usage", 91))
        on_event(("input_usage", 12345))
        return FINISH

    run(record, manager, ask)
    assert record.tokens_out == 91, record.tokens_out
    assert record.tokens_out_exact is True
    assert record.tokens_in == 12345 and record.tokens_in_exact is True
    assert record.total_tokens() == 91 + 12345


def test_output_tokens_accumulate_across_requests_instead_of_being_replaced():
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    replies = [action("read_file", path="a.py"), FINISH]

    def ask(messages, on_event=None, model=None, max_tokens=None, quiet=False):
        on_event(("usage", 50))
        return replies.pop(0)

    run(record, manager, ask, Executor(""))
    assert record.tokens_out == 100, record.tokens_out
    # Nothing is in flight once the agent has stopped asking.
    assert record.tokens_out_pending == 0, record.tokens_out_pending


def test_an_input_estimate_is_never_reported_as_exact():
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing" * 50)
    run(record, manager, Replies([FINISH]))
    assert record.tokens_in > 0, record.tokens_in
    assert record.tokens_in_exact is False, "an estimated input figure claimed to be exact"


def test_a_malformed_token_event_is_ignored_rather_than_ending_the_agent():
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")

    def ask(messages, on_event=None, model=None, max_tokens=None, quiet=False):
        on_event(("usage", "not a number"))
        on_event("not even a tuple")
        on_event(())
        return FINISH

    assert run(record, manager, ask) == "task complete"
    assert record.tokens_out_exact is False


# --- the rules the whole design rests on ---------------------------------


def test_a_worker_never_prints():
    """A console write from a background thread lands on top of the live
    region and corrupts the arithmetic that repaints it."""
    manager = AgentManager(clock=Clock())
    record = manager.spawn("do the thing")
    ask = Replies(["{unreadable", action("write_file", path="a.py"),
                   action("send_message", message="hello"),
                   action("write_file", path="a.py", content="x"), FINISH])
    out, err = io.StringIO(), io.StringIO()
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        run(record, manager, ask, Executor())
    finally:
        sys.stdout, sys.stderr = saved
    assert out.getvalue() == "", out.getvalue()
    assert err.getvalue() == "", err.getvalue()


def _import_lines(source, module_scope_only=False):
    """Every import statement in a module, lazy ones included."""
    lines = []
    for line in source.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("import ") or stripped.startswith("from ")):
            continue
        if module_scope_only and line != stripped:
            continue
        lines.append(stripped)
    return lines


def test_the_manager_holds_no_interface_and_no_model():
    """It is the one piece that can be tested completely, and it stays that
    way only while it imports none of them -- lazily included, which is why
    every import in the file is checked and not only the ones at the top."""
    source = open(agent_manager.__file__, encoding="utf-8").read()
    for line in _import_lines(source):
        for forbidden in ("agent_ui", "agent_menu", "agent_model",
                          "agent_actions", "rich"):
            assert forbidden not in line, line
    assert "print(" not in source


def test_the_worker_imports_nothing_at_module_scope_that_could_draw():
    """It may reach agent_ui for a constant once it is running -- the same
    constant the corner meter estimates with, so the two cannot drift -- but
    nothing it needs may be pulled in merely by importing the module, and it
    must contain no printing code at all."""
    source = open(agent_worker.__file__, encoding="utf-8").read()
    assert "print(" not in source
    for line in _import_lines(source, module_scope_only=True):
        for forbidden in ("agent_ui", "agent_menu", "agent_model",
                          "agent_actions", "agent_config", "rich"):
            assert forbidden not in line, line


# --- the path sandbox -----------------------------------------------------


def test_a_workers_destructive_action_cannot_reach_outside_the_workspace():
    """Path validation is not relaxed for a worker. Driven through the real
    dispatcher with a worker's own context, because that is the only path a
    worker can take -- the same shape as the existing test in
    test_agent_toolflow.py, aimed at the most destructive tool there is."""
    import tempfile
    from pathlib import Path
    from test_agent_toolflow import Project, remove_tree

    outer = Path(tempfile.mkdtemp(prefix="tmt_outside_agent_")).resolve()
    victim = outer / "untouchable.txt"
    victim.write_bytes(b"old_function_name\n")
    box = Project(files={"src/calc.py": "def old_function_name():\n    pass\n"})
    manager = AgentManager(clock=Clock())
    record = manager.spawn("rename it everywhere")
    try:
        import agent_actions
        ask = Replies([
            action("replace_across", search="old_function_name",
                   replace="wrecked", path="..", apply=True),
            action("write_file", path="../untouchable.txt", content="wrecked"),
            FINISH,
        ])
        run(record, manager, ask, execute=agent_actions.execute_action)
        assert victim.read_bytes() == b"old_function_name\n", victim.read_bytes()
        assert "old_function_name" in box.read("src/calc.py")
    finally:
        box.close()
        remove_tree(outer)
