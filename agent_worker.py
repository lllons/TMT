"""The headless step loop a background agent runs.

This is TMT's own agent loop with the interface taken out and cancellation
put in. It asks the model for one action, runs it, feeds the result back, and
goes round again -- the same shape as `TMT._session_loop`, and deliberately so,
because that loop's behaviour was arrived at by things going wrong and a
second loop that quietly disagreed with it would have to learn all of them
again.

What is different, and why:

**It never prints.** Not once, not on an error, not on a failure it cannot
recover from. A `console.print` from a background thread lands on top of the
session's live region and corrupts the arithmetic that repaints it, so the
worker's only channel to the screen is the manager's event bus. Everything
this loop would have said is said through `manager.set_activity`, or comes
back as the string it returns.

**Its only terminal verb is `internal_response`.** `respond` and `done` end a
turn for the user, and a worker has no user. They are refused before dispatch,
along with `git_push`, by a check that reads a set rather than a policy.

**Cancellation is checked at three boundaries.** A Python thread cannot be
killed and a streamed response has no abort primitive, so "kill" cannot mean
"stop instantly" and pretending otherwise would be a lie in the one place a
lie is expensive. What is enforceable is enforced: *after the flag is set, no
further tool call executes.* The flag is checked at the top of each step, in
the stream handler between chunks, and -- the one that carries the guarantee --
on the line immediately before each action is dispatched. Nothing may be
inserted between that check and the `execute(...)` call.

**The tools it is given are a whitelist, not a blacklist.** The note agent gets
`NOTE_ACTIONS` and nothing else. A blacklist would silently admit every action
added to TMT after it was written, which is the failure that matters: the
person adding the action is not the person who wrote the blacklist.

`ask`, `execute` and `system_prompt` are all injectable, so the loop can be
tested without a model, without a network and without a real workspace. The
defaults are looked up inside the functions rather than imported at module
scope, so this module still imports cleanly when one of them is mid-edit or
absent from a frozen install's module list.
"""

import json

from agent_manager import WorkerCancelled, clip_activity

# The verb a background agent may use to say something, and the one it may
# not use at all. Spelled here rather than imported from `agent_actions`,
# because this module imports that one INSIDE its functions on purpose -- a
# frozen module list must not be able to stop a worker loading -- and a
# module-scope import for two strings would give that up. There is a test that
# the two spellings agree.
SEND_MESSAGE = "send_message"
END_CONVERSATION = "end_conversation"

# How many actions a worker may take before it is stopped. Larger than a
# user-facing turn's budget because a delegated task is a whole piece of work
# rather than one exchange, and a worker that runs out has nobody to ask.
WORKER_ROUNDS = 40

# Replies that could not be used -- unreadable JSON, a failed validation,
# arguments the action itself rejected. Mirrors TMT.py, including the reason
# it is a separate budget from the rounds: a reply with a comma out of place
# is not a step of the work, and charging it as one let a model exhaust a task
# it had not started.
MAX_INVALID_RETRIES = 6

# Everything the note agent may do, and nothing else. Read-only by
# construction: there is no verb in here that writes a file, runs a program,
# reaches a network or changes git.
NOTE_ACTIONS = frozenset({
    "list_files", "read_file", "read_lines", "search_files", "find_text",
    "find_symbol", "tree", "code_map", "related_tests", "recall",
    "git_status", "git_diff", "git_identity",
    "send_message", "internal_response",
})

# The reviewbot's readout verb. Handled by this loop directly rather than
# through `execute_action`, for a reason `TERMINAL_ACTION` shares and for one
# of its own: it writes to the agenda hanging off THIS agent's record, and the
# only thing that can find that record is the manager -- which `_context`
# deliberately withholds from a background agent so a worker cannot spawn
# workers of its own. Routing it through the dispatcher would mean handing
# every worker a register in order to give one reviewer a checklist.
#
# `execute_action` still knows the name and answers it with a sentence saying
# where it belongs, so a main model or a plain worker that emits it is
# corrected rather than told "Unknown action".
AGENDA_ACTION = "review_agenda"

