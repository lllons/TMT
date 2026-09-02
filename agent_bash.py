"""The `bash` action: the one verb a model runs anything with.

Four modules meet here and none of them does another's job. `agent_shell`
works out what the line MEANS, `agent_policy` works out whether it MAY happen,
`agent_sandbox` is the only place in TMT that creates a process, and this
module is the sequence they are asked in and the sentence the model reads
afterwards. It holds no policy of its own: there is no table of denied
programs here, no second containment test, and no way to reach the sandbox
that skips the decision. A guard that appears in two modules is two guards,
and the day one of them is tightened the other becomes the way round it.

The order is the whole design and it is fixed:

    parse -> confine the working directory -> expand globs -> decide
          -> ask, if the decision was ASK -> build the environment -> run
          -> report

Globbing happens BEFORE the decision on purpose. The policy works by reading
arguments, so it has to read the arguments that will actually be handed to the
program -- deciding about `*.py` and then running against forty expanded paths
would be a decision about something that never ran.

WHAT THIS MODULE PROMISES, AND WHAT IT DOES NOT
===============================================

Every result names the sandbox level `agent_sandbox` actually ran under, and
that is the honest half of the claim. Under `LEVEL_POLICY` -- no OS sandbox
helper, which is the normal case on Windows -- a child process's filesystem
writes are NOT confined. What is confined is what TMT was asked to run: the
program, its arguments, the working directory, the environment, the lifetime
and the output. A permitted build tool that runs the repository's own code can
still write wherever the user can, because it is that code doing the writing
and no inspection of a command line can see it coming. Nothing here says
otherwise, in a docstring or in a result.

THE DECISIONS THAT CARRY THE WEIGHT
===================================

**Approval is injected and this module never reads a terminal.** `approve` is
a callable the caller supplies -- `TMT._session_loop` puts one in the action
context -- and when it is absent the answer to an ASK is **no**. A piped run,
the test suite and every background agent all arrive here with no approver, and
all three get the same refusal naming the rule that asked. That is this
repository's standing rule about raw terminal input: any doubt that a human is
there means no, because a wrong yes blocks on a read nobody can answer. It is
also why nothing in this module calls `input()` -- a test that did would hang
the whole suite, which has no per-test timeout.

**The decision is asked once.** `agent_policy.decide` is called with the whole
parsed line and returns one verdict, worst wins. There is no second pass that
re-reads the rules file after an approval and no path that consults `Rules` to
re-decide, because `decide` returns a DENY *before* it looks at the rules at
all, and a second lookup here would be exactly the branch that guarantee is
built on not existing.

**`&&` and `||` short-circuit on the previous stage's exit code, `;` always
runs.** Getting this wrong is not cosmetic: a model that writes `a && b` and is
handed `b`'s output after `a` failed will conclude something false about its
own change. A stage that did not run says so, in the result, with the reason.

**The exit code is the result.** Nothing here reads output text to decide
whether a command succeeded. That is the rule the verification engine is built
on, and this is the same rule in the module that produces the evidence.

**Nothing outlives the session.** Background jobs are bounded (`MAX_JOBS`),
each writes to its own log file under the sandbox home, and each is killed with
the sandbox's process-tree kill on `stop`, on its deadline, and at exit --
through `shutdown()`, which is registered with `atexit` here and can also be
called by the session directly. `agent_sandbox.kill_all` is a second net under
the same promise.
"""

import atexit
import os
import threading
import time
from pathlib import Path

import agent_config
import agent_file_ops
import agent_policy
import agent_sandbox
import agent_shell

# The operations the one verb carries, in the shape `plan` and
# `project_context` already use: one action, an `operation` key, and this
# module says which key is missing for the operation actually asked for. A
# second execution action for background work would be a second place every
# guard above had to be repeated.
RUN = "run"
START = "start"
STATUS = "status"
LOGS = "logs"
STOP = "stop"
OPERATIONS = (RUN, START, STATUS, LOGS, STOP)

# Which operations need a command line, and which need a job to act on. Stated
# as data so the refusal for a missing key can name the operation rather than
# being written out four times.
_NEEDS_COMMAND = (RUN, START)
_NEEDS_JOB = (LOGS, STOP)

# At most four background jobs at once. A judgement rather than a measurement:
# four is enough for a dev server, a watcher, a tunnel and one more, and a
# ceiling exists so that a model which starts something in a loop is stopped by
# a sentence rather than by the machine. The count is DERIVED from the registry
# on every read -- see `_capacity` -- never maintained as a number, so a job
# that ends in three different ways still releases exactly one slot.
MAX_JOBS = 4

# How long a background job may live. Longer than a foreground command's
# ceiling because the case is a dev server somebody watches for a session, and
# bounded because "until something notices" is not a lifetime. Both are
# judgements; what is not a judgement is that the deadline is swept on every
# call into this module and enforced with the same process-tree kill a timeout
# uses.
JOB_DEFAULT_TIMEOUT = 3600.0
JOB_MAX_TIMEOUT = 43200.0

