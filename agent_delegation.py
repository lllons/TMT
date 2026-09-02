"""The contract a delegation runs under: permission, time, and what it owes.

`spawn_agent` used to take a task and nothing else, which made a background
agent fire-and-forget: it could write anything, run for as long as its step
budget lasted, and report whatever sentence it felt like writing. This module
is the other half of that -- the *contract* a delegation is handed with its
task, and the one place that says what a contract means.

It is pure state, in the division `agent_plan`, `agent_review`, `agent_verify`
and `agent_reviewbot` already use: no I/O, no threads, no subprocess, no model,
no terminal, and no import of anything in TMT that has any of those. What
enforces the contract is `agent_worker` (before every dispatch) and
`agent_actions.execute_action` (again, at the dispatcher); what *decides* what
the contract permits is here, once, so the two layers cannot drift apart.

Three decisions shape it.

**Read-only is a WHITELIST.** `READ_ONLY_ACTIONS` names every verb a read-only
delegation may use, and everything else is refused -- including every verb
added to TMT after this was written. A blacklist would silently admit the next
mutation verb somebody registers, and the person adding it is not the person
who wrote the blacklist. That is `agent_worker.NOTE_ACTIONS`'s reasoning, and
this is the same reasoning applied to a delegation the main agent writes rather
than to a question the user asks.

**Constraints are immutable.** A `DelegationConstraints` has no setter, no
`update`, and no method that takes a name and a value. The only way to make one
is `parse()`, which reads the spawning model's object once, and the only thing
that happens to one afterwards is that it is read. A contract a running worker
could have rewritten under it is not a contract, and a method that could be
handed `("read_only", False)` is a method a later edit can wire model output
into -- the same reasoning that keeps `agent_review.settle` taking only a
`ReviewResult` and `agent_capabilities.Capabilities` free of a setter.

**Report requirements are not permissions.** They are separate objects and
separate words throughout, because they answer different questions: one is what
the worker may do, the other is what TMT must collect about it afterwards. A
report requirement can never widen or narrow what a worker is allowed, and
`refusal()` never reads one.
"""


# --- what a read-only delegation may do ------------------------------------

# Every verb a read-only delegation is allowed, and nothing else.
#
# A WHITELIST, for the reason `agent_worker.NOTE_ACTIONS` is one: a blacklist
# of writing verbs would silently admit every verb registered after it was
# written, and the failure would be invisible -- a delegation the user was told
# was read-only, quietly writing through a verb nobody had thought to list.
#
# Spelled out here rather than derived from `agent_config.MUTATING_ACTIONS`,
# and that is deliberate. MUTATING_ACTIONS answers a different question -- "does
# this invalidate the cached system prompt?" -- and it is correctly narrower:
# `bash` runs a command that can write anything, `git_commit` changes the
# repository, `open_app` launches an application on the user's desktop, and
# `remember` writes to TMT's own store. None of those are in MUTATING_ACTIONS
# and all four must be refused here. Deriving one from the other would tie a
# security boundary to a cache-invalidation set, and the day somebody narrowed
# the cache set for a good reason they would widen this without noticing.
#
# `bash` is the sharpest illustration there is of why this set is a whitelist
# and not a hint. It is the widest verb in TMT, it is what replaced `run_file`
# and `run_python`, and adding it here would make "read-only" mean a worker
# that may run a build -- which is not read-only in any sense a user would
# recognise. A read-only worker is also the one caller that cannot answer the
# approval question the command policy asks, so the verb would be unusable
# even where it was not unsafe. Its absence from this set is the second of the
# three refusals it gets; `agent_worker.WORKER_FORBIDDEN` is the first and no
# background prompt teaching it is the third.
#
# There is a test that this set and MUTATING_ACTIONS are disjoint, and another
# naming each of those four verbs explicitly.
#
# Named separately from `agent_worker.NOTE_ACTIONS` and
# `agent_worker.REVIEW_ACTIONS` although it currently holds the same verbs as
# the first, for the reason those two are named separately from each other: the
# three lists are read-only for three different reasons, and a shared name
# would make a change to one silently a change to the others.
READ_ONLY_ACTIONS = frozenset({
    # Reading the workspace. `grep` reads file contents and `glob` reads file
    # names; neither writes, and they replaced `search_files` and `find_text`,
    # which are gone from TMT entirely. A verb absent from this set is refused,
    # so both new names have to be named here for a read-only delegation to be
    # able to search at all.
    "list_files", "read_file", "read_lines", "grep", "glob",
    "find_symbol", "tree", "code_map", "related_tests",
    # Reading what TMT remembers. `recall` reads; `remember` writes, and is
    # absent for that reason.
    "recall",
    # Reading the web. Both are read-only in the sense this set means: they
    # touch nothing in the workspace and nothing on this machine, so a
    # read-only delegation may use them exactly as it may use `grep`.
    #
    # `bash` is absent from this set because a command can write; these two
    # cannot, and the distinction is worth being explicit about because both
    # reach the network. Reaching the network is not the property this set is
    # about -- writing is. What bounds the network side is in `agent_web`:
    # https only, no private addresses, a timeout, a size cap, and a refusal
    # to put this machine's own credentials into an outbound query.
    "web_search", "web_fetch",
    # Several calls in one action. The verb itself writes nothing: what it
    # does is dispatch its calls, and every one of them comes back through
    # `agent_actions.execute_action`, which asks `refusal` about EACH under
    # this same contract -- and the worker loop has asked about each before
    # dispatch as well. A read-only delegation may therefore read five files
    # in one action, and a write listed inside one is refused by the same
    # sentence that refuses it on its own.
    "multi_tool",
    # Reading git. The three inspecting verbs and no more: `git_commit` and
    # `git_push` change the repository, and `git_push` is refused to every
    # background agent anyway.
    "git_status", "git_diff", "git_identity",
    # Saying something, and finishing. `send_message` reaches nobody from a
    # background agent -- the loop answers it with a sentence saying so -- but
    # it changes nothing, so refusing it here would be refusing the wrong
    # thing and would give a read-only worker a confusing second reason for a
    # refusal it already has.
    "send_message", "internal_response",
})

