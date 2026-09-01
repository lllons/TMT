"""The register of background agents, and nothing else.

This module knows that agents exist, what state each one is in, and who wants
to be told when that changes. It knows nothing about models, tools, terminals
or prompts, and it must not learn: it imports no `agent_ui`, no `agent_menu`,
no `agent_model` and no `agent_actions`, which is what makes it the one part of
the multi-agent system that can be tested completely -- no network, no
terminal, and no clock that actually has to pass.

Three rules shape it.

**One lock.** Everything mutable is behind a single `threading.RLock`. Five
worker threads, the session loop and a repainting UI thread all read and write
these records, and a second lock would be a second ordering to get wrong.
Reentrant because the manager's own methods call each other.

**Listeners are called outside the lock.** A listener is UI code; UI code
repaints, and repainting takes time. Holding the register's lock across a
repaint would stall every worker in the process behind the terminal. So each
transition mutates under the lock, collects what it has to announce, releases,
and only then announces it. A listener that raises is swallowed, because a
broken observer must not be able to kill the worker it was observing.

**The clock is injected, and there is no timer.** A finished agent's card
stays on screen for `RETENTION_SECONDS` and then goes. That is implemented by
stamping `finished_at` at the moment of the terminal transition and filtering
in `visible_agents()` on read -- no timer thread, nothing to cancel, nothing to
leak. It also satisfies every clause of the rule for free: the five seconds do
not reset when the panel repaints, they survive the panel being closed and
reopened, and they are real elapsed time even if the terminal never managed to
draw a single frame. Tests advance the clock instead of sleeping.

Ageing out is a fact about the *card*, never about the *record*. A worker that
has scrolled off the panel is still in `list()` and its answer is still in
`result()`, because the main AI may ask for it minutes later.
"""

import threading
import time

# Ten at once. The main AI does not count against this and neither does the
# note agent: the cap exists to bound how much work is running unattended, and
# neither of those is unattended.
#
# It was five. The number is enforced in exactly one place -- `spawn`, against
# `_active_count_locked` -- and the count is DERIVED from the records rather
# than maintained as a tally, which is what makes the invariant
# `0 <= running <= MAX_WORKERS` hold by construction: there is no increment to
# forget and no decrement to run twice, so a worker that completes, fails,
# times out and is killed in four racing threads still releases exactly one
# slot, because it releases none -- it simply stops being counted.
MAX_WORKERS = 10

# How long a finished agent's card stays on screen after it finishes.
RETENTION_SECONDS = 5.0

# An activity label is read at a glance from a card roughly twenty columns
# wide. Five words is what fits; a longer one would be truncated by the panel
# anyway, and a label cut mid-word says less than a short one.
MAX_ACTIVITY_WORDS = 5


class Status:
    """The states an agent can be in, as plain strings.

    Strings rather than an enum so a record can be compared, logged and put in
    a test assertion without importing this module's types, and so the value
    that reaches the panel is already the word the panel would have had to
    look up anyway.

    WAITING is defined for an agent blocked on something outside itself. The
    worker loop does not currently enter it -- workers do not wait on each
    other -- but the panel and the status vocabulary need one name for it
    rather than two invented later.
    """

    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    KILLED = "killed"
    FAILED = "failed"
    # A delegation that reached the deadline its contract gave it. Kept apart
    # from KILLED and from FAILED, and it is worth saying why both ways round.
    #
    # Not KILLED: nobody asked for it to stop. A kill is the main agent or the
    # user deciding the work is wrong; a timeout is the contract the work was
    # started under running out, which is a thing the delegation itself agreed
    # to and often a thing it half-finished usefully.
    #
    # Not FAILED: a worker that inspected forty files and ran out of time has
    # not crashed, and a main agent told "failed" would go looking for a bug
    # that is not there. Section 44 of the brief asks for exactly this
    # distinction and the report vocabulary in `agent_delegation` keeps it.
    TIMED_OUT = "timed_out"


# The four states nothing comes back from. Every transition into one of them
# stamps `finished_at` and sets the record's `done` event, and every one of
# them is checked against this set rather than against a list of names written
# out again at each call site.
#
# TIMED_OUT is in here, which is what releases a timed-out worker's slot: the
# capacity check counts records that are NOT terminal, so a record entering
# this set stops being counted by the same arithmetic that stopped counting a
# completed one. There is no separate release to get wrong.
TERMINAL = frozenset({Status.COMPLETED, Status.KILLED, Status.FAILED,
                      Status.TIMED_OUT})


# The event bus, as module-level strings. A listener is handed the name and
# the record, so a UI reacts to what happened instead of polling a model.
AGENT_CREATED = "agent_created"
AGENT_STARTED = "agent_started"
AGENT_ACTIVITY_CHANGED = "agent_activity_changed"
AGENT_TOKEN_UPDATE = "agent_token_update"
AGENT_COMPLETED = "agent_completed"
AGENT_KILLED = "agent_killed"
AGENT_FAILED = "agent_failed"
AGENT_TIMED_OUT = "agent_timed_out"
AGENT_VIOLATION = "agent_violation"
AGENT_REMOVED_FROM_UI = "agent_removed_from_ui"
AGENT_RESULT_AVAILABLE = "agent_result_available"
NOTE_STARTED = "note_started"
NOTE_COMPLETED = "note_completed"


class WorkerCancelled(Exception):
    """Raised inside a worker once its cancel flag is set.

    It is an exception rather than a return value because it has to unwind out
    of places that have no way to report a value -- the middle of a streamed
    response being read chunk by chunk, most of all. A worker thread that ends
    on one of these has already been marked KILLED by whoever set the flag, so
    the thread wrapper deliberately does not overwrite that with FAILED.
    """