# How much of a job's log is handed back at once, tail-biased for
# `agent_sandbox.MAX_OUTPUT`'s reason: the useful part of a server's log is
# what it printed last.
LOG_TAIL = 20000

# How many finished jobs are kept for reading. A finished job's status and log
# are exactly what a model wants a moment after it exits, so they are not
# dropped when the process ends -- but the list must not grow for the life of a
# session either.
MAX_RETAINED = 20

# How much network the session has granted. `offline`, and the model cannot
# change it: `bash` takes a `network` key because the dispatcher forwards
# whatever the model wrote, and `_network_mode` below narrows that against this
# rather than adopting it. Widening is the user's, exactly as authorising a
# push is -- see `agent_git.authorizes_push`, where the same rule is enforced
# for the same reason. When a user-facing grant is built, this is the constant
# it sets.
GRANTED_NETWORK = agent_policy.OFFLINE

_NETWORK_RANK = {agent_policy.OFFLINE: 0, agent_policy.DEPS: 1,
                 agent_policy.OPEN: 2}


# --- the sentences ----------------------------------------------------------
#
# Written once, here, so the same mistake reads the same way every time it is
# made. Each one says what happened and what would work instead, and none of
# them describes a way round a guard: a refusal that explains how to avoid the
# check has taught avoiding it.

_NO_COMMAND = (
    "FAILED: the `%s` operation needs a `command` -- the command line to run. "
    "Emit {\"action\":\"bash\",\"command\":\"python run_tests.py\"}."
)

_NO_JOB_ID = (
    "FAILED: the `%s` operation needs an `id` -- which background job to act "
    "on. %s"
)

_UNKNOWN_OPERATION = (
    "FAILED: `%s` is not an operation of the bash action. It is one of %s; "
    "\"run\" is the default and runs a command line to completion."
)

_NO_TERMINAL = (
    "REFUSED: %s\n"
    "There is nobody to ask. This run has no terminal -- it is piped, "
    "scripted, or the question reached a background agent -- so an approval "
    "nobody can give is answered no, which is TMT's standing rule wherever a "
    "human might not be there. Nothing ran and nothing was changed. Say in "
    "your message what you needed to run and why, so the user can decide."
)

_DECLINED = (
    "REFUSED: the user was asked whether this command could run and said no. "
    "Nothing ran and nothing was changed. Do not ask again for the same "
    "command; say what you needed it for, and carry on with the work that does "
    "not depend on it."
)

_APPROVER_FAILED = (
    "REFUSED: %s\n"
    "The approval question could not be put to the user (%s), so the answer is "
    "no. Nothing ran and nothing was changed."
)

_BAD_CWD = (
    "FAILED: `cwd` must name a directory inside the workspace. %s"
)

_JOB_LIMIT = (
    "FAILED: %d background jobs are already running, which is the limit. Stop "
    "one with {\"action\":\"bash\",\"operation\":\"stop\",\"id\":\"...\"} "
    "before starting another. There is no queue: TMT has no scheduler to put "
    "one in, and a job that claimed to be waiting would be a claim about "
    "something that is not happening."
)

_JOB_ONE_COMMAND = (
    "FAILED: a background job runs ONE program. This line has %s, and TMT "
    "would have nothing to hand you back an id for. Start the one program that "
    "is long-lived, or write the sequence into a script file and start that."
)

_JOB_NO_REDIRECT = (
    "FAILED: a background job's output goes to its own log file, which is what "
    "the logs operation reads, so a redirect on it would send the output "
    "somewhere the job cannot report. Start it without the redirect and read "
    "the log, or run it in the foreground where redirects work."
)

_NO_SUCH_JOB = "FAILED: there is no background job with the id `%s`. %s"

_NO_JOBS = "There are no background jobs."


# --- the working directory --------------------------------------------------

def _root():
    """The workspace, resolved. Read at call time, never bound at import."""
    return Path(agent_file_ops.workspace()).resolve()


def _resolve_cwd(cwd):
    """(directory, refusal). Exactly one is falsy.

    `agent_file_ops.safe_path` is the containment test and there is no second
    one here. It resolves symbolic links before it compares, so a directory
    inside the workspace that points out of it is refused rather than followed
    -- which is the case a hand-written `startswith` would miss and the reason
    that function exists.
    """
    root = _root()
    text = "" if cwd is None else str(cwd).strip()
    if not text:
        return root, ""
    try:
        target = agent_file_ops.safe_path(text)
    except ValueError:
        return None, _BAD_CWD % (
            "`%s` resolves outside %s. Name a directory inside the workspace, "
            "relative to its root." % (text, root))
    except (OSError, TypeError) as error:
        return None, _BAD_CWD % ("`%s` could not be resolved (%s)." % (text, error))
    if not target.is_dir():
        return None, _BAD_CWD % (
            "`%s` is not a directory that exists." % text)
    return target, ""


