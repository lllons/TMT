"""What a task's verification ran, what it proved, and the rules around it.

State and nothing else -- the division `agent_plan`, `agent_review` and
`agent_manager` all keep, and for the same reason: this is read from the
action thread and drawn in two places, and a state object that also knew how
to discover a command or how to run one would have to be right about all of
it at once. `agent_verify_discovery` decides what COULD be run here,
`agent_verify_engine` decides what SHOULD be and runs it, and this module
holds what came back.

What this is for. The plan says what will be done and the review says whether
doing it worked; neither of them is evidence. A model can write a plan, mark
every step completed, satisfy a reviewer that read the diff, and still have
shipped code that does not compile. Verification is the part of the pipeline
that produces facts: a command ran, it exited 0 or it did not, and that
number is not an opinion anybody can argue with.

Five things here are deliberate and are the ones to read before changing it:

- **Only an execution outcome can make a check pass.** `VerificationCheck`
  keeps its status private and offers exactly three ways to move it:
  `record()` from a real exit code, `skip()` and `fail_to_run()`. Neither of
  the last two can reach PASSED, and `record()` takes an integer -- a string
  is a TypeError, deliberately, because the failure being guarded against is
  somebody wiring model text into it. There is no `verify` key that carries a
  status and no operation that sets one.
- **A result's status is COMPUTED, never given.** `VerificationResult` takes
  checks and works out where it stands from them. It has no status parameter
  to pass the wrong thing to.
- **A passing verification goes stale the moment the code moves under it.**
  It was evidence about a tree. Editing after it means the thing that passed
  is not the thing that would ship, so `passed` is false while anything has
  changed since. This is what makes the fix/verify loop close rather than
  being a suggestion, and it is the same mechanism `agent_review` uses.
- **ERROR is not a pass, and it does not spend a cycle.** A command that
  timed out, a runner that could not be started, a verification that was
  cancelled -- none of those is evidence, and treating a missing answer as a
  good one is the single worst thing this feature could do. It costs no cycle
  because the limit exists to bound the fix/verify LOOP, and a run that never
  reported has not been round it.
- **A repository with nothing to run releases the gate rather than trapping
  it.** If every check was skipped because the tool it needs is not installed,
  there is no evidence to be had here and holding the answer forever would be
  a broken verifier keeping finished work hostage -- the outcome
  `agent_review.refusal` already refuses to produce. It is released loudly:
  the user is told, and the model is told to say so.
"""

import re
import time

# --- what one check can be doing -------------------------------------------

# The statuses of a single check. Kept apart from the run's own states below
# even where the words coincide, because they answer different questions: a
# check status is what happened to one command, and a state is where the whole
# verification has got to. They were briefly one set, and reading `state ==
# PASSED` then meant two different things three lines apart.
CHECK_PENDING = "pending"
CHECK_RUNNING = "running"
CHECK_PASSED = "passed"
CHECK_FAILED = "failed"
CHECK_SKIPPED = "skipped"
CHECK_ERROR = "error"

CHECK_STATUSES = (CHECK_PENDING, CHECK_RUNNING, CHECK_PASSED, CHECK_FAILED,
                  CHECK_SKIPPED, CHECK_ERROR)

# The four the brief asks to be kept apart rather than collapsed into a
# boolean, and they are genuinely four different things: it worked, it did
# not, it never ran because something earlier settled the question, and it
# broke. A caller that wants a boolean asks `check.passed`.
CHECK_SETTLED = (CHECK_PASSED, CHECK_FAILED, CHECK_SKIPPED, CHECK_ERROR)

# What a check is checking. The category is what the column shows and what the
# selector groups on; it is not the level, because one category can appear at
# two levels -- targeted tests and the full suite are both TEST.
SYNTAX = "syntax"
FORMAT = "format"
LINT = "lint"
TYPECHECK = "typecheck"
TEST = "test"
BUILD = "build"

CATEGORIES = (SYNTAX, FORMAT, LINT, TYPECHECK, TEST, BUILD)

# The hierarchy of section 8, cheapest first. The numbers are the order things
# run in and the order they are given up on: a level-1 failure means the
# levels above it would be measuring a tree that is already known to be wrong.
LEVEL_BASIC = 1        # syntax, parse, format of what changed
LEVEL_STATIC = 2       # lint, type check, compiler check
LEVEL_TARGETED = 3     # the tests that name what changed
LEVEL_RELATED = 4      # the tests around them
LEVEL_BUILD = 5        # the project's own build
LEVEL_FULL = 6         # the whole suite

LEVELS = (LEVEL_BASIC, LEVEL_STATIC, LEVEL_TARGETED, LEVEL_RELATED,
          LEVEL_BUILD, LEVEL_FULL)

LEVEL_NAMES = {
    LEVEL_BASIC: "basic",
    LEVEL_STATIC: "static",
    LEVEL_TARGETED: "targeted tests",
    LEVEL_RELATED: "related tests",
    LEVEL_BUILD: "build",
    LEVEL_FULL: "full regression",
}


# --- where a whole verification has got to ---------------------------------

IDLE = "idle"
PLANNING = "planning"
RUNNING = "running"
PASSED = "passed"
FAILED = "failed"
ERROR = "error"
CANCELLED = "cancelled"

STATES = (IDLE, PLANNING, RUNNING, PASSED, FAILED, ERROR, CANCELLED)

# A display-only value, and deliberately NOT in STATES. `VerificationState`
# stores PASSED for a verification that really did pass and later went stale,
# because it really did pass; but drawing it as passed would put a green tick
# beside work the gate is at that moment refusing. `display` answers this
# instead. The lesson is `agent_review.ReviewState.display`'s, applied before
# it could be learned twice -- there, a stale review drew the tick and a test
# found it rather than a person.
STALE = "stale"

# The states a final answer may go out from, and there is exactly one. ERROR,
# CANCELLED and a stale pass are all "no usable evidence", and the whole point
# of this module is that those are not successes.
SETTLED_PASS = (PASSED,)

