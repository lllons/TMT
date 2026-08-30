"""Tests for per-project memory.

Two things are being defended here and they are not the same thing. One is that
a note written in one session is still there in the next, in the right
project's notebook and nowhere near the user's code. The other is that a
credential can never reach that notebook, because unlike everything else TMT
handles, what lands there outlives the process.

Every test redirects agent_config.INSTALL_DIR as well as ROOT_DIR. Redirecting
only the workspace would leave these writing into the developer's own real
memory file, which is both a wrong test and a rude one.
"""

import importlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import agent_config
import agent_memory

INSTALL_DIR = Path(agent_config.__file__).resolve().parent


class Sandbox:
    """A throwaway workspace and a throwaway install directory.

    close() restores both and must run in a finally: a leaked INSTALL_DIR would
    point every later test -- and every later manual run in this interpreter --
    at a deleted directory, and a leaked ROOT_DIR does the same to the
    workspace.
    """

    def __init__(self):
        self.previous_root = agent_config.ROOT_DIR
        self.previous_install = agent_config.INSTALL_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_mem_ws_")).resolve()
        self.install = Path(tempfile.mkdtemp(prefix="tmt_mem_install_")).resolve()
        agent_config.INSTALL_DIR = self.install
        agent_config.set_workspace_root(self.path)

    def workspace_contents(self):
        return sorted(str(p.relative_to(self.path)).replace("\\", "/")
                      for p in self.path.rglob("*"))

    def raw(self):
        """The memory file exactly as it sits on disk, or ""."""
        try:
            return agent_memory.memory_path().read_text(encoding="utf-8")
        except OSError:
            return ""

    def close(self):
        agent_config.ROOT_DIR = self.previous_root
        agent_config.INSTALL_DIR = self.previous_install
        shutil.rmtree(self.path, ignore_errors=True)
        shutil.rmtree(self.install, ignore_errors=True)


# --- storing and recalling --------------------------------------------------

def test_a_note_is_stored_and_recalled():
    box = Sandbox()
    try:
        report = agent_memory.remember("The tests are run with run_tests.py.",
                                       tags=["tests"])
        assert "Remembered" in report, report
        recalled = agent_memory.recall()
        assert "run_tests.py" in recalled, recalled
        assert "tests" in recalled, recalled
        assert "1 stored" in recalled, recalled
    finally:
        box.close()


def test_memory_survives_the_module_being_discarded_and_reimported():
    """The whole point. A note is only memory if it is still there after the
    process that wrote it has gone, so the module is thrown out of sys.modules
    and imported again rather than merely called twice."""
    box = Sandbox()
    try:
        agent_memory.remember("agent_ui.py is the only place width is defined.")
        del sys.modules["agent_memory"]
        fresh = importlib.import_module("agent_memory")
        assert fresh is not agent_memory or True   # identity is not the point
        recalled = fresh.recall()
        assert "only place width is defined" in recalled, recalled
    finally:
        sys.modules["agent_memory"] = agent_memory
        box.close()


def test_two_workspaces_do_not_see_each_others_notes():
    """One notebook per project. A note about one repository surfacing while
    TMT works on another is worse than no memory at all: it is confident and
    wrong."""
    first = Sandbox()
    try:
        agent_memory.remember("first project uses tabs")
        first_file = agent_memory.memory_path()
    finally:
        pass
    second_path = Path(tempfile.mkdtemp(prefix="tmt_mem_ws2_")).resolve()
    try:
        agent_config.set_workspace_root(second_path)
        second_file = agent_memory.memory_path()
        assert second_file != first_file, second_file
        assert "first project uses tabs" not in agent_memory.recall()
        agent_memory.remember("second project uses spaces")
        assert "spaces" in agent_memory.recall()

        agent_config.set_workspace_root(first.path)
        back = agent_memory.recall()
        assert "tabs" in back and "spaces" not in back, back
    finally:
        shutil.rmtree(second_path, ignore_errors=True)
        first.close()


