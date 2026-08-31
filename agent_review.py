"""The independent review of a task's work: its findings, and the rules around them.

State and nothing else, the division `agent_plan` and `agent_manager` both
keep and for the same reason: this is read from two threads and drawn in two
places, and a state object that also knew how to paint itself or how to reach
a model would have to be right about all of it at once.

What this is for. TMT can write code and TMT can run tests, and neither of
those answers the question the user is actually asking, which is "did you
build what I asked for and is it safe". A green suite says the code does what
its tests say; it says nothing about whether the tests are the right tests,
whether an unrelated endpoint stopped being reachable, or whether the feature
that was asked for is the feature that was built. So a SECOND agent reads the
diff, the plan, the task and the repository, without having written any of it,
and says what it found. `agent_worker.run_review` runs that agent read-only;
this module holds what it produced and the rules the runtime enforces around
it.

Four things here are deliberate and are the ones to read before changing it:

- **Only a real review can produce a pass.** `ReviewState` has no method the
  main agent can reach, because the main agent has no verb that writes here.
  `settle()` takes a `ReviewResult`, and the only way to get one is
  `parse_result` on text a reviewer agent actually returned. A model saying
  "review passed" is a sentence, and sentences do not move this state
  machine. That is section 15 of the brief, implemented as an absence.
- **The reviewer's own verdict can be overruled downward, never upward.** A
  reply that says PASS while listing a CRITICAL finding is not a pass; the
  blocking findings decide. A reply that says FAIL with nothing but a MINOR
  in it is still a fail, because a reviewer is allowed to block on an
  accumulation of small things and second-guessing that would be this module
  deciding it knows the code better than the agent that read it.
- **A passing review goes stale the moment the code moves under it.** The
  review was of a diff. Editing after it means the thing that passed is not
  the thing that would ship, so the next final answer needs a fresh review.
  This is what makes the fix/re-review loop close rather than being a
  suggestion.
- **The cycle limit RELEASES the gate rather than tightening it.** Three
  rounds of review and fix, and then the answer goes out carrying the
  unresolved findings. Holding it any longer would spend the turn's round
  budget and end with no answer at all -- the user would get silence instead
  of "here is the work, and here is what review still objects to", and
  silence is the worse of the two.
"""

import json
import re

# --- what a reviewer may conclude ------------------------------------------

# The three verdicts a review can reach. PASS_WITH_WARNINGS is a pass: it is
# the shape of a review that found real things worth saying and nothing worth
# stopping for, and collapsing it into PASS would throw away the distinction
# the user most wants drawn.
PASS = "PASS"
PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
FAIL = "FAIL"

VERDICTS = (PASS, PASS_WITH_WARNINGS, FAIL)

# How bad one finding is. The two at the top force another implementation
# cycle; the two below it do not, and a reviewer that wants to stop the work
# for an accumulation of small problems says FAIL and says why rather than
# inflating a MINOR into a MAJOR.
CRITICAL = "CRITICAL"
MAJOR = "MAJOR"
MINOR = "MINOR"
SUGGESTION = "SUGGESTION"

SEVERITIES = (CRITICAL, MAJOR, MINOR, SUGGESTION)

# The severities that stop a task on their own.
BLOCKING_SEVERITIES = (CRITICAL, MAJOR)

# How a requirement taken from the user's own request came out. Kept apart
# from the findings on purpose: "the code has a bug" and "the code does not do
# what was asked" are different failures, and a review that only ever reported
# the first would pass an implementation of the wrong feature.
SATISFIED = "satisfied"
PARTIAL = "partial"
NOT_SATISFIED = "not_satisfied"

REQUIREMENT_STATES = (SATISFIED, PARTIAL, NOT_SATISFIED)

# --- the state the runtime tracks ------------------------------------------

# Where a task's review has got to. These are the runtime's words, not the
# reviewer's: a verdict is what one review concluded, and a state is what is
# true of the task right now, which includes "nobody has reviewed it yet" and
# "the review did not finish" -- neither of which is a verdict anybody can
# reach.
IDLE = "idle"
RUNNING = "running"
PASSED = "passed"
WARNINGS = "warnings"
FAILED = "failed"
ERROR = "error"

STATES = (IDLE, RUNNING, PASSED, WARNINGS, FAILED, ERROR)

_STATE_FOR_VERDICT = {PASS: PASSED, PASS_WITH_WARNINGS: WARNINGS, FAIL: FAILED}

# The states a final answer may go out from. ERROR is deliberately not one of
# them, and that is section 21 of the brief in one line: a review that crashed
# is not a review that passed, and treating a missing answer as a good one is
# the single worst thing this feature could do.
SETTLED_PASS = (PASSED, WARNINGS)

# How many times one task may go round the review/fix loop. Three is the
# brief's default and it is a judgement rather than a measurement: one review
# and one fix is the common shape, two is a hard task, and a third that still
# finds blocking work says something about the task that another round is
# unlikely to change.
MAX_REVIEW_CYCLES = 3

# What makes a task substantial enough to be worth reviewing. Both halves are
# facts the RUNTIME observed rather than claims the model made: the plan is
# the one it wrote and the runtime is holding, and the changed paths are the
# ones actions actually named. A model cannot talk its way out of a review by
# describing its work as small.
#
# Three steps because that is where a plan stops being a formality: one or two
# steps is a task somebody could have described in a sentence, and the
# planning rules already tell the model not to make a plan for those.
REVIEW_MIN_PLAN_STEPS = 3
REVIEW_MIN_CHANGED_PATHS = 1

# How much of the reviewer's own report is quoted back into the transcript and
# the model's next input. Generous, because the whole value of a review is in
# its specifics and a summarised finding is a finding somebody has to go and
# read again -- but bounded, because a reviewer having a bad day must not be
# able to crowd the task out of its own context.
MAX_ISSUES_REPORTED = 20
MAX_FIELD_CHARS = 1200
MAX_SUMMARY_CHARS = 2000

# Bounds on what a snapshot carries into the reviewer's brief. The diff is the
# highest-signal thing in the whole review and gets the most room; everything
# else is there to orient it.
MAX_BRIEF_DIFF_CHARS = 24000
MAX_BRIEF_STATUS_CHARS = 4000
MAX_BRIEF_TASK_CHARS = 6000


