"""The delegation contract enforced: through the real loop and the real dispatcher.

`test_agent_delegation.py` tests the RULE. This file tests that nothing gets
past it, which is a different question and the one section 49 calls the most
important part of the feature.

So every read-only assertion here is made against a REAL dispatcher --
`agent_actions.execute_action` over a real temporary workspace -- and checks
the file on disk afterwards rather than reading the sentence the action
returned. A refusal that returned the right words and wrote the file anyway
would pass a test that only read the words.

The bypass tests are the point. A constraint enforced on the single-action
path and not on the batch path is a constraint a model walks round by putting
the write in a list, and this repository has been bitten twice by a branch
that was only ever rehearsed in the rare case. So each one is driven both ways.

Nothing here sleeps. The deadline is driven by advancing an injected clock,
which is what `agent_manager` takes one for -- a test that proved a
ten-minute rule by waiting ten minutes would be measuring `time.sleep`. Every
blocking wait has a short real timeout, because this suite has no per-test
timeout and a test that blocked on an event nobody sets would hang the run.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

import agent_actions
import agent_config
import agent_delegation as D
import agent_manager
import agent_panel
import agent_worker
from agent_manager import AgentManager, CapacityError, Status

# Long enough that a slow machine does not fail a passing test, short enough
# that a genuinely broken one fails the run instead of hanging it.
WAIT = 5.0


class Clock:
    """A clock the test moves by hand. Never waited on, only advanced."""

    def __init__(self, now=1000.0):
        self.now = float(now)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)
        return self.now


class Workspace:
    """A real temporary workspace, with agent_config pointed at it.

    Real because the read-only tests have to check the FILESYSTEM afterwards.
    A refusal that returns the right sentence and writes the file anyway is
    exactly the failure being guarded against, and only the disk can tell.
    """

    def __init__(self, files=None):
        self.path = Path(tempfile.mkdtemp(prefix="tmt_delegation_"))
        self._saved = agent_config.ROOT_DIR
        agent_config.set_workspace_root(self.path)
        for name, content in (files or {}).items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    def read(self, name):
        target = self.path / name
        return target.read_text(encoding="utf-8") if target.exists() else None

    def exists(self, name):
        return (self.path / name).exists()

    def close(self):
        agent_config.set_workspace_root(self._saved)
        shutil.rmtree(self.path, ignore_errors=True)


def act(name, **keys):
    keys["action"] = name
    return json.dumps(keys)


FINISH = act("internal_response", response="done")


class Replies:
    """Scripted model replies, handed out in order."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def __call__(self, messages, on_event=None, model=None, max_tokens=None,
                 quiet=False, **extra):
        self.calls += 1
        self.messages = [dict(m) for m in messages]
        if not self.replies:
            return FINISH
        return self.replies.pop(0)


def spawn(manager, task="do the thing", constraints=None, **keys):
    """One worker under a contract, parsed the way spawn_agent parses it."""
    if isinstance(constraints, dict) or constraints is None:
        constraints, error = D.parse(constraints)
        assert error == "", error
    return manager.spawn(task, constraints=constraints, **keys)


def run(record, manager, replies, execute=None):
    """Run one worker to completion with the REAL dispatcher unless told not to."""
    return agent_worker.run_worker(
        record, manager, ask=Replies(replies),
        execute=execute or agent_actions.execute_action,
        system_prompt="worker prompt")


# --- read-only, against the real dispatcher and the real disk ---------------

MUTATIONS = (
    ("write_file", {"path": "target.txt", "content": "written"}),
    ("append_file", {"path": "target.txt", "content": "appended"}),
    ("patch_file", {"path": "target.txt", "search": "original",
                    "replace": "patched"}),
    ("replace_lines", {"path": "target.txt", "start": 1, "end": 1,
                       "content": "replaced"}),
    ("delete_file", {"path": "target.txt"}),
    ("copy_file", {"path": "target.txt", "to": "copy.txt"}),
    ("rename_file", {"path": "target.txt", "new_name": "moved.txt"}),
    ("create_folder", {"path": "made"}),
    ("delete_folder", {"path": "sub", "recursive": True}),
    ("write_files", {"files": [{"path": "one.txt", "content": "one"}]}),
    ("replace_across", {"search": "original", "replace": "gone", "apply": True}),
    ("bash", {"command": "python writer.py"}),
    ("git_commit", {"message": "should never happen"}),
    ("remember", {"note": "should never be stored"}),
    ("open_app", {"app": "notepad", "path": "target.txt"}),
)

FILES = {
    "target.txt": "original\n",
    "sub/inner.txt": "inner\n",
    # A script that writes a file if it is ever run. `bash` is the general
    # code-execution path a worker has -- it replaced `run_file`, and it is a
    # wider one, because a command can be anything -- and section 7 is about
    # exactly this: a delegation must not be able to mutate the workspace by
    # running a program instead of by calling a writing verb.
    "writer.py": "open('escaped.txt','w').write('a program wrote this')\n",
}


def _unchanged(box):
    """Everything FILES set up, exactly as it was set up."""
    return (box.read("target.txt") == "original\n"
            and box.read("sub/inner.txt") == "inner\n"
            and not box.exists("escaped.txt")
            and not box.exists("copy.txt")
            and not box.exists("moved.txt")
            and not box.exists("made")
            and not box.exists("one.txt"))


def test_a_read_only_delegation_changes_nothing_on_disk_whatever_it_asks_for():
    """Every mutation path in TMT, one at a time, through the real dispatcher,
    checked against the filesystem afterwards.

    The disk is the assertion. A refusal that returned the right words and
    wrote the file anyway would pass a test that only read the words, and that
    is the failure this whole feature exists to make impossible.
    """
    box = Workspace(files=FILES)
    try:
        for action, keys in MUTATIONS:
            manager = AgentManager(clock=Clock())
            record = spawn(manager, constraints={"read_only": True})
            answer = run(record, manager, [act(action, **keys), FINISH])
            assert answer == "done", (action, answer)
            assert _unchanged(box), "%s changed the workspace" % action
            assert record.violations, "%s was not recorded as a violation" % action
            assert record.violations[0]["operation"] == action, record.violations
    finally:
        box.close()


