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
from agent_menu import (
    BottomPad, PromptBox, clear_screen, opening_pad, render_command,
    render_status, render_task, run_startup,
)
from agent_config import (
    MUTATING_ACTIONS, console, default_workspace, set_workspace_root,
    workspace_needs_confirmation, workspace_refusal,
)
from agent_config import REQUIRED_KEYS
from agent_actions import authorizes_push, batch_summary, build_result_message, execute_action, trim_messages
from agent_actions import READ_ONLY_ACTIONS, ACTION_LABELS, MAX_TURNS, action_event
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
from agent_session import Session
from agent_live_renderer import LiveRelay
from agent_setup import ensure_api_key, ensure_git_identity
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

# What the model is told after an announcement, so the turn goes on.
_ANNOUNCED = ("That was shown to the user as progress. The task is not "
              "finished. Emit the action you just announced.")


def is_announcement(obj):
    """Whether this is the model saying what it is about to do.

    Two shapes mean it, and they are not equally safe.

    `announce` is the one to reach for: it has no terminal meaning at all, so
    there is nothing to get wrong. The turn cannot end on it however it is
    written, whatever other keys ride along, and whatever the model forgets.

    `respond` with `final: false` means the same thing and stays supported,
    but it is a flag on the action that DOES end the task, so forgetting the
    flag fails silently in the worst direction -- "I'll read the parser first"
    ending the turn with the parser unread, the work undone and the user
    having to ask again. A separate verb cannot be forgotten into a terminal
    action, which is the whole reason `announce` exists beside it.

    On `respond`, absent means final, so every reply written before that key
    existed still means exactly what it meant. A string is read as well as a
    bool, because a model that writes "false" means false and being pedantic
    about the type here would resurrect the bug this exists to fix.
    """
    if not isinstance(obj, dict):
        return False
    if obj.get("action") == "announce":
        return True
    if obj.get("action") != "respond":
        return False
    value = obj.get("final", True)
    if isinstance(value, str):
        value = value.strip().lower() not in ("false", "no", "0", "")
    return not value


def announce(obj, transcript):
    """Show an announcement and return whether there was anything to show.

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
    if not ensure_api_key():
        return
    # Settings may have moved the model since import, and a request built from
    # a stale value would quietly use the wrong one.
    agent_config.refresh_model()
    # And the effort level, for the same reason: it is stored beside the model
    # and would otherwise be written but never read, so /effort would last a
    # session and quietly revert on the next launch.
    agent_config.refresh_effort()
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


def _dispatch_command(task, session, manager):
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
    parsed = agent_commands.parse(task)
    if parsed is not None:
        name, argument = parsed
        if name == "note" and argument:
            return agent_commands.run_note(argument, session, manager)
        if name == "agents" and not argument:
            return agent_commands._agents(argument, session, manager)
    return agent_commands.dispatch(task, session)


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
    live_panel = {"relay": None}

    def _panel_changed(name, record):
        relay = live_panel.get("relay")
        if relay is not None:
            relay.refresh()

    manager.subscribe(_panel_changed)
    placeholder = OPENING_SUGGESTION
    while True:
        # Shadow text, and nowhere else. The opening line on the first
        # question and the last turn's hint after that: `ask` returns what was
        # typed, and an untouched box returns the empty line it actually
        # holds, never the line drawn in it.
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
        answered = _dispatch_command(task, session, manager)
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
                    pad.take(2 + (render_command(
                        agent_commands.run_note(asked.strip(), session,
                                                manager)) or 0))
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
                   "manager": manager}
        # The request the turn starts from: the system prompt, the earlier
        # questions and answers this session has already had, and then the new
        # task. `pinned` is how much of that the loop's own trimming must
        # leave alone -- everything up to and including the task.
        messages, pinned = session.begin_turn(task, get_system_prompt())
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
                                         if prompt_box.panel() else None))
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
                messages[0]["content"] = get_system_prompt()
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
                    for sub_obj in batch:
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
                        if is_announcement(sub_obj):
                            announce(sub_obj, transcript)
                            results.append(f"{sub_action}: shown to the user")
                            continue
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
                        if sub_action in ("done", "respond"):
                            suggestion = finish_response(live_ui, relay, result,
                                                         transcript, turn_state,
                                                         pad)
                            break
                        results.append(f"{sub_action}: {result}")
                    else:
                        relay.reset()
                        # The batch ran and the model is asked what is next, so
                        # this round was work and costs a step.
                        steps += 1
                        retries = 0
                        messages.extend([{"role": "assistant", "content": raw}, {"role": "user", "content": f"Batch results:\n{chr(10).join(results)}\nOutput your next action."}])
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
                if is_announcement(obj):
                    announce(obj, transcript)
                    relay.reset()
                    live_ui.intermediate_event("Processing...")
                    steps += 1
                    retries = 0
                    messages.extend([{"role": "assistant", "content": raw},
                                     {"role": "user", "content": _ANNOUNCED}])
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
                if action in ("done", "respond"):
                    # A reply ask_model made up to report a failure is shown
                    # like any other, because the user has to be told. It is
                    # not recorded as the model's answer: the sentence in it
                    # is a machine's report about a failure, and carrying it
                    # forward told the next turn that the model had said
                    # "no JSON object found in response".
                    if is_synthetic(obj):
                        turn_state["outcome"] = str(result)
                    suggestion = finish_response(live_ui, relay, result,
                                                 transcript, turn_state, pad)
                else:
                    relay.reset()
                    live_ui.intermediate_event(ACTION_LABELS.get(action, "Processing..."))
                if action in MUTATING_ACTIONS:
                    invalidate_prompt()
                if action in ("done", "respond"):
                    break
                messages.extend([{"role": "assistant", "content": raw}, {"role": "user", "content": build_result_message(action, result)}])
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