class ReviewError(ValueError):
    """A review result that could not be read, carrying the reason.

    A subclass of ValueError for the reason `agent_plan.PlanError` is one: a
    caller that only knows about bad arguments still catches it, and nothing
    here can end a session. `agent_actions` turns it into an ordinary action
    result and the state becomes ERROR, which blocks the answer -- so a
    malformed review is a review that did not happen, never a review that
    passed.
    """


# --- one finding -----------------------------------------------------------


def _text(value, limit=MAX_FIELD_CHARS):
    """One field of a reviewer's report, trimmed and bounded."""
    text = str(value if value is not None else "").strip()
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text


def normalize_severity(value):
    """One of SEVERITIES, or a ReviewError naming what was allowed.

    Case and spacing are forgiven because a reviewer writing "major" means
    MAJOR. A severity nobody defined is not forgiven: mapping it onto the
    nearest one would decide for the reviewer whether its finding blocks the
    task, which is the one judgement this parser must never make.
    """
    text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if text in SEVERITIES:
        return text
    raise ReviewError("'%s' is not a severity. Use one of: %s."
                      % (value, ", ".join(SEVERITIES)))


def normalize_verdict(value):
    """One of VERDICTS, or a ReviewError.

    Deliberately strict about the three words and forgiving about their
    shape: "pass with warnings" and "PASS_WITH_WARNINGS" are the same verdict,
    and "probably fine" is not a verdict at all.
    """
    text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if text in VERDICTS:
        return text
    raise ReviewError("'%s' is not a review status. Use one of: %s."
                      % (value, ", ".join(VERDICTS)))


def normalize_requirement_state(value):
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in REQUIREMENT_STATES:
        return text
    if text in ("unsatisfied", "missing", "no", "not_met"):
        return NOT_SATISFIED
    if text in ("partially_satisfied", "partially"):
        return PARTIAL
    if text in ("met", "yes", "done"):
        return SATISFIED
    raise ReviewError("'%s' is not a requirement state. Use one of: %s."
                      % (value, ", ".join(REQUIREMENT_STATES)))