def test_the_same_mutations_in_a_BATCH_are_refused_the_same_way():
    """Section 49's bypass test, on the path this repository has twice found a
    dormant branch in. A constraint enforced on the single-action path only is
    a constraint a model walks round by putting the write in a list."""
    box = Workspace(files=FILES)
    try:
        for action, keys in MUTATIONS:
            manager = AgentManager(clock=Clock())
            record = spawn(manager, constraints={"read_only": True})
            batch = json.dumps({"actions": [
                {"action": "read_file", "path": "target.txt"},
                dict(keys, action=action)]})
            run(record, manager, [batch, FINISH])
            assert _unchanged(box), "%s changed the workspace in a batch" % action
            assert record.violations, "%s in a batch recorded no violation" % action
    finally:
        box.close()


def test_the_legacy_execution_verbs_are_translated_into_a_refusal_not_a_run():
    """The one route into execution that does not name `bash`.

    `run_file` and `run_python` are gone as verbs, but the legacy net still
    translates them so a model written against the old names ends its turn
    instead of burning its retries. `_adopt_verb` runs BEFORE the contract is
    consulted, so what the contract sees is `bash` -- which is what makes this
    worth pinning rather than assuming: an order the other way round would put
    a name no whitelist mentions in front of a whitelist check, and a
    fail-closed check would refuse it correctly today and record a violation
    naming a verb that no longer exists. The disk is the assertion either way.
    """
    box = Workspace(files=FILES)
    try:
        for action, keys in (("run_file", {"path": "writer.py"}),
                             ("run_python", {"path": "writer.py"})):
            manager = AgentManager(clock=Clock())
            record = spawn(manager, constraints={"read_only": True})
            answer = run(record, manager, [act(action, **keys), FINISH])
            assert answer == "done", (action, answer)
            assert not box.exists("escaped.txt"), "%s ran the script" % action
            assert record.violations, "%s recorded no violation" % action
            assert record.violations[0]["operation"] == "bash", record.violations
    finally:
        box.close()


def test_a_read_only_delegation_can_still_read_the_file_it_may_not_write():
    """The constraint is read-only, not do-nothing. If this failed the feature
    would be useless for the investigation it exists for."""
    box = Workspace(files=FILES)
    try:
        manager = AgentManager(clock=Clock())
        record = spawn(manager, constraints={"read_only": True})
        answer = run(record, manager, [
            act("read_file", path="target.txt"),
            act("list_files"),
            act("grep", query="original"),
            act("internal_response", response="target.txt says 'original'")])
        assert "original" in answer, answer
        assert not record.violations, record.violations
        assert record.reads == ("target.txt",), record.reads
    finally:
        box.close()


def test_the_dispatcher_refuses_a_write_even_reached_directly():
    """The second layer. `agent_worker` has already refused every mutating verb
    before dispatch; this is what refuses one that reached the dispatcher
    another way -- a direct call, a test, a third dispatch path somebody adds
    later. Both layers ask the same function, so there is one rule."""
    box = Workspace(files=FILES)
    try:
        context = {"push_authorized": False, "agent_id": "1",
                   "agent_kind": "worker", "read_only": True}
        for action, keys in MUTATIONS:
            said = agent_actions.execute_action(dict(keys, action=action), context)
            assert D.VIOLATION_HEADER in said, (action, said)
            assert _unchanged(box), "%s changed the workspace at the dispatcher" % action
        # And reading through the same context still works.
        assert "original" in agent_actions.execute_action(
            {"action": "read_file", "path": "target.txt"}, context)
    finally:
        box.close()


def test_an_unconstrained_delegation_writes_exactly_as_it_always_did():
    """Section 4, and the half of it that matters most: nothing about an
    existing worker changed. Same spawn, same loop, same dispatcher, and the
    file is written."""
    box = Workspace(files=FILES)
    try:
        manager = AgentManager(clock=Clock())
        record = manager.spawn("write the file")          # no constraints at all
        assert record.constraints is None
        run(record, manager, [act("write_file", path="new.txt",
                                  content="written"), FINISH])
        assert box.read("new.txt") == "written", box.read("new.txt")
        assert record.violations == ()
        assert record.paths == ("new.txt",)
    finally:
        box.close()


def test_a_worker_context_carries_read_only_only_when_it_is_read_only():
    """Absent rather than False for an unconstrained delegation, so an ordinary
    worker's context is the dict it always was and the dispatcher's guard is
    not even consulted."""
    manager = AgentManager(clock=Clock())
    plain = manager.spawn("plain")
    assert "read_only" not in agent_worker._context(plain)
    constrained = spawn(manager, constraints={"read_only": True})
    assert agent_worker._context(constrained)["read_only"] is True
    writing = spawn(manager, constraints={"read_only": False,
                                          "timeout_seconds": 60})
    assert "read_only" not in agent_worker._context(writing)


def test_a_read_only_delegation_cannot_spawn_a_worker_that_would_write():
    """Section 62 and 63. Nested spawning is not supported in TMT -- a
    background agent's context carries no manager at all -- so the answer here
    is to prove the existing isolation still holds rather than to invent a
    propagation rule for something that cannot happen.

    Both halves are asserted: the verb is refused by the contract, and the
    action itself reports no register even when reached directly.
    """
    manager = AgentManager(clock=Clock())
    record = spawn(manager, constraints={"read_only": True})
    assert D.refusal(record.constraints, "spawn_agent"), "spawn_agent was allowed"
    said = agent_actions.execute_action(
        {"action": "spawn_agent", "task": "write everything"},
        agent_worker._context(record))
    assert D.VIOLATION_HEADER in said, said
    # And for an unconstrained worker, which is refused for the other reason.
    plain = manager.spawn("plain")
    said = agent_actions.execute_action(
        {"action": "spawn_agent", "task": "write everything"},
        agent_worker._context(plain))
    assert "not available here" in said, said


