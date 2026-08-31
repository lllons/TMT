"""The reviewbot's agenda: what the reviewer said it would check, and how far it got.

State and nothing else, the division `agent_plan`, `agent_review` and
`agent_verify` all keep and for the same reason: this is written from a
worker thread and drawn from a renderer thread, and an object that also knew
how to paint itself or how to reach a model would have to be right about all
of it at once.

What this is for. A review used to be a promise. The action blocked, the
column said `REVIEW 1/3 / ● Running independent review`, and for however many
minutes that took there was nothing on screen saying what the reviewer was
reading, what it had cost, or whether it was working through the job or stuck
in a loop reading the same file. The user's word for it was "just a promise
that it is actually doing what it is meant to", which is exactly right: the
runtime knew the reviewer was alive and could not say anything else about it.

So the reviewer now says what it is going to check BEFORE it checks anything,
in its own words, and ticks each item off as it finishes with it. This module
holds that list. `agent_panel.reviewbot_rows` draws it under the main progress
bar, where the per-agent bars are drawn, and `agent_worker` applies the
operations the reviewer emits.

Four things here are deliberate and are the ones to read before changing it:

- **The items are the REVIEWER's, never the runtime's.** There is no default
  agenda and no fallback list. A reviewer that declares nothing gets no
  agenda rows at all, and the strip falls back to the bar and the activity
  label -- which are both measured facts about a process that is running. A
  list this module invented would be a description of what a review is
  supposed to do, drawn beside a review that might be doing something else,
  and the user would have no way to tell the two apart. That is the "never
  fabricate" rule applied to the one display whose whole job is to say what
  is actually happening.
- **Nothing comes back out of `done` or `skipped`.** `_TRANSITIONS` is empty
  for both. A check reported as finished is evidence the user has already
  read; un-ticking it would make the row that was on screen a lie. An agenda
  whose shape turned out wrong is EXTENDED with `add`, which is visible, and
  not rewritten.
- **`create` is refused once anything has settled**, exactly as `Plan.clear`
  is and for exactly the same reason: re-declaring the agenda half way
  through is the one route round the record. A reviewer that has ticked three
  items and then declares a fresh five-item agenda has erased its own
  statement of what it was going to do, which is the whole of what this
  feature is for. Before anything settles it is free -- that is a reviewer
  correcting a list nobody has acted on yet.
- **`retire()` is total and can never refuse.** The lesson `Plan.retire` was
  written for, applied before it could be learned a second time: the session
  retires this between turns, that call is on no path that catches anything,
  and a retirement that could raise would take the session with it. Turning a
  readout off is never the dangerous direction.
"""

# --- what an item can be ---------------------------------------------------

# Not started. The reviewer said it would do this and has not yet.
PENDING = "pending"
# The one it is on. At most one at a time, for the reason the plan allows at
# most one in-progress step: two active rows is a readout of a process that
# cannot be in two places.
ACTIVE = "active"
# Checked. The reviewer looked and has whatever it found.
DONE = "done"
# Deliberately not checked, with a reason. Kept apart from `done` because
# they are different facts and collapsing them would be the review claiming
# coverage it did not have -- a reviewer that could not read a file must be
# able to say so on the row rather than having to choose between lying and
# leaving the agenda stuck.
SKIPPED = "skipped"

STATUSES = (PENDING, ACTIVE, DONE, SKIPPED)

# The two that are over. Both count as progress on the bar: the question the
# bar answers is "how much of the declared agenda is behind it", and an item
# the reviewer consciously set aside is behind it.
SETTLED = (DONE, SKIPPED)

# Where an item may go from where it is. `done` and `skipped` map to nothing,
# which is the sentence above written as a table.
_TRANSITIONS = {
    PENDING: (ACTIVE, DONE, SKIPPED),
    ACTIVE: (DONE, SKIPPED),
    DONE: (),
    SKIPPED: (),
}

# The operations the reviewer may name. `show` is here for the reason the
# plan has it: a reviewer that has lost track of its own list must be able to
# ask rather than guess, and guessing is what produces an update to the wrong
# item.
OPERATIONS = ("create", "update", "add", "show")