class WorkerTimedOut(WorkerCancelled):
    """Raised inside a worker once its delegation's deadline has passed.

    A SUBCLASS of WorkerCancelled on purpose, and the reason is worth stating
    because subclassing an exception to inherit handling is usually the wrong
    instinct. Here it is exactly right: every `except WorkerCancelled: raise`
    in the worker loop and in the batch runner exists to say "this is not an
    action failing, it is the agent stopping -- do not swallow it and do not
    hand it back to the model", and that sentence is true word for word of a
    deadline. A separate exception would need every one of those clauses
    copied, and the day somebody added a fifth and forgot the copy, a
    timed-out worker would have its own timeout handed back to it as an
    action error to correct.

    What differs is only how the ending is RECORDED, and that is decided in
    one place -- the thread wrapper in `start`, which catches this first.
    """


class CapacityError(RuntimeError):
    """An eleventh worker was asked for while ten were already running.

    It carries a sentence rather than a code because the party that has to act
    on it is a language model: the orchestration action turns this into the
    action's result, and "wait for one to finish or kill one" is something a
    model can do, where a bare False is something it will retry forever.
    """


def clip_activity(text):
    """A label of at most MAX_ACTIVITY_WORDS words, whitespace-collapsed.

    Collapsing first matters as much as clipping: labels are built by joining
    an action name to a path, and either half can arrive with a newline in it
    from a model's JSON. A newline inside a live region is not a long label,
    it is a broken frame.
    """
    words = str(text or "").split()
    return " ".join(words[:MAX_ACTIVITY_WORDS])


