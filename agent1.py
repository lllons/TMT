"""Command-line entry point for the local file AI agent."""

import json
from agent_config import MODEL, OPENROUTER_API_KEY, MUTATING_ACTIONS, Panel, console
from agent_config import REQUIRED_KEYS
from agent_actions import batch_summary, build_result_message, execute_action, trim_messages
from agent_actions import READ_ONLY_ACTIONS, ACTION_LABELS, MAX_TURNS
from agent_model import ask_model
from agent_prompt import get_system_prompt, invalidate_prompt, validate_action
from agent_execution import APP_REGISTRY, RUNNERS, open_app, run_file, run_python
from agent_file_ops import (
    append_file, copy_file, create_folder, delete_file, delete_folder, list_files,
    patch_file, read_file, read_lines, replace_lines, safe_path, search_files,
    write_file, write_files,
)

def main():
    if not OPENROUTER_API_KEY:
        console.print("[bold red]ERROR:[/bold red] OPENROUTER_API_KEY is not set.\n"
                      "Export it before running:  [bold]set OPENROUTER_API_KEY=sk-or-v1-...[/bold]")
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
        try:
            for _ in range(35):
                messages[0]["content"] = get_system_prompt()
                messages = trim_messages(messages)
                raw = ask_model(messages)
                identical_count = identical_count + 1 if raw == last_raw else 0
                last_raw = raw
                if identical_count >= 3:
                    console.print("[bold red]Circuit Breaker Tripped:[/bold red] Identical response 3 times in a row. Stopping.")
                    break
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError as error:
                    console.print(f"[red]Bad JSON:[/red] {error}")
                    break
                if "actions" in obj:
                    batch = obj["actions"]
                    if not isinstance(batch, list) or not batch:
                        messages.extend([{"role": "assistant", "content": raw}, {"role": "user", "content": "INVALID: 'actions' must be a non-empty list. Try again."}])
                        continue
                    console.print(f"[dim]{batch_summary(batch)}[/dim]")
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
                            console.print(Panel(str(result), border_style="green"))
                            break
                        results.append(f"{sub_action}: {result}")
                    else:
                        messages.extend([{"role": "assistant", "content": raw}, {"role": "user", "content": f"Batch results:\n{chr(10).join(results)}\nOutput your next action."}])
                        continue
                    break
                error = validate_action(obj)
                if error:
                    console.print(f"[red]Invalid action:[/red] {error}")
                    messages.extend([{"role": "assistant", "content": raw}, {"role": "user", "content": f"INVALID: {error}. Output a corrected action JSON."}])
                    continue
                action = obj["action"]
                result = execute_action(obj)
                console.print(Panel(str(result), border_style="green"))
                if action in MUTATING_ACTIONS:
                    invalidate_prompt()
                if action in ("done", "respond"):
                    break
                messages.extend([{"role": "assistant", "content": raw}, {"role": "user", "content": build_result_message(action, result)}])
        except KeyboardInterrupt:
            console.print("\n[yellow]Task cancelled. Returning to prompt.[/yellow]")

if __name__ == "__main__":
    main()
