"""Command-line entry point for TMT, the CLI coding agent."""

import argparse
import json
import shutil
import sys
from pathlib import Path

# An editable install writes a fixed list of the modules it maps, built at
# install time. A module added to the project afterwards is invisible to it, so
# the console script loads this file and then fails importing a sibling that is
# sitting right beside it. Putting this file's own directory on the path first
# makes the siblings resolvable however TMT was started -- console script, -m,
# or the file directly -- and means a new module never needs a reinstall.
#
# First rather than last on purpose: TMT now runs inside arbitrary projects,
# and a project of its own with a file named agent_config.py would otherwise
# shadow TMT's.
_INSTALL_DIR = str(Path(__file__).resolve().parent)
if _INSTALL_DIR in sys.path:
    sys.path.remove(_INSTALL_DIR)
sys.path.insert(0, _INSTALL_DIR)

import agent_config
import agent_menu
from agent_menu import (
    BottomPad, PromptBox, TypeAhead, clear_screen, opening_pad, render_command,
    render_status, render_task, run_startup,
)
from agent_config import (
    MUTATING_ACTIONS, console, default_workspace, set_workspace_root,
    workspace_needs_confirmation, workspace_refusal,
)
from agent_config import REQUIRED_KEYS
from agent_actions import authorizes_push, batch_summary, build_result_message, execute_action, trim_messages
from agent_actions import READ_ONLY_ACTIONS, ACTION_LABELS, MAX_TURNS, action_event
# The two verbs that talk to the user, and the translation of the names
# they used to have. Imported rather than spelled out again: the loop's
# terminal check and the dispatcher's own branch must be the same string,
# and two copies of it are two chances for the turn to stop ending.
from agent_actions import END_CONVERSATION, SEND_MESSAGE, adopt_verb as _adopt_verb
# The paths an action named, taken from the request rather than parsed back
# out of the result. Imported rather than reimplemented: `note_work` needs
# exactly the list the transcript's own events are built from, and a second
# copy of that rule here would drift from the first without anything failing.
from agent_actions import _paths_named
from agent_model import (
    PARSE_FAILURE, ask_model, is_prose, is_synthetic, synthetic_reason,
)
from agent_ui import (
    OPENING_SUGGESTION, RUNNING_HINT, AgentEvent, LiveUI, ResponseHistory,
    Transcript, fallback_suggestion, render_response, validate_suggestion,
    wrap_lines,
)
import agent_commands
import agent_manager
import agent_panel
from agent_session import Session
from agent_live_renderer import LiveRelay
from agent_setup import ensure_api_key, ensure_git_identity
from agent_splash import run_splash
from agent_prompt import get_system_prompt, invalidate_prompt, validate_action
from agent_execution import APP_REGISTRY, RUNNERS, open_app, run_file, run_python
from agent_file_ops import (
    append_file, copy_file, create_folder, delete_file, delete_folder, list_files,
    patch_file, read_file, read_lines, replace_lines, safe_path, search_files,
    write_file, write_files,
)

def stream_handler(live_ui, relay, state, transcript=None, session=None):
    """Turn model stream events into UI updates.

    Two destinations, and which one an event goes to is the whole distinction
    the interface rests on. The temporary ones -- the progress bar, the
    thinking word, the token and elapsed readout, the streamed reply -- go to
    `live_ui` and `relay`, which repaint one region in place. A progress
    message goes to the `transcript` instead, where it is printed once and
    kept: it is something the user is meant to still have at the end of the
    turn, and a row that gets repainted is exactly how it used to be lost.

    Progress is emitted here, as it arrives off the stream, rather than when
    the object finishes parsing. That is what makes it live.

    Only real generated content moves the UI on. Text never advances progress,
    so a long reply cannot produce one step per token.
    """
    def handle(event):
        kind, value = event
        if kind == "first_content":
            live_ui.meaningful_output()
        elif kind == "text":
            relay.feed(value)
        elif kind == "output":
            live_ui.add_output(value)
            # The same characters, to the same running total the meter reads,
            # so the readout climbs while the reply arrives instead of
            # standing still and then jumping when the turn ends.
            if session is not None:
                session.note_output(value)
        elif kind == "usage":
            live_ui.settle_tokens(value)
            # The provider's own count of what it generated. Kept so the
            # session's running total can be the exact figure rather than the
            # estimate it would otherwise have to fall back on.
            state["usage"] = value
        elif kind == "action":
            live_ui.intermediate_event(ACTION_LABELS.get(value, "Processing..."))
        elif kind == "progress":
            if transcript is not None and str(value).strip():
                transcript.emit_kind("progress", str(value).strip())
                state["progress_seen"].add(str(value).strip())
        elif kind == "next_step":
            # Held, not shown. It belongs immediately before the final block,
            # which has not been written yet.
            state["next_step"] = str(value)
        elif kind == "error":
            state["error"] = value
    return handle


# How many unusable replies in a row a question may absorb before it is given
# up on. Unusable means the model produced nothing the loop could act on --
# JSON that would not parse, an action that failed validation, arguments the
# action itself rejected. Every one of those is handed back with the error so
# the model can correct it, which is a thing models are reliably good at, so
# the ceiling is here only to stop a model that cannot be corrected from being
# asked forever. It is deliberately not the round budget: a reply that could
# not be read is not a step of the user's work, and charging it as one meant a
# model with a comma out of place could exhaust a question it had not started.
MAX_INVALID_RETRIES = 6

# What the model is told when its reply could not be read at all. It names the
# parser's own complaint, because "invalid JSON" without the position is a
# thing to guess at rather than a thing to fix.
_UNREADABLE_FEEDBACK = (
    "INVALID: that reply could not be read as JSON. The parser said: %s\n"
    "Reply with exactly one JSON object and nothing else -- no prose before "
    "it, no prose after it, no code fences. Emit the action you meant."
)

# And what the turn records when the model never managed a usable reply. It
# says how many attempts it took rather than "it failed", because the next
# turn is shown this sentence and a count is a fact it can act on.
_UNUSABLE_OUTCOME = "the model could not produce a usable action in %d attempts"

# What the model is told when the action was well formed but would not run.
# `execute_action` reads its arguments straight out of the object, so a key of
# the wrong type raised out of the loop and took the whole program with it --
# a model writing "start": "12" instead of 12 could end the session. The types
# are named in the complaint because that is the mistake being made.
_ACTION_RAISED = (
    "INVALID: the action '%s' could not run with those arguments -- it raised "
    "%s: %s\nCheck the type of every key you sent: paths and text are strings, "
    "line numbers are unquoted numbers, flags are unquoted true or false. Emit "
    "a corrected action."
)

# What the model is told after a `send_message`, so the turn goes on. It says
# the task is NOT finished in as many words, because the one mistake this verb
# invites is a model treating "I have told the user" as "I am done".
_MESSAGE_SENT = ("That was shown to the user. It did NOT end the task and the "
                 "task is not finished: send_message never ends anything. "
                 "Carry on with the work, and use end_conversation only when "
                 "you have actually finished.")

# What the USER is shown when a final answer is refused because the plan is
# not finished. One line, naming the step being waited on, because the plan
# itself is on screen a few columns to the right and repeating it here would
# say the same thing twice.
_PLAN_HELD = "Plan not finished - %d step%s outstanding, next is %s. Continuing."