def _shown_cwd(directory, root):
    """The working directory as a reader recognises it: `.` for the root."""
    try:
        relative = os.path.relpath(str(directory), str(root))
    except (OSError, ValueError):
        return str(directory)
    return relative if relative not in ("", os.curdir) else "."


# --- what the model asked for, narrowed to what it may have ------------------

def _network_mode(requested):
    """The narrower of what was asked for and what the session granted.

    A model cannot widen its own network access. The key exists because the
    dispatcher forwards every key the model wrote, and forwarding it into
    `agent_policy.decide` unchanged would let a model turn a flat DENY on
    `curl` into an approval question by writing one word -- the exact shape
    `agent_git` refuses for a push, where the authority comes from the user's
    own words and the model cannot add to it.
    """
    asked = str(requested or "").strip().lower()
    if asked not in _NETWORK_RANK:
        asked = GRANTED_NETWORK
    if _NETWORK_RANK[asked] > _NETWORK_RANK.get(GRANTED_NETWORK, 0):
        return GRANTED_NETWORK
    return asked


def _timeout(requested, default, ceiling):
    """A requested timeout, clamped. Anything unreadable takes the default."""
    try:
        seconds = float(requested) if requested is not None else float(default)
    except (TypeError, ValueError):
        seconds = float(default)
    return max(1.0, min(seconds, float(ceiling)))


# --- the parse, and what is done to it before anything is decided ------------

def _expand(stages, cwd):
    """Expand every command's globs in place, against the working directory.

    In place rather than into a copy because these objects were built by this
    call and go nowhere else, and because everything downstream -- the
    decision, the render and the launch -- has to see the same argv. Two
    structures, one expanded and one not, is how a policy comes to be applied
    to a command line that is not the one that runs.

    A redirect's target is deliberately not expanded; `agent_shell.expand` says
    why, and the short version is that a redirect names one file and a pattern
    matching two would have no meaning to apply.
    """
    for stage in stages:
        for command in stage.pipeline.commands:
            command.argv = agent_shell.expand(command.argv, cwd)


def _describe(stages):
    """The whole line as TMT read it."""
    return agent_shell.describe(stages)


def _describe_stage(stage):
    """One stage on its own, without the operator that precedes it."""
    return agent_shell.describe([agent_shell.Stage(stage.pipeline, "")])


def _summarise(text, limit=600):
    """A rendered command line, bounded, and honest about being bounded.

    A glob that matched three hundred files renders to a line nobody reads. It
    is cut rather than dropped, and the cut says how much went, because a
    silently shortened command line is a claim that what is shown is what ran.
    """
    text = str(text or "")
    if len(text) <= limit:
        return text
    return "%s... (+%d more characters)" % (text[:limit], len(text) - limit)


# --- asking a human ---------------------------------------------------------

# What a caller's approver may answer. Anything else -- False, None, "", a
# refusal typed at the terminal -- is no, which is the direction an unreadable
# answer to a security question has to fail in.
_YES = frozenset({"y", "yes", "true", "1", "run", "once"})
_ALWAYS = frozenset({"a", "always", "allow", "remember"})


def _approval_question(decision, rendered, shown_cwd, pattern):
    """What the user is shown. The caller adds how to answer it.

    The division is deliberate: this module knows what is being asked and the
    session knows what the keys are. A question composed here that told the
    user to press `y` would be this module deciding a terminal's interface,
    and a question composed there would be the session restating a policy it
    does not own.
    """
    lines = ["TMT wants to run a command that needs your approval.",
             "",
             "    %s" % _summarise(rendered),
             "    in %s" % shown_cwd,
             "",
             "%s: %s" % (decision.rule, decision.reason)]
    if pattern:
        lines.append("The rule this would be remembered as: %s" % pattern)
    return "\n".join(lines)


def _ask(approve, question, pattern):
    """(answer, failure). The answer is "", "once" or "always".

    Two shapes of approver are accepted -- `approve(question, pattern)` and
    `approve(question)` -- because the second is what a test naturally writes
    and being strict about it would buy nothing. Every failure is no: a
    callable that raises has not approved anything, and a security question
    that could not be put is a question that was not answered.
    """
    if approve is None:
        return "", ""
    try:
        answer = approve(question, pattern)
    except TypeError:
        try:
            answer = approve(question)
        except Exception as error:
            return "", str(error)
    except Exception as error:
        return "", str(error)
    if answer is True:
        return "once", ""
    if isinstance(answer, str):
        word = answer.strip().lower()
        if word in _ALWAYS:
            return "always", ""
        if word in _YES:
            return "once", ""
    return "", ""


