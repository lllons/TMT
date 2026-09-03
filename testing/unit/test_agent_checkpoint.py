"""`agent_checkpoint` on its own: what is captured, what is refused, what is put back.

Everything here drives real files in a real temporary workspace, with a real
store in a second temporary directory, because the whole contract is about
bytes on disk. A fake filesystem would test the arithmetic and miss the two
things that actually matter -- that a restore is byte-exact across CRLF and LF,
and that it puts a file back where the workspace really is.

What is pinned, and why each is worth a test of its own:

- a round trip is byte-identical, CRLF included, because this repository is a
  mix of line endings and a restore that normalised one would be a restore that
  changed the file it claimed to have put back;
- `refusal` FAILS CLOSED for every case it cannot be sure of, which is what
  puts it with `agent_capabilities.refusal` rather than with the completion
  gates -- a half-restored workspace is the failure this module exists to make
  impossible;
- a refused plan carries the refusal and NO paths, because a list of files
  beside a sentence saying it will not happen is a list somebody acts on;
- `restore` takes the PLAN, so what was previewed is what runs -- the identity
  `agent_uninstall` settled for the other action here that cannot be undone;
- `will_mutate` is asked BEFORE dispatch and therefore reads a multi_tool's
  requested calls rather than `agent_multi`'s record of what ran, which does
  not exist yet;
- one snapshot per turn however many files the turn writes, because the
  snapshot is of the whole workspace and the fortieth write is already in the
  picture taken before the first.
"""

import json
import os
import tempfile
import time
from pathlib import Path

import agent_checkpoint as C
import agent_config

from test_agent_workspace import Workspace, remove_tree


class Store:
    """A throwaway checkpoint store, MADE the store.

    `agent_config.CHECKPOINT_DIR` is what the module reads at call time -- it
    is deliberately never bound at import -- so redirecting it here is the
    whole of the isolation, and restoring it in `close` is what stops a leaked
    value pointing every later test at a deleted directory.
    """

    def __init__(self):
        self.previous = agent_config.CHECKPOINT_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_cp_")).resolve()
        agent_config.CHECKPOINT_DIR = self.path

    def blobs(self):
        return sorted(p.name for p in self.path.glob("*/blobs/*/*"))

    def manifests(self):
        return sorted(p.name for p in self.path.glob("*/turns/*.json"))

    def close(self):
        agent_config.CHECKPOINT_DIR = self.previous
        remove_tree(self.path)


FILES = {
    "keep.py": "print('a')\n",
    "gone.py": "old\n",
    "sub/deep.txt": "deep\n",
    "docs/notes.md": "notes\n",
}


def project(files=None):
    """A throwaway workspace, MADE the workspace.

    `Workspace` only builds the directory; `use()` is what points TMT at it,
    and `test_agent_multi` records what happens when that line is left out --
    a walk over this repository instead of over the fixture.
    """
    box = Workspace(files=FILES if files is None else files)
    box.use()
    return box


def write(box, name, data):
    """Write bytes, never text: the point of most of this file is the bytes."""
    target = box.path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


# --- capture and restore ---------------------------------------------------

def test_a_round_trip_puts_every_kind_of_change_back():
    """A file changed, a file deleted and a file created, in one turn, undone
    by one restore -- and the result names all three."""
    box, store = project(), Store()
    try:
        write(box, "keep.py", b"print('a')\r\n")   # CRLF on purpose
        # Read rather than assumed: `Workspace` writes its fixture with
        # `write_text`, which translates the line ending on Windows, so a
        # literal here would be asserting the platform rather than the
        # restore. What is wanted is "byte-identical to what was there".
        was = {name: (box.path / name).read_bytes()
               for name in ("keep.py", "gone.py")}
        before = C.capture(label="fix the parser")
        write(box, "keep.py", b"print('b')\r\n")
        (box.path / "gone.py").unlink()
        write(box, "new.py", b"brand new\n")
        C.commit(before)

        plan = C.plan(before)
        assert plan.refusal == "", plan.refusal
        assert plan.overwrite == ["keep.py"], plan.overwrite
        assert plan.recreate == ["gone.py"], plan.recreate
        assert plan.delete == ["new.py"], plan.delete

        outcome = C.restore(plan)
        assert outcome.ok(), outcome.failed
        assert sorted(outcome.restored) == ["gone.py", "keep.py"], outcome.restored
        assert outcome.deleted == ["new.py"], outcome.deleted
        # Bytes, not text. A restore that "helpfully" normalised the line
        # ending would pass a text comparison and have changed the file.
        assert (box.path / "keep.py").read_bytes() == was["keep.py"] == b"print('a')\r\n"
        assert (box.path / "gone.py").read_bytes() == was["gone.py"]
        assert not (box.path / "new.py").exists()
    finally:
        store.close()
        box.close()


