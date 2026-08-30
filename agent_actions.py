"""Action dispatch and conversation-context helpers."""

import re
from collections import Counter

import agent_ui
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

def _run_tool(module_name, operation):
    """Run one of the repository-understanding tools and return its result.

    Imported here rather than at module scope for the reason `_run_git` gives:
    a module that is missing or fails to import must degrade to an action
    result the model can read and work around, not an exception that ends the
    session. These tools are the ones most likely to be absent -- an editable
    install freezes its module list at install time, so a module added to the
    source tree is invisible to `tmtcode` until pyproject.toml is updated, and
    the failure that produces should say so rather than crash.

    A ValueError is the sandbox refusing a path outside the workspace, which
    is a fact the model needs in words rather than a traceback.
    """
    try:
        module = __import__(module_name)
    except Exception as error:
        return f"{module_name} is unavailable: {error}"
    try:
        return operation(module)
    except ValueError as error:
        return f"Refused: {error}"


# --- background agents ------------------------------------------------------
#
# Six verbs the main agent uses to delegate, and one a background agent ends
# on. Everything they touch lives behind `context["manager"]`, which the
# session loop puts there and a background agent's own context deliberately
# does not have. So every branch below starts from the same question -- is
# there a manager? -- and answers a missing one in words rather than raising:
# a worker that asked to spawn a worker must be told it cannot, and an
# AttributeError on None would end the whole run instead.

# How long a wait blocks before it gives up and says so. Ten minutes: long
# enough for a real delegated task, short enough that a session cannot be
# hung forever by a worker stuck on a socket that will never answer. It
# returns "still running" rather than a failure, because a worker that has
# not finished has not failed.
DEFAULT_WAIT_TIMEOUT = 600.0

_NO_MANAGER = (
    "Background agents are not available here, so '%s' did nothing. Nothing was "
    "started, nothing was stopped, and no agent state changed. Carry out the work "
    "yourself with the ordinary file, search and git actions."
)


def _manager(context):
    """The agent register for this call, or None when there is not one.

    `(context or {}).get(...)` rather than `context["manager"]` because a
    background agent's context has no such key AT ALL -- not a None under it
    -- and the main loop's may not either on an install where the manager was
    never wired in.
    """
    return (context or {}).get("manager")


def _agent_modules():
    """(agent_manager, agent_worker), or (None, None) with a sentence.

    Imported at call time for the reason `_run_tool` gives: an editable
    install freezes its module list, so a module present in the source tree
    is invisible to the installed entry point until pyproject.toml catches
    up. That has to come back as an action result the model can work around,
    not an ImportError at the top of this file that stops TMT starting at all.
    """
    try:
        import agent_manager
        import agent_worker
        return agent_manager, agent_worker, ""
    except Exception as error:
        return None, None, "Background agents are unavailable: %s" % error


def _agent_id(obj):
    """The id key as a string. Models write 2 as often as they write "2"."""
    value = obj.get("id")
    if isinstance(value, bool) or value is None:
        return ""
    return str(value).strip()


def _timeout(obj):
    """The wait timeout, defaulting and refusing nonsense rather than raising."""
    value = obj.get("timeout", None)
    if value is None:
        return DEFAULT_WAIT_TIMEOUT
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_WAIT_TIMEOUT
    # A zero or negative timeout is a poll, which is a legitimate thing to
    # ask for; an enormous one is not, and would hang a session on a typo.
    return max(0.0, min(seconds, DEFAULT_WAIT_TIMEOUT))


def _agent_line(record, now=None):
    """One agent's state, as a line. Every figure is one the record holds.

    Token counts are marked `~` unless the provider reported them, which is
    what `tokens_in_exact` and `tokens_out_exact` record. An unmarked estimate
    would be TMT stating a number it guessed.
    """
    marks = ("" if record.tokens_in_exact else "~",
             "" if record.tokens_out_exact else "~")
    line = ("#%s %s -- %s (%s%d tokens in, %s%d out)"
            % (record.id, record.status, record.activity or "no activity yet",
               marks[0], record.tokens_in, marks[1], record.tokens_out))
    if record.paths:
        line += "\n    wrote: %s" % ", ".join(record.paths)
    if record.error:
        line += "\n    error: %s" % record.error
    return line


