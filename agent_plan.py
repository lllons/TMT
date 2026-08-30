"""The plan a turn works to: steps, their statuses, and the rules between them.

State and nothing else. No UI, no model, no tools and no I/O -- the same
division `agent_manager` keeps from `agent_panel`, and for the same reason:
the plan is read from two threads and drawn in two places, and a state object
that also knew how to paint itself would have to be right about both.

A plan is the turn's execution contract. The model writes it before starting
substantial work, moves through it as the work actually happens, and cannot
end the turn while a step is still outstanding -- that last part is enforced
by the loop in TMT.py, using `refusal()` below, rather than by asking the
model nicely. `agent_panel.plan_rows` draws it, `agent_actions` dispatches the
`plan` action into it, and `agent_session.Session` owns one per turn.

Three things here are deliberate and are the ones to read before changing it:

- **Positions are the identity.** Step 1 is S1 is `steps[0]`. Ids are not a
  second numbering to keep in step with the first, because two numberings
  drift and the model then updates a step it cannot see. Only `add` and
  `remove` can shift a position, and both say in their result exactly what the
  numbering became, so the model is never guessing about a change it did not
  just make.
- **At most one step is in progress.** Making S3 active demotes S2 to pending
  rather than completing it: the work was not done, and a plan that quietly
  marked it done would be lying in the one place the user is trusting it.
- **Nothing moves backward out of completed.** A finished step stays finished.
  A plan whose shape turned out to be wrong is REPLACED -- `create` again --
  rather than unwound a step at a time, because a revision is one decision and
  should be one call.
"""

# The three statuses a step moves through, and the fourth it can be parked in.
#
# `blocked` is not required by anything here and nothing produces it on its
# own; it exists because a step that genuinely cannot proceed is a real thing
# to say, and saying it is better than a step sitting in `in_progress` for the
# rest of the turn while nobody works on it. It counts as outstanding, so it
# does not let the turn end.
PENDING = "pending"
IN_PROGRESS = "in_progress"
COMPLETED = "completed"
BLOCKED = "blocked"

STATUSES = (PENDING, IN_PROGRESS, COMPLETED, BLOCKED)

# What a step may become, from where. Read as: from this status, these are
# reachable. Completed is absent as a source on purpose -- see the module
# docstring.
_TRANSITIONS = {
    PENDING: (IN_PROGRESS, COMPLETED, BLOCKED),
    IN_PROGRESS: (COMPLETED, PENDING, BLOCKED),
    BLOCKED: (PENDING, IN_PROGRESS, COMPLETED),
    COMPLETED: (),
}

# Ceilings, both of them presentation limits rather than arithmetic ones. A
# plan is the high-level shape of a task -- the milestones a user would
# recognise, not every tool call -- and twenty steps is already more than a
# column can show. A title longer than this is a paragraph, and the panel
# would elide it to nothing useful anyway.
MAX_STEPS = 20
MAX_TITLE = 120

# The operations the `plan` action understands.
OPERATIONS = ("create", "update", "add", "remove", "clear", "show")


class PlanError(ValueError):
    """A plan operation the model got wrong, carrying the sentence to send it.

    A subclass of ValueError so a caller that only knows about bad arguments
    still catches it, and so nothing here can end a session: `agent_actions`
    turns it into an ordinary action result and the model corrects itself the
    way it corrects any other mistake.
    """


class PlanStep:
    """One step: where it sits, what it is called, and how it is going."""

    __slots__ = ("position", "title", "status")

    def __init__(self, position, title, status=PENDING):
        self.position = int(position)
        self.title = str(title)
        self.status = str(status)

    @property
    def id(self):
        """The label the user and the model both use for this step."""
        return "S%d" % self.position

    @property
    def done(self):
        return self.status == COMPLETED

    def as_dict(self):
        return {"id": self.id, "position": self.position,
                "title": self.title, "status": self.status}

    def __repr__(self):
        return "PlanStep(%s, %r, %s)" % (self.id, self.title, self.status)


def normalize_status(value):
    """One of STATUSES, or a PlanError naming what was actually allowed.

    Case and spacing are forgiven, and so is the hyphen a model reaches for
    when it has seen "in-progress" written that way. What is not forgiven is
    a status nobody defined: silently mapping it onto the nearest one would
    put a step into a state the model did not ask for and did not know about.
    """
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in STATUSES:
        return text
    raise PlanError("'%s' is not a status. Use one of: %s."
                    % (value, ", ".join(STATUSES)))


