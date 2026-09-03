"""Several tool calls in one action, and one call repeated over many files.

`multi_tool` takes a list of ordinary action objects and runs them in order,
handing back every result under a numbered header. An entry may carry
`for_each`, a path pattern, in which case it is a TEMPLATE: it is run once per
matching file, with the file's path put where the template says. Both shapes
answer the same request -- "read these five files", "read the first six lines
of every Python file" -- in one round trip instead of five or a hundred.

    {"action":"multi_tool","calls":[
        {"action":"read_file","path":"src/a.py"},
        {"action":"read_file","path":"src/b.py"},
        {"action":"read_lines","for_each":"**/*.py","start":1,"end":6}]}

What this module owns is the shape: which entries are usable, how a template
becomes calls, how many calls one action may run, how much text the result
may carry, and what the result looks like. It runs nothing itself. Every call
goes through the `dispatch` it is handed -- `agent_actions.execute_action` for
the main agent, and the worker loop's own guarded dispatcher for a background
agent -- so a call inside a multi_tool meets exactly the guards the same call
would meet on its own: the sandbox, the command policy, the push authority,
the capability gate, the read-only contract. Nothing is bypassed by being
listed, because the list is not a dispatch path.

Four verbs are refused inside one, and each for the same reason: the loop
intercepts them BEFORE dispatch and gives them their meaning there.
`send_message` and `end_conversation` are shown and (for the second) end the
turn by the loop's hand; `internal_response` is a background agent's ending;
`review_agenda` is applied by the worker loop to the reviewer's own record.
Reaching any of them through the dispatcher does something other than what
the model meant -- a `multi_tool` that "ended the conversation" would run its
remaining calls and then carry on -- so they are refused with the place they
belong named. A nested `multi_tool` is refused too: flattening it is one
list, and nesting would be a second bound to reason about.

Every call runs whatever the calls before it returned. A call that must not
run unless an earlier one succeeded belongs in a later turn, and the prompt
says so. What this module can honestly report is which calls RAISED -- those
are marked FAILED and the count is in the header. Whether a call that ran
did what was wanted is in its own result text, exactly as it would be on its
own, because reading a program's output for the word "failed" is the rule
that once called a green test run a failure.
"""

from pathlib import Path

import agent_file_ops

# The verb, spelled here so the worker loop and the dispatcher agree on it
# without either importing the other.
MULTI_ACTION = "multi_tool"
CALLS = "calls"
FOR_EACH = "for_each"
LIMIT = "limit"

# Where the calls that ran, and what each returned, are recorded on the action
# object once `run` has started. A private key, exactly as
# `agent_actions.LEGACY_BLOCKED_KEY` is: the loops put the model's own `raw`
# reply in the conversation, never this object, so nothing here reaches the
# model, and the transcript, the review state and the verification state can
# ask which inner calls actually happened rather than trusting the list that
# was asked for.
RAN_KEY = "_multi_ran"

# How many calls one action may run. The default is `agent_glob`'s row cap,
# for the same reason: past it the result stops being something a model reads
# and becomes something it has to search. A caller that means more says so
# with `limit`, up to the hard ceiling, and a pattern that would run more than
# that is refused with the count rather than silently cut -- a fan-out over
# "every Python file" that quietly stopped at the two-hundredth would be a
# result claiming completeness it does not have.
MAX_CALLS = 200
HARD_MAX_CALLS = 1000

# The most text one result carries, over every call together. `bash` caps one
# command's output at 40000 characters; this is one action carrying up to two
# hundred results, so it is a little wider and it is shared. When the results
# do not fit, each call's text is cut to a fair share -- see `_allocate` --
# and the cut is marked in the block it was made in, so a model that needs
# the rest knows to ask for that one call on its own.
MAX_CHARS = 60000

# What a template may write a matched file's path into. `{path}` is the
# workspace-relative path; `{name}` is its last segment; `{stem}` is that with
# the extension taken off, which is what "a test file for every module" needs.
# When a template uses none of them the path goes in `path`, which is where
# every reading and editing action takes its subject.
PLACEHOLDERS = ("{path}", "{name}", "{stem}")