def _remember(rules, pattern):
    """Persist an "always" answer, and say what was saved. Never fatal.

    A rule that could not be written costs one more approval question the next
    time, which is the mild direction; raising here would throw away a command
    the user has just approved. `Rules.remember` refuses to store an allow for
    a boundary refusal, and that refusal arrives here as a ValueError and is
    reported rather than swallowed -- but it cannot happen from this path,
    because a boundary DENY never reaches an approval question.
    """
    if rules is None or not pattern:
        return ""
    try:
        return str(rules.remember(pattern, agent_policy.ALLOW))
    except Exception as error:
        return ("The rule could not be saved (%s), so TMT will ask about `%s` "
                "again." % (error, pattern))


# --- running one line -------------------------------------------------------

class _StageReport:
    """What happened to one stage of a line, for the result.

    A stage that did not run carries the reason it did not, because "nothing
    was printed" and "this never ran" are different facts and a model reading
    the first as the second draws a false conclusion about its own change.
    """

    __slots__ = ("rendered", "outcome", "skipped")

    def __init__(self, rendered, outcome=None, skipped=""):
        self.rendered = rendered
        self.outcome = outcome
        self.skipped = skipped


def _should_run(operator, previous):
    """Whether a stage runs, given the operator before it and the last code.

    `previous` is None before anything has run and after a stage that produced
    no exit code -- a timeout, a program that was not there. None is treated as
    a failure for chaining, which is what a shell does with a killed process
    and is the safe direction here: `build && deploy` must not deploy because
    the build was stopped on the clock rather than because it failed.
    """
    if operator == "&&":
        return previous == 0
    if operator == "||":
        return previous != 0
    return True


def _skip_reason(operator, previous):
    if operator == "&&":
        return ("the stage before it %s, and && runs only after a success"
                % _ended(previous))
    return ("the stage before it succeeded, and || runs only after a failure")


def _ended(code):
    return "did not finish" if code is None else "exited %d" % code


def _run_line(stages, cwd, env, seconds, network):
    """Run every stage that the operators say should run. Never raises.

    One deadline covers the whole line rather than each stage, so `a; b; c`
    cannot take three times the timeout the caller asked for. A stage killed on
    the clock ends the line: the budget is spent, and carrying on would run the
    rest of it with no time to run it in.
    """
    reports = []
    previous = None
    started = time.monotonic()
    deadline = started + seconds
    for index, stage in enumerate(stages):
        rendered = _describe_stage(stage)
        operator = stage.operator if index else ""
        if not _should_run(operator, previous):
            reports.append(_StageReport(rendered,
                                        skipped=_skip_reason(operator, previous)))
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            reports.append(_StageReport(
                rendered,
                skipped="the command's time limit of %d seconds was already "
                        "spent" % int(seconds)))
            previous = None
            continue
        outcome = agent_sandbox.run_pipeline(
            stage.pipeline.commands, cwd=str(cwd), env=env,
            timeout=remaining, network=network)
        reports.append(_StageReport(rendered, outcome=outcome))
        previous = outcome.exit_code
        if outcome.killed:
            # The tree is dead and the budget is gone. Anything after this
            # would be run with no time, and reporting it as skipped for the
            # honest reason is better than reporting it as failed.
            for later in stages[index + 1:]:
                reports.append(_StageReport(
                    _describe_stage(later),
                    skipped="the command was stopped on its time limit"))
            break
    return reports, time.monotonic() - started


def _format_run(reports, elapsed, rendered, shown_cwd, level, seconds):
    """The result the model reads. The exit code is never inferred."""
    lines = ["$ %s" % _summarise(rendered)]
    ran = [report for report in reports if report.outcome is not None]

    if len(reports) > 1:
        for index, report in enumerate(reports, 1):
            if report.outcome is None:
                lines.append("[%d/%d] %s -- not run: %s"
                             % (index, len(reports), _summarise(report.rendered, 200),
                                report.skipped))
                continue
            lines.append("[%d/%d] %s -- %s in %.1fs"
                         % (index, len(reports), _summarise(report.rendered, 200),
                            _ended(report.outcome.exit_code),
                            report.outcome.duration))

    # The line's exit code is the last stage that actually RAN, which is what a
    # shell reports and what the model has to reason about. A stage that was
    # skipped has no code to give and must not be allowed to supply one.
    last = ran[-1].outcome if ran else None
    if last is None:
        lines.append("Nothing ran. cwd: %s | sandbox: %s" % (shown_cwd, level))
    elif last.exit_code is None:
        # No exit code is the absence of evidence, never a failure code
        # invented to fill the slot: `agent_sandbox` reports None when a
        # command did not run to completion, and collapsing that into a number
        # would make "it was stopped on the clock" and "it failed" the same
        # answer.
        lines.append("no exit code in %.1fs | cwd: %s | sandbox: %s"
                     % (elapsed, shown_cwd, level))
        if last.killed:
            # The number is the one the CALLER asked for, not the one the last
            # stage happened to be given: a line's stages share one deadline,
            # so a stage started late is handed what is left of it, and
            # quoting that would tell the model its 60-second limit was 58.
            lines.append("It did not run to completion: it was still running "
                         "after the %d second limit, and its whole process "
                         "tree was stopped." % int(seconds))
        else:
            lines.append("It did not run to completion: %s"
                         % (last.error or "the command did not finish"))
    else:
        lines.append("exit %d in %.1fs | cwd: %s | sandbox: %s"
                     % (last.exit_code, elapsed, shown_cwd, level))

    for report in ran:
        for sentence in (report.outcome.degraded or ()):
            lines.append("Note: %s" % sentence)

    text = _joined_output(ran)
    if text:
        lines.append("--- output ---")
        lines.append(text)
    else:
        lines.append("--- no output ---")
    for report in ran:
        outcome = report.outcome
        if outcome.truncated:
            lines.append("(the command produced %d characters; the last %d are "
                         "above)" % (outcome.total_output, len(outcome.output)))
    return "\n".join(lines)


