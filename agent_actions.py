"""Action dispatch and conversation-context helpers."""

import re
from collections import Counter

import agent_ui
from agent_config import MUTATING_ACTIONS
from agent_execution import RUNNERS, open_app
from agent_file_ops import (
    append_file, copy_file, create_folder, delete_file, delete_folder, list_files,
    patch_file, read_file, read_lines, replace_lines, safe_path,
    write_file, write_files,
)

# A push is the only action that leaves the machine and cannot be quietly
# undone, so it needs the user's own words behind it. These patterns run over
# the task text the human typed, never over anything the model produced.
#
# "push" has to read as a command, not as a subject. "push to main" asks for
# one; "the push button is broken" and "why did the push fail" only mention
# one, and arming the gate on those would hand a push to a task that never
# asked for it. Noun uses are struck out before intent is tested, so a task
# that both mentions and requests a push still authorizes it.
_PUSH_NOUN = r"\b(?:the|a|an|this|that|any|each|every|its|last|first)\s+push(?:es)?\b"
_PUSH_LEAD = r"(?:^|[.;:!?,]\s*|\b(?:and|then|also|please|now|afterwards|can you|could you|would you|you)\s+)"
_PUSH_OBJECT = r"(?:\s+(?:it|this|that|these|those|them|everything|all|up|to|the|my|our|changes|commit|commits)\b|\s*$)"
_PUSH_INTENT = (
    _PUSH_LEAD + r"push(?:es|ing)?\b",
    r"\bpush(?:es|ing)?\b" + _PUSH_OBJECT,
    # "publish" counts only alongside something git-shaped: publishing a
    # document is an ordinary file task, not a request to reach a remote.
    r"\bpublish(?:es|ing)?\b[^.?!]{0,30}\b(?:git|github|gitlab|bitbucket|remote|origin|upstream|branch|repo|repository)\b",
    r"\b(?:send|upload|sync)\b[^.?!]{0,30}\b(?:github|gitlab|bitbucket|remote|origin|upstream)\b",
)
# A negator anywhere near the verb withdraws the authorization outright.
_PUSH_NEGATION = r"\b(?:do not|don't|dont|never|without|no|avoid|skip)\b[^.?!]{0,24}\b(?:push|publish)"

PUSH_BLOCKED = (
    "BLOCKED: the user did not ask for a push in this task. A commit was not sent to "
    "the remote. Tell the user what is ready to push and ask them to confirm."
)

def authorizes_push(task):
    """Whether the user's own words asked for a push.

    A conservative safety gate on the human's request, not a command parser:
    the model still decides whether to push, this only decides whether it is
    allowed to. False is always safe -- it downgrades to asking.
    """
    text = str(task or "").replace("\u2019", "'").lower()
    if re.search(_PUSH_NEGATION, text):
        return False
    text = re.sub(_PUSH_NOUN, " ", text)
    return any(re.search(pattern, text) for pattern in _PUSH_INTENT)

def _run_git(operation):
    """Run a git operation and return its result as a plain string.

    agent_git is imported here rather than at module scope so this module still
    loads when git support is absent, and so a GitError -- the expected failure
    for every unset identity, missing repository or rejected push -- comes back
    as an action result the model can react to instead of an exception.
    """
    try:
        import agent_git
    except Exception as error:
        return f"Git support is unavailable: {error}"
    git_error = getattr(agent_git, "GitError", None)
    try:
        return operation(agent_git)
    except Exception as error:
        if git_error is not None and isinstance(error, git_error):
            return f"Git error: {error}"
        raise

def _run_tool(module_name, operation):
    """Run one of the repository-understanding tools and return its result.

    Imported here rather than at module scope for the reason `_run_git` gives:
    a module that is missing or fails to import must degrade to an action
    result the model can read and work around, not an exception that ends the
    session. These tools are the ones most likely to be absent -- an editable
    install freezes its module list at install time, so a module added to the
    source tree is invisible to `tmtcode` until pyproject.toml is updated, and
    the failure that produces should say so rather than crash.

    A ValueError is the sandbox refusing a path outside the workspace, which
    is a fact the model needs in words rather than a traceback.
    """
    try:
        module = __import__(module_name)
    except Exception as error:
        return f"{module_name} is unavailable: {error}"
    try:
        return operation(module)
    except ValueError as error:
        return f"Refused: {error}"


# --- background agents ------------------------------------------------------
#
# Six verbs the main agent uses to delegate, and one a background agent ends
# on. Everything they touch lives behind `context["manager"]`, which the
# session loop puts there and a background agent's own context deliberately
# does not have. So every branch below starts from the same question -- is
# there a manager? -- and answers a missing one in words rather than raising:
# a worker that asked to spawn a worker must be told it cannot, and an
# AttributeError on None would end the whole run instead.

# How long a wait blocks before it gives up and says so. Ten minutes: long
# enough for a real delegated task, short enough that a session cannot be
# hung forever by a worker stuck on a socket that will never answer. It
# returns "still running" rather than a failure, because a worker that has
# not finished has not failed.
DEFAULT_WAIT_TIMEOUT = 600.0

_NO_MANAGER = (
    "Background agents are not available here, so '%s' did nothing. Nothing was "
    "started, nothing was stopped, and no agent state changed. Carry out the work "
    "yourself with the ordinary file, search and git actions."
)


def _manager(context):
    """The agent register for this call, or None when there is not one.

    `(context or {}).get(...)` rather than `context["manager"]` because a
    background agent's context has no such key AT ALL -- not a None under it
    -- and the main loop's may not either on an install where the manager was
    never wired in.
    """
    return (context or {}).get("manager")


# The three verbs the user has to authorise before the model may use them.
# Named here rather than derived at the call site so the dispatcher's own list
# and `agent_capabilities.CAPABILITIES` can be asserted equal by a test: two
# spellings of the same set are two chances for one to grow a member the other
# does not know about, and the member that went missing would be a capability
# running unauthorised.
_CAPABILITY_ACTIONS = frozenset(("plan", "review", "verify"))


def _capability_refusal(context, action):
    """The refusal for an unauthorised capability, or "" when it may run.

    Reads the authorisation out of the action context, which is where the
    session put its own `Capabilities` object -- the one it adopts from the
    user's typed line at `begin_turn` and from nothing else.

    A context with NO capabilities key refuses all three, and that direction
    is deliberate. It is what a background agent's context looks like, so a
    worker cannot verify its own work even if it were somehow handed the verb;
    it is what an install that never wired the session in looks like; and it
    is what a caller that forgot the key looks like. In every one of those the
    honest answer is that nobody authorised anything, and the cost of being
    wrong is a task done with the ordinary actions.

    Guarded to a refusal rather than to an exception for the reason every
    other check in this module is: a capability the model asked for wrongly is
    a mistake to correct on the next step, never a reason to end the turn.
    """
    try:
        import agent_capabilities
    except Exception:
        # The frozen-module-list failure `_run_tool` guards against. A
        # capability whose authorisation cannot be READ has not been granted,
        # so this fails closed -- the one guard in this file that does, and it
        # is the one where an open failure would be a permission nobody gave.
        return ("Capability authorisation is unavailable, so '%s' did not "
                "run. Carry out the work with the ordinary file, search and "
                "git actions." % action)
    return agent_capabilities.refusal((context or {}).get("capabilities"),
                                      action)


_NO_PLAN_STATE = (
    "Planning is not available here, so '%s' did nothing and no plan exists. "
    "Carry out the work with the ordinary file, search and git actions, and "
    "say what you did in your final end_conversation."
)


def _plan_state(context):
    """The plan for this turn, or None when there is not one.

    `(context or {}).get(...)` for the reason `_manager` gives: a background
    agent's context has no such key at all, and neither has an install where
    the session never wired one in. Both must come back as words the model can
    work around rather than as a KeyError that ends the turn.
    """
    return (context or {}).get("plan")


def _review_veto(context, plan, obj):
    """The refusal for a plan update the review will not allow, or "".

    Guarded to nothing at every step. A turn with no review state, an install
    where `agent_review` will not import, or a veto that raises all let the
    update through: keeping a plan current is how the user knows where the
    work is, and a refinement on that display must never be able to stop it.
    """
    review = _review_state(context)
    if review is None:
        return ""
    try:
        import agent_review
        return agent_review.plan_veto(review, plan, obj)
    except Exception:
        return ""


def _verify_veto(context, plan, obj):
    """The refusal for a plan update verification will not allow, or "".

    The same shape as `_review_veto` and guarded to nothing in the same way,
    and asked FIRST of the two: verification comes before review in the
    pipeline, so a plan whose verification step is outstanding should be told
    about that rather than about a review it has not reached yet.
    """
    verify = _verify_state(context)
    if verify is None:
        return ""
    try:
        import agent_verify
        return agent_verify.plan_veto(verify, plan, obj)
    except Exception:
        return ""


def _plan(context, obj):
    """Run one `plan` operation against the turn's plan.

    The operation names are read here rather than in agent_plan so that module
    stays pure state, and every failure comes back as a sentence: a plan call
    the model got wrong is a mistake to correct on the next step, exactly like
    a patch whose search string did not match, and never a reason to stop.
    """
    plan = _plan_state(context)
    operation = str(obj.get("operation", "")).strip().lower()
    if plan is None:
        return _NO_PLAN_STATE % (operation or "plan")
    try:
        import agent_plan
    except Exception as error:
        # The frozen-module-list failure `_run_tool` guards against, answered
        # in words for the same reason.
        return "Planning is unavailable: %s" % error
    if operation not in agent_plan.OPERATIONS:
        return ("FAILED: '%s' is not a plan operation. Use one of: %s."
                % (obj.get("operation"), ", ".join(agent_plan.OPERATIONS)))
    # A review step cannot be completed by saying it is complete. Checked here
    # rather than inside agent_plan so that module stays pure state and knows
    # nothing about reviews, and refused BEFORE the operation runs so a vetoed
    # update leaves no trace of having happened -- the same rule the loop's
    # own gate follows for a refused respond.
    #
    # It is a refinement and not the guarantee, and `agent_review.plan_veto`
    # says so in its own docstring: it rests on the step's title naming
    # review, which a model can avoid. What cannot be avoided is the gate on
    # the final answer, which is driven by ReviewState and does not care what
    # any step is called. This keeps the plan on screen honest; that keeps the
    # answer honest.
    # Verification first, then review: that is the order of the pipeline, so a
    # plan with both steps outstanding is told about the one it has to do
    # next rather than the one after it.
    vetoed = _verify_veto(context, plan, obj) or _review_veto(context, plan, obj)
    if vetoed:
        return vetoed
    try:
        if operation == "create":
            return plan.create(obj.get("steps"))
        if operation == "update":
            return plan.update(obj.get("step", obj.get("id")),
                               obj.get("status"), obj.get("title"),
                               updates=obj.get("steps"))
        if operation == "add":
            return plan.add(obj.get("title"), after=obj.get("after"))
        if operation == "remove":
            return plan.remove(obj.get("step", obj.get("id")))
        if operation == "clear":
            return plan.clear()
        return plan.describe()
    except agent_plan.PlanError as error:
        return "FAILED: %s" % error
    except Exception as error:
        # A plan is a convenience the turn can survive losing. Whatever went
        # wrong in there, the model is told and the work carries on -- a
        # corrupted plan must never take the session with it.
        return "FAILED: the plan could not be updated (%s: %s)." % (
            type(error).__name__, error)


# --- the independent review ------------------------------------------------
#
# One verb, and it BLOCKS. The session loop is synchronous and has no event
# loop to suspend into, so a review is an action that takes a while rather
# than a state the loop enters -- the same shape `wait_for_agents` already
# has, and for the same reason. Blocking is also what makes the snapshot
# stable: while this call is out, the main agent is not running actions, so
# nothing it does can move the tree the reviewer is reading.

_NO_REVIEW_STATE = (
    "Independent review is not available here, so 'review' did nothing and "
    "nothing has been reviewed. Do not claim the work was reviewed. Check the "
    "change yourself with git_diff and the file tools, and say in your final "
    "message that no independent review ran."
)

_REVIEW_NEEDS_AGENTS = (
    "Independent review needs background agents and they are not available "
    "here, so 'review' did nothing and nothing has been reviewed. Do not "
    "claim the work was reviewed."
)

_WORKERS_STILL_RUNNING = (
    "REFUSED: %d background agent(s) are still running (%s), and they can "
    "write to this workspace while the reviewer reads it -- the review would "
    "be of a state that never existed. Collect them with wait_for_agents (or "
    "stop them with kill_agent), then request the review again."
)

_REVIEW_AGENDA_ELSEWHERE = (
    "REFUSED: 'review_agenda' belongs to the independent reviewer and is "
    "applied inside its own run. It writes to that reviewer's checklist, and "
    "you do not have one. Nothing was changed. Start a review with "
    "{\"action\":\"review\"} if this task needs one."
)

# The values "scope" understands. One, for now, and a wrong one is named
# rather than ignored: a model that asked for "changed_files" and silently got
# a whole-task review would draw the wrong conclusion from the answer.
REVIEW_SCOPES = ("current_task",)


def _review_state(context):
    """The review state for this turn, or None when there is not one.

    `(context or {}).get(...)` for the reason `_manager` and `_plan_state`
    give: a background agent's context has no such key at all, and neither has
    an install where the session never wired one in. Both must come back as
    words rather than a KeyError that ends the turn.
    """
    return (context or {}).get("review")