def test_a_blocked_write_is_reported_to_the_model_so_it_can_adjust():
    """Section 8. The worker is told, in words it can act on, and the task
    carries on -- a violation is not automatically a failed delegation."""
    box = Workspace(files=FILES)
    try:
        manager = AgentManager(clock=Clock())
        record = spawn(manager, constraints={"read_only": True})
        ask = Replies([act("write_file", path="target.txt", content="no"),
                       act("read_file", path="target.txt"), FINISH])
        answer = agent_worker.run_worker(record, manager, ask=ask,
                                         execute=agent_actions.execute_action,
                                         system_prompt="worker prompt")
        assert answer == "done", answer
        assert record.status != Status.FAILED
        # The refusal went back to the model as its own user turn.
        said = "\n".join(str(m.get("content")) for m in ask.messages)
        assert D.VIOLATION_HEADER in said, said
        assert "read-only" in said, said
    finally:
        box.close()


# --- the worker's prompt ------------------------------------------------------

def test_a_constrained_worker_is_told_its_contract():
    """Section 64 and 65: the prompt EXPLAINS the rules. What it must not do is
    be the thing that enforces them, which the tests above already settle."""
    constraints, _ = D.parse({"read_only": True, "timeout_seconds": 600,
                              "report": {"file_list": True}})
    prompt = agent_worker._with_contract("BASE PROMPT", constraints)
    assert prompt.startswith("BASE PROMPT")
    assert "READ ONLY" in prompt
    assert "10:00" in prompt
    assert "file_list" in prompt
    assert "not requests" in prompt, "the prompt does not say who enforces it"


def test_an_unconstrained_workers_prompt_is_byte_for_byte_what_it_was():
    """"Backward compatible" is a claim; byte equality is a measurement."""
    base = "BASE PROMPT"
    assert agent_worker._with_contract(base, None) is base
    assert agent_worker._with_contract(base, D.DEFAULT) is base
    constraints, _ = D.parse({})
    assert agent_worker._with_contract(base, constraints) is base


def test_a_contract_that_cannot_be_described_still_starts_the_worker():
    """A readout must never be able to stop the work it reports on."""
    class Broken(object):
        def is_default(self):
            raise RuntimeError("no")
    assert agent_worker._with_contract("BASE", Broken()) == "BASE"


# --- the deadline -------------------------------------------------------------

def test_the_clock_starts_when_the_worker_starts_and_not_when_it_is_spawned():
    """Section 11. A delegation must not lose part of its time to something
    else being slow between the spawn and the start."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = spawn(manager, constraints={"timeout_seconds": 60})
    assert record.deadline() is None, "a spawned worker already had a deadline"
    assert record.remaining() is None
    clock.advance(300)                       # a long time passes before it starts
    record.status = Status.RUNNING
    record.started_at = clock()
    assert record.deadline() == clock() + 60
    assert record.remaining() == 60
    assert not record.expired()


def test_the_deadline_is_not_reset_by_an_action_or_by_a_model_reply():
    """Section 11: the timeout applies to the whole delegation. A per-action
    clock would let a worker with forty short steps run forever."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = spawn(manager, constraints={"timeout_seconds": 100})
    record.started_at = clock()
    record.status = Status.RUNNING
    end = record.deadline()
    for _ in range(5):
        clock.advance(15)
        agent_worker._record_reads(manager, record, "read_file", {"path": "a.py"})
        assert record.deadline() == end, "the deadline moved"
    assert record.remaining() == 25
    clock.advance(30)
    assert record.expired()


def test_a_worker_that_runs_out_of_time_dispatches_nothing_further():
    """The guarantee, stated exactly: after the deadline, no further tool call
    runs. Not "it stops instantly" -- a Python thread cannot be terminated and
    claiming otherwise would be a lie in the one place a lie is expensive."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = spawn(manager, constraints={"timeout_seconds": 60})
    dispatched = []

    class Executor:
        def __call__(self, obj, context):
            dispatched.append(obj.get("action"))
            return "ok"

    calls = []

    def ask(messages, **keys):
        calls.append(1)
        if len(calls) == 2:
            clock.advance(120)          # the deadline passes while it thinks
        return act("read_file", path="f%d.py" % len(calls))

    thread = manager.start(record, lambda rec, mgr: agent_worker.run_worker(
        rec, mgr, ask=ask, execute=Executor(), system_prompt="p"))
    thread.join(WAIT)
    assert not thread.is_alive(), "the worker did not stop at its deadline"
    assert record.status == Status.TIMED_OUT, record.status
    assert dispatched == ["read_file"], dispatched
    assert len(calls) == 2, calls


def test_a_timed_out_worker_is_not_recorded_as_killed_or_failed():
    """Section 44. Three distinct endings, and the thread wrapper has to catch
    the timeout BEFORE WorkerCancelled, which it is a subclass of -- reversing
    that order would record every timeout as a kill."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    timed = spawn(manager, constraints={"timeout_seconds": 10})
    killed = manager.spawn("killed")
    failed = manager.spawn("failed")
    for record in (timed, killed, failed):
        record.started_at = clock()
        record.status = Status.RUNNING
    clock.advance(11)
    manager.expire()
    manager.kill(killed.id)
    manager.fail(failed.id, "an exception")
    assert timed.status == Status.TIMED_OUT
    assert killed.status == Status.KILLED
    assert failed.status == Status.FAILED
    assert agent_actions._result_status(timed, agent_manager, D) == D.TIMED_OUT
    assert agent_actions._result_status(killed, agent_manager, D) == D.CANCELLED
    assert agent_actions._result_status(failed, agent_manager, D) == D.FAILED