def _joined_output(reports):
    """Every stage's output, in order, bounded to the sandbox's own cap."""
    parts = []
    many = len([r for r in reports if (r.outcome.output or "").strip()]) > 1
    for index, report in enumerate(reports, 1):
        text = report.outcome.output or ""
        if not text:
            continue
        if many:
            parts.append("[%d] %s" % (index, text))
        else:
            parts.append(text)
    joined = "".join(parts)
    if len(joined) > agent_sandbox.MAX_OUTPUT:
        joined = joined[-agent_sandbox.MAX_OUTPUT:]
    return joined.rstrip("\n")


def _run(command, cwd, timeout, network, approve):
    """The `run` operation, end to end."""
    stages, refusal = _parse(command)
    if refusal:
        return refusal
    directory, refusal = _resolve_cwd(cwd)
    if refusal:
        return refusal
    root = _root()
    _expand(stages, directory)
    rendered = _describe(stages)

    mode = _network_mode(network)
    decision, refusal = _authorise(stages, directory, root, mode, approve,
                                   rendered)
    if refusal:
        return refusal

    seconds = _timeout(timeout, agent_sandbox.DEFAULT_TIMEOUT,
                       agent_sandbox.MAX_TIMEOUT)
    env = agent_sandbox.build_env(root, mode)
    reports, elapsed = _run_line(stages, directory, env, seconds, mode)
    level = _level(reports)
    text = _format_run(reports, elapsed, rendered,
                       _shown_cwd(directory, root), level, seconds)
    return text if not decision.note else "%s\n%s" % (decision.note, text)


def _level(reports):
    """The sandbox level the run actually had.

    Taken from an outcome rather than asked of the host, so what is reported is
    the level a process really ran under. Nothing ran means nothing to report,
    and `sandbox_level()` is then the honest answer to what this host can do.
    """
    for report in reports:
        if report.outcome is not None and report.outcome.level:
            return report.outcome.level
    return agent_sandbox.sandbox_level()


# --- the decision, and the one place an approval is asked for ---------------

class _Verdict:
    """A decision that was allowed to proceed, plus anything to say about it."""

    __slots__ = ("note",)

    def __init__(self, note=""):
        self.note = note


def _parse(command, operation=RUN):
    """(stages, refusal). Exactly one is falsy."""
    if not isinstance(command, str) or not command.strip():
        return None, _NO_COMMAND % operation
    try:
        return agent_shell.parse(command), ""
    except agent_shell.ShellError as error:
        return None, "FAILED: %s" % error
    except Exception as error:
        # The parser is not supposed to raise anything else. If it does, the
        # model gets a sentence rather than a traceback through the loop, for
        # the reason every other guard in this program returns words.
        return None, "FAILED: that command line could not be read (%s)." % error


def _authorise(stages, cwd, root, network, approve, rendered):
    """(verdict, refusal). Exactly one is falsy.

    The decision is asked ONCE, of `agent_policy.decide`, with the whole line.
    Nothing here re-reads the rules file afterwards and nothing re-decides: a
    DENY returns from `decide` before the rules are consulted at all, and a
    second lookup on this side would be precisely the branch that guarantee
    rests on not existing.
    """
    rules = _rules(root)
    try:
        decision = agent_policy.decide(stages, cwd=cwd, root=root,
                                       network=network, rules=rules)
    except Exception as error:
        # A policy that cannot answer has not permitted anything. The one
        # guard in this module that fails closed, and it is the one where an
        # open failure would be a permission nobody gave.
        return None, ("REFUSED: TMT could not decide whether that command may "
                      "run (%s), so it did not run. Nothing was changed."
                      % error)

    if decision.verdict == agent_policy.DENY:
        return None, "REFUSED: %s\nRule: %s" % (_without_prefix(decision.reason),
                                                decision.rule)
    if decision.verdict != agent_policy.ASK:
        return _Verdict(), ""

    pattern = _pattern(stages)
    if approve is None:
        return None, "%s\nRule: %s" % (_NO_TERMINAL % _without_prefix(decision.reason),
                                       decision.rule)
    question = _approval_question(decision, rendered,
                                  _shown_cwd(cwd, root), pattern)
    answer, failure = _ask(approve, question, pattern)
    if failure:
        return None, _APPROVER_FAILED % (_without_prefix(decision.reason), failure)
    if not answer:
        return None, "%s\nRule: %s" % (_DECLINED, decision.rule)
    note = ""
    if answer == "always":
        note = _remember(rules, pattern)
    return _Verdict(note), ""


