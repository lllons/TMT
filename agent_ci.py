"""CI mode: TMT as a bounded worker rather than a desk agent.

    tmtcode --ci "run the test suite and fix failures"
    tmtcode --ci --json --max-turns 30 --timeout 600 "lint and fix src/"

Interactively TMT is a program somebody sits in front of: a splash, a menu, a
prompt box, and a question whenever something wants a human's judgement. None of
that exists in a pipeline. What a pipeline needs is one task, a hard bound on
how long it may take, no question that can block, and an exit code somebody can
branch on.

**This is not a mode that can do more; it is a mode that can do less.** Every
guard the interactive agent has is still in front of every action -- the
workspace boundary, the command policy, the boundary DENYs, the push
authorisation. What CI mode changes is the ANSWER to a question nobody is there
to answer, and the answer is always no. See `Run.approve`.

The division is the one every state module here keeps: this decides the
contract, the clock and the words, and it reads no terminal, runs no command and
speaks to no model. `TMT.run_ci` is the half that knows how to start a session.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
There is no flag that turns policy off, no flag that widens the workspace, and
no way to answer an approval yes. A CI mode that could grant its own permissions
would be a way to reach, from a YAML file nobody reads twice, everything the
interactive guards exist to put in front of a person.
"""

import json
import time

# Exit codes. Kept small, documented in `docs/ci.md`, and distinct so a
# pipeline can branch on them: "it did not finish" and "it finished and the
# work is wrong" are different failures and want different retries.
EXIT_OK = 0          # the task completed
EXIT_FAILED = 1      # it ran and the work failed: the agent said so, or a check did
EXIT_USAGE = 2       # bad flags, no credential, unusable workspace
EXIT_LIMIT = 3       # the wall clock or the turn budget ran out
EXIT_BLOCKED = 4     # policy refused something and the task could not go on

COMPLETED = "completed"
FAILED = "failed"
TIMEOUT = "timeout"
MAX_TURNS = "max_turns"
BLOCKED = "blocked"
ERROR = "error"

_CODES = {COMPLETED: EXIT_OK, FAILED: EXIT_FAILED, TIMEOUT: EXIT_LIMIT,
          MAX_TURNS: EXIT_LIMIT, BLOCKED: EXIT_BLOCKED, ERROR: EXIT_FAILED}

# Judgements, not measurements. The turn budget is deliberately higher than the
# interactive default for `high` effort: a CI task is unattended, so the cost of
# stopping one round short is a red build somebody has to re-run by hand, while
# the cost of one round too many is a few seconds.
DEFAULT_MAX_TURNS = 30
DEFAULT_TIMEOUT = 900.0
# Ceilings, and they exist to catch a typo rather than to express a policy --
# `--timeout 6000000` is somebody who meant milliseconds, and a run that sat
# there for eleven weeks would be a stuck pipeline nobody could see.
MAX_TURNS_CEILING = 500
MAX_TIMEOUT = 24 * 60 * 60.0

# How many changed paths the summary carries. A task that rewrote four hundred
# files says so in the count; the list is for a person reading a build log.
MAX_CHANGED_FILES = 200

NO_TASK = ("--ci needs a task. Give it as an argument, or pipe it in:\n"
           '    tmtcode --ci "run the test suite and fix failures"\n'
           '    echo "fix the lint errors" | tmtcode --ci')
NO_KEY = ("No API key is configured, and CI mode will not open the setup "
          "screen -- there is nobody to fill it in. Set the provider's key in "
          "the environment (OPENROUTER_API_KEY, OPENAI_API_KEY, "
          "ANTHROPIC_API_KEY or GEMINI_API_KEY), or run tmtcode once "
          "interactively to store one.")

# What a refused approval is recorded as. The question itself is composed by
# `agent_bash`, which knows what is being asked; this is only the note that the
# answer was no and why there was nobody to give a different one.
REFUSED = ("%s -- refused automatically: CI mode has no user to approve it. "
           "Anything needing a person's judgement is denied.")