def test_nothing_is_written_inside_the_workspace():
    """TMT's own state never lands in the project it is working on. A memory
    file in the workspace would appear in the user's git status and eventually
    be committed as if it were their code."""
    box = Sandbox()
    try:
        before = box.workspace_contents()
        agent_memory.remember("a note", tags=["x"])
        agent_memory.recall()
        agent_memory.forget("m1")
        assert box.workspace_contents() == before == [], box.workspace_contents()
        stored = agent_memory.memory_path().resolve()
        assert box.install in stored.parents, stored
        assert box.path not in stored.parents, stored
    finally:
        box.close()


def test_the_memory_file_lives_under_the_install_dir_and_names_its_workspace():
    box = Sandbox()
    try:
        agent_memory.remember("keyed by the resolved workspace path")
        assert agent_memory.memory_dir().resolve().parent == box.install
        data = json.loads(box.raw())
        # The key is a hash so it can be a filename; the readable path is kept
        # inside so a human can still tell whose notebook this is.
        assert str(box.path) == data["workspace"], data["workspace"]
    finally:
        box.close()


# --- filtering --------------------------------------------------------------

def test_a_query_filters_over_notes_and_tags():
    box = Sandbox()
    try:
        agent_memory.remember("the streaming parser lives in agent_model")
        agent_memory.remember("the gradient belongs to instruments", tags=["design"])
        hits = agent_memory.recall("streaming")
        assert "agent_model" in hits and "gradient" not in hits, hits
        by_tag = agent_memory.recall("DESIGN")      # case-insensitive
        assert "gradient" in by_tag and "agent_model" not in by_tag, by_tag
        empty = agent_memory.recall("nothing here matches this")
        assert "Nothing matched" in empty, empty
        assert "2 stored" in empty, empty
    finally:
        box.close()


def test_kind_filters_independently_of_the_query():
    box = Sandbox()
    try:
        agent_memory.remember("run PYTHONIOENCODING=utf-8", kind="command")
        agent_memory.remember("heredocs corrupt source here", kind="hazard")
        only = agent_memory.recall(kind="hazard")
        assert "heredocs" in only, only
        assert "PYTHONIOENCODING" not in only, only
        assert "2 stored" in only, only
    finally:
        box.close()


def test_limit_is_respected_and_the_header_reports_both_numbers():
    box = Sandbox()
    try:
        for index in range(5):
            agent_memory.remember("note number %d" % index)
        shown = agent_memory.recall(limit=2)
        assert "5 stored" in shown, shown
        assert "2 shown" in shown, shown
        # Newest first, so the last two written are the ones present.
        assert "note number 4" in shown and "note number 3" in shown, shown
        assert "note number 0" not in shown, shown
    finally:
        box.close()


# --- bounds -----------------------------------------------------------------

def test_the_cap_drops_the_oldest_and_says_so():
    """Silently dropping a note the user watched TMT take would make the
    notebook lie about what it holds, so the drop is reported."""
    box = Sandbox()
    real_cap = agent_memory.MAX_ENTRIES
    try:
        agent_memory.MAX_ENTRIES = 3
        for index in range(3):
            report = agent_memory.remember("note %d" % index)
            assert "dropped" not in report, report
        report = agent_memory.remember("note 3")
        assert "dropped" in report, report
        assert "3-entry limit" in report, report
        stored = json.loads(box.raw())["entries"]
        assert len(stored) == 3, stored
        assert [e["note"] for e in stored] == ["note 1", "note 2", "note 3"], stored
        # The survivors keep the ids and timestamps they were given: appending
        # must never renumber what is already there, or forget() stops working.
        assert [e["id"] for e in stored] == ["m2", "m3", "m4"], stored
    finally:
        agent_memory.MAX_ENTRIES = real_cap
        box.close()