def plan_block(session, obj):
    """The refusal for a terminal action the plan will not allow yet, or "".

    THE enforcement point for the planning contract, and the reason it is code
    in the loop rather than a line in the prompt: a rule the model is merely
    told about is a rule the model gets to decide it has satisfied. Here, a
    `respond` that arrives with steps outstanding is not executed at all. It
    is handed back as the model's own next input, with the outstanding steps
    named, and the turn carries on -- so a model that has decided it is
    finished simply finds itself still working.

    It cannot trap a session. Nothing here loops: the refusal costs a round
    from the turn's existing budget, and a model that keeps sending the same
    reply meets the identical-reply circuit breaker after three. Both of those
    end the turn with the reason recorded and the unfinished plan still on
    screen, which is the honest ending for work that was not finished.

    Three things are exempt, and each for its own reason.

    A turn with NO PLAN is not gated, which is most turns. The gate is a
    consequence of having made a plan, not a requirement to make one.

    A SYNTHETIC reply is one this program made up to report a failed call.
    There is no model behind it to send back to, and refusing it would hide a
    provider failure behind a plan the model never had the chance to finish.

    A PLAN THAT CANNOT ANSWER lets the answer through. Every other guard in
    this loop fails in that direction, and a broken plan object holding a
    finished piece of work hostage would be the worst outcome available.
    """
    if session is None or not isinstance(obj, dict):
        return ""
    action = obj.get("action")
    if not action or is_synthetic(obj):
        return ""
    try:
        import agent_plan
        return agent_plan.refusal(getattr(session, "plan", None), action)
    except Exception:
        return ""


def plan_held_line(session):
    """The one line the user is shown when an answer was held back."""
    outstanding = ()
    try:
        outstanding = session.plan.outstanding()
    except Exception:
        pass
    if not outstanding:
        return "Plan not finished. Continuing."
    return _PLAN_HELD % (len(outstanding), "" if len(outstanding) == 1 else "s",
                         "%s %s" % (outstanding[0].id, outstanding[0].title))


def review_block(session, obj):
    """The refusal for a terminal action the review will not allow yet, or "".

    The second half of the completion gate, sitting beside `plan_block` at
    both terminal sites and asked immediately after it. Two conditions, and
    NEITHER excuses the other: a complete plan does not excuse a review that
    failed, and a passed review does not excuse a plan with steps left. The
    brief asks for both to be enforced and this is what "both" means -- two
    questions asked in sequence, each able to hold the answer on its own.

    It is code and not a prompt rule for the reason the plan's gate is: a
    model told it must be reviewed is a model that can decide it has been.
    Here, a `respond` that arrives without a passing review is not executed at
    all. And it cannot be argued into passing either, because nothing the
    model writes reaches `ReviewState`: the only thing that moves that state
    is `agent_review.parse_result` over a reviewer agent's own output.

    Exempt on the same three grounds `plan_block` is exempt, and one more.
    A turn where no review is REQUIRED is not gated, which is most turns. A
    SYNTHETIC reply is a report this program made up about a failed call and
    there is no model behind it to send back to. A state object that RAISES
    lets the answer through. And a task that has been round the review loop
    its full number of times is released rather than held forever -- see
    `agent_review.limit_release`, which is the sentence that ending carries.
    """
    if session is None or not isinstance(obj, dict):
        return ""
    action = obj.get("action")
    if not action or is_synthetic(obj):
        return ""
    try:
        import agent_review
        return agent_review.refusal(getattr(session, "review", None),
                                    getattr(session, "plan", None), action)
    except Exception:
        return ""


def review_held_line(session):
    """The one line the user is shown when an answer was held for review."""
    try:
        import agent_review
        return agent_review.held_line(getattr(session, "review", None),
                                      getattr(session, "plan", None))
    except Exception:
        return "Review not finished. Continuing."


def verify_block(session, obj):
    """The refusal for a terminal action verification will not allow, or "".

    The third condition of the completion gate, and the one that is EVIDENCE
    rather than judgement. The plan says the work was done and the review says
    it looks right; neither of them ran anything. This one holds the answer
    until a command in this repository actually exited zero.

    It is code and not a prompt rule for the reason the other two are, and
    more so: a model told it must verify is a model that can decide it has.
    Here there is nothing to decide with. Nothing the model writes reaches
    `VerificationState` -- there is no key on the `verify` action that carries
    a status, and the only thing that moves that state is a process's exit
    code, read by `agent_verify_engine` from `subprocess`.

    Exempt on the same three grounds `plan_block` is exempt, and two more. A
    turn where no verification is REQUIRED is not gated, which is most turns.
    A task at the cycle limit is released rather than held forever. And a
    repository that offered NOTHING TO RUN is released too, because there is
    no evidence to be had there and a verifier that cannot verify holding
    finished work hostage is the worst outcome available -- see
    `agent_verify.limit_release`, which is the sentence either ending carries.
    """
    if session is None or not isinstance(obj, dict):
        return ""
    action = obj.get("action")
    if not action or is_synthetic(obj):
        return ""
    try:
        import agent_verify
        return agent_verify.refusal(getattr(session, "verify", None),
                                    getattr(session, "plan", None), action)
    except Exception:
        return ""


def verify_held_line(session):
    """The one line the user is shown when an answer was held for verification."""
    try:
        import agent_verify
        return agent_verify.held_line(getattr(session, "verify", None),
                                      getattr(session, "plan", None))
    except Exception:
        return "Verification not finished. Continuing."


# The actions whose whole purpose is to run something. Whether a run PROVED
# anything is not decided here and is not decidable here -- reading a
# program's output for the word "failed" is what once labelled a green test
# run a failure -- so what is recorded is the fact that it ran and what it
# was, and the reviewer is the one that reads the output and judges it.
_VERIFYING_ACTIONS = ("run_file", "run_python")


def note_work(session, action, obj):
    """Tell the review state what this action actually did.

    The one place the runtime learns whether a task is substantial, and it
    learns it from actions that ran rather than from anything the model said
    about them. That is what makes `is_required` unarguable: a model can
    describe its work as small, and it cannot make `write_file` not have
    happened.

    Called after the action, beside the `MUTATING_ACTIONS` check that already
    knows which actions write, so the one place that knows which verbs mutate
    stays `agent_config` and `agent_review` stays pure state.

    Guarded to nothing. A session with no review state, or a state that
    raises, must not be able to end a turn that has already done its work.
    """
    if session is None:
        return
    paths = None
    review = getattr(session, "review", None)
    if review is not None:
        try:
            if action in MUTATING_ACTIONS:
                paths = _paths_named(action, obj)
                review.note_change(action, paths)
            elif action in _VERIFYING_ACTIONS:
                review.note_run(action, str((obj or {}).get("path") or ""))
        except Exception:
            pass
    # The same observation, to the state that gates on evidence. It has to be
    # told separately rather than sharing the review's record, because the two
    # go stale independently: a review passes over a diff and a verification
    # passes over a tree, and an edit after either one invalidates that one
    # whatever the other is doing. Guarded to nothing for the reason above --
    # a state that raises must not end a turn that has done its work.
    verify = getattr(session, "verify", None)
    if verify is not None:
        try:
            if action in MUTATING_ACTIONS:
                if paths is None:
                    paths = _paths_named(action, obj)
                verify.note_change(action, paths)
        except Exception:
            pass


def _panel_refresh(live_panel):
    """A callable that nudges whichever relay is current, or does nothing.

    Handed to the action context so a blocking action can make the right-hand
    column repaint while it works. It closes over the DICT rather than over a
    relay, so it goes on working when the next turn puts a new relay in --
    which is the same indirection `_panel_changed` already uses, for the same
    reason: a subscription per turn would stack a dead listener per question.

    Guarded to nothing. A repaint that fails is a repaint that did not happen,
    and it must never be able to fail the action that asked for it.
    """
    def refresh():
        relay = live_panel.get("relay")
        if relay is None:
            return
        try:
            relay.refresh()
        except Exception:
            pass
    return refresh