def test_a_timeout_releases_the_slot_and_never_double_releases_it():
    """Section 26 and 28. The count is DERIVED from the records rather than
    maintained as a tally, so there is no increment to forget and no decrement
    to run twice -- a worker that times out, is killed and fails in three
    racing calls still releases exactly one slot."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    records = [spawn(manager, "t%d" % index,
                     constraints={"timeout_seconds": 30}) for index in range(3)]
    for record in records:
        record.started_at = clock()
        record.status = Status.RUNNING
    assert manager.active_count() == 3
    clock.advance(31)
    assert manager.active_count() == 0, "the slots were not released"
    # Every later transition is a no-op, however many arrive.
    for record in records:
        assert manager.time_out(record.id) is False
        assert manager.kill(record.id) is False
        assert manager.fail(record.id, "late") is None
        assert manager.complete(record.id, "late") is None
        assert record.status == Status.TIMED_OUT
    assert manager.active_count() == 0
    assert 0 <= manager.active_count() <= agent_manager.MAX_WORKERS


def test_expiry_is_idempotent_however_many_times_it_is_swept():
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = spawn(manager, constraints={"timeout_seconds": 5})
    record.started_at = clock()
    record.status = Status.RUNNING
    clock.advance(6)
    first = manager.expire()
    assert [r.id for r in first] == [record.id]
    for _ in range(5):
        assert manager.expire() == (), "a record was retired twice"
    assert record.finished_at is not None
    assert not record.expired(), "a terminal record still reports as expired"


def test_a_wait_cannot_outlive_a_deadline():
    """Without this, a wait with no timeout on a delegation whose deadline
    passes while the wait is out would block forever on a worker the contract
    had already ended -- the timeout enforced everywhere except the one call
    that is actually watching for it."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = spawn(manager, constraints={"timeout_seconds": 30})
    record.started_at = clock()
    record.status = Status.RUNNING
    clock.advance(31)                       # already past, before the wait
    finished = manager.wait([record.id], timeout=WAIT)
    assert record.id in finished, "the wait did not return the timed-out worker"
    assert record.status == Status.TIMED_OUT


def test_a_wait_on_an_untimed_worker_blocks_exactly_as_it_always_did():
    """A contract with no timeout contributes nothing to the wait's slicing."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = manager.spawn("no contract")
    record.started_at = clock()
    record.status = Status.RUNNING

    def finish():
        manager.complete(record.id, "eventually")

    timer = threading.Timer(0.05, finish)
    timer.daemon = True
    timer.start()
    finished = manager.wait([record.id], timeout=WAIT)
    timer.cancel()
    assert finished[record.id] is record
    assert record.status == Status.COMPLETED


def test_a_timeout_cannot_be_extended_after_the_worker_starts():
    """Section 39, at the runtime rather than at the parser. There is no verb
    that changes a contract, and the object itself refuses assignment."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = spawn(manager, constraints={"timeout_seconds": 30})
    record.started_at = clock()
    record.status = Status.RUNNING
    try:
        record.constraints._timeout_seconds = 99999
    except RuntimeError:
        pass
    else:
        raise AssertionError("the deadline was extended in flight")
    assert record.deadline() == record.started_at + 30
    # And there is no action that does it either.
    assert "constraints" not in agent_config.REQUIRED_KEYS.get("agent_status", [])
    for verb in ("agent_status", "agent_result", "kill_agent"):
        assert "constraint" not in " ".join(agent_config.REQUIRED_KEYS[verb])


# --- capacity ------------------------------------------------------------------

def test_one_five_and_ten_workers_all_run():
    """Section 52, and it is a statement about the real register rather than
    about a constant: each of these is spawned and started for real."""
    for count in (1, 5, 10):
        manager = AgentManager(clock=Clock())
        records = [spawn(manager, "task %d" % index) for index in range(count)]
        for record in records:
            record.status = Status.RUNNING
        assert manager.active_count() == count, count
        assert manager.capacity() == (count, 10), count


def test_the_eleventh_worker_never_becomes_running():
    """Section 24. TMT has no queue -- section 27 says not to build one where
    the architecture has none -- so the answer is the clear capacity error the
    register already produced, and the eleventh delegation genuinely does not
    exist afterwards."""
    manager = AgentManager(clock=Clock())
    for index in range(agent_manager.MAX_WORKERS):
        record = spawn(manager, "task %d" % index)
        record.status = Status.RUNNING
    before = len(manager.list())
    try:
        spawn(manager, "the eleventh")
    except CapacityError as error:
        said = str(error)
    else:
        raise AssertionError("an eleventh worker was accepted")
    assert "maximum of 10" in said, said
    assert "wait_for_agents" in said and "kill_agent" in said, said
    assert len(manager.list()) == before, "a refused worker was half-registered"
    assert manager.active_count() == agent_manager.MAX_WORKERS


def test_every_way_a_worker_can_end_frees_its_slot():
    """Sections 28, 29, 30 and 43 in one test, because they are one rule: the
    count is what is NOT terminal, and every ending is terminal."""
    for ending in ("complete", "fail", "kill", "timeout"):
        clock = Clock()
        manager = AgentManager(clock=clock)
        records = [spawn(manager, "t%d" % index,
                         constraints={"timeout_seconds": 30})
                   for index in range(agent_manager.MAX_WORKERS)]
        for record in records:
            record.started_at = clock()
            record.status = Status.RUNNING
        assert manager.active_count() == agent_manager.MAX_WORKERS
        victim = records[4]
        if ending == "complete":
            manager.complete(victim.id, "done")
        elif ending == "fail":
            manager.fail(victim.id, "crashed")
        elif ending == "kill":
            manager.kill(victim.id)
        else:
            clock.advance(31)
        expected = 0 if ending == "timeout" else agent_manager.MAX_WORKERS - 1
        assert manager.active_count() == expected, ending
        # And the freed capacity is real: another delegation starts.
        extra = spawn(manager, "now there is room")
        assert extra is not None, ending
        assert manager.active_count() <= agent_manager.MAX_WORKERS, ending