# The verbs most likely to be reached for by a model that has been told it may
# not write a file, and the reason each is refused. Used to make the refusal
# say something a model can act on rather than "not permitted": a worker told
# only that `bash` is unavailable reasonably tries `open_app` next.
_WHY_REFUSED = {
    "bash": "it runs a command, and a command can write anything -- and the "
            "approval a command sometimes needs has to be answered by a human "
            "at the terminal, which a background agent does not have",
    "open_app": "it launches an application outside this workspace",
    "git_commit": "committing changes the repository",
    "remember": "it writes to TMT's own memory store",
    "verify": "it runs this project's build and test commands, which write "
              "caches, lockfiles and build artifacts",
    "replace_across": "it rewrites files across the workspace when applied",
}

_DEFAULT_WHY = "it can make a persistent change to the workspace"


# --- how long a delegation may run -----------------------------------------

# The ceiling on a delegation's runtime, in seconds. One hour.
#
# A judgement rather than a measurement, and it is here to catch a typo rather
# than to express a policy: `"timeout_seconds": 6000000` is a model meaning
# minutes and writing something else, and a delegation nobody can wait out is
# indistinguishable from one with no timeout at all. A worker also has its own
# step budget (`agent_worker.WORKER_ROUNDS`), so an hour is not the only thing
# bounding it.
MAX_TIMEOUT_SECONDS = 3600

# The floor. One second, because the smallest honest timeout is one a worker
# can actually start under, and because a deadline in the past is not a
# constraint on a delegation, it is a refusal to make one.
MIN_TIMEOUT_SECONDS = 1


# --- the report a delegation owes ------------------------------------------

def _sealed_setattr(self, name, value):
    """Refuse every assignment once the object has been built.

    The properties below already refuse the PUBLIC names -- a property with no
    setter raises -- and `__slots__` already refuses a new attribute. What was
    left was the private slot underneath each property, which is assignable
    like any other slot, and "immutable except for the four names right next to
    the four names that are immutable" is not a guarantee anybody can rely on.

    So both are closed here, and the claim in the module docstring is a
    measurement rather than a convention. There is a test that tries each
    private name by hand.

    A `RuntimeError` rather than an `AttributeError`, deliberately: an
    AttributeError is what a typo produces and is routinely swallowed by
    `getattr(..., default)` and by the broad `except Exception` guards all
    over this codebase's readers. Assigning to a delegation's contract is not
    a typo, and it must not be possible to do it quietly.
    """
    raise RuntimeError(
        "a delegation contract is immutable once it exists: %s cannot be set "
        "to %r. The contract is fixed when the worker is spawned -- spawn "
        "another agent instead." % (name, value))