def note_capability_choices(session):
    """Turn this turn's authorisation into the two states' completion rules.

    The one place a capability becomes a REQUIREMENT, and the reason it is one
    place is that the rule is one rule: a capability the user asked for must
    be satisfied before the answer goes out, and a capability they did not ask
    for must never hold an answer up.

    Both states already had exactly the right mechanism for this.
    `user_choice` is consulted first by `is_required` and is final in both
    directions, so setting it True means "this turn cannot end without one"
    and False means "nothing here can hold the answer". What changes is only
    where the answer comes from: it used to be read out of the task's PROSE --
    "please review this", "no need to run the tests" -- and it is now read
    from the capability commands, which are unambiguous and cannot be arrived
    at by accident. `agent_review.requests_review` and
    `agent_verify.requests_verification` still exist and still answer the
    question they always answered; they are simply no longer what authorises.
    That is the point of the feature: "verify this code" is a request to look
    at some code, and `/verify` is a request for the verification engine.

    Never None here, which is the other half of the change. Silence used to
    fall through to the runtime evidence -- enough changed files and a long
    enough plan turned a review on by itself -- and that is precisely the
    automatic activation the user is taking back. Silence now means no.

    The plan needs no equivalent. Its gate fires on a plan EXISTING with steps
    outstanding, and a plan can only exist if the `plan` action ran, which the
    runtime guard refuses without `/plan`. So an unauthorised plan cannot gate
    anything because it cannot be created in the first place.

    Called after `begin_turn`, never before, for the reason the two functions
    it replaces were: the retirement inside `begin_turn` resets every field on
    both states, so a choice recorded before it would be wiped by it. That is
    also where the capabilities themselves are adopted.
    """
    if session is None:
        return
    capabilities = getattr(session, "capabilities", None)
    review = getattr(session, "review", None)
    if review is not None:
        try:
            review.note_user_choice(
                bool(capabilities is not None and capabilities.review))
        except Exception:
            pass
    verify = getattr(session, "verify", None)
    if verify is not None:
        try:
            verify.note_user_choice(
                bool(capabilities is not None and capabilities.verify))
        except Exception:
            pass


def review_release_warning(session):
    """The line a released answer carries, or "" when nothing was released.

    Section 14 of the brief, and the half of it that is easy to get wrong. The
    cycle limit stops the review/fix loop; it must not also quietly stop the
    user being told. When an answer goes out with a review that never passed,
    this says so beside it.
    """
    try:
        import agent_review
        return agent_review.limit_release(getattr(session, "review", None))
    except Exception:
        return ""


def verify_release_warning(session):
    """The line a released answer carries when verification never passed."""
    try:
        import agent_verify
        return agent_verify.limit_release(getattr(session, "verify", None))
    except Exception:
        return ""


def release_warnings(session):
    """Every line a released answer has to carry, in pipeline order.

    Both gates can release for their own reasons and both releases have to be
    said. Silently letting an answer out under one of them would be exactly
    the failure the release mechanism exists to avoid -- an answer that reads
    as though the checks approved it.
    """
    return tuple(line for line in (verify_release_warning(session),
                                   review_release_warning(session)) if line)


def completion_block(session, obj):
    """The whole completion gate: (what to hand the model, what to show the user).

    ("", "") when the answer may go out. Asked at both terminal sites, before
    `execute_action` runs, so a refused `respond` leaves no trace of having
    happened -- it sets no `acted`, emits no event and reaches no user.

    The two conditions are asked in order and the FIRST to refuse decides,
    which is also how the pair is told apart: the line the user sees comes
    from whichever gate produced the refusal, rather than being guessed at
    afterwards from the refusal's wording. Asking the gates again a moment
    later could get a different answer -- a review settling on another thread
    is enough -- and a user shown "plan not finished" for a review that failed
    would go looking for the wrong thing.

    The plan comes first because it is the coarser statement: a turn with
    steps outstanding has not finished the work, and telling it what the
    review made of unfinished work would be answering a question nobody has
    reached yet.
    """
    held = plan_block(session, obj)
    if held:
        return held, plan_held_line(session)
    # Verification before review, which is the order of the pipeline and the
    # order the two are worth asking in. Verification is evidence and costs a
    # subprocess; review is judgement and costs a whole agent run -- and the
    # reviewer's brief carries what verification found, so a review requested
    # before anything ran is a review of unverified work. A model told about
    # the review first would go and get one, and then be told to verify, and
    # then need a second review because the fixes made the first one stale.
    held = verify_block(session, obj)
    if held:
        return held, verify_held_line(session)
    held = review_block(session, obj)
    if held:
        return held, review_held_line(session)
    return "", ""


def is_send_message(obj):
    """Whether this reply talks to the user WITHOUT ending the task.

    One shape means it and there is nothing else to check: the verb.
    `send_message` has no terminal meaning at all, so the turn cannot end on
    it however it is written, whatever other keys ride along, and whatever the
    model forgets. A `send_message` carrying `"final": true` is still a
    message; the key is not read here and is not read anywhere.

    That absence is the feature. This used to be two shapes -- `announce`, and
    `respond` with `final: false` -- and the second was a flag on the action
    that DOES end the task, so forgetting it failed silently in the worst
    direction: "I'll read the parser first" ending the turn with the parser
    unread, the work undone and the user having to ask again. There is no flag
    to forget now. The two meanings are two verbs, and neither can be written
    as the other.

    A reply using the old names never reaches this: `agent_actions.
    canonical_action` has already translated it, and it translates the MEANING
    -- a `respond` that carried `final: false` arrives here as a
    `send_message`, which is what it always meant.
    """
    return isinstance(obj, dict) and obj.get("action") == SEND_MESSAGE


def send_message(obj, transcript):
    """Show a message and return whether there was anything to show.

    It goes to the transcript, not through `finish_response`: it is permanent,
    it belongs in the scrollback with the rest of the turn, and it is not the
    answer, so it must not be drawn in the answer's box.
    """
    said = " ".join(str((obj or {}).get("message") or "").split())
    if not said:
        return False
    transcript.emit_kind("progress", said)
    return True


def declared_events(obj, transcript, state):
    """Emit the public events an action object declared, in order.

    Covers two cases the stream cannot. A blocking request has no stream, so
    its `progress` arrives only here; and the `events` array is read from the
    finished object because a partially parsed array entry is not yet a fact.
    Anything already shown live is skipped, so nothing appears twice.
    """
    if not isinstance(obj, dict):
        return
    progress = obj.get("progress")
    if isinstance(progress, str) and progress.strip() and progress.strip() not in state["progress_seen"]:
        transcript.emit_kind("progress", progress.strip())
        state["progress_seen"].add(progress.strip())
    declared = obj.get("events")
    if isinstance(declared, list):
        for payload in declared:
            transcript.emit(AgentEvent.from_payload(payload))
    step = obj.get("next_step")
    if isinstance(step, str) and step.strip() and not state.get("next_step"):
        state["next_step"] = step


def resolve_suggestion(state, history):
    """The next-step hint to show, guaranteed short and guaranteed true.

    Three sources, in order of preference: what the model offered, a shorter
    rewrite of it, and what the turn's own history says happened. The last of
    those is why the hint can never describe work that was not done -- it is
    read off the events, not invented.

    A hint is decoration. Nothing here may fail a turn, so every path ends in
    a usable string.
    """
    offered = state.get("next_step")
    ok, cleaned, _ = validate_suggestion(offered)
    if ok:
        return cleaned
    if cleaned:
        # Offered but too long. The truncation is already correct and already
        # relevant, which beats a generic line and costs no extra request.
        return cleaned
    return fallback_suggestion(history)

def _console_line():
    """One line from the console, or None when the input has ended.

    The prompt box falls back to this wherever raw keys cannot be read. It
    goes through the same console every other prompt in TMT uses rather than
    reading the stream directly, so encoding, history and interruption behave
    as they already do.
    """
    try:
        return console.input("")
    except (EOFError, KeyboardInterrupt):
        return None


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="tmtcode",
        description="A CLI coding agent that works on one project directory.",
        epilog="Run it inside a project and that project is the workspace. "
               "TMT's own installation is never the workspace unless you are "
               "standing in it.",
    )
    parser.add_argument(
        "project", nargs="?", default=None, metavar="PATH",
        help="the project TMT may modify (default: the current directory). "
             "It selects a directory; it never creates one.",
    )
    parser.add_argument(
        "--dir", dest="dir_option", default=None, metavar="PATH",
        help="the same thing as the positional PATH, kept for existing use.",
    )
    args = parser.parse_args(argv)
    if args.project and args.dir_option and args.project != args.dir_option:
        parser.error(
            "PATH and --dir name different directories; give only one."
        )
    args.directory = args.dir_option or args.project
    return args


