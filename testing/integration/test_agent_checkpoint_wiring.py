"""Checkpoints as the session actually reaches them: the loop, and the two commands.

`test_agent_checkpoint` drives the module on its own. This drives the halves
that only exist once it is wired in -- the hook the loop calls before every
dispatch, the commit at the end of every turn, and `/undo` and `/checkpoints`
through the real `agent_commands.dispatch`.

What is pinned, and why each is worth a test of its own:

- the hook is on BOTH dispatch paths. A batch is where a model puts its edits
  when it has several, so a hook only on the single-action path would miss
  exactly the turns most worth being able to undo -- and this repository has
  twice shipped a branch that was only ever rehearsed in the rare case;
- the snapshot is taken BEFORE the action, which is asserted against the
  source as well as by behaviour, because afterwards there is nothing left to
  snapshot and no test of the result can tell the difference;
- `/undo` alone changes NOTHING. Saying nothing means changing nothing is
  `replace_across`'s rule, reached here because this is the most destructive
  thing TMT does to a tree;
- an undo is refused while anything is still running, because a worker writing
  into a tree being rewritten under it would leave both wrong and neither able
  to tell;
- a turn that only read leaves no checkpoint at all, because a list full of
  rows about turns where nothing happened is a list nobody reads.
"""

import json
import re
from pathlib import Path

import agent_checkpoint as C
import agent_commands
import agent_config
import TMT

from test_agent_checkpoint import Store, project, write
from test_agent_cli import drive_session
from test_agent_workspace import Workspace


class Busy:
    """A register that says something is still working."""

    def __init__(self, agents=0, note=False, review=False):
        self._agents = agents
        self._note = _Record() if note else None
        self._review = _Record() if review else None

    def active_count(self):
        return self._agents

    def note(self):
        return self._note

    def review(self):
        return self._review


class _Record:
    def is_terminal(self):
        return False


class Exploding:
    """A register that cannot answer. The guard must not refuse forever."""

    def active_count(self):
        raise RuntimeError("boom")

    def note(self):
        raise RuntimeError("boom")

    def review(self):
        raise RuntimeError("boom")


def reply(action, **keys):
    keys["action"] = action
    keys.setdefault("progress", "doing it")
    return json.dumps(keys)


ANSWER = reply("end_conversation", message="Done.")


# --- the two commands -------------------------------------------------------

def test_both_commands_dispatch_and_are_offered_like_every_other():
    box, store = project(), Store()
    try:
        for name in ("undo", "checkpoints"):
            assert name in agent_commands.names(), name
            assert agent_commands.SUMMARY[name]
            assert agent_commands.USAGE[name].startswith("/" + name)
            result = agent_commands.dispatch("/" + name)
            assert result is not None, name
        # `/undo` takes an argument and `/checkpoints` does not, and the
        # refusal for a stray one reads like every other command's.
        refused = agent_commands.dispatch("/checkpoints now")
        assert not refused.ok and "takes no argument" in refused.text()
    finally:
        store.close()
        box.close()


def test_undo_alone_shows_what_would_move_and_moves_nothing():
    """`replace_across`'s rule, reached for its reason: a bulk change nobody
    looked at first is how a repository gets wrecked."""
    box, store = project(), Store()
    try:
        was = (box.path / "keep.py").read_bytes()
        C.commit(C.capture(label="rewrite the module"))
        write(box, "keep.py", b"changed\n")
        write(box, "brand_new.py", b"new\n")

        said = agent_commands.dispatch("/undo").text()
        assert "keep.py" in said and "brand_new.py" in said, said
        assert "DELETE" in said, said
        assert "Nothing has changed" in said, said
        assert "confirm" in said, said
        # The whole point.
        assert (box.path / "keep.py").read_bytes() == b"changed\n"
        assert (box.path / "brand_new.py").exists()

        done = agent_commands.dispatch("/undo confirm")
        assert done.ok, done.text()
        assert (box.path / "keep.py").read_bytes() == was
        assert not (box.path / "brand_new.py").exists()
        assert "Put back" in done.text() and "Deleted" in done.text()
    finally:
        store.close()
        box.close()


def test_a_named_checkpoint_can_be_undone_rather_than_only_the_newest():
    box, store = project(), Store()
    try:
        was = (box.path / "keep.py").read_bytes()
        first = C.commit(C.capture(label="turn one"))
        write(box, "keep.py", b"one\n")
        C.commit(C.capture(label="turn two"))
        write(box, "keep.py", b"two\n")

        listed = agent_commands.dispatch("/checkpoints").text()
        assert "turn one" in listed and "turn two" in listed, listed
        assert first.id in listed, listed

        said = agent_commands.dispatch("/undo %s confirm" % first.id)
        assert said.ok, said.text()
        assert (box.path / "keep.py").read_bytes() == was
    finally:
        store.close()
        box.close()