def _spawn_agent(manager, obj):
    """Create a background worker and start it. Returns a sentence."""
    agent_manager, agent_worker, problem = _agent_modules()
    if problem:
        return problem
    task = obj.get("task")
    if not isinstance(task, str) or not task.strip():
        return ("spawn_agent needs a 'task': the instruction the background "
                "agent is to carry out, written as a whole piece of work it can "
                "finish on its own without asking anyone anything.")
    try:
        record = manager.spawn(task.strip(), model=obj.get("model"),
                               effort=obj.get("effort"))
    except agent_manager.CapacityError as error:
        # A structured refusal, not silence. The sentence names the cap and
        # says what to do about it, because the party reading it is a model
        # and a bare failure is something it will simply retry.
        return str(error)
    if manager.start(record, lambda rec, mgr: agent_worker.run_worker(rec, mgr)) is None:
        return ("Background agent #%s could not be started; it is %s."
                % (record.id, record.status))
    return ("Started background agent #%s on: %s\nIt runs on its own from here. "
            "Carry on with other work, then use wait_for_agents to collect what "
            "it produced, or agent_status to see how it is getting on."
            % (record.id, record.task))


def _agent_status(manager, obj):
    """One agent's state, or every worker's."""
    agent_id = _agent_id(obj)
    if agent_id:
        record = manager.inspect(agent_id)
        if record is None:
            return "There is no background agent with id %r." % agent_id
        return _agent_line(record)
    records = manager.list()
    if not records:
        return ("No background agents have been started in this session. Use "
                "spawn_agent to delegate a piece of work.")
    running = manager.active_count()
    header = ("%d background agent(s), %d still running:"
              % (len(records), running))
    return "\n".join([header] + [_agent_line(record) for record in records])


def _agent_result(manager, obj):
    """What one agent produced, or why there is nothing yet."""
    agent_id = _agent_id(obj)
    record = manager.inspect(agent_id)
    if record is None:
        return "There is no background agent with id %r." % agent_id
    if not record.is_terminal():
        return ("Background agent #%s has not finished; it is %s and its last "
                "activity was %r. Use wait_for_agent to block until it does."
                % (record.id, record.status, record.activity))
    result = manager.result(record.id)
    if not result:
        return ("Background agent #%s is %s and produced no report."
                % (record.id, record.status))
    return "Background agent #%s (%s) reported:\n%s" % (record.id, record.status, result)


def _wait_report(manager, records, finished):
    """What a wait returned: each finished agent verbatim, each one still out.

    The response is quoted rather than summarised. It is the whole of what a
    worker produced and the only thing it produced, and a paraphrase here
    would be TMT restating a report it did not write.
    """
    lines, waiting = [], []
    for record in records:
        if record.id not in finished:
            waiting.append(record)
            continue
        result = manager.result(record.id) or "(no report)"
        lines.append("Background agent #%s (%s) reported:\n%s"
                     % (record.id, record.status, result))
    if waiting:
        lines.append("Still running after the wait: %s. Their work is not lost "
                     "-- wait for them again, or collect them later with "
                     "agent_result."
                     % ", ".join("#%s (%s)" % (record.id, record.activity or record.status)
                                 for record in waiting))
    # Two agents that wrote the same file is the one concurrency fact the main
    # agent cannot work out for itself and does need to know. There is no lock
    # manager and no transaction; this is the whole of that story, and it is
    # enough to send someone to look.
    clashes = manager.conflicts()
    if clashes:
        lines.append("Two or more agents wrote the same file:\n" + "\n".join(
            "    %s -- agents %s" % (path, ", ".join("#" + one for one in ids))
            for path, ids in clashes))
    return "\n\n".join(lines) if lines else "Nothing to report."