def resolve_workspace(directory=None, ask=None):
    """Settle the workspace once, before anything can reach the disk.

    Returns the resolved root, or None when the run must not start. The checks
    are front-loaded on purpose: once TMT is running, every path it touches is
    judged against this root, so this is the decision that bounds the session.
    """
    candidate = directory or default_workspace()
    refusal = workspace_refusal(candidate)
    if refusal:
        console.print(f"[red]Not a usable workspace:[/red] {refusal}")
        console.print("Run TMT from inside a project, or name one with --dir.")
        return None
    root = set_workspace_root(candidate)
    if workspace_needs_confirmation(root):
        console.print(f"\n[yellow]{root}[/yellow] already has files in it and is not a git repository.")
        console.print("TMT can create, overwrite and delete files there, and nothing it does will be recoverable.")
        answer = (ask or console.input)("Use it as the workspace? (y/N): ")
        if answer.strip().lower() != "y":
            console.print("[yellow]Stopped. No files were touched.[/yellow]")
            return None
    return root


def main(argv=None):
    args = parse_args(argv)
    root = resolve_workspace(args.directory)
    if root is None:
        return
    # The launch screen, and it is the first thing on screen on every launch.
    # It comes AFTER the workspace is settled and before everything else:
    # `resolve_workspace` can refuse, and can ask the user a question, and a
    # splash drawn in front of a run that is not going to start would be a
    # welcome to a program that then exits.
    #
    # It may not return. A successful update replaces this process with a
    # fresh one running the code it just pulled, which comes back through
    # here and draws the same screen again -- see agent_update for why that
    # cannot loop.
    #
    # It draws nothing at all and returns "start" when the terminal cannot be
    # driven, so every piped run, every script and the whole test suite reach
    # the agent exactly as they did before this screen existed.
    if run_splash() == "exit":
        return 0
    # Only now: the API credential. That order is the routing the launch
    # sequence asks for -- splash, then the optional update, and only then the
    # question of whether this installation has been configured at all. A
    # first-time user meets the wordmark before they meet a form.
    if not ensure_api_key():
        return
    # Settings may have moved the model since import, and a request built from
    # a stale value would quietly use the wrong one.
    agent_config.refresh_model()
    # And the effort level, for the same reason: it is stored beside the model
    # and would otherwise be written but never read, so /effort would last a
    # session and quietly revert on the next launch.
    agent_config.refresh_effort()
    # And whether TMT checks itself for updates. Read here beside the other
    # two so a setting toggled in the menu is live on the next launch rather
    # than written and never read -- the exact bug refresh_effort exists for.
    agent_config.refresh_auto_update()
    # Once per launch, and never again in this session. It returns immediately
    # when the terminal cannot drive a menu, so a piped or scripted run reaches
    # the agent exactly as it did before this screen existed.
    if run_startup(workspace=root) == "exit":
        return 0
    # Offered once, and never blocking: a missing co-author address stops
    # commits, not the session, and the refusal explains itself when it happens.
    ensure_git_identity()
    # The startup menu has just been on this screen. Clearing it puts the
    # session at the top of the window: the header first, then the questions,
    # the work and the answers reading downward from it in the order they
    # happened. Once the window is full the terminal scrolls and the prompt
    # box, always the last thing written, stays at the foot of it.
    clear_screen()
    return _session_loop(root)


# Shadow text for the second prompt a bare `/note` asks for. It states what
# the line is for, because the box it is drawn in looks exactly like the one
# that takes a task and nothing else on screen would say otherwise.
NOTE_PLACEHOLDER = "Ask one question about this workspace"


# What the status row says while a note is being answered. The note agent is a
# whole model round trip, so this row stands for seconds; it names what is
# happening rather than animating, because nothing about it is measurable.
_NOTE_STATUS = "Answering your note..."


def _blocking_command(run, prompt_box, pad, agent_rows=None):
    """Run a slow command with the screen still looking like a screen.

    `PromptBox.ask` clears its region the moment it returns, and for the five
    commands that only read settings that is invisible -- the answer is
    printed within a millisecond and the box is drawn again under it. `/note`
    is a model round trip, so the same code left the user looking at a
    terminal with no prompt box in it for several seconds, which reads as TMT
    having exited rather than as TMT thinking.

    An ordinary turn already solves this: it builds a `LiveRelay` whose footer
    is the box, so the bottom of the screen keeps its shape while the work
    happens. This gives a blocking command the same treatment. The region is
    taken down before the answer is printed, because `render_command` writes
    straight to the stream and printing past a live region leaves its repaint
    arithmetic pointing at rows that have moved.
    """
    # Drawn to the box's own stream rather than to sys.stdout. They are the
    # same thing in a session and are not in a test, and a live region
    # painting to a different terminal from the box it is drawing is a bug
    # waiting for the first person who redirects one of them.
    relay = LiveRelay(stream=getattr(prompt_box, "stream", None),
                      footer=lambda size=None: prompt_box.running_lines(
                          RUNNING_HINT, size=size),
                      # The same column a turn's region draws. The box itself
                      # no longer composes one when it is a footer -- the
                      # relay owns it -- so a region built without this hook
                      # would take the plan off the screen for the length of
                      # the command.
                      panel=lambda columns, rows: (
                          prompt_box.panel().frame(columns, rows)
                          if prompt_box.panel() else None),
                      pad=pad, agent_rows=agent_rows)
    try:
        relay.set_status(_NOTE_STATUS)
        return run()
    finally:
        # Whatever happened -- an answer, a timeout, or Ctrl-C -- the terminal
        # goes back before anything else is written to it.
        relay.abort()


def _dispatch_command(task, session, manager, slow=None):
    """Answer a slash command, giving the ones that need it the register.

    `agent_commands.dispatch` takes a session and no more, which is right for
    the five commands that only read settings. `/note` needs the session's own
    register instead of the private one it would otherwise build, so that the
    note it starts is the same note the panel and `/agents` can see, and so
    its events reach the live region like any other agent's.

    Anything that is not a command still returns None here, so an ordinary
    task -- including one that merely begins with a path -- goes to the model
    exactly as it always did.
    """
    # With no wrapper supplied a slow command is simply called, so this stays
    # drivable with no terminal at all -- which is what the tests do.
    if slow is None:
        slow = lambda run: run()
    parsed = agent_commands.parse(task)
    if parsed is not None:
        name, argument = parsed
        if name == "note" and argument:
            # The one command that blocks long enough to be noticed. `slow`
            # keeps the box on screen while it runs.
            return slow(lambda: agent_commands.run_note(argument, session,
                                                        manager))
        if name == "agents" and not argument:
            return agent_commands._agents(argument, session, manager)
    return agent_commands.dispatch(task, session)


def _still_running(manager):
    """What is still working, as a phrase, or "" when nothing is.

    Everything a session can have out at once, not only the fleet: the note
    agent and the reviewer live in slots of their own and `active_count`
    counts neither, so asking it alone would offer Settings while a review was
    reading the tree. All three are reasons the provider and the model must
    not move underneath them.

    Guarded to "", which is the safe direction here rather than the cautious
    one: a register that cannot answer must not be able to lock the user out
    of Settings for the rest of the session.
    """
    if manager is None:
        return ""
    try:
        parts = []
        agents = int(manager.active_count())
        if agents:
            parts.append("%d agent%s" % (agents, "" if agents == 1 else "s"))
        for record, name in ((manager.note(), "a note"),
                             (manager.review(), "a review")):
            if record is not None and not record.is_terminal():
                parts.append(name)
        return ", ".join(parts)
    except Exception:
        return ""