def _verification_text(review):
    """What actually ran this turn, as the reviewer is told it.

    An observation and never a verdict. TMT does not read a program's output
    for whether it succeeded -- that is the rule that once called a green test
    run a failure -- so this says what was executed and leaves what it proves
    to the agent that can read the output.
    """
    ran = review.verification
    if not ran:
        return ("Nothing was executed in this session. There is no evidence "
                "here that the change was verified; treat it as unverified "
                "unless you find evidence in the repository itself.")
    return ("These ran in this session (that they ran is all TMT recorded -- "
            "what they proved is yours to judge):\n%s"
            % "\n".join("  " + line for line in ran))


def _review_snapshot(agent_review, context, obj, review):
    """The repository state this review is taken against.

    Built once, here, at the moment the review starts, and held in memory --
    nothing of TMT's is written into the workspace. Every git call is guarded
    by `_run_git`, so a repository that cannot be read becomes a sentence in
    the brief rather than an exception: a workspace with no git at all is a
    perfectly reviewable workspace, and refusing to review it would be
    refusing the wrong thing.
    """
    plan = _plan_state(context)
    paths = obj.get("paths") if isinstance(obj.get("paths"), (list, tuple)) else None
    notes = []
    claim = str(obj.get("notes") or obj.get("focus") or "").strip()
    if claim:
        # Carried, and labelled as a CLAIM. The implementing agent is allowed
        # to point the reviewer at something; it is not allowed to be believed
        # about it, and the reviewer's rules say so in the same words.
        notes.append("The implementing agent says: %s\n(That is its own claim "
                     "about its own work. Check it against the repository "
                     "rather than accepting it.)" % claim)
    if paths:
        notes.append("The diff below was narrowed to: %s. Widen it yourself "
                     "with git_diff if that looks incomplete."
                     % ", ".join(str(p) for p in paths))
    return agent_review.ReviewSnapshot(
        task=str((context or {}).get("task") or ""),
        plan_text=plan.describe() if plan is not None else "",
        plan_complete=bool(plan.is_complete()) if plan is not None else False,
        root=_run_git(lambda g: str(g.TMTGit.discover().root)),
        status=_run_git(_git_status),
        diff=_run_git(lambda g: g.TMTGit.discover().diff(paths=paths)),
        stat=_run_git(lambda g: g.TMTGit.discover().diff_stat(paths=paths)),
        commit=_run_git(lambda g: g.TMTGit.discover().head()),
        changed_paths=review.changed_paths,
        verification=_verification_text(review),
        notes=notes)


def _attach_agenda(record, review):
    """Give this reviewer a fresh agenda, and point the review state at it.

    A new one per review, not one per task: a second cycle is a second
    reviewer with its own list, and carrying the first one's ticked items over
    would show a review as most of the way through work it has not started.
    The finished list of the review before it is in that review's own result,
    which is where a finished thing belongs.

    Guarded to nothing, deliberately. Everything here is a readout, and the
    review runs identically without it.
    """
    try:
        import agent_reviewbot
        agenda = agent_reviewbot.Agenda()
        record.agenda = agenda
        review.agenda = agenda
        return agenda
    except Exception:
        return None


def _note_review_cost(review, record):
    """Tell the review state what its reviewer cost. Guarded to nothing.

    `exact` is both halves or neither, the rule the corner meter follows: a
    total that mixes one figure the provider reported with one this runtime
    estimated is an estimate, and it is drawn with a leading `~` to say so.

    Every failure here is swallowed. A readout must never be the thing that
    turns a completed review into a failed one.
    """
    try:
        elapsed = record.elapsed(record.finished_at)
        review.note_cost(
            tokens=record.total_tokens(),
            exact=bool(record.tokens_in_exact and record.tokens_out_exact),
            seconds=elapsed)
    except Exception:
        pass


def _review(context, obj):
    """Run one independent review and return what it found.

    The whole lifecycle in one call, because the loop has nowhere to park a
    half-finished one: refuse or begin, snapshot, spawn a read-only reviewer,
    block on it, parse what it said, settle the state, report.

    Every failure between those steps lands in `ReviewState.fail`, which is
    the ERROR state, which blocks the final answer. That is section 21 of the
    brief and it is the single most important line in this function: a
    reviewer that crashed, timed out or returned nonsense must never be
    mistaken for a reviewer that approved the work.
    """
    review = _review_state(context)
    if review is None:
        return _NO_REVIEW_STATE
    try:
        import agent_review
    except Exception as error:
        # The frozen-module-list failure `_run_tool` guards against. It is
        # reported as an unavailable review rather than a passed one, and the
        # state is untouched, so the gate goes on holding the answer.
        return "Independent review is unavailable: %s" % error
    scope = str(obj.get("scope") or REVIEW_SCOPES[0]).strip().lower()
    if scope not in REVIEW_SCOPES:
        return ("FAILED: '%s' is not a review scope. Use one of: %s."
                % (obj.get("scope"), ", ".join(REVIEW_SCOPES)))
    agent_manager_mod, agent_worker_mod, problem = _agent_modules()
    if problem:
        return problem
    manager = _manager(context)
    if manager is None:
        return _REVIEW_NEEDS_AGENTS
    running = [record for record in manager.list() if not record.is_terminal()]
    if running:
        # Section 22. A reviewer reading a tree that another agent is writing
        # to is reviewing a state that never existed, and every finding it
        # made would be about a moment that had already passed.
        return _WORKERS_STILL_RUNNING % (
            len(running), ", ".join("#%s" % record.id for record in running))
    held = review.begin()
    if held:
        return held
    try:
        snapshot = _review_snapshot(agent_review, context, obj, review)
    except Exception as error:
        return "FAILED: %s" % review.fail(
            "the review snapshot could not be built (%s: %s)"
            % (type(error).__name__, error))
    review.snapshot = snapshot
    try:
        record = manager.spawn(snapshot.describe(), kind="review",
                               model=obj.get("model"), effort=obj.get("effort"))
    except Exception as error:
        return "FAILED: %s" % review.fail(
            "the reviewer could not be created (%s: %s)"
            % (type(error).__name__, error))
    # The checklist the reviewer will declare into, attached BEFORE the thread
    # exists so there is no window in which the reviewer has started and its
    # readout has nowhere to go. One object with two references: the record is
    # what the reviewer's own thread writes through, and the review state is
    # what outlives the record -- a copy would leave the strip drawing one and
    # `/review` printing the other.
    #
    # Guarded to nothing. A readout that cannot be built must never be what
    # stops a review: without it the reviewer's `review_agenda` calls come
    # back as sentences saying so, and the strip falls back to the bar, the
    # tokens and the activity label, all of which are measured either way.
    _attach_agenda(record, review)
    started = manager.start(
        record, lambda rec, mgr: agent_worker_mod.run_review(rec, mgr))
    if started is None:
        return "FAILED: %s" % review.fail(
            "the reviewer could not be started; it is %s" % record.status)
    seconds = _timeout(obj)
    finished = manager.wait([record.id], timeout=seconds)
    # What that reviewer cost, recorded before anything can go wrong with what
    # it said. It is the register's own measurement of a thread it owned, and
    # it is put on the review state because the record ages off the strip five
    # seconds after it finishes while the state outlives the turn -- so
    # "what did that review cost" stops being a question that is only
    # answerable while nobody is asking it.
    #
    # Recorded on every path, including a reviewer that timed out or returned
    # nonsense: those cost exactly as much as a good one, and leaving the
    # figure off the failures would make reviews look cheaper than they are.
    _note_review_cost(review, record)
    if record.id not in finished:
        # Killed rather than abandoned. A reviewer still reading while the
        # main agent resumes editing would be reporting on a tree that has
        # moved, and its answer could arrive long after the finding was true.
        manager.kill(record.id)
        return "FAILED: %s" % review.fail(
            "the reviewer did not report within %d seconds and was stopped "
            "(its last activity was %r)" % (int(seconds), record.activity or "none"))
    if record.status != agent_manager_mod.Status.COMPLETED:
        return "FAILED: %s" % review.fail(
            "the reviewer stopped without reviewing: %s"
            % (record.error or "it gave no reason"))
    answer = (manager.result(record.id) or "").strip()
    if not answer:
        return "FAILED: %s" % review.fail("the reviewer produced no result")
    try:
        result = agent_review.parse_result(answer, number=review.cycles + 1)
    except agent_review.ReviewError as error:
        # THE line that keeps section 15 honest. Text that does not validate
        # is not a review, whatever it claims about itself, and it leaves the
        # task in ERROR rather than passing.
        return "FAILED: %s" % review.fail(
            "the reviewer's result could not be read: %s" % error)
    review.settle(result)
    return result.describe()


def _read_only_refusal(context, action):
    """The delegation contract's refusal for this action here, or "".

    The second of the two layers enforcing read-only, and the rule itself is
    NOT restated: it asks `agent_delegation.refusal`, which is the same
    function `agent_worker` asks before dispatch. One rule, two places that
    enforce it, because two copies of a security policy are two policies.

    Imported at call time, for the reason `_run_tool` and `_agent_modules`
    import at call time: a module missing from a frozen install's list must
    come back as behaviour this function can describe rather than as an
    ImportError at the top of the file that stops TMT starting at all.

    An unreadable `agent_delegation` FAILS CLOSED for a context that says it is
    read-only: the delegation was constrained, the rule cannot be consulted,
    and letting the write through would be the one outcome the constraint
    exists to prevent. A context with no `read_only` key is not the unreadable
    case -- it is every ordinary caller in TMT, and it is untouched.
    """
    if not (context or {}).get("read_only"):
        return ""
    try:
        import agent_delegation
    except Exception as error:
        return ("CONSTRAINT VIOLATION\n\nThis delegation is read-only and the "
                "rule that says what that permits could not be loaded (%s), so "
                "'%s' was refused rather than run. Finish with "
                "internal_response and say what you would have changed."
                % (error, action))
    return agent_delegation.refusal(
        agent_delegation.DelegationConstraints(read_only=True), action)


def _agent_modules():
    """(agent_manager, agent_worker), or (None, None) with a sentence.

    Imported at call time for the reason `_run_tool` gives: an editable
    install freezes its module list, so a module present in the source tree
    is invisible to the installed entry point until pyproject.toml catches
    up. That has to come back as an action result the model can work around,
    not an ImportError at the top of this file that stops TMT starting at all.
    """
    try:
        import agent_manager
        import agent_worker
        return agent_manager, agent_worker, ""
    except Exception as error:
        return None, None, "Background agents are unavailable: %s" % error


# --- verification -----------------------------------------------------------
#
# One verb, and it BLOCKS -- the same shape `review` and the two wait verbs
# have, and for the same reason: the session loop is synchronous and has no
# event loop to suspend into, so running a test suite is an action that takes
# a while rather than a state the loop enters. Blocking is also what keeps the
# evidence meaningful: while this call is out the main agent is not editing,
# so the tree the commands ran against is the tree that was reported on.
#
# Unlike `review`, no model is involved anywhere. Discovery reads files, the
# engine runs argv lists, and the statuses are exit codes. That is what makes
# section 18's guarantee absolute rather than approximate: there is no text
# anywhere on this path for a model to write.

_NO_VERIFY_STATE = (
    "Verification is not available here, so 'verify' did nothing and nothing "
    "has been verified. Do not claim the work was verified. Check what you can "
    "with bash and the file tools, and say in your final message that no "
    "verification ran."
)

# The values "scope" understands. One, for now, and a wrong one is named
# rather than ignored: a model that asked for "changed_files" and silently got
# a whole-task verification would draw the wrong conclusion from the answer.
VERIFY_SCOPES = ("current_task",)


def _verify_state(context):
    """The verification state for this turn, or None when there is not one.

    `(context or {}).get(...)` for the reason `_manager`, `_plan_state` and
    `_review_state` give: a background agent's context has no such key at all,
    and neither has an install where the session never wired one in. Both must
    come back as words rather than a KeyError that ends the turn.
    """
    return (context or {}).get("verify")


def _verify_level(obj):
    """(level or None, refusal). A level the model asked for, validated."""
    raw = obj.get("level")
    if raw in (None, ""):
        return None, ""
    try:
        level = int(raw)
    except (TypeError, ValueError):
        return None, ("FAILED: \"level\" must be a whole number from 1 to 6, "
                      "not %r." % (raw,))
    if not 1 <= level <= 6:
        return None, ("FAILED: \"level\" must be from 1 (basic) to 6 (full "
                      "regression); %d is outside that." % level)
    return level, ""


