"""What the user has already typed at a prompt, and walking back through it.

Pure state: no terminal, no keys, no drawing, no model, no I/O. `agent_menu`
owns which keystroke means "back" and how a box is painted; this owns what
going back MEANS. Same division as `agent_plan`, `agent_review` and
`agent_reviewbot`, and for the same reason -- the rules below are the part
worth testing without a terminal in the room.

**Two tiers, and the difference between them is the whole feature.**

  * PENDING lines have not run yet. They were entered while a turn was
    working and are waiting for it to finish. Submitting an edit of one
    REPLACES it where it stands, so the correction reaches the task that is
    still waiting rather than arriving as a second task after it -- and
    submitting an empty edit drops it, which is the only way to unsay
    something you have already queued.
  * RECORDED lines have already been answered. Submitting one enters it
    again, as a new line, which is what a shell history does and what anyone
    pressing Up expects.

Newest first within each tier, and pending before recorded, because the thing
somebody reaches for is the thing they said last -- and the thing they said
last, during a turn, is the one still waiting.

**A recalled line is loaded through the editor's own `insert`.** So a task
that was pasted in as a block comes back folded to the same token it was
folded to when it was typed, and `expanded()` puts it back on submit. Nothing
here knows the folding rules; it reuses the ones the field already has.

**The record is memory only and dies with the session.** A task line carries
whatever the user pasted into it, and writing that to a file in INSTALL_DIR is
a decision they have not made. `agent_memory.scrub` exists for the day
somebody wants to.
"""

# How many answered lines are kept. A ring rather than a cap that refuses:
# the oldest entry is the one nobody is reaching for, and a history that
# stopped recording would be silently wrong about what "previous" means.
MAX_ENTRIES = 200


class Entry(object):
    """One thing the cursor can be standing on.

    `handle` is the pending line's id, or None for a line that has already
    been answered. That one field is what tells "edit the task that is still
    waiting" from "enter this again", and it is an OPAQUE id rather than a
    position because a position shifts under anything that adds or removes a
    line -- which, during a turn, is the user themselves.
    """

    __slots__ = ("text", "handle")

    def __init__(self, text, handle=None):
        self.text = "" if text is None else str(text)
        self.handle = handle

    @property
    def editable(self):
        """Whether submitting this replaces it rather than adding a new one."""
        return self.handle is not None

    def __repr__(self):
        return "Entry(%r, handle=%r)" % (self.text, self.handle)


class QueuedLine(object):
    """One line waiting for the running turn to finish."""

    __slots__ = ("id", "text")

    def __init__(self, identifier, text):
        self.id = identifier
        self.text = text

    def __repr__(self):
        return "QueuedLine(%r, %r)" % (self.id, self.text)


class PendingQueue(object):
    """Lines entered while a turn was running, in the order they will run.

    One of these per session, shared: the reader thread that takes keystrokes
    during a turn adds to it, the session loop takes from it between turns,
    and the recall cursor reads it. Held in one place rather than one per turn
    because a user who queued three lines and then wants to fix the third
    should be able to, and the third does not stop existing because the turn
    that was running when they typed it has ended.

    Locked, because the two ends are different threads. It is an RLock so a
    caller already holding it -- the reader, mid-keystroke -- can ask what is
    on the queue without deadlocking on itself.
    """

    def __init__(self):
        import threading
        self._lock = threading.RLock()
        self._lines = []
        self._next = 1

    def add(self, text):
        """Queue a line. Returns the QueuedLine, or None for an empty one."""
        text = "" if text is None else str(text)
        if not text.strip():
            return None
        with self._lock:
            line = QueuedLine(self._next, text)
            self._next += 1
            self._lines.append(line)
            return line

    def pop(self):
        """The next line to run, or None when there is none."""
        with self._lock:
            if not self._lines:
                return None
            return self._lines.pop(0).text

    def take(self):
        """Every line, in order, and empty the queue."""
        with self._lock:
            lines, self._lines = self._lines, []
            return [line.text for line in lines]

    def replace(self, handle, text):
        """Rewrite a queued line in place, or drop it when the text is empty.

        In place, and that is the point: the queue is an ORDER, and a line the
        user corrected has to keep the position it had. Appending the
        correction instead would run the mistake first and the fix afterwards,
        which is the one outcome an edit must not produce.

        Returns True if the line was found, whichever of the two happened.
        """
        text = "" if text is None else str(text)
        with self._lock:
            for index, line in enumerate(self._lines):
                if line.id != handle:
                    continue
                if not text.strip():
                    del self._lines[index]
                else:
                    line.text = text
                return True
            return False

    def entries(self):
        """What the cursor can reach here, newest first."""
        with self._lock:
            return [Entry(line.text, line.id) for line in reversed(self._lines)]

    def texts(self):
        """Every queued line, in the order they run."""
        with self._lock:
            return [line.text for line in self._lines]

    def count(self):
        with self._lock:
            return len(self._lines)