def _return_to_menu(session, manager, prompt_box, pad, root):
    """Step out to the startup menu and come back. True to resume, False to quit.

    What `/back` is: the session is left exactly as it is -- the conversation,
    the plan, every running agent -- and the startup menu is drawn over it
    with Resume where Start was. Nothing here touches the session, and that is
    the whole feature; the only state that changes is the screen.

    The settings are re-read on the way back for the reason `main` reads them
    on the way in: the user may have just changed one, and a setting that is
    written and never re-read lasts until the next launch and then quietly
    reverts. That was a real bug once, which is what `refresh_effort` exists
    for.

    A run that cannot draw a menu -- a pipe, a script, the test suite --
    resumes at once without clearing anything. `run_startup` would return
    "start" there anyway; what this avoids is the clear and the second header
    that would follow it, which on a pipe is output nobody asked for.
    """
    if not agent_menu.is_interactive(sys.stdout):
        return True
    choice = run_startup(workspace=root, resuming=True,
                         busy=lambda: _still_running(manager))
    if choice == "exit":
        return False
    agent_config.refresh_model()
    agent_config.refresh_effort()
    agent_config.refresh_auto_update()
    # The menu owned the screen and has just let it go. The session opens
    # again the way it opened the first time -- cleared, header at the top --
    # and the pad is counted again from the row the cursor is now on, which is
    # the one moment it is answerable.
    clear_screen()
    drawn = render_status(workspace=root, prompt=False)
    pad.reset(opening_pad(drawn or 0))
    return True