def test_an_undo_is_refused_while_anything_is_still_running():
    """A worker writing into a tree being rewritten under it would leave the
    restore and the worker both wrong, with neither able to tell."""
    box, store = project(), Store()
    try:
        C.commit(C.capture(label="a turn"))
        write(box, "keep.py", b"changed\n")
        for manager, phrase in ((Busy(agents=2), "2 agents"),
                                (Busy(note=True), "a note"),
                                (Busy(review=True), "a review")):
            said = agent_commands._undo("confirm", None, manager)
            assert not said.ok, said.text()
            assert phrase in said.text(), (phrase, said.text())
            assert (box.path / "keep.py").read_bytes() == b"changed\n"
        # Nothing running, and it goes through.
        assert agent_commands._undo("confirm", None, Busy()).ok
    finally:
        store.close()
        box.close()


def test_a_register_that_cannot_answer_does_not_refuse_the_undo_forever():
    """Guarded to "" for `_still_running`'s reason: a register that cannot
    answer must not lock the user out. The preview is the second look, so a
    wrong "" here costs a question rather than a tree."""
    box, store = project(), Store()
    try:
        C.commit(C.capture(label="a turn"))
        write(box, "keep.py", b"changed\n")
        said = agent_commands._undo("", None, Exploding())
        assert said.ok, said.text()
        assert "keep.py" in said.text(), said.text()
    finally:
        store.close()
        box.close()


def test_undo_says_so_when_there_is_nothing_to_undo():
    box, store = project(), Store()
    try:
        said = agent_commands.dispatch("/undo")
        assert not said.ok
        assert "nothing to undo" in said.text().lower(), said.text()
        listed = agent_commands.dispatch("/checkpoints")
        assert listed.ok and "Nothing yet" in listed.text()
        missing = agent_commands.dispatch("/undo 4321")
        assert not missing.ok and "no checkpoint 4321" in missing.text()
    finally:
        store.close()
        box.close()


def test_undo_says_so_when_the_workspace_already_matches():
    box, store = project(), Store()
    try:
        C.commit(C.capture(label="a turn"))
        said = agent_commands.dispatch("/undo")
        assert said.ok, said.text()
        assert "already matches" in said.text(), said.text()
    finally:
        store.close()
        box.close()


def test_the_command_fork_gives_undo_the_register_and_nothing_else_does():
    """`agent_commands.dispatch` takes a session and no more, which is right
    for every command that only reads. `/undo` needs the one question the
    register answers, so the loop hands it over -- the seam `/agents` and
    `/note` already use."""
    box, store = project(), Store()
    try:
        C.commit(C.capture(label="a turn"))
        write(box, "keep.py", b"changed\n")
        said = TMT._dispatch_command("/undo confirm", None, Busy(agents=1))
        assert not said.ok, said.text()
        assert "1 agent" in said.text(), said.text()
        # And through plain dispatch there is no register, so it is not
        # refused -- which is what makes the fork the thing being tested.
        assert agent_commands.dispatch("/undo confirm").ok
    finally:
        store.close()
        box.close()


# --- the loop ---------------------------------------------------------------

def test_the_hook_runs_before_the_action_on_both_dispatch_paths():
    """Asserted against the source because no test of the RESULT can tell the
    difference: after the action there is nothing left to snapshot, so a hook
    in the wrong place produces a checkpoint of the workspace as the action
    already left it -- which restores to exactly where it started.

    The same shape as the test that pins where `persist_context` is called.
    """
    source = Path(TMT.__file__).read_text(encoding="utf-8", errors="replace")
    for argument in ("obj", "sub_obj"):
        action = "action" if argument == "obj" else "sub_action"
        hook = source.find("checkpoints.before(%s, %s)" % (action, argument))
        dispatch = source.find("execute_action(%s, context)" % argument)
        assert hook != -1, argument
        assert dispatch != -1, argument
        assert hook < dispatch, (
            "the snapshot must be taken before the %s path dispatches" % argument)
    assert source.count("checkpoints.before(") == 2, "one hook per dispatch path"
    assert "checkpoints.commit()" in source
    assert "checkpoints.begin(task)" in source


def test_a_turn_that_writes_leaves_a_checkpoint_labelled_with_the_task():
    store = Store()
    try:
        drive_session(["add a greeting to hello.py", "quit"],
                      [reply("write_file", path="hello.py", content="hi\n"),
                       ANSWER])
        manifests = store.manifests()
        assert manifests == ["0001.json"], manifests
        body = json.loads((list(store.path.glob("*/turns/0001.json"))[0])
                          .read_text("utf-8"))
        assert body["label"] == "add a greeting to hello.py", body["label"]
        assert body["complete"] is True
        assert body["files"], "the before-picture held nothing"
        assert "hello.py" not in body["files"], (
            "the file the turn created must not be in the picture taken "
            "before it was created")
    finally:
        store.close()


def test_a_turn_that_only_reads_leaves_no_checkpoint_at_all():
    """Most turns change nothing. A list full of rows about those is a list
    nobody reads, and the walk they would cost is a third of a second and
    seventy megabytes on every question TMT is ever asked."""
    store = Store()
    try:
        drive_session(["what does this project do?", "quit"],
                      [reply("list_files"), ANSWER])
        assert store.manifests() == [], store.manifests()
        assert store.blobs() == [], store.blobs()
    finally:
        store.close()