class History(object):
    """The lines this session has already answered, oldest first.

    Recorded when a task is dispatched rather than when it is typed, so a line
    is in exactly one tier at a time: waiting to run, or run. Recording at the
    box instead would put a queued line in both, and an edit of it would leave
    the pre-edit text sitting in the history underneath the corrected one.

    No `__len__`, deliberately. `Session` has one, an empty session is
    therefore falsy, and the caption silently dropped the meter for the whole
    of a session's first question because of it. `count()` says the same thing
    and cannot be mistaken for "is there a history object here at all".
    """

    def __init__(self, limit=MAX_ENTRIES):
        self.limit = max(1, int(limit))
        self._lines = []

    def record(self, text):
        """Remember a line that was actually entered. Returns whether it was."""
        text = "" if text is None else str(text)
        if not text.strip():
            return False
        if self._lines and self._lines[-1] == text:
            # Two identical lines in a row are one entry, as they are in every
            # shell: pressing Up should reach the thing BEFORE the repeat, not
            # walk through the repeat twice.
            return False
        self._lines.append(text)
        if len(self._lines) > self.limit:
            del self._lines[:len(self._lines) - self.limit]
        return True

    def lines(self):
        """Every recorded line, oldest first."""
        return list(self._lines)

    def recent(self):
        """Every recorded line, newest first -- the order Up walks."""
        return list(reversed(self._lines))

    def entries(self):
        """What the cursor can reach here, newest first. None of it editable."""
        return [Entry(text) for text in reversed(self._lines)]

    def count(self):
        return len(self._lines)

    def clear(self):
        self._lines = []


def entries_for(queue, history):
    """Everything a cursor can reach, newest first, pending before recorded.

    One definition, asked by both boxes -- the one that takes keys between
    turns and the one that takes them while a turn runs. Two orderings would
    be two answers to "what did I say last", and the user is looking at one
    screen.

    Either half may be None, which is a box that has only the other.
    """
    found = []
    if queue is not None:
        found.extend(queue.entries())
    if history is not None:
        found.extend(history.entries())
    return found


def capture(editor):
    """Everything about a half-written line, so it can be put back exactly.

    Taken the moment the cursor first steps back, and restored the moment it
    returns to where it started. Without it, pressing Up to check what you
    said last would silently destroy what you were in the middle of writing --
    which is the one thing a read-only gesture must never do.
    """
    return {
        "value": getattr(editor, "value", ""),
        "cursor": getattr(editor, "cursor", 0),
        "pastes": list(getattr(editor, "pastes", ()) or ()),
        "placeholder_visible": bool(getattr(editor, "placeholder_visible", False)),
    }