# The verbs a multi_tool may not carry, each with the reason and the place it
# belongs. The reason is the half a model acts on: told only "not allowed" it
# would reasonably try a different spelling.
_LOOP_VERBS = {
    "send_message": "it talks to the user and the loop shows it; put it in the "
                    "\"actions\" batch beside the multi_tool instead",
    "end_conversation": "it ends the task and only the loop can end one; put it "
                        "in the \"actions\" batch after the multi_tool instead",
    "internal_response": "it is a background agent's ending and only the loop "
                         "can end one; send it as its own action instead",
    "review_agenda": "the loop applies it to the reviewer's own checklist; put "
                     "it in the \"actions\" batch beside the multi_tool instead",
}

_NESTED = ("it is another multi_tool; put its calls in this one's list instead "
           "-- one flat list is the whole shape")

# How one call is headed in the result. The index is 1-based because the
# model counts entries the way a person does, and "[3/5]" is a place it can
# quote back.
_HEAD = "[%d/%d] %s"

# What a call that raised is recorded as. The same word the loop uses for an
# action that could not run, so a model reads it the same way.
_RAISED = "FAILED: the call raised %s: %s"

# What replaces the part of a result that did not fit.
_CLIPPED = ("\n[... %d more characters not shown. Run this call on its own to "
            "read all of it.]")

_NEEDS_CALLS = ("multi_tool needs \"calls\": a non-empty list of action "
                "objects to run. Nothing ran.")
_ENTRY_NOT_OBJECT = "it is not a JSON object"
_ENTRY_REFUSED = "multi_tool call %d of %d cannot run: %s. Nothing ran."
_BAD_FOR_EACH = ("\"for_each\" must be a path pattern written as text, such "
                 "as \"**/*.py\"")
_BAD_LIMIT = "\"limit\" must be a whole number of calls"
_TOO_MANY = ("multi_tool would run %d calls and the ceiling is %d. Narrow the "
             "for_each pattern, split the work across several multi_tool "
             "actions, or pass \"limit\" (up to %d). Nothing ran.")
_NOTHING_MATCHED = ("multi_tool has nothing to run: %s. Nothing ran.")


# --- what the entries are ---------------------------------------------------

def _validate(entry):
    """The entry's own validation, or "" when it is a usable action.

    `agent_prompt.validate_action` is the schema every other dispatch path
    checks against, imported at call time for the reason every import in the
    worker loop is: an install whose frozen module list predates a module must
    degrade to the one check that needs no schema rather than to an
    ImportError.
    """
    try:
        import agent_prompt
    except Exception:
        action = entry.get("action")
        return "" if isinstance(action, str) and action else "Missing 'action' key in JSON"
    return agent_prompt.validate_action(entry) or ""


def _adopt(entry):
    """The entry rewritten to the verb in force now, exactly as the loops do."""
    try:
        import agent_actions
        return agent_actions.adopt_verb(entry)
    except Exception:
        return entry


def entries(obj):
    """(templates, refusal) -- the validated entries of `obj`, or why not.

    Every entry is adopted and validated HERE, before anything is expanded or
    run, and one unusable entry refuses the whole action with nothing run. A
    multi_tool that ran four calls and then reported the fifth was malformed
    would leave the model correcting an object whose first four entries had
    already happened -- and would run them again when it resent the corrected
    one. Refusing up front is the `agent_reviewbot` lesson: a refused update
    leaves no trace of having happened.
    """
    calls = obj.get(CALLS) if isinstance(obj, dict) else None
    if not isinstance(calls, list) or not calls:
        return [], _NEEDS_CALLS
    total = len(calls)
    templates = []
    for index, entry in enumerate(calls, 1):
        if not isinstance(entry, dict):
            return [], _ENTRY_REFUSED % (index, total, _ENTRY_NOT_OBJECT)
        entry = _adopt(entry)
        pattern = entry.get(FOR_EACH)
        if pattern is not None and (not isinstance(pattern, str)
                                    or not pattern.strip()):
            return [], _ENTRY_REFUSED % (index, total, _BAD_FOR_EACH)
        # A template is validated as the call it will become. `fill` puts
        # the matched file in `path` when no placeholder claims it, so a
        # `read_lines` template with no `path` of its own is a complete call
        # once expanded -- and refusing it here for the key the expansion
        # supplies would refuse the commonest template there is. Every
        # expanded call is validated again in `expand`, so nothing reaches
        # the dispatcher on the strength of this probe alone.
        probe = dict(entry)
        if pattern is not None:
            probe.setdefault("path", "<for_each>")
        invalid = _validate(probe)
        if invalid:
            return [], _ENTRY_REFUSED % (index, total, invalid)
        action = entry.get("action")
        if action == MULTI_ACTION:
            return [], _ENTRY_REFUSED % (index, total, _NESTED)
        if action in _LOOP_VERBS:
            return [], _ENTRY_REFUSED % (index, total,
                                         "%s: %s" % (action, _LOOP_VERBS[action]))
        templates.append(entry)
    return templates, ""