def _line_number(value):
    """A line number the reviewer actually gave, or None.

    None rather than 0 or a guess. "Do not fabricate line numbers" is a rule
    the reviewer is given and this is the half of it the runtime can keep:
    anything that is not a positive integer becomes no line number at all,
    and the finding is reported against its file alone.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


class ReviewIssue:
    """One finding: how bad, what it is, where, and why it matters."""

    __slots__ = ("id", "severity", "title", "description", "file", "line",
                 "evidence", "why_it_matters", "suggested_fix")

    def __init__(self, identifier, severity, title, description="", file="",
                 line=None, evidence="", why_it_matters="", suggested_fix=""):
        self.id = str(identifier)
        self.severity = severity
        self.title = title
        self.description = description
        self.file = file
        self.line = line
        self.evidence = evidence
        self.why_it_matters = why_it_matters
        self.suggested_fix = suggested_fix

    @property
    def blocking(self):
        return self.severity in BLOCKING_SEVERITIES

    @property
    def location(self):
        """"path:line", "path", or "" -- never a line without a file.

        A line number on its own points at nothing, and one invented to keep
        the format tidy would be exactly the fabrication the reviewer is told
        not to commit.
        """
        if not self.file:
            return ""
        return "%s:%d" % (self.file, self.line) if self.line else self.file

    def as_dict(self):
        return {"id": self.id, "severity": self.severity, "title": self.title,
                "description": self.description, "file": self.file,
                "line": self.line, "evidence": self.evidence,
                "why_it_matters": self.why_it_matters,
                "suggested_fix": self.suggested_fix}

    def describe(self):
        """The finding as the main agent reads it: short, specific, actionable.

        The shape section 9 of the brief asks for -- an id and a severity, a
        location, the finding, why it matters, and a direction -- with every
        empty field simply absent rather than printed as a heading over
        nothing.
        """
        head = "%s %s" % (self.id, self.severity)
        where = self.location
        if where:
            head += "\n%s" % where
        rows = [head, self.title]
        if self.description and self.description != self.title:
            rows.append(self.description)
        if self.evidence:
            rows.append("Evidence: %s" % self.evidence)
        if self.why_it_matters:
            rows.append("Why: %s" % self.why_it_matters)
        if self.suggested_fix:
            rows.append("Suggested direction: %s" % self.suggested_fix)
        return "\n".join(rows)

    def __repr__(self):
        return "ReviewIssue(%s, %s, %r)" % (self.id, self.severity, self.title)


class ReviewRequirement:
    """One thing the user asked for, and whether it is there.

    The other half of a review, and the half that tells "does this code look
    good" apart from "did we build what was asked". A requirement that came
    back NOT_SATISFIED is the review saying the task is not done, whatever
    the code quality is.
    """

    __slots__ = ("text", "status", "note")

    def __init__(self, text, status=SATISFIED, note=""):
        self.text = text
        self.status = status
        self.note = note

    @property
    def met(self):
        return self.status == SATISFIED

    def as_dict(self):
        return {"text": self.text, "status": self.status, "note": self.note}

    def describe(self):
        marks = {SATISFIED: "[x]", PARTIAL: "[~]", NOT_SATISFIED: "[ ]"}
        row = "%s %s" % (marks.get(self.status, "[?]"), self.text)
        return "%s -- %s" % (row, self.note) if self.note else row

    def __repr__(self):
        return "ReviewRequirement(%r, %s)" % (self.text, self.status)


# --- one review ------------------------------------------------------------


class ReviewResult:
    """What one review concluded, validated and countable.

    `verdict` is what the runtime acts on and `stated_verdict` is what the
    reviewer wrote. They differ only in one direction: a reply claiming a pass
    while listing blocking findings is recorded as a FAIL and keeps its own
    claim on the record, so the disagreement is visible rather than silently
    resolved. The reverse -- a FAIL with nothing blocking in it -- is left
    exactly as the reviewer wrote it, because a reviewer is allowed to stop
    the work for an accumulation of small problems and overruling that would
    be this module deciding it read the code better than the agent that did.
    """

    __slots__ = ("number", "verdict", "stated_verdict", "summary", "issues",
                 "requirements", "tests", "recommendations")

    def __init__(self, verdict, summary="", issues=(), requirements=(),
                 tests="", recommendations="", number=1, stated_verdict=None):
        self.number = int(number)
        self.issues = tuple(issues)
        self.requirements = tuple(requirements)
        self.summary = summary
        self.tests = tests
        self.recommendations = recommendations
        self.stated_verdict = stated_verdict or verdict
        self.verdict = FAIL if self.blocking() else verdict

    # --- reading ----------------------------------------------------------

    def blocking(self):
        """Every finding that forces another implementation cycle."""
        return tuple(issue for issue in self.issues if issue.blocking)

    def unmet(self):
        """Every requirement the review did not find fully satisfied."""
        return tuple(req for req in self.requirements if not req.met)

    def counts(self):
        """{severity: how many}, every severity present, in severity order."""
        return dict((severity,
                     sum(1 for issue in self.issues if issue.severity == severity))
                    for severity in SEVERITIES)

    @property
    def passed(self):
        return self.verdict in (PASS, PASS_WITH_WARNINGS)

    @property
    def overruled(self):
        """Whether the reviewer's own verdict was downgraded by its findings."""
        return self.verdict != self.stated_verdict

    def headline(self):
        """The first line, and it carries the number that decides what happens.

        The count is on this line rather than the one below it because this
        line is the whole of what the transcript shows: `agent_actions`
        describes a review by its result's first line, so a bare "REVIEW
        FAILED" scrolling past would tell the user a review failed and not by
        how much. The two passing forms both begin "REVIEW PASSED", which is
        what `action_event` tests to decide between a success and a warning --
        an exact prefix on a sentence this program wrote, never a substring
        scan of anything the reviewer wrote.
        """
        blocking = len(self.blocking())
        if self.verdict == PASS:
            return "REVIEW PASSED"
        if self.verdict == PASS_WITH_WARNINGS:
            return ("REVIEW PASSED WITH WARNINGS - %d finding(s), none blocking"
                    % len(self.issues))
        return ("REVIEW FAILED - %d blocking issue%s"
                % (blocking, "" if blocking == 1 else "s"))

    def counts_line(self):
        counts = self.counts()
        return ", ".join("%s: %d" % (severity.capitalize(), counts[severity])
                         for severity in SEVERITIES)

    def as_dict(self):
        return {"number": self.number, "status": self.verdict,
                "stated_status": self.stated_verdict, "summary": self.summary,
                "issues": [issue.as_dict() for issue in self.issues],
                "requirements": [req.as_dict() for req in self.requirements],
                "tests": self.tests, "recommendations": self.recommendations}

    def describe(self):
        """The review as the main agent reads it back.

        Section 9 of the brief: concise, specific, and actionable, with the
        blocking findings first because they are the ones that decide whether
        there is more work to do. It is deliberately not an essay -- the
        reviewer's reasoning stays with the reviewer, and what comes out is
        what somebody has to act on.
        """
        rows = [self.headline(), ""]
        if self.overruled:
            # Said plainly rather than quietly applied. A reviewer that called
            # a critical finding a pass has contradicted itself, and hiding
            # that would leave the main agent reading a FAIL it cannot
            # account for.
            rows.append("(The reviewer wrote %s while reporting %d blocking "
                        "finding(s); blocking findings decide, so this is a "
                        "%s.)" % (self.stated_verdict, len(self.blocking()),
                                  self.verdict))
            rows.append("")
        if self.summary:
            rows.extend([self.summary, ""])
        rows.append(self.counts_line())
        blocking = self.blocking()
        rows.append("%d blocking issue%s found."
                    % (len(blocking), "" if len(blocking) == 1 else "s"))
        rows.append("")
        shown = self.issues[:MAX_ISSUES_REPORTED]
        for issue in shown:
            rows.extend([issue.describe(), ""])
        if len(self.issues) > len(shown):
            rows.append("... and %d further finding(s) not listed here."
                        % (len(self.issues) - len(shown)))
            rows.append("")
        if self.requirements:
            rows.append("Requirements from the original request:")
            rows.extend("  " + req.describe() for req in self.requirements)
            rows.append("")
        if self.tests:
            rows.extend(["Tests: %s" % self.tests, ""])
        if self.recommendations:
            rows.extend(["Recommendations: %s" % self.recommendations, ""])
        rows.append(self.required_action())
        return "\n".join(rows).strip()

    def required_action(self):
        """The one sentence saying what happens next."""
        blocking = self.blocking()
        if not blocking:
            unmet = self.unmet()
            if unmet:
                return ("Required action: none of the findings block, but %d "
                        "requirement(s) from the original request are not "
                        "fully satisfied. Read them before you answer."
                        % len(unmet))
            return ("Required action: none. The review found nothing blocking. "
                    "Finish the remaining plan steps and answer.")
        return ("Required action: fix %s, run verification again, then request "
                "review again with {\"action\":\"review\"}. Do not answer until "
                "a review passes."
                % ", ".join(issue.id for issue in blocking))

    def __repr__(self):
        return "ReviewResult(#%d, %s, %d issues)" % (
            self.number, self.verdict, len(self.issues))


# --- reading what a reviewer sent ------------------------------------------


def _first_object(text):
    """The first balanced JSON object in some text, or "" if there is none.

    A reviewer finishes with `internal_response`, whose "response" is free
    text by contract, and a model asked for JSON inside free text will
    sometimes wrap it in a sentence or a code fence. Scanning for a balanced
    object is what makes those readable without loosening the schema
    validation that follows -- the shape is still checked in full, this only
    finds where it starts.
    """
    text = str(text or "")
    depth, start, in_string, escaped = 0, -1, False, False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth:
                depth -= 1
                if depth == 0 and start >= 0:
                    return text[start:index + 1]
    return ""


def _issue_from(raw, index):
    """One ReviewIssue from one entry of the reviewer's "issues" list."""
    if not isinstance(raw, dict):
        raise ReviewError("Each issue must be an object with a \"severity\", a "
                          "\"title\" and a \"description\"; entry %d was %s."
                          % (index + 1, type(raw).__name__))
    severity = normalize_severity(raw.get("severity"))
    title = _text(raw.get("title"))
    if not title:
        raise ReviewError("Issue %d has no \"title\". Every finding needs a "
                          "one-line title naming what is wrong." % (index + 1,))
    description = _text(raw.get("description") or raw.get("detail"))
    if not description:
        raise ReviewError("Issue %d (%s) has no \"description\". Every finding "
                          "needs the specifics; a title on its own is not "
                          "actionable." % (index + 1, title))
    identifier = _text(raw.get("id"), 16) or "R-%03d" % (index + 1)
    return ReviewIssue(
        identifier, severity, title, description,
        file=_text(raw.get("file") or raw.get("path"), 300),
        line=_line_number(raw.get("line")),
        evidence=_text(raw.get("evidence")),
        why_it_matters=_text(raw.get("why_it_matters") or raw.get("why")),
        suggested_fix=_text(raw.get("suggested_fix") or raw.get("fix")))