def _verify(context, obj):
    """Run one verification and return what the checks actually said.

    The whole lifecycle in one call, because the loop has nowhere to park a
    half-finished one: refuse or begin, inspect the repository, choose the
    checks, run them in order, settle the state, report.

    Every failure between those steps lands in `VerificationState.fail`, which
    is the ERROR state, which blocks the final answer. A verification that
    crashed, timed out or could not choose a command must never be mistaken
    for one that found the work sound.
    """
    verify = _verify_state(context)
    if verify is None:
        return _NO_VERIFY_STATE
    try:
        import agent_verify_engine
    except Exception as error:
        # The frozen-module-list failure `_run_tool` guards against. Reported
        # as unavailable rather than passed, and the state is untouched, so
        # the gate goes on holding the answer.
        return "Verification is unavailable: %s" % error
    scope = str(obj.get("scope") or VERIFY_SCOPES[0]).strip().lower()
    if scope not in VERIFY_SCOPES:
        return ("FAILED: '%s' is not a verification scope. Use one of: %s."
                % (obj.get("scope"), ", ".join(VERIFY_SCOPES)))
    level, refused = _verify_level(obj)
    if refused:
        return refused
    paths = obj.get("paths") if isinstance(obj.get("paths"), (list, tuple)) else None
    result, held = agent_verify_engine.verify(
        verify, paths=paths, level=level, full=bool(obj.get("full", False)),
        timeout=_timeout(obj) if obj.get("timeout") else None,
        # The screen's own nudge. The session loop puts a refresh callable
        # here so the column can show checks finishing while this call blocks;
        # a context without one simply does not repaint, which is what every
        # scripted and piped run does anyway.
        on_change=(context or {}).get("refresh"))
    if result is None:
        return held
    # What ran is put in the REVIEW's brief, because the reviewer's job
    # includes judging whether the verification was the right verification.
    # It is stated as fact rather than quoted, which is allowed here and
    # nowhere else: these are commands TMT chose and exit codes TMT read, not
    # a program's opinion of itself.
    review = _review_state(context)
    if review is not None:
        try:
            review.note_run("verify", agent_verify_engine.review_note(result))
        except Exception:
            pass
    return result.describe()


def _agent_id(obj):
    """The id key as a string. Models write 2 as often as they write "2"."""
    value = obj.get("id")
    if isinstance(value, bool) or value is None:
        return ""
    return str(value).strip()


def _timeout(obj):
    """The wait timeout, defaulting and refusing nonsense rather than raising."""
    value = obj.get("timeout", None)
    if value is None:
        return DEFAULT_WAIT_TIMEOUT
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_WAIT_TIMEOUT
    # A zero or negative timeout is a poll, which is a legitimate thing to
    # ask for; an enormous one is not, and would hang a session on a typo.
    return max(0.0, min(seconds, DEFAULT_WAIT_TIMEOUT))


def _agent_line(record, now=None):
    """One agent's state, as a line. Every figure is one the record holds.

    Token counts are marked `~` unless the provider reported them, which is
    what `tokens_in_exact` and `tokens_out_exact` record. An unmarked estimate
    would be TMT stating a number it guessed.
    """
    marks = ("" if record.tokens_in_exact else "~",
             "" if record.tokens_out_exact else "~")
    line = ("#%s %s -- %s (%s%d tokens in, %s%d out)"
            % (record.id, record.status, record.activity or "no activity yet",
               marks[0], record.tokens_in, marks[1], record.tokens_out))
    contract = _contract_line(record)
    if contract:
        line += "\n    contract: %s" % contract
    if record.paths:
        line += "\n    wrote: %s" % ", ".join(record.paths)
    blocked = _violations_line(record)
    if blocked:
        line += "\n    blocked: %s" % blocked
    if record.error:
        line += "\n    error: %s" % record.error
    return line


def _contract_line(record):
    """`READ ONLY  TIMEOUT 10:00  (4:12 left)  reports: diff, summary`, or "".

    Empty for an unconstrained delegation, which is what keeps `agent_status`
    identical to the report it produced before contracts existed. Guarded
    throughout: a status line is a readout, and a readout must never be able
    to end the action that is producing it.
    """
    try:
        import agent_delegation
        constraints = getattr(record, "constraints", None)
        if constraints is None or constraints.is_default():
            return ""
        parts = list(constraints.chips())
        left = record.remaining()
        if left is not None and not record.is_terminal():
            parts.append("(%s left)" % agent_delegation.clock_text(left))
        names = constraints.report.names()
        if names:
            parts.append("reports: " + ", ".join(names))
        return "  ".join(parts)
    except Exception:
        return ""


def _violations_line(record):
    try:
        import agent_delegation
        return agent_delegation.violations_line(getattr(record, "violations", ()))
    except Exception:
        return ""


def _worker_diff(record):
    """What git says about the files THIS delegation wrote, or "".

    Limited to `record.paths` -- the paths the worker's own actions named --
    and that limit is the whole of section 46. The main agent goes on working
    while a worker runs, and several workers can run at once, so the
    repository's whole diff is emphatically not one delegation's work. Asking
    git about the files this one actually touched is the most that can be
    attributed to it, and it is derived from the repository rather than from
    anything the worker said about itself.

    "" when it wrote nothing, so the report says "No workspace changes" from
    the fact rather than from an empty git answer that could also mean git was
    unavailable.
    """
    paths = tuple(getattr(record, "paths", ()) or ())
    if not paths:
        return ""
    try:
        import agent_git
        return agent_git.TMTGit.discover().diff(paths=list(paths))
    except Exception as error:
        # A repository that is not a repository, or a git that is not there.
        # Reported as what it is rather than as "no changes", which would be a
        # claim about the workspace TMT is in no position to make.
        return "(the diff could not be read: %s)" % error


def delegation_result(record):
    """One delegation's structured result, built from state and not from prose.

    Everything in it is either measured by the runtime -- the status, the
    timing, the step count, the paths the worker's own actions named, the
    operations its contract refused -- or is the worker's own
    `internal_response`, which goes in `summary` and nowhere else. That
    division is sections 17, 18 and 19 in one function: a file list is never
    read out of a sentence, a diff is never a claim, and the one field that IS
    the model's words is named as such.

    The diff is fetched only when the contract asked for one. It is a git
    subprocess, and running it for every collected worker would spend it on
    reports nobody asked for.
    """
    import agent_delegation
    import agent_manager
    constraints = getattr(record, "constraints", None) or agent_delegation.DEFAULT
    status = _result_status(record, agent_manager, agent_delegation)
    want_diff = False
    try:
        want_diff = bool(constraints.report.diff)
    except Exception:
        want_diff = False
    return agent_delegation.DelegationResult(
        record.id, status, task=getattr(record, "task", ""),
        constraints=constraints,
        # The worker's own report, or -- when it never produced one -- the
        # manager's honest sentence about why there is none. `result()`
        # already falls back to the error for exactly this reason.
        summary=(getattr(record, "result", "") or getattr(record, "error", "")),
        inspected=getattr(record, "reads", ()),
        changed=getattr(record, "paths", ()),
        diff=_worker_diff(record) if want_diff else "",
        errors=getattr(record, "error", ""),
        violations=getattr(record, "violations", ()),
        duration=_record_duration(record),
        started_at=getattr(record, "started_at", None),
        finished_at=getattr(record, "finished_at", None),
        steps=getattr(record, "steps", None))


def _record_duration(record):
    """How long this delegation ran, in seconds, or None.

    Read through the record's own `elapsed`, which already stops at
    `finished_at` -- a duration that went on counting after the work stopped
    would be reporting time the work did not take.
    """
    try:
        import time as _time
        return record.elapsed(_time.monotonic())
    except Exception:
        return None


def _result_status(record, agent_manager, agent_delegation):
    """The delegation's outcome word from the record's lifecycle status.

    The one translation between the two vocabularies, and they are separate on
    purpose: `agent_manager.Status` is about a thread's lifecycle and
    `agent_delegation`'s words are about a contract's outcome. Keeping them
    apart is what lets the manager go on calling a stop a "kill" while the
    report calls it what the main agent needs to hear, which is "cancelled".

    A delegation that was refused every write it tried and produced nothing is
    reported as a CONSTRAINT VIOLATION rather than as a completion, because
    "completed" would tell the main agent the work is done when the contract
    is the reason it is not. A delegation that hit a violation and finished
    anyway is COMPLETED with the violations listed -- section 8's "do not
    automatically mark the whole worker as failed unless the violation makes
    successful completion impossible".
    """
    status = getattr(record, "status", "")
    if status == agent_manager.Status.TIMED_OUT:
        return agent_delegation.TIMED_OUT
    if status == agent_manager.Status.KILLED:
        return agent_delegation.CANCELLED
    if status == agent_manager.Status.FAILED:
        if getattr(record, "violations", ()) and not getattr(record, "paths", ()):
            return agent_delegation.CONSTRAINT_VIOLATION
        return agent_delegation.FAILED
    if status == agent_manager.Status.COMPLETED:
        return agent_delegation.COMPLETED
    return agent_delegation.RUNNING


def _report_text(manager, record):
    """What one finished delegation is reported as, to the main agent.

    An unconstrained delegation is reported EXACTLY as it was before contracts
    existed -- same sentence, same quoting of the worker's own words -- which
    is section 4 held to at the one place a change would be most visible. A
    constrained one gets the structured result instead, carrying only the
    sections its contract asked for.
    """
    try:
        constraints = getattr(record, "constraints", None)
        structured = constraints is not None and not constraints.is_default()
    except Exception:
        structured = False
    if structured:
        try:
            return delegation_result(record).describe()
        except Exception:
            # A report that cannot be assembled must not lose the worker's
            # own words, which are the thing that was actually asked for.
            structured = False
    result = manager.result(record.id) or "(no report)"
    return "Background agent #%s (%s) reported:\n%s" % (
        record.id, record.status, result)


def _spawn_agent(manager, obj):
    """Create a background worker under its contract and start it.

    The contract is parsed BEFORE anything is registered, so a malformed one
    leaves no agent behind at all -- section 38's "do not partially start a
    worker with half-valid constraints". A model that wrote `"timeout": 600`
    instead of `"timeout_seconds"` is told so and spawns nothing, rather than
    being handed an untimed worker it believes has ten minutes.
    """
    agent_manager, agent_worker, problem = _agent_modules()
    if problem:
        return problem
    task = obj.get("task")
    if not isinstance(task, str) or not task.strip():
        return ("spawn_agent needs a 'task': the instruction the background "
                "agent is to carry out, written as a whole piece of work it can "
                "finish on its own without asking anyone anything.")
    try:
        import agent_delegation
    except Exception as error:
        return ("Background agents are unavailable: the delegation contract "
                "could not be loaded (%s)." % error)
    constraints, refused = agent_delegation.parse(obj.get("constraints"))
    if refused:
        return refused
    try:
        record = manager.spawn(task.strip(), model=obj.get("model"),
                               effort=obj.get("effort"),
                               constraints=constraints)
    except agent_manager.CapacityError as error:
        # A structured refusal, not silence. The sentence names the cap and
        # says what to do about it, because the party reading it is a model
        # and a bare failure is something it will simply retry.
        return str(error)
    if manager.start(record, lambda rec, mgr: agent_worker.run_worker(rec, mgr)) is None:
        return ("Background agent #%s could not be started; it is %s."
                % (record.id, record.status))
    # The contract is repeated back, so the main agent's own record of what it
    # delegated is the runtime's reading of the object rather than the object
    # it sent. A model that asked for a constraint it did not get would
    # otherwise carry on believing it had one.
    contract = constraints.describe()
    said = ("Started background agent #%s on: %s" % (record.id, record.task))
    if contract:
        said += "\nUnder this contract, enforced by TMT and not by asking:\n%s" % (
            "\n".join("    " + line for line in contract.splitlines()))
    running, cap = manager.capacity()
    return (said + "\nIt runs on its own from here (%d of %d workers running). "
            "Carry on with other work, then use wait_for_agents to collect what "
            "it produced, or agent_status to see how it is getting on."
            % (running, cap))


def _agent_status(manager, obj):
    """One agent's state, or every worker's."""
    agent_id = _agent_id(obj)
    if agent_id:
        record = manager.inspect(agent_id)
        if record is None:
            return "There is no background agent with id %r." % agent_id
        return _agent_line(record)
    records = manager.list()
    if not records:
        return ("No background agents have been started in this session. Use "
                "spawn_agent to delegate a piece of work.")
    running, cap = manager.capacity()
    header = ("%d background agent(s), %d of %d worker slots running:"
              % (len(records), running, cap))
    return "\n".join([header] + [_agent_line(record) for record in records])


def _agent_result(manager, obj):
    """What one agent produced, or why there is nothing yet."""
    agent_id = _agent_id(obj)
    record = manager.inspect(agent_id)
    if record is None:
        return "There is no background agent with id %r." % agent_id
    if not record.is_terminal():
        line = ("Background agent #%s has not finished; it is %s and its last "
                "activity was %r. Use wait_for_agent to block until it does."
                % (record.id, record.status, record.activity))
        contract = _contract_line(record)
        return line + ("\n    contract: %s" % contract if contract else "")
    result = manager.result(record.id)
    if not result and not _is_constrained(record):
        return ("Background agent #%s is %s and produced no report."
                % (record.id, record.status))
    return _report_text(manager, record)


def _is_constrained(record):
    """Whether this delegation was given a contract at all.

    What it gates is whether a delegation that produced no sentence is
    reported as "produced no report" or as a structured result. A constrained
    one always gets the structured result, even with no report requirements
    and nothing to quote, because the structure carries the thing that
    sentence cannot: WHY there is no report. A delegation stopped at its
    deadline after seventeen actions and one that crashed on its first are
    both "no report" in the old sentence and are different facts -- which is
    the collapse sections 14, 21 and 44 all forbid.
    """
    try:
        return not record.constraints.is_default()
    except Exception:
        return False


def _wait_report(manager, records, finished):
    """What a wait returned: each finished agent verbatim, each one still out.

    The response is quoted rather than summarised. It is the whole of what a
    worker produced and the only thing it produced, and a paraphrase here
    would be TMT restating a report it did not write.
    """
    lines, waiting = [], []
    for record in records:
        if record.id not in finished:
            waiting.append(record)
            continue
        lines.append(_report_text(manager, record))
    if waiting:
        lines.append("Still running after the wait: %s. Their work is not lost "
                     "-- wait for them again, or collect them later with "
                     "agent_result."
                     % ", ".join("#%s (%s)" % (record.id, record.activity or record.status)
                                 for record in waiting))
    # Two agents that wrote the same file is the one concurrency fact the main
    # agent cannot work out for itself and does need to know. There is no lock
    # manager and no transaction; this is the whole of that story, and it is
    # enough to send someone to look.
    clashes = manager.conflicts()
    if clashes:
        lines.append("Two or more agents wrote the same file:\n" + "\n".join(
            "    %s -- agents %s" % (path, ", ".join("#" + one for one in ids))
            for path, ids in clashes))
    return "\n\n".join(lines) if lines else "Nothing to report."