def _session_loop(root):
    """Ask, answer, repeat, until the user leaves."""
    # The header is drawn once, as the session opens: the wordmark, the date,
    # and the directory this run may write to. Those cannot change while the
    # loop is running, and repeating them before every prompt only pushed the
    # conversation up the screen. The facts that do change -- the clock, the
    # provider, the model -- are stated by the prompt box instead, which is
    # drawn again for every question anyway.
    drawn = render_status(workspace=root, prompt=False)
    # Blank rows enough to put the first prompt box at the foot of the window.
    # They live inside the live region and are given up one at a time as
    # permanent output is printed into it, so the session fills the window
    # downward from the header while the box stays where the eye left it, and
    # once they are gone the terminal's own scrolling keeps it there.
    #
    # Worked out here because here is the one moment it is answerable: the
    # screen has just been cleared, so the cursor is on a row we know rather
    # than one we would have to guess.
    pad = BottomPad(opening_pad(drawn or 0))
    # One session object for the run, and the only place the conversation
    # lives. It is created here and dropped when this function returns, so
    # nothing it holds can reach the next launch.
    session = Session(workspace=root)
    # One register of background agents for the run, beside the session and
    # with the same lifetime. It is built here rather than lazily because two
    # separate things read it -- the delegation actions, through the context,
    # and the panel, through the prompt box -- and a register created on first
    # use would give them one each, so the panel would draw an empty list
    # while workers ran.
    #
    # Its threads are daemons, so a session that ends with workers still
    # running cannot hold the process open.
    manager = agent_manager.AgentManager()
    # One box for the session. It draws its own frame each time it is asked,
    # so nothing needs redrawing between turns, and the console keeps being
    # the line reader on any run that cannot take raw keys -- a pipe, a
    # redirect, the test suite -- so a scripted run behaves as it always did.
    prompt_box = PromptBox(line_reader=_console_line, session=session, pad=pad,
                           completer=agent_commands.completions,
                           completed=agent_commands.completed,
                           manager=manager)
    # Which live region the panel currently belongs to. A worker changing its
    # activity label happens on its own thread, and the relay repaints only
    # when the reply or the status row moves -- so without a nudge the panel
    # would sit still through everything the workers actually did and catch up
    # only when something else forced a frame.
    #
    # One subscription for the whole session, holding the CURRENT relay rather
    # than closing over one: a relay belongs to a single turn and is taken
    # down at the end of it. `refresh` only marks the region dirty, so this
    # stays a notification and never becomes a paint from a worker thread,
    # which is the rule the whole live surface depends on.
    # One builder for the rows drawn under the main progress bar, shared by
    # the turn's own live region and by any blocking command that has to
    # hold the screen. `visible_agents` rather than `list`, so a finished
    # agent's row stays for the retention window and then goes, exactly as
    # its card does -- the two are the same fact drawn twice and they must
    # disappear together.
    #
    # The reviewbot's rows go on the end of the same strip, because it is one
    # of these agents: same loop, same record, same background thread. What it
    # is not is one of the fleet -- it lives in the manager's own review slot
    # rather than in the register, so `visible_agents` never returns it and it
    # had no row at all until it was asked for by name. A review blocks the
    # whole session for as long as it takes, and for the whole of that time
    # the only thing on screen about it was the word "Running" in the column.
    #
    # Last rather than first: the fleet cannot be running at the same time --
    # `agent_actions._review` refuses to start a review while any worker is
    # live -- so in practice one of the two lists is always empty, and the
    # order only decides which way round they would appear if that ever
    # changed. The reviewer is the subordinate of the two.
    agent_rows = lambda columns: (
        agent_panel.agent_status_rows(manager.visible_agents(), columns,
                                      stream=sys.stdout)
        + agent_panel.reviewbot_rows(manager.review(), session.review, columns,
                                     stream=sys.stdout))
    # Lines the user typed while a turn was running. They are taken from
    # the reader when the turn ends and answered before the next question
    # is asked, in the order they were entered.
    queued = []
    live_panel = {"relay": None}

    def _panel_changed(name, record):
        relay = live_panel.get("relay")
        if relay is not None:
            relay.refresh()

    manager.subscribe(_panel_changed)
    placeholder = OPENING_SUGGESTION
    while True:
        if queued:
            # Something the user typed while the last turn was running. It is
            # taken before the box is drawn, so a queued line is answered
            # rather than sitting behind a prompt nobody is looking at, and it
            # is echoed into the scrollback by `render_task` below exactly as
            # a typed one is -- the record must not be able to tell them
            # apart, because the user cannot either.
            answer = queued.pop(0)
        else:
            # Shadow text, and nowhere else. The opening line on the first
            # question and the last turn's hint after that: `ask` returns what
            # was typed, and an untouched box returns the empty line it
            # actually holds, never the line drawn in it.
            answer = prompt_box.ask(placeholder)
            if answer is None:
                break
            if prompt_box.cancelled:
                console.print("[yellow]Use 'quit' or 'exit' to close.[/yellow]")
                continue
        task = answer.strip()
        if task.lower() in {"quit", "exit"}:
            break
        if not task:
            continue
        # A slash command is answered here and never becomes a request. The
        # test is the parser's, not a prefix check: a task that happens to
        # start with a path is not a command and goes to the model exactly as
        # it always did.
        # Slow commands keep the prompt box on screen while they run.
        slow = lambda run: _blocking_command(run, prompt_box, pad,
                                             agent_rows=agent_rows)
        answered = _dispatch_command(task, session, manager, slow=slow)
        if answered is not None:
            render_task(task, moment=prompt_box.asked_at)
            # The rows just printed take blank rows from the pad, the same
            # as any other permanent output, so the box does not move. The
            # placeholder is left alone: asking what the model is does not
            # change what the last turn suggested doing next.
            pad.take(2 + (render_command(answered) or 0))
            # A command that needs a second line asks for it here, on the
            # same box, and its answer is printed like any other command's.
            # Only an interactive run can reach this: the piped reader takes
            # one task per line, so a two-stage prompt is unreachable from a
            # pipe or from the test suite -- which is exactly why the inline
            # `/note <question>` form is the one that works everywhere and
            # this is only the convenience on top of it.
            if answered.prompt_for == "note":
                asked = prompt_box.ask(NOTE_PLACEHOLDER)
                if asked is not None and asked.strip() and not prompt_box.cancelled:
                    render_task(asked.strip(), moment=prompt_box.asked_at)
                    answer = slow(lambda: agent_commands.run_note(
                        asked.strip(), session, manager))
                    pad.take(2 + (render_command(answer) or 0))
            # `/back`. The menu is drawn over the session and the session is
            # untouched behind it: nothing is cleared, nothing is cancelled,
            # no agent is waited for. Choosing Exit there ends TMT, which is
            # the same thing Exit has always meant on that screen.
            if getattr(answered, "to_menu", False):
                if not _return_to_menu(session, manager, prompt_box, pad, root):
                    break
                # The screen is new but the conversation is not, so the hint
                # the last turn left is still the right thing to suggest.
                continue
            continue
        # The question, into scrollback. The box that collected it is a live
        # region and has already been taken down, so this is the only record
        # of what was asked -- and it belongs above the work the answer came
        # out of, which is why it is written here rather than at the end.
        render_task(task, moment=prompt_box.asked_at)
        pad.take(2)              # the caption and the marker row it just wrote
        # Authority to push comes from this task's wording alone, decided once
        # here so nothing the model later writes can widen it.
        # The register goes in the context beside the push authority, because
        # that is how the delegation actions reach it. A context without the
        # key is the honest state of an install where background agents are
        # not available, and every one of those actions answers a missing
        # manager in words rather than raising -- which is also what a
        # background agent's own context looks like, so a worker asking to
        # spawn a worker is told it cannot.
        context = {"push_authorized": authorizes_push(task),
                   "manager": manager,
                   # The plan for THIS task. The session holds one Plan for
                   # its whole life and empties it in `begin_turn`, which runs
                   # a few lines below this -- so the object put in here is
                   # the right one and it is empty by the time the model can
                   # reach it. That is why the session resets the plan in
                   # place rather than assigning a new one.
                   "plan": session.plan,
                   # The review for THIS task, put here for the same reason
                   # and with the same warning as the plan above: the session
                   # holds one ReviewState for its whole life and empties it
                   # in `begin_turn`, a few lines below. Rebinding it there
                   # would leave the review action writing into one object
                   # while the completion gate read another -- the gate
                   # silently off, with nothing anywhere to notice it by.
                   "review": session.review,
                   # The verification for THIS task, put here for the same
                   # reason and with the same warning as the two above: the
                   # session holds one VerificationState for its whole life
                   # and empties it in `begin_turn`, a few lines below.
                   "verify": session.verify,
                   # How the verify action tells the screen a check finished.
                   # `live_panel` is built before this dict and re-pointed at
                   # each turn's relay, exactly as the manager's own panel
                   # subscription is -- so this closure keeps working across
                   # turns without stacking a listener per question. A
                   # verification blocks the loop for as long as its commands
                   # take, and without this the column would stand still for
                   # the whole of it.
                   "refresh": _panel_refresh(live_panel),
                   # Which of the three higher-level capabilities this task's
                   # own wording authorised. Put here for the same reason and
                   # with the same warning as the three states above: the
                   # session holds one Capabilities for its whole life and
                   # re-reads it in `begin_turn`, a few lines below. Rebinding
                   # it there would leave `execute_action` asking a set of
                   # flags nothing writes to -- authorisation switched off,
                   # with no error and nothing on screen to notice it by.
                   #
                   # This is the object the runtime guard reads. It is filled
                   # from the user's typed line and from nothing the model,
                   # a worker or a tool result produced.
                   "capabilities": session.capabilities,
                   # The user's own words, verbatim. The reviewer treats this
                   # as the source of truth and checks the implementation
                   # against it, so it must be what was actually asked rather
                   # than the model's account of what was asked.
                   "task": task}
        # The authorisation is read BEFORE the prompt is built, because the
        # prompt is built out of it: an unauthorised capability is not
        # described at all. `begin_turn` adopts it as well, which is where it
        # belongs -- the capability's whole lifetime is one turn and that is
        # the turn boundary -- and doing it here too is not two sources of
        # truth but the same parser over the same line twice. It has to be
        # here because Python evaluates `get_system_prompt(...)` before the
        # call it is an argument to, so waiting for `begin_turn` would build
        # this turn's prompt from the LAST turn's permissions.
        session.capabilities.adopt(task)
        # The request the turn starts from: the system prompt, the earlier
        # questions and answers this session has already had, and then the new
        # task. `pinned` is how much of that the loop's own trimming must
        # leave alone -- everything up to and including the task.
        messages, pinned = session.begin_turn(
            task, get_system_prompt(session.capabilities))
        # After `begin_turn`, never before: retiring the review resets every
        # field on it, so a choice recorded a few lines earlier -- where the
        # push authority is decided -- would be wiped by the retirement that
        # runs between. Decided once from this task's wording alone, so
        # nothing the model later writes can turn a required review off.
        note_capability_choices(session)
        last_raw, identical_count = "", 0
        live_ui = LiveUI()
        # The live area for this turn, drawn as one block at the foot of the
        # window: the reply as it arrives, then the prompt box, then the
        # status row. The box is in the region rather than left behind above
        # it, so the whole of the bottom of the screen holds still while the
        # turn's permanent output scrolls past over it.
        # The panel rides in this region rather than owning a column of the
        # whole window, and it has to: the rows above are the terminal's own
        # scrollback, which is TMT's only permanent surface, and the two
        # escapes that would let a program take the whole screen (DECSTBM and
        # the alternate buffer) both destroy it. So the live area composes as
        # left content, a gutter, and the panel -- and the scrollback above
        # stays full width and is never redrawn.
        relay = LiveRelay(
            footer=lambda size=None: prompt_box.running_lines(RUNNING_HINT,
                                                              size=size),
            pad=pad,
            panel=lambda columns, rows: (prompt_box.panel().frame(columns, rows)
                                         if prompt_box.panel() else None),
            # One row per background agent, drawn under the main progress bar.
            agent_rows=agent_rows)
        # The one subscriber built before the loop is pointed at this turn's
        # relay. Assigned rather than subscribed again: a subscription per
        # turn would stack a listener for every question ever asked, and each
        # dead one would keep refreshing a region that had already been taken
        # down.
        live_panel["relay"] = relay
        # The turn's permanent record. It is written through the live region
        # rather than straight to the stream: the region is repainted in place
        # a few rows further down, and printing past it without telling it
        # would leave the repaint arithmetic pointing at rows that have moved.
        history = ResponseHistory()

        def write_permanently(text):
            # Every permanent line takes one of the blank rows holding the box
            # against the foot of the window, so the box does not move and the
            # line lands in the space the pad gave up. The count need not be
            # exact -- it only ever decreases, and being a row out means the
            # box sits a row off the bottom until the next line corrects it.
            pad.spend(text)
            return relay.write_above(text)

        transcript = Transcript(history=history, writer=write_permanently)
        # `acted` records whether anything has actually run this turn. It is
        # what tells an announcement apart from a summary: the same sentence
        # ("I'll check the files") means the model is about to start when
        # nothing has run yet, and means it is describing what it did when
        # something has.
        turn_state = {"next_step": "", "progress_seen": set(), "acted": False}
        suggestion = ""
        # Keys taken while this turn runs. The box stops being inert: the
        # user can write the next question, and Enter puts it on the queue
        # instead of interrupting the work. It reads nothing at all on a
        # run that has no raw keys to read, so a piped or scripted run is
        # exactly as it was.
        typeahead = TypeAhead(on_change=relay.refresh)
        prompt_box.typeahead = typeahead
        typeahead.start()
        live_ui.attach_sink(relay.set_status)
        live_ui.start()
        try:
            # Two budgets, because two different things can go wrong and only
            # one of them is the question's to pay for.
            #
            # `rounds` is the work, from the effort setting. Read here rather
            # than fixed, so /effort changes the next question rather than the
            # next launch. A step is spent when the model produced something
            # the loop could act on.
            #
            # `retries` is the model failing to produce anything usable at all
            # -- unreadable JSON, an action that does not validate, arguments
            # the action itself rejects. Those are handed back to be corrected
            # and cost no step: ending the question on one threw away the work
            # already done and made the user ask for it again. They are still
            # bounded, because a model that cannot be corrected must be
            # stopped rather than asked forever.
            rounds = agent_config.rounds_for_effort()
            steps, retries = 0, 0

            def hand_back(said, feedback):
                """Give the model its own error back so it can correct it.

                Returns True when there is budget left to try again, and False
                when the model has had its attempts and the turn has to stop.
                The reply is kept in the conversation as the assistant's turn,
                with the complaint as the user's answer to it, so the model is
                looking at exactly what it wrote when it tries again -- and so
                nothing that already ran this turn is lost or repeated.
                """
                nonlocal retries
                retries += 1
                if retries > MAX_INVALID_RETRIES:
                    return False
                relay.reset()
                messages.extend([{"role": "assistant", "content": said},
                                 {"role": "user", "content": feedback}])
                return True

            while steps < rounds:
                # Rebuilt from THIS turn's authorisation every round, so a
                # prompt dropped by a mutating action comes back teaching the
                # same capabilities it taught before rather than all of them.
                messages[0]["content"] = get_system_prompt(session.capabilities)
                messages = trim_messages(messages, pinned)
                state = {"error": None, "next_step": turn_state["next_step"],
                         "progress_seen": turn_state["progress_seen"],
                         "usage": None}
                # Counted before the request goes, because after it there is
                # no longer a list to measure. An estimate, and the readout
                # says so.
                session.record_request(messages)
                raw = ask_model(messages,
                                on_event=stream_handler(live_ui, relay, state,
                                                        transcript, session))
                session.record_reply(raw, state["usage"])
                turn_state["next_step"] = state["next_step"] or turn_state["next_step"]
                live_ui.meaningful_output()
                if state["error"]:
                    relay.abort()
                    live_ui.attach_sink(None)
                    live_ui.stop()
                    console.print(f"[red]Stream failed:[/red] {state['error']}")
                    turn_state["outcome"] = "the stream failed"
                    break
                identical_count = identical_count + 1 if raw == last_raw else 0
                last_raw = raw
                if identical_count >= 3:
                    relay.release()
                    console.print("[bold red]Circuit Breaker Tripped:[/bold red] Identical response 3 times in a row. Stopping.")
                    turn_state["outcome"] = "the model repeated itself and was stopped"
                    break
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError as error:
                    # Handed back rather than fatal. An action that fails
                    # validation has always been returned to the model to be
                    # corrected; a reply that would not parse was not, and it
                    # ended the turn where it stood -- throwing away whatever
                    # the turn had already done and making the user ask the
                    # same question again. The two are the same kind of
                    # mistake and now get the same answer.
                    console.print(f"[yellow]Unreadable reply, asking again:[/yellow] {error}")
                    if hand_back(raw, _UNREADABLE_FEEDBACK % error):
                        continue
                    relay.release()
                    console.print("[red]Gave up:[/red] the reply still could not be read.")
                    turn_state["outcome"] = _UNUSABLE_OUTCOME % retries
                    break
                # A reply this program made up because it could not read what
                # the model sent. Which failure it stands for decides what to
                # do with it: a parse failure is the model's to correct, so it
                # is handed back like any other unreadable reply, and showing
                # it as the answer put a machine's report of a failure on
                # screen where the user was waiting for their work. A provider
                # failure is nobody's to correct -- the call itself did not
                # land, and asking again would not land either -- so that one
                # falls through and is shown, which is the safety valve.
                if synthetic_reason(obj) == PARSE_FAILURE:
                    complaint = str(obj.get("message") or "the reply could not be read")
                    console.print(f"[yellow]Unreadable reply, asking again:[/yellow] {complaint}")
                    if hand_back(raw, _UNREADABLE_FEEDBACK % complaint):
                        continue
                    relay.release()
                    turn_state["outcome"] = _UNUSABLE_OUTCOME % retries
                    break
                # The model wrote a sentence instead of an action. When it has
                # already done something this turn the sentence is its summary
                # of that work and ends the turn, which is what it has always
                # meant. When nothing has run yet it cannot be a summary -- it
                # is an announcement of work not started -- so it is shown and
                # the model is asked for the action it just described.
                if is_prose(obj) and not turn_state["acted"]:
                    said = str(obj.get("message") or "").strip()
                    if said:
                        transcript.emit_kind("progress", said)
                    if hand_back(raw, "That was prose, and nothing has run yet, so it "
                                      "announced work rather than reporting it. Emit the "
                                      "action you just described, as one JSON object."):
                        continue
                    relay.release()
                    turn_state["outcome"] = _UNUSABLE_OUTCOME % retries
                    break
                # Translated before anything looks at it, so every check
                # below -- the message test, the completion gates, the
                # terminal test, the dispatcher -- sees the two verbs in force
                # now and nothing else has to know the old names. A reply that
                # already uses them is returned untouched.
                _adopt_verb(obj)
                declared_events(obj, transcript, turn_state)
                if "actions" in obj:
                    batch = obj["actions"]
                    if not isinstance(batch, list) or not batch:
                        if hand_back(raw, "INVALID: 'actions' must be a non-empty list. Try again."):
                            continue
                        relay.release()
                        turn_state["outcome"] = _UNUSABLE_OUTCOME % retries
                        break
                    live_ui.intermediate_event("Processing...")
                    results = []
                    invalid = ""
                    held = held_line = ""
                    for sub_obj in batch:
                        # Each entry in its own right: a batch is a list of
                        # actions and an old name can appear in any of them.
                        _adopt_verb(sub_obj)
                        invalid = validate_action(sub_obj)
                        if invalid:
                            # Handed back below so the model can correct it,
                            # exactly as a bad single action is. It used to
                            # break out of both loops onto the bare `break`,
                            # which threw the batch's results away, told the
                            # model nothing, printed nothing, and ended the
                            # turn where it stood -- work done and no word
                            # about why it had stopped.
                            break
                        sub_action = sub_obj["action"]
                        # A batch carries its progress on the entries, not on
                        # the object around them, and the stream only reports
                        # top-level values. Read here, before the action runs,
                        # so the message still arrives ahead of its work.
                        declared_events(sub_obj, transcript, turn_state)
                        # An announcement inside a batch is shown and stepped
                        # over. The entries after it are the work it announced,
                        # and ending the turn on it would leave every one of
                        # them unrun.
                        if is_send_message(sub_obj):
                            send_message(sub_obj, transcript)
                            results.append(f"{sub_action}: shown to the user")
                            continue
                        # The same gate the single-action path takes, in the
                        # same place: before the action runs. A batch that
                        # ends in a refused respond keeps everything it did
                        # before that entry -- those results are real work and
                        # are reported below with the refusal.
                        held, held_line = completion_block(session, sub_obj)
                        if held:
                            break
                        try:
                            result = execute_action(sub_obj, context)
                        except Exception as error:
                            # An action whose arguments it could not read used
                            # to raise straight out of the loop and end the
                            # program. It is the model's mistake and the model
                            # can fix it, so it is handed back with the rest of
                            # the batch's results, exactly as a validation
                            # failure is.
                            invalid = _ACTION_RAISED % (sub_action,
                                                        type(error).__name__, error)
                            break
                        turn_state["acted"] = True
                        session.count_event(
                            transcript.emit(action_event(sub_action, sub_obj, result)))
                        if sub_action in MUTATING_ACTIONS:
                            invalidate_prompt()
                        note_work(session, sub_action, sub_obj)
                        if sub_action == END_CONVERSATION:
                            # Said before the answer, so an answer the review
                            # never approved is never read without the reason
                            # it was let out anyway.
                            for released in release_warnings(session):
                                transcript.emit_kind("warning", released)
                            suggestion = finish_response(live_ui, relay, result,
                                                         transcript, turn_state,
                                                         pad)
                            break
                        # The same reminder a single action gets, so a
                        # model that hides three silent reads inside a
                        # batch is told exactly as plainly as one that
                        # emits them one at a time.
                        results.append(build_result_message(
                            sub_action, f"{sub_action}: {result}", sub_obj))
                    else:
                        relay.reset()
                        # The batch ran and the model is asked what is next, so
                        # this round was work and costs a step.
                        steps += 1
                        retries = 0
                        messages.extend([{"role": "assistant", "content": raw}, {"role": "user", "content": f"Batch results:\n{chr(10).join(results)}\nOutput your next action."}])
                        continue
                    if held:
                        # The batch did its work and was stopped at the
                        # answer. The results go back with the refusal so the
                        # model is not asked to redo what already ran.
                        transcript.emit_kind("warning", held_line)
                        relay.reset()
                        steps += 1
                        retries = 0
                        ran = chr(10).join(results) if results else "Nothing ran."
                        messages.extend([
                            {"role": "assistant", "content": raw},
                            {"role": "user",
                             "content": "Batch results:\n%s\n%s" % (ran, held)}])
                        continue
                    if invalid:
                        console.print(f"[red]Invalid action in batch:[/red] {invalid}")
                        ran = chr(10).join(results) if results else "Nothing ran."
                        if hand_back(raw, f"INVALID: {invalid}\nRan before it:\n{ran}\n"
                                          "Output a corrected action JSON."):
                            continue
                        relay.release()
                        turn_state["outcome"] = _UNUSABLE_OUTCOME % retries
                        break
                    break
                error = validate_action(obj)
                if error:
                    console.print(f"[red]Invalid action:[/red] {error}")
                    if hand_back(raw, f"INVALID: {error}. Output a corrected action JSON."):
                        continue
                    relay.release()
                    turn_state["outcome"] = _UNUSABLE_OUTCOME % retries
                    break
                action = obj["action"]
                # An announcement is shown and the loop goes on. This is the
                # whole of the progress fix: the model saying what it is about
                # to do used to be indistinguishable from the model saying what
                # it had done, so "I'll inspect the files first" ended the turn
                # with nothing inspected.
                if is_send_message(obj):
                    send_message(obj, transcript)
                    relay.reset()
                    live_ui.intermediate_event("Processing...")
                    steps += 1
                    retries = 0
                    messages.extend([{"role": "assistant", "content": raw},
                                     {"role": "user", "content": _MESSAGE_SENT}])
                    continue
                # The plan's gate, and it is taken BEFORE the action runs.
                # `respond` has no side effect worth avoiding, but running it
                # would set `acted` and emit an event for work that was
                # refused -- and an action that is not allowed should not
                # leave a trace of having happened.
                held, held_line = completion_block(session, obj)
                if held:
                    transcript.emit_kind("warning", held_line)
                    relay.reset()
                    live_ui.intermediate_event("Processing...")
                    steps += 1
                    retries = 0
                    messages.extend([{"role": "assistant", "content": raw},
                                     {"role": "user", "content": held}])
                    continue
                try:
                    result = execute_action(obj, context)
                except Exception as error:
                    # The one path that used to end the program rather than the
                    # turn: execute_action reads its arguments straight off the
                    # object, so a key of the wrong type raised through the loop
                    # and out of main. It is the model's mistake, so it is
                    # handed back like every other one.
                    console.print(f"[red]Action failed:[/red] {type(error).__name__}: {error}")
                    if hand_back(raw, _ACTION_RAISED % (action, type(error).__name__, error)):
                        continue
                    relay.release()
                    turn_state["outcome"] = _UNUSABLE_OUTCOME % retries
                    break
                turn_state["acted"] = True
                steps += 1
                retries = 0
                # Counted as it happens, not when the turn ends: the meter
                # has to move when the file is written, not two minutes
                # later when the answer lands.
                session.count_event(
                    transcript.emit(action_event(action, obj, result)))
                if action == END_CONVERSATION:
                    # A reply ask_model made up to report a failure is shown
                    # like any other, because the user has to be told. It is
                    # not recorded as the model's answer: the sentence in it
                    # is a machine's report about a failure, and carrying it
                    # forward told the next turn that the model had said
                    # "no JSON object found in response".
                    if is_synthetic(obj):
                        turn_state["outcome"] = str(result)
                    # The same warning the batch path gives, in the same place
                    # relative to the answer: before it.
                    for released in release_warnings(session):
                        transcript.emit_kind("warning", released)
                    suggestion = finish_response(live_ui, relay, result,
                                                 transcript, turn_state, pad)
                else:
                    relay.reset()
                    live_ui.intermediate_event(ACTION_LABELS.get(action, "Processing..."))
                if action in MUTATING_ACTIONS:
                    invalidate_prompt()
                note_work(session, action, obj)
                if action == END_CONVERSATION:
                    break
                messages.extend([{"role": "assistant", "content": raw}, {"role": "user", "content": build_result_message(action, result, obj)}])
            else:
                # Thirty-five rounds and no final action. The work that did
                # happen is on screen; what is missing is the answer, and the
                # next question is told that rather than left to infer it.
                turn_state["outcome"] = "it ran out of steps before answering"
        except KeyboardInterrupt:
            relay.abort()
            live_ui.attach_sink(None)
            live_ui.stop()
            console.print("\n[yellow]Task cancelled. Returning to prompt.[/yellow]")
            turn_state["outcome"] = "you stopped it with Ctrl-C"
        except Exception:
            relay.abort()
            live_ui.attach_sink(None)
            live_ui.stop()
            raise
        else:
            relay.abort()
            live_ui.attach_sink(None)
            live_ui.stop()
        # The turn joins the session, and the next question is asked with it
        # already in front of the model. Every turn, however it ended: it used
        # to take an answer to be recorded at all, so a stream failure, a
        # circuit break, an unreadable reply or a turn that ran out of steps
        # dropped the user's question along with it, and the next question
        # arrived with no sign the exchange had happened. That is exactly what
        # "it has no context between prompts" looked like from outside. A turn
        # with no answer says so, in words, rather than being left out.
        answer = "" if turn_state.get("outcome") else turn_state.get("answer", "")
        # The reader is stopped BEFORE anything else reads stdin, and it is
        # waited on rather than abandoned: the next thing this loop does is
        # read keys on the main thread, and two readers on one stdin would
        # take it in turns to swallow the user's characters.
        typeahead.stop()
        prompt_box.typeahead = None
        queued.extend(typeahead.take())
        session.record(task, answer, history, turn_state.get("outcome", ""))
        # Carried to the next prompt as shadow text only. It is drawn in the
        # box and never put in the buffer, so pressing Enter on an untouched
        # prompt still submits nothing.
        placeholder = suggestion or OPENING_SUGGESTION

