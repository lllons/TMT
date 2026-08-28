"""Action dispatch and conversation-context helpers."""

from collections import Counter
from agent_config import MUTATING_ACTIONS
from agent_execution import open_app, run_file
from agent_file_ops import (
    append_file, copy_file, create_folder, delete_file, delete_folder, list_files,
    patch_file, read_file, read_lines, replace_lines, safe_path, search_files,
    write_file, write_files,
)

def execute_action(obj):
    action = obj["action"]
    if action == "write_file": return write_file(obj["path"], obj.get("content", ""))
    if action == "append_file": return append_file(obj["path"], obj.get("content", ""))
    if action == "write_files": return write_files(obj["files"])
    if action == "patch_file": return patch_file(obj["path"], obj.get("search", ""), obj.get("replace", ""))
    if action == "delete_file": return delete_file(obj["path"])
    if action == "read_file": return read_file(obj["path"])
    if action == "list_files": return list_files()
    if action == "search_files": return search_files(obj["query"], regex=obj.get("regex", False), path=obj.get("path"))
    if action == "read_lines": return read_lines(obj["path"], obj.get("start", 1), obj.get("end"))
    if action == "replace_lines": return replace_lines(obj["path"], obj["start"], obj["end"], obj.get("content", ""))
    if action == "copy_file": return copy_file(obj["path"], obj.get("to") or obj.get("new_path") or obj.get("dest", ""))
    if action == "delete_folder": return delete_folder(obj["path"], recursive=obj.get("recursive", False))
    if action == "rename_file":
        old, new_name = safe_path(obj["path"]), obj.get("new_name") or obj.get("new_path", "")
        new = safe_path(new_name)
        if not old.exists(): return f"File not found: {obj['path']}"
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
        return f"Renamed {obj['path']} to {new_name}"
    if action == "create_folder": return create_folder(obj["path"])
    if action in ("run_python", "run_file"): return run_file(obj["path"])
    if action == "open_app": return open_app(obj["app"], file_path=obj.get("path"), url=obj.get("url"))
    if action in ("respond", "done"): return obj.get("message", "done")
    return f"Unknown action: {action}"

READ_ONLY_ACTIONS = ("list_files", "read_file", "search_files", "read_lines")

def build_result_message(action, result):
    result_str = str(result)
    if "SyntaxError" in result_str:
        return f"FAILED with SyntaxError: {result_str}\nOutput a corrected action that fixes the exact syntax error above."
    if action == "patch_file" and "Search text not found" in result_str:
        return f"FAILED: {result_str}\nThe search string didn't match exactly. Use search_files or read_lines, then retry."
    if action == "replace_lines" and "Invalid range" in result_str:
        return f"FAILED: {result_str}\nUse read_lines on this file first, then retry replace_lines."
    if "not found" in result_str.lower() and action in ("read_file", "run_python", "patch_file", "read_lines", "copy_file"):
        return f"FAILED: {result_str}\nCheck the file path with list_files and retry."
    if action in READ_ONLY_ACTIONS:
        return f"Result:\n{result_str}\nNow output a respond action that naturally answers the user's question using this data."
    return f"Result: {result_str}"

ACTION_LABELS = {action: action.replace("_", " ").title() for action in (
    "write_file", "append_file", "write_files", "patch_file", "delete_file", "read_file",
    "list_files", "search_files", "read_lines", "replace_lines", "copy_file",
    "delete_folder", "rename_file", "create_folder", "run_python", "run_file",
    "open_app", "respond", "done",
)}

def batch_summary(batch):
    counts = Counter(sub.get("action", "unknown") for sub in batch)
    return "Running: " + ", ".join(
        f"{ACTION_LABELS.get(action, action)} x{count}" if count > 1 else ACTION_LABELS.get(action, action)
        for action, count in counts.items()
    )

MAX_TURNS = 10
def trim_messages(messages):
    fixed, turns = messages[:2], messages[2:]
    max_messages = MAX_TURNS * 2
    if len(turns) <= max_messages:
        return messages
    return fixed + [{"role": "user", "content": f"[{len(turns) - max_messages} earlier messages trimmed to stay within context limits.]"}] + turns[-max_messages:]
