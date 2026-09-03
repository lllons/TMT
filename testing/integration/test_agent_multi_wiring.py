"""`multi_tool` as far as TMT is concerned: registered, dispatched, guarded, drawn.

Everything here goes through the real seams -- `agent_actions.execute_action`,
the worker loop, the session loop -- and never through `agent_multi.run`
with a fake dispatcher, which is `test_agent_multi`'s instrument. The reason
is the one every wiring file in this directory states: a tool that works
perfectly and is not registered is a tool that does not exist, and a tool
that is registered and walks round a guard is worse than one that does not
exist.

The guard question is the one this file is mostly about. A multi_tool is a
LIST of calls, and a list is the obvious place to put a verb the loop would
refuse on its own. So every layer that refuses a bare verb is driven here
with the same verb inside a list: the note agent's whitelist, the reviewer's,
the delegation contract (both of its layers), the capability gate, the push
authority and the command policy. In each case the sentence is the one the
bare verb gets, and in the worker's case nothing in the list runs.
"""

import io
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

import agent_actions
import agent_config
import agent_delegation
import agent_multi
import agent_prompt
import agent_subprompts
import agent_ui
import agent_worker
import TMT
from agent_config import REQUIRED_KEYS
from agent_manager import AgentManager, WorkerCancelled

from test_agent_cli import drive_session

REPO = Path(agent_config.__file__).resolve().parent


def remove_tree(path):
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


class Project:
    """A throwaway workspace, with TMT's own state sent somewhere throwaway too."""

    def __init__(self, files=None):
        self.previous_root = agent_config.ROOT_DIR
        self.previous_install = agent_config.INSTALL_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_multi_")).resolve()
        self.install = Path(tempfile.mkdtemp(prefix="tmt_multiinst_")).resolve()
        agent_config.ROOT_DIR = self.path
        agent_config.INSTALL_DIR = self.install
        for name, body in (files or {}).items():
            self.write(name, body)

    def write(self, name, body):
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body.encode("utf-8"))
        return target

    def close(self):
        agent_config.ROOT_DIR = self.previous_root
        agent_config.INSTALL_DIR = self.previous_install
        agent_prompt.invalidate_prompt()
        remove_tree(self.path)
        remove_tree(self.install)


PROJECT = {
    "src/a.py": "import logging\n\nlog = logging.getLogger('a')\nA = 1\n",
    "src/b.py": "import os\nB = 2\n",
    "src/deep/c.py": "C = 3\n",
    "docs/readme.md": "hello\n",
}


def run(obj, context=None):
    """One action through the dispatcher, with the loop's default authority."""
    return str(agent_actions.execute_action(
        obj, {"push_authorized": False} if context is None else context))


def multi(*calls, **extra):
    obj = {"action": "multi_tool", "calls": list(calls)}
    obj.update(extra)
    return obj


def act(name, **keys):
    keys["action"] = name
    return json.dumps(keys)


FINISH = act("internal_response", response="done")