def test_a_batch_of_edits_leaves_one_checkpoint_for_the_whole_turn():
    store = Store()
    try:
        drive_session(["write three files", "quit"],
                      [json.dumps({"actions": [
                          {"action": "write_file", "path": "a.py",
                           "content": "a\n", "progress": "one"},
                          {"action": "write_file", "path": "b.py",
                           "content": "b\n", "progress": "two"},
                          {"action": "write_file", "path": "c.py",
                           "content": "c\n", "progress": "three"}]}),
                       ANSWER])
        assert store.manifests() == ["0001.json"], store.manifests()
    finally:
        store.close()


def test_two_writing_turns_leave_two_checkpoints():
    """The turn boundary is real: `begin` forgets the last one and `commit`
    closes it, so a second question that writes gets its own before-picture
    rather than extending the first."""
    store = Store()
    try:
        drive_session(["write one", "write two", "quit"],
                      [reply("write_file", path="one.py", content="1\n"),
                       ANSWER,
                       reply("write_file", path="two.py", content="2\n"),
                       ANSWER])
        assert store.manifests() == ["0001.json", "0002.json"], store.manifests()
        labels = sorted(json.loads(path.read_text("utf-8"))["label"]
                        for path in store.path.glob("*/turns/*.json"))
        assert labels == ["write one", "write two"], labels
    finally:
        store.close()


def test_the_store_never_appears_in_the_workspace_a_session_ran_in():
    """The rule every piece of TMT's own state keeps, checked through a whole
    real session rather than against the constant."""
    store = Store()
    try:
        screen, _, _ = drive_session(
            ["write a file", "quit"],
            [reply("write_file", path="kept.py", content="k\n"), ANSWER])
        assert ".tmt_checkpoints" not in screen
        assert store.manifests(), "nothing was checkpointed at all"
        assert agent_config.CHECKPOINT_DIR == store.path
    finally:
        store.close()


def test_the_module_is_declared_where_an_editable_install_can_see_it():
    """An editable install writes a frozen py-modules list, so a module that
    is not in it is invisible to `tmtcode` however well it works here."""
    text = (Path(agent_config.__file__).resolve().parent
            / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^\s*"agent_checkpoint",\s*$', text, re.M), \
        "agent_checkpoint is missing from pyproject.toml's py-modules"


def test_undoing_is_not_an_operation_the_model_can_reach():
    """The escape hatch belongs to the USER and to nobody else.

    A model that could restore a checkpoint could undo its own work, or
    somebody else's, and then answer as though the turn had gone differently
    -- the runtime would have handed the one mechanism that proves what
    happened to the party it exists to check. So there is no action, no key
    and no prompt section: `/undo` is typed by a person, and `agent_checkpoint`
    is reached from the session loop and from `agent_commands` and from
    nowhere else.

    Asserted as an ABSENCE, the way `agent_review`'s "only parse_result can
    produce a pass" is -- section 15 of that brief was implemented as a
    missing key too, and the test that proves it tries the shapes a model
    would reach for.
    """
    import agent_prompt
    for verb in ("undo", "checkpoint", "checkpoints", "restore", "rewind"):
        assert verb not in agent_config.REQUIRED_KEYS, verb
        assert agent_prompt.validate_action({"action": verb}), \
            "%s must not validate as an action" % verb
    # Against an EMPTY workspace, and this is not fussiness. The prompt
    # inlines the workspace, TMT's own repository IS the workspace when TMT
    # is run on TMT, and `agent_checkpoint.py` is a file in it -- so asking
    # "is this string in the prompt" here answers yes for anything written in
    # any module. `test_agent_tips` was changed for exactly this, and
    # `test_agent_web` met it again through a docs filename.
    empty = Workspace()
    try:
        empty.use()
        agent_prompt.invalidate_prompt()
        prompt = agent_prompt.get_system_prompt()
        for phrase in ("agent_checkpoint", "/undo", "/checkpoints"):
            assert phrase not in prompt, phrase
    finally:
        empty.close()
    # And nothing in the dispatcher answers to it either.
    import agent_actions
    assert "undo" not in agent_actions.ACTION_LABELS
    assert "restore" not in agent_actions.ACTION_LABELS


def test_the_module_starts_no_processes_of_its_own():
    """Deleting and overwriting files across a whole workspace is the last
    place a shell-out belongs, and `test_agent_bash` allows only four modules
    to create a process at all."""
    source = (Path(C.__file__)).read_text(encoding="utf-8", errors="replace")
    assert not re.search(r"^\s*import subprocess", source, re.M)
    # CALLS, not the substring. `_scan`'s own docstring says in prose that it
    # uses `agent_file_ops.iter_workspace_files` rather than an os.walk of its
    # own, and a naive `"os.walk" not in source` fails on the sentence
    # explaining why it is not there. `test_agent_web` learned the same lesson
    # from a docs filename putting the word "bash" into a prompt.
    for banned in (r"os\.system\(", r"Popen\(", r"os\.walk\(", r"os\.execv"):
        assert not re.search(banned, source), banned