def _without_prefix(reason):
    """A policy reason with its own DENIED: marker taken off.

    The result already says REFUSED on the line above; two markers in two
    sentences reads as two refusals.
    """
    text = str(reason or "")
    return text[len("DENIED:"):].strip() if text.startswith("DENIED:") else text


def _pattern(stages):
    """The rules-file pattern an approval would be remembered as.

    The FIRST command that is not plainly safe, because that is the one the
    question was about. `agent_policy.pattern_for` builds it; nothing here
    invents a pattern of its own, and a line whose commands are all recognised
    has nothing worth remembering.
    """
    for command in agent_policy.iter_commands(stages):
        try:
            decision = agent_policy.classify(command)
        except Exception:
            continue
        if decision.verdict == agent_policy.ASK:
            return agent_policy.pattern_for(command)
    return ""


def _rules(root):
    """The saved rules for this workspace, or None when they cannot be read.

    None rather than an empty `Rules`, so `agent_policy.decide` is handed
    nothing at all rather than an object claiming there are no rules. Failing
    to load can only ever make TMT ask more often, which is the safe
    direction.
    """
    try:
        return agent_policy.Rules.load(str(root))
    except Exception:
        return None


# --- background jobs --------------------------------------------------------

class Job:
    """One background command, its process, and its log.

    A job is ONE program. A pipeline or a `&&` line is refused at `start` with
    a sentence, because there would be no single thing to hand back an id for
    and no single thing to kill -- and half a stopped pipeline is exactly the
    process nobody notices is still running.
    """

    __slots__ = ("id", "rendered", "process", "log", "started_at",
                 "finished_at", "deadline", "exit_code", "ending", "cwd",
                 "level")

    def __init__(self, identifier, rendered, process, log, deadline, cwd, level):
        self.id = identifier
        self.rendered = rendered
        self.process = process
        self.log = log
        self.started_at = time.time()
        self.finished_at = None
        self.deadline = deadline
        self.exit_code = None
        self.ending = ""
        self.cwd = cwd
        self.level = level

    @property
    def running(self):
        return self.finished_at is None

    def elapsed(self):
        end = self.finished_at if self.finished_at is not None else time.time()
        return max(0.0, end - self.started_at)

    def settle(self, code, ending):
        """Record how this job ended. Idempotent, like every terminal move.

        The first ending wins. A job that times out while a `stop` is arriving
        is one job that ended once, and a second recording would change what
        the user is told happened without anything having happened twice.
        """
        if self.finished_at is not None:
            return
        self.finished_at = time.time()
        self.exit_code = code
        self.ending = ending
        try:
            self.process.release()
        except Exception:
            pass

    def describe(self):
        if self.running:
            return "job %s  running   %5.1fs  %s" % (
                self.id, self.elapsed(), _summarise(self.rendered, 120))
        return "job %s  %-9s %5.1fs  %s" % (
            self.id, self.ending, self.elapsed(),
            _summarise(self.rendered, 120))


_JOBS = []
_JOB_LOCK = threading.RLock()
_NEXT_ID = [1]


def jobs():
    """Every job this session started, oldest first. A copy."""
    with _JOB_LOCK:
        return list(_JOBS)


def _capacity():
    """How many jobs are running, derived and never maintained.

    `AgentManager._active_count_locked`'s discipline, for its reason: there is
    no counter to decrement twice and none to forget, so a job that is stopped,
    times out and exits at the same moment still frees exactly one slot --
    because it frees none, it simply stops being counted.
    """
    return len([job for job in _JOBS if job.running])


def _sweep():
    """Settle anything that has ended, and kill anything past its deadline.

    Called at the top of every entry into this module: filtered on read, with
    no timer thread to cancel and nothing to leak. It is the same discipline
    the agent register's five-second retention and its expiry sweep use, and a
    test can move the clock instead of waiting an hour.
    """
    with _JOB_LOCK:
        for job in _JOBS:
            if not job.running:
                continue
            try:
                code = job.process.poll()
            except Exception:
                code = None
            if code is not None:
                job.settle(int(code), "exit %d" % int(code))
                continue
            if job.deadline is not None and time.time() >= job.deadline:
                _kill(job)
                job.settle(None, "timeout")
        _retain()


def _retain():
    """Drop the oldest finished jobs once there are too many to keep.

    Only finished ones, and never a running one whatever the count says: a job
    dropped from the registry is a job nothing would stop at the end of the
    session, which is the one thing this must not produce.
    """
    finished = [job for job in _JOBS if not job.running]
    for job in finished[:max(0, len(finished) - MAX_RETAINED)]:
        _JOBS.remove(job)