def _wait_for_agent(manager, obj):
    agent_id = _agent_id(obj)
    record = manager.inspect(agent_id)
    if record is None:
        return "There is no background agent with id %r." % agent_id
    finished = manager.wait([record.id], timeout=_timeout(obj))
    return _wait_report(manager, [record], finished)


def _wait_for_agents(manager, obj):
    ids = obj.get("ids")
    if isinstance(ids, (list, tuple)) and ids:
        wanted = [str(one).strip() for one in ids if str(one).strip()]
        records = [record for record in (manager.inspect(one) for one in wanted)
                   if record is not None]
        missing = [one for one in wanted if manager.inspect(one) is None]
    else:
        # No ids named means every worker this session started. Finished ones
        # return at once, so this is also how a main agent collects results it
        # never got round to reading.
        records, missing = list(manager.list()), []
    if not records:
        if missing:
            return "None of those ids is a background agent: %s." % ", ".join(missing)
        return ("No background agents have been started in this session, so "
                "there was nothing to wait for.")
    finished = manager.wait([record.id for record in records], timeout=_timeout(obj))
    report = _wait_report(manager, records, finished)
    if missing:
        report += "\n\nNo agent has the id(s): %s." % ", ".join(missing)
    return report


def _kill_agent(manager, obj):
    agent_id = _agent_id(obj)
    record = manager.inspect(agent_id)
    if record is None:
        return "There is no background agent with id %r." % agent_id
    if not manager.kill(record.id):
        return ("Background agent #%s was already %s; nothing was stopped."
                % (record.id, record.status))
    # Said exactly, because the guarantee is exact and a larger claim would be
    # false: a thread cannot be terminated and a stream has no abort, so a
    # request already in flight still finishes arriving.
    return ("Stopped background agent #%s. It will run no further action. A "
            "request already in flight may still complete, and anything it had "
            "already written is still written -- it wrote: %s."
            % (record.id, ", ".join(record.paths) if record.paths else "nothing"))


MAX_STATUS_PATHS = 40