def _sealed_delattr(self, name):
    raise RuntimeError(
        "a delegation contract is immutable once it exists: %s cannot be "
        "deleted." % name)


class ReportRequirements(object):
    """What TMT must collect about a delegation when it ends.

    Three flags and nothing else. They are NOT permissions and they never
    become one: nothing here is read by `refusal`, and asking for a diff has no
    effect at all on what the worker is allowed to do.

    Every one of them is collected by TMT from state it can see -- the paths
    the worker's own actions named, the repository's own diff -- except
    `summary`, which is the worker's `internal_response` and is therefore the
    worker's own words about its own work. That division is the whole of
    section 17's rule: a file list assembled from a model's prose would be a
    list of the files it *said* it read.
    """

    __slots__ = ("_file_list", "_diff", "_summary", "_sealed")

    def __init__(self, file_list=False, diff=False, summary=False):
        object.__setattr__(self, "_file_list", bool(file_list))
        object.__setattr__(self, "_diff", bool(diff))
        object.__setattr__(self, "_summary", bool(summary))
        object.__setattr__(self, "_sealed", True)

    __setattr__ = _sealed_setattr
    __delattr__ = _sealed_delattr

    # Read-only properties rather than plain attributes, so the object cannot
    # be edited after a worker has started. `__slots__` closes the other door:
    # a new attribute cannot be attached to stand in for one of these.
    @property
    def file_list(self):
        return self._file_list

    @property
    def diff(self):
        return self._diff

    @property
    def summary(self):
        return self._summary

    def any(self):
        return self._file_list or self._diff or self._summary

    def names(self):
        """The requirements that are on, in a fixed order, as words."""
        out = []
        if self._file_list:
            out.append("file_list")
        if self._diff:
            out.append("diff")
        if self._summary:
            out.append("summary")
        return tuple(out)

    def chips(self):
        """The short forms the interface draws: FILES, DIFF, SUMMARY."""
        labels = {"file_list": "FILES", "diff": "DIFF", "summary": "SUMMARY"}
        return tuple(labels[name] for name in self.names())

    def __eq__(self, other):
        if not isinstance(other, ReportRequirements):
            return NotImplemented
        return (self._file_list, self._diff, self._summary) == (
            other._file_list, other._diff, other._summary)

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def __hash__(self):
        return hash((self._file_list, self._diff, self._summary))

    def __repr__(self):
        return "<ReportRequirements %s>" % (", ".join(self.names()) or "none")


NO_REPORT = ReportRequirements()