def test_a_restore_is_itself_checkpointed_so_an_undo_can_be_undone():
    """The most destructive thing TMT does to a tree is the one action whose
    own mistake would otherwise have no way back."""
    box, store = project(), Store()
    try:
        was = (box.path / "keep.py").read_bytes()
        first = C.commit(C.capture(label="the change"))
        write(box, "keep.py", b"changed\n")
        outcome = C.restore(C.plan(first))
        assert outcome.checkpoint is not None
        assert outcome.checkpoint.id != first.id
        assert (box.path / "keep.py").read_bytes() == was
        # And the redo really is a redo: restoring the checkpoint the undo
        # took puts the change back.
        C.restore(C.plan(outcome.checkpoint))
        assert (box.path / "keep.py").read_bytes() == b"changed\n"
    finally:
        store.close()
        box.close()


def test_a_restore_that_changes_nothing_asks_for_no_checkpoint_of_its_own():
    """An empty plan is not work, and a checkpoint per no-op would fill the
    list the user reads with rows about turns where nothing happened."""
    box, store = project(), Store()
    try:
        snapshot = C.commit(C.capture(label="nothing moved"))
        plan = C.plan(snapshot)
        assert plan.empty()
        outcome = C.restore(plan)
        assert outcome.checkpoint is None
        assert store.manifests() == ["0001.json"], store.manifests()
    finally:
        store.close()
        box.close()


def test_restore_takes_the_plan_that_was_shown_and_not_the_snapshot():
    """A user agrees to a preview. Taking the snapshot again here would let
    the workspace move between the two and be restored to something nobody
    was shown -- the identity `agent_uninstall` settled for the same reason."""
    box, store = project(), Store()
    try:
        snapshot = C.commit(C.capture(label="a turn"))
        write(box, "keep.py", b"one\n")
        plan = C.plan(snapshot)
        assert plan.overwrite == ["keep.py"]
        # The tree moves again after the preview was taken.
        write(box, "docs/notes.md", b"moved after the preview\n")
        outcome = C.restore(plan)
        assert outcome.restored == ["keep.py"], outcome.restored
        # Untouched, because it was not in the plan the user read.
        assert (box.path / "docs/notes.md").read_bytes() == b"moved after the preview\n"
        assert outcome.snapshot is snapshot
    finally:
        store.close()
        box.close()


def test_a_directory_created_during_the_turn_is_emptied_and_left_standing():
    """Said rather than fixed. The walk yields files, so TMT cannot tell a
    directory created during the turn from one that was already there and
    already empty -- and removing the second kind would be the restore
    destroying something the turn never touched."""
    box, store = project(), Store()
    try:
        snapshot = C.commit(C.capture(label="a turn"))
        write(box, "made/up/file.py", b"new\n")
        outcome = C.restore(C.plan(snapshot))
        assert outcome.deleted == ["made/up/file.py"], outcome.deleted
        assert (box.path / "made" / "up").is_dir()
        assert C.DIRECTORY_CAVEAT in outcome.notes
    finally:
        store.close()
        box.close()


# --- what is refused -------------------------------------------------------

def test_there_is_nothing_to_undo_is_a_sentence_and_not_an_empty_plan():
    assert C.refusal(None) == C._NOTHING
    plan = C.plan(None)
    assert plan.refusal == C._NOTHING
    assert plan.touches() == [], "a refused plan must name no paths at all"