def _git_status(agent_git):
    """Report the changed paths by name.

    Counts alone are useless to the model: it cannot commit "one untracked
    item", and an untracked file appears in no diff, so a name reported here is
    the only way it can ever learn one.
    """
    repo = agent_git.TMTGit.discover()
    state = repo.status()
    lines = [
        f"Repository: {state.get('root') or repo.root}",
        f"Branch: {state.get('branch', 'unknown')}",
    ]
    if state.get("clean"):
        lines.append("Working tree clean; there is nothing to commit.")
        return "\n".join(lines)
    for key, label in (("staged", "Staged"), ("unstaged", "Modified"), ("untracked", "Untracked")):
        paths = state.get(key) or []
        if not paths:
            continue
        shown = paths[:MAX_STATUS_PATHS]
        listed = ", ".join(shown)
        if len(paths) > len(shown):
            listed += f", and {len(paths) - len(shown)} more"
        lines.append(f"{label} ({len(paths)}): {listed}")
    return "\n".join(lines)

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
    # Understanding the repository. Each answers one question and no more, so
    # the model can pick the narrowest tool instead of reading whole files to
    # find one line.
    if action == "tree":
        return _run_tool("agent_tree", lambda m: m.tree(
            obj.get("path"), obj.get("depth"), obj.get("limit")))
    if action == "find_text":
        return _run_tool("agent_file_ops", lambda m: m.find_text(
            obj["query"], path=obj.get("path"), glob=obj.get("glob"),
            context=obj.get("context", 0), limit=obj.get("limit")))
    if action == "find_symbol":
        return _run_tool("agent_symbols", lambda m: m.find_symbol(
            obj["name"], kind=obj.get("kind"), path=obj.get("path"),
            limit=obj.get("limit")))
    if action == "replace_across":
        # Preview unless the model explicitly asks to apply. A bulk edit it
        # did not look at first is how a repository gets wrecked, so the
        # default is the harmless one and saying nothing means changing
        # nothing.
        return _run_tool("agent_file_ops", lambda m: m.replace_across(
            obj["search"], obj["replace"], glob=obj.get("glob"),
            path=obj.get("path"), apply=bool(obj.get("apply", False))))
    if action == "code_map":
        return _run_tool("agent_index", lambda m: m.code_map(
            obj["target"], relation=obj.get("relation", "all")))
    if action == "related_tests":
        return _run_tool("agent_testsel", lambda m: m.related_tests(obj.get("path")))
    if action == "remember":
        return _run_tool("agent_memory", lambda m: m.remember(
            obj["note"], tags=obj.get("tags"), kind=obj.get("kind", "note")))
    if action == "recall":
        return _run_tool("agent_memory", lambda m: m.recall(
            query=obj.get("query"), limit=obj.get("limit"), kind=obj.get("kind")))
    # Delegating to background agents. Every one of these needs the register
    # the session loop holds; without it they say so and change nothing.
    if action in ("spawn_agent", "agent_status", "agent_result",
                  "wait_for_agent", "wait_for_agents", "kill_agent"):
        manager = _manager(context)
        if manager is None:
            return _NO_MANAGER % action
        if action == "spawn_agent": return _spawn_agent(manager, obj)
        if action == "agent_status": return _agent_status(manager, obj)
        if action == "agent_result": return _agent_result(manager, obj)
        # These two BLOCK, here, inside the action, for as long as the timeout
        # allows. The session loop is synchronous and has no event loop to
        # suspend into, so waiting is an action that takes a while rather than
        # a state the loop enters. The screen stays alive because LiveRelay
        # repaints from its own thread, and a KeyboardInterrupt is deliberately
        # not caught anywhere on this path: Ctrl-C during a wait must abort it
        # and return to the prompt, which TMT.py already arranges.
        if action == "wait_for_agent": return _wait_for_agent(manager, obj)
        if action == "wait_for_agents": return _wait_for_agents(manager, obj)
        return _kill_agent(manager, obj)
    # A background agent's ending, and NOT a terminal action here. The main
    # loop ends a turn on `done` and `respond` only, so a main model that
    # somehow emitted this one gets an ordinary result and carries on -- which
    # is the whole point of it being a separate verb rather than a flag.
    if action == "internal_response": return obj.get("response", "")
    # Never terminal. The loop shows the message and carries straight on; the
    # result exists only so the batch report has something to record.
    if action == "announce": return obj.get("message", "")
    if action in ("respond", "done"): return obj.get("message", "done")
    return f"Unknown action: {action}"

READ_ONLY_ACTIONS = ("list_files", "read_file", "search_files", "read_lines", "git_diff")

# What the model is told when it did work without saying what the work was.
#
# Deliberately a reminder attached to the result rather than a validation
# failure. `validate_action` still does not require "progress", and must not:
# an action without one is a valid action that ran and did its job, and
# rejecting it would turn a presentation rule into a failed turn and throw the
# work away over a missing sentence.
#
# But teaching it in the prompt alone was not enough. Models skip it exactly
# where it matters most -- three reads in a row with nothing said between them,
# which from outside is indistinguishable from a stuck loop. This closes the
# gap the only way that costs nothing: the model is told, at the moment it
# happened, about the specific action that went unnarrated, and it carries on.
_MISSING_PROGRESS = (
    "\n[No \"progress\" was sent with that %s, so the user saw a tool run and no "
    "reason for it. Put a one-sentence \"progress\" on the next action.]"
)

# The actions that ARE the thing being said, and so need nothing said about
# them. Kept beside the reminder rather than inferred, so adding an action
# cannot silently make it exempt.
_SPEAKS_FOR_ITSELF = frozenset(("respond", "done", "announce"))