def normalize_title(value):
    """A step title, or a PlanError. Trimmed, single-line and bounded."""
    title = " ".join(str(value or "").split())
    if not title:
        raise PlanError("A step needs a title. Give each step a short phrase "
                        "naming the work, such as \"Run the test suite\".")
    if len(title) > MAX_TITLE:
        title = title[:MAX_TITLE - 1].rstrip() + "…"
    return title


class Plan:
    """The steps of one task, and the only thing allowed to change them.

    Empty until something calls `create`. An empty plan is not a broken plan:
    most questions do not need one, and an empty plan gates nothing, so a
    conversational turn is exactly the turn it was before this existed.
    """

    def __init__(self, steps=()):
        self._steps = []
        if steps:
            self.create(steps)

    # --- reading ----------------------------------------------------------

    @property
    def steps(self):
        return tuple(self._steps)

    def __len__(self):
        return len(self._steps)

    def __bool__(self):
        """Whether there is a plan at all.

        Defined explicitly because `__len__` is defined: without this an empty
        plan would be falsy, and `if plan:` would then mean "if the plan has
        steps" in some places and "if there is a plan object" in others.
        `agent_session.Session` has been bitten by exactly this before, which
        is why the rule here is written down rather than left to be inferred.
        """
        return bool(self._steps)

    def active(self):
        """The step being worked on, or None."""
        for step in self._steps:
            if step.status == IN_PROGRESS:
                return step
        return None

    def outstanding(self):
        """Every step that is not finished, in order."""
        return tuple(step for step in self._steps if not step.done)

    def completed(self):
        return tuple(step for step in self._steps if step.done)

    def is_complete(self):
        """Whether the turn may end.

        An empty plan is complete. That is not a loophole -- it is the answer
        to "may this turn end", and a turn nobody made a plan for was never
        gated in the first place.
        """
        return not self.outstanding()

    def as_list(self):
        return [step.as_dict() for step in self._steps]

    def describe(self):
        """The plan as plain text, for a tool result or a slash command.

        Unpainted. Callers that draw it colour it themselves; this is the
        version that has to survive being read in a log, handed back to the
        model, or printed on a terminal that has no colour at all.
        """
        if not self._steps:
            return "No plan."
        rows = ["%s: %s [%s]" % (step.id, step.title, step.status)
                for step in self._steps]
        rows.append("%d of %d complete." % (len(self.completed()), len(self._steps)))
        return "\n".join(rows)

    # --- finding a step ---------------------------------------------------

    def find(self, reference):
        """The step a model referred to, or a PlanError explaining the range.

        Accepts 1, "1", "S1" and "s1", because all four are things a model
        writes when it is looking at a column headed S1. A position that does
        not exist is named against the range that does, so the correction is
        one the model can make without another call.
        """
        if not self._steps:
            raise PlanError("There is no plan yet. Create one first with "
                            "{\"action\":\"plan\",\"operation\":\"create\","
                            "\"steps\":[\"...\"]}.")
        text = str(reference).strip()
        if text[:1] in ("s", "S"):
            text = text[1:]
        try:
            position = int(text)
        except (TypeError, ValueError):
            raise PlanError("'%s' is not a step. Refer to a step by its "
                            "number or its label, such as 2 or \"S2\"."
                            % (reference,))
        if not 1 <= position <= len(self._steps):
            raise PlanError("There is no step %s. The plan has %d step%s, S1 "
                            "to S%d." % (reference, len(self._steps),
                                         "" if len(self._steps) == 1 else "s",
                                         len(self._steps)))
        return self._steps[position - 1]

    # --- changing it ------------------------------------------------------

    def create(self, steps):
        """Replace the plan with these steps. The revision path, and the only
        way a finished step is ever unfinished again.

        Entries may be plain titles or objects carrying a title and a status,
        so a revision that keeps the progress already made can say so instead
        of starting the whole task again.
        """
        entries = self._read_steps(steps)
        had = len(self._steps)
        self._steps = [PlanStep(index + 1, title, status)
                       for index, (title, status) in enumerate(entries)]
        self._settle()
        # A sentence first, then the listing. The transcript shows an action's
        # first line and nothing else, and "S1: Inspect repository [pending]"
        # on its own does not say that a plan was made.
        opened = "Plan %s with %d step%s." % (
            "replaced" if had else "created", len(self._steps),
            "" if len(self._steps) == 1 else "s")
        return "%s\n%s" % (opened, self.describe())

    def add(self, title, after=None):
        """Insert one step, at the end or after a step that already exists."""
        if len(self._steps) >= MAX_STEPS:
            raise PlanError("A plan holds at most %d steps and this one is "
                            "full. Replace it with a shorter one using "
                            "\"create\"." % MAX_STEPS)
        step = PlanStep(0, normalize_title(title))
        index = len(self._steps) if after is None else self.find(after).position
        self._steps.insert(index, step)
        self._renumber()
        self._settle()
        return "Added %s: %s.\n%s" % (step.id, step.title, self._numbering())

    def remove(self, reference):
        """Drop one step. Everything after it moves up, and the result says so."""
        step = self.find(reference)
        self._steps.remove(step)
        self._renumber()
        self._settle()
        if not self._steps:
            return "Removed the last step; there is no plan now."
        return "Removed %s (%s).\n%s" % (step.id, step.title, self._numbering())

    def update(self, reference=None, status=None, title=None, updates=None):
        """Change one step, or several in one call.

        Applied all-or-nothing. A batch that failed half way would leave a
        plan nobody wrote -- some of it moved, some of it not, and the model
        told only about the part that failed -- so the statuses are put back
        if anything in the batch is refused.
        """
        batch = self._read_updates(reference, status, title, updates)
        snapshot = [(step, step.status, step.title) for step in self._steps]
        try:
            changed = [self._apply_one(*entry) for entry in batch]
        except PlanError:
            for step, was_status, was_title in snapshot:
                step.status, step.title = was_status, was_title
            raise
        self._settle()
        return "\n".join(changed + [self._progress_line()])

    def clear(self):
        """Drop the plan entirely. The turn is no longer gated by anything.

        Refused once any step has been COMPLETED, and that refusal is the one
        place the gate could otherwise be walked around. Every other route out
        of an unfinished plan is a visible statement about the work -- finish
        the steps, or `create` a plan that describes the task properly, both of
        which stay on screen and both of which the user can read. Dropping a
        plan that is half done is the one route that says nothing at all, and
        it would turn the contract into a formality.

        A plan with nothing completed is a different thing: the task turned out
        not to need one, no work has been claimed against it, and dropping it
        costs nobody anything. That is what this is for.
        """
        done = self.completed()
        if done:
            raise PlanError(
                "This plan cannot be cleared: %d step%s already completed (%s). "
                "Finish the outstanding steps, or replace the plan with "
                "\"create\" if the task turned out to be a different shape. "
                "Clearing is only for a plan nothing has been done against."
                % (len(done), "" if len(done) == 1 else "s",
                   ", ".join(step.id for step in done)))
        had = len(self._steps)
        self._steps = []
        return "Cleared the plan (%d step%s, none completed)." % (
            had, "" if had == 1 else "s")

    # --- the rules --------------------------------------------------------

    def _apply_one(self, step, status, title):
        """One step's change, already looked up and validated for shape."""
        said = []
        if title is not None:
            was, step.title = step.title, title
            said.append("%s renamed from \"%s\" to \"%s\"." % (step.id, was, title))
        if status is not None:
            said.append(self._move(step, status))
        if not said:
            raise PlanError("%s: say what to change -- a \"status\", a "
                            "\"title\", or both." % step.id)
        return " ".join(said)

    def _move(self, step, status):
        """Move one step's status, or refuse with the reason."""
        if step.status == status:
            if status == COMPLETED:
                # Named as a refusal rather than shrugged off. A model
                # completing a step twice has usually lost track of which step
                # it is on, and being told so is more use than a silent
                # success that leaves it just as lost.
                raise PlanError("%s (%s) is already completed. Nothing to do "
                                "-- move to the next outstanding step."
                                % (step.id, step.title))
            return "%s is already %s." % (step.id, status)
        if status not in _TRANSITIONS[step.status]:
            raise PlanError(
                "%s is %s and cannot become %s. A completed step stays "
                "completed; if the plan itself was wrong, replace it with "
                "\"create\"." % (step.id, step.status, status))
        was, step.status = step.status, status
        if status == IN_PROGRESS:
            # One active step, and the others go back to pending rather than
            # to completed. Demoting is visible and honest; the work was not
            # done, and the panel goes on showing it as outstanding.
            for other in self._steps:
                if other is not step and other.status == IN_PROGRESS:
                    other.status = PENDING
        return "%s (%s) %s -> %s." % (step.id, step.title, was, status)

    def _settle(self):
        """Make the invariants true again after any change.

        Two of them, and both exist so the panel can be read at a glance:
        exactly one step is active while there is work left, and the active
        one is the first that is not finished.

        The promotion is what makes `update` match the way people actually
        work: completing S1 makes S2 active without a second call, and a model
        that sends that second call anyway is told S2 is already in progress
        rather than being refused.
        """
        if not self._steps:
            return
        active = [step for step in self._steps if step.status == IN_PROGRESS]
        for extra in active[1:]:
            extra.status = PENDING
        if active:
            return
        for step in self._steps:
            if step.status == PENDING:
                step.status = IN_PROGRESS
                return

    def _renumber(self):
        for index, step in enumerate(self._steps):
            step.position = index + 1

    def _numbering(self):
        return "The plan is now: " + ", ".join(
            "%s %s" % (step.id, step.title) for step in self._steps) + "."

    def _progress_line(self):
        remaining = self.outstanding()
        if not remaining:
            return "Every step is complete."
        return "%d of %d complete; still outstanding: %s." % (
            len(self.completed()), len(self._steps),
            ", ".join("%s %s" % (step.id, step.title) for step in remaining))

    # --- reading what the model sent --------------------------------------

    @staticmethod
    def _read_steps(steps):
        """[(title, status)] from what a model put in "steps"."""
        if isinstance(steps, str) or not isinstance(steps, (list, tuple)):
            raise PlanError("\"steps\" must be a list of step titles, such as "
                            "[\"Inspect the repository\", \"Run the tests\"].")
        if not steps:
            raise PlanError("A plan needs at least one step. To drop the plan "
                            "instead, use {\"action\":\"plan\","
                            "\"operation\":\"clear\"}.")
        if len(steps) > MAX_STEPS:
            raise PlanError("A plan holds at most %d steps; that one has %d. "
                            "A plan is the milestones the user would "
                            "recognise, not every tool call."
                            % (MAX_STEPS, len(steps)))
        entries = []
        for raw in steps:
            if isinstance(raw, dict):
                title = normalize_title(raw.get("title", raw.get("step", "")))
                status = normalize_status(raw.get("status", PENDING))
            else:
                title, status = normalize_title(raw), PENDING
            entries.append((title, status))
        return entries

    def _read_updates(self, reference, status, title, updates):
        """[(step, status or None, title or None)] for one or many changes.

        Every entry is resolved and validated here, before anything is
        applied, so a batch with a bad step number in the middle of it changes
        nothing at all.
        """
        if updates is not None:
            if isinstance(updates, dict):
                updates = [updates]
            if not isinstance(updates, (list, tuple)) or not updates:
                raise PlanError("\"steps\" for an update must be a non-empty "
                                "list of {\"step\":N,\"status\":\"...\"} "
                                "objects.")
            batch = []
            for entry in updates:
                if not isinstance(entry, dict):
                    raise PlanError("Each update must be an object such as "
                                    "{\"step\":2,\"status\":\"completed\"}.")
                batch.append(self._read_one(
                    entry.get("step", entry.get("id")),
                    entry.get("status"), entry.get("title")))
            return batch
        return [self._read_one(reference, status, title)]

    def _read_one(self, reference, status, title):
        if reference is None:
            raise PlanError("Say which step to update, as \"step\": 2 or "
                            "\"step\": \"S2\".")
        step = self.find(reference)
        return (step,
                None if status is None else normalize_status(status),
                None if title is None else normalize_title(title))