# Everything the review agent may do, and nothing else. Read-only by
# construction in exactly the way NOTE_ACTIONS is, and a whitelist for exactly
# the same reason: a blacklist silently admits every action added to TMT after
# it was written, and the person adding the action is not the person who wrote
# the blacklist.
#
# It is NOTE_ACTIONS plus `find_symbol`'s companions and nothing that writes.
# The reviewer is deliberately not given `run_file`: a reviewer that runs the
# code is a reviewer that changes what it is reviewing -- a test run writes
# caches, a script writes files -- and it would also be reading a result it
# produced rather than the one the implementing agent produced. What actually
# ran this turn is put in its brief as an observed fact instead, and judging
# what that proves is its job.
#
# `review` itself is absent, so a reviewer cannot start a review of its own.
#
# `review_agenda` is the one verb it has that the note agent does not, and it
# is what the two lists were named separately for: the reviewer says what it
# is going to check before it checks anything, and ticks each item off as it
# goes. It writes to a readout and nothing else -- no file, no workspace, no
# review verdict -- so it does not cost the reviewer its read-only guarantee.
REVIEW_ACTIONS = frozenset(NOTE_ACTIONS | {AGENDA_ACTION})

# What a worker may never do, whatever its prompt says. `git_push` because a
# push needs the user's own words behind it and a worker's task text is
# written by a model; `respond` and `done` because they are how a turn ends
# for a user, and a worker has no user to end a turn for.
# `plan` because the plan is the MAIN agent's contract with the user: it is
# what gates that agent's final answer and what the user is reading in the
# panel. A worker writing to it would be editing the shape of a task it can
# see only one step of, and a worker completing a step would let the main
# agent finish on work the worker had merely claimed.
# `review` for the same shape of reason, from the other end: a review is the
# INDEPENDENT check on the main agent's work, and independence is the whole of
# its value. A worker that could start one would be reviewing a diff it had
# itself just written, and the reviewer agent -- which runs through this same
# loop -- would be able to review its own review. Refused here in code as well
# as being absent from every background prompt, so the isolation does not rest
# on wording.
# `verify` for the third variation on the same reason: verification is the
# EVIDENCE the main agent's final answer is gated on, and a worker producing
# it would let that agent finish on checks it never chose and never saw. It
# would also run the project's whole test suite on a background thread while
# the main agent edits the files under it, which is a result about a tree that
# never existed -- the same hazard `review` refuses to start into.
WORKER_FORBIDDEN = frozenset({"git_push", "end_conversation", "plan",
                              "review", "verify",
                              # The project's persistent memory, refused for
                              # the reason the three above it are: it is the
                              # main agent's account of the project, written
                              # to files the user reads and commits, and a
                              # worker that could write it would be recording
                              # a conclusion from inside a subtask -- before
                              # the agent that delegated the subtask had
                              # reached one, and without the whole task in
                              # front of it.
                              #
                              # Two-sided, exactly as those are: it is absent
                              # from every background prompt as well, so a
                              # worker is neither taught the verb nor allowed
                              # it. And a background agent's action context
                              # carries no "context" key at all, so even
                              # reaching `execute_action` directly finds
                              # nothing to write to.
                              "project_context"})

# Refused to every background agent, for a different reason from the verbs
# above: these two are not the wrong shape for a worker, they are unreachable
# from one. Both call a bare blocking `input()` to confirm the deletion, and a
# background thread has no terminal to be asked at -- it would compete with
# the session's own reader for stdin and then block forever on a prompt nobody
# can see. The suite has no per-test timeout either, so a test that reached
# one would hang the whole run rather than fail.
#
# Refusing is the correct behaviour rather than a capability withheld: that
# confirmation is a deliberate safety property of both actions, and an agent
# that cannot obtain it must not proceed without it. Enforced here, in code,
# because a rule that lives only in a prompt is taught rather than guaranteed.
WORKER_NEEDS_TERMINAL = frozenset({"delete_file", "delete_folder"})

# The verb both kinds finish on. Handled by this loop directly rather than
# through `execute_action`, so a worker still terminates correctly on an
# install where the action has not been registered yet.
TERMINAL_ACTION = "internal_response"

_FALLBACK_CHARS_PER_TOKEN = 4

_UNREADABLE = (
    "INVALID: that reply could not be read as JSON. The parser said: %s\n"
    "Reply with exactly one JSON object and nothing else -- no prose before "
    "it, no prose after it, no code fences."
)

_PROSE_FEEDBACK = (
    "That was prose, and nothing has run yet, so it described work rather "
    "than doing it. Emit the action you just described, as one JSON object."
)

_ACTION_RAISED = (
    "INVALID: the action '%s' could not run with those arguments -- it raised "
    "%s: %s\nCheck the type of every key you sent: paths and text are strings, "
    "line numbers are unquoted numbers, flags are unquoted true or false."
)

_MESSAGE_SENT = ("Noted, and nothing was shown. Nothing you write reaches "
                 "anybody -- you have no user -- so send_message costs a step "
                 "and tells nobody. The task is not finished: emit the action "
                 "you just described.")