def test_an_incomplete_snapshot_is_refused_with_the_reason_in_it():
    """Half a workspace put back and called undone is the failure this whole
    module exists to make impossible."""
    box, store = project(), Store()
    try:
        snapshot = C.Snapshot(id="0007", complete=False,
                              reason="the workspace has more than 5000 files")
        held = C.refusal(snapshot)
        assert "0007" in held and "more than 5000 files" in held, held
        assert C.plan(snapshot).touches() == []
    finally:
        store.close()
        box.close()


def test_an_oversize_file_that_moved_refuses_the_restore_and_names_it():
    """TMT has its size and not its contents, so putting the rest back would
    leave that one file at its new contents and say the workspace was
    restored."""
    box, store = project(), Store()
    try:
        big = box.path / "huge.bin"
        big.write_bytes(b"x" * (C.MAX_BLOB_BYTES + 10))
        snapshot = C.commit(C.capture(label="a turn"))
        assert "huge.bin" in snapshot.oversize, snapshot.oversize
        assert "huge.bin" not in snapshot.files, "it must not have been stored"
        assert C.refusal(snapshot) == "", "unchanged, so there is nothing wrong"
        # Moved. mtime alone can be too coarse to differ within one test, so
        # the size is changed too -- which is what a rewritten large file does.
        big.write_bytes(b"y" * (C.MAX_BLOB_BYTES + 20))
        held = C.refusal(snapshot)
        assert "huge.bin" in held and "0001" in held, held
        assert C.plan(snapshot).refusal == held
    finally:
        store.close()
        box.close()


def test_an_oversize_file_that_is_gone_refuses_the_restore_and_names_it():
    box, store = project(), Store()
    try:
        big = box.path / "huge.bin"
        big.write_bytes(b"x" * (C.MAX_BLOB_BYTES + 10))
        snapshot = C.commit(C.capture(label="a turn"))
        big.unlink()
        held = C.refusal(snapshot)
        assert "huge.bin" in held and "never had its contents" in held, held
    finally:
        store.close()
        box.close()


def test_refusal_fails_closed_when_it_cannot_answer_at_all():
    """The one guard shape that matters here. Every completion gate in TMT
    swallows and returns "" because the worst outcome there is finished work
    held hostage; the worst outcome HERE is a workspace half rewritten."""

    class Hostile:
        complete = True
        id = "0001"

        @property
        def oversize(self):
            raise RuntimeError("boom")

    held = C.refusal(Hostile())
    assert held and "will not restore" in held, held
    assert "RuntimeError" in held and "boom" in held, held


def test_restoring_a_refused_plan_raises_rather_than_doing_part_of_it():
    box, store = project(), Store()
    try:
        was = (box.path / "keep.py").read_bytes()
        plan = C.RestorePlan(C.Snapshot(id="0001"), overwrite=["keep.py"],
                             refusal="nope")
        try:
            C.restore(plan)
        except C.CheckpointError as error:
            assert "nope" in str(error), error
        else:
            raise AssertionError("a refused plan must not be carried out")
        assert (box.path / "keep.py").read_bytes() == was
    finally:
        store.close()
        box.close()


# --- the store -------------------------------------------------------------

def test_the_store_lives_beside_the_install_and_never_in_the_workspace():
    """The rule `.tmt_index/` and `.tmt_memory/` already keep, and the one
    that matters most here: when TMT is run ON TMT the install and the
    workspace are the same directory, so a store that followed the workspace
    would be committed -- full of copies of the repository."""
    box, store = project(), Store()
    try:
        C.commit(C.capture(label="a turn"))
        assert store.manifests(), "nothing was written at all"
        assert C.workspace_dir().is_relative_to(store.path) \
            if hasattr(Path, "is_relative_to") else True
        assert store.path in C.workspace_dir().parents
        assert box.path not in C.workspace_dir().parents
        assert not list(box.path.glob("**/.tmt_checkpoints"))
    finally:
        store.close()
        box.close()