def finish_response(live_ui, relay, result, transcript=None, state=None, pad=None):
    """Close out a finished turn.

    The order is the point. The suggestion is settled first, then the live
    strip is retired, and only then does the final answer go up, so the strip
    is gone by the time the reply lands rather than sitting between the reply
    and the prompt.

    The suggestion is recorded and never printed. It is the shadow text of the
    next prompt box and nothing else -- announcing it in the reply as well
    would tell the user, in the answer, about a line they are one row away
    from reading under their own cursor.

    Returns the suggestion, which becomes that placeholder. It is a hint to
    look at, never a value: it is not put into the input.
    """
    live_ui.final_event()
    relay.finish()
    suggestion = ""
    if transcript is not None:
        suggestion = resolve_suggestion(state or {}, transcript.history)
        transcript.emit_kind("next_step_suggestion", suggestion)
    live_ui.attach_sink(None)
    live_ui.complete()
    render_response(str(result))
    if pad is not None:
        # The answer's own box: two borders and however many rows the reply
        # wrapped to. Measured from the reply rather than assumed, so a long
        # answer takes the room it actually used.
        pad.take(2 + len(wrap_lines(str(result),
                                    max(10, shutil.get_terminal_size((80, 24)).columns - 5))))
    if transcript is not None:
        # Recorded, not drawn: render_response has already drawn it, and the
        # history is what the turn is answerable from afterwards.
        transcript.history.append(AgentEvent.make("final", str(result)))
    if state is not None:
        # Handed to the session by the loop once the turn has finished, so the
        # next question arrives with this answer already behind it.
        state["answer"] = str(result)
    return suggestion

if __name__ == "__main__":
    main()