# How many times one task may go round the fix/verify loop. Three, matching
# the review cycle limit it sits beside: one run and one fix is the common
# shape, two is a hard task, and a third that still fails says something a
# fourth is unlikely to change.
MAX_VERIFY_CYCLES = 3

# What makes a task substantial enough to be worth verifying. Both halves are
# facts the RUNTIME observed rather than claims the model made: the plan is
# the one it wrote and the runtime is holding, and the changed paths are the
# ones actions actually named. A model cannot talk its way out of verification
# by describing its work as small.
#
# The same two numbers `agent_review` uses, and named separately on purpose so
# that changing one is not silently changing the other. Three steps is where a
# plan stops being a formality -- the planning rules already tell the model not
# to make one for a task of one or two.
VERIFY_MIN_PLAN_STEPS = 3
VERIFY_MIN_CHANGED_PATHS = 1

# How much of a command's output is kept. Generous for the failing case,
# because a failure the model cannot diagnose costs another whole cycle, and
# bounded because a test runner having a bad day must not crowd the task out
# of its own context. The tail is what is kept: a runner puts its summary and
# its failures at the end.
MAX_OUTPUT_CHARS = 4000
MAX_SUMMARY_CHARS = 300
MAX_CHECKS_REPORTED = 24


class VerifyError(ValueError):
    """A verification the caller got wrong, carrying the sentence to send.

    A subclass of ValueError for the reason `agent_plan.PlanError` is one: a
    caller that only knows about bad arguments still catches it, and nothing
    here can end a session. `agent_actions` turns it into an ordinary action
    result, and the state stays wherever it was -- which, for anything that
    went wrong mid-run, is ERROR, which blocks the answer.
    """


def _text(value, limit=MAX_SUMMARY_CHARS):
    """One field of a report, trimmed and bounded."""
    text = str(value if value is not None else "").strip()
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text


def _tail(text, limit=MAX_OUTPUT_CHARS):
    """The END of some output, bounded, saying how much was dropped.

    The tail rather than the head, which is the opposite of what TMT does with
    a diff and is right for the opposite reason: a diff's first lines say what
    file it is about, and a test runner's last lines say what happened. A
    failure truncated from the front throws away the failure.
    """
    text = str(text or "")
    if len(text) <= limit:
        return text
    kept = text[-limit:]
    kept = kept.split("\n", 1)[-1] if "\n" in kept else kept
    return ("... %d earlier character(s) of output omitted; the end is kept "
            "because that is where a runner puts its result.\n%s"
            % (len(text) - len(kept), kept))


def last_line(text):
    """The command's own last word about itself, or "".

    Quoted, never interpreted. This is the one place a count like "43 passed"
    reaches the screen, and it reaches it as the runner's sentence rather than
    as TMT's claim -- because TMT deciding what a program's output means is
    the rule that once called a green test run a failure. Whether the check
    PASSED is decided by the exit code and by nothing else; this is only
    allowed to say what the program said.
    """
    for line in reversed(str(text or "").splitlines()):
        stripped = line.strip()
        if stripped:
            return _text(stripped)
    return ""


# Whether a quoted last line is a runner reporting on itself, rather than the
# last thing that happened to reach the pipe. Both halves are needed: a count
# without a word ("1.2.3") is a version string, and a word without a count
# ("Traceback (most recent call last):") is the start of something rather than
# a summary of it.
_SUMMARY_WORDS = re.compile(
    r"\b(pass(?:ed|ing)?|fail(?:ed|ures?)?|ok|error(?:s)?|warning(?:s)?|"
    r"tests?|checks?|files?|assertions?|examples?|skipped|success(?:ful)?)\b",
    re.IGNORECASE)
_SUMMARY_COUNT = re.compile(r"\d")


def looks_like_a_summary(text):
    """Whether a command's last line is worth showing as its result.

    A display rule, and only a display rule: it can make a row say less, never
    say something wrong. It exists because a command that succeeds silently
    still has a last line, and that line is whatever else reached the pipe --
    `python -m py_compile` exits 0 and its last line, on this very repository,
    is a SyntaxWarning quoting somebody's regex. Printed as the check's result
    that reads as a finding, when what actually happened is that the check
    passed and printed nothing about itself.

    So a quoted line has to look like a report: a number and a word that
    belongs in one. Everything else falls back to the exit code, which is what
    TMT actually measured and is never misleading.
    """
    text = str(text or "")
    return bool(_SUMMARY_COUNT.search(text) and _SUMMARY_WORDS.search(text))


# --- one check -------------------------------------------------------------