class CIError(Exception):
    """A CI run that cannot start. Carries the exit code to leave with."""

    def __init__(self, message, code=EXIT_USAGE):
        Exception.__init__(self, message)
        self.code = code


def check_turns(value):
    """A usable `--max-turns`, or CIError.

    Refuses a bool by name for `agent_glob`'s reason: `int(True)` is 1, so
    `--max-turns` given a flag-shaped value would silently become a one-round
    budget -- an answer, for an argument the caller plainly did not mean as a
    count.
    """
    if value is None:
        return DEFAULT_MAX_TURNS
    if isinstance(value, bool):
        raise CIError("--max-turns needs a whole number of turns.")
    try:
        turns = int(value)
    except (TypeError, ValueError):
        raise CIError("--max-turns needs a whole number, not %r." % (value,))
    if turns < 1:
        raise CIError("--max-turns must be at least 1; %d would run nothing "
                      "at all." % turns)
    if turns > MAX_TURNS_CEILING:
        raise CIError("--max-turns is capped at %d; %d is almost certainly a "
                      "typo." % (MAX_TURNS_CEILING, turns))
    return turns


def check_timeout(value):
    """A usable `--timeout` in seconds, or CIError."""
    if value is None:
        return DEFAULT_TIMEOUT
    if isinstance(value, bool):
        raise CIError("--timeout needs a number of seconds.")
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise CIError("--timeout needs a number of seconds, not %r." % (value,))
    if seconds <= 0:
        raise CIError("--timeout must be more than 0 seconds; %g would stop "
                      "the run before it started." % seconds)
    if seconds > MAX_TIMEOUT:
        raise CIError("--timeout is capped at %g seconds (24 hours); %g is "
                      "almost certainly a typo." % (MAX_TIMEOUT, seconds))
    return seconds


def read_task(parts, stdin=None):
    """The task text, from the command line or from a pipe. CIError if neither.

    The command line wins, and the remaining arguments are JOINED rather than
    requiring one quoted string, because `--ci run the tests` is what somebody
    types the first time and refusing it teaches nothing.

    Stdin is read only when it is not a terminal and nothing was given on the
    command line -- reading a terminal would be the one thing CI mode must
    never do. It is read WHOLE, unlike the interactive piped reader which takes
    one task per line: a task is one instruction, and this repository has
    already recorded what splitting one into four does to a repository.
    """
    text = " ".join(str(part) for part in (parts or ()) if str(part).strip())
    text = " ".join(text.split())
    if text:
        return text
    if stdin is not None:
        try:
            if not stdin.isatty():
                text = " ".join((stdin.read() or "").split())
        except Exception:
            text = ""
    if not text:
        raise CIError(NO_TASK)
    return text