def _wait_for_agent(manager, obj):
    agent_id = _agent_id(obj)
    record = manager.inspect(agent_id)
    if record is None:
        return "There is no background agent with id %r." % agent_id
    finished = manager.wait([record.id], timeout=_timeout(obj))
    return _wait_report(manager, [record], finished)


def _wait_for_agents(manager, obj):
    ids = obj.get("ids")
    if isinstance(ids, (list, tuple)) and ids:
        wanted = [str(one).strip() for one in ids if str(one).strip()]
        records = [record for record in (manager.inspect(one) for one in wanted)
                   if record is not None]
        missing = [one for one in wanted if manager.inspect(one) is None]
    else:
        # No ids named means every worker this session started. Finished ones
        # return at once, so this is also how a main agent collects results it
        # never got round to reading.
        records, missing = list(manager.list()), []
    if not records:
        if missing:
            return "None of those ids is a background agent: %s." % ", ".join(missing)
        return ("No background agents have been started in this session, so "
                "there was nothing to wait for.")
    finished = manager.wait([record.id for record in records], timeout=_timeout(obj))
    report = _wait_report(manager, records, finished)
    if missing:
        report += "\n\nNo agent has the id(s): %s." % ", ".join(missing)
    return report


def _kill_agent(manager, obj):
    agent_id = _agent_id(obj)
    record = manager.inspect(agent_id)
    if record is None:
        return "There is no background agent with id %r." % agent_id
    if not manager.kill(record.id):
        return ("Background agent #%s was already %s; nothing was stopped."
                % (record.id, record.status))
    # Said exactly, because the guarantee is exact and a larger claim would be
    # false: a thread cannot be terminated and a stream has no abort, so a
    # request already in flight still finishes arriving.
    return ("Stopped background agent #%s. It will run no further action. A "
            "request already in flight may still complete, and anything it had "
            "already written is still written -- it wrote: %s."
            % (record.id, ", ".join(record.paths) if record.paths else "nothing"))


MAX_STATUS_PATHS = 40

def _git_status(agent_git):
    """Report the changed paths by name.

    Counts alone are useless to the model: it cannot commit "one untracked
    item", and an untracked file appears in no diff, so a name reported here is
    the only way it can ever learn one.
    """
    repo = agent_git.TMTGit.discover()
    state = repo.status()
    lines = [
        f"Repository: {state.get('root') or repo.root}",
        f"Branch: {state.get('branch', 'unknown')}",
    ]
    if state.get("clean"):
        lines.append("Working tree clean; there is nothing to commit.")
        return "\n".join(lines)
    for key, label in (("staged", "Staged"), ("unstaged", "Modified"), ("untracked", "Untracked")):
        paths = state.get(key) or []
        if not paths:
            continue
        shown = paths[:MAX_STATUS_PATHS]
        listed = ", ".join(shown)
        if len(paths) > len(shown):
            listed += f", and {len(paths) - len(shown)} more"
        lines.append(f"{label} ({len(paths)}): {listed}")
    return "\n".join(lines)

# A diff of a large change would otherwise arrive whole: it goes into the
# model's context and is relayed live to the user at the same time, so one
# refactor could crowd out the rest of the task on both. The engine already
# caps a diff far higher; this is the size a reply is still built from.
MAX_DIFF_RESULT_CHARS = 6000

def _clip_diff(diff):
    """Cap a diff at MAX_DIFF_RESULT_CHARS, cutting on a line boundary.

    The note says how much was dropped, so the model can tell a partial diff
    from a complete one and narrow the next one with "paths" instead of
    reasoning about changes it never saw.
    """
    if len(diff) <= MAX_DIFF_RESULT_CHARS:
        return diff
    shown = diff[:MAX_DIFF_RESULT_CHARS].rsplit("\n", 1)[0]
    omitted = diff[len(shown):].count("\n")
    return (
        f"{shown}\n"
        f"... diff truncated: {len(shown)} of {len(diff)} characters shown, "
        f"{omitted} further lines omitted. Re-run git_diff with \"paths\" set to "
        "the files you need in order to see the rest."
    )

def _git_diff(agent_git, obj):
    return _clip_diff(agent_git.TMTGit.discover().diff(paths=obj.get("paths")))

def _git_commit(agent_git, obj):
    result = agent_git.TMTGit.discover().commit(
        obj["message"], paths=obj.get("paths"), stage_all=bool(obj.get("all", False))
    )
    files = result.get("files") or []
    return (
        f"Committed {result.get('short', '')} on {result.get('branch', '')} "
        f"as {result.get('author', '')}\n"
        f"Files ({len(files)}): {', '.join(files) if files else 'none listed'}"
    )

def _git_push(agent_git, obj):
    result = agent_git.TMTGit.discover().push(branch=obj.get("branch"), remote=obj.get("remote"))
    return (
        f"Pushed {result.get('branch', '')} to {result.get('remote', '')} "
        f"({result.get('remote_url_host', 'unknown host')}): {result.get('summary', '')}"
    )

# --- the project's persistent context ---------------------------------------
#
# `project_context` reaches TMT_Context/notes.md and TMT_Context/progress.md,
# the two markdown files in the user's own repository that carry what TMT knows
# about this project between sessions. One verb with an "operation", the shape
# `plan` and `review_agenda` already use, because every operation acts on the
# same pair of files.
#
# Three of the four operations are reads or narrow writes. There is deliberately
# NO operation that replaces a file, and no key that carries one: every write
# names a section, and `agent_context.Document` puts back every byte outside
# that section. A model that could hand over a whole file could destroy a
# user's own notes in one call, and no amount of prompt wording makes that safe.

CONTEXT_OPERATIONS = ("show", "note", "progress", "check")

_NO_CONTEXT_STATE = (
    "The persistent project context is not available in this run, so there is "
    "nothing to read or write. Carry on with the task; nothing is lost except "
    "what would have been remembered for next time."
)

_CONTEXT_OFF = (
    "Persistent project context is turned off in Settings, so TMT_Context is "
    "neither read nor written. Do not try again this session -- the setting is "
    "the user's and only they can change it, in the Settings menu."
)

_CONTEXT_USAGE = (
    "FAILED: %r is not a project_context operation. Use one of: "
    "show (read what is remembered), note (record how the project works, in "
    "notes.md), progress (record what was done or what remains, in "
    "progress.md), check (list notes that name paths which no longer exist)."
)

_CONTEXT_NEEDS_SECTION = (
    "FAILED: a %s operation needs a \"section\" and \"content\". The sections "
    "are: %s."
)


def _context_state(context):
    """The project context for this session, or None when there is not one.

    `(context or {}).get(...)` for the reason `_manager`, `_plan_state` and
    `_review_state` give: a background agent's context has no such key at all,
    and neither has an install where the session never wired one in. Both come
    back as words rather than as a KeyError that ends the turn.

    That absence is also the whole of the worker isolation for this verb.
    `agent_worker` refuses `project_context` outright, and even if it did not,
    a worker's context carries no `context` key -- so the two-sided isolation
    `plan`, `review` and `verify` have is here too, by construction rather than
    by wording.
    """
    return (context or {}).get("context")


def _project_context(context, obj):
    """Run one `project_context` operation against this project's memory.

    The operation names are read here rather than in `agent_context` so that
    module stays a document model with a filesystem behind it, and every
    failure comes back as a sentence: a call the model got wrong is a mistake
    to correct on its next step, exactly like a patch whose search string did
    not match, and never a reason to stop the task.
    """
    state = _context_state(context)
    if state is None:
        return _NO_CONTEXT_STATE
    try:
        import agent_context
    except Exception as error:
        # The frozen-module-list failure `_run_tool` guards against, answered
        # the same way: an unavailable memory is reported and the task carries
        # on, because nothing about this feature is worth failing a turn for.
        return "The persistent project context is unavailable: %s" % error
    if not agent_context.enabled():
        return _CONTEXT_OFF
    operation = str(obj.get("operation") or "").strip().lower()
    if operation not in CONTEXT_OPERATIONS:
        return _CONTEXT_USAGE % (obj.get("operation"),)

    if operation == "show":
        if not state.available:
            return ("There is no project context yet for %s. It is created on "
                    "the first task of a session; if this is that task it may "
                    "not have been written when you asked." % state.root)
        return state.describe()

    if operation == "check":
        stale = state.stale_notes()
        if not stale:
            return ("Every path the notes name still exists in the workspace. "
                    "That is not proof the notes are right -- only that they "
                    "are not obviously out of date.")
        return ("These paths are named in %s/%s but are not in the workspace: "
                "%s.\nRead the repository to find out what replaced them, then "
                "correct the note with a project_context note operation. Do "
                "not act on a path that is not there."
                % (agent_context.CONTEXT_DIR_NAME, agent_context.NOTES_NAME,
                   ", ".join(stale)))

    sections = (agent_context.NOTES_SECTIONS if operation == "note"
                else agent_context.PROGRESS_SECTIONS)
    section = str(obj.get("section") or "").strip()
    content = obj.get("content") or obj.get("text") or obj.get("note") or ""
    if not section or not str(content).strip():
        return _CONTEXT_NEEDS_SECTION % (operation, ", ".join(sections))
    # "append" unless the model says otherwise, which is the safe default in
    # the one direction that matters: appending cannot lose a line somebody
    # else wrote, and replacing can. A model that means to correct a stale
    # note says `"mode": "replace"` and is then replacing one SECTION, never a
    # file.
    mode = str(obj.get("mode") or "append").strip().lower()
    if mode not in ("append", "replace", "line"):
        return ("FAILED: %r is not a mode. Use append (add to the section, "
                "the default), replace (rewrite that one section, for "
                "correcting something stale) or line (add one list item, "
                "skipped if it is already there)." % mode)
    if operation == "note":
        result = state.update_notes(section, content, mode=mode)
    else:
        result = state.update_progress(section, content, mode=mode)
    return str(result) if result.ok else "FAILED: %s" % result


# --- running commands -------------------------------------------------------
#
# One verb, and it is the only way a model runs anything at all. It goes
# through `_run_tool` for both of that helper's guarantees, and this branch
# needs them more than any other: `agent_bash` pulls in the parser, the policy
# and the sandbox behind it, so it is the deepest import in the program, and a
# module missing from a frozen editable install has to come back as a sentence
# the model can work around rather than an exception that ends the session. A
# ValueError -- the workspace sandbox refusing a cwd or a redirect target --
# comes back as words for the same reason.
#
# Everything about what may run is decided inside `agent_bash`; nothing about
# it is decided here. This function's whole job is to hand over the model's
# keys and the ONE thing the model cannot supply: the approval callable, which
# the session loop puts in the context and which is how an ASK verdict reaches
# a human. Absent -- a piped run, a test, a background agent's context -- it
# stays None, and `agent_bash` answers an approval nobody can give with no.
# That is the existing rule for raw terminal input in this program, arriving
# where it matters most.


def _bash(context, obj):
    """Run one command line, or report why it did not run."""
    # The legacy net's refusal, checked before anything else. `adopt_verb` puts
    # it here when it could translate a `run_file`'s VERB but not its meaning
    # -- a file whose extension has no known runner -- because inventing a
    # command for it is the one thing that must not happen. It can only ever
    # refuse, so a model writing the key itself buys nothing but a refusal.
    blocked = obj.get(LEGACY_BLOCKED_KEY)
    if isinstance(blocked, str) and blocked.strip():
        return blocked
    return _run_tool("agent_bash", lambda m: m.bash(
        command=obj.get("command"),
        operation=obj.get("operation") or "run",
        cwd=obj.get("cwd"),
        timeout=obj.get("timeout"),
        id=obj.get("id"),
        network=obj.get("network"),
        approve=(context or {}).get("approve")))


# What a typed answer to a deletion question may be. Narrower than
# `agent_bash._YES` on purpose: "always" and "allow" are about remembering a
# command rule for next time, and there is nothing to remember about a file
# that is about to be gone.
_DELETE_YES = frozenset({"y", "yes"})


_IMAGE_ATTACHED = (
    "Attached %s. It is in this message: look at it and say what you see, "
    "then carry on with the task."
)


def _view_image(context, obj):
    """Read an image and attach it to the message this result goes back in.

    The only action whose result is not entirely text, and the seam it uses is
    the one `agent_multi` already uses for the same problem: a handler returns
    a string, so what cannot be a string is hung on the action object and the
    loop assembling the next request asks for it back. `agent_images.attach`
    is that, `agent_actions.result_content` is the loop's half of it, and
    every path that builds a result message goes through the second.

    The model's own model is asked FIRST, before the file is opened. Loading a
    three-megabyte image only to find out it cannot be sent wastes the read
    and, worse, produces a refusal that reads as though the file were the
    problem. The three answers are handled apart: False refuses and says what
    the user could change, True proceeds, and None -- which is most models --
    proceeds too, because a name table cannot know about a model released
    after it was written and refusing on that would be refusing on a guess.
    """
    path = obj.get("path")
    if not isinstance(path, str) or not path.strip():
        return "Refused: view_image needs a 'path' naming an image file."
    try:
        import agent_images
    except Exception as error:
        # The frozen-module-list failure `_run_tool` guards against, answered
        # in the same shape: a sentence the model can work around rather than
        # an exception that ends the turn.
        return "agent_images is unavailable: %s" % error
    # A worker may be running on a model the session is not, so its own is
    # asked for when the context names one. An absent key is the main agent,
    # whose model `agent_images` reads for itself at call time.
    model = (context or {}).get("model") or None
    named = agent_images.unavailable_reason(model_id=model)
    if named:
        return agent_images.UNSUPPORTED % (path, named)
    try:
        image = agent_images.load(path)
    except ValueError as error:
        return "Refused: %s" % error
    agent_images.attach(obj, [image])
    return _IMAGE_ATTACHED % image.label()