class VerificationCheck:
    """One command, and what running it proved.

    The status is private and there are exactly three ways to move it, none of
    which takes a caller's word for anything:

      `record(exit_code, ...)`  the command ran; 0 is a pass and anything else
                                is a failure. THE only route to CHECK_PASSED.
      `skip(reason)`            it was not run, and the reason is kept.
      `fail_to_run(reason)`     it could not be run or did not finish.

    That is section 32 implemented as an absence: there is no setter, no
    constructor argument and no operation that can put this into CHECK_PASSED
    without a real process having exited zero.
    """

    __slots__ = ("id", "name", "category", "level", "command", "scope", "why",
                 "_status", "exit_code", "output", "summary", "duration",
                 "reason", "reused")

    def __init__(self, identifier, name, category, level, command=(),
                 scope=(), why=""):
        self.id = str(identifier)
        self.name = str(name)
        self.category = str(category)
        self.level = int(level)
        # The argv list, as a tuple so nothing downstream can edit the command
        # that is about to run -- or, after the fact, the command that ran.
        self.command = tuple(str(part) for part in (command or ()))
        # The paths this check speaks for. Empty means the whole repository,
        # which is what makes a repository-wide check invalidated by any
        # change while a targeted one is invalidated only by its own files.
        self.scope = tuple(scope or ())
        self.why = str(why or "")
        self._status = CHECK_PENDING
        self.exit_code = None
        self.output = ""
        self.summary = ""
        self.duration = None
        self.reason = ""
        self.reused = False

    # --- reading ----------------------------------------------------------

    @property
    def status(self):
        return self._status

    @property
    def passed(self):
        return self._status == CHECK_PASSED

    @property
    def failed(self):
        return self._status == CHECK_FAILED

    @property
    def errored(self):
        return self._status == CHECK_ERROR

    @property
    def skipped(self):
        return self._status == CHECK_SKIPPED

    @property
    def settled(self):
        return self._status in CHECK_SETTLED

    @property
    def command_line(self):
        """The command as a person would type it, for a report.

        Assembled for reading only. Nothing ever executes this string: the
        argv tuple is what runs, and there is no shell anywhere on that path.
        """
        return " ".join(self.command)

    # --- moving it --------------------------------------------------------

    def start(self):
        self._status = CHECK_RUNNING
        return self

    def record(self, exit_code, output="", duration=None):
        """Take the outcome of a real execution. The only route to a pass.

        `exit_code` must be an integer, because the whole guarantee rests on
        this being a process's own answer rather than something a model wrote.
        A bool is refused as well: `record(True)` reads like a pass and would
        silently be exit code 1, which is a failure -- the one confusion in
        this function worth spending a branch on.
        """
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise TypeError(
                "record() takes the integer exit code of a command that "
                "actually ran, not %s. A check cannot be passed by asserting "
                "that it passed." % type(exit_code).__name__)
        self.exit_code = exit_code
        self.output = _tail(output)
        self.summary = last_line(output)
        self.duration = None if duration is None else float(duration)
        self._status = CHECK_PASSED if exit_code == 0 else CHECK_FAILED
        return self

    def skip(self, reason):
        """Not run, and why. Never a pass, and never a failure either.

        Three things produce this and they are different: a tool that is not
        installed, a check the selector did not reach because an earlier one
        failed, and a check whose result is being reused from an earlier run
        in this task. The reason says which, because a skipped check is a hole
        in the evidence and the reader has to know what shape it is.
        """
        self._status = CHECK_SKIPPED
        self.reason = _text(reason)
        return self

    def fail_to_run(self, reason):
        """It could not be run, or did not finish. An ERROR, never a failure.

        The distinction is the point. A failure is evidence -- the code is
        wrong -- and an error is the absence of evidence. Collapsing them
        would tell the model to go and fix a bug that nothing has found.
        """
        self._status = CHECK_ERROR
        self.reason = _text(reason)
        return self

    def reuse(self, earlier):
        """Take a passing result from an earlier run in this task.

        Only from a check that actually PASSED, and the outcome carried across
        is the one that execution produced -- this copies evidence, it does not
        mint it. `reused` is set so every report can say the run it came from
        rather than implying the command ran again.
        """
        if not isinstance(earlier, VerificationCheck) or not earlier.passed:
            raise TypeError(
                "reuse() takes a check that actually passed, not %r. A result "
                "that was never produced cannot be carried forward." % (earlier,))
        self.exit_code = earlier.exit_code
        self.output = earlier.output
        self.summary = earlier.summary
        self.duration = earlier.duration
        self._status = CHECK_PASSED
        self.reused = True
        self.reason = "unchanged since it passed; not run again"
        return self

    # --- describing it ----------------------------------------------------

    def mark(self, plain=False):
        """The one glyph a reader scans the column for."""
        marks = {CHECK_PASSED: ("✓", "+"), CHECK_FAILED: ("✗", "x"),
                 CHECK_RUNNING: ("●", ">"), CHECK_PENDING: ("○", "."),
                 CHECK_SKIPPED: ("–", "-"), CHECK_ERROR: ("!", "!")}
        pair = marks.get(self._status, ("○", "."))
        return pair[1 if plain else 0]

    def reported(self):
        """What the command said about itself, where that is what it said.

        See `looks_like_a_summary`. Empty when the last line is not a report,
        which is the honest answer for a command that succeeded quietly.
        """
        return self.summary if looks_like_a_summary(self.summary) else ""

    def detail(self):
        """The short right-hand column: what the command said, or why not.

        The runner's own summary where it gave one, so "43 passed" is the
        runner's words and not TMT's. TMT's own words -- which are only ever
        about the exit code -- where there is nothing to quote.
        """
        if self.reused:
            return "reused"
        if self._status in (CHECK_SKIPPED, CHECK_ERROR):
            return self.reason or self._status
        if self._status == CHECK_RUNNING:
            return "running…"
        if self._status == CHECK_PENDING:
            return ""
        said = self.reported()
        if said:
            return said
        return "passed" if self.passed else "exit %s" % self.exit_code

    def as_dict(self):
        return {"id": self.id, "name": self.name, "category": self.category,
                "level": self.level, "command": list(self.command),
                "status": self._status, "exit_code": self.exit_code,
                "duration": self.duration, "scope": list(self.scope),
                "reason": self.reason, "reused": self.reused,
                "summary": self.summary}

    def describe(self):
        """One check as the main agent reads it back."""
        rows = ["%s %s [%s]" % (self.mark(), self.name, self._status)]
        if self.command:
            rows.append("  command: %s" % self.command_line)
        if self.why:
            rows.append("  chosen because: %s" % self.why)
        if self.reason:
            rows.append("  %s" % self.reason)
        if self.exit_code is not None:
            rows.append("  exit code: %s" % self.exit_code)
        said = self.reported()
        if said:
            rows.append("  it reported: %s" % said)
        return "\n".join(rows)

    def failure_report(self):
        """Everything the model needs to fix this one, and nothing else.

        Section 20: a failure has to be actionable. The command, the exit
        code, and the end of what it printed -- which is where a test runner
        puts the assertion that failed.
        """
        rows = ["%s FAILED" % self.name,
                "command: %s" % self.command_line,
                "exit code: %s" % self.exit_code]
        if self.scope:
            rows.append("covering: %s" % ", ".join(self.scope[:12]))
        if self.output:
            rows.extend(["output:", self.output])
        return "\n".join(rows)

    def __repr__(self):
        return "VerificationCheck(%s, %s, %s)" % (self.id, self.name, self._status)