# What the reviewer is told after touching its agenda. The tail matters: the
# agenda is a readout and updating it is not reviewing, so a reviewer that has
# just ticked an item is pointed straight back at the work rather than left
# looking at a checklist it could keep tidying.
_AGENDA_RESULT = ("Agenda: %s\nThat updated the readout only; it reviewed "
                  "nothing. Take the next action for the item you are on.")

# The nudge a reviewer gets when it has started reviewing without saying what
# it was going to check. It is the shape `build_result_message` already uses
# for a missing `progress` sentence, and it is that shape for the same reason:
# it RIDES ON A RESULT, so it costs no step and cannot fail one.
#
# Deliberately not enforced. A gate here -- refusing every action until an
# agenda exists -- would let a reviewer that could not produce the shape burn
# its retry budget and end in ERROR, and ERROR blocks the final answer. A
# readout must never be able to stop the work it is reporting on, so this is
# taught in the prompt, nudged once here, and never required.
_AGENDA_MISSING = (
    "\n\nReminder: you have not declared your agenda, and the person waiting "
    "can see that you are working but not what you are working through. Emit "
    "{\"action\":\"review_agenda\",\"operation\":\"create\",\"items\":[...]} "
    "with the four to eight things you are checking, then carry on."
)

_REFUSED = (
    "REFUSED: '%s' is not available to you. %s Emit a different action, or "
    "finish with internal_response."
)

_NOT_A_WORKER_VERB = (
    "You are a background agent and have no user to answer; your only "
    "terminal verb is internal_response."
)

_NOT_A_NOTE_VERB = (
    "You are answering one question by reading the workspace and may not "
    "change it."
)

# The same refusal for the reviewer, in its own words. Threaded through the
# loop rather than shared with the note's because a model told the wrong
# reason reasonably looks for another route to the same effect -- the mistake
# WORKER_NEEDS_TERMINAL was given its own sentence to avoid. A reviewer told
# it is "answering one question" would reasonably conclude that fixing what it
# found is somebody's job it might take on.
_NOT_A_REVIEW_VERB = (
    "You are reviewing work you did not write, and a reviewer that edits the "
    "code is no longer independent of it. Report the finding instead; the "
    "implementing agent makes the change."
)

_NEEDS_A_TERMINAL = (
    "It waits for a human to confirm at the terminal, and you are running in "
    "the background with no terminal to be asked at. Name the path in your "
    "internal_response and leave the deletion to the main agent."
)


def _adopt_verb(obj):
    """Rewrite a reply's action to the name in force now, if it needs it.

    The worker's half of the translation the main loop does. It matters less
    here -- a background agent is refused `end_conversation` either way -- but
    it matters for `send_message`, which is on the note agent's and the
    reviewer's whitelists: a reviewer that reached for the old `announce`
    would otherwise be refused a verb it is allowed.

    Guarded to nothing. `agent_actions` is imported inside the call for the
    reason every import in this module is, and a translation that cannot be
    made leaves the reply exactly as the model wrote it.
    """
    if not isinstance(obj, dict):
        return obj
    try:
        import agent_actions
        return agent_actions.adopt_verb(obj)
    except Exception:
        return obj


def _guard(record):
    """Stop here if this agent has been cancelled.

    Called at each of the three boundaries. It raises rather than returning a
    value because two of the three places it is called from -- the stream
    handler, and the middle of a batch -- have no way to report one.
    """
    if record.cancel.is_set():
        raise WorkerCancelled("agent %s was cancelled" % record.id)


def _chars_per_token():
    """The constant the rest of TMT estimates with, or a fallback.

    Read from agent_ui rather than restated, so an estimate made here and an
    estimate made by the corner meter cannot drift apart and report two
    different numbers for the same reply.
    """
    try:
        import agent_ui
        return int(agent_ui.CHARS_PER_TOKEN) or _FALLBACK_CHARS_PER_TOKEN
    except Exception:
        return _FALLBACK_CHARS_PER_TOKEN


def _tokens_for_chars(chars):
    """An estimated token count, and everything that takes it marks it so."""
    per = _chars_per_token()
    return (max(0, int(chars)) + per - 1) // per


def _message_chars(messages):
    return sum(len(str(message.get("content", ""))) for message in messages)


def _default_ask():
    """`agent_model.ask_model`, imported at call time.

    Late, for the reason `agent_actions._run_tool` imports late: an editable
    install freezes its module list, and a module that cannot be imported must
    come back as a failure this loop can report rather than an ImportError at
    the top of the file that stops the whole program from starting.
    """
    import agent_model
    return agent_model.ask_model