def result_content(text, objs):
    """The content value for a message reporting what these actions did.

    A STRING when nothing attached an image, and that is the property the rest
    of the program rests on: every existing call site built a string, every
    provider adapter has always been handed a string, and a turn that looked
    at no image produces byte-for-byte the request it produced before this
    existed. Only a turn that actually read an image gets the list form.

    Takes a LIST of action objects because a batch and a `multi_tool` both run
    several actions into one result message, and an image read by the third of
    them has to reach the same request as the text describing it.
    """
    try:
        import agent_images
    except Exception:
        return text
    return agent_images.parts(text, agent_images.gather(objs))


def _ask_user(context, obj):
    """Put a question to the user and hand back what they chose.

    An ordinary action: it returns a result and the turn carries on with it,
    which is the whole point. `end_conversation` is the only verb that ends a
    turn, and a model that had to end one to ask a question would throw away
    everything it had read to get there.

    Reached through `context["choose"]`, which the session builds because only
    the session knows whether there is a terminal. No `choose` -- a piped run,
    a script, the suite, any background agent -- is answered with
    `NO_TERMINAL`, never with a block: `agent_ask` says in as many words that
    nobody was asked and tells the model to decide and state its assumption,
    because a model left with no answer and no instruction asks again.

    Imported inside the function for `_run_tool`'s reason: an editable install
    freezes its module list, so a module in the source tree can be invisible to
    the entry point, and that must come back as a result the model can work
    around rather than as an exception that ends the session.
    """
    try:
        import agent_ask
    except Exception as error:
        return ("ask_user is unavailable here (%s: %s). Decide it yourself and "
                "say what you assumed." % (type(error).__name__, error))
    question, refused = agent_ask.parse(obj)
    if refused:
        return "REFUSED: " + refused
    choose = (context or {}).get("choose")
    if not callable(choose):
        return agent_ask.NO_TERMINAL
    # Measured here rather than in `agent_ask`, which is a pure function of a
    # width and must stay one: a module that read the terminal itself would be
    # doing it from every test that composes a question. 80 is what TMT
    # assumes everywhere else when it cannot measure.
    try:
        import shutil
        columns = shutil.get_terminal_size((80, 24)).columns
    except Exception:
        columns = 80
    try:
        key = choose(agent_ask.render(question, columns), question.keys())
    except KeyboardInterrupt:
        # The user stopping the turn. It belongs to the loop, which already
        # knows how to end one; swallowing it here would leave the question
        # answered by a keystroke that meant the opposite.
        raise
    except Exception:
        # A terminal that failed mid-question has not chosen anything, and an
        # exception out of here would end the session over a question.
        return agent_ask.DISMISSED
    return agent_ask.answer(question, key)


def _confirmation(context):
    """`confirm(question) -> bool` built from the context's approver, or None.

    The session puts one `approve` callable in the action context -- the one
    `agent_bash` asks about a command -- and a deletion asks through the same
    one, so the question is written inside the live region with the type-ahead
    reader stopped, rather than printed past it by a bare `input()`. A context
    with no approver (a test, a script, a direct call) gets None and
    `agent_file_ops` falls back to the console prompt it always had.

    Both approver shapes `agent_bash._ask` accepts are accepted here, and
    every failure is no: a callable that raises has not agreed to anything.
    """
    approve = (context or {}).get("approve")
    if not callable(approve):
        return None

    def confirm(question):
        try:
            answer = approve(question)
        except TypeError:
            try:
                answer = approve(question, "")
            except Exception:
                return False
        except Exception:
            return False
        if answer is True:
            return True
        return isinstance(answer, str) and answer.strip().lower() in _DELETE_YES
    return confirm


def execute_action(obj, context=None):
    """Run one action object and return its result.

    `context` carries per-task authority, currently {'push_authorized': bool}.
    Callers that pass nothing get the safe default: no push authority.
    """
    # Translated here as well as in the loops, so a caller that reaches this
    # function directly -- a worker, a test, anything -- gets the same verbs
    # the loop does. `adopt_verb` rather than `canonical_action`, because a
    # rename is not always the whole translation: `run_file` carries a `path`
    # and `bash` needs a `command` built from it, and `search_files` needs its
    # case-insensitivity put back. Naming the verb and leaving the keys behind
    # got the object HALF translated -- renamed, then refused for a key it was
    # never given -- which is the shape of half-net this comment's own promise
    # was written to rule out. `or obj["action"]` keeps the old behaviour for
    # an object with no action at all: a KeyError, raised where it always was.
    adopt_verb(obj)
    action = obj.get("action") or obj["action"]
    # The delegation contract's SECOND layer, asked before anything is
    # dispatched. `agent_worker` has already refused every mutating verb for a
    # read-only delegation before it got here; this is what refuses one that
    # reached the dispatcher another way -- a direct call, a test, a third
    # dispatch path somebody adds later.
    #
    # It is the `push_authorized` shape a hundred lines below, and it is that
    # shape on purpose: a returned sentence rather than a raised error, so a
    # model that reaches for a verb its contract does not carry is corrected on
    # its next step exactly like a patch whose search string did not match. The
    # key is ABSENT rather than False for an unconstrained delegation and for
    # every non-worker caller, so the guard is not even consulted on the paths
    # that existed before this feature.
    refused = _read_only_refusal(context, action)
    if refused:
        return refused
    if action == "write_file": return write_file(obj["path"], obj.get("content", ""))
    if action == "append_file": return append_file(obj["path"], obj.get("content", ""))
    if action == "write_files": return write_files(obj["files"])
    if action == "patch_file": return patch_file(obj["path"], obj.get("search", ""), obj.get("replace", ""))
    if action == "delete_file": return delete_file(obj["path"], confirm=_confirmation(context))
    if action == "read_file": return read_file(obj["path"])
    if action == "list_files": return list_files()
    if action == "read_lines": return read_lines(obj["path"], obj.get("start", 1), obj.get("end"))
    if action == "replace_lines": return replace_lines(obj["path"], obj["start"], obj["end"], obj.get("content", ""))
    if action == "copy_file": return copy_file(obj["path"], obj.get("to") or obj.get("new_path") or obj.get("dest", ""))
    if action == "delete_folder": return delete_folder(obj["path"], recursive=obj.get("recursive", False),
                                                       confirm=_confirmation(context))
    if action == "rename_file":
        old, new_name = safe_path(obj["path"]), obj.get("new_name") or obj.get("new_path", "")
        new = safe_path(new_name)
        if not old.exists(): return f"File not found: {obj['path']}"
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
        return f"Renamed {obj['path']} to {new_name}"
    if action == "create_folder": return create_folder(obj["path"])
    if action == "bash": return _bash(context, obj)
    if action == "ask_user": return _ask_user(context, obj)
    if action == "open_app": return open_app(obj["app"], file_path=obj.get("path"), url=obj.get("url"))
    if action == "git_status": return _run_git(_git_status)
    if action == "git_diff": return _run_git(lambda agent_git: _git_diff(agent_git, obj))
    if action == "git_identity": return _run_git(lambda agent_git: agent_git.TMTGitIdentity.resolve().describe())
    if action == "git_commit": return _run_git(lambda agent_git: _git_commit(agent_git, obj))
    if action == "git_push":
        # Both the model's structured request and the user's own task text must
        # authorize a push; missing context means unauthorized.
        if not (context or {}).get("push_authorized"):
            return PUSH_BLOCKED
        return _run_git(lambda agent_git: _git_push(agent_git, obj))
    # The three higher-level capabilities, and the one gate that stands in
    # front of all of them. Asked HERE, once, before any of the three
    # branches, because this is the only path a capability can be reached by:
    # the loop dispatches through this function on both its single and its
    # batch site, and a worker's own loop dispatches through it too.
    #
    # This is the second of the two layers, and it is the load-bearing one.
    # The first -- leaving the verb out of the system prompt -- makes an
    # unauthorised capability invisible, which is worth having and is not a
    # guarantee: a model can emit a verb it was never taught, a prompt can
    # fail to rebuild, and a later edit can leak a reference back in. This
    # one makes it unavailable, and it reads the flags the USER's own words
    # set rather than anything the model wrote.
    #
    # It is the `git_push` shape a few lines above, deliberately: a returned
    # sentence rather than a raised error, so a model that reaches for a
    # capability it was not given is corrected on its next step exactly like
    # a patch whose search string did not match. It is NOT converted into a
    # silent no-op -- the action genuinely does not run, and the result says
    # so and says what would change it.
    if action in _CAPABILITY_ACTIONS:
        refused = _capability_refusal(context, action)
        if refused:
            return refused
    # The task's own plan. It touches no file and runs nothing, so it is not
    # in MUTATING_ACTIONS and does not invalidate the cached system prompt --
    # the workspace is exactly as it was.
    if action == "plan": return _plan(context, obj)
    # The independent review. It touches no file -- the reviewer is refused
    # every writing verb before dispatch -- so it is not in MUTATING_ACTIONS
    # and the cached prompt still describes the workspace correctly. It does
    # BLOCK, for as long as the reviewer runs or the timeout allows, exactly
    # as the two wait verbs do.
    if action == "review": return _review(context, obj)
    # The reviewbot's own readout, and the one registered verb this dispatcher
    # deliberately does not carry out. It writes to the agenda hanging off one
    # reviewer's record, and the only thing that can find that record is the
    # manager -- which a background agent's context deliberately withholds, so
    # a worker cannot spawn workers of its own. `agent_worker` applies it
    # inside the loop that has both.
    #
    # Answered with a sentence rather than left to fall through to "Unknown
    # action", which would be a true statement that sends the reader looking
    # for a typo. This one names where the verb lives and who may use it.
    if action == "review_agenda": return _REVIEW_AGENDA_ELSEWHERE
    # The project's persistent memory. It DOES write files -- two markdown
    # files in the workspace -- but deliberately not to MUTATING_ACTIONS: the
    # cached system prompt describes the project's SOURCE, and TMT_Context is
    # TMT's notes about that source rather than part of it. Invalidating on
    # every note would rebuild the whole workspace snapshot (~8k tokens of walk
    # and inlining) to say exactly the same thing about exactly the same files.
    #
    # It is not in _CAPABILITY_ACTIONS either, and that is the deliberate
    # difference from the three above it: those spend a user's money and a
    # user's minutes, so they are authorised per prompt. This writes two files
    # the user can read and delete, so it is governed by a setting instead --
    # and the setting is checked inside the handler rather than here, so a run
    # with the feature off gets a sentence explaining that rather than silence.
    if action == "project_context": return _project_context(context, obj)
    # Verification. It writes no file of TMT's, so it is not in
    # MUTATING_ACTIONS and the cached prompt still describes the workspace --
    # but it DOES run the project's own commands, which is why it goes through
    # `agent_execution.run_command` and nothing else. It BLOCKS for as long as
    # the checks take, exactly as `review` and the two wait verbs do.
    if action == "verify": return _verify(context, obj)
    # Understanding the repository. Each answers one question and no more, so
    # the model can pick the narrowest tool instead of reading whole files to
    # find one line.
    if action == "tree":
        return _run_tool("agent_tree", lambda m: m.tree(
            obj.get("path"), obj.get("depth"), obj.get("limit")))
    # The two search verbs. Both go through `_run_tool` for its two guarantees,
    # and both need them: a module missing from a frozen editable install comes
    # back as a sentence the model can work around rather than an exception
    # that ends the session, and `safe_path` refusing a path outside the
    # workspace comes back as words rather than a traceback. Their `path` key
    # is the one the model is most likely to point somewhere it may not go.
    if action == "glob":
        return _run_tool("agent_glob", lambda m: m.glob(
            obj["pattern"], path=obj.get("path"), kind=obj.get("kind"),
            limit=obj.get("limit")))
    if action == "grep":
        return _run_tool("agent_grep", lambda m: m.grep(
            obj["query"], path=obj.get("path"), glob=obj.get("glob"),
            regex=bool(obj.get("regex", False)),
            ignore_case=bool(obj.get("ignore_case", False)),
            context=obj.get("context", 0), limit=obj.get("limit")))
    if action == "find_symbol":
        return _run_tool("agent_symbols", lambda m: m.find_symbol(
            obj["name"], kind=obj.get("kind"), path=obj.get("path"),
            limit=obj.get("limit")))
    # The two network tools, through `_run_tool` for both of its guarantees.
    # The first matters more here than anywhere else: `agent_web` is the
    # newest module in TMT, so it is the one most likely to be missing from a
    # frozen editable install, and "agent_web is unavailable" is a sentence
    # the model can work around where a traceback ends the session.
    #
    # Neither returns a refusal through ValueError -- `agent_web` catches its
    # own WebError and answers in words, because a search that failed and a
    # search that found nothing are different facts and only the module knows
    # which happened.
    if action == "web_search":
        return _run_tool("agent_web", lambda m: m.search(
            obj["query"], max_results=obj.get("max_results"),
            recency=obj.get("recency")))
    if action == "web_fetch":
        return _run_tool("agent_web", lambda m: m.fetch(
            obj["url"], timeout=obj.get("timeout")))
    if action == "view_image": return _view_image(context, obj)
    if action == "replace_across":
        # Preview unless the model explicitly asks to apply. A bulk edit it
        # did not look at first is how a repository gets wrecked, so the
        # default is the harmless one and saying nothing means changing
        # nothing.
        return _run_tool("agent_file_ops", lambda m: m.replace_across(
            obj["search"], obj["replace"], glob=obj.get("glob"),
            path=obj.get("path"), apply=bool(obj.get("apply", False))))
    if action == "code_map":
        return _run_tool("agent_index", lambda m: m.code_map(
            obj["target"], relation=obj.get("relation", "all")))
    if action == "related_tests":
        return _run_tool("agent_testsel", lambda m: m.related_tests(obj.get("path")))
    if action == "remember":
        return _run_tool("agent_memory", lambda m: m.remember(
            obj["note"], tags=obj.get("tags"), kind=obj.get("kind", "note")))
    if action == "recall":
        return _run_tool("agent_memory", lambda m: m.recall(
            query=obj.get("query"), limit=obj.get("limit"), kind=obj.get("kind")))
    # Several calls in one action. `agent_multi` owns the shape -- which
    # entries are usable, how a `for_each` template becomes calls, the ceiling
    # on how many, the budget on how much text comes back -- and runs nothing
    # itself: every call is dispatched back through THIS function with THIS
    # context, so a call inside a multi_tool meets exactly the guards the same
    # call meets on its own. The read-only contract, the capability gate, the
    # push authority and the command policy are all asked again per call,
    # because the list is a shape and not a dispatch path.
    #
    # Through `_run_tool` for its first guarantee: a frozen install whose
    # module list predates agent_multi answers in a sentence rather than
    # ending the session.
    if action == "multi_tool":
        return _run_tool("agent_multi", lambda m: m.run(
            obj, context, dispatch=lambda call: execute_action(call, context)))
    # Delegating to background agents. Every one of these needs the register
    # the session loop holds; without it they say so and change nothing.
    if action in ("spawn_agent", "agent_status", "agent_result",
                  "wait_for_agent", "wait_for_agents", "kill_agent"):
        manager = _manager(context)
        if manager is None:
            return _NO_MANAGER % action
        if action == "spawn_agent": return _spawn_agent(manager, obj)
        if action == "agent_status": return _agent_status(manager, obj)
        if action == "agent_result": return _agent_result(manager, obj)
        # These two BLOCK, here, inside the action, for as long as the timeout
        # allows. The session loop is synchronous and has no event loop to
        # suspend into, so waiting is an action that takes a while rather than
        # a state the loop enters. The screen stays alive because LiveRelay
        # repaints from its own thread, and a KeyboardInterrupt is deliberately
        # not caught anywhere on this path: Ctrl-C during a wait must abort it
        # and return to the prompt, which TMT.py already arranges.
        if action == "wait_for_agent": return _wait_for_agent(manager, obj)
        if action == "wait_for_agents": return _wait_for_agents(manager, obj)
        return _kill_agent(manager, obj)
    # A background agent's ending, and NOT a terminal action here. The main
    # loop ends a turn on `end_conversation` and on nothing else, so a main
    # model that somehow emitted this one gets an ordinary result and carries
    # on -- which is the whole point of it being a separate verb rather than a
    # flag.
    if action == "internal_response": return obj.get("response", "")
    # Never terminal. The loop shows the message and carries straight on; the
    # result exists only so the batch report has something to record.
    if action == SEND_MESSAGE: return obj.get("message", "")
    if action == END_CONVERSATION: return obj.get("message", "done")
    return f"Unknown action: {action}"