def _said_something(obj):
    """Whether an action object carried a public sentence about itself."""
    if not isinstance(obj, dict):
        return True          # nothing to judge; never nag about a non-object
    progress = obj.get("progress")
    if isinstance(progress, str) and progress.strip():
        return True
    # An `events` entry is a public record too. A model that reported its work
    # that way said what it was doing, in the place the prompt also offers.
    declared = obj.get("events")
    if isinstance(declared, list):
        for entry in declared:
            if isinstance(entry, dict) and str(entry.get("message", "")).strip():
                return True
    return False


def build_result_message(action, result, obj=None):
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
        message = f"Result:\n{result_str}\nNow output a respond action that naturally answers the user's question using this data."
    else:
        message = f"Result: {result_str}"
    # Appended to the end, after whatever the result had to say, so it is the
    # last thing read before the next action is written and never displaces
    # the correction a failed action needs.
    if action not in _SPEAKS_FOR_ITSELF and not _said_something(obj):
        message += _MISSING_PROGRESS % action
    return message

ACTION_LABELS = {action: action.replace("_", " ").title() for action in (
    "write_file", "append_file", "write_files", "patch_file", "delete_file", "read_file",
    "list_files", "search_files", "read_lines", "replace_lines", "copy_file",
    "delete_folder", "rename_file", "create_folder", "run_python", "run_file",
    "open_app", "git_status", "git_diff", "git_identity", "git_commit", "git_push",
    "tree", "find_text", "find_symbol", "replace_across", "code_map",
    "related_tests", "remember", "recall",
    "spawn_agent", "agent_status", "agent_result", "wait_for_agent",
    "wait_for_agents", "kill_agent", "internal_response",
    "announce", "respond", "done",
)}

def batch_summary(batch):
    counts = Counter(sub.get("action", "unknown") for sub in batch)
    return "Running: " + ", ".join(
        f"{ACTION_LABELS.get(action, action)} x{count}" if count > 1 else ACTION_LABELS.get(action, action)
        for action, count in counts.items()
    )

MAX_TURNS = 10
def trim_messages(messages, pinned=2):
    """Drop the middle of a long turn, keeping its head and its tail.

    `pinned` is how many messages at the front must survive whatever else
    goes. It is the system prompt, the conversation carried in from earlier
    questions, and the task itself -- everything the session put there before
    the loop started adding actions and results. Trimming into that would take
    the question out of the request and leave the model answering something
    nobody asked, which is the one failure this cannot be allowed to have.

    The default of two is the shape this had before a session context existed:
    a system prompt and a task. Callers that carry history pass their own
    count, which `Session.begin_turn` returns for exactly this.
    """
    pinned = max(0, min(int(pinned), len(messages)))
    fixed, turns = messages[:pinned], messages[pinned:]
    max_messages = MAX_TURNS * 2
    if len(turns) <= max_messages:
        return messages
    return fixed + [{"role": "user", "content": f"[{len(turns) - max_messages} earlier messages trimmed to stay within context limits.]"}] + turns[-max_messages:]


# --- actions as user-visible events --------------------------------------
#
# The transcript shows what the agent did, so the facts on it have to come from
# what actually ran. Everything below is measured from the action object and
# its result: a line count is counted, a path is the path that was written, a
# failure is a failure the action reported. Nothing here estimates, and an
# action whose outcome cannot be described honestly gets a plain event with no
# facts rather than a plausible-looking one.