def _default_execute():
    import agent_actions
    return agent_actions.execute_action


def _default_prompt(kind):
    try:
        import agent_subprompts
    except Exception as error:
        raise RuntimeError("the %s prompt is unavailable: %s" % (kind, error))
    if kind == "note":
        return agent_subprompts.note_prompt()
    if kind == "review":
        return agent_subprompts.review_prompt()
    return agent_subprompts.worker_prompt()


def _reply_flags(obj):
    """(parse_failure, provider_failure, prose) for a parsed reply.

    `ask_model` never returns unparseable output: every failure inside it
    converges on a fabricated action object, because the loop has no other
    shape to receive one in. So a broken reply does not arrive as an
    exception, it arrives as a valid terminal action, and these three markers
    are the only way to tell it apart from one the model meant.
    """
    try:
        import agent_model
    except Exception:
        return False, False, False
    reason = agent_model.synthetic_reason(obj)
    return (reason == agent_model.PARSE_FAILURE,
            reason == agent_model.PROVIDER_FAILURE,
            agent_model.is_prose(obj))


def _validate(obj):
    """The action's required keys, or a sentence saying which are missing.

    Falls back to the one check that can be made without the schema, because
    a worker on an install where agent_prompt will not import should still
    refuse an object with no action rather than hand it to the dispatcher.
    """
    try:
        import agent_prompt
    except Exception:
        action = obj.get("action")
        return None if isinstance(action, str) and action else "Missing 'action' key in JSON"
    return agent_prompt.validate_action(obj)


def _effort_setting(effort, name, fallback):
    try:
        import agent_config
        return getattr(agent_config, name)(effort or None)
    except Exception:
        return fallback


def _activity_label(action, obj):
    """The five-word label a card shows while this action runs.

    Built from the action and whichever key names its target, because "Read
    Lines" alone is the same label for the fortieth read as for the first and
    a panel showing it says only that the agent is still alive. A path is
    shown by its last segment: the card is about twenty columns wide and the
    directory is the half that repeats.
    """
    try:
        import agent_actions
        label = agent_actions.ACTION_LABELS.get(action) or action.replace("_", " ").title()
    except Exception:
        label = str(action).replace("_", " ").title()
    target = ""
    for key in ("path", "query", "name", "target", "search", "app", "message"):
        value = obj.get(key) if isinstance(obj, dict) else None
        if isinstance(value, str) and value.strip():
            target = value.strip()
            break
    if target:
        target = target.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return clip_activity(("%s %s" % (label, target)).strip())


def _result_message(action, result):
    """What the model is told after an action ran.

    Deliberately not `agent_actions.build_result_message`: that one ends a
    read with "Now output a respond action", and `respond` is exactly the verb
    a worker is forbidden. Telling a worker to emit a refused action would
    spend a retry on advice this loop had given it.
    """
    text = str(result)
    if "SyntaxError" in text:
        return ("FAILED with SyntaxError: %s\nOutput a corrected action that "
                "fixes the exact syntax error above." % text)
    if action == "patch_file" and "Search text not found" in text:
        return ("FAILED: %s\nThe search string did not match exactly. Use "
                "find_text or read_lines, then retry." % text)
    return ("Result: %s\nOutput your next action, or internal_response when "
            "the task is done." % text)


def _record_paths(manager, record, action, obj, result=None):
    """Tell the manager what this action wrote: which files, and how much.

    Only for mutating actions, and the paths are taken from the request rather
    than parsed back out of the result, because the request is where a path is
    a fact. This is what lets the main AI be told that two workers wrote the
    same file -- the whole of the concurrent-write story, and enough of it.

    The line counts go through `agent_actions.action_event`, which is the same
    function the main loop's own counter reads. That matters more than saving
    the call would: it means a line a worker wrote is counted by exactly the
    rule a line the main agent wrote is counted by, including the awkward
    cases -- a `write_file` over an existing file reports only what it wrote,
    because what it replaced was gone before anyone could count it, and an
    event with nothing certain to report contributes nothing rather than a
    zero. Two counters with two rules would drift, and the meter would be
    adding up numbers that did not mean the same thing.
    """
    try:
        import agent_actions
        import agent_config
        if action not in agent_config.MUTATING_ACTIONS:
            return
        paths = agent_actions._paths_named(action, obj)
    except Exception:
        return
    if paths:
        manager.note_paths(record.id, paths)
    try:
        event = agent_actions.action_event(action, obj, result)
        detail = (getattr(event, "detail", None) or {}) if event is not None else {}
    except Exception:
        # Counting is a readout, never the work. An action that ran must not
        # be undone by a failure to measure it.
        return
    if detail.get("added") is not None or detail.get("removed") is not None:
        manager.add_lines(record.id, detail.get("added"), detail.get("removed"))