def test_appending_does_not_disturb_earlier_ids_or_timestamps():
    box = Sandbox()
    try:
        agent_memory.remember("first")
        before = json.loads(box.raw())["entries"][0]
        agent_memory.remember("second")
        after = json.loads(box.raw())["entries"][0]
        assert before == after, (before, after)
    finally:
        box.close()


def test_a_corrupt_file_is_discarded_rather_than_raising():
    """A notebook must never be able to stop a session starting. Every kind of
    damage recovers the same way -- begin empty, which is the state a
    first-ever session is already known to work in."""
    box = Sandbox()
    try:
        agent_memory.remember("this will be destroyed")
        path = agent_memory.memory_path()
        for damage in ("{ not json at all", "", "[]", '{"version": 999}',
                       '{"version": 1, "entries": "not a list"}'):
            path.write_text(damage, encoding="utf-8")
            recalled = agent_memory.recall()
            assert "Nothing remembered" in recalled, (damage, recalled)
            assert agent_memory.forget("m1").startswith("Nothing forgotten"), damage
            report = agent_memory.remember("and rebuilt from nothing")
            assert "Remembered" in report, (damage, report)
            assert "and rebuilt" in agent_memory.recall(), damage
    finally:
        box.close()


def test_a_single_damaged_entry_does_not_condemn_the_whole_file():
    box = Sandbox()
    try:
        agent_memory.remember("good note")
        data = json.loads(box.raw())
        data["entries"].append({"id": "m9"})        # missing everything else
        agent_memory.memory_path().write_text(json.dumps(data), encoding="utf-8")
        recalled = agent_memory.recall()
        assert "good note" in recalled, recalled
        assert "1 stored" in recalled, recalled
    finally:
        box.close()


# --- forgetting -------------------------------------------------------------

def test_forget_removes_exactly_one_and_never_claims_a_removal_it_did_not_make():
    box = Sandbox()
    try:
        agent_memory.remember("keep me")
        agent_memory.remember("remove me")
        agent_memory.remember("keep me too")
        report = agent_memory.forget("m2")
        assert "Forgot [m2]" in report, report
        assert "2 stored" in report, report
        left = agent_memory.recall()
        assert "remove me" not in left, left
        assert "keep me" in left and "keep me too" in left, left

        missed = agent_memory.forget("m2")
        assert missed.startswith("Nothing forgotten"), missed
        assert "2 stored" in missed, missed
        assert agent_memory.forget("").startswith("Nothing to forget"), "empty id"
        assert len(json.loads(box.raw())["entries"]) == 2
    finally:
        box.close()


# --- timestamps -------------------------------------------------------------

def test_timestamps_are_iso_8601_utc_and_never_relative():
    box = Sandbox()
    try:
        agent_memory.remember("timed")
        stamp = json.loads(box.raw())["entries"][0]["timestamp"]
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", stamp), stamp
        parsed = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        drift = abs((datetime.now(timezone.utc) - parsed).total_seconds())
        assert drift < 300, (stamp, drift)
        assert "ago" not in agent_memory.recall()
    finally:
        box.close()


# --- secret safety ----------------------------------------------------------

def test_an_api_key_shaped_string_never_reaches_the_disk():
    """Read off disk, not from the return value. The return value is what the
    code claims it did; the file is what it did."""
    box = Sandbox()
    try:
        secret = "sk-or-v1-" + "a1b2c3d4" * 8
        report = agent_memory.remember(
            "The OpenRouter key for this project is %s, keep it handy." % secret)
        raw = box.raw()
        assert secret not in raw, raw
        assert "a1b2c3d4a1b2c3d4" not in raw, raw
        assert "[redacted]" in raw, raw
        assert "Redacted" in report, report
        # The surrounding sentence is the useful part and survives.
        assert "OpenRouter" in raw, raw
    finally:
        box.close()