# --- the two verbs that talk to the user -----------------------------------

# The verb that says something and lets the turn go on.
SEND_MESSAGE = "send_message"
# The verb that says the last thing and ends the task. The only one that ends
# anything at all.
END_CONVERSATION = "end_conversation"

# What those two used to be called, and it is a SEMANTIC translation rather
# than a table of spellings -- `respond` meant either verb depending on a flag,
# so the flag is what decides which one it becomes.
#
# This is a narrow compatibility layer and it is deliberately NOT TAUGHT. No
# prompt mentions these names, no tool list offers them, and no error message
# suggests them; the only thing that knows them is this function. It exists
# because these two verbs are the ones a turn ENDS on, and a model that reached
# for the old name would otherwise spend its retry budget being told the name
# was wrong and finish having said nothing to the user. A rename is not worth
# a lost answer.
#
# It cannot become a bypass. Every old name lands on a new verb and goes
# through exactly the gates that verb goes through: `respond` becomes
# `end_conversation`, which the plan, the review and the verification all hold
# on precisely as they held `respond`.
#
# The two search verbs are here for the same reason and under the same rules.
# `search_files` and `find_text` were one loose search and one exact one, and
# both are now `grep`, which does either. Nothing teaches the old names -- they
# are in no prompt, no tool list and no error message -- but they were the
# commonest verbs in TMT for a long time, so a model reaching for one out of
# habit is answered rather than made to spend a retry on a spelling. `grep`
# reads the workspace and nothing else, so there is no gate for this to walk
# round.
#
# The translation is of the MEANING again, and for `search_files` the spelling
# is not the whole of it: see `adopt_verb`, which turns `ignore_case` on.
#
# `run_file` and `run_python` are here under the same rules and for the same
# reason, and they are the widest translation of the three. They ran ONE FILE
# by its extension; `bash` runs a command line. So the meaning of
# `{"action":"run_file","path":"x.py"}` is not "bash with a path", it is the
# command that file's extension names -- `python x.py` -- which is why
# `adopt_verb` builds it from `agent_execution.RUNNERS` rather than moving a
# key across. Taught nowhere, exactly like the other four: no prompt mentions
# them, no tool list offers them, no error suggests them.
#
# It cannot become a bypass. What comes out is an ordinary `bash` action and it
# goes through everything a `bash` action goes through -- the parser, the
# policy, the approval, the sandbox -- so a legacy verb reaches nothing a
# current one does not.
_LEGACY_ACTIONS = {
    "announce": SEND_MESSAGE,
    "done": END_CONVERSATION,
    "search_files": "grep",
    "find_text": "grep",
    "run_file": "bash",
    "run_python": "bash",
}

# The keys that used to make `respond` mean "and keep going". False-ish here
# meant the message was not the final one, so a `respond` carrying one is a
# `send_message` and not an ending. Read as a string as well as a bool,
# because a model that writes "false" means false.
_NOT_FINAL = ("false", "no", "0", "")


# What a bare legacy `done` becomes the message of. `done` took no keys at
# all, and `execute_action` has always answered one with this word -- so
# supplying it here is not TMT inventing an answer, it is the same default
# moved one step earlier, to where `validate_action` can see it.
#
# Without it the net half-works, which is worse than not working: the name is
# translated and the reply is then REJECTED for a missing `message`, so the
# commonest legacy ending shape costs a retry instead of ending the turn. A
# net that only catches the replies that were already nearly right is not a
# net.
LEGACY_EMPTY_MESSAGE = "done"


# Where the net puts a refusal it has to make. `_bash` reads it before it does
# anything else and returns the sentence; nothing else in TMT looks at it.
#
# A key on the action object rather than a raised error, because this is the
# `hand_back` shape the whole loop is built on: the model gets a sentence
# saying what went wrong and what to write instead, and spends one step. It can
# only ever REFUSE -- there is no value of it that makes something run -- so a
# model writing it itself fails safe.
LEGACY_BLOCKED_KEY = "_legacy_blocked"

_LEGACY_RUN_REFUSED = (
    "FAILED: run_file and run_python are gone -- commands run through the bash "
    "action now. TMT could not translate that one for you because %s. Emit "
    "{\"action\":\"bash\",\"command\":\"...\"} with the command that runs it."
)

# Whether a path can go into a command line as it stands. A WHITELIST of the
# characters that are certainly ordinary, so anything else -- a space, a
# backslash, a pipe, a glob character, an operator -- is quoted rather than
# left for the parser to interpret. A blacklist here would be a list of
# metacharacters somebody has to keep in step with `agent_shell`, and the day
# it fell behind would be the day a filename ran a second command.
_LEGACY_PLAIN_PATH = re.compile(r"[^A-Za-z0-9_./-]")


def _legacy_extension(path):
    """The file extension of a path, lowercased, or "".

    Split by hand rather than with `os.path` because a model writes either
    separator on either platform, and `posixpath.splitext` on `src\\x.py` finds
    a directory that is not there. A dot before the last separator is part of a
    directory name and is not this file's extension.
    """
    dot = path.rfind(".")
    separator = max(path.rfind("/"), path.rfind("\\"))
    return path[dot:].lower() if dot > separator + 1 else ""


def _legacy_bash_command(obj):
    """(command, refusal) for a legacy run_file. Exactly one is non-empty.

    The one place the old verb's meaning is turned into a command line, and the
    one place it can honestly fail. `RUNNERS` says what runs a `.py` and what
    runs a `.go`; it says nothing about a `.zzz`, and it no longer says
    anything about `.c`, `.cpp` or `.java` either, because the compile-and-run
    paths that used to handle those went with `run_file`.

    **A file type with no runner becomes a refusal, never a guess.** Handing
    `bash` a command TMT invented for a file type it does not know would be
    fabricating the one thing a model would then act on, and it is the kind of
    invention that runs rather than merely misleads.

    The path is quoted rather than escaped, and only when it needs it. TMT
    parses this line itself a moment later, so an unquoted backslash in a
    Windows path is an escape character and `src\\x.py` would arrive as
    `srcx.py` -- a file not found, from a translation nobody asked for. Single
    quotes are the one quoting form that is literal all the way through, so a
    path that contains one is refused rather than escaped by guesswork.
    """
    path = obj.get("path")
    if not isinstance(path, str) or not path.strip():
        return "", _LEGACY_RUN_REFUSED % "it named no path to run"
    path = path.strip()
    extension = _legacy_extension(path)
    runner = RUNNERS.get(extension)
    if not runner:
        return "", _LEGACY_RUN_REFUSED % (
            "'%s' has no runner TMT knows about (it knows %s)" % (extension, ", ".join(RUNNERS))
            if extension else
            "that path has no extension for TMT to choose a runner from")
    if "'" in path:
        return "", _LEGACY_RUN_REFUSED % (
            "that path contains a quote and TMT will not guess how to escape it")
    quoted = "'%s'" % path if _LEGACY_PLAIN_PATH.search(path) else path
    return " ".join(part.replace("{file}", quoted) for part in runner), ""


def adopt_verb(obj):
    """Rewrite this reply to the verb in force now. Returns it, mutated.

    The whole translation in one place, so the two step loops and the
    dispatcher cannot drift: `canonical_action` decides WHAT it means, and
    this is what makes the object say it.

    It also fills in the keys a rename left a hole in, and there are three.

    `done` required no keys and `end_conversation` requires `message`, so
    renaming alone left a bare `{"action":"done"}` failing validation --
    translated, and then refused for a key it never had to carry.

    `search_files` matched case-INSENSITIVELY and `grep` does not, so
    translating that one's spelling alone would silently change what the reply
    MEANS: a model that wrote "todo" expecting to find "TODO" would get a
    different answer under a name it never chose. `ignore_case` is turned on
    for it, and only when the model did not write the key itself. `find_text`
    was already exact, so it is translated as it stands.

    `run_file` carried a `path` and `bash` carries a `command`, and the gap
    between them is not a rename at all -- it is the runner table. `x.py` meant
    `python x.py` and `main.go` meant `go run main.go`, so that is what is
    built, from `agent_execution.RUNNERS`. When it cannot be built honestly the
    object is left carrying a refusal instead of a command; see
    `_legacy_bash_command`.

    The reply the model actually wrote is untouched by this: the loops keep
    `raw` and put that in the conversation, so the record still shows the
    model its own words.
    """
    if not isinstance(obj, dict):
        return obj
    was = obj.get("action")
    adopted = canonical_action(obj)
    if not adopted:
        return obj
    obj["action"] = adopted
    if (adopted == END_CONVERSATION and was != END_CONVERSATION
            and "message" not in obj):
        obj["message"] = LEGACY_EMPTY_MESSAGE
    if was == "search_files" and "ignore_case" not in obj:
        obj["ignore_case"] = True
    if was in ("run_file", "run_python") and "command" not in obj:
        command, refused = _legacy_bash_command(obj)
        if refused:
            obj[LEGACY_BLOCKED_KEY] = refused
        else:
            obj["command"] = command
    return obj