def _invalidate_prompt():
    """Drop the cached system prompt after a worker changed the workspace.

    The snapshot in it describes files a worker has just rewritten, and the
    main AI is the one that would otherwise go on reasoning about the old
    version. Guarded, because failing to invalidate a cache must never be the
    thing that ends an agent.
    """
    try:
        import agent_prompt
        agent_prompt.invalidate_prompt()
    except Exception:
        pass


class _StreamSink:
    """The worker's `on_event` handler: tokens in, cancellation out.

    It is the second cancellation boundary. Raising from here unwinds the
    iteration over the response, which is the only way to stop reading a
    stream that has no abort primitive -- and even where `ask_model` catches
    it on the way out and returns a fabricated reply instead, the step loop's
    own check catches the flag before anything is dispatched. The guarantee
    does not depend on the exception escaping.
    """

    def __init__(self, record, manager):
        self._record = record
        self._manager = manager
        self._settled = 0
        self.chars = 0
        self.error = ""

    def begin(self, settled):
        """Start a new request. `settled` is the output tokens before it."""
        self._settled = int(settled)
        self.chars = 0
        self.error = ""

    def handle(self, event):
        _guard(self._record)
        if not isinstance(event, (tuple, list)) or not event:
            return
        kind = event[0]
        value = event[1] if len(event) > 1 else None
        try:
            if kind == "output":
                self.chars += int(value or 0)
                pending = _tokens_for_chars(self.chars)
                # An estimate while it streams, so the figure moves as the
                # work happens; marked inexact so the card prints `~`, and
                # replaced outright by the provider's own count below.
                self._manager.set_pending_output(self._record.id, pending)
                self._manager.set_tokens(self._record.id,
                                         tokens_out=self._settled + pending,
                                         output_exact=False)
            elif kind == "usage":
                reported = int(value)
                self._manager.set_pending_output(self._record.id, reported)
                self._manager.set_tokens(self._record.id,
                                         tokens_out=self._settled + reported,
                                         output_exact=True)
            elif kind == "input_usage":
                self._manager.set_tokens(self._record.id, tokens_in=int(value),
                                         input_exact=True)
            elif kind == "error":
                self.error = str(value)
        except (TypeError, ValueError):
            # A malformed event is a token figure that cannot be counted, not
            # a reason to end an agent. Nothing is recorded rather than
            # something invented.
            pass


def _refusal(action, allowed, forbidden, read_only=_NOT_A_NOTE_VERB):
    """The sentence refusing an action, or "" when it may run.

    `read_only` is why this agent's whitelist exists, in its own words. It is
    passed down rather than looked up because two whitelists can hold the same
    verbs and mean different things by it: the note agent and the reviewer are
    both read-only and are read-only for different reasons, and the reason is
    the half of the sentence a model actually acts on.
    """
    if action in WORKER_NEEDS_TERMINAL:
        # Checked ahead of both sets below, so the sentence names the real
        # reason. Told it was "not a worker verb" the model would reasonably
        # try to reach the same effect another way; told the confirmation
        # cannot happen, it reports the path instead.
        return _REFUSED % (action, _NEEDS_A_TERMINAL)
    if action in forbidden:
        return _REFUSED % (action, _NOT_A_WORKER_VERB)
    if allowed is not None and action not in allowed:
        return _REFUSED % (action, read_only)
    return ""


def _stop(manager, record, sentence):
    """End an agent that produced no response, and return the reason.

    Recorded as a failure rather than returned as an answer, because a
    sentence describing why there is no answer is not an answer, and the main
    AI reading it as one would repeat the whole mistake `is_synthetic` exists
    to prevent. `manager.result()` falls back to the error, so the words still
    reach whoever was waiting.
    """
    manager.fail(record.id, sentence)
    return sentence


