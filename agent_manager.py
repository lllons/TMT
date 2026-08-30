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

# Five at once. The main AI does not count against this and neither does the
# note agent: the cap exists to bound how much work is running unattended, and
# neither of those is unattended.
MAX_WORKERS = 5

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


# The three states nothing comes back from. Every transition into one of them
# stamps `finished_at` and sets the record's `done` event, and every one of
# them is checked against this set rather than against a list of names written
# out again at each call site.
TERMINAL = frozenset({Status.COMPLETED, Status.KILLED, Status.FAILED})


# The event bus, as module-level strings. A listener is handed the name and
# the record, so a UI reacts to what happened instead of polling a model.
AGENT_CREATED = "agent_created"
AGENT_STARTED = "agent_started"
AGENT_ACTIVITY_CHANGED = "agent_activity_changed"
AGENT_TOKEN_UPDATE = "agent_token_update"
AGENT_COMPLETED = "agent_completed"
AGENT_KILLED = "agent_killed"
AGENT_FAILED = "agent_failed"
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


class CapacityError(RuntimeError):
    """A sixth worker was asked for while five were already running.

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
                 effort="", created_at=None):
        self.id = str(agent_id)
        self.number = int(number)
        self.kind = kind
        self.task = task
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

    def spawn(self, task, model=None, effort=None, prompt="", kind="worker"):
        """Register a new agent in Status.CREATED and return its record.

        Raises CapacityError when the workers already running are at the cap.
        A note agent never counts against the cap and never raises: it is one
        read-only question the user asked directly, and refusing it because
        five unrelated workers are busy would be refusing the wrong thing.
        """
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
                                 effort=effort or "", created_at=self._clock())
            self._by_id[record.id] = record
            if kind == "note":
                self._note = record
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

    def visible_agents(self, now=None):
        """The workers a panel should draw right now.

        Every live one, plus every finished one still inside its retention
        window. Filtered on read, which is the whole reason there is no timer
        thread in this module.
        """
        moment = self._clock() if now is None else now
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
        with self._lock:
            return self._active_count_locked()

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

    def note_paths(self, agent_id, paths):
        """Record the workspace paths an agent has written to, each once."""
        with self._lock:
            record = self._by_id.get(str(agent_id))
            if record is None:
                return None
            seen = list(record.paths)
            for path in paths or ():
                if isinstance(path, str) and path.strip() and path not in seen:
                    seen.append(path)
            record.paths = tuple(seen)
            return record

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
        for record in records:
            if record.is_terminal():
                continue
            remaining = None
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
            record.done.wait(remaining)
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