def test_two_workspaces_never_share_a_history():
    """Keyed by a hash of the absolute path, because a path is not a filename
    on any platform and two projects called `src` must not see each other's
    files."""
    store = Store()
    first, second = project(), None
    try:
        one = C.workspace_dir()
        second = project()
        two = C.workspace_dir()
        assert one != two
        assert one.parent == two.parent == store.path
    finally:
        store.close()
        first.close()
        if second is not None:
            second.close()


def test_identical_contents_are_stored_once():
    """Content addressing is what makes a snapshot per turn affordable: the
    second one stores only what actually changed."""
    box, store = project({"a.py": "same\n", "b.py": "same\n",
                          "c.py": "different\n"}), Store()
    try:
        C.commit(C.capture(label="one"))
        assert len(store.blobs()) == 2, store.blobs()
        # A second turn that changed nothing adds no blobs at all.
        C.commit(C.capture(label="two"))
        assert len(store.blobs()) == 2, store.blobs()
    finally:
        store.close()
        box.close()


def test_ids_count_up_past_what_pruning_has_taken_away():
    """One past the highest on disk rather than a count of what is there, so
    pruning the oldest never hands a new checkpoint a number the user has
    already seen this session."""
    box, store = project(), Store()
    try:
        for _ in range(3):
            C.commit(C.capture(label="a turn"))
        assert [s.id for s in C.history()] == ["0003", "0002", "0001"]
        (C.workspace_dir() / "turns" / "0001.json").unlink()
        assert C.commit(C.capture(label="another")).id == "0004"
    finally:
        store.close()
        box.close()


def test_pruning_keeps_the_newest_and_drops_the_blobs_nothing_names():
    box, store = project({"a.py": "0\n"}), Store()
    try:
        previous = C.MAX_TURNS
        C.MAX_TURNS = 3
        try:
            for step in range(5):
                write(box, "a.py", ("body %d\n" % step).encode())
                C.commit(C.capture(label="turn %d" % step))
            kept = C.history()
            assert [s.id for s in kept] == ["0005", "0004", "0003"], kept
            # Exactly the blobs the survivors name, and nothing the dropped
            # manifests were the only reference to.
            live = set()
            for snapshot in kept:
                live.update(snapshot.files.values())
            assert set(store.blobs()) == live, (store.blobs(), live)
        finally:
            C.MAX_TURNS = previous
    finally:
        store.close()
        box.close()


def test_a_manifest_from_another_format_is_ignored_rather_than_guessed_at():
    """The rule `agent_memory` keeps: a store read under the wrong assumption
    is worse than a store that is not read."""
    box, store = project(), Store()
    try:
        snapshot = C.commit(C.capture(label="a turn"))
        path = C.workspace_dir() / "turns" / ("%s.json" % snapshot.id)
        body = json.loads(path.read_text("utf-8"))
        body["format"] = C.FORMAT + 1
        path.write_text(json.dumps(body), encoding="utf-8")
        assert C.history() == []
        assert C.find() is None
    finally:
        store.close()
        box.close()


def test_a_damaged_manifest_does_not_take_the_others_off_the_screen():
    box, store = project(), Store()
    try:
        C.commit(C.capture(label="one"))
        good = C.commit(C.capture(label="two"))
        (C.workspace_dir() / "turns" / "0001.json").write_text("{not json",
                                                               encoding="utf-8")
        known = C.history()
        assert [s.id for s in known] == [good.id], known
    finally:
        store.close()
        box.close()


def test_find_takes_an_id_with_or_without_its_leading_zeros():
    box, store = project(), Store()
    try:
        C.commit(C.capture(label="one"))
        second = C.commit(C.capture(label="two"))
        assert C.find().id == second.id, "no id means the newest"
        assert C.find("0001").id == "0001"
        assert C.find("1").id == "0001"
        assert C.find("9999") is None
    finally:
        store.close()
        box.close()


# --- the question the loop asks --------------------------------------------