def _requirement_from(raw, index):
    if isinstance(raw, str):
        return ReviewRequirement(_text(raw), SATISFIED)
    if not isinstance(raw, dict):
        raise ReviewError("Each requirement must be an object such as "
                          "{\"text\":\"...\",\"status\":\"satisfied\"}; entry "
                          "%d was %s." % (index + 1, type(raw).__name__))
    text = _text(raw.get("text") or raw.get("requirement"))
    if not text:
        raise ReviewError("Requirement %d has no \"text\"." % (index + 1,))
    return ReviewRequirement(text,
                             normalize_requirement_state(
                                 raw.get("status", SATISFIED)),
                             _text(raw.get("note")))


def parse_result(text, number=1):
    """A ReviewResult from what a reviewer agent returned, or a ReviewError.

    THE gate on section 15 of the brief. Nothing else in this module builds a
    ReviewResult, and `ReviewState.settle` takes nothing else -- so the only
    route to a passing review runs through here, over text a reviewer agent
    actually produced. Raw model text never mutates review state; it is
    parsed, validated field by field, and either becomes a result or becomes
    an error.

    Every failure raises rather than returning a partial result, and the
    caller turns that into the ERROR state, which blocks the answer. A review
    that could not be read is not a review that passed, and there is no shape
    of malformed output that this function will quietly wave through.
    """
    body = _first_object(text)
    if not body:
        raise ReviewError("The reviewer returned no JSON object. A review must "
                          "end with one object carrying \"status\", \"summary\" "
                          "and \"issues\".")
    try:
        raw = json.loads(body)
    except ValueError as error:
        raise ReviewError("The reviewer's JSON could not be read: %s" % error)
    if not isinstance(raw, dict):
        raise ReviewError("The reviewer's result must be a JSON object, not %s."
                          % type(raw).__name__)
    if "status" not in raw and "verdict" not in raw:
        raise ReviewError("The reviewer's result has no \"status\". It must be "
                          "one of: %s." % ", ".join(VERDICTS))
    verdict = normalize_verdict(raw.get("status", raw.get("verdict")))
    issues_raw = raw.get("issues", [])
    if issues_raw is None:
        issues_raw = []
    if not isinstance(issues_raw, (list, tuple)):
        raise ReviewError("\"issues\" must be a list of findings, or an empty "
                          "list when there are none.")
    issues = [_issue_from(entry, index)
              for index, entry in enumerate(issues_raw)]
    requirements_raw = raw.get("requirements", [])
    if requirements_raw is None:
        requirements_raw = []
    if not isinstance(requirements_raw, (list, tuple)):
        raise ReviewError("\"requirements\" must be a list, or an empty list "
                          "when the request had none worth listing.")
    requirements = [_requirement_from(entry, index)
                    for index, entry in enumerate(requirements_raw)]
    summary = _text(raw.get("summary"), MAX_SUMMARY_CHARS)
    if not summary:
        raise ReviewError("The reviewer's result has no \"summary\". Say in one "
                          "or two sentences what was reviewed and what was "
                          "concluded.")
    return ReviewResult(verdict, summary=summary, issues=issues,
                        requirements=requirements,
                        tests=_text(raw.get("tests"), MAX_SUMMARY_CHARS),
                        recommendations=_text(raw.get("recommendations"),
                                              MAX_SUMMARY_CHARS),
                        number=number)


# --- the snapshot a review is taken against --------------------------------


