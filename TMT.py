"""Command-line entry point for the local file AI agent."""

import argparse
import json
import sys
from agent_config import (
    MODEL, MUTATING_ACTIONS, Panel, console, default_workspace,
    set_workspace_root, workspace_needs_confirmation, workspace_refusal,
)
from agent_config import REQUIRED_KEYS
from agent_actions import authorizes_push, batch_summary, build_result_message, execute_action, trim_messages
from agent_actions import READ_ONLY_ACTIONS, ACTION_LABELS, MAX_TURNS
from agent_model import ask_model
from agent_ui import LiveUI, render_response
from agent_live_renderer import LiveRelay
from agent_setup import ensure_api_key
from agent_prompt import get_system_prompt, invalidate_prompt, validate_action
from agent_execution import APP_REGISTRY, RUNNERS, open_app, run_file, run_python
from agent_file_ops import (
    append_file, copy_file, create_folder, delete_file, delete_folder, list_files,
    patch_file, read_file, read_lines, replace_lines, safe_path, search_files,
    write_file, write_files,
)

def stream_handler(live_ui, relay, state):
    """Turn model stream events into UI updates.

    Only real generated content moves the UI on: the first content event
    replaces THINKING with the progress bar, user-facing text goes to the live
    relay, output and usage events feed the activity readout, and structured
    action events drive progress. Text never advances progress, so a long reply
    cannot produce one step per token.
    """
    def handle(event):
        kind, value = event
        if kind == "first_content":
            live_ui.meaningful_output()
        elif kind == "text":
            relay.feed(value)
        elif kind == "output":
            live_ui.add_output(value)
        elif kind == "usage":
            live_ui.settle_tokens(value)
        elif kind == "action":
            live_ui.intermediate_event(ACTION_LABELS.get(value, "Processing..."))
        elif kind == "error":
            state["error"] = value
    return handle

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
    console.print(Panel.fit(f"[bold green]Local File AI[/bold green] (OpenRouter / {MODEL})"))
    # Stated plainly and before the first prompt: this is the directory about
    # to be modified, and a run from the wrong place should be obvious here
    # rather than three edits later.
    console.print(f"[bold]Workspace:[/bold] {root}")
    while True:
        try:
            task = console.input("\n[bold cyan]Task> [/bold cyan]").strip()
        except KeyboardInterrupt:
            console.print("\n[yellow]Use 'quit' or 'exit' to close.[/yellow]")
            continue
        except EOFError:
            break
        if task.lower() in {"quit", "exit"}:
            break
        if not task:
            continue
        # Authority to push comes from this task's wording alone, decided once
        # here so nothing the model later writes can widen it.
        context = {"push_authorized": authorizes_push(task)}
        messages = [{"role": "system", "content": get_system_prompt()}, {"role": "user", "content": task}]
        last_raw, identical_count = "", 0
        live_ui = LiveUI()
        relay = LiveRelay()
        live_ui.attach_sink(relay.set_status)
        live_ui.start()
        try:
            for _ in range(35):
                messages[0]["content"] = get_system_prompt()
                messages = trim_messages(messages)
                state = {"error": None}
                raw = ask_model(messages, on_event=stream_handler(live_ui, relay, state))
                live_ui.meaningful_output()
                if state["error"]:
                    relay.abort()
                    live_ui.attach_sink(None)
                    live_ui.stop()
                    console.print(f"[red]Stream failed:[/red] {state['error']}")
                    break
                identical_count = identical_count + 1 if raw == last_raw else 0
                last_raw = raw
                if identical_count >= 3:
                    relay.release()
                    console.print("[bold red]Circuit Breaker Tripped:[/bold red] Identical response 3 times in a row. Stopping.")
                    break
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError as error:
                    relay.release()
                    console.print(f"[red]Bad JSON:[/red] {error}")
                    break
                if "actions" in obj:
                    batch = obj["actions"]
                    if not isinstance(batch, list) or not batch:
                        relay.reset()
                        messages.extend([{"role": "assistant", "content": raw}, {"role": "user", "content": "INVALID: 'actions' must be a non-empty list. Try again."}])
                        continue
                    live_ui.intermediate_event("Processing...")
                    results = []
                    for sub_obj in batch:
                        error = validate_action(sub_obj)
                        if error:
                            results.append(f"INVALID: {error}")
                            break
                        sub_action = sub_obj["action"]
                        result = execute_action(sub_obj, context)
                        if sub_action in MUTATING_ACTIONS:
                            invalidate_prompt()
                        if sub_action in ("done", "respond"):
                            finish_response(live_ui, relay, result)
                            break
                        results.append(f"{sub_action}: {result}")
                    else:
                        relay.reset()
                        messages.extend([{"role": "assistant", "content": raw}, {"role": "user", "content": f"Batch results:\n{chr(10).join(results)}\nOutput your next action."}])
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
                if action in ("done", "respond"):
                    finish_response(live_ui, relay, result)
                else:
                    relay.reset()
                    live_ui.intermediate_event(ACTION_LABELS.get(action, "Processing..."))
                if action in MUTATING_ACTIONS:
                    invalidate_prompt()
                if action in ("done", "respond"):
                    break
                messages.extend([{"role": "assistant", "content": raw}, {"role": "user", "content": build_result_message(action, result)}])
        except KeyboardInterrupt:
            relay.abort()
            live_ui.attach_sink(None)
            live_ui.stop()
            console.print("\n[yellow]Task cancelled. Returning to prompt.[/yellow]")
        except Exception:
            relay.abort()
            live_ui.attach_sink(None)
            live_ui.stop()
            raise
        else:
            relay.abort()
            live_ui.attach_sink(None)
            live_ui.stop()

def finish_response(live_ui, relay, result):
    """Close out a finished turn: 95% FINALIZING, let the live relay resolve
    every remaining symbol, then 100% and the final response — shown once."""
    live_ui.final_event()
    relay.finish()
    live_ui.attach_sink(None)
    live_ui.complete()
    render_response(str(result))

if __name__ == "__main__":
    main()