def _kill(job):
    """Stop a job's whole process tree. Never raises."""
    try:
        job.process.kill_tree()
    except Exception:
        pass


def _log_directory(root):
    return Path(agent_sandbox.sandbox_home(root)) / "logs"


def _start(command, cwd, timeout, network, approve):
    """The `start` operation: run something long-lived and hand back an id."""
    stages, refusal = _parse(command, START)
    if refusal:
        return refusal
    if len(stages) > 1:
        return _JOB_ONE_COMMAND % "more than one stage (&&, || or ;)"
    commands = stages[0].pipeline.commands
    if len(commands) > 1:
        return _JOB_ONE_COMMAND % "a pipeline"
    if commands[0].redirects:
        return _JOB_NO_REDIRECT

    directory, refusal = _resolve_cwd(cwd)
    if refusal:
        return refusal
    root = _root()
    _expand(stages, directory)
    rendered = _describe(stages)

    with _JOB_LOCK:
        if _capacity() >= MAX_JOBS:
            return _JOB_LIMIT % MAX_JOBS

    mode = _network_mode(network)
    decision, refusal = _authorise(stages, directory, root, mode, approve,
                                   rendered)
    if refusal:
        return refusal

    seconds = _timeout(timeout, JOB_DEFAULT_TIMEOUT, JOB_MAX_TIMEOUT)
    env = agent_sandbox.build_env(root, mode)
    with _JOB_LOCK:
        # Re-checked inside the lock with the id taken in the same breath, so
        # two threads cannot both see the last free slot.
        if _capacity() >= MAX_JOBS:
            return _JOB_LIMIT % MAX_JOBS
        identifier = str(_NEXT_ID[0])
        _NEXT_ID[0] += 1

    log = _log_directory(root) / ("job-%s.log" % identifier)
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        handle = open(str(log), "wb")
    except OSError as error:
        return ("FAILED: the job's log file could not be opened (%s), so it "
                "was not started -- a background job whose output nobody can "
                "read is a job nobody can check." % error)

    try:
        # stdout and stderr to the same handle, so the log reads in the order a
        # terminal would have shown it. The parent's handle is closed
        # immediately: the child holds its own copy, and a handle left open
        # here is one the log could not be replaced under later.
        process = agent_sandbox.launch(
            commands[0].argv, cwd=str(directory), env=env, timeout=seconds,
            stdout=handle, stderr=handle)
    except agent_sandbox.SandboxError as error:
        return "FAILED: %s" % error
    except Exception as error:
        return "FAILED: the job could not be started (%s)." % error
    finally:
        try:
            handle.close()
        except OSError:
            pass

    job = Job(identifier, rendered, process, log,
              time.time() + seconds, _shown_cwd(directory, root),
              getattr(process, "level", agent_sandbox.sandbox_level()))
    with _JOB_LOCK:
        _JOBS.append(job)

    lines = ["Started job %s: %s" % (identifier, _summarise(rendered)),
             "in %s | sandbox: %s | it is stopped after %d seconds, and at the "
             "end of the session whatever happens"
             % (job.cwd, job.level, int(seconds)),
             "Read what it prints with "
             "{\"action\":\"bash\",\"operation\":\"logs\",\"id\":\"%s\"} and "
             "stop it with "
             "{\"action\":\"bash\",\"operation\":\"stop\",\"id\":\"%s\"}."
             % (identifier, identifier)]
    if decision.note:
        lines.insert(0, decision.note)
    return "\n".join(lines)


def _find(identifier):
    """(job, refusal). A single job may be named by having no others."""
    with _JOB_LOCK:
        current = list(_JOBS)
    text = "" if identifier is None else str(identifier).strip()
    if not text:
        if len(current) == 1:
            return current[0], ""
        return None, ""
    for job in current:
        if job.id == text:
            return job, ""
    return None, _NO_SUCH_JOB % (text, _known_jobs(current))


def _known_jobs(current):
    if not current:
        return _NO_JOBS
    return "The jobs this session has are: %s." % ", ".join(
        "%s (%s)" % (job.id, "running" if job.running else job.ending)
        for job in current)


def _status(identifier):
    """The `status` operation. Every job, or one of them."""
    current = jobs()
    if identifier is not None and str(identifier).strip():
        job, refusal = _find(identifier)
        if refusal:
            return refusal
        current = [job]
    if not current:
        return ("%s Start one with "
                "{\"action\":\"bash\",\"operation\":\"start\",\"command\":"
                "\"...\"}." % _NO_JOBS)
    lines = ["%d of %d background job slots in use."
             % (_running_count(), MAX_JOBS)]
    for job in current:
        lines.append(job.describe())
        lines.append("        in %s | sandbox: %s | log: %s"
                     % (job.cwd, job.level, job.log.name))
    return "\n".join(lines)