class DelegationConstraints(object):
    """One delegation's contract: what it may do, how long, what it owes.

    Immutable by construction. There is no setter, no `update` and no method
    that takes a field name -- `parse()` makes one from the spawning model's
    object and nothing changes it afterwards. Section 39 asks for exactly that,
    and the reason is the one `agent_capabilities` gives for having no setter
    either: a method that could be handed `("read_only", False)` is a method a
    later edit can wire model output into, and the model in question is the one
    the constraint exists to bound.

    The default -- no read-only, no timeout, no report -- is what a
    `spawn_agent` with no `constraints` key produces, and a worker running
    under it behaves exactly as every worker did before this existed. That is
    checked by a test rather than asserted here.
    """

    __slots__ = ("_read_only", "_timeout_seconds", "_report", "_sealed")

    def __init__(self, read_only=False, timeout_seconds=None, report=None):
        object.__setattr__(self, "_read_only", bool(read_only))
        object.__setattr__(self, "_timeout_seconds",
                           None if timeout_seconds is None else int(timeout_seconds))
        object.__setattr__(
            self, "_report",
            report if isinstance(report, ReportRequirements) else NO_REPORT)
        object.__setattr__(self, "_sealed", True)

    __setattr__ = _sealed_setattr
    __delattr__ = _sealed_delattr

    @property
    def read_only(self):
        return self._read_only

    @property
    def timeout_seconds(self):
        return self._timeout_seconds

    @property
    def report(self):
        return self._report

    def is_default(self):
        """Whether this contract constrains anything at all.

        The one question the rest of TMT asks most often, because the answer
        decides whether anything changes: a default contract adds no section to
        the worker's prompt, no chips to its card and no structured block to
        its report, so a session that never uses constraints draws and reads
        exactly the screen and the text it did before.
        """
        return (not self._read_only and self._timeout_seconds is None
                and not self._report.any())

    def chips(self):
        """The short forms the interface draws for the constraints themselves.

        `READ ONLY` and `TIMEOUT 10:00`. The report requirements have their own
        chips and are deliberately not mixed in here -- they are a different
        kind of fact about the delegation, and a row reading
        `READ ONLY  DIFF` invites the reading that DIFF is a permission.
        """
        out = []
        if self._read_only:
            out.append("READ ONLY")
        if self._timeout_seconds is not None:
            out.append("TIMEOUT " + clock_text(self._timeout_seconds))
        return tuple(out)

    def describe(self):
        """The contract in words, for a prompt or a report. "" when default."""
        if self.is_default():
            return ""
        lines = []
        if self._read_only:
            lines.append("Mode: READ ONLY -- you may inspect this workspace "
                         "and you may not change it.")
        else:
            lines.append("Mode: read and write.")
        if self._timeout_seconds is not None:
            lines.append("Maximum runtime: %s (%d seconds) from the moment you "
                         "started." % (clock_text(self._timeout_seconds),
                                       self._timeout_seconds))
        if self._report.any():
            lines.append("Required report: %s." % ", ".join(self._report.names()))
        return "\n".join(lines)

    def __eq__(self, other):
        if not isinstance(other, DelegationConstraints):
            return NotImplemented
        return (self._read_only, self._timeout_seconds, self._report) == (
            other._read_only, other._timeout_seconds, other._report)

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def __hash__(self):
        return hash((self._read_only, self._timeout_seconds, self._report))

    def __repr__(self):
        return "<DelegationConstraints read_only=%s timeout=%s report=%s>" % (
            self._read_only, self._timeout_seconds,
            ",".join(self._report.names()) or "none")


# The contract a `spawn_agent` with no "constraints" key runs under, and the
# one every caller falls back to. A module-level singleton because it is
# immutable and there is nothing to distinguish two of them; every worker
# spawned without constraints shares this object and none of them can change
# it, which is also the strongest possible statement that they cannot leak
# state into each other.
DEFAULT = DelegationConstraints()


# --- reading the model's object --------------------------------------------

# The keys `constraints` understands. Unknown keys are refused rather than
# ignored, which is section 38: a model that wrote "readonly" or "timeout" and
# was silently given a delegation with neither would have been told its
# constraint was applied when it was not, and the whole value of a contract is
# that both sides know what it says.
_CONSTRAINT_KEYS = frozenset({"read_only", "timeout_seconds", "report"})
_REPORT_KEYS = frozenset({"file_list", "diff", "summary"})

_UNKNOWN = ("FAILED: %r is not a delegation constraint. The constraints are: "
            "read_only (true/false), timeout_seconds (a whole number of "
            "seconds), and report (an object with file_list, diff and summary "
            "flags).")

_UNKNOWN_REPORT = ("FAILED: %r is not a report requirement. The requirements "
                   "are file_list, diff and summary, each true or false.")


def _flag(value, where, name):
    """(bool, error). A flag read strictly: no strings, no numbers, no None."""
    if value is None:
        return False, ""
    if isinstance(value, bool):
        return value, ""
    return False, ("FAILED: %s.%s must be true or false, not %r." %
                   (where, name, value))