# --- one verification run --------------------------------------------------


class VerificationResult:
    """What one verification run concluded, worked out from its checks.

    There is no `status` parameter. The status is derived, every time, from
    what the checks actually did -- so there is no argument for a caller to
    get wrong and no value for one to assert. A run is PASSED when at least
    one check passed and none failed or errored; FAILED when any check failed;
    ERROR when any check could not be run, or when nothing ran at all.

    ERROR outranks FAILED deliberately. A run where the type checker failed
    and the test runner could not start is not a run that found a type error;
    it is a run that does not know what the tests would have said, and the
    honest report of it says so.
    """

    __slots__ = ("number", "checks", "started_at", "finished_at", "scope",
                 "notes", "changed", "level_reached")

    def __init__(self, checks=(), number=1, started_at=None, finished_at=None,
                 scope="current_task", notes=(), changed=()):
        self.number = int(number)
        self.checks = tuple(checks)
        self.started_at = started_at
        self.finished_at = finished_at
        self.scope = str(scope)
        self.notes = tuple(notes)
        self.changed = tuple(changed)
        self.level_reached = max([c.level for c in self.checks
                                  if c.settled and not c.skipped] or [0])

    # --- reading ----------------------------------------------------------

    @property
    def status(self):
        """Computed, never given. See the class docstring."""
        if self.errors():
            return ERROR
        if self.failures():
            return FAILED
        if self.passes():
            return PASSED
        # Nothing ran and nothing broke: every check was skipped. That is not
        # a pass -- there is no evidence in it at all -- and `nothing_to_run`
        # is what tells the gate to release rather than to hold forever.
        return ERROR

    @property
    def passed(self):
        return self.status == PASSED

    @property
    def duration(self):
        if self.started_at is None or self.finished_at is None:
            return None
        return max(0.0, float(self.finished_at) - float(self.started_at))

    def passes(self):
        return tuple(c for c in self.checks if c.passed)

    def failures(self):
        return tuple(c for c in self.checks if c.failed)

    def errors(self):
        return tuple(c for c in self.checks if c.errored)

    def skips(self):
        return tuple(c for c in self.checks if c.skipped)

    def ran(self):
        """The checks that actually executed, reused ones included."""
        return tuple(c for c in self.checks if c.passed or c.failed)

    @property
    def nothing_to_run(self):
        """Whether this repository offered no check at all.

        True when nothing executed and nothing broke -- every check skipped,
        or no check discovered in the first place. It is the difference
        between "the code is wrong" and "there is no way to find out here",
        and the gate treats them differently: a failure holds the answer, and
        an absence of tooling releases it with the absence stated.
        """
        return not self.ran() and not self.errors()

    def counts(self):
        return {CHECK_PASSED: len(self.passes()),
                CHECK_FAILED: len(self.failures()),
                CHECK_ERROR: len(self.errors()),
                CHECK_SKIPPED: len(self.skips())}

    def headline(self):
        """The first line, and it carries what decides what happens next.

        The transcript shows an action's first line and nothing else, so a
        bare "VERIFY FAILED" scrolling past would say a verification failed
        and not what of. Both passing forms begin "VERIFY PASSED", which is
        what `agent_actions.action_event` tests on -- an exact prefix on a
        sentence THIS program wrote, never a substring scan of a runner's
        output.
        """
        counts = self.counts()
        if self.status == PASSED:
            return ("VERIFY PASSED - %d check%s, 0 failures"
                    % (counts[CHECK_PASSED],
                       "" if counts[CHECK_PASSED] == 1 else "s"))
        if self.nothing_to_run:
            return ("VERIFY COULD NOT RUN - no check was available in this "
                    "repository")
        if self.status == FAILED:
            return ("VERIFY FAILED - %d check%s failed"
                    % (counts[CHECK_FAILED],
                       "" if counts[CHECK_FAILED] == 1 else "s"))
        return ("VERIFY ERROR - %d check%s could not be run"
                % (counts[CHECK_ERROR], "" if counts[CHECK_ERROR] == 1 else "s"))

    def summary_line(self):
        counts = self.counts()
        return ("%d passed, %d failed, %d errored, %d skipped"
                % (counts[CHECK_PASSED], counts[CHECK_FAILED],
                   counts[CHECK_ERROR], counts[CHECK_SKIPPED]))

    def as_dict(self):
        return {"number": self.number, "status": self.status,
                "summary": self.summary_line(),
                "checks": [c.as_dict() for c in self.checks],
                "failures": [c.as_dict() for c in self.failures()],
                "warnings": [c.as_dict() for c in self.skips()],
                "scope": self.scope, "duration": self.duration,
                "level_reached": self.level_reached,
                "changed": list(self.changed)}

    def recommendations(self):
        """What to do next, worked out from what actually happened."""
        rows = []
        for check in self.failures():
            rows.append("Fix what %s reported, then run {\"action\":\"verify\"} "
                        "again." % check.name)
        for check in self.errors():
            rows.append("%s could not be run (%s). Fix that or the "
                        "verification cannot report on it."
                        % (check.name, check.reason or "no reason recorded"))
        if self.nothing_to_run:
            rows.append("No verification command was found or runnable here. "
                        "Say so plainly in your final message; do not describe "
                        "the work as verified.")
        if not rows and self.passed:
            rows.append("Nothing. Verification passed - carry on with the "
                        "plan, and request a review if one is required.")
        return rows

    def describe(self):
        """The run as the main agent reads it back.

        Section 34, and the shape of section 3: the headline, the checks with
        their marks, then the failures in full -- because the failing output is
        the part another cycle depends on and everything else is orientation.
        """
        rows = [self.headline(), ""]
        shown = self.checks[:MAX_CHECKS_REPORTED]
        for check in shown:
            detail = check.detail()
            rows.append(("%s %-22s %s"
                         % (check.mark(), check.name, detail)).rstrip())
        if len(self.checks) > len(shown):
            rows.append("... and %d further check(s)."
                        % (len(self.checks) - len(shown)))
        rows.extend(["", self.summary_line()])
        if self.level_reached:
            rows.append("Reached level %d (%s)."
                        % (self.level_reached,
                           LEVEL_NAMES.get(self.level_reached, "?")))
        if self.changed:
            rows.append("Verified against %d changed path(s): %s"
                        % (len(self.changed), ", ".join(self.changed[:10])))
        for note in self.notes:
            rows.append(note)
        broken = self.failures() + self.errors()
        if broken:
            rows.append("")
            for check in broken[:MAX_CHECKS_REPORTED]:
                rows.extend([check.failure_report() if check.failed
                             else "%s COULD NOT RUN: %s" % (check.name, check.reason),
                             ""])
        advice = self.recommendations()
        if advice:
            rows.append("Required action:")
            rows.extend("  " + line for line in advice)
        return "\n".join(rows).strip()

    def __repr__(self):
        return "VerificationResult(#%d, %s, %d checks)" % (
            self.number, self.status, len(self.checks))