def test_a_timed_out_worker_frees_its_slot_without_anybody_having_looked_first():
    """Section 28, end to end, and the ONE thing every other capacity test
    here quietly failed to check.

    A worker times out. Nobody reads `active_count`, nobody repaints, nobody
    waits -- the very next thing that happens is the eleventh `spawn_agent`.
    That has to succeed, which means the sweep has to run inside `spawn`
    itself, before the capacity check, rather than relying on something else
    having noticed.

    Found by mutation testing: removing `self.expire()` from `spawn` broke
    nothing, because every other test in this file called `active_count`
    first -- and `active_count` sweeps.
    """
    clock = Clock()
    manager = AgentManager(clock=clock)
    for index in range(agent_manager.MAX_WORKERS):
        record = spawn(manager, "task %d" % index,
                       constraints={"timeout_seconds": 30})
        record.started_at = clock()
        record.status = Status.RUNNING
    clock.advance(31)
    # Nothing has looked at the register since the clock moved.
    eleventh = spawn(manager, "the work that was waiting for a slot")
    assert eleventh.id == str(agent_manager.MAX_WORKERS + 1), eleventh.id
    assert manager.active_count() == 1, manager.active_count()
    for index in range(agent_manager.MAX_WORKERS):
        assert manager.inspect(str(index + 1)).status == Status.TIMED_OUT


def test_a_killed_worker_frees_its_slot_without_anybody_having_looked_first():
    """The same for cancellation -- section 29 -- which does not need a sweep
    at all, because `kill` is a terminal transition and the count is derived.
    Asserted anyway, so the two paths are held to the same standard."""
    manager = AgentManager(clock=Clock())
    records = [spawn(manager, "task %d" % index)
               for index in range(agent_manager.MAX_WORKERS)]
    for record in records:
        record.status = Status.RUNNING
    manager.kill(records[4].id)
    extra = spawn(manager, "the work that was waiting")
    assert extra.id == str(agent_manager.MAX_WORKERS + 1), extra.id
    assert manager.active_count() == agent_manager.MAX_WORKERS


def test_the_count_never_exceeds_ten_or_falls_below_zero_under_concurrency():
    """Section 53. Ten workers finishing on ten threads at once, with the
    capacity read throughout -- the invariant has to hold from every angle at
    every moment, not only at the end."""
    manager = AgentManager(clock=Clock())
    records = [spawn(manager, "t%d" % index)
               for index in range(agent_manager.MAX_WORKERS)]
    for record in records:
        record.status = Status.RUNNING
    seen = []
    stop = threading.Event()

    def watch():
        while not stop.is_set():
            seen.append(manager.active_count())

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    barrier = threading.Barrier(len(records))

    def finish(record, index):
        barrier.wait(WAIT)
        # Four endings racing on the same records, deliberately: the same
        # worker is completed, failed and killed from different threads.
        manager.complete(record.id, "done")
        manager.fail(record.id, "also")
        manager.kill(record.id)
        manager.expire()

    threads = [threading.Thread(target=finish, args=(record, index), daemon=True)
               for index, record in enumerate(records)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT)
        assert not thread.is_alive()
    stop.set()
    watcher.join(WAIT)
    assert seen, "the watcher never read the count"
    assert min(seen) >= 0, min(seen)
    assert max(seen) <= agent_manager.MAX_WORKERS, max(seen)
    assert manager.active_count() == 0
    # Each one ended exactly once, as whichever transition landed first.
    for record in records:
        assert record.is_terminal(), record
        assert record.finished_at is not None


def test_ten_workers_each_keep_their_own_contract():
    """Section 22 and 54, through the register rather than through the parser:
    ten records, ten contracts, no leakage in either direction."""
    manager = AgentManager(clock=Clock())
    records = []
    for index in range(agent_manager.MAX_WORKERS):
        records.append(spawn(manager, "t%d" % index, constraints={
            "read_only": index % 2 == 0,
            "timeout_seconds": 100 + index,
            "report": {"diff": index % 2 == 1, "summary": True}}))
    for index, record in enumerate(records):
        assert record.constraints.read_only is (index % 2 == 0), index
        assert record.constraints.timeout_seconds == 100 + index, index
        assert record.constraints.report.diff is (index % 2 == 1), index
        expected = "" if index % 2 else D.VIOLATION_HEADER
        said = D.refusal(record.constraints, "write_file")
        assert (said.startswith(expected) if expected else said == ""), index


def test_a_read_only_worker_beside_a_writing_one_does_not_change_it():
    """Worker A must not inherit worker B's permissions, and the strongest
    form of that is to run both against the same real workspace."""
    box = Workspace(files=FILES)
    try:
        manager = AgentManager(clock=Clock())
        reader = spawn(manager, "read", constraints={"read_only": True})
        writer = spawn(manager, "write", constraints={"timeout_seconds": 600})
        run(reader, manager, [act("write_file", path="from_reader.txt",
                                  content="no"), FINISH])
        run(writer, manager, [act("write_file", path="from_writer.txt",
                                  content="yes"), FINISH])
        assert not box.exists("from_reader.txt"), "the read-only worker wrote"
        assert box.read("from_writer.txt") == "yes"
        assert reader.violations and not writer.violations
        assert reader.paths == () and writer.paths == ("from_writer.txt",)
    finally:
        box.close()


# --- spawn_agent, end to end ---------------------------------------------------

def test_spawn_agent_takes_a_contract_and_repeats_it_back():
    manager = AgentManager(clock=Clock())
    said = agent_actions._spawn_agent(manager, {
        "task": "investigate the parser",
        "constraints": {"read_only": True, "timeout_seconds": 600,
                        "report": {"file_list": True, "summary": True}}})
    assert "Started background agent #1" in said, said
    assert "READ ONLY" in said, said
    assert "10:00" in said, said
    assert "file_list, summary" in said, said
    assert "1 of 10 workers running" in said, said
    record = manager.inspect("1")
    assert record.constraints.read_only is True
    assert record.constraints.timeout_seconds == 600