def _run_loop(record, manager, ask, execute, system_prompt, allowed, forbidden,
              read_only=_NOT_A_NOTE_VERB):
    """The step loop every kind of agent runs. Returns its response string."""
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": record.task}]
    # The worker's own conversation, and never anybody else's. Assigned to the
    # record so the panel and the tests can see it; it is not shared, not
    # merged, and not read by any other agent.
    record.conversation = messages
    manager.set_activity(record.id, "Starting")

    # An effort the spawner named decides both the reply ceiling and the step
    # budget, exactly as it does for the main AI. Without one the reply
    # ceiling follows the session's current setting -- which is what passing
    # None to `max_tokens_for_effort` means -- and the step budget is this
    # module's own, because a delegated task is a piece of work rather than a
    # user-facing exchange and the two are not the same size.
    max_tokens = _effort_setting(record.effort, "max_tokens_for_effort", None)
    rounds = (_effort_setting(record.effort, "rounds_for_effort", WORKER_ROUNDS)
              if record.effort else WORKER_ROUNDS)
    sink = _StreamSink(record, manager)

    steps = retries = identical = 0
    last_raw = None
    acted = False
    settled_out = 0

    def hand_back(said, feedback):
        """Give the model its own mistake back. False when the budget is out.

        The reply goes in as the assistant turn and the complaint as the user
        turn, so the model is looking at exactly what it wrote, and nothing
        the agent has already done this run is lost or repeated.
        """
        nonlocal retries
        retries += 1
        if retries > MAX_INVALID_RETRIES:
            return False
        messages.append({"role": "assistant", "content": said})
        messages.append({"role": "user", "content": feedback})
        return True

    def dispatch(obj, action):
        """Run one action. The cancellation check is the line above the call.

        Nothing may be inserted between `_guard` and `execute`. That gap is
        the whole of the guarantee that a killed worker writes no more files.
        """
        manager.set_activity(record.id, _activity_label(action, obj))
        _guard(record)
        result = execute(obj, _context(record))
        _record_paths(manager, record, action, obj, result)
        _invalidate_prompt()
        return result

    nudged = [False]

    def agenda_nudge():
        """The reminder tail for a reviewer that has not declared its agenda.

        Once per run, and only for an agent that HAS the verb -- a worker or a
        note agent would be told to emit an action it is refused. Guarded to
        the empty string throughout: this is a line of advice, and a reviewer
        must not be able to fail because a reminder could not be built.
        """
        if nudged[0] or AGENDA_ACTION not in (allowed or ()):
            return ""
        try:
            if len(record.agenda or ()):
                return ""
        except Exception:
            return ""
        nudged[0] = True
        return _AGENDA_MISSING

    while steps < rounds:
        _guard(record)
        # Reported at the top of the step rather than at the end of it, so the
        # bar on this agent's row moves when the work starts rather than once
        # it is already over.
        manager.set_steps(record.id, steps, rounds)
        # The request about to go, not the run's total spend. The API is
        # stateless, so every step resends the whole conversation, and adding
        # those up would report a context that had quadrupled when the same
        # messages had merely been sent four times. Marked inexact until a
        # provider reports its own prompt-token count.
        manager.set_tokens(record.id,
                           tokens_in=_tokens_for_chars(_message_chars(messages)),
                           input_exact=False)
        sink.begin(settled_out)
        raw = ask(messages, on_event=sink.handle, model=record.model or None,
                  max_tokens=max_tokens, quiet=True)
        # A kill that landed while the request was in flight arrives here. The
        # stream handler raises, but `ask_model` catches broadly on its way
        # out and can turn the exception into a fabricated reply, so the flag
        # is checked again rather than trusted to have escaped.
        _guard(record)
        raw = "" if raw is None else str(raw)
        # Whatever the last request cost is now part of the total, and the
        # next one starts its estimate from there instead of from zero.
        settled_out = max(settled_out, record.tokens_out)
        manager.set_pending_output(record.id, 0)

        # Counted as replies, not as repeats. TMT.py's own breaker counts
        # repeats and so takes four requests to report "three times in a
        # row"; here the sentence and the count agree, and a worker nobody is
        # watching stops one request sooner.
        identical = identical + 1 if raw == last_raw else 1
        last_raw = raw
        if identical >= 3:
            # Three identical replies is a model that is not correcting
            # anything. Waiting for the retry budget to confirm that would
            # spend six more requests to learn what this one already says.
            return _stop(manager, record,
                         "stopped: the model sent the same reply three times "
                         "in a row without making progress")

        try:
            obj = json.loads(raw)
            _adopt_verb(obj)
        except (ValueError, TypeError) as error:
            if hand_back(raw, _UNREADABLE % error):
                continue
            return _stop(manager, record,
                         "stopped: the model's reply could not be read as JSON "
                         "after %d attempts" % retries)
        if not isinstance(obj, dict):
            if hand_back(raw, _UNREADABLE % "the reply was not a JSON object"):
                continue
            return _stop(manager, record,
                         "stopped: the model did not send a JSON object after "
                         "%d attempts" % retries)

        parse_failed, provider_failed, prose = _reply_flags(obj)
        if parse_failed:
            complaint = str(obj.get("message") or "the reply could not be read")
            if hand_back(raw, _UNREADABLE % complaint):
                continue
            return _stop(manager, record,
                         "stopped: the model's reply could not be read after "
                         "%d attempts" % retries)
        if provider_failed:
            # Nobody's to correct: the call itself did not land, and asking
            # the same provider again would not land either.
            return _stop(manager, record,
                         str(obj.get("message") or "the provider call failed"))
        if prose:
            said = " ".join(str(obj.get("message") or "").split())
            if acted and said:
                # The model wrote a sentence after doing the work. That
                # sentence is its account of what it did, in its own words,
                # and discarding it would throw away the only report of work
                # that actually happened.
                return said
            if hand_back(raw, _PROSE_FEEDBACK):
                continue
            return _stop(manager, record,
                         "stopped: the model wrote prose instead of an action "
                         "%d times without doing any work" % retries)

        if isinstance(obj.get("actions"), list) and obj["actions"]:
            outcome, response, ran = _run_batch(
                obj["actions"], record, manager, dispatch, allowed, forbidden,
                read_only=read_only)
            if ran:
                acted = True
            if outcome == "response":
                return response
            if outcome == "invalid":
                if hand_back(raw, response):
                    continue
                return _stop(manager, record,
                             "stopped: the model could not produce a usable "
                             "batch in %d attempts" % retries)
            steps += 1
            retries = 0
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content":
                             "Batch results:\n%s\nOutput your next action, or "
                             "internal_response when the task is done."
                             % "\n".join(response) + agenda_nudge()})
            continue

        # Read before validation, deliberately. `internal_response` is
        # registered by agent_config like any other action, but a worker whose
        # install has not caught up with that must still be able to finish --
        # an agent that could not say it was done would run to its step limit
        # and be recorded as a failure over a schema table.
        if obj.get("action") == TERMINAL_ACTION:
            return str(obj.get("response", ""))

        invalid = _validate(obj)
        if invalid:
            if hand_back(raw, "INVALID: %s. Output a corrected action JSON." % invalid):
                continue
            return _stop(manager, record,
                         "stopped: the model could not produce a valid action "
                         "in %d attempts" % retries)

        action = obj["action"]
        refusal = _refusal(action, allowed, forbidden, read_only)
        if refusal:
            if hand_back(raw, refusal):
                continue
            return _stop(manager, record,
                         "stopped: the model kept asking for actions it is not "
                         "allowed (%d attempts)" % retries)
        if action == SEND_MESSAGE:
            # Valid, and pointless here: nothing a background agent writes is
            # shown to anyone. It costs a step and the model is told why.
            steps += 1
            retries = 0
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": _MESSAGE_SENT})
            continue
        if action == AGENDA_ACTION:
            # The reviewbot's readout, applied here rather than dispatched.
            # `_refusal` above has already turned it away for every agent whose
            # whitelist does not carry it, so reaching this line means the
            # reviewer, and only the reviewer.
            #
            # It costs a step, because it is a whole request to a model and
            # pretending otherwise would let a reviewer spend its budget on the
            # readout and run out before it reviewed anything. A refusal is
            # fed back as an ordinary result rather than through `hand_back`:
            # the agenda is a display, and a reviewer that got the shape wrong
            # should read the correction and carry on reviewing, not spend a
            # retry proving it can operate a checklist.
            steps += 1
            retries = 0
            result = manager.apply_agenda(record.id, obj)
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": _AGENDA_RESULT % result})
            continue

        try:
            result = dispatch(obj, action)
        except WorkerCancelled:
            raise
        except Exception as error:
            if hand_back(raw, _ACTION_RAISED % (action, type(error).__name__, error)):
                continue
            return _stop(manager, record,
                         "stopped: the action '%s' kept failing to run (%s: %s)"
                         % (action, type(error).__name__, error))
        acted = True
        steps += 1
        retries = 0
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user",
                         "content": _result_message(action, result) + agenda_nudge()})

    return _stop(manager, record,
                 "stopped: ran out of steps after %d actions without finishing"
                 % steps)