def test_will_mutate_names_the_verbs_that_write():
    for action in sorted(agent_config.MUTATING_ACTIONS):
        assert C.will_mutate(action, {}), action
    for action in ("read_file", "grep", "glob", "tree", "code_map",
                   "git_diff", "end_conversation", "send_message", ""):
        assert not C.will_mutate(action, {}), action


def test_a_multi_tool_is_judged_on_the_calls_it_was_asked_for():
    """`TMT.mutated` reads `agent_multi`'s record of what RAN, and this is
    asked before dispatch, when there is no record yet. Reading the record
    here would answer no for every multi_tool there has ever been."""
    reads = {"action": "multi_tool",
             "calls": [{"action": "read_file", "path": "a"},
                       {"action": "grep", "query": "x"}]}
    writes = {"action": "multi_tool",
              "calls": [{"action": "read_file", "path": "a"},
                        {"action": "write_file", "path": "b", "content": "c"}]}
    assert not C.will_mutate("multi_tool", reads)
    assert C.will_mutate("multi_tool", writes)
    assert not C.will_mutate("multi_tool", {"calls": []})
    assert not C.will_mutate("multi_tool", {})


def test_a_delegation_counts_as_a_mutation_unless_its_contract_forbids_writing():
    """A worker writes on its own thread through the same dispatcher, and
    nothing in the loop sees those writes coming -- so the moment before the
    worker starts is the only one that covers them."""
    assert C.will_mutate("spawn_agent", {"task": "fix it"})
    assert C.will_mutate("spawn_agent",
                         {"task": "fix it", "constraints": {"timeout_seconds": 60}})
    assert not C.will_mutate("spawn_agent",
                             {"task": "look", "constraints": {"read_only": True}})


# --- the session-lived keeper ----------------------------------------------

def test_a_turn_that_only_reads_costs_nothing_at_all():
    """Most turns change nothing, and walking the tree for those would spend
    a third of a second and seventy megabytes of reads on every question."""
    box, store = project(), Store()
    try:
        keeper = C.Checkpointer()
        keeper.begin("what does this project do?")
        for action in ("read_file", "grep", "tree", "end_conversation"):
            assert keeper.before(action, {"path": "keep.py"}) == ""
        assert not keeper.taken()
        assert keeper.commit() is None
        assert store.manifests() == [], store.manifests()
        assert store.blobs() == [], "nothing should have been read at all"
    finally:
        store.close()
        box.close()


def test_one_snapshot_per_turn_however_many_files_the_turn_writes():
    """The snapshot is of the whole workspace, so the picture taken before the
    first write already contains the fortieth. A checkpoint per action would
    also turn one multi_tool of thirty edits into thirty rows to read."""
    box, store = project(), Store()
    try:
        keeper = C.Checkpointer()
        keeper.begin("rewrite everything")
        for name in ("keep.py", "gone.py", "sub/deep.txt"):
            keeper.before("write_file", {"path": name})
            write(box, name, b"changed\n")
        keeper.commit()
        assert store.manifests() == ["0001.json"], store.manifests()
        plan = C.plan(C.find())
        assert len(plan.overwrite) == 3, plan.overwrite
    finally:
        store.close()
        box.close()


def test_the_commands_a_turn_ran_are_on_the_manifest_and_in_the_report():
    """What is kept is that a command RAN and what it was, never anything
    about what it did -- reading a program's output to decide what happened is
    the rule this codebase refuses, and it would be at its worst here."""
    box, store = project(), Store()
    try:
        keeper = C.Checkpointer()
        keeper.begin("build it")
        keeper.before("bash", {"command": "make  all"})
        write(box, "keep.py", b"built\n")
        keeper.before("bash", {"command": "make  all"})       # same, once
        keeper.before("bash", {"command": "npm run build"})
        snapshot = keeper.commit()
        assert snapshot.commands == ["make all", "npm run build"], snapshot.commands
        outcome = C.restore(C.plan(snapshot))
        assert any("make all" in note for note in outcome.notes), outcome.notes
        assert any("outside the workspace" in note for note in outcome.notes)
    finally:
        store.close()
        box.close()


