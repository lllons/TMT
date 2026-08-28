"""Cached system prompt and action validation."""

from agent_config import REQUIRED_KEYS, ROOT_DIR
from agent_execution import APP_REGISTRY

_cached_prompt = None
_prompt_dirty = True

def invalidate_prompt():
    global _prompt_dirty
    _prompt_dirty = True

def get_system_prompt():
    global _cached_prompt, _prompt_dirty
    if not _prompt_dirty and _cached_prompt is not None:
        return _cached_prompt
    snapshot = ""
    for path in sorted(ROOT_DIR.rglob("*")):
        if path.is_file() and path.stat().st_size < 8000:
            snapshot += f"\n--- {path.relative_to(ROOT_DIR)} ---\n{path.read_text(encoding='utf-8', errors='replace')}\n"
    snapshot = snapshot or "(empty workspace)"
    apps = ", ".join(f"{key} ({value['description']})" for key, value in APP_REGISTRY.items()) or "none"
    _cached_prompt = f"""You are a helpful AI assistant and local file manager. You can chat naturally AND manage files.
Only output valid JSON. No markdown, no explanation, nothing except a single JSON object.
Allowed actions: write_file, append_file, write_files, patch_file, delete_file, read_file, list_files, search_files, read_lines, replace_lines, copy_file, rename_file, create_folder, delete_folder, run_file, open_app, respond.
Permitted apps for open_app: {apps}
Never run shell commands.
Always end every turn with a "respond" action containing a natural reply for the user.
Only perform file actions the user explicitly asked for. Never create or edit files unprompted.
Files under 8 KB appear below; use read_lines for larger files. Prefer patch_file for unique snippets.
Batch independent steps in an "actions" array and end every batch with respond.
Workspace root: {ROOT_DIR}
Current workspace files and contents:
{snapshot}
""".strip()
    _prompt_dirty = False
    return _cached_prompt

def validate_action(obj):
    action = obj.get("action")
    if not action:
        return "Missing 'action' key in JSON"
    if action not in REQUIRED_KEYS:
        return f"Unknown action: '{action}'. Allowed: {list(REQUIRED_KEYS)}"
    missing = [key for key in REQUIRED_KEYS[action] if key not in obj]
    return f"Action '{action}' is missing required keys: {missing}" if missing else None