class Replies:
    """Scripted model replies for a background agent, handed out in order."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.messages = []

    def __call__(self, messages, on_event=None, model=None, max_tokens=None,
                 quiet=False, **extra):
        self.messages = [dict(m) for m in messages]
        return self.replies.pop(0) if self.replies else FINISH

    def said(self):
        return "\n".join(str(m.get("content")) for m in self.messages)


# --- registration -------------------------------------------------------------

def test_the_verb_is_registered_with_the_one_key_it_requires():
    """REQUIRED_KEYS is the whole of what `validate_action` knows. `calls` is
    the only thing a multi_tool cannot do without; `limit` only widens."""
    assert REQUIRED_KEYS.get("multi_tool") == ["calls"], REQUIRED_KEYS.get("multi_tool")
    assert agent_prompt.validate_action({"action": "multi_tool", "calls": []}) is None
    assert agent_prompt.validate_action({"action": "multi_tool"}) is not None


def test_the_verb_is_labelled_kinded_and_in_every_whitelist_but_the_nudge():
    assert agent_actions.ACTION_LABELS.get("multi_tool") == "Multi Tool"
    assert agent_actions._EVENT_KIND_FOR_ACTION.get("multi_tool") == "tool"
    # A security whitelist: a read-only delegation may read five files in
    # one action, and the calls inside are refused one by one.
    assert "multi_tool" in agent_delegation.READ_ONLY_ACTIONS
    # The note agent's and the reviewer's, and the prompts that restate them.
    assert "multi_tool" in agent_worker.NOTE_ACTIONS
    assert "multi_tool" in agent_worker.REVIEW_ACTIONS
    assert set(agent_worker.NOTE_ACTIONS) == set(agent_subprompts.NOTE_VERBS)
    assert set(agent_worker.REVIEW_ACTIONS) == set(agent_subprompts.REVIEW_VERBS)
    assert "multi_tool" not in agent_worker.WORKER_FORBIDDEN
    assert "multi_tool" not in agent_worker.WORKER_NEEDS_TERMINAL
    # NOT the "now answer the question" nudge: a multi_tool may be writes.
    assert "multi_tool" not in agent_actions.READ_ONLY_ACTIONS


def test_the_verb_is_not_in_mutating_actions_because_its_calls_decide():
    """The set is read by verb NAME. `bash` went in because nothing can tell
    `make` from `ls` by the name; here the inner verbs are known, so
    `TMT.mutated` asks `agent_multi` instead and a list of reads keeps a
    passed review standing."""
    assert "multi_tool" not in agent_config.MUTATING_ACTIONS
    reads = multi({"action": "read_file", "path": "a"})
    reads[agent_multi.RAN_KEY] = [({"action": "read_file", "path": "a"}, "x")]
    assert TMT.mutated("multi_tool", reads) is False
    writes = multi({"action": "write_file", "path": "a", "content": "b"})
    writes[agent_multi.RAN_KEY] = [({"action": "write_file", "path": "a", "content": "b"}, "x")]
    assert TMT.mutated("multi_tool", writes) is True
    # A multi_tool that never ran changed nothing.
    assert TMT.mutated("multi_tool", multi({"action": "write_file", "path": "a", "content": "b"})) is False
    # And every other verb is answered by the set, exactly as before.
    assert TMT.mutated("write_file", {}) is True
    assert TMT.mutated("read_file", {}) is False


def test_the_module_is_in_the_frozen_module_list():
    """An editable install writes `py-modules` at install time, so a module
    missing from pyproject is invisible to `tmtcode` however well it works."""
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert '"agent_multi",' in text, "agent_multi is missing from py-modules"


# --- the prompt ------------------------------------------------------------

def test_the_reference_teaches_the_verb_and_every_example_in_it_is_real():
    """Every `{"action":"multi_tool"...}` line in the reference must be a
    valid multi_tool AND every call inside it must be one `agent_multi`
    would run -- the examples are the teaching device, and a template that
    the module refused would teach a shape that does not work."""
    text = agent_prompt.ACTION_REFERENCE
    assert "multi_tool - keys: calls" in text
    box = Project(files=PROJECT)
    try:
        seen = 0
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith('{"action":"multi_tool"'):
                continue
            seen += 1
            obj = json.loads(line)
            assert agent_prompt.validate_action(obj) is None, line
            templates, refusal = agent_multi.entries(obj)
            assert refusal == "", (line, refusal)
            assert templates, line
        assert seen >= 3, seen
    finally:
        box.close()


def test_the_tool_choice_table_has_the_row_and_its_arrow_lines_up():
    rows = [line for line in agent_prompt.TOOL_CHOICE_RULES.splitlines()
            if line.startswith("  ") and "->" in line]
    ours = [line for line in rows if line.endswith("-> multi_tool")]
    assert len(ours) == 1, rows
    assert len({line.index("->") for line in rows}) == 1, "the arrows do not line up"
    assert "8. multi_tool" in agent_prompt.TOOL_CHOICE_RULES
    assert "11. Several reads" in agent_prompt.PREFERENCE_RULES


def test_every_kind_of_agent_is_taught_the_verb():
    """It is on every whitelist, so every prompt offers it. The calls inside
    are what each whitelist actually decides."""
    box = Project(files=PROJECT)
    try:
        for prompt in (agent_prompt.get_system_prompt(),
                       agent_subprompts.worker_prompt(),
                       agent_subprompts.note_prompt(),
                       agent_subprompts.review_prompt()):
            assert "multi_tool - keys: calls" in prompt
    finally:
        box.close()


def test_the_worked_example_in_the_answering_section_is_a_real_multi_tool():
    lines = [line.strip() for line in agent_prompt.ANSWERING_EXAMPLES.splitlines()]
    examples = [line[len("You emit:"):].strip() for line in lines if line.startswith("You emit:")]
    ours = [json.loads(line) for line in examples if '"multi_tool"' in line]
    assert len(ours) == 1, examples
    box = Project(files=PROJECT)
    try:
        templates, refusal = agent_multi.entries(ours[0])
        assert refusal == "" and templates, refusal
    finally:
        box.close()


# --- the dispatcher -------------------------------------------------------------

def test_several_reads_come_back_in_one_result_under_numbered_headers():
    box = Project(files=PROJECT)
    try:
        obj = multi({"action": "read_file", "path": "src/a.py"},
                    {"action": "read_file", "path": "src/b.py"},
                    {"action": "read_lines", "path": "src/deep/c.py", "start": 1, "end": 1})
        said = run(obj)
        assert said.startswith("multi_tool ran 3 calls."), said
        assert "[1/3] read_file src/a.py" in said
        assert "logging.getLogger('a')" in said
        assert "[2/3] read_file src/b.py" in said
        assert "[3/3] read_lines src/deep/c.py" in said
        assert "    1 | C = 3" in said, said
    finally:
        box.close()


def test_for_each_reads_the_first_lines_of_every_python_file_in_one_action():
    """The request this feature was asked for, word for word."""
    box = Project(files=PROJECT)
    try:
        said = run(multi({"action": "read_lines", "for_each": "**/*.py", "start": 1, "end": 2}))
        assert said.startswith("multi_tool ran 3 calls."), said
        assert 'for_each "**/*.py" (read_lines) matched 3 files.' in said
        assert "[1/3] read_lines src/a.py" in said
        assert "    1 | import logging" in said
        assert "[2/3] read_lines src/b.py" in said
        assert "[3/3] read_lines src/deep/c.py" in said
        assert "hello" not in said, "the markdown file is not a python file"
    finally:
        box.close()


def test_a_placeholder_puts_the_file_into_a_command_and_the_command_really_runs():
    """`bash` inside a list goes through the parser, the policy and the
    sandbox exactly as it does alone: one real process per matched file."""
    box = Project(files=PROJECT)
    try:
        said = run(multi({"action": "bash", "for_each": "src/*.py",
                          "command": "python -m py_compile {path}"}))
        assert said.startswith("multi_tool ran 2 calls."), said
        assert "[1/2] bash python -m py_compile src/a.py" in said, said
        assert "[2/2] bash python -m py_compile src/b.py" in said, said
        assert said.count("exit 0") == 2, said
    finally:
        box.close()


def test_a_write_inside_really_writes_and_the_record_says_it_mutated():
    box = Project(files=PROJECT)
    try:
        obj = multi({"action": "write_file", "for_each": "src/*.py",
                     "path": "notes/{stem}.md", "content": "# {name}\n"},
                    {"action": "append_file", "path": "docs/readme.md", "content": "more\n"})
        said = run(obj)
        assert said.startswith("multi_tool ran 3 calls."), said
        assert (box.path / "notes/a.md").read_text(encoding="utf-8") == "# a.py\n"
        assert (box.path / "notes/b.md").read_text(encoding="utf-8") == "# b.py\n"
        assert (box.path / "docs/readme.md").read_text(encoding="utf-8") == "hello\nmore\n"
        assert agent_multi.mutates(obj) is True
        assert TMT.mutated("multi_tool", obj) is True
    finally:
        box.close()


def test_a_missing_file_is_that_calls_result_and_the_others_still_run():
    box = Project(files=PROJECT)
    try:
        said = run(multi({"action": "read_file", "path": "nope.py"},
                         {"action": "read_file", "path": "src/b.py"}))
        assert "File not found: nope.py" in said
        assert "B = 2" in said
    finally:
        box.close()


def test_a_path_outside_the_workspace_is_refused_per_call_in_words():
    """`safe_path` raises ValueError; alone, `_run_tool` turns that into a
    sentence. Inside a list the call's own failure is recorded as FAILED and
    nothing outside the workspace is read."""
    box = Project(files=PROJECT)
    try:
        said = run(multi({"action": "read_file", "path": "../../outside.txt"},
                         {"action": "read_file", "path": "src/b.py"}))
        assert "1 raised and is marked FAILED" in said, said
        assert "Blocked unsafe path" in said, said
        assert "B = 2" in said
    finally:
        box.close()


# --- every guard, asked per call ------------------------------------------------------------

def test_the_read_only_contract_refuses_a_write_inside_and_lets_the_reads_run():
    """The dispatcher's own layer of the delegation contract, asked per
    call. The read runs, the write is refused by the sentence it gets alone,
    and the file is not there afterwards."""
    box = Project(files=PROJECT)
    try:
        context = {"push_authorized": False, "read_only": True}
        said = run(multi({"action": "read_file", "path": "src/b.py"},
                         {"action": "write_file", "path": "src/new.py", "content": "x"}),
                   context)
        assert said.startswith("multi_tool ran 2 calls."), said
        assert "B = 2" in said
        assert agent_delegation.VIOLATION_HEADER in said, said
        assert not (box.path / "src/new.py").exists()
    finally:
        box.close()


def test_the_capability_gate_and_the_push_authority_are_asked_per_call():
    box = Project(files=PROJECT)
    try:
        said = run(multi({"action": "plan", "operation": "create", "steps": [{"title": "a"}]},
                         {"action": "verify"},
                         {"action": "git_push"}))
        assert said.startswith("multi_tool ran 3 calls."), said
        assert "/plan capability is not enabled" in said, said
        assert "/verify capability is not enabled" in said, said
        assert agent_actions.PUSH_BLOCKED in said, said
    finally:
        box.close()


def test_the_loop_verbs_cannot_be_smuggled_in_through_the_dispatcher():
    box = Project(files=PROJECT)
    try:
        for verb, keys in (("end_conversation", {"message": "bye"}),
                           ("send_message", {"message": "hi"}),
                           ("internal_response", {"response": "done"}),
                           ("review_agenda", {"operation": "show"}),
                           ("multi_tool", {"calls": [{"action": "list_files"}]})):
            obj = multi({"action": "read_file", "path": "src/b.py"}, dict(keys, action=verb))
            said = run(obj)
            assert said.startswith("multi_tool call 2 of 2 cannot run:"), (verb, said)
            assert "B = 2" not in said, "the read ran although the list was refused"
    finally:
        box.close()


# --- the transcript row ---------------------------------------------------------------------

def test_the_row_adds_up_what_its_calls_did_and_names_every_path():
    box = Project(files=PROJECT)
    try:
        obj = multi({"action": "read_file", "path": "src/a.py"},
                    {"action": "append_file", "path": "docs/readme.md", "content": "one\ntwo\n"},
                    {"action": "patch_file", "path": "src/b.py", "search": "B = 2", "replace": "B = 3"})
        said = run(obj)
        event = agent_actions.action_event("multi_tool", obj, said)
        assert event.kind == "tool", event.kind
        assert event.message == "Multi Tool: Read File, Append File, Patch File", event.message
        assert event.detail["calls"] == 3
        assert (event.detail["added"], event.detail["removed"]) == (3, 1), event.detail
        assert event.detail["paths"] == ("src/a.py", "docs/readme.md", "src/b.py"), event.detail
        # The session record reads paths off the same function the row does.
        assert agent_actions._paths_named("multi_tool", obj) == ("src/a.py", "docs/readme.md", "src/b.py")
        # And the facts row draws the count.
        transcript = agent_ui.Transcript(stream=io.StringIO())
        assert "3 calls" in transcript._facts(event)
    finally:
        box.close()


def test_the_row_is_a_warning_when_any_call_would_have_drawn_one():
    box = Project(files=PROJECT)
    try:
        obj = multi({"action": "read_file", "path": "src/a.py"},
                    {"action": "patch_file", "path": "src/b.py", "search": "not here", "replace": "x"})
        said = run(obj)
        event = agent_actions.action_event("multi_tool", obj, said)
        assert event.kind == "warning", event.kind
        assert event.message.endswith("(1 warning)"), event.message
        assert event.detail["calls"] == 2
    finally:
        box.close()


def test_a_refused_multi_tool_draws_the_refusal():
    obj = multi()
    said = run(obj)
    event = agent_actions.action_event("multi_tool", obj, said)
    assert event.kind == "warning"
    assert event.message.startswith("multi_tool needs \"calls\""), event.message
    assert "calls" not in event.detail


def test_a_fan_out_is_one_row_with_the_count_not_a_row_per_file():
    box = Project(files=PROJECT)
    try:
        obj = multi({"action": "read_lines", "for_each": "**/*.py", "start": 1, "end": 1})
        said = run(obj)
        event = agent_actions.action_event("multi_tool", obj, said)
        assert event.message == "Multi Tool: Read Lines x3", event.message
        assert event.detail["calls"] == 3
        assert event.detail["paths"] == ("src/a.py", "src/b.py", "src/deep/c.py")
    finally:
        box.close()


# --- the loop's bookkeeping -------------------------------------------------------------------

class Recording:
    """A review or verification state that records what the loop tells it."""

    def __init__(self):
        self.changes, self.runs = [], []

    def note_change(self, action, paths=()):
        self.changes.append((action, tuple(paths)))

    def note_run(self, action, target=""):
        self.runs.append((action, target))


class FakeSession:
    def __init__(self):
        self.review = Recording()
        self.verify = Recording()


def test_note_work_tells_the_review_and_verification_states_about_each_call_that_ran():
    """Under the CALL's own verb, so a write inside a list makes a passed
    review stale exactly as it would alone, a command is recorded as the run
    it was, and a list of reads tells both states nothing at all."""
    box = Project(files=PROJECT)
    try:
        session = FakeSession()
        obj = multi({"action": "read_file", "path": "src/a.py"},
                    {"action": "append_file", "path": "docs/readme.md", "content": "x\n"},
                    {"action": "bash", "command": "python -m py_compile src/b.py"})
        run(obj)
        TMT.note_work(session, "multi_tool", obj)
        assert session.review.changes == [("append_file", ("docs/readme.md",)),
                                          ("bash", ())], session.review.changes
        assert session.verify.changes == session.review.changes
        assert session.review.runs == [("bash", "python -m py_compile src/b.py")], session.review.runs
        quiet = FakeSession()
        reads = multi({"action": "read_file", "path": "src/a.py"}, {"action": "list_files"})
        run(reads)
        TMT.note_work(quiet, "multi_tool", reads)
        assert quiet.review.changes == [] and quiet.review.runs == []
        # A multi_tool that was refused before anything ran tells them nothing.
        untouched = FakeSession()
        TMT.note_work(untouched, "multi_tool", multi())
        assert untouched.review.changes == [] and untouched.verify.changes == []
    finally:
        box.close()


def test_a_turn_can_read_several_files_in_one_round_and_answer_on_the_next():
    """End to end through `TMT.main`: the model sends one multi_tool, the
    result of every call comes back in ONE user turn, and the transcript
    shows one row for the lot."""
    # drive_session builds an empty workspace of its own, so the two reads
    # come back "File not found" -- which is each call's own result and is
    # exactly what the second request has to carry. Nothing here needs the
    # files to exist; what is being driven is the loop around the action.
    replies = [
        json.dumps(multi({"action": "read_file", "path": "one.txt"},
                         {"action": "read_file", "path": "two.txt"},
                         progress="Reading both files at once.")),
        json.dumps({"action": "end_conversation", "message": "Neither file is there."}),
    ]
    screen, seen, console = drive_session(["read both files", "quit"], replies)
    assert len(seen) == 2, len(seen)
    # The second request carries the one result with both calls in it.
    second = "\n".join(str(m.get("content")) for m in seen[1])
    assert "multi_tool ran 2 calls." in second, second
    assert "[1/2] read_file one.txt" in second and "[2/2] read_file two.txt" in second, second
    assert second.count("File not found") == 2, second
    assert "Multi Tool: Read File x2" in screen, screen
    assert "Neither file is there." in screen, screen
    assert console.said() == "", console.said()


# --- the worker loop: whitelists per call, nothing runs on a refusal ----------------------

def test_the_note_agent_may_read_several_files_in_one_action():
    manager = AgentManager()
    record = manager.spawn("q", kind="note")
    executed = []

    def execute(obj, context):
        executed.append(obj["action"])
        return "body of %s" % obj["path"]

    ask = Replies([json.dumps(multi({"action": "read_file", "path": "a"},
                                    {"action": "read_file", "path": "b"})), FINISH])
    answer = agent_worker.run_note(record, manager, ask=ask, execute=execute, system_prompt="p")
    assert answer == "done"
    assert executed == ["read_file", "read_file"], executed
    assert "multi_tool ran 2 calls." in ask.said()
    assert "[2/2] read_file b" in ask.said()
    # Each inner read is on the record, as it would be alone.
    assert record.reads == ("a", "b"), record.reads


def test_the_note_agents_whitelist_refuses_a_write_inside_and_nothing_in_the_list_runs():
    """The first layer, and the one the list is the obvious way round. The
    sentence is the one the bare verb gets, the entry is named, and the read
    listed BEFORE the write did not run either."""
    manager = AgentManager()
    record = manager.spawn("q", kind="note")
    executed = []

    def execute(obj, context):
        executed.append(obj["action"])
        return "ok"

    ask = Replies([json.dumps(multi({"action": "read_file", "path": "a"},
                                    {"action": "write_file", "path": "a", "content": "x"})),
                   FINISH])
    answer = agent_worker.run_note(record, manager, ask=ask, execute=execute, system_prompt="p")
    assert answer == "done"
    assert executed == [], executed
    said = ask.said()
    assert "multi_tool call 2 of 2 cannot run: REFUSED: 'write_file'" in said, said
    assert "Nothing ran." in said


def test_the_reviewer_is_refused_a_review_and_the_worker_a_push_inside_a_list():
    for kind, runner, verb, keys in (
            ("review", agent_worker.run_review, "review", {}),
            ("worker", agent_worker.run_worker, "git_push", {}),
            ("worker", agent_worker.run_worker, "bash", {"command": "ls"}),
            ("worker", agent_worker.run_worker, "delete_file", {"path": "a"})):
        manager = AgentManager()
        record = manager.spawn("q", kind=kind)
        executed = []

        def execute(obj, context):
            executed.append(obj["action"])
            return "ok"

        ask = Replies([json.dumps(multi({"action": "read_file", "path": "a"},
                                        dict(keys, action=verb))), FINISH])
        runner(record, manager, ask=ask, execute=execute, system_prompt="p")
        assert executed == [], (verb, executed)
        assert "multi_tool call 2 of 2 cannot run: REFUSED: '%s'" % verb in ask.said(), (verb, ask.said())


def test_a_read_only_delegation_is_refused_a_write_inside_by_its_contract_first():
    """The contract before the kind of agent, per entry, so the model is told
    which of the two facts about it is the reason -- and the violation is
    recorded once, for the entry, not once per matched file."""
    box = Project(files=PROJECT)
    try:
        manager = AgentManager()
        constraints, error = agent_delegation.parse({"read_only": True})
        assert error == "", error
        record = manager.spawn("q", constraints=constraints)
        ask = Replies([json.dumps(multi({"action": "read_file", "path": "src/a.py"},
                                        {"action": "write_file", "for_each": "src/*.py",
                                         "path": "out/{stem}.txt", "content": "x"})),
                       FINISH])
        agent_worker.run_worker(record, manager, ask=ask,
                                execute=agent_actions.execute_action, system_prompt="p")
        said = ask.said()
        assert agent_delegation.VIOLATION_HEADER in said, said
        assert "call 2 of 2" in said, said
        assert not (box.path / "out").exists()
        assert len(record.violations) == 1, record.violations
    finally:
        box.close()


def test_a_kill_in_the_middle_of_a_list_stops_the_calls_after_it():
    manager = AgentManager()
    record = manager.spawn("q")
    ran = []

    def execute(obj, context):
        ran.append(obj["path"])
        if len(ran) == 1:
            record.cancel.set()
        return "ok"

    ask = Replies([json.dumps(multi({"action": "read_file", "path": "1"},
                                    {"action": "read_file", "path": "2"},
                                    {"action": "read_file", "path": "3"}))])
    try:
        agent_worker.run_worker(record, manager, ask=ask, execute=execute, system_prompt="p")
    except WorkerCancelled:
        pass
    else:
        raise AssertionError("the kill did not stop the worker")
    assert ran == ["1"], ran


def test_a_multi_tool_inside_a_batch_is_guarded_on_the_batch_path_too():
    """The batch path is not an afterthought: a whitelist enforced on one
    dispatch path is a whitelist a model can walk round by putting the list
    in a list."""
    manager = AgentManager()
    record = manager.spawn("q", kind="note")
    executed = []

    def execute(obj, context):
        executed.append(obj["action"])
        return "ok"

    batch = {"actions": [multi({"action": "read_file", "path": "a"},
                               {"action": "write_file", "path": "a", "content": "x"})]}
    ask = Replies([json.dumps(batch), FINISH])
    agent_worker.run_note(record, manager, ask=ask, execute=execute, system_prompt="p")
    assert executed == [], executed
    assert "REFUSED: 'write_file'" in ask.said()
    # And an allowed list inside a batch runs, through the same dispatcher.
    record = manager.spawn("q2", kind="note")
    ask = Replies([json.dumps({"actions": [multi({"action": "read_file", "path": "a"},
                                                 {"action": "read_file", "path": "b"})]}),
                   FINISH])
    agent_worker.run_note(record, manager, ask=ask, execute=execute, system_prompt="p")
    assert executed == ["read_file", "read_file"], executed
    assert "multi_tool ran 2 calls." in ask.said()