def _report(value):
    """(ReportRequirements, error) from the model's "report" object."""
    if value is None:
        return NO_REPORT, ""
    if not isinstance(value, dict):
        return NO_REPORT, ("FAILED: \"report\" must be an object with "
                           "file_list, diff and summary flags, not %r." % (value,))
    for key in value:
        if key not in _REPORT_KEYS:
            return NO_REPORT, _UNKNOWN_REPORT % (key,)
    flags = {}
    for key in ("file_list", "diff", "summary"):
        flag, error = _flag(value.get(key), "report", key)
        if error:
            return NO_REPORT, error
        flags[key] = flag
    return ReportRequirements(**flags), ""


def _timeout(value):
    """(seconds or None, error) from the model's "timeout_seconds".

    Strict about the type on purpose. `True` is an int in Python and would
    otherwise become a one-second deadline; a float is accepted only when it is
    a whole number, because 600.5 is a model computing rather than choosing;
    and a numeric string is refused rather than coerced, for the reason the
    unknown-key refusal exists -- a contract both sides do not read the same
    way is not a contract.
    """
    if value is None:
        return None, ""
    if isinstance(value, bool):
        return None, ("FAILED: \"timeout_seconds\" must be a whole number of "
                      "seconds, not %r. Leave it out if the delegation should "
                      "not be time-limited." % (value,))
    if isinstance(value, float):
        if value != int(value):
            return None, ("FAILED: \"timeout_seconds\" must be a whole number "
                          "of seconds; %r is not." % (value,))
        value = int(value)
    if not isinstance(value, int):
        return None, ("FAILED: \"timeout_seconds\" must be a whole number of "
                      "seconds, not %r." % (value,))
    if value < MIN_TIMEOUT_SECONDS:
        # Zero is refused rather than treated as "expire at once". A deadline
        # that has already passed is not a constraint on a delegation, it is a
        # refusal to make one, and a model that meant that would not have
        # spawned an agent. The refusal says what to write instead.
        return None, ("FAILED: \"timeout_seconds\" must be at least %d; %r "
                      "gives the delegation no time to run. Leave it out if "
                      "the delegation should not be time-limited."
                      % (MIN_TIMEOUT_SECONDS, value))
    if value > MAX_TIMEOUT_SECONDS:
        return None, ("FAILED: \"timeout_seconds\" is at most %d (%s); %d is "
                      "longer than any delegation should run."
                      % (MAX_TIMEOUT_SECONDS,
                         clock_text(MAX_TIMEOUT_SECONDS), value))
    return value, ""


def parse(value):
    """(DelegationConstraints, error) from a `spawn_agent`'s "constraints".

    The ONE way a contract is made. Every failure comes back as a sentence
    rather than an exception, because the party that has to act on it is a
    model taking its next step: a refusal it can read and correct is worth more
    than a stack trace, exactly as it is everywhere else in `agent_actions`.

    Nothing is half-accepted. A `constraints` object with one bad key produces
    no constraints and no worker at all -- section 38 -- because a delegation
    started under half a contract is the one outcome nobody can reason about:
    the main agent believes it asked for a read-only worker and the runtime
    believes it was never asked.
    """
    if value is None:
        return DEFAULT, ""
    if not isinstance(value, dict):
        return DEFAULT, ("FAILED: \"constraints\" must be an object, not %r. "
                         "Example: {\"read_only\":true,\"timeout_seconds\":600,"
                         "\"report\":{\"summary\":true}}" % (value,))
    if not value:
        # An empty object is a model saying "no constraints" the long way
        # round. It is not a mistake and there is nothing to refuse.
        return DEFAULT, ""
    for key in value:
        if key not in _CONSTRAINT_KEYS:
            return DEFAULT, _UNKNOWN % (key,)
    read_only, error = _flag(value.get("read_only"), "constraints", "read_only")
    if error:
        return DEFAULT, error
    seconds, error = _timeout(value.get("timeout_seconds"))
    if error:
        return DEFAULT, error
    report, error = _report(value.get("report"))
    if error:
        return DEFAULT, error
    return DelegationConstraints(read_only=read_only, timeout_seconds=seconds,
                                 report=report), ""


# --- the runtime rule ------------------------------------------------------

VIOLATION_HEADER = "CONSTRAINT VIOLATION"