# --- what the runtime enforces ---------------------------------------------

_NO_VERIFY = (
    "BLOCKED: you cannot finish yet. This task changed %d file(s) against a "
    "%d-step plan, so it must be verified before it can be called done, and "
    "no verification has been run.\n"
    "Run one now with {\"action\":\"verify\"}. It inspects this repository, "
    "works out which checks are worth running for what you changed, runs them, "
    "and reports what they said. Do not respond again until it passes."
)

_VERIFY_FAILED = (
    "BLOCKED: you cannot finish yet. Verification #%d failed: %d check(s) did "
    "not pass.\n%s\n"
    "Fix what they reported, then run {\"action\":\"verify\"} again. You cannot "
    "mark verification passed yourself -- the only thing that moves it is a "
    "command actually exiting zero."
)

_VERIFY_ERROR = (
    "BLOCKED: you cannot finish yet. Verification #%d could not complete "
    "(%s), so nothing has actually been verified. A verification that failed "
    "to run is not a verification that passed.\n"
    "Fix what stopped it and run {\"action\":\"verify\"} again."
)

_VERIFY_STALE = (
    "BLOCKED: you cannot finish yet. Verification #%d passed, but %d file(s) "
    "have changed since it ran (%s), so what passed is not what you are about "
    "to report.\n"
    "Run {\"action\":\"verify\"} again."
)

_VERIFY_RUNNING = (
    "BLOCKED: a verification is running now and has not reported yet. Wait "
    "for it rather than answering."
)

_VERIFY_CANCELLED = (
    "BLOCKED: you cannot finish yet. The last verification was cancelled "
    "(%s), so nothing was verified. Run {\"action\":\"verify\"} again."
)