# --- what the runtime enforces --------------------------------------------

# The sentence the loop hands back when a final answer is refused. It names
# the steps that are outstanding, because "finish the plan" is not actionable
# and "S3 Run the tests is still in progress" is.
_INCOMPLETE = (
    "BLOCKED: you cannot finish yet. The plan you made is the contract for "
    "this task, and %s still outstanding:\n%s\n"
    "Do the work for the next step, then mark it completed with "
    "{\"action\":\"plan\",\"operation\":\"update\",\"step\":N,"
    "\"status\":\"completed\"}. If a step turned out not to be needed, say so "
    "in its title and complete it, or replace the plan with \"create\". Do "
    "not respond again until every step is completed."
)


def refusal(plan, action):
    """Why a terminal action may not run yet, or "" when it may.

    This is the enforcement the whole feature turns on, and it is here rather
    than in the prompt on purpose: a rule the model is merely told about is a
    rule the model can decide it has satisfied. The loop asks this before it
    lets `respond` or `done` end the turn, and a non-empty answer is handed
    back to the model as its next input -- so a model that has decided it is
    finished simply finds itself still working.

    It is bounded by the loop, not here. The turn's own round budget and the
    identical-reply circuit breaker both still apply, so a model that will not
    finish its plan ends the turn the way any other stuck model does, with the
    reason recorded and the outstanding steps still on screen. Nothing here
    can loop forever and nothing here can be argued with.
    """
    if action not in ("respond", "done"):
        return ""
    if plan is None or not plan or plan.is_complete():
        return ""
    remaining = plan.outstanding()
    listed = "\n".join("  %s: %s [%s]" % (step.id, step.title, step.status)
                       for step in remaining)
    return _INCOMPLETE % ("1 step is" if len(remaining) == 1
                          else "%d steps are" % len(remaining), listed)