def test_spawn_agent_with_a_bad_contract_starts_nothing_at_all():
    """Section 38. A model that wrote "timeout" instead of "timeout_seconds"
    must not be handed an untimed worker it believes has ten minutes."""
    manager = AgentManager(clock=Clock())
    said = agent_actions._spawn_agent(manager, {
        "task": "investigate", "constraints": {"timeout": 600}})
    assert said.startswith("FAILED:"), said
    assert manager.list() == (), "a worker was started under a refused contract"
    assert manager.active_count() == 0


def test_spawn_agent_with_no_contract_behaves_as_it_always_did():
    manager = AgentManager(clock=Clock())
    said = agent_actions._spawn_agent(manager, {"task": "do the thing"})
    assert "Started background agent #1 on: do the thing" in said, said
    assert "contract" not in said, said
    record = manager.inspect("1")
    assert record.constraints is D.DEFAULT or record.constraints.is_default()


def test_spawn_agent_still_refuses_a_missing_task():
    manager = AgentManager(clock=Clock())
    said = agent_actions._spawn_agent(manager, {"constraints": {"read_only": True}})
    assert "needs a 'task'" in said, said
    assert manager.list() == ()


# --- what the main agent is handed back ----------------------------------------

def _finished(manager, clock, constraints, status=Status.COMPLETED,
             result="Investigated the parser.", reads=(), paths=(),
             violations=(), steps=7, ran=42.0):
    record = spawn(manager, "investigate", constraints=constraints)
    record.started_at = clock()
    record.status = Status.RUNNING
    record.reads = tuple(reads)
    record.paths = tuple(paths)
    record.violations = tuple(violations)
    record.steps = steps
    clock.advance(ran)
    if status == Status.COMPLETED:
        manager.complete(record.id, result)
    elif status == Status.FAILED:
        manager.fail(record.id, result)
    elif status == Status.KILLED:
        manager.kill(record.id)
    else:
        manager.time_out(record.id)
    return record


def test_a_constrained_delegation_reports_as_a_structured_result():
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = _finished(manager, clock,
                       {"read_only": True, "timeout_seconds": 600,
                        "report": {"file_list": True, "diff": True,
                                   "summary": True}},
                       reads=("src/auth.py", "tests/test_auth.py"))
    said = agent_actions._agent_result(manager, {"id": record.id})
    assert "STATUS: COMPLETED" in said, said
    assert "Runtime: 0:42 of 10:00" in said, said
    assert "7 actions taken, 2 files inspected, 0 files changed" in said, said
    assert "SUMMARY" in said and "Investigated the parser." in said
    assert "FILES" in said and "src/auth.py" in said
    assert "DIFF" in said and "No changes permitted by delegation." in said


def test_an_unconstrained_delegation_reports_exactly_as_it_used_to():
    """Section 4 at the place a change would be most visible: the sentence the
    main agent reads."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = manager.spawn("do the thing")
    manager.complete(record.id, "I renamed it in three files.")
    said = agent_actions._agent_result(manager, {"id": record.id})
    assert said == ("Background agent #1 (completed) reported:\n"
                    "I renamed it in three files."), said


def test_a_timed_out_delegation_reports_what_it_managed():
    """Section 14 and 21. The file list is real -- it is the paths the
    worker's own actions named -- so it is worth having even though the work
    did not finish."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = _finished(manager, clock,
                       {"timeout_seconds": 600,
                        "report": {"file_list": True, "summary": True}},
                       status=Status.TIMED_OUT, ran=600.0, steps=17,
                       reads=tuple("f%d.py" % index for index in range(11)))
    said = agent_actions._agent_result(manager, {"id": record.id})
    assert "STATUS: TIMED OUT" in said, said
    assert "Runtime: 10:00 of 10:00" in said, said
    assert "17 actions taken, 11 files inspected" in said, said
    assert "Inspected (11)" in said, said
    assert "f10.py" in said, said


def test_a_timed_out_delegation_says_so_even_with_no_report_requirements():
    """A contract can carry a deadline and ask for nothing back. Such a
    delegation still has to be distinguishable from one that crashed, which
    the old "produced no report" sentence cannot do -- it is the same words
    for a worker stopped after seventeen useful actions and one that died on
    its first."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = _finished(manager, clock, {"timeout_seconds": 600},
                       status=Status.TIMED_OUT, ran=600.0, steps=17,
                       result="", reads=("a.py", "b.py"))
    assert manager.result(record.id) == "", "the premise is gone"
    said = agent_actions._agent_result(manager, {"id": record.id})
    assert "STATUS: TIMED OUT" in said, said
    assert "Runtime: 10:00 of 10:00" in said, said
    assert "17 actions taken, 2 files inspected" in said, said
    assert "produced no report" not in said, said


def test_an_unconstrained_delegation_with_nothing_to_say_still_says_that():
    """And the sentence is untouched where it was right: a plain worker that
    produced nothing has no structure to fall back on and nothing else to
    report."""
    manager = AgentManager(clock=Clock())
    record = manager.spawn("plain")
    manager.complete(record.id, "")
    said = agent_actions._agent_result(manager, {"id": record.id})
    assert said == ("Background agent #1 is completed and produced no report."), said


def test_a_cancelled_delegation_is_reported_as_cancelled_and_not_as_failed():
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = _finished(manager, clock, {"report": {"summary": True}},
                       status=Status.KILLED)
    said = agent_actions._agent_result(manager, {"id": record.id})
    assert "STATUS: CANCELLED" in said, said
    assert "FAILED" not in said, said


def test_a_delegation_that_only_ever_hit_the_contract_says_so():
    """A worker refused every write it tried and that produced nothing is not
    a completion. "Completed" would tell the main agent the work is done when
    the contract is the reason it is not."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = _finished(manager, clock, {"read_only": True},
                       status=Status.FAILED, result="stopped: refused",
                       violations=(D.violation("write_file", ["a.py"]),))
    said = agent_actions._agent_result(manager, {"id": record.id})
    assert "STATUS: CONSTRAINT VIOLATION" in said, said
    assert "1 write operation blocked" in said, said