def _clip(text, limit, what):
    """Bound one section of the brief, saying so when it is cut."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    shown = text[:limit].rsplit("\n", 1)[0]
    return ("%s\n... %s truncated: %d of %d characters shown. Read the rest "
            "with the file and git tools." % (shown, what, len(shown), len(text)))


class ReviewSnapshot:
    """The repository state one review is taken against.

    In memory and nowhere else -- section 23 of the brief, and the same rule
    the rest of TMT keeps: nothing of TMT's is written into the workspace.
    What it holds is the stable boundary a reviewer reasons from, which is the
    honest version of a filesystem snapshot in a program that cannot take one:
    the diff, the status and the commit are read once, at the moment the
    review starts, and the reviewer is told that is what it is reviewing.

    The main agent cannot move the tree underneath it, because `review` blocks
    the synchronous session loop for as long as the reviewer runs. A
    BACKGROUND agent could, which is why `ReviewState.begin` refuses to start
    a review while any worker is still running.
    """

    __slots__ = ("task", "plan_text", "plan_complete", "root", "status",
                 "diff", "stat", "commit", "changed_paths", "verification",
                 "notes")

    def __init__(self, task="", plan_text="", plan_complete=False, root="",
                 status="", diff="", stat="", commit="", changed_paths=(),
                 verification="", notes=()):
        self.task = task
        self.plan_text = plan_text
        self.plan_complete = bool(plan_complete)
        self.root = root
        self.status = status
        self.diff = diff
        self.stat = stat
        self.commit = commit
        self.changed_paths = tuple(changed_paths)
        self.verification = verification
        self.notes = tuple(notes)

    def describe(self):
        """The brief, as the reviewer's task text.

        Stage 1 of section 20: the request, the plan, the status and the diff,
        and nothing else. The changed files, the surrounding code and the
        tests are stages 2 to 4 and the reviewer fetches them itself with its
        own read tools -- which is the difference between a reviewer that
        looked and a reviewer that was handed a summary and agreed with it.
        """
        rows = ["=== THE USER'S ORIGINAL REQUEST ===",
                _clip(self.task, MAX_BRIEF_TASK_CHARS, "the request")
                or "(the request was not recorded)",
                "",
                "=== THE PLAN THE IMPLEMENTING AGENT WROTE ===",
                self.plan_text or "(no plan was made for this task)",
                "Plan complete: %s" % ("yes" if self.plan_complete else "no"),
                ""]
        if self.root:
            rows.extend(["=== REPOSITORY ===", self.root, ""])
        if self.commit:
            rows.extend(["=== HEAD COMMIT AT THE START OF THIS REVIEW ===",
                         self.commit, ""])
        rows.extend(["=== GIT STATUS ===",
                     _clip(self.status, MAX_BRIEF_STATUS_CHARS, "the status")
                     or "(git status was unavailable)",
                     ""])
        if self.stat:
            rows.extend(["=== DIFF STAT ===",
                         _clip(self.stat, MAX_BRIEF_STATUS_CHARS, "the stat"),
                         ""])
        rows.extend(["=== GIT DIFF ===",
                     _clip(self.diff, MAX_BRIEF_DIFF_CHARS, "the diff")
                     or "(no diff was available)",
                     ""])
        if self.changed_paths:
            rows.extend(["=== PATHS THE IMPLEMENTING AGENT WROTE TO ===",
                         ", ".join(self.changed_paths), ""])
        rows.extend(["=== WHAT VERIFICATION ACTUALLY RAN ===",
                     self.verification or
                     "Nothing was run in this session. Treat the implementation "
                     "as unverified unless you find evidence otherwise.",
                     ""])
        if self.notes:
            rows.extend(["=== NOTES ON THIS SNAPSHOT ==="] + list(self.notes) + [""])
        return "\n".join(rows).strip()


# --- the state of one task's review ----------------------------------------

_NO_REVIEW = (
    "BLOCKED: you cannot finish yet. This task changed %d file(s) against a "
    "%d-step plan, so it needs an independent review before it can be called "
    "done, and no review has been run.\n"
    "Run one now with {\"action\":\"review\"}. It reads the diff, the plan and "
    "your request, and comes back with findings you must act on. Do not "
    "respond again until a review has passed."
)

_REVIEW_FAILED = (
    "BLOCKED: you cannot finish yet. Review #%d found %d blocking issue(s):\n"
    "%s\n"
    "Fix them, run verification again, then request another review with "
    "{\"action\":\"review\"}. A finding you believe is wrong is still yours to "
    "investigate and answer in the next review -- disagreeing with it does not "
    "clear it, and you cannot mark the review passed yourself."
)

_REVIEW_ERROR = (
    "BLOCKED: you cannot finish yet. The last review did not produce a usable "
    "result (%s), so nothing has actually been reviewed. A review that failed "
    "to run is not a review that passed.\n"
    "Run {\"action\":\"review\"} again."
)

_REVIEW_STALE = (
    "BLOCKED: you cannot finish yet. Review #%d passed, but %d file(s) have "
    "been changed since it ran (%s), so what passed is not what you are about "
    "to report.\n"
    "Run verification again, then request another review with "
    "{\"action\":\"review\"}."
)

_REVIEW_RUNNING = (
    "BLOCKED: a review is running now and has not reported yet. Wait for it "
    "rather than answering."
)


class ReviewState:
    """One task's review: whether it is needed, where it got to, what it found.

    Held by `agent_session.Session` for the life of the session and emptied in
    place between turns, exactly as the plan is and for exactly the same
    reason -- the session loop puts this object in the action context BEFORE
    it calls `begin_turn`, so rebinding it here would leave the review action
    writing into the state of a task that is over while the gate read a fresh
    empty one. That failure has no error anywhere: it is the gate silently
    switched off.

    Nothing the main agent can emit reaches `settle`. There is no verb for it,
    no operation on the `review` action that takes a verdict, and no path from
    model text to this state that does not pass through `parse_result` on a
    reviewer's own output. That absence is the enforcement.
    """

    def __init__(self, max_cycles=MAX_REVIEW_CYCLES):
        self.state = IDLE
        self.error = ""
        self._history = []
        self._max_cycles = max(1, int(max_cycles))
        # Evidence the runtime observed, never claims the model made. Both are
        # what `is_required` is decided on.
        self._changed_paths = []
        self._changed_seen = set()
        self._verification = []
        # Paths written since the last review settled. A passing review of a
        # diff that has since moved is a review of something else.
        self._changed_since = []
        # The user's own words, when they asked for a review or asked for none.
        # None means they did not say, which is the usual case.
        self.user_choice = None
        self.snapshot = None
        # What each reviewer of this task cost, as the register measured it.
        # An observation, exactly as `_verification` is, and kept here because
        # the reviewer's own record ages off the screen after five seconds
        # while this outlives the turn.
        self._costs = []
        # The checklist the current reviewer declared, as an
        # `agent_reviewbot.Agenda`, or None before any review has run.
        # Attached by `agent_actions._review` at the moment the reviewer is
        # spawned, and it is the SAME object that hangs off that reviewer's
        # `AgentRecord` -- the record is what the reviewer's own thread writes
        # through and this is what outlives it, so `/review` and the strip
        # under the progress bar report one list rather than two copies.
        #
        # It is a readout and nothing else. Nothing here reads it, no gate
        # consults it, and a review whose reviewer declared no agenda at all
        # passes and fails exactly as it did before this existed. That
        # separation is deliberate: an agenda is the reviewer's own account of
        # what it meant to do, and a gate driven by it would be a gate a
        # reviewer could open by shortening its list.
        self.agenda = None

    # --- reading ----------------------------------------------------------

    @property
    def history(self):
        return tuple(self._history)

    @property
    def cycles(self):
        """How many reviews have reported on this task."""
        return len(self._history)

    @property
    def max_cycles(self):
        return self._max_cycles

    @property
    def last(self):
        return self._history[-1] if self._history else None

    @property
    def passed(self):
        """Whether a review has actually passed and still stands.

        Both halves matter. ERROR and FAILED are obviously not passes; a
        PASSED that has been invalidated by later edits is the one that would
        otherwise slip through, because the state still reads `passed` and the
        code it passed is gone.
        """
        return self.state in SETTLED_PASS and not self.stale

    @property
    def stale(self):
        """Whether files have changed since the last review settled."""
        return bool(self._changed_since) and self.state in SETTLED_PASS

    @property
    def display(self):
        """The state to DRAW, which is not always the state that is stored.

        A passing review that has gone stale still reads `passed` in the state
        machine, and correctly so: the review really did pass, and what
        invalidated it happened afterwards. But drawing it as passed would put
        a green tick and the word "passed" beside work the gate is at that
        moment refusing -- the one place in the column a reader must not be
        misled, because a tick is the single glyph they are scanning for.

        So the column asks for this and the gate asks for `state`. They are
        different questions and they were the same attribute until a test drew
        a stale review and found a tick on it.
        """
        return WARNINGS if self.stale else self.state

    @property
    def limit_reached(self):
        return self.cycles >= self._max_cycles

    @property
    def changed_paths(self):
        return tuple(self._changed_paths)

    @property
    def verification(self):
        """What was actually run this turn, in the order it ran.

        An observation and not a verdict. TMT does not read a program's output
        for whether it succeeded -- that is the rule that once labelled a
        green test run a failure -- so what is recorded here is that something
        ran and what it was, and the REVIEWER is the one that reads the output
        and decides what it proves.
        """
        return tuple(self._verification)

    def is_required(self, plan=None):
        """Whether this task may not end without a review.

        The user's own words win in both directions when they said anything at
        all: asking for a review turns one on for a task that would not have
        had one, and asking for none turns it off. It is their tool, and a
        quality gate that cannot be declined is a quality gate somebody works
        around instead of using.

        Otherwise it is two facts the RUNTIME holds and the model cannot
        argue with: a plan of at least REVIEW_MIN_PLAN_STEPS steps, which is
        the model's own statement that this is substantial work, and at least
        REVIEW_MIN_CHANGED_PATHS file actually written, which is the runtime's
        observation that the work happened. Neither alone is enough. A long
        plan that changed nothing was research; a one-line patch with no plan
        was a favour.
        """
        if self.user_choice is not None:
            return bool(self.user_choice)
        if len(self._changed_paths) < REVIEW_MIN_CHANGED_PATHS:
            return False
        steps = len(getattr(plan, "steps", ()) or ())
        return steps >= REVIEW_MIN_PLAN_STEPS

    def counts(self):
        """The last review's severity counts, or every severity at zero."""
        last = self.last
        return last.counts() if last else dict((s, 0) for s in SEVERITIES)

    def blocking_count(self):
        last = self.last
        return len(last.blocking()) if last else 0

    def headline(self):
        """One short line naming the state, for the column and the transcript."""
        if self.state == IDLE:
            return "No review yet"
        if self.state == RUNNING:
            return "Running independent review"
        if self.state == ERROR:
            return "Review did not complete"
        blocking = self.blocking_count()
        if self.stale:
            return "Review is stale - %d file(s) changed since" % len(self._changed_since)
        if self.state == PASSED:
            return "Review passed"
        if self.state == WARNINGS:
            return "Review passed with warnings"
        return "%d blocking issue%s" % (blocking, "" if blocking == 1 else "s")

    def describe(self):
        """What `/review` prints: the state, the history, the last findings."""
        rows = ["REVIEW %s" % self.state.upper(), self.headline()]
        if self.state == IDLE and not self._history:
            rows.append("Nothing has been reviewed in this task yet.")
            if self._changed_paths:
                rows.append("%d file(s) have been changed: %s"
                            % (len(self._changed_paths),
                               ", ".join(self._changed_paths[:12])))
            return "\n".join(rows)
        cost = self.cost_line()
        if cost:
            rows.append(cost)
        rows.extend(self._agenda_rows())
        if self.error:
            rows.append("Error: %s" % self.error)
        rows.append("Review %d of at most %d for this task."
                    % (self.cycles, self._max_cycles))
        if self.limit_reached:
            rows.append("The review cycle limit has been reached.")
        rows.append("")
        for result in self._history:
            counts = result.counts()
            rows.append("Review #%d  %s  %d blocking, %d suggestion(s)"
                        % (result.number, result.verdict,
                           len(result.blocking()), counts[SUGGESTION]))
        last = self.last
        if last is not None:
            rows.extend(["", last.describe()])
        return "\n".join(rows)

    def _agenda_rows(self, title="What the reviewer set out to check:"):
        """The current reviewer's declared checklist, or no rows at all.

        Guarded to nothing at every step, because this is a readout inside a
        report: an agenda that cannot be described must cost `/review` those
        rows and never the findings under them. That is the same bargain the
        column strikes -- `review_rows` draws a review that raises as no
        review -- applied to the permanent surface.
        """
        agenda = self.agenda
        if agenda is None:
            return []
        try:
            if not len(agenda):
                return []
            return ["", title, agenda.describe()]
        except Exception:
            return []

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

    def note_run(self, action, target=""):
        """Record that something was executed. What it proved is not decided here."""
        label = "%s %s" % (action, target) if target else str(action)
        label = label.strip()
        if label and label not in self._verification:
            self._verification.append(label)

    def note_user_choice(self, choice):
        """Record that the user asked for a review, or asked for none."""
        self.user_choice = None if choice is None else bool(choice)

    def note_cost(self, tokens=None, exact=False, seconds=None):
        """What the reviewer that just ran cost, from the register's figures.

        An OBSERVATION and never a verdict, the same footing `note_run` sits
        on: these are numbers `AgentManager` measured about a thread it owned,
        and nothing here reads them for whether the review was any good.

        It is recorded on the state rather than left on the `AgentRecord`
        because the record ages out of the strip after five seconds and this
        outlives the turn. Without it "what did that review cost" is a
        question only answerable while the review is still on screen -- which
        is the moment nobody is asking it.

        Both halves are kept apart from `exact`, which travels with them: a
        token figure the provider never reported is drawn with a leading `~`,
        the rule everywhere else in TMT, and a total mixing one measured
        number with one estimated one is an estimate.
        """
        self._costs.append({
            "number": self.cycles + 1,
            "tokens": max(0, int(tokens or 0)),
            "exact": bool(exact),
            "seconds": None if seconds is None else max(0.0, float(seconds)),
        })

    @property
    def costs(self):
        """What each reviewer of this task cost, in the order they ran."""
        return tuple(dict(cost) for cost in self._costs)

    def cost_line(self):
        """One line naming what the last reviewer cost, or "".

        Nothing at all when nothing was measured, rather than a row of zeroes
        -- the rule the corner meter already follows, and the reason is the
        same: a readout of an absence is worse than the absence.
        """
        if not self._costs:
            return ""
        cost = self._costs[-1]
        parts = []
        if cost["tokens"]:
            parts.append("%s%d tokens" % ("" if cost["exact"] else "~",
                                          cost["tokens"]))
        if cost["seconds"] is not None:
            parts.append("%ds" % int(round(cost["seconds"])))
        if not parts:
            return ""
        return "Reviewer #%d used %s." % (cost["number"], " in ".join(parts))

    # --- moving through the lifecycle -------------------------------------

    def begin(self, snapshot=None):
        """Move into RUNNING, or return the sentence refusing to.

        Refused for two reasons and only two. The cycle limit, which stops the
        review/fix loop burning a session; and a background worker still
        running, which is section 22 -- a reviewer reading a tree that another
        agent is writing to is reviewing a state that never existed. Both come
        back as sentences the model can act on rather than as exceptions.
        """
        if self.state == RUNNING:
            return ("A review is already running for this task. Wait for it "
                    "rather than starting another.")
        if self.limit_reached:
            return ("REVIEW LOOP LIMIT REACHED: %d reviews have already run for "
                    "this task, which is the maximum. No further review will "
                    "be run. Report honestly what the last review found and "
                    "what you did about it."
                    % self._max_cycles)
        self.state = RUNNING
        self.error = ""
        self.snapshot = snapshot
        return ""

    def settle(self, result):
        """Record a parsed review and take its state. The only way to a pass.

        Takes a `ReviewResult` and nothing else, so the only route in is
        `parse_result` over a reviewer's own output. A string here is a
        TypeError rather than a pass, deliberately: the failure mode being
        guarded against is exactly somebody wiring model text into this.
        """
        if not isinstance(result, ReviewResult):
            raise TypeError(
                "settle() takes a ReviewResult built by parse_result from a "
                "reviewer agent's own output, not %s. Review state cannot be "
                "set from arbitrary text." % type(result).__name__)
        result.number = self.cycles + 1
        self._history.append(result)
        self.state = _STATE_FOR_VERDICT[result.verdict]
        self.error = ""
        # A new review is a review of the tree as it stands now, so whatever
        # had changed since the last one is accounted for.
        self._changed_since = []
        return result

    def fail(self, reason):
        """The review did not produce a usable result. Never a pass.

        Section 21 in one method: a reviewer that crashed, timed out, returned
        nothing or returned something unreadable leaves the task in ERROR, and
        ERROR is not in SETTLED_PASS, so the final answer stays blocked until
        a review actually completes.

        It does NOT spend a cycle. The limit exists to stop the review/fix
        loop running forever, and a review that never reported has not been
        round that loop -- charging it would let two provider hiccups exhaust
        a task's whole review budget without a single finding being made.
        """
        self.state = ERROR
        self.error = str(reason or "the review did not complete")
        return self.error

    def retire(self):
        """Empty the review because its task is over. Never refused.

        The lesson `Plan.retire` was written for, applied before it could be
        learned twice: `Session.begin_turn` and `Session.clear` both call
        this, neither is on a path that catches anything, and a retirement
        that could raise would take the session with it. So it is
        unconditional, and it empties IN PLACE -- the loop puts this object in
        the action context five lines before `begin_turn` runs, and a new
        object here would leave the review action writing into state the gate
        no longer reads.
        """
        self.state = IDLE
        self.error = ""
        self._history = []
        self._changed_paths = []
        self._changed_seen = set()
        self._changed_since = []
        self._verification = []
        self._costs = []
        self.user_choice = None
        self.snapshot = None
        # Dropped rather than retired in place, because it is not this
        # object's to empty: the agenda belongs to a reviewer's record, that
        # reviewer is over, and a fresh review gets a fresh one from
        # `agent_actions._review`. Nothing holds the old one afterwards.
        self.agenda = None

    def __bool__(self):
        """Whether anything has happened worth drawing.

        Defined explicitly for the reason `Plan.__bool__` is: this class has
        no `__len__`, but a caller reaching for `if review:` means "is there
        anything to show", and leaving that to default truthiness would make
        an untouched state indistinguishable from a finished one.
        """
        return self.state != IDLE or bool(self._history)

    def __repr__(self):
        return "ReviewState(%s, %d review(s), %d changed)" % (
            self.state, self.cycles, len(self._changed_paths))