def test_a_turn_that_ran_no_command_is_not_warned_about_one():
    box, store = project(), Store()
    try:
        keeper = C.Checkpointer()
        keeper.begin("edit it")
        keeper.before("write_file", {"path": "keep.py"})
        write(box, "keep.py", b"edited\n")
        outcome = C.restore(C.plan(keeper.commit()))
        assert not any("command ran" in note for note in outcome.notes), outcome.notes
    finally:
        store.close()
        box.close()


def test_a_workspace_too_large_is_said_once_and_no_half_snapshot_is_kept():
    """A list where half the rows say "cannot be restored" teaches the user
    that /undo does not work, which is worse than saying so once and offering
    nothing."""
    box, store = project({"f%03d.py" % n: "x\n" for n in range(12)}), Store()
    try:
        previous = C.MAX_SNAPSHOT_FILES
        C.MAX_SNAPSHOT_FILES = 4
        try:
            keeper = C.Checkpointer()
            keeper.begin("a big one")
            said = keeper.before("write_file", {"path": "f000.py"})
            assert said.startswith("This workspace is too large"), said
            assert "more than 4 files" in said, said
            # Once. A sentence at the top of every turn is one nobody reads.
            assert keeper.before("write_file", {"path": "f001.py"}) == ""
            assert keeper.commit() is None
            assert store.manifests() == [], store.manifests()
        finally:
            C.MAX_SNAPSHOT_FILES = previous
    finally:
        store.close()
        box.close()


def test_a_new_turn_forgets_the_last_turn_s_commands_and_label():
    box, store = project(), Store()
    try:
        keeper = C.Checkpointer()
        keeper.begin("first")
        keeper.before("bash", {"command": "make"})
        write(box, "keep.py", b"one\n")
        keeper.commit()
        keeper.begin("second")
        keeper.before("write_file", {"path": "keep.py"})
        write(box, "keep.py", b"two\n")
        second = keeper.commit()
        assert second.commands == [], second.commands
        assert second.label == "second", second.label
    finally:
        store.close()
        box.close()


def test_nothing_the_keeper_does_can_raise_out_of_a_turn():
    """It sits on the hot path of every action dispatch. A checkpoint that
    fails must cost the user their undo and never their turn."""
    box, store = project(), Store()
    try:
        keeper = C.Checkpointer()
        keeper.begin("a turn")
        agent_config.CHECKPOINT_DIR = box.path / "nope" / "\0bad"
        assert keeper.before("write_file", {"path": "keep.py"}) == ""
        assert keeper.commit() is None
        # And a hostile object handed to it is answered rather than raised at.
        assert keeper.before("write_file", None) == ""
        assert keeper.before(None, None) == ""
    finally:
        store.close()
        box.close()


def test_the_keeper_can_be_switched_off_entirely():
    box, store = project(), Store()
    try:
        keeper = C.Checkpointer(enabled=False)
        keeper.begin("a turn")
        assert keeper.before("write_file", {"path": "keep.py"}) == ""
        assert keeper.commit() is None
        assert store.blobs() == []
    finally:
        store.close()
        box.close()


# --- the label -------------------------------------------------------------

def test_a_long_task_is_cut_at_a_clause_and_not_at_a_character_count():
    """`agent_context` learned the other half of this the expensive way: the
    first progress file it wrote carried 160 characters of a 700-character
    instruction sawn through mid-word, which answered none of the questions
    the file existed to answer."""
    task = ("Everything for this change is already staged, so commit exactly "
            "what is staged and then push to main; do not name any paths "
            "yourself and do not build a path list, because git_commit will "
            "otherwise miss files.")
    label = C.label_for(task)
    assert len(label) <= C.MAX_LABEL_CHARS, label
    assert label.endswith("push to main"), label
    assert "..." not in label


def test_a_long_task_with_no_clause_in_it_is_cut_and_says_so():
    label = C.label_for("word " * 60)
    assert len(label) <= C.MAX_LABEL_CHARS + 3, label
    assert label.endswith("..."), label


def test_a_short_task_is_left_exactly_as_it_was_written():
    assert C.label_for("fix the parser") == "fix the parser"
    assert C.label_for("  fix   the\nparser ") == "fix the parser"
    assert C.label_for(None) == ""