class VerificationState:
    """One task's verification: whether it is needed, where it got to, what it found.

    Held by `agent_session.Session` for the life of the session and emptied in
    place between turns, exactly as the plan and the review are and for
    exactly the same reason -- the session loop puts this object in the action
    context BEFORE it calls `begin_turn`, so rebinding it here would leave the
    verify action writing into the state of a task that is over while the gate
    read a fresh empty one. That failure has no error anywhere: it is the gate
    silently switched off.

    Nothing the main agent can emit reaches `settle`. There is no verb for it,
    no key on the `verify` action that takes a status, and no path from model
    text to this state that does not pass through a process's exit code.
    """

    def __init__(self, max_cycles=MAX_VERIFY_CYCLES, clock=None):
        self.state = IDLE
        self.error = ""
        self._history = []
        self._max_cycles = max(1, int(max_cycles))
        self._clock = clock or time.time
        # Evidence the runtime observed, never claims the model made.
        self._changed_paths = []
        self._changed_seen = set()
        # Paths written since the last verification settled. A pass over a
        # tree that has since moved is evidence about something else.
        self._changed_since = []
        # The user's own words, when they asked for verification or for none.
        # None means they did not say, which is the usual case.
        self.user_choice = None
        self.started_at = None
        self.finished_at = None
        # What the current run is doing, for the column while it runs. A list
        # of VerificationCheck, replaced wholesale when a run starts.
        self._live = []
        self._activity = ""

    # --- reading ----------------------------------------------------------

    @property
    def history(self):
        return tuple(self._history)

    @property
    def cycles(self):
        return len(self._history)

    @property
    def max_cycles(self):
        return self._max_cycles

    @property
    def last(self):
        return self._history[-1] if self._history else None

    @property
    def passed(self):
        """Whether a verification has actually passed and still stands.

        Both halves matter. ERROR and FAILED are obviously not passes; a
        PASSED that later edits invalidated is the one that would otherwise
        slip through, because the state still reads `passed` and the tree it
        passed is gone.
        """
        return self.state in SETTLED_PASS and not self.stale

    @property
    def stale(self):
        return bool(self._changed_since) and self.state in SETTLED_PASS

    @property
    def display(self):
        """The state to DRAW, which is not always the state that is stored."""
        return STALE if self.stale else self.state

    @property
    def running(self):
        return self.state in (PLANNING, RUNNING)

    @property
    def limit_reached(self):
        return self.cycles >= self._max_cycles

    @property
    def changed_paths(self):
        return tuple(self._changed_paths)

    @property
    def changed_since(self):
        return tuple(self._changed_since)

    @property
    def activity(self):
        return self._activity

    def checks(self):
        """The checks to draw: the live run's, or the last run's."""
        if self._live:
            return tuple(self._live)
        last = self.last
        return last.checks if last is not None else ()

    def is_required(self, plan=None):
        """Whether this task may not end without verification.

        The user's own words win in both directions when they said anything at
        all. Otherwise it is two facts the RUNTIME holds and the model cannot
        argue with: a plan of at least VERIFY_MIN_PLAN_STEPS steps, which is
        the model's own statement that this is substantial work, and at least
        one file actually written, which is the runtime's observation that the
        work happened. Neither alone is enough -- a long plan that changed
        nothing was research, and a one-line patch with no plan was a favour.
        """
        if self.user_choice is not None:
            return bool(self.user_choice)
        if len(self._changed_paths) < VERIFY_MIN_CHANGED_PATHS:
            return False
        steps = len(getattr(plan, "steps", ()) or ())
        return steps >= VERIFY_MIN_PLAN_STEPS

    def headline(self):
        """One short line naming the state, for the column and the transcript."""
        if self.state == IDLE:
            return "Not verified yet"
        if self.state == PLANNING:
            return "Choosing checks…"
        if self.state == RUNNING:
            return self._activity or "Running checks…"
        if self.state == CANCELLED:
            return "Verification cancelled"
        if self.state == ERROR:
            return "Verification did not complete"
        if self.stale:
            return ("Verification is stale - %d file(s) changed since"
                    % len(self._changed_since))
        last = self.last
        if self.state == PASSED:
            passes = len(last.passes()) if last else 0
            return "Verified - %d check%s passed" % (passes, "" if passes == 1 else "s")
        failures = len(last.failures()) if last else 0
        return "%d check%s failed" % (failures, "" if failures == 1 else "s")

    def describe(self):
        """What `/verify` prints: the state, the history, the last run in full."""
        rows = ["VERIFY %s" % self.state.upper(), self.headline()]
        if self.state == IDLE and not self._history:
            rows.append("Nothing has been verified in this task yet.")
            if self._changed_paths:
                rows.append("%d file(s) have been changed: %s"
                            % (len(self._changed_paths),
                               ", ".join(self._changed_paths[:12])))
            return "\n".join(rows)
        if self.error:
            rows.append("Error: %s" % self.error)
        rows.append("Verification %d of at most %d for this task."
                    % (self.cycles, self._max_cycles))
        if self.limit_reached:
            rows.append("The verification cycle limit has been reached.")
        rows.append("")
        for result in self._history:
            rows.append("Verification #%d  %s  %s"
                        % (result.number, result.status, result.summary_line()))
        last = self.last
        if last is not None:
            rows.extend(["", last.describe()])
        return "\n".join(rows)

    # --- what the runtime observed ---------------------------------------

    def note_change(self, action, paths=()):
        """Record that an action wrote to the workspace.

        Called by the loop where it already tests `action in
        MUTATING_ACTIONS`, so the one place that knows which actions mutate
        stays `agent_config` and this module stays pure state. Paths are the
        ones the action itself named; an action that named none still counts
        as a change, under its own name, because a change nobody can name is
        still a change.
        """
        del action
        named = [p for p in (paths or ()) if isinstance(p, str) and p.strip()]
        for path in named or ("(unnamed)",):
            path = path.strip()
            if path not in self._changed_seen:
                self._changed_seen.add(path)
                self._changed_paths.append(path)
            if path not in self._changed_since:
                self._changed_since.append(path)

    def note_user_choice(self, choice):
        self.user_choice = None if choice is None else bool(choice)

    def note_activity(self, text):
        """What the run is doing right now, for the column. Never a verdict."""
        self._activity = _text(text)

    def set_live(self, checks):
        """The checks of the run in flight, so the column can show them arrive."""
        self._live = list(checks or [])

    # --- moving through the lifecycle -------------------------------------

    def begin(self):
        """Move into PLANNING, or return the sentence refusing to.

        Refused for two reasons and only two: a run already going, and the
        cycle limit, which stops the fix/verify loop burning a session. Both
        come back as sentences the model can act on rather than as exceptions.
        """
        if self.running:
            return ("A verification is already running for this task. Wait for "
                    "it rather than starting another.")
        if self.limit_reached:
            return ("VERIFY LOOP LIMIT REACHED: %d verifications have already "
                    "run for this task, which is the maximum. No further "
                    "verification will be run. Report honestly what the last "
                    "one found and what you did about it."
                    % self._max_cycles)
        self.state = PLANNING
        self.error = ""
        self._live = []
        self._activity = ""
        self.started_at = self._clock()
        self.finished_at = None
        return ""

    def running_now(self, checks=()):
        """Move from PLANNING into RUNNING with the checks that were chosen."""
        self.state = RUNNING
        self.set_live(checks)
        return self

    def settle(self, result):
        """Record a finished run and take its status. The only way to a pass.

        Takes a `VerificationResult` and nothing else, so the only route in is
        a result assembled from checks whose statuses came from real exit
        codes. A string here is a TypeError, deliberately: the failure being
        guarded against is exactly somebody wiring model text into this.
        """
        if not isinstance(result, VerificationResult):
            raise TypeError(
                "settle() takes a VerificationResult built from checks that "
                "actually ran, not %s. Verification state cannot be set from "
                "arbitrary text." % type(result).__name__)
        result.number = self.cycles + 1
        self._history.append(result)
        self.state = result.status
        self.error = "" if result.status != ERROR else (
            "; ".join(c.reason for c in result.errors() if c.reason)
            or "no check produced a result")
        self.finished_at = result.finished_at or self._clock()
        self._live = list(result.checks)
        self._activity = ""
        # A new run is a run against the tree as it stands now, so whatever
        # had changed since the last one is accounted for.
        self._changed_since = []
        return result

    def fail(self, reason):
        """The run could not produce a result at all. Never a pass.

        It does NOT spend a cycle. The limit exists to stop the fix/verify
        loop running forever, and a run that never reported has not been round
        that loop -- charging it would let two broken invocations exhaust a
        task's whole budget without a single check being made.
        """
        self.state = ERROR
        self.error = _text(reason or "the verification did not complete", 600)
        self.finished_at = self._clock()
        self._activity = ""
        return self.error

    def cancel(self, reason=""):
        """The user stopped it. Not a pass, and not a failure either."""
        self.state = CANCELLED
        self.error = _text(reason or "it was cancelled", 600)
        self.finished_at = self._clock()
        self._activity = ""
        return self.error

    def reusable(self):
        """The last run's passing checks, for a re-run that can skip them.

        Only from a run that PASSED and only while nothing has changed since.
        That is the whole of the caching, and it is deliberately the simple
        version: a per-check scope comparison would let a docs-only edit keep
        a lint result, and it would also be one more place for a stale pass to
        hide. What is here cannot produce one -- if anything at all has moved,
        nothing is reused.
        """
        last = self.last
        if last is None or self.state != PASSED or self._changed_since:
            return {}
        return dict((check.id, check) for check in last.checks if check.passed)

    def retire(self):
        """Empty the verification because its task is over. Never refused.

        The lesson `Plan.retire` was written for and `ReviewState.retire`
        repeats: `Session.begin_turn` and `Session.clear` both call this,
        neither is on a path that catches anything, and a retirement that
        could raise would take the session with it. So it is unconditional,
        and it empties IN PLACE -- the loop puts this object in the action
        context before `begin_turn` runs, and a new object here would leave
        the verify action writing into state the gate no longer reads.
        """
        self.state = IDLE
        self.error = ""
        self._history = []
        self._changed_paths = []
        self._changed_seen = set()
        self._changed_since = []
        self.user_choice = None
        self.started_at = None
        self.finished_at = None
        self._live = []
        self._activity = ""

    def __bool__(self):
        """Whether anything has happened worth drawing.

        Defined explicitly for the reason `Plan.__bool__` is: a caller
        reaching for `if verify:` means "is there anything to show", and
        leaving that to default truthiness would make an untouched state
        indistinguishable from a finished one.
        """
        return self.state != IDLE or bool(self._history)

    def __repr__(self):
        return "VerificationState(%s, %d run(s), %d changed)" % (
            self.state, self.cycles, len(self._changed_paths))