def test_violations_reach_the_main_agent_even_when_no_report_was_asked_for():
    """Section 41. A blocked write is often the reason a delegation did not
    finish, and it must not be possible to hide it by asking for no report."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = _finished(manager, clock, {"read_only": True},
                       violations=(D.violation("write_file", ["a.py"]),
                                   D.violation("bash", [])))
    said = agent_actions._agent_result(manager, {"id": record.id})
    assert "Constraint violations: 2 write operations blocked" in said, said


def test_a_wait_reports_every_delegation_in_its_own_shape():
    """One constrained and one not, collected together: each gets the report
    its own contract calls for, and the plain one is untouched."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    constrained = _finished(manager, clock, {"report": {"summary": True}},
                            result="Structured.")
    plain = manager.spawn("plain")
    manager.complete(plain.id, "Plain sentence.")
    said = agent_actions._wait_for_agents(manager, {"timeout": WAIT})
    assert "STATUS: COMPLETED" in said and "Structured." in said, said
    assert ("Background agent #%s (completed) reported:\nPlain sentence."
            % plain.id) in said, said


def test_the_diff_is_read_from_git_for_the_files_the_worker_wrote():
    """Section 18 and 46: derived from repository state, and scoped to the
    paths this delegation's own actions named -- the main agent goes on
    working while a worker runs, so the whole tree's diff is emphatically not
    one delegation's work."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = _finished(manager, clock, {"report": {"diff": True}},
                       paths=("src/auth.py",))
    asked = {}

    class FakeRepo:
        def diff(self, paths=None):
            asked["paths"] = paths
            return "Unstaged changes:\n+ token refresh"

    class FakeGit:
        class TMTGit:
            @staticmethod
            def discover():
                return FakeRepo()

    saved = sys.modules.get("agent_git")
    sys.modules["agent_git"] = FakeGit
    try:
        said = agent_actions._agent_result(manager, {"id": record.id})
    finally:
        if saved is None:
            del sys.modules["agent_git"]
        else:
            sys.modules["agent_git"] = saved
    assert asked["paths"] == ["src/auth.py"], asked
    assert "+ token refresh" in said, said


def test_a_delegation_that_wrote_nothing_never_asks_git_anything():
    """No paths means no diff to attribute, and a git subprocess run to
    discover that would be spending it to learn what the record already
    knows."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = _finished(manager, clock, {"report": {"diff": True}})

    class Exploding:
        class TMTGit:
            @staticmethod
            def discover():
                raise AssertionError("git was asked about a worker that wrote nothing")

    saved = sys.modules.get("agent_git")
    sys.modules["agent_git"] = Exploding
    try:
        said = agent_actions._agent_result(manager, {"id": record.id})
    finally:
        if saved is None:
            del sys.modules["agent_git"]
        else:
            sys.modules["agent_git"] = saved
    assert "No workspace changes." in said, said


def test_agent_status_says_how_many_slots_are_running_and_names_the_contracts():
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = spawn(manager, "investigate",
                   constraints={"read_only": True, "timeout_seconds": 600,
                                "report": {"diff": True}})
    record.started_at = clock()
    record.status = Status.RUNNING
    plain = manager.spawn("plain")
    plain.status = Status.RUNNING
    clock.advance(90)
    said = agent_actions._agent_status(manager, {})
    assert "2 of 10 worker slots running" in said, said
    assert "READ ONLY" in said and "TIMEOUT 10:00" in said, said
    assert "(8:30 left)" in said, said
    assert "reports: diff" in said, said
    # The unconstrained one gains no contract line at all.
    lines = [line for line in said.splitlines() if line.startswith("#%s " % plain.id)]
    assert lines, said
    assert "contract" not in said.split("#%s " % plain.id)[1].split("#")[0]


# --- the interface -------------------------------------------------------------

def _plain(text):
    """The text with every escape stripped, which is how this project asserts."""
    import re
    return re.sub(r"\033\[[0-9;]*[A-Za-z]", "", text)


def test_the_counter_and_the_panel_header_say_how_many_of_ten():
    manager = AgentManager(clock=Clock())
    for index in range(4):
        manager.spawn("t%d" % index).status = Status.RUNNING
    state = agent_panel.PanelState(manager, stream=sys.stdout)
    assert state.maximum() == 10
    assert state.counter() == "4/10 agents"
    assert agent_panel.panel_title(manager.list(), 10) == "AGENTS 4/10"
    # Ten of ten, which is the state the user most needs to be able to read.
    for index in range(6):
        manager.spawn("more %d" % index).status = Status.RUNNING
    assert state.counter() == "10/10 agents"


def test_a_card_carries_its_delegations_contract_compactly():
    """Section 32: the user should be able to understand what was delegated,
    and a card is about twenty columns wide."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = spawn(manager, "investigate",
                   constraints={"read_only": True, "timeout_seconds": 600,
                                "report": {"file_list": True, "diff": True,
                                           "summary": True}})
    record.started_at = clock()
    record.status = Status.RUNNING
    clock.advance(88)
    rows = [_plain(row) for row in
            agent_panel.card_lines(record, 30, stream=sys.stdout)]
    contract = [row for row in rows if "RO" in row]
    assert contract, rows
    assert "8:32/10:00" in contract[0], contract
    assert "F D S" in contract[0], contract
    for row in rows:
        assert len(row) <= 30, row


def test_an_unconstrained_agents_card_is_the_card_it_always_was():
    manager = AgentManager(clock=Clock())
    record = manager.spawn("plain")
    manager.set_activity(record.id, "Reading agent_ui.py")
    rows = agent_panel.card_lines(record, 30, stream=sys.stdout)
    assert len(rows) == 2, [_plain(row) for row in rows]


def test_a_timed_out_agent_reads_as_timed_out_and_not_as_failed():
    """Section 34, and the interface half of the distinction the whole feature
    rests on: painting it the same as a crash would undo on screen what the
    status vocabulary was split apart to say."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = spawn(manager, "slow", constraints={"timeout_seconds": 10})
    record.started_at = clock()
    record.status = Status.RUNNING
    clock.advance(11)
    manager.expire()
    row = _plain(agent_panel.agent_status_row(record, 80, stream=sys.stdout))
    assert "timeout" in row, row
    assert "failed" not in row and "killed" not in row, row
    assert (agent_panel._state_position(record)
            != agent_panel.FAILED_POSITION), "a timeout is painted as a failure"