def canonical_action(obj):
    """The verb this reply means, under the names in force now.

    `announce` becomes `send_message` and `done` becomes `end_conversation`.
    `respond` becomes whichever it actually meant: it ended the task unless it
    carried `final` set false, so a `respond` with that flag is a
    `send_message` and everything else is an `end_conversation`. Translating
    the MEANING rather than the spelling is what makes the old flag safe to
    delete -- a reply written under the old rules still does exactly what it
    did.

    `search_files` and `find_text` both become `grep`, and `run_file` and
    `run_python` both become `bash`. This answers the verb only; `adopt_verb`
    is what puts `search_files`'s case-insensitivity and `run_file`'s command
    back on the object, because in both cases half of the old verb's meaning
    lived somewhere other than in a key.

    Anything already using the current names, and every other action in TMT,
    is returned untouched. Not a dict, or no action at all, comes back as ""
    so the caller's own validation reports it rather than this raising.
    """
    if not isinstance(obj, dict):
        return ""
    action = obj.get("action")
    if not isinstance(action, str):
        return ""
    if action == "respond":
        final = obj.get("final", True)
        if isinstance(final, str):
            final = final.strip().lower() not in _NOT_FINAL
        return END_CONVERSATION if final else SEND_MESSAGE
    return _LEGACY_ACTIONS.get(action, action)


# The reads after which "now answer the question" is the right next move.
#
# NOT the same set as `agent_delegation.READ_ONLY_ACTIONS`, which shares its
# name and answers a different question -- that one is a security whitelist
# saying what a read-only worker may do, and this one is a nudge about what to
# do next. The two are deliberately not derived from each other.
#
# `web_search` and `web_fetch` are absent, and that is a decision rather than
# an omission. They are read-only in every sense, but the loop they belong to
# ends in a FIX and not in an answer: a model told to answer the user's
# question straight after looking up an error would report what the internet
# says instead of applying it and re-running. `bash` is absent for its own
# version of the same reason, one comment down.
READ_ONLY_ACTIONS = ("list_files", "read_file", "grep", "glob", "read_lines", "git_diff")

# What the model is told when it did work without saying what the work was.
#
# Deliberately a reminder attached to the result rather than a validation
# failure. `validate_action` still does not require "progress", and must not:
# an action without one is a valid action that ran and did its job, and
# rejecting it would turn a presentation rule into a failed turn and throw the
# work away over a missing sentence.
#
# But teaching it in the prompt alone was not enough. Models skip it exactly
# where it matters most -- three reads in a row with nothing said between them,
# which from outside is indistinguishable from a stuck loop. This closes the
# gap the only way that costs nothing: the model is told, at the moment it
# happened, about the specific action that went unnarrated, and it carries on.
_MISSING_PROGRESS = (
    "\n[No \"progress\" was sent with that %s, so the user saw a tool run and no "
    "reason for it. Put a one-sentence \"progress\" on the next action.]"
)

# The actions that ARE the thing being said, and so need nothing said about
# them. Kept beside the reminder rather than inferred, so adding an action
# cannot silently make it exempt.
_SPEAKS_FOR_ITSELF = frozenset((END_CONVERSATION, SEND_MESSAGE))


def _said_something(obj):
    """Whether an action object carried a public sentence about itself."""
    if not isinstance(obj, dict):
        return True          # nothing to judge; never nag about a non-object
    progress = obj.get("progress")
    if isinstance(progress, str) and progress.strip():
        return True
    # An `events` entry is a public record too. A model that reported its work
    # that way said what it was doing, in the place the prompt also offers.
    declared = obj.get("events")
    if isinstance(declared, list):
        for entry in declared:
            if isinstance(entry, dict) and str(entry.get("message", "")).strip():
                return True
    return False


def build_result_message(action, result, obj=None):
    result_str = str(result)
    if "SyntaxError" in result_str:
        return f"FAILED with SyntaxError: {result_str}\nOutput a corrected action that fixes the exact syntax error above."
    if action == "patch_file" and "Search text not found" in result_str:
        return f"FAILED: {result_str}\nThe search string didn't match exactly. Use grep or read_lines, then retry."
    if action == "replace_lines" and "Invalid range" in result_str:
        return f"FAILED: {result_str}\nUse read_lines on this file first, then retry replace_lines."
    # Deliberately not `bash`. "not found" in a command's output is the
    # program's own words -- `cat` on a missing file, a compiler on a missing
    # header -- and "check the file path with list_files" is advice about the
    # wrong thing. It is the same rule that keeps `bash` out of
    # `_REPORTED_ACTIONS`: a command's output is data, not a report on the
    # action.
    if "not found" in result_str.lower() and action in ("read_file", "patch_file", "read_lines", "copy_file"):
        return f"FAILED: {result_str}\nCheck the file path with list_files and retry."
    if action in READ_ONLY_ACTIONS:
        message = f"Result:\n{result_str}\nNow output an end_conversation action that naturally answers the user's question using this data."
    else:
        message = f"Result: {result_str}"
    # Appended to the end, after whatever the result had to say, so it is the
    # last thing read before the next action is written and never displaces
    # the correction a failed action needs.
    if action not in _SPEAKS_FOR_ITSELF and not _said_something(obj):
        message += _MISSING_PROGRESS % action
    return message

ACTION_LABELS = {action: action.replace("_", " ").title() for action in (
    "write_file", "append_file", "write_files", "patch_file", "delete_file", "read_file",
    "list_files", "read_lines", "replace_lines", "copy_file",
    "delete_folder", "rename_file", "create_folder", "bash", "ask_user",
    "open_app", "git_status", "git_diff", "git_identity", "git_commit", "git_push",
    "tree", "grep", "glob", "find_symbol", "replace_across", "code_map",
    # "Web Search" and "Web Fetch". Both title-case from the verb like
    # everything else here; they are listed because a registered action
    # with no entry shows the reader a raw verb in a column where every
    # neighbouring row is a phrase.
    "web_search", "web_fetch",
    # "View Image". Registered here for the reason the two above it are: an
    # action with no entry shows the reader a raw verb in a column where every
    # neighbouring row is a phrase.
    "view_image",
    # "Multi Tool". The row a multi_tool draws is built by `_multi_event`
    # from the calls that ran, and this label heads it.
    "multi_tool",
    "related_tests", "remember", "recall", "plan", "review", "verify",
    # "Project Context", which is what the transcript should say: the row is
    # about the project's own memory, not about the conversation's context
    # window, and those are two different things a user could reasonably
    # confuse if the label were shorter.
    "project_context",
    # Registered here although only the reviewer can carry it out, so an agent
    # that emits it anyway is named in words rather than by its raw verb. Every
    # other registered action has an entry; a missing one shows the reader
    # `review_agenda` where every neighbouring row says `Review Agenda`.
    "review_agenda",
    "spawn_agent", "agent_status", "agent_result", "wait_for_agent",
    "wait_for_agents", "kill_agent", "internal_response",
    SEND_MESSAGE, END_CONVERSATION,
)}

def batch_summary(batch):
    counts = Counter(sub.get("action", "unknown") for sub in batch)
    return "Running: " + ", ".join(
        f"{ACTION_LABELS.get(action, action)} x{count}" if count > 1 else ACTION_LABELS.get(action, action)
        for action, count in counts.items()
    )

MAX_TURNS = 10
def trim_messages(messages, pinned=2):
    """Drop the middle of a long turn, keeping its head and its tail.

    `pinned` is how many messages at the front must survive whatever else
    goes. It is the system prompt, the conversation carried in from earlier
    questions, and the task itself -- everything the session put there before
    the loop started adding actions and results. Trimming into that would take
    the question out of the request and leave the model answering something
    nobody asked, which is the one failure this cannot be allowed to have.

    The default of two is the shape this had before a session context existed:
    a system prompt and a task. Callers that carry history pass their own
    count, which `Session.begin_turn` returns for exactly this.
    """
    pinned = max(0, min(int(pinned), len(messages)))
    fixed, turns = messages[:pinned], messages[pinned:]
    max_messages = MAX_TURNS * 2
    if len(turns) <= max_messages:
        return messages
    return fixed + [{"role": "user", "content": f"[{len(turns) - max_messages} earlier messages trimmed to stay within context limits.]"}] + turns[-max_messages:]


# --- actions as user-visible events --------------------------------------
#
# The transcript shows what the agent did, so the facts on it have to come from
# what actually ran. Everything below is measured from the action object and
# its result: a line count is counted, a path is the path that was written, a
# failure is a failure the action reported. Nothing here estimates, and an
# action whose outcome cannot be described honestly gets a plain event with no
# facts rather than a plausible-looking one.

_EVENT_KIND_FOR_ACTION = {
    "write_file": "file_create", "write_files": "file_create",
    "create_folder": "file_create",
    "append_file": "file_edit", "patch_file": "file_edit",
    "replace_lines": "file_edit", "rename_file": "file_edit",
    "copy_file": "file_create",
    "delete_file": "file_delete", "delete_folder": "file_delete",
    "read_file": "file_read", "read_lines": "file_read",
    # Reading a file, and the row names the path like every other file_read.
    # What is different about it is inside the request rather than on screen:
    # nothing the transcript can draw distinguishes looking at a screenshot
    # from reading a source file, and a kind of its own would promise the
    # reader a distinction the row cannot make.
    "view_image": "file_read",
    # Both searches read the workspace and change nothing in it, so they take
    # the kind every other reading verb takes. `glob` reads names rather than
    # contents, which is a difference in what is read and not in what happens.
    "list_files": "file_read", "grep": "file_read", "glob": "file_read",
    # Reading, but not the workspace -- so `tool` rather than `file_read`,
    # which every other row of that kind names a path in. A web result has
    # no path, and a row that promised one would be the only `file_read`
    # in the transcript with nothing local behind it.
    "web_search": "tool", "web_fetch": "tool",
    # A multi_tool is whatever its calls are, and `_multi_event` decides the
    # row from them; this is the kind it takes when nothing inside it says
    # otherwise, and the kind a refused one falls back to before the warning.
    "multi_tool": "tool",
    # The one verb that runs anything, and the kind `run_file` and `run_python`
    # held before it. A command is not a file operation whatever it does to
    # files: what the user is watching is a process starting, and the row says
    # so.
    "bash": "command", "open_app": "command",
    # Neither a file operation nor a command: nothing runs and nothing is
    # read. What the user is watching is TMT waiting on THEM, so it takes the
    # neutral tool kind rather than borrowing a kind that promises a path.
    "ask_user": "tool",
    "git_status": "tool", "git_diff": "tool", "git_identity": "tool",
    "git_commit": "milestone", "git_push": "milestone",
    # A milestone rather than a tool, and it earns it: a plan step changing
    # state is the coarsest thing that happens in a turn, it is what the user
    # is following in the panel, and there are only ever a handful of them --
    # one per step, plus the one that made the plan.
    "plan": "milestone",
    # A milestone for the reason `plan` is one, and more so: a review is the
    # coarsest thing that happens in a turn, there is at most a handful of
    # them, and its verdict is the fact the user most wants to see go past.
    "review": "milestone",
    # A milestone beside the review, for the same reasons and one more: a
    # verification takes real time, and the row saying it happened is the one
    # the user scrolls back to when they want to know what was actually run.
    "verify": "milestone",
    # `background_agent` already exists in agent_prompt.EVENT_TYPES, at
    # prominence level 1 and gradient position 40 beside milestone and tool.
    # A new element takes a place on the existing scale; it does not get a
    # colour or a kind of its own, so these use the one that is already there.
    "spawn_agent": "background_agent", "agent_status": "background_agent",
    "agent_result": "background_agent", "wait_for_agent": "background_agent",
    "wait_for_agents": "background_agent", "kill_agent": "background_agent",
    "internal_response": "background_agent",
}

# Only ever matched against a short sentence an action wrote about itself.
_FAILURE_MARKERS = (
    "not found", "error:", "syntaxerror", "refusing", "invalid",
    "failed", "cancelled", "must be", "no matches for", "aborted",
)

# The actions whose result is a report on the action, and so can be read for
# whether it worked. Every other action's result is data -- file content, the
# output of a program someone ran -- where these words mean nothing about the
# action itself. Scanning those produced a real false alarm: running the test
# suite returns "180 passed, 0 failed", and a substring search called a fully
# green run a failure. An event that misreports what happened is worse than no
# event, so the list is stated rather than inferred.
#
# `bash` is the sharpest case of that and is deliberately absent. Its result is
# whatever a program printed, which is the definition of data here -- and it is
# the very output the false alarm above was found in.
_REPORTED_ACTIONS = frozenset((
    "write_file", "write_files", "append_file", "patch_file", "replace_lines",
    "delete_file", "delete_folder", "create_folder", "copy_file", "rename_file",
    "git_status", "git_diff", "git_identity", "git_commit", "git_push",
    # These three report on themselves: "Started background agent #2 on ...",
    # "Stopped background agent #2", the status listing. Their first line is
    # TMT's own sentence about what happened, so it can be shown and it can be
    # read for whether it worked.
    #
    # agent_result, wait_for_agent, wait_for_agents and internal_response are
    # deliberately NOT here. Their result is a worker's own report, quoted
    # verbatim -- it is data in exactly the way a program's output is data, and
    # scanning it for "failed" would label a worker that truthfully said "two
    # tests failed" as a failed action. That is the same false alarm that once
    # called a green test run a failure, arriving by a new route.
    "spawn_agent", "agent_status", "kill_agent",
    # The plan's result is TMT's own sentence about what the call did --
    # "S2 (Run the tests) in_progress -> completed." -- so its first line can
    # be shown, and a refused operation comes back as "FAILED: ..." and is
    # read as the warning it is by the same markers every other action uses.
    "plan",
    # Here for `_describe` rather than for the marker scan, which
    # `action_event` overrides for this one action. Its first line is TMT's
    # own headline -- "REVIEW PASSED", "REVIEW FAILED - 2 blocking issues" --
    # so it is exactly the short specific sentence this set is for, and
    # describing the request instead would put the bare word "Review" in the
    # transcript where the verdict belongs.
    "review",
    # Here for `_describe` for exactly the reason `review` is, and with the
    # same override in `action_event`: its first line is TMT's own headline --
    # "VERIFY PASSED - 2 checks, 0 failures" -- so it is the short specific
    # sentence this set is for, and describing the request instead would put
    # the bare word "Verify" in the transcript where the evidence belongs.
    "verify",
    # Its result is TMT's own sentence about what the call did -- "Recorded
    # under 'Architecture' in notes.md.", "FAILED: ..." -- so its first line
    # can be shown, and a refused operation is read as the warning it is by
    # the same markers every other action uses. Without it the transcript
    # would say "Project Context" and nothing else, which does not distinguish
    # a note that was written from one that was refused.
    #
    # No `action_event` override, unlike `review` and `verify`: this action's
    # result never quotes anything a model or a program wrote, so there is no
    # borrowed prose for the marker scan to trip over. Every word in it is
    # this module's or `agent_context`'s.
    "project_context",
))