# --- one template into many calls ------------------------------------------

def _cap(obj):
    """The call ceiling this action asked for, or None for an unusable one.

    A bool is refused by name: `int(True)` is 1, and `"limit": true` would
    refuse every fan-out wider than one call for a ceiling nobody chose.
    """
    limit = obj.get(LIMIT) if isinstance(obj, dict) else None
    if isinstance(limit, bool):
        return None
    if limit in (None, ""):
        return MAX_CALLS
    try:
        return max(1, min(HARD_MAX_CALLS, int(limit)))
    except (TypeError, ValueError):
        return None


def files_matching(pattern):
    """(paths, hit_the_scan_ceiling): the workspace FILES a pattern names.

    The same walk, the same matcher and the same containment test `glob`
    uses, through `agent_file_ops`, because a second traversal is a second
    set of rules about what counts as machinery and what counts as inside the
    workspace. Files only: a template is a call that takes a file, and a
    directory handed to `read_lines` is a refusal per directory. Sorted, so
    "[7/40]" names the same file on every run.
    """
    keep = agent_file_ops.glob_filter(pattern)
    here = agent_file_ops.workspace()
    found, scanned = [], 0
    for relative, absolute in agent_file_ops.iter_workspace_entries(here):
        scanned += 1
        if not keep(relative):
            continue
        try:
            if Path(absolute).is_dir():
                continue
        except OSError:
            continue
        if not agent_file_ops.within_workspace(absolute):
            continue
        found.append(agent_file_ops.posix(relative))
    found.sort()
    return found, scanned >= agent_file_ops.WORKSPACE_MAX_SCAN


def _substitute(value, fills, used):
    """`value` with every placeholder replaced, recursively through lists and
    objects, so a `git_diff` whose `paths` is `["{path}"]` and a
    `write_files` entry both work. `used` records that a placeholder was
    seen, which decides whether `path` is filled in as well."""
    if isinstance(value, str):
        if any(mark in value for mark in fills):
            used[0] = True
            for mark, text in fills.items():
                value = value.replace(mark, text)
        return value
    if isinstance(value, list):
        return [_substitute(item, fills, used) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, fills, used) for key, item in value.items()}
    return value


def fill(template, path):
    """One call built from a template for one matched file.

    The matched path is written wherever the template put a placeholder, and
    into `path` when it put none -- which is the common case, and is what
    `{"action":"read_lines","for_each":"*.py","start":1,"end":6}` means. The
    `for_each` key itself does not survive: the call is an ordinary action
    once it has its file, and a dispatcher handed an unknown key would still
    run it, but a model reading the record back should see what ran.
    """
    name = path.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name[1:] else name
    fills = {"{path}": path, "{name}": name, "{stem}": stem}
    used = [False]
    call = {}
    for key, value in template.items():
        if key == FOR_EACH:
            continue
        call[key] = _substitute(value, fills, used)
    if not used[0]:
        call["path"] = path
    return call