class AgentRecord:
    """One background agent: what it was asked, and everything it has done.

    Plain attributes. Readers -- the panel above all -- read them without
    taking the lock, because a panel that had to lock the register to draw a
    frame would be able to stall a worker by being slow. Every field here is
    written as a single assignment of an immutable value, so a reader sees
    either the old one or the new one and never half of one. Where a
    consistent view across several fields is actually needed, the manager
    copies under the lock and hands out the copy.

    `conversation` is the worker's own message list and is never shared with
    another record. That is the whole of the isolation between workers: each
    one is handed its own list, and nothing in this module ever puts one
    agent's messages anywhere near another's.
    """

    def __init__(self, agent_id, number, kind, task, prompt="", model="",
                 effort="", created_at=None, constraints=None, clock=None):
        self.id = str(agent_id)
        self.number = int(number)
        self.kind = kind
        self.task = task
        # This delegation's contract: what it may do, how long it may run, and
        # what it owes when it stops. An `agent_delegation.DelegationConstraints`
        # or None, and the two mean the same thing to every reader -- None is
        # the unconstrained delegation every worker was before this existed.
        #
        # Assigned once, here, and never again. That is the whole of section
        # 39: `DelegationConstraints` has no setter, this attribute is written
        # by the constructor, and nothing in this module or any other assigns
        # to it afterwards. A contract a running worker's own model could
        # rewrite would not be a contract, and a test asserts the absence
        # rather than trusting the convention.
        #
        # It is per-record, which is the whole of section 22: one worker's
        # contract is one object hanging off one record, there is no module
        # global anywhere on this path, and the default is a shared IMMUTABLE
        # singleton, so even the fallback cannot carry state between two
        # delegations.
        self.constraints = constraints
        # The clock this record measures its own deadline against. The
        # manager's injected one, so a test advances a number instead of
        # spending ten real minutes proving a ten-minute rule -- exactly what
        # the retention window already does.
        self._clock = clock or time.monotonic
        self.prompt = prompt
        self.model = model or ""
        self.effort = effort or ""
        self.status = Status.CREATED
        # A card is never blank. The label is set before the thread exists, so
        # there is no window in which a card has been drawn and the agent has
        # not yet said what it is doing.
        self.activity = "Starting"
        self.tokens_in = 0
        self.tokens_out = 0
        # Whether each figure came from the provider or was estimated here.
        # False means the panel prints it with a leading `~`. It starts False
        # because nothing has been reported yet, and an unmarked zero would be
        # a claim that the provider said zero.
        self.tokens_in_exact = False
        self.tokens_out_exact = False
        # The output tokens of the request currently in flight, which the card
        # shows as the `+N` after the running total. It is a separate figure
        # rather than arithmetic on `tokens_out` because the total has to stay
        # a total: an agent on its fourth request has three earlier replies
        # inside `tokens_out`, and subtracting to recover the fourth would
        # need a fourth number anyway. Zero between requests.
        self.tokens_out_pending = 0
        # How much of its step budget this agent has spent, and how large that
        # budget is. This is what the per-agent bar fills with, and it is
        # deliberately NOT a completion estimate: nothing can know how close a
        # worker is to finishing, and a bar that claimed to would be inventing
        # the one figure nobody has. What it does show is real and measured --
        # the allowance used so far -- so it climbs while work happens and it
        # is full when the allowance is gone. A finished agent is drawn full
        # whatever its step count, because it is done, and that is the one
        # moment completion IS known.
        self.steps = 0
        self.max_steps = 0
        self.created_at = created_at
        self.started_at = None
        self.finished_at = None
        self.cancel = threading.Event()
        # Set by every terminal transition. `wait` blocks on these rather than
        # polling, so a caller waiting on five workers costs nothing while it
        # waits and returns the instant the last one lands.
        self.done = threading.Event()
        self.result = ""
        self.error = ""
        self.paths = ()
        # The workspace paths this agent READ, as opposed to the ones it wrote
        # (`paths`, above). Kept separately because a report has to say both --
        # "11 inspected, 0 changed" is the whole answer for an investigation --
        # and because merging them would make a read-only delegation's report
        # unable to distinguish the two at all.
        #
        # Taken from the requests the agent's own actions carried, never from
        # anything it said afterwards. Section 17.
        self.reads = ()
        # Operations this agent asked for and was refused by its contract, as
        # `agent_delegation.violation` dicts. A blocked write is frequently the
        # reason a delegation did not finish, so it is recorded rather than
        # only refused, and it reaches the main agent in the result.
        self.violations = ()
        # What this agent measurably changed, in lines, and only where both
        # halves are actually known. They come off the same `action_event`
        # detail the main loop's own counter reads, so a worker's work is
        # counted by exactly the rule the session already counts by: a patch
        # knows what it added and what it removed, a `write_file` over an
        # existing file knows only what it wrote, because what it replaced was
        # gone before anyone could count it. A missing half contributes
        # nothing rather than a zero, which would read as "removed none" when
        # the truth is "nobody knows".
        self.lines_added = 0
        self.lines_removed = 0
        # The reviewbot's declared checklist, and None for every other kind of
        # agent. It is an `agent_reviewbot.Agenda` when there is one, attached
        # by `agent_actions._review` at the moment the reviewer is spawned and
        # shared -- one object, two references -- with the turn's
        # `agent_review.ReviewState`, so the strip under the progress bar and
        # `/review` are reading the same list rather than two copies of it.
        #
        # Declared here as a plain attribute rather than constructed here, so
        # this module goes on importing nothing it does not need: a manager
        # that had to import the agenda to make a record would fail to import
        # at all on an install whose frozen module list has not caught up.
        self.agenda = None
        self.conversation = []
        # The daemon thread running it, once `start` has made one. Held so a
        # caller that needs to know the thread has actually unwound -- a test,
        # or a shutdown -- can join it. Nothing in normal operation waits on
        # it; `wait` waits on `done`, which is set before the thread ends.
        self.thread = None
        # Whether AGENT_REMOVED_FROM_UI has already been announced for this
        # record. `visible_agents` is called on every repaint, so without this
        # a finished card would announce its own removal many times a second
        # for the rest of the session.
        self._ui_removed = False

    def is_terminal(self):
        return self.status in TERMINAL

    def total_tokens(self):
        return self.tokens_in + self.tokens_out

    # --- the delegation's deadline ---------------------------------------
    #
    # Three small functions and no timer thread, which is the same shape the
    # retention window has and it is chosen for the same reasons: nothing to
    # cancel, nothing to leak, no clock that has to actually pass in a test,
    # and an answer that is correct even if nobody ever asked. What makes it
    # an ENFORCEMENT rather than a readout is where it is asked FROM --
    # `agent_worker._guard`, on the line before every dispatch, which is the
    # same boundary that carries the kill guarantee.

    def timeout_seconds(self):
        """This delegation's runtime limit in seconds, or None.

        Guarded, because a record with a malformed contract must not be able
        to raise out of the panel that is drawing it or the sweep that is
        deciding capacity. A contract that cannot be read imposes no deadline
        -- which is the safe direction here, unlike read-only: the harm of a
        missed deadline is a worker that runs long, and the harm of a spurious
        one is finished work thrown away.
        """
        try:
            seconds = self.constraints.timeout_seconds
        except Exception:
            return None
        return None if seconds is None else int(seconds)

    def deadline(self):
        """The moment this delegation must stop, on the manager's clock.

        None when it has no timeout, and None until it has STARTED. Section 11
        asks for the timer to begin when the worker is admitted to the running
        state rather than when it was registered, and `started_at` is stamped
        by `start()` at exactly that moment -- so the deadline is simply not
        yet a number for a record that has been spawned and not begun, and a
        worker cannot lose part of its time to something else being slow.
        """
        seconds = self.timeout_seconds()
        if seconds is None or self.started_at is None:
            return None
        return self.started_at + seconds

    def remaining(self, now=None):
        """Seconds left before the deadline, never below zero. None if none.

        Clamped at zero rather than going negative, because every reader of it
        is either drawing a countdown or deciding how long to block, and a
        negative wait is a wait that never happened.
        """
        end = self.deadline()
        if end is None:
            return None
        moment = self._clock() if now is None else now
        return max(0.0, end - moment)

    def expired(self, now=None):
        """Whether the deadline has passed. False for anything terminal.

        False once the record is terminal so that a finished delegation's card
        is not re-reported as timing out for the rest of the session, and so
        that the sweep is idempotent: a record can enter TIMED_OUT once and
        can never be moved again by anything, which is section 26's
        "cleanup must be idempotent" answered by construction.
        """
        if self.is_terminal():
            return False
        end = self.deadline()
        if end is None:
            return False
        moment = self._clock() if now is None else now
        return moment >= end

    def elapsed(self, now):
        """How long this agent has been running, in seconds.

        Measured from `started_at`, or from `created_at` for one that has been
        spawned and not yet started, so the figure is never None and never
        negative. A finished agent's elapsed time stops at `finished_at`: a
        card that goes on counting for the five seconds it is retained would
        be reporting time the work did not take.
        """
        start = self.started_at if self.started_at is not None else self.created_at
        if start is None:
            return 0.0
        end = self.finished_at if self.finished_at is not None else now
        if end is None:
            return 0.0
        return max(0.0, end - start)

    def __repr__(self):
        return "<AgentRecord #%s %s %s %r>" % (
            self.id, self.kind, self.status, str(self.task)[:40])