def test_other_provider_key_shapes_are_caught_too():
    box = Sandbox()
    try:
        for secret in ("AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q",
                       "ghp_" + "A1b2C3d4E5f6G7h8I9j0",
                       "AKIA" + "ABCDEFGHIJKLMNOP",
                       "deadbeef" * 6):
            agent_memory.remember("found this lying about: %s" % secret)
            assert secret not in box.raw(), secret
    finally:
        box.close()


def test_an_env_style_password_line_is_caught():
    box = Sandbox()
    try:
        report = agent_memory.remember(
            "The .env has PASSWORD=hunter2correcthorse and DB_HOST=localhost.")
        raw = box.raw()
        assert "hunter2correcthorse" not in raw, raw
        assert "PASSWORD" in raw, raw          # the fact survives, the value does not
        assert "Redacted" in report, report
        # An ordinary setting on the same line is not collateral damage.
        assert "localhost" in raw, raw
    finally:
        box.close()


def test_a_note_that_is_nothing_but_a_secret_is_refused_outright():
    box = Sandbox()
    try:
        report = agent_memory.remember("sk-" + "z9y8x7w6" * 6)
        assert report.startswith("Refused"), report
        assert box.raw() == "", box.raw()
        assert "Nothing remembered" in agent_memory.recall()
    finally:
        box.close()


def test_an_ordinary_note_containing_the_word_key_is_not_refused():
    """The check has to be able to tell a credential from English. A scanner
    that redacts every sentence with "key" in it makes the notebook useless,
    which is the failure that turns the feature off in practice."""
    box = Sandbox()
    try:
        ordinary = [
            "The key insight is that agent_ui.py owns every width and colour.",
            "Memory is keyed by the resolved workspace path, so projects stay apart.",
            "Note the important detail: agent_config.INSTALL_DIR is read at call time.",
            "Press the escape key to cancel, and check the auth flow in the menu.",
            "The token budget is reported by the corner meter with a leading tilde.",
        ]
        for note in ordinary:
            report = agent_memory.remember(note)
            assert "Remembered" in report, (note, report)
            assert "Redacted" not in report, (note, report)
        raw = box.raw()
        assert "[redacted]" not in raw, raw
        for note in ordinary:
            assert note in agent_memory.recall(limit=len(ordinary)), note
    finally:
        box.close()


def test_an_author_field_is_not_mistaken_for_an_auth_field():
    """"auth" is a substring of "author". Matching the name by substring is the
    obvious implementation and it silently eats real information."""
    box = Sandbox()
    try:
        agent_memory.remember("author=julian.lonsdale and monkeypatching=allowed")
        raw = box.raw()
        assert "julian.lonsdale" in raw, raw
        assert "[redacted]" not in raw, raw
    finally:
        box.close()


# --- housekeeping -----------------------------------------------------------

def test_an_empty_note_is_refused_and_writes_nothing():
    box = Sandbox()
    try:
        assert agent_memory.remember("   ").startswith("Nothing to remember")
        assert box.raw() == "", box.raw()
    finally:
        box.close()


def test_a_note_longer_than_the_cap_is_truncated_and_says_so():
    box = Sandbox()
    try:
        report = agent_memory.remember("x" * (agent_memory.MAX_NOTE_CHARS + 500))
        assert "Truncated" in report, report
        note = json.loads(box.raw())["entries"][0]["note"]
        assert len(note) == agent_memory.MAX_NOTE_CHARS, len(note)
    finally:
        box.close()


def test_importing_agent_memory_creates_no_directory():
    """Import must not touch the disk, the same rule agent_config keeps. The
    memory directory appears on the first save and not before."""
    box = Sandbox()
    try:
        assert not agent_memory.memory_dir().exists()
        agent_memory.recall()
        agent_memory.forget("m1")
        assert not agent_memory.memory_dir().exists(), os.listdir(box.install)
        agent_memory.remember("now it exists")
        assert agent_memory.memory_dir().exists()
    finally:
        box.close()