def expand(obj):
    """(calls, notes, refusal): every call this action would run, in order.

    Templates become one call per matching file and plain entries stay as
    they are, in the order they were written -- so a call listed after a
    fan-out runs after all of it. `notes` is what the result says about each
    expansion, including the one that matched nothing: an empty expansion
    silently contributing no calls would leave a model believing the files it
    named were read.

    The ceiling is checked against the whole list, after expansion, because
    that is the number of calls that would actually run.
    """
    templates, refusal = entries(obj)
    if refusal:
        return [], [], refusal
    cap = _cap(obj)
    if cap is None:
        return [], [], _ENTRY_REFUSED % (1, len(templates), _BAD_LIMIT)
    calls, notes, empty = [], [], []
    for template in templates:
        pattern = template.get(FOR_EACH)
        if pattern is None:
            calls.append(template)
            continue
        pattern = pattern.strip()
        verb = str(template.get("action", "?"))
        matched, capped = files_matching(pattern)
        if not matched:
            empty.append('for_each "%s" (%s) matched no files' % (pattern, verb))
            notes.append('for_each "%s" (%s) matched no files.' % (pattern, verb))
        else:
            notes.append('for_each "%s" (%s) matched %s.' % (
                pattern, verb, agent_file_ops.plural(len(matched), "file")))
        if capped:
            notes.append("(The walk stopped at %d entries, so files beyond that "
                         "were never examined.)" % agent_file_ops.WORKSPACE_MAX_SCAN)
        if len(calls) + len(matched) > cap:
            return [], [], _TOO_MANY % (len(calls) + len(matched), cap, HARD_MAX_CALLS)
        for index, path in enumerate(matched):
            call = fill(template, path)
            invalid = _validate(call)
            if invalid:
                # Unreachable when the probe in `entries` passed, and kept
                # because the probe is a stand-in: nothing reaches the
                # dispatcher that the schema has not seen as it will run.
                return [], [], _ENTRY_REFUSED % (index + 1, len(matched), invalid)
            calls.append(call)
    if len(calls) > cap:
        return [], [], _TOO_MANY % (len(calls), cap, HARD_MAX_CALLS)
    if not calls:
        return [], [], _NOTHING_MATCHED % "; ".join(empty)
    return calls, notes, ""


# --- running them ----------------------------------------------------------

def _cancellation():
    """The exception a background agent is stopped with, or None.

    Imported at call time so this module loads on an install without the
    manager; on such an install nothing can be cancelled and nothing needs
    re-raising.
    """
    try:
        from agent_manager import WorkerCancelled
        return WorkerCancelled
    except Exception:
        return None


def subject(call):
    """The one word after the verb in a call's header: its file, its query,
    its command. Taken from the request, where it is a fact."""
    for key in ("path", "query", "pattern", "url", "app", "command", "name",
                "target", "id", "search", "task", "note"):
        value = call.get(key)
        if isinstance(value, str) and value.strip():
            value = " ".join(value.split())
            return value if len(value) <= 120 else value[:117] + "..."
    return ""


def head(index, total, call):
    """`[3/5] read_lines src/app.py` -- the header one call's block carries."""
    label = str(call.get("action", "?"))
    target = subject(call)
    return _HEAD % (index, total, "%s %s" % (label, target) if target else label)