# The operations that only READ. Named here rather than in the register that
# consults it, so the knowledge of what an operation does stays in the one
# module that knows: `AgentManager.apply_agenda` announces a change to the
# screen, and announcing one for a `show` would repaint the region for a frame
# identical to the one already on it. That is the discipline `set_activity`
# keeps a few lines away -- it declines to emit an unchanged label -- and the
# reason is the same, because a review repaints on this bus for minutes at a
# time.
READ_ONLY_OPERATIONS = ("show",)

# How many items an agenda may hold. Twelve because the block is drawn in the
# strip under the progress bar, six rows of it at a time, and an agenda twice
# that long is a reviewer describing its method rather than its plan for this
# change. The refusal names the number, so a reviewer that hits it can
# consolidate rather than retry.
MAX_ITEMS = 12

# How wide one item's title may be. Narrower than the plan's 120 because
# these rows are drawn in a strip that is also carrying a bar, a token figure
# and an elapsed time, and a title that has to be elided on every terminal is
# a title nobody reads the end of.
MAX_TITLE = 72

# How much of a skip reason is kept. Short: it is a clause on a row, not a
# finding. Findings go in the review result.
MAX_NOTE = 160


class AgendaError(ValueError):
    """An agenda operation that could not be carried out, and why.

    A subclass of ValueError for the reason `PlanError` and `ReviewError` are:
    a caller that only knows about bad arguments still catches it, and nothing
    in this module may end a session. `agent_worker` turns it into an ordinary
    result string and the reviewer corrects itself on its next step, exactly
    as it does for a patch whose search text did not match.
    """


def normalize_status(value):
    """One of STATUSES, or an AgendaError naming what was allowed.

    Case and spacing are forgiven because a reviewer writing "Done" means
    DONE. A word nobody defined is not forgiven and is not mapped onto the
    nearest one: guessing between `done` and `skipped` would decide for the
    reviewer whether it actually checked something, which is the one judgement
    this module must never make on its behalf.
    """
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    # The two spellings a model reaches for that mean something here. Accepted
    # rather than refused because both are unambiguous, and a refusal would
    # cost the reviewer a step to learn a synonym.
    text = {"complete": DONE, "completed": DONE, "in_progress": ACTIVE,
            "started": ACTIVE, "skip": SKIPPED}.get(text, text)
    if text in STATUSES:
        return text
    raise AgendaError("'%s' is not an agenda status. Use one of: %s."
                      % (value, ", ".join(STATUSES)))


def normalize_title(value):
    """One item's title: trimmed to one line and bounded."""
    title = " ".join(str(value or "").split())
    if not title:
        raise AgendaError("An agenda item needs a title saying what you will "
                          "check.")
    if len(title) > MAX_TITLE:
        title = title[:MAX_TITLE - 1].rstrip() + "…"
    return title


def normalize_note(value):
    note = " ".join(str(value or "").split())
    if len(note) > MAX_NOTE:
        note = note[:MAX_NOTE - 1].rstrip() + "…"
    return note


# The one refusal that is needed in two places, so it is written once. A skip
# with no reason is indistinguishable from a check that was quietly dropped,
# which is the single thing the agenda exists to make visible -- and the
# reason belongs on the row as a clause, not in the review result as a
# finding.
_NEEDS_A_REASON = (
    "%s cannot be skipped without a reason. Give a \"note\" saying why you "
    "could not check it."
)


class AgendaItem:
    """One thing the reviewer said it would check.

    Its position IS its identity, exactly as a plan step's is, and for the
    same reason: a second numbering kept beside the display numbering is two
    numberings that drift, and the reviewer then updates a row it cannot see.
    Only `add` ever shifts a position and it appends, so nothing an existing
    item is called can change under the reviewer while it works.
    """

    def __init__(self, position, title, status=PENDING, note=""):
        self.position = int(position)
        self.title = normalize_title(title)
        self.status = normalize_status(status)
        self.note = normalize_note(note)
        if self.status == SKIPPED and not self.note:
            # Enforced HERE as well as on the transition, because `create` and
            # `add` both mint items with a status and would otherwise be a way
            # straight past the rule -- and a reason-less skipped row is also
            # a settled row, which means one such call would trip `create`'s
            # guard and freeze the agenda for the rest of the review.
            raise AgendaError(_NEEDS_A_REASON % ("A%d" % self.position))

    @property
    def id(self):
        return "A%d" % self.position

    @property
    def settled(self):
        return self.status in SETTLED

    def as_dict(self):
        return {"id": self.id, "title": self.title, "status": self.status,
                "note": self.note}

    def describe(self):
        text = "%s: %s [%s]" % (self.id, self.title, self.status)
        return "%s - %s" % (text, self.note) if self.note else text

    def __repr__(self):
        return "AgendaItem(%s, %r, %s)" % (self.id, self.title, self.status)