# --- what the runtime enforces ---------------------------------------------


def refusal(review, plan=None, action=None):
    """Why a terminal action may not run yet, or "" when it may.

    The review half of the completion gate, shaped exactly like
    `agent_plan.refusal` and called from the same two places in the loop, so
    the two conditions section 12 asks for are enforced side by side and
    neither can be satisfied by the other. A plan that is complete does not
    excuse a review that failed; a review that passed does not excuse a plan
    with steps outstanding.

    Everything it can say is a sentence the model can act on, and every one of
    them names the action that clears it. It cannot trap a session: the turn's
    round budget and the identical-reply circuit breaker both still bound it,
    and the cycle limit releases the gate outright rather than holding an
    answer that will never come.

    Exempt, and each for its own reason:

    A task with NO REVIEW REQUIRED is not gated, which is most tasks. The gate
    is a consequence of having done substantial work, not a tax on answering.

    A task at the CYCLE LIMIT is released. Three rounds of review and fix and
    it goes out, carrying whatever the last review objected to. Holding it
    further would spend the turn's rounds and end with no answer at all, and
    "here is the work and here is what review still says" is worth more to the
    user than silence.

    A state object that RAISES lets the answer through, the direction every
    other guard in that loop fails in. A broken review object holding finished
    work hostage is the worst outcome available.
    """
    if action is not None and action not in ("respond", "done"):
        return ""
    if review is None:
        return ""
    try:
        if not review.is_required(plan):
            return ""
        if review.limit_reached and not review.passed:
            # Released, not cleared. `limit_release` is what the loop shows
            # the user, so the ending is stated rather than silent.
            return ""
        if review.state == RUNNING:
            return _REVIEW_RUNNING
        if review.state == ERROR:
            return _REVIEW_ERROR % (review.error or "no reason was recorded")
        if review.state == IDLE:
            steps = len(getattr(plan, "steps", ()) or ())
            return _NO_REVIEW % (len(review.changed_paths), steps)
        if review.stale:
            changed = review._changed_since
            return _REVIEW_STALE % (review.last.number, len(changed),
                                    ", ".join(changed[:8]))
        if review.passed:
            return ""
        last = review.last
        listed = "\n".join("  %s %s: %s%s"
                           % (issue.id, issue.severity, issue.title,
                              " (%s)" % issue.location if issue.location else "")
                           for issue in last.blocking())
        return _REVIEW_FAILED % (last.number, len(last.blocking()), listed)
    except Exception:
        return ""