def restore(editor, state):
    """Put a captured line back into an editor."""
    if not state:
        return False
    editor.value = state.get("value", "")
    editor.pastes = list(state.get("pastes", ()) or ())
    editor.cursor = max(0, min(int(state.get("cursor", 0) or 0), len(editor.value)))
    editor.placeholder_visible = bool(state.get("placeholder_visible", False))
    return True


def load(editor, text):
    """Put a recalled line into an editor, ready to be edited.

    Through `insert(..., pasted=True)` rather than by assigning `value`, so
    the field's own rule about blocks applies to a recalled line exactly as it
    applied when the line was first entered: one line goes in verbatim however
    long it is, and anything with a break in it is folded back to the token it
    was folded to at the time. `expanded()` then puts it back on submit, so
    what is recalled and what is re-entered are the same text.

    The paste list is emptied first because the recalled text is already
    whole: it came out of `expanded()`, so any token in it has been resolved
    and there is nothing left for a stale entry to resolve against.
    """
    editor.pastes = []
    editor.value = ""
    editor.cursor = 0
    editor.placeholder_visible = False
    if text:
        editor.insert(text, pasted=True)
    return True


class Recall(object):
    """A cursor walking back through what has already been entered.

    Position 0 is the line being written now. 1 is the newest entry, 2 the one
    before it, and so on. The line being written is stashed on the way out and
    put back on the way in, so the gesture costs nothing if you change your
    mind.

    `entries` is a CALLABLE, not a list, and re-asked at every step. The two
    tiers move underneath it -- a turn ends and the pending lines become
    recorded ones, or the user queues another -- and a cursor built over a
    snapshot taken when the box opened would offer lines that are no longer
    there. The entry that was actually loaded is remembered separately, so
    `current()` answers about the text on screen rather than about whatever is
    at that position now.
    """

    def __init__(self, entries=None):
        self._entries = entries
        self.position = 0
        self._draft = None
        self._current = None

    def snapshot(self):
        """The entries as they stand, newest first. Never raises."""
        if self._entries is None:
            return []
        try:
            found = self._entries()
        except Exception:
            # A recall that cannot read the queue offers nothing, which leaves
            # the box exactly as it was. Every other guard on this surface
            # fails in that direction and this one is no different: the worst
            # outcome available here is a keystroke that does nothing, and the
            # alternative is an exception raised out of a reader thread.
            return []
        return [entry for entry in (found or []) if isinstance(entry, Entry)]

    @property
    def active(self):
        """Whether the box is showing something recalled rather than a draft."""
        return self.position > 0

    def current(self):
        """The entry that was loaded, or None when the draft is on screen."""
        return self._current if self.position > 0 else None

    def back(self, editor):
        """Step one entry further back. Returns whether anything moved."""
        entries = self.snapshot()
        if not entries or self.position >= len(entries):
            return False
        if self.position == 0:
            self._draft = capture(editor)
        self.position += 1
        self._current = entries[self.position - 1]
        load(editor, self._current.text)
        return True

    def forward(self, editor):
        """Step one entry towards the draft. Returns whether anything moved."""
        if self.position <= 0:
            return False
        self.position -= 1
        if self.position == 0:
            self._current = None
            restore(editor, self._draft)
            self._draft = None
            return True
        entries = self.snapshot()
        if self.position > len(entries):
            # The queue shrank underneath the cursor. Clamped to the oldest
            # entry that still exists rather than left pointing past the end,
            # because a cursor off the end of the list would answer `current()`
            # with a line nobody can see.
            self.position = len(entries)
        if self.position <= 0:
            self._current = None
            restore(editor, self._draft)
            self._draft = None
            return True
        self._current = entries[self.position - 1]
        load(editor, self._current.text)
        return True

    def reset(self):
        """Forget where the cursor was, and forget the stashed draft.

        Called when a line is submitted or abandoned. Total and unguarded, the
        way `Plan.retire` is: turning a cursor off is never the dangerous
        direction, so there is nothing here to refuse.
        """
        self.position = 0
        self._draft = None
        self._current = None