def _allocate(sizes, budget):
    """A fair share of `budget` for each of `sizes`, smallest first.

    Every result that fits keeps its whole text; what is left over is shared
    evenly between the ones that do not. Cutting every block to the same
    length would waste the share a short result did not need on nothing, and
    cutting only the largest would let one whole file starve the rest.
    """
    shares = [0] * len(sizes)
    remaining = max(0, int(budget))
    left = len(sizes)
    for index in sorted(range(len(sizes)), key=lambda i: sizes[i]):
        share = min(sizes[index], remaining // left) if left else 0
        shares[index] = max(0, share)
        remaining -= shares[index]
        left -= 1
    return shares


def _clip(text, share):
    """`text` cut to `share` characters, with the cut marked inside it.

    A share too small to hold the marker yields the marker alone, a little
    over the share: what is said about the cut is never itself cut, and a
    budget spread over two hundred calls is allowed to overrun by the width
    of two hundred markers rather than lose the sentence that explains them.
    """
    if len(text) <= share:
        return text
    keep = max(0, share - len(_CLIPPED % (len(text) - share)))
    return text[:keep] + _CLIPPED % (len(text) - keep)


def render(obj, calls, results, notes, raised):
    """The result text: a header, the notes, and one block per call."""
    total = len(calls)
    lines = ["multi_tool ran %s%s." % (
        agent_file_ops.plural(total, "call"),
        "; %d raised and %s marked FAILED" % (raised, "is" if raised == 1 else "are")
        if raised else "")]
    lines.extend(notes)
    heads = [head(index, total, call) for index, call in enumerate(calls, 1)]
    # Exactly what the join below costs with every body empty: the header
    # lines and their breaks, the blank line, and each head with the break
    # after it and the blank line between blocks.
    overhead = (len("\n".join(lines)) + 2
                + sum(len(h) + 1 for h in heads) + 2 * max(0, len(heads) - 1))
    shares = _allocate([len(text) for text in results], MAX_CHARS - overhead)
    blocks = []
    for h, text, share in zip(heads, results, shares):
        body = _clip(text, share)
        blocks.append("%s\n%s" % (h, body) if body else h)
    return "\n".join(lines) + "\n\n" + "\n\n".join(blocks)


def run(obj, context=None, dispatch=None, refuse=None):
    """Run every call in `obj` and return one result text. Never raises for
    a call's own failure; always re-raises a cancellation.

    `dispatch(call)` runs one call and returns its result; it defaults to
    `agent_actions.execute_action` with this same `context`, which is what
    puts every inner call through the guards it would meet on its own.
    `refuse(entry)`, when given, is asked about every TEMPLATE before anything
    is expanded or run, and a sentence from it refuses the whole action -- it
    is how the worker loop applies its whitelists to the calls inside, on the
    same terms it applies them to a bare action.

    What ran is recorded on `obj` under RAN_KEY as (call, result) pairs, in
    order, so the transcript and the completion gates can ask about the calls
    that happened rather than the list that was asked for. A refusal returns
    before that key exists, which is how "nothing ran" is told apart from
    "nothing was asked".
    """
    templates, refusal = entries(obj)
    if refusal:
        return refusal
    if refuse is not None:
        for index, template in enumerate(templates, 1):
            said = refuse(template)
            if said:
                return _ENTRY_REFUSED % (index, len(templates),
                                         str(said).strip().rstrip("."))
    calls, notes, refusal = expand(obj)
    if refusal:
        return refusal
    if dispatch is None:
        import agent_actions
        dispatch = lambda call: agent_actions.execute_action(call, context)
    cancelled = _cancellation()
    ran = []
    obj[RAN_KEY] = ran
    results, raised = [], 0
    for call in calls:
        try:
            result = str(dispatch(call))
        except Exception as error:
            # A cancellation is the agent stopping, not a call failing, and
            # the whole of the kill guarantee is that no further call runs
            # once it has arrived. Everything else is this one call's own
            # failure and the calls after it still run.
            if cancelled is not None and isinstance(error, cancelled):
                raise
            result = _RAISED % (type(error).__name__, error)
            raised += 1
        ran.append((call, result))
        results.append(result)
    return render(obj, calls, results, notes, raised)


# --- what the rest of TMT asks about a multi_tool that ran -----------------

def ran(obj):
    """The (call, result) pairs that actually ran, or () before anything did."""
    if not isinstance(obj, dict):
        return ()
    recorded = obj.get(RAN_KEY)
    if not isinstance(recorded, list):
        return ()
    return tuple(pair for pair in recorded
                 if isinstance(pair, tuple) and len(pair) == 2
                 and isinstance(pair[0], dict))


def started(obj):
    """Whether `run` got as far as running anything. False for a refusal."""
    return isinstance(obj, dict) and isinstance(obj.get(RAN_KEY), list)


def mutates(obj):
    """Whether any call that ran is one `agent_config.MUTATING_ACTIONS` names.

    Asked instead of putting `multi_tool` in that set. The set is read by
    verb name, and for `bash` that cost was accepted because nothing can tell
    `make` from `ls` by the name alone. Here the inner verbs are known, so a
    multi_tool of reads can say honestly that it changed nothing -- and one
    that carried a write makes a passed review and a passed verification
    stale exactly as the write would have on its own.
    """
    try:
        from agent_config import MUTATING_ACTIONS
    except Exception:
        return True
    return any(call.get("action") in MUTATING_ACTIONS for call, _ in ran(obj))