def _line_count(text):
    """Lines in a block of content, counting an unterminated last line."""
    if not isinstance(text, str) or not text:
        return 0
    return len(text.splitlines())


# The gutter `agent_file_ops.read_lines` writes in front of every line it
# returns: the line number right-aligned in five columns, then " | ". A number
# past 99999 is wider than the field and gets no padding at all, so the run of
# leading spaces is optional rather than fixed.
_READ_GUTTER = re.compile(r"^ *(\d+) \|")


def _lines_read(result):
    """The line range a `read_lines` result actually covers, or None.

    Measured off the gutter TMT itself printed, and never taken from the
    action's own `start` and `end`. The two disagree whenever the request
    overshoots the file: `read_lines` clamps `end` to the last line and fills
    an absent one in, so a model asking for 1-500 of a forty-line file is
    answered with 1-40, and a row reading "(1-500)" would put on screen a
    range nobody read. This is not the forbidden reading-of-data either --
    what is parsed back is this program's own numbering, not the file's
    contents, and the first and last rows of the block carry it whatever the
    lines between them happen to say.

    Every unreadable shape returns None and the row falls back to the plain
    label. A missing file, a backwards range and an empty result all say
    nothing about which lines were read, and no range at all is better than
    a plausible one.
    """
    rows = str(result).splitlines()
    if not rows:
        return None
    first, last = _READ_GUTTER.match(rows[0]), _READ_GUTTER.match(rows[-1])
    if not (first and last):
        return None
    start, end = int(first.group(1)), int(last.group(1))
    return (start, end) if end >= start else None


def _describe(action, obj, result):
    """A one-line public description of what an action did.

    The result string is already written for a person, so where it is short
    and specific it is used as-is rather than paraphrased into something that
    might not match what happened.
    """
    text = str(result).strip()
    first = text.split("\n", 1)[0]
    if action not in _REPORTED_ACTIONS:
        # The result is data -- a file's contents, a program's output -- not a
        # sentence about the action. Its first line describes neither what ran
        # nor how it went: a test run whose output began "PASS test_retries"
        # was labelled with that one line, which says less than the truth and
        # implies it was all of it. Describe the request instead, which is the
        # part that is known.
        # `pattern` is last because it belongs to one action: without it a
        # `glob` row is the bare word "Glob", which says a search happened and
        # not what was looked for. `command` is there for the same reason and
        # matters more -- a bash row is the one row a user scrolls back to in
        # order to find out what was actually run, and "Bash" alone answers
        # nothing. It is the command the model asked for; what TMT parsed it
        # into is in the result.
        # `url` is here for `pattern`'s reason exactly, found the same way --
        # by somebody reading the composed row rather than the code. A
        # `web_fetch` row without it is the bare words "Web Fetch", so the
        # fortieth page read in a session is the same row as the first and a
        # reader scrolling back cannot tell which one was fetched.
        target = (obj.get("path") or obj.get("query") or obj.get("pattern")
                  or obj.get("url") or obj.get("app") or obj.get("command")
                  # An `ask_user` row without it is the bare words "Ask User",
                  # which says a question was put and not what was asked --
                  # and the answer the user gave is the one thing a reader
                  # scrolling back needs the question beside.
                  or obj.get("question") or "")
        label = ACTION_LABELS.get(action, action)
        if action == "read_lines":
            # The one read whose extent is a fact rather than a guess, so it
            # is said: "Read Lines (12-15) CalcTUI.py". Without it the fifth
            # read of one file is the same row as the first, and a reader
            # scrolling back cannot tell which part of it the agent looked at
            # -- which is most of what a ranged read means. Always start-end,
            # even for a single line: "(12)" beside "(12-15)" invites reading
            # as a count, and "(12-12)" can only mean what it says.
            covered = _lines_read(result)
            if covered:
                label = "%s (%d-%d)" % (label, covered[0], covered[1])
        return "%s %s" % (label, target) if target else label
    if first and len(first) <= 200:
        return first
    return ACTION_LABELS.get(action, action)


def action_event(action, obj, result):
    """One AgentEvent describing an action that has already run, or None.

    Called after the action, never before, so the event can report what
    happened rather than what was intended.
    """
    # Neither produces an event of its own. `end_conversation` IS the answer,
    # and `send_message` is drawn by the loop before the work it is talking
    # about -- an event here would print the same sentence a second time.
    if action in (END_CONVERSATION, SEND_MESSAGE):
        return None
    if action == "multi_tool":
        return _multi_event(obj, result)
    kind = _EVENT_KIND_FOR_ACTION.get(action, "tool")
    text = str(result)
    detail = {}

    failed = False
    if action in _REPORTED_ACTIONS:
        lowered = text[:200].lower()
        failed = any(marker in lowered for marker in _FAILURE_MARKERS)
    if action == "git_push" and text == PUSH_BLOCKED:
        failed = True
    if action == "review":
        # Decided by an exact prefix on a sentence THIS program wrote, and
        # never by the marker scan above. A review's result carries the
        # reviewer's own prose -- titles, evidence, a summary -- and a passing
        # review that said "the tests that used to fail now pass" would be
        # labelled a failure by any substring search for "fail". That is the
        # false alarm that once called a green test run a failure, and this is
        # the one action where the reviewer's words are guaranteed to be full
        # of the words the scan looks for.
        #
        # Both passing verdicts begin "REVIEW PASSED"; a failure, an error,
        # a refusal and an unavailable review all begin with something else,
        # and every one of those is a warning rather than a success.
        failed = not text.startswith("REVIEW PASSED")
        if not failed:
            kind = "success"
    if action == "verify":
        # The same rule as `review` above and for the same reason, sharpened:
        # a verification's result quotes what the commands printed, and a
        # passing run of a test suite whose output says "2 previously failing
        # tests now pass" would be labelled a failure by any substring search.
        # So the decision is an exact prefix on a sentence THIS program wrote,
        # built by `VerificationResult.headline` from exit codes.
        failed = not text.startswith("VERIFY PASSED")
        if not failed:
            kind = "success"

    if failed:
        # A refusal is not a crash, and a missing file is not a broken agent.
        # Both are things the user needs to see, at a weight that does not
        # read as the run having fallen over.
        return agent_ui.AgentEvent.make("warning", _describe(action, obj, result))

    # Counts, only where they can be counted exactly.
    if action == "append_file":
        # An append adds and removes nothing, so both halves are known.
        detail["added"], detail["removed"] = _line_count(obj.get("content", "")), 0
    elif action == "write_file":
        # Two cases, and only the action's own report can tell them apart.
        # A write over an existing file replaces content that was gone before
        # anyone could count it, so only what was written is ever claimed:
        # "+3 -0" on a write that flattened a hundred-line file would be a
        # confident falsehood. A write to a path that did not exist removed
        # nothing, and that "nothing" is a measurement rather than a guess.
        lines = _line_count(obj.get("content", ""))
        if text.startswith("Created file:"):
            detail["added"], detail["removed"] = lines, 0
        elif lines:
            detail["lines"] = lines
    elif action == "patch_file":
        detail["added"] = _line_count(obj.get("replace", ""))
        detail["removed"] = _line_count(obj.get("search", ""))
    elif action == "replace_lines":
        try:
            detail["removed"] = max(0, int(obj["end"]) - int(obj["start"]) + 1)
        except (KeyError, TypeError, ValueError):
            pass
        detail["added"] = _line_count(obj.get("content", ""))
    elif action == "write_files":
        files = obj.get("files")
        if isinstance(files, list):
            detail["files"] = len(files)
            # Both halves only when every one of them was a creation, which is
            # the batch's own report. One overwrite among them and the removed
            # count is unknowable for the batch as a whole, so it is not given.
            reports = [line for line in text.splitlines() if line.strip()]
            if reports and all(line.startswith("Created file:") for line in reports):
                detail["added"] = sum(_line_count(entry.get("content", ""))
                                      for entry in files if isinstance(entry, dict))
                detail["removed"] = 0

    # Which files the action named. Recorded, not drawn: the transcript's
    # second row reports only counts, and the description above already says
    # the path in the words the action used. It is here for the session
    # record, which carries "what the last turn changed" into the next
    # question -- and a path is the one part of that a follow-up like "now
    # add percentage support" depends on and never states.
    paths = _paths_named(action, obj)
    if paths:
        detail["paths"] = paths

    return agent_ui.AgentEvent.make(kind, _describe(action, obj, result), **detail)


def _multi_calls(obj):
    """The calls a multi_tool ran, or the ones it was asked for if it has not.

    The ran list is the fact and the asked list is the intent; the second is
    only consulted before there is a first -- a refusal recording which paths
    a contract was violated for is about what was asked.
    """
    try:
        import agent_multi
        pairs = agent_multi.ran(obj)
    except Exception:
        pairs = ()
    if pairs:
        return [call for call, _ in pairs]
    calls = obj.get("calls") if isinstance(obj, dict) else None
    return [call for call in (calls or ()) if isinstance(call, dict)]


def _multi_event(obj, result):
    """The one transcript row a multi_tool draws, measured off its calls.

    One row rather than one per call, on purpose: a fan-out over a hundred
    files is a hundred rows the user did not ask to scroll past, and the
    per-call detail is in the result the model reads. What the row carries is
    everything the inner rows would have added up to -- the verbs and how
    many of each, the line counts summed where they were counted, every path
    named for the session record, and a warning whenever any call's own row
    would have been one. Nothing here is estimated: each inner event is the
    same `action_event` the call would have produced on its own.

    A multi_tool refused before anything ran draws the refusal, which is this
    program's own sentence about the request and can be shown as-is.
    """
    label = ACTION_LABELS.get("multi_tool", "Multi Tool")
    try:
        import agent_multi
        started = agent_multi.started(obj)
        pairs = agent_multi.ran(obj)
    except Exception:
        started, pairs = False, ()
    if not started:
        first = str(result).strip().split("\n", 1)[0]
        return agent_ui.AgentEvent.make("warning", first[:200] if first else label)
    counts = Counter(str(call.get("action", "?")) for call, _ in pairs)
    added = removed = None
    warnings = 0
    seen, paths = set(), []
    for call, sub_result in pairs:
        event = action_event(str(call.get("action", "")), call, sub_result)
        if event is None:
            continue
        if event.kind == "warning":
            warnings += 1
        found = event.detail or {}
        if isinstance(found.get("added"), int):
            added = (added or 0) + found["added"]
        if isinstance(found.get("removed"), int):
            removed = (removed or 0) + found["removed"]
        for path in found.get("paths") or ():
            if path not in seen:
                seen.add(path)
                paths.append(path)
    summary = ", ".join(
        "%s x%d" % (ACTION_LABELS.get(verb, verb), count) if count > 1
        else ACTION_LABELS.get(verb, verb)
        for verb, count in counts.items())
    message = "%s: %s" % (label, summary) if summary else label
    if warnings:
        message += " (%d %s)" % (warnings, "warning" if warnings == 1 else "warnings")
    detail = {"calls": len(pairs)}
    if added is not None or removed is not None:
        detail["added"], detail["removed"] = added or 0, removed or 0
    if paths:
        detail["paths"] = tuple(paths)
    return agent_ui.AgentEvent.make("warning" if warnings else "tool", message, **detail)


def _paths_named(action, obj):
    """The workspace paths an action was given, in order, each once.

    Taken from the request rather than parsed back out of the result: the
    request is where a path is a fact. An action that names none contributes
    none.
    """
    if not isinstance(obj, dict):
        return ()
    candidates = []
    if action == "write_files":
        for entry in obj.get("files") or ():
            if isinstance(entry, dict):
                candidates.append(entry.get("path"))
    elif action == "multi_tool":
        # Every path every call named, under that call's own verb, so a
        # `write_files` inside a multi_tool still names each of its files.
        for call in _multi_calls(obj):
            candidates.extend(_paths_named(str(call.get("action", "")), call))
    else:
        candidates.extend((obj.get("path"), obj.get("destination"), obj.get("new_path")))
    seen, out = set(), []
    for value in candidates:
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        if value not in seen:
            seen.add(value)
            out.append(value)
    return tuple(out)


def batch_events(batch, results):
    """Events for a batch, in the order the actions ran.

    `results` is the list execute_action produced, which may be shorter than
    the batch when one action ended it early. Pairing them by position keeps
    every event tied to the action that actually produced it.
    """
    events = []
    for sub_obj, result in zip(batch, results):
        if not isinstance(sub_obj, dict):
            continue
        event = action_event(sub_obj.get("action", ""), sub_obj, result)
        if event is not None:
            events.append(event)
    return events