class Result:
    """What a CI run did, as one object: the summary, the JSON and the code.

    Every field is either something the runtime measured or None. Nothing here
    is estimated and nothing is inferred from what the model said about itself:
    `changed_files` comes from the actions' own requests, `turns` from the
    loop's own counter, and `verify` from exit codes. A field TMT cannot answer
    honestly is null rather than guessed -- which is the whole reason a
    pipeline would trust this file rather than parsing the log.
    """

    __slots__ = ("status", "workspace", "task", "turns", "duration",
                 "message", "changed_files", "verify", "blocked_reason",
                 "error")

    def __init__(self, status=COMPLETED, workspace="", task="", turns=0,
                 duration=0.0, message="", changed_files=(), verify=None,
                 blocked_reason=None, error=None):
        self.status = str(status)
        self.workspace = str(workspace)
        self.task = str(task)
        self.turns = int(turns)
        self.duration = float(duration)
        self.message = str(message or "")
        self.changed_files = list(changed_files or ())
        self.verify = dict(verify or {"ran": False, "passed": None, "details": None})
        self.blocked_reason = blocked_reason
        self.error = error

    @property
    def ok(self):
        return self.status == COMPLETED

    def exit_code(self):
        return _CODES.get(self.status, EXIT_FAILED)

    def as_dict(self):
        return {"ok": self.ok, "status": self.status,
                "workspace": self.workspace, "task": self.task,
                "turns": self.turns,
                "duration_seconds": round(self.duration, 1),
                "message": self.message,
                "changed_files": self.changed_files,
                "verify": self.verify,
                "blocked_reason": self.blocked_reason,
                "error": self.error}

    def to_json(self):
        """One object, indented, with a trailing newline. Nothing else."""
        return json.dumps(self.as_dict(), indent=2, sort_keys=False) + "\n"

    def human(self):
        """The same facts for somebody reading a build log."""
        rows = ["TMT CI: %s (%d turn%s, %.1fs)"
                % (self.status, self.turns, "" if self.turns == 1 else "s",
                   self.duration)]
        if self.message:
            rows.append(self.message)
        if self.changed_files:
            rows.append("Changed %d file%s: %s"
                        % (len(self.changed_files),
                           "" if len(self.changed_files) == 1 else "s",
                           ", ".join(self.changed_files[:10])
                           + (" ..." if len(self.changed_files) > 10 else "")))
        if self.verify.get("ran"):
            rows.append("Checks: %s (%s)"
                        % ("passed" if self.verify.get("passed") else "failed",
                           self.verify.get("details") or "no detail"))
        if self.blocked_reason:
            rows.append("Blocked: %s" % self.blocked_reason)
        if self.error:
            rows.append("Error: %s" % self.error)
        return "\n".join(rows)