class Agenda:
    """The reviewer's declared checklist for one review.

    Held on the `AgentRecord` of the reviewbot and on the turn's
    `agent_review.ReviewState`, which are two references to one object rather
    than two objects -- the record is what the worker thread writes through
    and the review state is what outlives it, and a copy would leave the strip
    drawing one of them while `/review` printed the other.

    Emptied in place by `retire`, never rebound, for the reason every other
    state object in TMT is: the session loop hands these out before a turn
    starts, and a fresh object at the turn boundary leaves a writer pointed at
    state nothing reads.
    """

    def __init__(self, items=()):
        self._items = []
        if items:
            self.create(items)

    # --- reading ----------------------------------------------------------

    @property
    def items(self):
        return tuple(self._items)

    def __len__(self):
        return len(self._items)

    def __bool__(self):
        """Whether there is anything to draw.

        Defined explicitly for the reason `Plan.__bool__` is: a caller
        reaching for `if agenda:` means "is there anything to show", and an
        agenda that has been declared and not yet worked on is very much
        something to show.
        """
        return bool(self._items)

    def active(self):
        """The item being checked now, or None."""
        for item in self._items:
            if item.status == ACTIVE:
                return item
        return None

    def active_index(self):
        """Where the window should be centred: the active item, else the first
        unsettled one, else the last row.

        Never an index past the end of a non-empty agenda. An EMPTY one
        answers 0, which is not a row either -- there is no honest answer
        there, and 0 is the one that slices to nothing rather than raising in
        the middle of a repaint. Every caller checks the agenda has items
        first; `agenda_rows` returns before it asks.
        """
        for index, item in enumerate(self._items):
            if item.status == ACTIVE:
                return index
        for index, item in enumerate(self._items):
            if not item.settled:
                return index
        return max(0, len(self._items) - 1)

    def settled(self):
        return tuple(item for item in self._items if item.settled)

    def outstanding(self):
        return tuple(item for item in self._items if not item.settled)

    def counts(self):
        """(settled, total). The two figures the header row carries."""
        return len(self.settled()), len(self._items)

    def progress(self):
        """How full the bar is, 0-100: the share of the agenda that is behind it.

        This IS a completion figure, and it is the one place in TMT where one
        is honest rather than invented. A worker's bar shows step budget spent
        because nothing can know how close a worker is to done; here the
        reviewer itself declared how many things it was going to check and has
        itself reported which of them are finished, so `4 of 7` is a fact it
        stated rather than a guess the runtime made about it.

        An empty agenda is 0 and not 100. There is nothing declared, so
        nothing is behind it, and a full bar over an empty list would be the
        readout at its most misleading.
        """
        settled, total = self.counts()
        if total <= 0:
            return 0
        return min(100, max(0, int(round(100.0 * settled / total))))

    def is_complete(self):
        return bool(self._items) and not self.outstanding()

    def as_list(self):
        return [item.as_dict() for item in self._items]

    def describe(self):
        """The agenda as plain text, for a tool result or `/review`.

        Unpainted, for the reason `Plan.describe` is: callers that draw it
        colour it themselves, and this is the version that survives being read
        in a log, handed back to a model, or printed on a terminal with no
        colour at all.
        """
        if not self._items:
            return "No review agenda."
        rows = [item.describe() for item in self._items]
        settled, total = self.counts()
        rows.append("%d of %d checked." % (settled, total))
        return "\n".join(rows)

    def find(self, reference):
        """The item the reviewer referred to, or an AgendaError naming the range.

        Accepts 1, "1", "A1" and "a1", because all four are things a model
        writes when it is looking at a row labelled A1. A position that does
        not exist is named against the range that does, so the correction can
        be made without another call to find out what the range is.
        """
        if not self._items:
            raise AgendaError(
                "There is no agenda yet. Declare one first with "
                "{\"action\":\"review_agenda\",\"operation\":\"create\","
                "\"items\":[\"...\"]}.")
        text = str(reference).strip()
        if text[:1] in ("a", "A"):
            text = text[1:]
        try:
            position = int(text)
        except (TypeError, ValueError):
            raise AgendaError(
                "'%s' is not an agenda item. Refer to one by its number or "
                "its label, such as 2 or \"A2\"." % (reference,))
        if not 1 <= position <= len(self._items):
            raise AgendaError(
                "There is no item %s. The agenda has %d item%s, A1 to A%d."
                % (reference, len(self._items),
                   "" if len(self._items) == 1 else "s", len(self._items)))
        return self._items[position - 1]

    # --- changing it ------------------------------------------------------

    def create(self, items):
        """Declare the agenda. Refused once anything has been checked.

        The guard is `Plan.clear`'s, for the same reason and with the same
        escape: before anything settles this is a reviewer correcting a list
        nobody has acted on, which is free; after it, it is a reviewer erasing
        its own statement of what it was going to do, and the row that was on
        screen becomes a claim nobody can check. The refusal names `add`,
        which is the honest way to change an agenda in flight -- it is visible,
        and it leaves what was already said standing.
        """
        settled = self.settled()
        if settled:
            raise AgendaError(
                "This agenda cannot be replaced: %d item%s already reported "
                "(%s). Extend it with \"add\" instead, so what you already "
                "said you checked stays on the record."
                % (len(settled), "" if len(settled) == 1 else "s",
                   ", ".join(item.id for item in settled)))
        read = self._read_items(items)
        self._items = read
        self._settle()
        return self._summary("Agenda set")

    def add(self, titles, status=PENDING):
        """Append one or more items the reviewer found it also needed.

        Appends only. An insert would shift the numbering of items the
        reviewer has already been shown and may already have ticked, and the
        next update would land on a different row from the one it named --
        which is the failure positions-as-identity exists to prevent.
        """
        if isinstance(titles, (str, bytes)) or isinstance(titles, dict):
            titles = [titles]
        entries = list(titles or ())
        if not entries:
            raise AgendaError("Give at least one item to add.")
        if len(self._items) + len(entries) > MAX_ITEMS:
            raise AgendaError(
                "An agenda holds at most %d items and this would make %d. "
                "Check the related things together under one item."
                % (MAX_ITEMS, len(self._items) + len(entries)))
        for entry in entries:
            title, entry_status, note = self._read_one(entry, status or PENDING)
            self._items.append(AgendaItem(len(self._items) + 1, title,
                                          entry_status, note))
        self._settle()
        return self._summary("Added %d item%s"
                             % (len(entries), "" if len(entries) == 1 else "s"))

    def update(self, reference=None, status=None, note=None, updates=None):
        """Move one item, or several, to a new status. All of them, or none.

        `updates` is a list so a reviewer that finished two checks in one step
        can report both without spending two steps on the readout. One
        reference and one status is the common shape and is what the prompt
        teaches.

        **It is two passes, and that is the whole of what is interesting
        here.** The obvious one-pass version -- resolve an entry, move it,
        resolve the next -- leaves a batch whose third entry is bad with its
        first two already applied, `_settle` never run, and the refusal
        returned to the caller before anything repaints. So the strip goes on
        drawing the frame it had, and when something else eventually forces a
        repaint the reader is shown a checklist with an item ticked and
        nothing at all in progress. That is the multi-minute blind spot this
        readout exists to remove, reintroduced by a partial write.

        So pass one resolves and checks every entry and moves nothing, and
        pass two cannot fail. A refused update leaves no trace of having
        happened -- the same rule the completion gate follows when it refuses
        a `respond` before `execute_action` runs.

        The check is made against a SIMULATED status rather than the item's
        real one, so two entries naming the same item are validated in the
        order they would actually be applied. Without that, `[A1 -> active,
        A1 -> done]` would be checked twice against `pending` and pass, and
        then fail half way through pass two -- which is the thing being fixed.
        """
        entries = self._read_updates(reference, status, note, updates)
        planned, simulated = [], {}
        for item_reference, item_status, item_note in entries:
            item = self.find(item_reference)
            current = simulated.get(id(item), item.status)
            target = normalize_status(item_status)
            self._check(item, current, target, item_note)
            simulated[id(item)] = target
            planned.append((item, current, target, item_note))
        moved = [self._apply(item, current, target, item_note)
                 for item, current, target, item_note in planned]
        self._settle()
        return self._summary("; ".join(moved))

    def retire(self):
        """Empty the agenda because its review is over. Never refused.

        Unconditional and in place, exactly as `Plan.retire` and
        `ReviewState.retire` are, and for the reason they are: this is called
        from `Session.begin_turn` and `Session.clear`, neither of which
        catches anything, and a retirement that could raise would end the
        session on the next question. It is also why `create`'s guard is safe
        to have -- the guarded verb is the reviewer's, and the runtime's way
        out does not go through it.
        """
        self._items = []

    # --- the rules --------------------------------------------------------

    def _check(self, item, current, target, note):
        """Refuse one transition with its reason, or return having said nothing.

        Pass one of `update`. It reads `current` rather than `item.status`
        because two entries in one call may name the same item, and the second
        has to be checked against what the first will have left behind.

        A transition into the status an item already holds is allowed rather
        than refused: a reviewer that ticks A2 twice has told the truth twice,
        and a refusal there would cost it a retry for saying so.
        """
        if target == current:
            return
        allowed = _TRANSITIONS.get(current, ())
        if target not in allowed:
            if not allowed:
                raise AgendaError(
                    "%s is already %s and cannot be changed back. An agenda "
                    "records what you reported; if you have more to say about "
                    "it, say it in the review result." % (item.id, current))
            raise AgendaError(
                "%s is %s and can only become: %s."
                % (item.id, current, ", ".join(allowed)))
        if target == SKIPPED and not (note or item.note):
            raise AgendaError(_NEEDS_A_REASON % item.id)

    def _apply(self, item, current, target, note):
        """Pass two of `update`. Nothing here may fail: `_check` already ran."""
        if note:
            item.note = normalize_note(note)
        if target == current:
            return "%s was already %s" % (item.id, target)
        item.status = target
        if target == ACTIVE:
            # The item the reviewer has just named wins, and every other
            # active one goes back to PENDING. That direction is the whole of
            # the decision and it is `Plan._move`'s: the reviewer has just
            # said which row it is on, and resolving the conflict the other
            # way -- keeping whichever item happens to come first -- would
            # silently discard that statement and return a result whose two
            # halves contradict each other ("A3 is active ... Now on A1").
            #
            # PENDING and never DONE. The work was not done, and the strip
            # goes on showing it as outstanding; a quiet promotion to done
            # would be a lie on the one display the user is trusting.
            for other in self._items:
                if other is not item and other.status == ACTIVE:
                    other.status = PENDING
        return "%s is %s" % (item.id, target)

    def _settle(self):
        """Make the invariants true again after any change. Two of them.

        **At most one item is active**, and the rest go back to PENDING rather
        than being quietly marked done -- the work was not done, and demoting
        is the honest half of the same statement. `_apply` has usually settled
        this already, by demoting the others at the moment the reviewer named
        a new one, so on the `update` path this finds exactly one and leaves
        it alone. What it is here for is `create` and `add`, which mint items
        from a status the reviewer supplied and can hand over three at once.
        There the first in position order wins, because there is nothing to
        distinguish them by -- unlike an update, where the item just named is
        the reviewer's own statement about where it is.

        **Something is active while there is work left.** That is what makes
        one call per item enough: a reviewer that reports A2 done does not
        also have to say A3 has started, and the strip moves to the row it is
        actually on. A reviewer that DOES send the explicit `active` is told
        the item is already active rather than refused, so both shapes work.
        An agenda with nothing pending left is left alone rather than reaching
        backwards for something to light up.
        """
        active = [item for item in self._items if item.status == ACTIVE]
        for extra in active[1:]:
            extra.status = PENDING
        if active:
            return
        for item in self._items:
            if item.status == PENDING:
                item.status = ACTIVE
                return

    def _summary(self, prefix):
        settled, total = self.counts()
        active = self.active()
        rows = ["%s. %d of %d checked." % (prefix, settled, total)]
        if active is not None:
            rows.append("Now on %s: %s" % (active.id, active.title))
        elif total and settled == total:
            rows.append("Every item is reported. Finish with your "
                        "internal_response carrying the review result.")
        return "\n".join(rows)

    @staticmethod
    def _read_one(entry, status=PENDING):
        """(title, status, note) from a title string or an item object."""
        if isinstance(entry, dict):
            title = entry.get("title") or entry.get("item") or entry.get("text")
            return (normalize_title(title),
                    normalize_status(entry.get("status", status)),
                    normalize_note(entry.get("note", "")))
        return normalize_title(entry), normalize_status(status), ""

    def _read_items(self, items):
        if isinstance(items, (str, bytes)) or isinstance(items, dict):
            items = [items]
        entries = list(items or ())
        if not entries:
            raise AgendaError(
                "An agenda needs at least one item. Say what you are going to "
                "check, in the order you will check it.")
        if len(entries) > MAX_ITEMS:
            raise AgendaError(
                "An agenda holds at most %d items and this one has %d. Check "
                "the related things together under one item."
                % (MAX_ITEMS, len(entries)))
        read = []
        for position, entry in enumerate(entries, start=1):
            title, status, note = self._read_one(entry)
            read.append(AgendaItem(position, title, status, note))
        return read

    def _read_updates(self, reference, status, note, updates):
        """The (reference, status, note) triples one `update` call carries."""
        if updates is not None:
            if not isinstance(updates, (list, tuple)) or not updates:
                raise AgendaError(
                    "\"updates\" must be a non-empty list of objects, each "
                    "with an \"item\" and a \"status\".")
            entries = []
            for raw in updates:
                if not isinstance(raw, dict):
                    raise AgendaError(
                        "Every entry in \"updates\" must be an object with an "
                        "\"item\" and a \"status\".")
                entries.append((
                    raw.get("item", raw.get("id", raw.get("position"))),
                    raw.get("status", status),
                    raw.get("note", "")))
            return entries
        if reference is None:
            raise AgendaError(
                "Say which item to update, as \"item\": 2 or \"item\": \"A2\".")
        if status is None:
            raise AgendaError(
                "Say what to move %s to, as \"status\": one of: %s."
                % (reference, ", ".join(STATUSES)))
        return [(reference, status, note or "")]


