"""Command-line entry point for the local file AI agent."""

import json
from agent_config import MODEL, MUTATING_ACTIONS, Panel, console
from agent_config import REQUIRED_KEYS
from agent_actions import batch_summary, build_result_message, execute_action, trim_messages
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
    relay, and structured action events drive progress. Text never advances
    progress, so a long reply cannot produce one step per token.
    """
    def handle(event):
        kind, value = event
        if kind == "first_content":
            live_ui.meaningful_output()
        elif kind == "text":
            relay.feed(value)
        elif kind == "action":
            live_ui.intermediate_event(ACTION_LABELS.get(value, "Processing..."))
        elif kind == "error":
            state["error"] = value
    return handle

def main():
    if not ensure_api_key():
        return
    console.print(Panel.fit(f"[bold green]Local File AI[/bold green] (OpenRouter / {MODEL})"))
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
                        result = execute_action(sub_obj)
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
                result = execute_action(obj)
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