# --- the gate --------------------------------------------------------------


def refusal(verify, plan=None, action=None):
    """Why a terminal action may not run yet, or "" when it may.

    The verification half of the completion gate, shaped exactly like
    `agent_plan.refusal` and `agent_review.refusal` and called from the same
    two places in the loop, so the three conditions section 18 asks for are
    enforced side by side and none of them can be satisfied by another. A
    complete plan does not excuse a failed verification; a passing review does
    not excuse one either -- they answer different questions, and that is the
    whole reason both exist.

    It cannot trap a session. The turn's round budget and the identical-reply
    circuit breaker both still bound it, and two things release it outright:
    the cycle limit, and a repository that had nothing to run.

    Exempt, and each for its own reason:

    A task with NO VERIFICATION REQUIRED is not gated, which is most tasks.
    The gate is a consequence of having done substantial work, not a tax on
    answering.

    A task at the CYCLE LIMIT is released, carrying whatever the last run
    objected to. Holding it further would spend the turn's rounds and end with
    no answer at all, and "here is the work and here is what verification
    still says" is worth more to the user than silence.

    A repository with NOTHING TO RUN is released. There is no evidence to be
    had, and a verifier that cannot verify holding finished work hostage is
    the worst outcome available.

    A state object that RAISES lets the answer through, the direction every
    other guard in that loop fails in.
    """
    if action is not None and action not in ("respond", "done"):
        return ""
    if verify is None:
        return ""
    try:
        if not verify.is_required(plan):
            return ""
        if verify.limit_reached and not verify.passed:
            return ""
        last = verify.last
        if last is not None and last.nothing_to_run and not verify.changed_since:
            return ""
        if verify.running:
            return _VERIFY_RUNNING
        if verify.state == CANCELLED:
            return _VERIFY_CANCELLED % (verify.error or "no reason was recorded")
        if verify.state == ERROR:
            number = last.number if last is not None else verify.cycles
            return _VERIFY_ERROR % (number,
                                    verify.error or "no reason was recorded")
        if verify.state == IDLE:
            steps = len(getattr(plan, "steps", ()) or ())
            return _NO_VERIFY % (len(verify.changed_paths), steps)
        if verify.stale:
            changed = verify.changed_since
            return _VERIFY_STALE % (last.number, len(changed),
                                    ", ".join(changed[:8]))
        if verify.passed:
            return ""
        listed = "\n".join("  %s: %s" % (check.name, check.detail())
                           for check in last.failures())
        return _VERIFY_FAILED % (last.number, len(last.failures()), listed)
    except Exception:
        return ""


def limit_release(verify):
    """The warning a released answer carries, or "" when nothing was released.

    Written for the USER rather than for the model, because by the time this
    is reached the turn is ending and there is no next step to instruct. One
    line; `/verify` holds the run in full.
    """
    if verify is None:
        return ""
    try:
        if verify.passed:
            return ""
        last = verify.last
        if last is not None and last.nothing_to_run and not verify.changed_since:
            return ("Verification found nothing it could run in this "
                    "repository, so the work is unverified. See /verify.")
        if not verify.limit_reached:
            return ""
        failures = len(last.failures()) if last is not None else 0
        return ("Verification did not pass: %d run(s) and %d check(s) still "
                "failing. The answer is no longer being held - see /verify."
                % (verify.cycles, failures))
    except Exception:
        return ""


def held_line(verify, plan=None):
    """The one line the USER is shown when an answer was held for verification."""
    del plan
    if verify is None:
        return "Verification not finished. Continuing."
    try:
        if verify.state == IDLE:
            return "Verification required and not yet run - continuing."
        if verify.running:
            return "Verification still running - continuing."
        if verify.state == CANCELLED:
            return "Verification was cancelled - continuing."
        if verify.state == ERROR:
            return "Verification did not complete - continuing."
        if verify.stale:
            return ("Verification is stale - %d file(s) changed since it "
                    "passed. Continuing." % len(verify.changed_since))
        last = verify.last
        failures = len(last.failures()) if last is not None else 0
        return ("Verification found %d failing check%s - continuing."
                % (failures, "" if failures == 1 else "s"))
    except Exception:
        return "Verification not finished. Continuing."


