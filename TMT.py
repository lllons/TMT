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
    BottomPad, PromptBox, clear_screen, opening_pad, render_status,
    render_task, run_startup,
)
from agent_config import (
    MUTATING_ACTIONS, console, default_workspace, set_workspace_root,
    workspace_needs_confirmation, workspace_refusal,
)
from agent_config import REQUIRED_KEYS
from agent_actions import authorizes_push, batch_summary, build_result_message, execute_action, trim_messages
from agent_actions import READ_ONLY_ACTIONS, ACTION_LABELS, MAX_TURNS, action_event
from agent_model import ask_model, is_synthetic
from agent_ui import (
    OPENING_SUGGESTION, RUNNING_HINT, AgentEvent, LiveUI, ResponseHistory,
    Transcript, fallback_suggestion, render_response, validate_suggestion,
    wrap_lines,
)
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
    # One box for the session. It draws its own frame each time it is asked,
    # so nothing needs redrawing between turns, and the console keeps being
    # the line reader on any run that cannot take raw keys -- a pipe, a
    # redirect, the test suite -- so a scripted run behaves as it always did.
    prompt_box = PromptBox(line_reader=_console_line, session=session, pad=pad)
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
        # The question, into scrollback. The box that collected it is a live
        # region and has already been taken down, so this is the only record
        # of what was asked -- and it belongs above the work the answer came
        # out of, which is why it is written here rather than at the end.
        render_task(task, moment=prompt_box.asked_at)
        pad.take(2)              # the caption and the marker row it just wrote
        # Authority to push comes from this task's wording alone, decided once
        # here so nothing the model later writes can widen it.
        context = {"push_authorized": authorizes_push(task)}
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
        relay = LiveRelay(footer=lambda: prompt_box.running_lines(RUNNING_HINT),
                          pad=pad)
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
        turn_state = {"next_step": "", "progress_seen": set()}
        suggestion = ""
        live_ui.attach_sink(relay.set_status)
        live_ui.start()
        try:
            for _ in range(35):
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
                    relay.release()
                    console.print(f"[red]Bad JSON:[/red] {error}")
                    turn_state["outcome"] = "the reply could not be read as JSON"
                    break
                declared_events(obj, transcript, turn_state)
                if "actions" in obj:
                    batch = obj["actions"]
                    if not isinstance(batch, list) or not batch:
                        relay.reset()
                        messages.extend([{"role": "assistant", "content": raw}, {"role": "user", "content": "INVALID: 'actions' must be a non-empty list. Try again."}])
                        continue
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
                        result = execute_action(sub_obj, context)
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
                        messages.extend([{"role": "assistant", "content": raw}, {"role": "user", "content": f"Batch results:\n{chr(10).join(results)}\nOutput your next action."}])
                        continue
                    if invalid:
                        relay.release()
                        console.print(f"[red]Invalid action in batch:[/red] {invalid}")
                        ran = chr(10).join(results) if results else "Nothing ran."
                        messages.extend([
                            {"role": "assistant", "content": raw},
                            {"role": "user",
                             "content": f"INVALID: {invalid}\nRan before it:\n{ran}\n"
                                        "Output a corrected action JSON."}])
                        continue
                    break
                error = validate_action(obj)
                if error:
                    relay.release()
                    console.print(f"[red]Invalid action:[/red] {error}")
                    messages.extend([{"role": "assistant", "content": raw}, {"role": "user", "content": f"INVALID: {error}. Output a corrected action JSON."}])
                    continue
                action = obj["action"]
                result = execute_action(obj, context)
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