_EVENT_KIND_FOR_ACTION = {
    "write_file": "file_create", "write_files": "file_create",
    "create_folder": "file_create",
    "append_file": "file_edit", "patch_file": "file_edit",
    "replace_lines": "file_edit", "rename_file": "file_edit",
    "copy_file": "file_create",
    "delete_file": "file_delete", "delete_folder": "file_delete",
    "read_file": "file_read", "read_lines": "file_read",
    "list_files": "file_read", "search_files": "file_read",
    "run_python": "command", "run_file": "command", "open_app": "command",
    "git_status": "tool", "git_diff": "tool", "git_identity": "tool",
    "git_commit": "milestone", "git_push": "milestone",
    # `background_agent` already exists in agent_prompt.EVENT_TYPES, at
    # prominence level 1 and gradient position 40 beside milestone and tool.
    # A new element takes a place on the existing scale; it does not get a
    # colour or a kind of its own, so these use the one that is already there.
    "spawn_agent": "background_agent", "agent_status": "background_agent",
    "agent_result": "background_agent", "wait_for_agent": "background_agent",
    "wait_for_agents": "background_agent", "kill_agent": "background_agent",
    "internal_response": "background_agent",
}

# Only ever matched against a short sentence an action wrote about itself.
_FAILURE_MARKERS = (
    "not found", "error:", "syntaxerror", "refusing", "invalid",
    "failed", "cancelled", "must be", "no matches for", "aborted",
)

# The actions whose result is a report on the action, and so can be read for
# whether it worked. Every other action's result is data -- file content, the
# output of a program someone ran -- where these words mean nothing about the
# action itself. Scanning those produced a real false alarm: `run_python` on
# the test suite returns "180 passed, 0 failed", and a substring search called
# a fully green run a failure. An event that misreports what happened is worse
# than no event, so the list is stated rather than inferred.
_REPORTED_ACTIONS = frozenset((
    "write_file", "write_files", "append_file", "patch_file", "replace_lines",
    "delete_file", "delete_folder", "create_folder", "copy_file", "rename_file",
    "git_status", "git_diff", "git_identity", "git_commit", "git_push",
    # These three report on themselves: "Started background agent #2 on ...",
    # "Stopped background agent #2", the status listing. Their first line is
    # TMT's own sentence about what happened, so it can be shown and it can be
    # read for whether it worked.
    #
    # agent_result, wait_for_agent, wait_for_agents and internal_response are
    # deliberately NOT here. Their result is a worker's own report, quoted
    # verbatim -- it is data in exactly the way a program's output is data, and
    # scanning it for "failed" would label a worker that truthfully said "two
    # tests failed" as a failed action. That is the same false alarm that once
    # called a green test run a failure, arriving by a new route.
    "spawn_agent", "agent_status", "kill_agent",
))


def _line_count(text):
    """Lines in a block of content, counting an unterminated last line."""
    if not isinstance(text, str) or not text:
        return 0
    return len(text.splitlines())


def _describe(action, obj, result):
    """A one-line public description of what an action did.

    The result string is already written for a person, so where it is short
    and specific it is used as-is rather than paraphrased into something that
    might not match what happened.
    """
    text = str(result).strip()
    first = text.split("\n", 1)[0]
    if action not in _REPORTED_ACTIONS:
        # The result is data -- a file's contents, a program's output -- not a
        # sentence about the action. Its first line describes neither what ran
        # nor how it went: a test run whose output began "PASS test_retries"
        # was labelled with that one line, which says less than the truth and
        # implies it was all of it. Describe the request instead, which is the
        # part that is known.
        target = obj.get("path") or obj.get("query") or obj.get("app") or ""
        label = ACTION_LABELS.get(action, action)
        return "%s %s" % (label, target) if target else label
    if first and len(first) <= 200:
        return first
    return ACTION_LABELS.get(action, action)