class AgentManager:
    """The register itself: spawn, start, watch, wait, kill.

    It owns no threads of its own. `start` hands a runner to a daemon thread
    and the runner reports back through these methods; nothing here polls and
    nothing here sleeps except `wait`, which blocks on the records' own events.
    """

    def __init__(self, clock=time.monotonic, max_workers=MAX_WORKERS):
        # Injected so retention can be tested by advancing a number rather
        # than by spending five real seconds per assertion. `time.monotonic`
        # rather than `time.time`, because retention is a duration and a
        # system clock that steps backwards must not resurrect a card.
        self._clock = clock
        self._max_workers = int(max_workers)
        self._lock = threading.RLock()
        self._records = []
        self._by_id = {}
        self._note = None
        # The review agent, held like the note agent and apart from the fleet
        # for the same two reasons. It does not count against the cap -- a
        # review is TMT auditing its own work rather than unattended work the
        # main agent chose to start, and refusing one because five unrelated
        # workers are busy would be refusing the wrong thing. And it does not
        # draw a card: the review has its own block in the same column, and
        # one thing drawn twice in two shapes reads as two things.
        self._review = None
        self._listeners = []
        # One counter across both kinds, so no two live records can ever share
        # an id. `kill("2")` and `inspect("2")` address a record by that
        # string and a collision there would kill the wrong agent, while the
        # cost of the shared counter is only that worker numbering can skip a
        # value when a note agent has taken one. A cosmetic gap against a
        # real mis-addressing is not a close call.
        self._counter = 0

    # --- the event bus ---------------------------------------------------

    def subscribe(self, listener):
        """Register `listener(event_name, record)`. Duplicates are ignored."""
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def unsubscribe(self, listener):
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _emit(self, events):
        """Announce a list of (name, record) pairs, outside the lock.

        Every caller mutates under the lock, collects its pairs, drops the
        lock and then calls this. A listener that raises is swallowed with
        its exception: the observer of a worker must never be able to kill it,
        and there is nowhere to report the failure to from a background thread
        anyway -- printing one would land on top of the live region.
        """
        if not events:
            return
        with self._lock:
            listeners = tuple(self._listeners)
        for name, record in events:
            for listener in listeners:
                try:
                    listener(name, record)
                except Exception:
                    pass

    # --- creating and starting -------------------------------------------

    def spawn(self, task, model=None, effort=None, prompt="", kind="worker",
              constraints=None):
        """Register a new agent in Status.CREATED and return its record.

        Raises CapacityError when the workers already running are at the cap.
        A note agent never counts against the cap and never raises: it is one
        read-only question the user asked directly, and refusing it because
        ten unrelated workers are busy would be refusing the wrong thing.
        A review agent is held apart for the same reason -- it is the quality
        gate on the task, and a gate that can be crowded out by the work it is
        gating is not a gate.

        `constraints` is this delegation's contract and is attached to the
        record and never touched again. It is per-delegation and nothing
        global: two workers spawned a millisecond apart with opposite
        contracts each hold their own object, which is section 22.

        The expiry sweep runs FIRST, before capacity is counted. That is what
        makes section 28 work: a worker whose deadline passed while nobody was
        looking is retired here, its slot stops being counted by the same
        arithmetic that stops counting a completed one, and the delegation
        being spawned starts instead of being refused by a worker that is not
        really running.
        """
        self.expire()
        with self._lock:
            if kind == "worker":
                running = self._active_count_locked()
                if running >= self._max_workers:
                    raise CapacityError(
                        "%d background agents are already running, which is the "
                        "maximum of %d. Wait for one to finish with "
                        "wait_for_agents, or kill one with kill_agent, before "
                        "spawning another."
                        % (running, self._max_workers))
            self._counter += 1
            number = self._counter
            record = AgentRecord(str(number), number, kind, task,
                                 prompt=prompt, model=model or "",
                                 effort=effort or "", created_at=self._clock(),
                                 constraints=constraints, clock=self._clock)
            self._by_id[record.id] = record
            if kind == "note":
                self._note = record
            elif kind == "review":
                self._review = record
            else:
                self._records.append(record)
        self._emit([(AGENT_CREATED, record)])
        return record

    def start(self, record, runner):
        """Run `runner(record, manager)` on a daemon thread and return it.

        Daemon, so an agent that has been killed and abandoned -- blocked on a
        socket that will never answer, which is the one case cooperative
        cancellation cannot reach -- can never hold the process open at exit.

        The thread wrapper is where an agent's ending is decided. A runner
        that returns normally completes with what it returned; one that raises
        WorkerCancelled has already been marked KILLED by whoever set the
        flag, so its status is left alone; anything else is a failure and is
        recorded as one. `complete` and `fail` both no-op on a record that has
        already reached a terminal state, so a worker killed halfway through
        its last action cannot be resurrected as COMPLETED by the value its
        runner happened to return afterwards.
        """
        with self._lock:
            if record.is_terminal():
                return None
            record.status = Status.STARTING
            record.started_at = self._clock()

        def body():
            with self._lock:
                if not record.is_terminal():
                    record.status = Status.RUNNING
            started = NOTE_STARTED if record.kind == "note" else AGENT_STARTED
            self._emit([(started, record)])
            try:
                response = runner(record, self)
            except WorkerTimedOut:
                # Caught BEFORE WorkerCancelled, which it is a subclass of.
                # The order is the whole of what tells the two endings apart,
                # and reversing it would record every timeout as a kill --
                # which is precisely the collapse section 44 forbids.
                with self._lock:
                    already = record.status == Status.TIMED_OUT
                if not already:
                    self.time_out(record.id)
            except WorkerCancelled:
                with self._lock:
                    already_killed = record.status == Status.KILLED
                if not already_killed:
                    self.kill(record.id)
            except Exception as error:
                self.fail(record.id, "%s: %s" % (type(error).__name__, error))
            else:
                self.complete(record.id, response)

        thread = threading.Thread(target=body, name="tmt-agent-%s" % record.id,
                                  daemon=True)
        with self._lock:
            record.thread = thread
        thread.start()
        return thread

    # --- terminal transitions --------------------------------------------

    def _finish_locked(self, record, status, activity):
        """Move a record into a terminal state. Called with the lock held."""
        record.status = status
        record.activity = activity
        record.finished_at = self._clock()
        record.done.set()

    def complete(self, agent_id, response):
        """Record an agent's internal response and mark it COMPLETED."""
        with self._lock:
            record = self._by_id.get(str(agent_id))
            if record is None or record.is_terminal():
                return None
            record.result = "" if response is None else str(response)
            self._finish_locked(record, Status.COMPLETED, "Completed")
        events = [(AGENT_COMPLETED, record), (AGENT_RESULT_AVAILABLE, record)]
        if record.kind == "note":
            # The note agent's answer goes to the main terminal rather than to
            # a card, so it gets its own event as well as the general one --
            # a listener drawing cards and a listener printing the answer are
            # different listeners and should not have to filter by kind.
            events.append((NOTE_COMPLETED, record))
        self._emit(events)
        return record

    def fail(self, agent_id, error):
        with self._lock:
            record = self._by_id.get(str(agent_id))
            if record is None or record.is_terminal():
                return None
            record.error = "" if error is None else str(error)
            self._finish_locked(record, Status.FAILED, "Failed")
        # A failure carries AGENT_RESULT_AVAILABLE too. Something the caller
        # was waiting for is now readable -- `result()` falls back to the
        # error sentence -- and a listener that only woke on completion would
        # leave a main AI waiting on a worker that had already stopped.
        events = [(AGENT_FAILED, record), (AGENT_RESULT_AVAILABLE, record)]
        if record.kind == "note":
            events.append((NOTE_COMPLETED, record))
        self._emit(events)
        return record

    def kill(self, agent_id):
        """Set the cancel flag and mark the agent KILLED. True if it did.

        The flag is set even for an agent that has not started, so a worker
        killed between `spawn` and `start` never runs an action at all.

        What this guarantees is bounded and worth stating exactly: after this
        returns, no further tool call will be dispatched by that worker. It
        does not stop a request already in flight -- Python threads cannot be
        terminated and a streamed response has no abort primitive -- so a
        worker blocked on a stalled socket is marked KILLED here and then
        abandoned, which is safe because the thread is a daemon.
        """
        with self._lock:
            record = self._by_id.get(str(agent_id))
            if record is None or record.is_terminal():
                return False
            record.cancel.set()
            self._finish_locked(record, Status.KILLED, "Killed")
        self._emit([(AGENT_KILLED, record)])
        return True

    def time_out(self, agent_id):
        """Stop an agent because its delegation's deadline passed. True if it did.

        The same mechanism `kill` uses -- set the cancel flag, mark it
        terminal -- because there is only one way to stop a Python thread's
        work and inventing a second would be inventing a second set of
        guarantees to get wrong. What differs is the STATUS, and that is the
        whole point: the runtime stopped this one, nobody asked it to, and the
        report has to be able to say so.

        The guarantee is the one `kill` carries, word for word: after this
        returns, no further tool call will be dispatched by that worker. A
        request already in flight still finishes arriving, because a thread
        cannot be terminated and a stream has no abort primitive. Claiming
        more than that would be a lie in the one place a lie is expensive.

        Idempotent, like every other terminal transition here: a record that
        is already terminal returns False and is left exactly as it is, so a
        worker that completes at the same instant its deadline passes ends as
        whichever landed first and never as both.
        """
        with self._lock:
            record = self._by_id.get(str(agent_id))
            if record is None or record.is_terminal():
                return False
            record.cancel.set()
            self._finish_locked(record, Status.TIMED_OUT, "Timed out")
        # AGENT_RESULT_AVAILABLE alongside, for the reason `fail` carries it:
        # something a caller was waiting on is now readable -- partial work,
        # the paths it touched, the reason it stopped -- and a listener that
        # only woke on completion would leave a main agent waiting on a worker
        # that had already stopped.
        self._emit([(AGENT_TIMED_OUT, record), (AGENT_RESULT_AVAILABLE, record)])
        return True

    def expire(self, now=None):
        """Retire every running agent whose deadline has passed. Returns them.

        The runtime half of the timeout, and it is deliberately a SWEEP rather
        than a timer thread. That is this module's existing discipline -- the
        retention window is filtered on read for the same reasons, and they
        all hold here: there is no timer to cancel, no thread to leak, no
        callback that can fire into a manager that is being torn down, and a
        test drives it by advancing a number rather than by waiting.

        It is called from every place a stale "running" would matter: before
        the capacity check in `spawn`, from `active_count`, from
        `visible_agents` on every repaint, and inside `wait`, which bounds its
        own blocking by the nearest deadline so a wait can never outlive one.
        A worker also checks its OWN deadline at each of the three
        cancellation boundaries, which is what actually stops the work -- this
        sweep is what makes the register, the capacity and the screen agree
        with that even when the worker is between boundaries.

        Nothing is swept twice: `expired()` is False for anything terminal and
        `time_out` refuses a terminal record, so the two guards agree and the
        cleanup is idempotent however many threads arrive at once.
        """
        moment = self._clock() if now is None else now
        with self._lock:
            due = [record for record in self._records
                   if record.expired(moment)]
            for slot in (self._note, self._review):
                if slot is not None and slot.expired(moment):
                    due.append(slot)
        return tuple(record for record in due if self.time_out(record.id))

    def note_violation(self, agent_id, entry):
        """Record one operation this agent's contract refused. Returns the record.

        Kept on the record rather than only refused in the loop, because a
        blocked write is often the reason a delegation did not finish its task
        -- section 41 -- and a main agent reading an incomplete result with no
        explanation for it would be being kept in the dark by TMT rather than
        by the worker.
        """
        if not isinstance(entry, dict):
            return None
        with self._lock:
            record = self._by_id.get(str(agent_id))
            if record is None:
                return None
            record.violations = tuple(record.violations) + (dict(entry),)
        self._emit([(AGENT_VIOLATION, record)])
        return record

    def kill_all(self):
        """Kill every live worker and return how many were killed.

        Workers only. The note agent is a question the user asked and is not
        part of the fleet being stopped, and the main AI is not in here at all.
        """
        with self._lock:
            ids = [record.id for record in self._records
                   if not record.is_terminal()]
        return sum(1 for agent_id in ids if self.kill(agent_id))

    def kill_note(self):
        with self._lock:
            record = self._note
        return self.kill(record.id) if record is not None else False

    def kill_review(self):
        with self._lock:
            record = self._review
        return self.kill(record.id) if record is not None else False

    # --- reading ----------------------------------------------------------

    def inspect(self, agent_id):
        with self._lock:
            return self._by_id.get(str(agent_id))

    def list(self):
        """Every worker ever spawned, in creation order, terminal or not."""
        with self._lock:
            return tuple(self._records)

    def note(self):
        with self._lock:
            return self._note

    def review(self):
        """The most recent review agent, or None. Not part of the fleet.

        One slot rather than a list, exactly as the note has: a review runs to
        completion before the action that started it returns, so two can never
        be live at once, and the finished one is only ever wanted until the
        next replaces it. Its findings live in `agent_review.ReviewState`,
        which is where anything that outlives the agent belongs.
        """
        with self._lock:
            return self._review

    def visible_agents(self, now=None):
        """The workers a panel should draw right now.

        Every live one, plus every finished one still inside its retention
        window. Filtered on read, which is the whole reason there is no timer
        thread in this module.

        The deadline sweep runs first, so a delegation whose time is up is
        drawn as TIMED OUT on the very next repaint rather than going on
        showing a bar that is still filling. Section 13's "do not allow a
        timed-out worker to remain active invisibly", answered on the one path
        the screen actually reads.
        """
        moment = self._clock() if now is None else now
        self.expire(moment)
        with self._lock:
            visible, retired = [], []
            for record in self._records:
                if not record.is_terminal():
                    visible.append(record)
                    continue
                finished = record.finished_at
                if finished is None or finished + RETENTION_SECONDS > moment:
                    visible.append(record)
                elif not record._ui_removed:
                    record._ui_removed = True
                    retired.append(record)
            visible = tuple(visible)
        self._emit([(AGENT_REMOVED_FROM_UI, record) for record in retired])
        return visible

    def _active_count_locked(self):
        return sum(1 for record in self._records if not record.is_terminal())

    def active_count(self):
        """How many workers are running, after retiring any that are out of time.

        Swept first so the figure is the truth rather than the last thing
        anybody noticed. This is the number the capacity check, the panel
        header and the corner meter all read, and a stale one would refuse an
        eleventh delegation on behalf of a worker that stopped ten minutes ago.
        """
        self.expire()
        with self._lock:
            return self._active_count_locked()

    def capacity(self):
        """`(running, maximum)` -- what the interface draws as `4/10`.

        One call rather than two, because the pair has to be consistent: two
        separate reads could straddle a worker finishing and put a count on
        screen that was never true.
        """
        self.expire()
        with self._lock:
            return self._active_count_locked(), self._max_workers

    @property
    def max_workers(self):
        return self._max_workers

    def result(self, agent_id):
        """What the agent produced, as a string.

        A failed agent has no internal response, and returning "" for one
        would tell the main AI nothing about a worker it is waiting on. The
        recorded error is this manager's own honest report of what happened,
        so it stands in -- never alongside a result, only in place of a
        missing one.
        """
        with self._lock:
            record = self._by_id.get(str(agent_id))
            if record is None:
                return ""
            return record.result or record.error or ""

    def cancelled(self, agent_id):
        """Whether this agent has been told to stop.

        An id this manager does not know is reported as cancelled. That is the
        safe direction: the only caller is a worker asking whether it may run
        another tool, and an agent the register has forgotten is not one that
        should still be writing to the workspace.
        """
        with self._lock:
            record = self._by_id.get(str(agent_id))
            return True if record is None else record.cancel.is_set()

    # --- updates from a running agent ------------------------------------

    def set_activity(self, agent_id, label):
        """Set the one-line label a card shows. Clipped to five words."""
        text = clip_activity(label)
        with self._lock:
            record = self._by_id.get(str(agent_id))
            if record is None or record.activity == text:
                # Announced only when it actually changed. A worker calls this
                # at every action and the panel repaints on every event, so
                # emitting an unchanged label would repaint the region for a
                # frame identical to the one already on screen.
                return None
            record.activity = text
        self._emit([(AGENT_ACTIVITY_CHANGED, record)])
        return record

    def apply_agenda(self, agent_id, obj):
        """Run one `review_agenda` operation for this agent, and repaint.

        The rules are all in `agent_reviewbot`; what is here is the register's
        two jobs. It finds the agent, which is the only thing that knows which
        agenda is being written to, and it emits -- without which the strip
        would hold the frame it had when the review started and catch up only
        when something else forced a repaint. A review blocks the main loop for
        minutes, so "something else" is a long time away.

        The agenda is mutated OUTSIDE the lock. That is deliberate and it is
        the same rule every other field here follows: readers do not take the
        lock, because a panel that had to lock the register to draw a frame
        could stall a worker by being slow. What the lock protects is the
        register's own maps, and an agenda belongs to exactly one agent, which
        is written to by exactly one thread -- its own.

        Everything comes back as a string, including every refusal, because
        the caller is a step loop feeding a model: a reviewer that got the
        shape wrong corrects it on its next step exactly as it does for a
        patch whose search text did not match.
        """
        with self._lock:
            record = self._by_id.get(str(agent_id))
        if record is None:
            return "There is no agenda here: this agent is not registered."
        try:
            import agent_reviewbot
        except Exception as error:
            # The frozen-module-list failure every late import here guards
            # against. A readout that cannot be built is a readout, never a
            # reason to end a review.
            return "The review agenda is unavailable: %s" % error
        if record.agenda is None:
            record.agenda = agent_reviewbot.Agenda()
        try:
            result = agent_reviewbot.apply_operation(record.agenda, obj)
        except agent_reviewbot.AgendaError as error:
            return "FAILED: %s" % error
        except Exception as error:
            return "FAILED: the agenda could not be updated (%s: %s)" % (
                type(error).__name__, error)
        operation = str((obj or {}).get("operation", "")).strip().lower()
        if operation not in agent_reviewbot.READ_ONLY_OPERATIONS:
            # Announced only when something actually moved, the rule
            # `set_activity` keeps a few methods above: a `show` changes
            # nothing, and emitting for it would repaint the region for a
            # frame identical to the one already on screen.
            self._emit([(AGENT_ACTIVITY_CHANGED, record)])
        return result

    def _apply_tokens(self, agent_id, tokens_in, tokens_out,
                      input_exact, output_exact, accumulate):
        with self._lock:
            record = self._by_id.get(str(agent_id))
            if record is None:
                return None
            if tokens_in is not None:
                record.tokens_in = (record.tokens_in + int(tokens_in)
                                    if accumulate else int(tokens_in))
            if tokens_out is not None:
                record.tokens_out = (record.tokens_out + int(tokens_out)
                                     if accumulate else int(tokens_out))
            if input_exact is not None:
                record.tokens_in_exact = bool(input_exact)
            if output_exact is not None:
                record.tokens_out_exact = bool(output_exact)
        self._emit([(AGENT_TOKEN_UPDATE, record)])
        return record

    def add_tokens(self, agent_id, tokens_in=None, tokens_out=None,
                   input_exact=None, output_exact=None):
        """Add to the running totals -- one request's cost onto a session's."""
        return self._apply_tokens(agent_id, tokens_in, tokens_out,
                                  input_exact, output_exact, True)

    def set_tokens(self, agent_id, tokens_in=None, tokens_out=None,
                   input_exact=None, output_exact=None):
        """Replace a figure outright.

        This is how an estimate is superseded. While a reply streams, the
        output figure is re-estimated from the characters seen so far and
        marked inexact; when the provider finally reports its own count, that
        count replaces the estimate and is marked exact. Adding there would
        double-count the same tokens.
        """
        return self._apply_tokens(agent_id, tokens_in, tokens_out,
                                  input_exact, output_exact, False)

    def set_pending_output(self, agent_id, tokens):
        """Set the output tokens of the request in flight -- the card's `+N`.

        Set to 0 when no request is in flight, so a finished card shows a
        total and nothing trailing it.
        """
        with self._lock:
            record = self._by_id.get(str(agent_id))
            if record is None or record.tokens_out_pending == int(tokens or 0):
                return None
            record.tokens_out_pending = int(tokens or 0)
        self._emit([(AGENT_TOKEN_UPDATE, record)])
        return record

    def _add_paths(self, record, attribute, paths):
        """Append paths to one of the record's path tuples, each once.

        Assigned as one whole tuple rather than appended to in place, which is
        the rule every field on a record follows: a reader that does not take
        the lock sees either the old tuple or the new one and never a list
        being grown under it.
        """
        seen = list(getattr(record, attribute))
        for path in paths or ():
            if isinstance(path, str) and path.strip() and path not in seen:
                seen.append(path)
        setattr(record, attribute, tuple(seen))
        return record

    def note_paths(self, agent_id, paths):
        """Record the workspace paths an agent has written to, each once."""
        with self._lock:
            record = self._by_id.get(str(agent_id))
            if record is None:
                return None
            return self._add_paths(record, "paths", paths)

    def note_reads(self, agent_id, paths):
        """Record the workspace paths an agent has READ, each once.

        The other half of a delegation's file list. Separate from `paths`
        because "11 inspected, 0 changed" is the whole answer for an
        investigation and one merged list could not say it -- and because a
        read-only delegation's changed list must be empty as a fact about what
        happened rather than as a claim anybody made.

        Never used for the conflict report: two agents reading the same file
        is not a conflict, it is a Tuesday.
        """
        with self._lock:
            record = self._by_id.get(str(agent_id))
            if record is None:
                return None
            return self._add_paths(record, "reads", paths)

    def set_steps(self, agent_id, steps=None, max_steps=None):
        """Record how much of its step budget an agent has spent.

        Set rather than added, because the worker knows its own count and a
        second tally here could only drift from it.
        """
        with self._lock:
            record = self._by_id.get(str(agent_id))
            if record is None:
                return None
            for value, name in ((steps, "steps"), (max_steps, "max_steps")):
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    setattr(record, name, value)
        self._emit([(AGENT_TOKEN_UPDATE, record)])
        return record

    def add_lines(self, agent_id, added=None, removed=None):
        """Add one action's measured line counts to an agent's totals.

        Each half is taken only when the action could count it, for the reason
        `Session.count_event` gives: a half nobody measured is left out rather
        than sent in as a zero.

        Emitted as a token update rather than a kind of its own. The event bus
        exists so the interface knows something on a card changed, and a line
        count and a token count change the same row for the same reason; a
        second event name would mean two subscriptions and two repaints to say
        one thing.
        """
        with self._lock:
            record = self._by_id.get(str(agent_id))
            if record is None:
                return None
            for value, name in ((added, "lines_added"), (removed, "lines_removed")):
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    setattr(record, name, getattr(record, name) + value)
        self._emit([(AGENT_TOKEN_UPDATE, record)])
        return record

    def totals(self, now=None):
        """What every agent together has cost and changed, as a dict.

        `(agents, tokens, lines_added, lines_removed, exact)`, keyed by those
        names. `agents` counts only the ones still running, because that is
        what a "2 agents" readout means to somebody watching; the other
        figures cover every agent this session has had, including the ones
        whose cards have aged out, because the work they did did not age out
        with the card.

        `exact` is False when any figure in the total was estimated, and the
        interface marks the whole readout `~` when it is. A total mixing one
        measured number with one guessed number is a guess.
        """
        agents = 0
        tokens = added = removed = 0
        exact = True
        with self._lock:
            records = [r for r in self._records if r.kind == "worker"]
        for record in records:
            if not record.is_terminal():
                agents += 1
            tokens += record.total_tokens()
            added += record.lines_added
            removed += record.lines_removed
            if record.total_tokens() and not (record.tokens_in_exact
                                              and record.tokens_out_exact):
                exact = False
        return {"agents": agents, "tokens": tokens, "lines_added": added,
                "lines_removed": removed, "exact": exact}

    def conflicts(self):
        """Paths more than one worker wrote, as ((path, (id, id)), ...).

        This is the whole of the concurrent-write story, deliberately. There
        is no lock manager and no transaction: `agent_file_ops` makes any
        single write atomic, and this makes it possible to tell the main AI
        "worker 2 and worker 3 both wrote agent_ui.py", which is the fact it
        needs in order to go and look. Inventing a transaction system to
        prevent it would be a much larger thing that nobody asked for.
        """
        with self._lock:
            owners = {}
            for record in self._records:
                for path in record.paths:
                    owners.setdefault(path, []).append(record.id)
        return tuple(sorted(
            (path, tuple(ids)) for path, ids in owners.items() if len(ids) > 1))

    # --- waiting ----------------------------------------------------------

    def wait(self, ids, timeout=None):
        """Block until the named workers are terminal, or `timeout` passes.

        Returns `{id: record}` for those that ARE terminal. One still running
        is simply absent, and the caller reports it as still running rather
        than being handed something that claims to be a result.

        The deadline is real time, not the injected clock. The injected clock
        exists so a retention window can be advanced without waiting; this is
        an actual block on actual events, and measuring it against a clock the
        test controls would mean a test that advanced its clock returned from a
        wait that had not happened. KeyboardInterrupt is deliberately not
        caught: Ctrl-C during a wait must abort it and return to the prompt,
        and TMT.py already catches it there.
        """
        if isinstance(ids, str):
            ids = [ids]
        wanted = [str(agent_id) for agent_id in (ids or ())]
        with self._lock:
            records = [self._by_id[key] for key in wanted if key in self._by_id]
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        while True:
            # Retire anything already out of time before deciding to block on
            # it. Without this, a wait with no timeout on a delegation whose
            # deadline passes while the wait is out would block forever on a
            # worker the contract had already ended -- the timeout enforced
            # everywhere except the one call that is actually watching for it.
            self.expire()
            pending = [record for record in records if not record.is_terminal()]
            if not pending:
                break
            remaining = None
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
            # Never block past the nearest delegation deadline. Each slice is
            # the shortest of what the caller asked for and what the earliest
            # contract allows; when it runs out the loop sweeps and comes back
            # round, so the timeout takes effect during the wait rather than
            # after it. Contracts with no timeout contribute nothing, so a
            # wait on unconstrained workers blocks exactly as it always did.
            due = [left for left in (record.remaining() for record in pending)
                   if left is not None]
            if due:
                soonest = max(0.0, min(due))
                remaining = soonest if remaining is None else min(remaining, soonest)
            if remaining is not None and remaining <= 0:
                # A deadline that has already passed: sweep rather than sleep.
                continue
            pending[0].done.wait(remaining)
        return {record.id: record for record in records if record.is_terminal()}

    def wait_all(self, timeout=None):
        """Wait on every worker ever spawned. Finished ones return at once."""
        return self.wait([record.id for record in self.list()], timeout)

    def __len__(self):
        with self._lock:
            return len(self._records)

    def __repr__(self):
        with self._lock:
            return "<AgentManager %d agents, %d active>" % (
                len(self._records), self._active_count_locked())