_REFUSAL = (
    "%s\n\n"
    "This delegation is read-only.\n"
    "    Attempted operation: %s\n"
    "    Reason it is refused: %s\n"
    "    Result: operation blocked -- nothing was read, written or run.\n\n"
    "Carry on with the task using what you may do: %s. If the task cannot be "
    "finished without changing a file, say exactly that in your "
    "internal_response and name the change you would have made -- the main "
    "agent will make it."
)

# The verbs named in the refusal, as a readable list. Built once because the
# sentence is assembled on a background thread at the moment a worker is about
# to be corrected, and rebuilding it per refusal would sort the set each time
# for a string that never differs.
_ALLOWED_LIST = ", ".join(sorted(READ_ONLY_ACTIONS - {"internal_response",
                                                      "send_message"}))


def refusal(constraints, action):
    """The sentence refusing this action under this contract, or "".

    The ONE authority on what read-only means, asked by `agent_worker` before
    every dispatch on both its single-action and its batch path, and again by
    `agent_actions.execute_action` at the dispatcher itself. Two layers, one
    rule: a later edit that adds a third dispatch path gets the same answer
    from the same function rather than a fourth copy of the policy.

    It FAILS CLOSED on a contract it cannot read. `agent_capabilities.refusal`
    is the only other guard in TMT that does, and for the same reason: every
    other guard here is protecting finished work from being held hostage, where
    this one is protecting a permission nobody gave. An object that is not a
    `DelegationConstraints` is not evidence that writing was allowed.
    """
    if constraints is None:
        # No contract at all is the ordinary, unconstrained delegation. It is
        # not the unreadable case -- `DEFAULT` and None mean the same thing to
        # every caller -- and refusing it would make every existing worker
        # read-only, which is exactly what section 4 forbids.
        return ""
    try:
        read_only = bool(constraints.read_only)
    except Exception:
        read_only = True
    if not read_only:
        return ""
    name = str(action or "")
    if name in READ_ONLY_ACTIONS:
        return ""
    return _REFUSAL % (VIOLATION_HEADER, name or "(no action named)",
                       _WHY_REFUSED.get(name, _DEFAULT_WHY), _ALLOWED_LIST)


def violation(action, paths=(), kind="write_blocked"):
    """One recorded constraint violation, as a plain dict.

    A dict rather than a class because it is data that gets counted, printed
    and put in a report, and never behaves. Section 41's shape:
    `{"type": ..., "operation": ..., "path": ...}`, with the path left out
    rather than filled with None when the action named none.
    """
    entry = {"type": str(kind), "operation": str(action or "")}
    named = tuple(p for p in (paths or ()) if isinstance(p, str) and p.strip())
    if named:
        entry["path"] = named[0]
        if len(named) > 1:
            entry["paths"] = named
    return entry


def violations_line(violations):
    """`1 write operation blocked (write_file agent_ui.py)`, or "".

    One line, because it rides in a report the main agent reads at a glance and
    the detail is in the entries themselves. Never omitted when there are any:
    a blocked write is often the reason a delegation did not finish its task,
    and hiding it would leave the main agent reading an incomplete result with
    no explanation for it.
    """
    entries = list(violations or ())
    if not entries:
        return ""
    shown = []
    for entry in entries[:3]:
        operation = entry.get("operation") or "?"
        path = entry.get("path")
        shown.append("%s %s" % (operation, path) if path else operation)
    tail = "" if len(entries) <= 3 else ", and %d more" % (len(entries) - 3)
    return "%d write operation%s blocked (%s%s)" % (
        len(entries), "" if len(entries) == 1 else "s", ", ".join(shown), tail)


# --- how a delegation ended ------------------------------------------------
#
# Six outcomes, kept apart, because collapsing them is what section 14 and
# section 44 both forbid: a delegation that timed out after inspecting forty
# files is not a delegation that crashed, and a main agent told "failed" about
# the first would go looking for a bug that is not there.
#
# These are the words a report uses. The RECORD's own status
# (`agent_manager.Status`) is what the runtime moves through, and
# `status_of` is the one translation between them -- the two vocabularies are
# separate because the manager's is about a thread's lifecycle and this one is
# about a contract's outcome.

COMPLETED = "completed"
FAILED = "failed"
TIMED_OUT = "timed_out"
CANCELLED = "cancelled"
CONSTRAINT_VIOLATION = "constraint_violation"
ERROR = "error"
RUNNING = "running"