def _run_batch(batch, record, manager, dispatch, allowed, forbidden,
               read_only=_NOT_A_NOTE_VERB):
    """Run a batch of actions. Returns (outcome, payload, ran_anything).

    `outcome` is "response" when an entry finished the agent, "invalid" when
    one could not be used -- in which case the payload is the complaint,
    carrying what did run before it, so nothing is silently thrown away -- and
    "ok" when the whole batch ran, in which case the payload is the list of
    result lines.
    """
    results = []
    ran = False
    for entry in batch:
        _adopt_verb(entry)
        if not isinstance(entry, dict):
            return "invalid", _batch_complaint(
                "every entry in 'actions' must be a JSON object", results), ran
        if entry.get("action") == TERMINAL_ACTION:
            return "response", str(entry.get("response", "")), ran
        invalid = _validate(entry)
        if invalid:
            return "invalid", _batch_complaint(invalid, results), ran
        action = entry["action"]
        refusal = _refusal(action, allowed, forbidden, read_only)
        if refusal:
            return "invalid", _batch_complaint(refusal, results), ran
        if action == SEND_MESSAGE:
            results.append("%s: nothing was shown; nobody sees your messages" % action)
            continue
        if action == AGENDA_ACTION:
            # Answered here as well as on the single-action path, because a
            # batch dispatches through the same `execute` and the agenda is
            # not reachable from there. Without this a reviewer that put its
            # agenda in a batch would be told the verb belongs somewhere else
            # while looking at the one place it does belong.
            results.append("%s: %s" % (action, manager.apply_agenda(record.id, entry)))
            continue
        try:
            result = dispatch(entry, action)
        except WorkerCancelled:
            raise
        except Exception as error:
            return "invalid", _batch_complaint(
                _ACTION_RAISED % (action, type(error).__name__, error), results), ran
        ran = True
        results.append("%s: %s" % (action, result))
    return "ok", results, ran