def limit_release(review):
    """The warning a released answer carries, or "" when nothing was released.

    Section 14: the loop must not silently continue forever, and it must not
    silently stop caring either. When the cycle limit lets an answer out with
    findings still open, this is what the USER is shown beside it -- the
    ending stated rather than an answer that simply appears as though the
    review had approved it.

    Written for the user rather than for the model, because by the time this
    is reached the turn is ending and there is no next step to instruct. It is
    one line, and `/review` holds the findings in full.
    """
    if review is None:
        return ""
    try:
        if not review.limit_reached or review.passed:
            return ""
        blocking = review.blocking_count()
        return ("Review did not pass: %d reviews ran and %d blocking issue%s "
                "still open. The answer is no longer being held - see /review."
                % (review.cycles, blocking, "" if blocking == 1 else "s"))
    except Exception:
        return ""


# --- what the user's own words asked for ------------------------------------
#
# The same shape as `agent_actions.authorizes_push`, and with the same
# modesty: a conservative reading of a human's request, never a command
# parser. The difference is that this one answers three ways, because "say
# nothing" is the common case and must not be read as either instruction.

_REVIEW_DECLINED = (
    r"\b(?:no|without|skip|skipping)\s+(?:the\s+|a\s+|an\s+|any\s+)?"
    r"(?:independent\s+|code\s+)?review\b",
    r"\b(?:do\s*not|don'?t)\s+(?:\w+\s+){0,3}review\b",
    r"\breview\s+(?:is\s+)?not\s+(?:needed|required|necessary)\b",
    r"\bno\s+need\s+(?:for|to)\s+(?:a\s+|an\s+)?(?:independent\s+|code\s+)?review\b",
)