# What the interface draws for each, in the project's existing vocabulary --
# the panel already says "done", "killed" and "failed", and these are the two
# it did not have a word for.
STATUS_WORDS = {
    COMPLETED: "COMPLETED",
    FAILED: "FAILED",
    TIMED_OUT: "TIMED OUT",
    CANCELLED: "CANCELLED",
    CONSTRAINT_VIOLATION: "CONSTRAINT VIOLATION",
    ERROR: "ERROR",
    RUNNING: "RUNNING",
}


def clock_text(seconds):
    """`600` -> `10:00`. A duration as minutes and seconds, or hours.

    Used for the contract's timeout, for the time a delegation has spent, and
    for what is left of it, so all three read the same way and the eye can
    compare them without converting anything.
    """
    try:
        total = int(max(0, round(float(seconds))))
    except (TypeError, ValueError):
        return "0:00"
    if total >= 3600:
        return "%d:%02d:%02d" % (total // 3600, (total % 3600) // 60, total % 60)
    return "%d:%02d" % (total // 60, total % 60)


# How many paths a report lists before it says how many more there are. A
# delegation that read two hundred files has said something useful by saying
# "200 inspected"; pasting all two hundred into the main agent's context is
# section 40's "do not dump every tool call" arriving through the report.
MAX_REPORT_PATHS = 40

# How much of a diff goes into the main agent's context. `agent_git` clips its
# own diffs already; this is the second, tighter clip, for the same reason --
# the main agent is being told what a worker changed, not being asked to review
# it line by line, and it can read any file it wants to.
MAX_REPORT_DIFF_CHARS = 6000

_NO_CHANGES = "No workspace changes."
_READ_ONLY_DIFF = "No changes permitted by delegation."


class DelegationResult(object):
    """How one delegation ended, as one structured object.

    Every field is either a fact the runtime measured (the status, the timing,
    the paths the worker's own actions named, the violations it was refused) or
    a thing the worker itself said (`summary`, which is its
    `internal_response`). Nothing here is inferred from a worker's prose, and
    the one field that IS a worker's prose is named `summary` rather than
    `files` or `diff`, which is section 17 and section 18 in the shape of a
    class.

    The diff is passed IN rather than fetched here, because this module runs no
    subprocess and reads no repository. `agent_actions` builds it from
    `agent_git`, which is the same git infrastructure `git_diff` uses.
    """

    __slots__ = ("worker_id", "status", "task", "constraints", "summary",
                 "inspected", "changed", "diff", "errors", "violations",
                 "duration", "started_at", "finished_at", "steps")

    def __init__(self, worker_id, status, task="", constraints=None,
                 summary="", inspected=(), changed=(), diff="", errors="",
                 violations=(), duration=None, started_at=None,
                 finished_at=None, steps=None):
        self.worker_id = str(worker_id)
        self.status = str(status)
        self.task = str(task or "")
        self.constraints = constraints if constraints is not None else DEFAULT
        self.summary = str(summary or "")
        self.inspected = tuple(inspected or ())
        self.changed = tuple(changed or ())
        self.diff = str(diff or "")
        self.errors = str(errors or "")
        self.violations = tuple(violations or ())
        self.duration = duration
        self.started_at = started_at
        self.finished_at = finished_at
        self.steps = steps

    @property
    def timeout(self):
        """The contract's timeout, so a reader has it beside the duration."""
        try:
            return self.constraints.timeout_seconds
        except Exception:
            return None

    def status_word(self):
        return STATUS_WORDS.get(self.status, self.status.upper())

    def _paths_block(self, title, paths):
        if not paths:
            return "  %s: none" % title
        shown = list(paths[:MAX_REPORT_PATHS])
        tail = ("" if len(paths) <= MAX_REPORT_PATHS
                else "\n    ... and %d more" % (len(paths) - MAX_REPORT_PATHS))
        return "  %s (%d):\n%s%s" % (title, len(paths),
                                     "\n".join("    " + p for p in shown), tail)

    def files_block(self):
        """The FILES section: what was inspected, and what was changed.

        Both halves are built from the paths the worker's own ACTIONS named --
        `read_file`'s path, `write_file`'s path -- taken from the request,
        where a path is a fact, and never from anything the worker wrote in
        prose. A read-only delegation's "Changed" is therefore empty because
        nothing could have changed it, not because the worker said so.
        """
        return "FILES\n%s\n%s" % (self._paths_block("Inspected", self.inspected),
                                  self._paths_block("Changed", self.changed))

    def diff_block(self):
        """The DIFF section, from the repository's own state.

        Three shapes, and which one appears is decided by what happened rather
        than by what the worker claims: a read-only delegation says so in as
        many words, a delegation that wrote nothing says there are no changes,
        and one that wrote something shows what git says about the files it
        named.
        """
        if self.constraints is not None and getattr(self.constraints, "read_only", False):
            return "DIFF\n  " + _READ_ONLY_DIFF
        text = self.diff.strip()
        if not text or text == "(no changes)":
            return "DIFF\n  " + _NO_CHANGES
        if len(text) > MAX_REPORT_DIFF_CHARS:
            text = (text[:MAX_REPORT_DIFF_CHARS]
                    + "\n... (diff clipped at %d characters)" % MAX_REPORT_DIFF_CHARS)
        return "DIFF\n" + "\n".join("  " + line for line in text.splitlines())

    def summary_block(self):
        text = " ".join(self.summary.split()) if self.summary else ""
        if not text:
            text = ("The delegation produced no report of its own."
                    if self.status != TIMED_OUT else
                    "The delegation was stopped at its deadline before it "
                    "reported.")
        return "SUMMARY\n  " + text

    def timing_line(self):
        """`Runtime: 4:32 of 10:00`, or just the runtime when uncapped."""
        if self.duration is None:
            return ""
        spent = clock_text(self.duration)
        limit = self.timeout
        if limit is None:
            return "Runtime: %s" % spent
        return "Runtime: %s of %s" % (spent, clock_text(limit))

    def progress_line(self):
        """What the delegation got through, from figures the runtime kept.

        The partial result section 14 and section 21 ask for. It is drawn for
        every outcome and not only for a timeout, because a delegation that
        failed on its thirtieth step and one that failed on its first are
        different failures and the count is the only thing that says which.
        """
        parts = []
        if self.steps is not None:
            parts.append("%d action%s taken" % (self.steps,
                                                "" if self.steps == 1 else "s"))
        if self.inspected:
            parts.append("%d file%s inspected" % (len(self.inspected),
                                                  "" if len(self.inspected) == 1 else "s"))
        parts.append("%d file%s changed" % (len(self.changed),
                                            "" if len(self.changed) == 1 else "s"))
        return "Progress: " + ", ".join(parts)

    def describe(self):
        """The whole result, as the text the main agent is handed.

        Only what was asked for, plus what always matters. The three report
        sections appear when the contract asked for them; the status, the
        timing, the progress and any violations appear always, because those
        are how the main agent tells a delegation that finished from one that
        ran out of time -- and being unable to tell them apart is the failure
        section 44 exists to prevent.
        """
        head = ["Background agent #%s" % self.worker_id,
                "STATUS: %s" % self.status_word()]
        chips = ()
        try:
            chips = self.constraints.chips() + self.constraints.report.chips()
        except Exception:
            chips = ()
        if chips:
            head.append("Contract: %s" % "  ".join(chips))
        timing = self.timing_line()
        if timing:
            head.append(timing)
        head.append(self.progress_line())
        line = violations_line(self.violations)
        if line:
            head.append("Constraint violations: %s" % line)
        blocks = ["\n".join(head)]
        report = getattr(self.constraints, "report", NO_REPORT)
        if getattr(report, "summary", False) or self.summary:
            blocks.append(self.summary_block())
        if getattr(report, "file_list", False):
            blocks.append(self.files_block())
        if getattr(report, "diff", False):
            blocks.append(self.diff_block())
        if self.errors:
            blocks.append("ERRORS\n  " + " ".join(self.errors.split()))
        return "\n\n".join(blocks)

    def __repr__(self):
        return "<DelegationResult #%s %s>" % (self.worker_id, self.status)