# --- the operation the reviewer emits --------------------------------------


def apply_operation(agenda, obj):
    """Run one `review_agenda` action against an agenda. Returns its result.

    The analogue of `agent_review.parse_result`: a pure function over a dict
    the model produced, in the pure module, so the loop that dispatches it
    holds no rules of its own. Everything it can go wrong with comes back as
    an `AgendaError` carrying the sentence that corrects it.

    It is deliberately NOT reachable from `agent_actions.execute_action`. The
    agenda belongs to one reviewer's run and is applied inside the loop that
    runs it, where the record and the manager are -- see `agent_worker`. Any
    other agent emitting this verb is told so rather than silently writing
    into somebody's readout.
    """
    if agenda is None:
        raise AgendaError("There is no agenda to write to here.")
    operation = str((obj or {}).get("operation", "")).strip().lower()
    if operation not in OPERATIONS:
        raise AgendaError("'%s' is not an agenda operation. Use one of: %s."
                          % ((obj or {}).get("operation"),
                             ", ".join(OPERATIONS)))
    if operation == "show":
        return agenda.describe()
    if operation == "create":
        return agenda.create((obj.get("items") if obj.get("items") is not None
                              else obj.get("steps")))
    if operation == "add":
        return agenda.add((obj.get("items") if obj.get("items") is not None
                           else obj.get("item")),
                          status=obj.get("status") or PENDING)
    return agenda.update(
        reference=obj.get("item", obj.get("id", obj.get("position"))),
        status=obj.get("status"),
        note=obj.get("note", ""),
        updates=obj.get("updates"))
