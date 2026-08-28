"""Action dispatch and conversation-context helpers."""

import re
from collections import Counter
from agent_config import MUTATING_ACTIONS
from agent_execution import open_app, run_file
from agent_file_ops import (
    append_file, copy_file, create_folder, delete_file, delete_folder, list_files,
    patch_file, read_file, read_lines, replace_lines, safe_path, search_files,
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

def _git_status(agent_git):
    repo = agent_git.TMTGit.discover()
    state = repo.status()
    if state.get("clean"):
        changes = "Working tree clean"
    else:
        changes = ", ".join(
            f"{len(state.get(key) or [])} {label}"
            for key, label in (("staged", "staged"), ("unstaged", "unstaged"), ("untracked", "untracked"))
        )
    return f"Repository: {state.get('root') or repo.root}\nBranch: {state.get('branch', 'unknown')}\n{changes}"

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

def execute_action(obj, context=None):
    """Run one action object and return its result.

    `context` carries per-task authority, currently {'push_authorized': bool}.
    Callers that pass nothing get the safe default: no push authority.
    """
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
    if action in ("respond", "done"): return obj.get("message", "done")
    return f"Unknown action: {action}"

READ_ONLY_ACTIONS = ("list_files", "read_file", "search_files", "read_lines", "git_diff")

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
    "open_app", "git_status", "git_diff", "git_identity", "git_commit", "git_push",
    "respond", "done",
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