class Run:
    """One CI run: the contract it works under, the clock, and what happened.

    Handed to the session loop, which asks it three things -- has the time gone,
    what should an approval say, and what did this action touch -- and tells it
    one: how the turn ended. Everything else about the loop is unchanged, which
    is the point: CI mode is a mode of the same agent, not a second one.
    """

    __slots__ = ("task", "workspace", "max_turns", "timeout", "allow_push",
                 "_clock", "_started", "_paths", "_refusals", "_result")

    def __init__(self, task, workspace="", max_turns=DEFAULT_MAX_TURNS,
                 timeout=DEFAULT_TIMEOUT, allow_push=False, clock=None):
        self.task = str(task)
        self.workspace = str(workspace)
        self.max_turns = int(max_turns)
        self.timeout = float(timeout)
        self.allow_push = bool(allow_push)
        self._clock = clock or time.monotonic
        self._started = self._clock()
        self._paths = []
        self._refusals = []
        self._result = None

    # --- the clock ---------------------------------------------------------

    def elapsed(self):
        return max(0.0, self._clock() - self._started)

    def remaining(self):
        return max(0.0, self.timeout - self.elapsed())

    def expired(self):
        """Whether the wall clock has gone.

        Asked at round boundaries by the loop. It cannot interrupt a command
        that is already running -- `bash` has its own timeout for that -- so
        the wall clock is enforced to the nearest action rather than to the
        second, and `docs/ci.md` says so rather than implying otherwise.
        """
        return self.elapsed() >= self.timeout

    # --- the answers nobody is there to give -------------------------------

    def approve(self, question, pattern=""):
        """The CI answer to an approval, which is always no.

        THIS IS THE WHOLE OF THE CI APPROVAL POLICY, and it is worth being
        blunt about what it is not. It does not widen anything. `agent_policy`
        has already decided ALLOW, ASK or DENY before this is ever called: an
        ALLOW never reaches here, a DENY never reaches here, and what reaches
        here is exactly the set of commands TMT would have put to a human.

        With no human, the only two available answers are "always yes" and
        "always no". "Always yes" would make `--ci` a documented way to run
        every command the interactive agent refuses to run unattended -- from a
        YAML file, in a repository anybody can open a pull request against.
        So it is no, every time, and the question is recorded so the summary
        can say what was refused rather than leaving a mystery in the log.

        The shape is `agent_bash._ask`'s: "" means refused. `agent_actions`'
        deletion confirmation reads the same value and refuses on it too.
        """
        note = REFUSED % " ".join(str(question or "").split())[:300]
        if note not in self._refusals:
            self._refusals.append(note)
        return ""

    def choose(self, text, keys):
        """The CI answer to an `ask_user` question: nobody is here.

        None rather than "" on purpose. "" is a person who was here and pressed
        Esc; None is nobody at all, and `agent_ask.answer` tells the model
        which -- so a CI run gets "there is nobody to ask, decide it yourself
        and say what you assumed" rather than being told a person declined.
        """
        return None

    # --- what happened ------------------------------------------------------

    def note_action(self, action, paths=()):
        """Record the workspace paths an action that changed something named.

        From the action's own request, never from the model's account of it --
        the rule `agent_review.note_change` already follows. A command's paths
        are unknowable, so `bash` contributes the same "(unnamed)" marker the
        review record uses rather than nothing or a guess.
        """
        for path in paths or ():
            text = str(path)
            if text and text not in self._paths:
                self._paths.append(text)

    def refusals(self):
        return list(self._refusals)

    def changed(self):
        return sorted(self._paths)[:MAX_CHANGED_FILES]

    def finish(self, answer="", outcome="", turns=0, verify=None, error=None):
        """Settle the run's status from what the turn actually did.

        The order the questions are asked in is the order they matter in. An
        error is what happened; a timeout and an exhausted budget are why it
        stopped; a refusal is why it could not go on; an answer is success. A
        run that ended any other way FAILED, and the outcome the loop recorded
        is the message, because that sentence was written for a person.
        """
        status, message, blocked = COMPLETED, str(answer or ""), None
        if error:
            status, message = ERROR, str(error)
        elif self.expired():
            status = TIMEOUT
            message = ("Stopped after %.0f seconds: the --timeout for this run."
                       % self.timeout)
        elif outcome and "ran out of steps" in outcome:
            status = MAX_TURNS
            message = ("Stopped after %d turns: the --max-turns for this run. "
                       "The work done so far is in the workspace."
                       % self.max_turns)
        elif outcome:
            status, message = FAILED, str(outcome)
        if self._refusals:
            blocked = " ".join(self._refusals[:3])
            if status != COMPLETED:
                # Only terminal when the run did not finish. A task that was
                # refused one command, took another route and passed its checks
                # is a task that SUCCEEDED, and reporting it as blocked would
                # fail a build that is green. The refusals are in the summary
                # either way, so nothing is hidden.
                status = BLOCKED
        self._result = Result(status=status, workspace=self.workspace,
                              task=self.task, turns=int(turns),
                              duration=self.elapsed(), message=message,
                              changed_files=self.changed(),
                              verify=verify, blocked_reason=blocked,
                              error=str(error) if error else None)
        return self._result

    def result(self):
        """The settled result, or a failure saying the run never settled."""
        if self._result is None:
            return self.finish(outcome="the run ended without reporting")
        return self._result


def verify_summary(state):
    """`session.verify` as the three fields the JSON promises. Never raises.

    `ran` is false when nothing was verified, which is the common case: the
    verification engine is a capability the user's own words authorise, so a CI
    task that did not ask for it did not get it. Saying `"ran": false` is the
    honest answer there; inventing a pass would be the worst possible field to
    invent, in the one readout a pipeline branches on.
    """
    empty = {"ran": False, "passed": None, "details": None}
    if state is None:
        return empty
    try:
        last = getattr(state, "last", None)
        if last is None:
            return empty
        details = None
        try:
            details = last.summary_line()
        except Exception:
            details = None
        return {"ran": True, "passed": bool(state.passed), "details": details}
    except Exception:
        return empty