def _running_count():
    with _JOB_LOCK:
        return _capacity()


def _logs(identifier):
    """The `logs` operation. Works for a job that has already exited.

    That is when it matters most: a server that died two seconds after it
    started has printed exactly the thing worth reading, and a log that were
    only readable while the process lived would lose it.
    """
    job, refusal = _find(identifier)
    if refusal:
        return refusal
    if job is None:
        return _NO_JOB_ID % (LOGS, _known_jobs(jobs()))
    text, error = _read_tail(job.log)
    header = "job %s (%s): %s" % (
        job.id, "running" if job.running else job.ending,
        _summarise(job.rendered, 200))
    if error:
        return "%s\n%s" % (header, error)
    if not text.strip():
        return "%s\nIt has printed nothing yet." % header
    return "%s\n--- log (last %d characters) ---\n%s" % (header, len(text), text)


def _read_tail(path):
    """(text, error). The end of a log file, decoded permissively.

    Read from the end rather than whole, so a server that has been running for
    an hour costs a seek instead of its whole log, and decoded with `replace`
    because a tail can begin in the middle of a multi-byte character and a log
    is not worth a UnicodeDecodeError.
    """
    try:
        size = os.path.getsize(str(path))
        with open(str(path), "rb") as handle:
            if size > LOG_TAIL:
                handle.seek(size - LOG_TAIL)
            data = handle.read()
    except OSError as error:
        return "", "Its log could not be read (%s)." % error
    return data.decode("utf-8", "replace").replace("\r\n", "\n"), ""


def _stop(identifier):
    """The `stop` operation: kill the job's whole process tree."""
    job, refusal = _find(identifier)
    if refusal:
        return refusal
    if job is None:
        return _NO_JOB_ID % (STOP, _known_jobs(jobs()))
    if not job.running:
        return ("Job %s had already ended (%s). Its log is still readable with "
                "the logs operation." % (job.id, job.ending))
    _kill(job)
    job.settle(None, "stopped")
    return ("Stopped job %s and everything it started: %s\nIt ran for %.1fs. "
            "Its log is still readable with the logs operation."
            % (job.id, _summarise(job.rendered, 200), job.elapsed()))


def shutdown():
    """Stop every background job. Called at exit, and by the session.

    Registered with `atexit` below AND public, because a job that outlives the
    session is the one thing background execution must not produce. It is
    total and never raises: this runs while the process is going away, and an
    exception on the way out must not be what decides whether something is
    left running.

    `agent_sandbox.kill_all` is a second net under the same promise -- every
    process this module started went through `agent_sandbox.launch`, so it is
    in that module's own live set as well.
    """
    stopped = 0
    for job in jobs():
        if not job.running:
            continue
        _kill(job)
        job.settle(None, "stopped")
        stopped += 1
    return stopped


atexit.register(shutdown)


# --- the action -------------------------------------------------------------

def bash(command=None, operation=RUN, cwd=None, timeout=None, id=None,
         network=None, approve=None):
    """Run a command line, or manage a background job. Always returns a string.

    `id` shadows a builtin and is named that because the action's key is
    `id`; the builtin is not used anywhere in this module, and a parameter
    named for the key it carries is worth more here than one renamed to avoid
    a name nobody reaches for.

    `approve` is the caller's, and this module never reads a terminal itself.
    Its shape is `approve(question, pattern)` -- `approve(question)` is
    accepted too -- returning True to run it once, the string "always" to run
    it and remember the rule, and anything else to refuse. **Absent means
    no.** A piped run, the suite and every background agent arrive here with
    no approver, and all three are refused with the rule that asked.

    Every refusal is a sentence saying what was refused and what would work
    instead. None of them describes a way round a guard.
    """
    _sweep()
    chosen = str(operation or RUN).strip().lower()
    if chosen not in OPERATIONS:
        return _UNKNOWN_OPERATION % (operation, ", ".join(OPERATIONS))
    if chosen in _NEEDS_COMMAND and not (isinstance(command, str)
                                         and command.strip()):
        return _NO_COMMAND % chosen
    try:
        if chosen == RUN:
            return _run(command, cwd, timeout, network, approve)
        if chosen == START:
            return _start(command, cwd, timeout, network, approve)
        if chosen == STATUS:
            return _status(id)
        if chosen == LOGS:
            return _logs(id)
        return _stop(id)
    except KeyboardInterrupt:
        # Ctrl-C belongs to the loop that already catches it. Nothing this
        # module started may survive on the way past.
        shutdown()
        raise
    except Exception as error:
        # Anything unforeseen comes back as a sentence rather than as an
        # exception through the step loop: an action that raises ends a turn,
        # and a turn ended by a command tool is work thrown away over
        # something the model could have been told about and worked around.
        return ("FAILED: the bash action could not complete (%s: %s). Nothing "
                "is known to have run to completion."
                % (type(error).__name__, error))