def test_a_snapshot_round_trips_through_its_own_json():
    snapshot = C.Snapshot(id="0003", label="a turn", files={"a.py": "abc"},
                          oversize={"big.bin": [10, 20]}, commands=["make"])
    again = C.Snapshot.from_json(json.loads(json.dumps(snapshot.to_json())))
    assert again.id == "0003" and again.label == "a turn"
    assert again.files == {"a.py": "abc"}
    assert again.oversize == {"big.bin": [10, 20]}
    assert again.commands == ["make"]
    assert C.Snapshot.from_json({"format": C.FORMAT + 1}) is None
    assert C.Snapshot.from_json("not an object") is None


# --- what a walk must not follow -------------------------------------------

def test_a_symlink_pointing_out_of_the_workspace_is_never_captured():
    """A file symlink is READ where it points, so one pointing out of the
    workspace would be captured here and WRITTEN BACK there by a restore.
    `within_workspace` is the containment test with the refusal taken off,
    which is exactly the question an entry a walk arrived at asks."""
    box, store = project(), Store()
    outside = Path(tempfile.mkdtemp(prefix="tmt_out_")).resolve()
    try:
        (outside / "secret.txt").write_bytes(b"not yours\n")
        try:
            os.symlink(str(outside / "secret.txt"), str(box.path / "link.txt"))
        except (OSError, NotImplementedError, AttributeError):
            return   # Windows without the privilege; nothing to assert
        snapshot = C.capture(label="a turn")
        assert "link.txt" not in snapshot.files, snapshot.files
        assert "link.txt" not in snapshot.oversize
        assert (outside / "secret.txt").read_bytes() == b"not yours\n"
    finally:
        remove_tree(outside)
        store.close()
        box.close()


def test_a_capture_that_hit_a_ceiling_leaves_no_blobs_behind_it():
    """A capture writes blobs as it reads, so one that hits a ceiling has
    already stored everything up to that point -- and its snapshot is never
    committed, so nothing would ever name them. The sweep runs on every
    commit rather than only when a manifest was dropped."""
    box, store = project({"f%03d.py" % n: "body %d\n" % n for n in range(12)}), Store()
    try:
        previous = C.MAX_SNAPSHOT_FILES
        C.MAX_SNAPSHOT_FILES = 4
        try:
            failed = C.capture(label="too big")
            assert not failed.complete
            assert store.blobs(), "the orphans this test is about were not made"
        finally:
            C.MAX_SNAPSHOT_FILES = previous
        kept = C.commit(C.capture(label="fits now"))
        assert set(store.blobs()) == set(kept.files.values()), store.blobs()
    finally:
        store.close()
        box.close()


def test_the_store_is_never_captured_into_a_snapshot_of_itself():
    """Not hypothetical. When TMT is run ON TMT the install and the workspace
    are the same directory, so the store sits inside the tree being walked --
    and every capture would copy every blob the captures before it wrote,
    squaring the store once per turn. It is the collision `.tmt_index/` and
    `.tmt_memory/` already record, arriving where it costs the most."""
    box = project()
    previous = agent_config.CHECKPOINT_DIR
    try:
        # The store INSIDE the workspace, which is what running TMT on TMT
        # does, rather than the tidy temporary directory `Store` sets up.
        agent_config.CHECKPOINT_DIR = box.path / ".tmt_checkpoints"
        first = C.commit(C.capture(label="one"))
        assert first.files, "nothing was captured at all"
        assert not any(name.startswith(".tmt_checkpoints") for name in first.files), \
            first.files
        write(box, "keep.py", b"changed\n")
        second = C.commit(C.capture(label="two"))
        assert not any(name.startswith(".tmt_checkpoints") for name in second.files), \
            second.files
        # The second snapshot names the same files as the first, plus nothing.
        assert set(second.files) == set(first.files), (
            set(second.files) ^ set(first.files))
    finally:
        agent_config.CHECKPOINT_DIR = previous
        box.close()