# Deliberately narrow. "Review the README" is a request to READ something and
# must not turn on an independent audit of a diff, so a bare "review" followed
# by a noun is not a match: what matches is either the compound noun
# ("code review", "independent review"), a verb aimed at the work itself
# ("review the changes", "review your implementation"), or an explicit request
# to run one. Everything this misses falls through to the runtime evidence,
# which is where the decision is meant to be made anyway.
_REVIEW_REQUESTED = (
    r"\b(?:independent|code|peer|self)[- ]review\b",
    r"\breview\s+(?:the\s+|your\s+|this\s+|these\s+|all\s+)?"
    r"(?:change|changes|diff|work|implementation|patch|result|results)\b",
    r"\b(?:run|do|perform|request|get|add)\s+(?:an?\s+|the\s+)?"
    r"(?:independent\s+|code\s+)?review\b",
    r"\b(?:then|and)\s+review\s+it\b",
    r"\breview\s+it\s+(?:before|after|when|first|then|carefully|properly)\b",
)


def requests_review(task):
    """True, False or None: the user asked for a review, declined one, or
    said nothing about it.

    None rather than False for silence, and the distinction is the whole
    point: `ReviewState.is_required` treats an explicit answer as final in
    both directions and falls back to the runtime evidence only when there was
    no answer. Collapsing silence into False would make every task opt-in;
    collapsing it into True would make every conversational question expensive.

    Declining is checked first, so "commit it, no review needed" is a decline
    and not a request -- both halves match, and the one the user actually
    wrote is the one with the negation in it.
    """
    text = str(task or "").replace("’", "'").lower()
    if any(re.search(pattern, text) for pattern in _REVIEW_DECLINED):
        return False
    if any(re.search(pattern, text) for pattern in _REVIEW_REQUESTED):
        return True
    return None


def held_line(review, plan=None):
    """The one line the USER is shown when an answer was held for review.

    One line, because the review block itself is on screen a few columns to
    the right and repeating its findings here would say the same thing twice.
    """
    if review is None:
        return "Review not finished. Continuing."
    try:
        if review.state == IDLE:
            return "Review required and not yet run - continuing."
        if review.state == ERROR:
            return "Review did not complete - continuing."
        if review.state == RUNNING:
            return "Review still running - continuing."
        if review.stale:
            return ("Review is stale - %d file(s) changed since it passed. "
                    "Continuing." % len(review._changed_since))
        blocking = review.blocking_count()
        return ("Review found %d blocking issue%s - continuing."
                % (blocking, "" if blocking == 1 else "s"))
    except Exception:
        return "Review not finished. Continuing."


# --- the plan's review step ------------------------------------------------

# A plan step whose title names review. Matched as a whole word so "Review the
# findings" and "Independent review" both count while "Reviewer notes in the
# README" -- which is a documentation step -- does not get caught by a
# substring search for "review" inside a longer word.
_REVIEW_STEP = re.compile(r"\breview(s|ed|ing)?\b", re.IGNORECASE)


def is_review_step(title):
    """Whether a plan step's title names the review milestone."""
    return bool(_REVIEW_STEP.search(str(title or "")))


_STEP_VETO = (
    "FAILED: %s (%s) is the review step and the review has not passed -- it is "
    "%s. %s A review step cannot be completed by saying it is complete; run "
    "{\"action\":\"review\"} and let it report."
)


def plan_veto(review, plan, obj):
    """Why a plan update may not be applied yet, or "" when it may.

    Section 11: a failed review must not allow the review step to be marked
    completed. Enforced here rather than in `agent_plan` so that module stays
    pure state and knows nothing about reviews, and so the rule sits with the
    feature it belongs to.

    This is a nicety and is documented as one. It rests on the step's title
    naming review, which a model can avoid by naming the step something else
    -- and that is exactly why it is not the guarantee. The guarantee is
    `refusal` above, which is driven by ReviewState and does not care what any
    step is called. This makes the plan on screen honest; that makes the
    answer honest.
    """
    if review is None or plan is None or not isinstance(obj, dict):
        return ""
    try:
        if str(obj.get("operation", "")).strip().lower() != "update":
            return ""
        if not review.is_required(plan):
            return ""
        if review.passed:
            return ""
        for reference, status in _updates_in(obj):
            if status != "completed":
                continue
            step = plan.find(reference)
            if not is_review_step(step.title):
                continue
            return _STEP_VETO % (step.id, step.title, _state_words(review),
                                 _what_to_do(review))
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


def _state_words(review):
    if review.state == IDLE:
        return "not been run"
    if review.state == RUNNING:
        return "still running"
    if review.state == ERROR:
        return "recorded as an error (%s)" % (review.error or "no reason given")
    if review.stale:
        return "stale: files changed after it passed"
    return "reporting %d blocking issue(s)" % review.blocking_count()


def _what_to_do(review):
    if review.state in (IDLE, ERROR) or review.stale:
        return "Run {\"action\":\"review\"}."
    if review.state == RUNNING:
        return "Wait for it to report."
    return "Fix the blocking findings, then run {\"action\":\"review\"} again."