def _batch_complaint(problem, results):
    ran = "\n".join(results) if results else "Nothing ran."
    return "INVALID: %s\nRan before it:\n%s\nOutput a corrected action." % (problem, ran)


def _context(record):
    """The authority a background agent runs with.

    `push_authorized` is absent, so `git_push` returns PUSH_BLOCKED even if
    the whitelist above were wrong -- belt and braces, and the belt is the
    part the user's safety actually rests on. `manager` is absent too, so the
    orchestration actions report themselves unavailable rather than letting a
    worker spawn workers of its own.
    """
    return {"push_authorized": False, "agent_id": record.id,
            "agent_kind": record.kind}


def run_worker(record, manager, ask=None, execute=None, system_prompt=None):
    """Run one background worker to completion. Returns its response string.

    `ask` defaults to `agent_model.ask_model`, `execute` to
    `agent_actions.execute_action`, and `system_prompt` to
    `agent_subprompts.worker_prompt()`. All three are injectable so a test
    needs neither a model nor a real workspace.

    Raises WorkerCancelled if the agent is killed while it runs; the manager's
    thread wrapper expects that and leaves the record KILLED.
    """
    return _run_loop(record, manager,
                     ask or _default_ask(),
                     execute or _default_execute(),
                     system_prompt if system_prompt is not None else _default_prompt("worker"),
                     allowed=None, forbidden=WORKER_FORBIDDEN)


def run_note(record, manager, ask=None, execute=None, system_prompt=None):
    """Run the note agent: one question, answered by reading the workspace.

    The same loop with `NOTE_ACTIONS` as a whitelist. Read-only is enforced
    here, before dispatch, and not left to the prompt: a prompt is a request
    and this is a guarantee.
    """
    return _run_loop(record, manager,
                     ask or _default_ask(),
                     execute or _default_execute(),
                     system_prompt if system_prompt is not None else _default_prompt("note"),
                     allowed=NOTE_ACTIONS, forbidden=WORKER_FORBIDDEN)


def run_review(record, manager, ask=None, execute=None, system_prompt=None):
    """Run the review agent: one independent audit of work it did not write.

    The same loop again, with `REVIEW_ACTIONS` as a whitelist. Read-only is
    enforced here, before dispatch, and is not left to the prompt: the brief
    asks for a reviewer that cannot silently modify code, and a prompt is a
    request where this is a guarantee.

    Its `record.task` is the review brief -- the user's request, the plan, the
    git status and the diff, built by `agent_review.ReviewSnapshot.describe`.
    Everything past that it fetches itself with its read tools, which is the
    difference between a reviewer that looked at the repository and one that
    was handed a summary and agreed with it.

    What comes back is the `response` of its single `internal_response`, and
    `agent_review.parse_result` is the only thing allowed to turn that into a
    review verdict.
    """
    return _run_loop(record, manager,
                     ask or _default_ask(),
                     execute or _default_execute(),
                     system_prompt if system_prompt is not None else _default_prompt("review"),
                     allowed=REVIEW_ACTIONS, forbidden=WORKER_FORBIDDEN,
                     read_only=_NOT_A_REVIEW_VERB)