def action_event(action, obj, result):
    """One AgentEvent describing an action that has already run, or None.

    Called after the action, never before, so the event can report what
    happened rather than what was intended.
    """
    # These three produce no event of their own. Two are the answer and the
    # third is drawn as progress by the loop before the work it announces --
    # an event here would print the same sentence a second time.
    if action in ("respond", "done", "announce"):
        return None
    kind = _EVENT_KIND_FOR_ACTION.get(action, "tool")
    text = str(result)
    detail = {}

    failed = False
    if action in _REPORTED_ACTIONS:
        lowered = text[:200].lower()
        failed = any(marker in lowered for marker in _FAILURE_MARKERS)
    if action == "git_push" and text == PUSH_BLOCKED:
        failed = True

    if failed:
        # A refusal is not a crash, and a missing file is not a broken agent.
        # Both are things the user needs to see, at a weight that does not
        # read as the run having fallen over.
        return agent_ui.AgentEvent.make("warning", _describe(action, obj, result))

    # Counts, only where they can be counted exactly.
    if action == "append_file":
        # An append adds and removes nothing, so both halves are known.
        detail["added"], detail["removed"] = _line_count(obj.get("content", "")), 0
    elif action == "write_file":
        # Two cases, and only the action's own report can tell them apart.
        # A write over an existing file replaces content that was gone before
        # anyone could count it, so only what was written is ever claimed:
        # "+3 -0" on a write that flattened a hundred-line file would be a
        # confident falsehood. A write to a path that did not exist removed
        # nothing, and that "nothing" is a measurement rather than a guess.
        lines = _line_count(obj.get("content", ""))
        if text.startswith("Created file:"):
            detail["added"], detail["removed"] = lines, 0
        elif lines:
            detail["lines"] = lines
    elif action == "patch_file":
        detail["added"] = _line_count(obj.get("replace", ""))
        detail["removed"] = _line_count(obj.get("search", ""))
    elif action == "replace_lines":
        try:
            detail["removed"] = max(0, int(obj["end"]) - int(obj["start"]) + 1)
        except (KeyError, TypeError, ValueError):
            pass
        detail["added"] = _line_count(obj.get("content", ""))
    elif action == "write_files":
        files = obj.get("files")
        if isinstance(files, list):
            detail["files"] = len(files)
            # Both halves only when every one of them was a creation, which is
            # the batch's own report. One overwrite among them and the removed
            # count is unknowable for the batch as a whole, so it is not given.
            reports = [line for line in text.splitlines() if line.strip()]
            if reports and all(line.startswith("Created file:") for line in reports):
                detail["added"] = sum(_line_count(entry.get("content", ""))
                                      for entry in files if isinstance(entry, dict))
                detail["removed"] = 0

    # Which files the action named. Recorded, not drawn: the transcript's
    # second row reports only counts, and the description above already says
    # the path in the words the action used. It is here for the session
    # record, which carries "what the last turn changed" into the next
    # question -- and a path is the one part of that a follow-up like "now
    # add percentage support" depends on and never states.
    paths = _paths_named(action, obj)
    if paths:
        detail["paths"] = paths

    return agent_ui.AgentEvent.make(kind, _describe(action, obj, result), **detail)


def _paths_named(action, obj):
    """The workspace paths an action was given, in order, each once.

    Taken from the request rather than parsed back out of the result: the
    request is where a path is a fact. An action that names none contributes
    none.
    """
    if not isinstance(obj, dict):
        return ()
    candidates = []
    if action == "write_files":
        for entry in obj.get("files") or ():
            if isinstance(entry, dict):
                candidates.append(entry.get("path"))
    else:
        candidates.extend((obj.get("path"), obj.get("destination"), obj.get("new_path")))
    seen, out = set(), []
    for value in candidates:
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        if value not in seen:
            seen.add(value)
            out.append(value)
    return tuple(out)


def batch_events(batch, results):
    """Events for a batch, in the order the actions ran.

    `results` is the list execute_action produced, which may be shorter than
    the batch when one action ended it early. Pairing them by position keeps
    every event tied to the action that actually produced it.
    """
    events = []
    for sub_obj, result in zip(batch, results):
        if not isinstance(sub_obj, dict):
            continue
        event = action_event(sub_obj.get("action", ""), sub_obj, result)
        if event is not None:
            events.append(event)
    return events