# --- what the user's own words asked for ------------------------------------
#
# The same shape and the same modesty as `agent_review.requests_review`: a
# conservative reading of a human's request, never a command parser, answering
# three ways because "say nothing" is the common case and must not be read as
# either instruction.

_VERIFY_DECLINED = (
    r"\b(?:no|without|skip|skipping)\s+(?:the\s+|a\s+|an\s+|any\s+)?"
    r"(?:verification|verify(?:ing)?)\b",
    r"\b(?:do\s*not|don'?t)\s+(?:\w+\s+){0,3}verif(?:y|ication)\b",
    r"\bverification\s+(?:is\s+)?not\s+(?:needed|required|necessary)\b",
    r"\bno\s+need\s+(?:for|to)\s+(?:a\s+|an\s+)?verif(?:y|ication)\b",
    r"\b(?:do\s*not|don'?t|no|without|skip)\s+(?:\w+\s+){0,3}run\s+"
    r"(?:the\s+)?(?:tests?|suite|checks?)\b",
)

_VERIFY_REQUESTED = (
    r"\bverif(?:y|ication)\b",
    r"\b(?:run|do|perform|add)\s+(?:the\s+|an?\s+)?(?:tests?|test suite|"
    r"suite|checks?|lint|type\s*check(?:ing)?|build)\b",
    r"\bmake sure it (?:works|passes|builds|compiles)\b",
    r"\bcheck (?:that )?it (?:works|passes|builds|compiles)\b",
)


def requests_verification(task):
    """True, False or None: the user asked, declined, or said nothing.

    None rather than False for silence, and the distinction is the whole
    point: `VerificationState.is_required` treats an explicit answer as final
    in both directions and falls back to the runtime evidence only when there
    was no answer. Collapsing silence into False would make every task opt-in;
    collapsing it into True would make every conversational question expensive.

    Declining is checked first, so "commit it, no need to run the tests" is a
    decline and not a request -- both halves match, and the one the user
    actually wrote is the one with the negation in it.
    """
    text = str(task or "").replace("’", "'").lower()
    if any(re.search(pattern, text) for pattern in _VERIFY_DECLINED):
        return False
    if any(re.search(pattern, text) for pattern in _VERIFY_REQUESTED):
        return True
    return None


# --- the plan's verification step ------------------------------------------

# A plan step whose title names verification. Matched as a whole word.
# Deliberately narrower than it could be: "Add tests" and "Run the tests" are
# implementation steps that a model completes by doing them, and catching
# those would refuse a plan update for work that really was finished.
_VERIFY_STEP = re.compile(r"\bverif(y|ies|ied|ying|ication)\b", re.IGNORECASE)


def is_verify_step(title):
    """Whether a plan step's title names the verification milestone."""
    return bool(_VERIFY_STEP.search(str(title or "")))


_STEP_VETO = (
    "FAILED: %s (%s) is the verification step and verification has not passed "
    "-- it is %s. %s A verification step cannot be completed by saying it is "
    "complete; run {\"action\":\"verify\"} and let it report."
)


def plan_veto(verify, plan, obj):
    """Why a plan update may not be applied yet, or "" when it may.

    The same refinement `agent_review.plan_veto` is, and documented as one: it
    rests on the step's title naming verification, which a model can avoid by
    naming the step something else -- and that is exactly why it is not the
    guarantee. The guarantee is `refusal` above, which is driven by
    VerificationState and does not care what any step is called. This keeps
    the plan on screen honest; that keeps the answer honest.
    """
    if verify is None or plan is None or not isinstance(obj, dict):
        return ""
    try:
        if str(obj.get("operation", "")).strip().lower() != "update":
            return ""
        if not verify.is_required(plan):
            return ""
        if verify.passed:
            return ""
        last = verify.last
        if last is not None and last.nothing_to_run and not verify.changed_since:
            # Released for the reason the gate releases: there is nothing to
            # run, so there is nothing the model can do to make this pass, and
            # refusing the step would leave the plan permanently unfinishable.
            return ""
        if verify.limit_reached:
            return ""
        for reference, status in _updates_in(obj):
            if status != "completed":
                continue
            step = plan.find(reference)
            if not is_verify_step(step.title):
                continue
            return _STEP_VETO % (step.id, step.title, _state_words(verify),
                                 _what_to_do(verify))
        return ""
    except Exception:
        # Every failure here lets the update through. A veto is a refinement
        # on the plan's own display; a broken one must never stop the model
        # keeping its plan up to date.
        return ""


def _updates_in(obj):
    """[(step reference, status)] from one plan update, single or batched."""
    entries = obj.get("steps")
    if isinstance(entries, dict):
        entries = [entries]
    if isinstance(entries, (list, tuple)):
        out = []
        for entry in entries:
            if isinstance(entry, dict):
                reference = entry.get("step", entry.get("id"))
                if reference is not None:
                    out.append((reference,
                                str(entry.get("status") or "").strip().lower()))
        return out
    reference = obj.get("step", obj.get("id"))
    if reference is None:
        return []
    return [(reference, str(obj.get("status") or "").strip().lower())]


def _state_words(verify):
    if verify.state == IDLE:
        return "not been run"
    if verify.running:
        return "still running"
    if verify.state == CANCELLED:
        return "cancelled"
    if verify.state == ERROR:
        return "recorded as an error (%s)" % (verify.error or "no reason given")
    if verify.stale:
        return "stale: files changed after it passed"
    last = verify.last
    return "reporting %d failing check(s)" % (len(last.failures()) if last else 0)


def _what_to_do(verify):
    if verify.running:
        return "Wait for it to report."
    if verify.state in (IDLE, ERROR, CANCELLED) or verify.stale:
        return "Run {\"action\":\"verify\"}."
    return "Fix the failing checks, then run {\"action\":\"verify\"} again."