def test_every_status_the_panel_can_be_handed_has_a_word_of_its_own():
    """Section 34: queued, running, completed, failed, timed out, cancelled.
    TMT has no queue, so CREATED stands in for the pre-running state and is
    the only one of the six with no separate word -- which is honest rather
    than a gap: nothing is ever queued."""
    manager = AgentManager(clock=Clock())
    record = manager.spawn("t")
    words = {}
    for status in (Status.RUNNING, Status.COMPLETED, Status.FAILED,
                   Status.TIMED_OUT, Status.KILLED):
        record.status = status
        words[status] = agent_panel._state_word(record)
    assert len(set(words.values())) == 5, words
    assert words[Status.TIMED_OUT] == "timeout"
    assert words[Status.KILLED] == "killed"


def test_the_agents_report_states_the_contract_in_full():
    """`/agents` prints to the permanent surface with the width of the
    terminal, so it is the place the contract is actually readable -- and
    abbreviating it here to match the card would shrink the one readout that
    has room."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = spawn(manager, "investigate the auth flow",
                   constraints={"read_only": True, "timeout_seconds": 600,
                                "report": {"file_list": True, "diff": True}})
    record.started_at = clock()
    record.status = Status.RUNNING
    manager.note_violation(record.id, D.violation("write_file", ["a.py"]))
    clock.advance(60)
    said = _plain(agent_panel.agents_report(manager))
    assert "WORKERS 1/10" in said, said
    assert "READ ONLY" in said and "TIMEOUT 10:00" in said, said
    assert "(9:00 left)" in said, said
    assert "REPORTS FILES  DIFF" in said, said
    assert "BLOCKED 1 write operation blocked" in said, said


def test_a_narrow_terminal_keeps_the_identity_and_gives_up_the_rest():
    """The row gives up its parts from the right. What it must never lose is
    which agent it is."""
    clock = Clock()
    manager = AgentManager(clock=clock)
    record = spawn(manager, "t", constraints={"read_only": True,
                                              "timeout_seconds": 600})
    record.started_at = clock()
    record.status = Status.RUNNING
    for columns in (30, 40, 60, 100):
        row = _plain(agent_panel.agent_status_row(record, columns,
                                                  stream=sys.stdout))
        assert "#%s" % record.id in row, (columns, row)
        assert len(row) <= columns - 1, (columns, len(row), row)
    for columns in (30, 40, 60, 100):
        for row in agent_panel.panel_rows(manager.list(), columns, stream=sys.stdout,
                                          maximum=10):
            assert len(_plain(row)) <= columns, (columns, _plain(row))


def test_the_whole_stack_delegates_under_a_contract_from_a_real_session():
    """`TMT.main` itself: a real session loop, a real register built by it, a
    real prompt box, a real dispatcher, and the contract parsed out of the
    model's own JSON.

    Every other test here reaches for a piece. This one is the wiring: it is
    what would fail if `context["manager"]` stopped reaching `spawn_agent`, or
    if the main agent's prompt stopped documenting `constraints`, or if the
    session loop built a second register that the actions could not see.
    """
    from test_agent_cli import drive_session

    spawn = json.dumps({
        "action": "spawn_agent",
        "task": "Read agent_ui.py and say what it owns.",
        "constraints": {"read_only": True, "timeout_seconds": 600,
                        "report": {"file_list": True, "summary": True}},
        "progress": "Delegating the investigation."})
    end = json.dumps({"action": "end_conversation",
                      "message": "Delegated the investigation."})
    drawn, requests, console = drive_session(
        ["delegate a read-only investigation", "quit"], [spawn, end])

    # The action's own result went back to the model as the next user turn,
    # which is the only place the contract could have been repeated back from.
    said = "\n".join(str(message.get("content"))
                     for message in requests[-1]
                     if message.get("role") == "user")
    assert "Started background agent #1" in said, said
    assert "READ ONLY" in said, said
    assert "10:00" in said, said
    assert "1 of 10 workers running" in said, said
    assert "enforced by TMT and not by asking" in said, said
    # And the main agent's own prompt taught it the schema in the first place.
    system = str(requests[0][0]["content"])
    assert "\"read_only\"" in system, "the contract is not in the main prompt"
    assert "timeout_seconds" in system and "file_list" in system
    assert "At most 10 background agents" in system, system


def test_a_short_panel_gives_up_the_contract_before_the_activity_label():
    """A contract does not change while a delegation runs and `/agents` says
    it in full; the activity label is the only thing on a card that says the
    work is still moving."""
    manager = AgentManager(clock=Clock())
    for index in range(3):
        record = spawn(manager, "t%d" % index,
                       constraints={"read_only": True})
        record.status = Status.RUNNING
        manager.set_activity(record.id, "Reading agent_ui.py")
    records = manager.list()
    tall = [_plain(row) for row in agent_panel.panel_rows(
        records, 30, height=40, stream=sys.stdout, maximum=10)]
    assert any("RO" in row for row in tall), tall
    assert any("Reading" in row for row in tall), tall
    height = len(tall) - 3
    short = [_plain(row) for row in agent_panel.panel_rows(
        records, 30, height=height, stream=sys.stdout, maximum=10)]
    assert len(short) <= height, short
    assert not any("RO" in row for row in short), short
    assert any("Reading" in row for row in short), short
