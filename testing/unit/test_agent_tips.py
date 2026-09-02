"""Tests for the header tip: the catalogue, and the rotation that moves it.

The feature is one dim row under the session header, so almost nothing here is
about it working -- that is two tests -- and almost all of it is about the two
ways it can be wrong in a way nobody notices:

  A tip that is not true.  It is advice, and the user acts on it. A tip naming
      a command that does not exist costs them a keystroke and some of their
      trust in everything else on that screen, so the commands are checked
      against the registry that answers them rather than against a list
      written out again here.

  A rotation that does not rotate.  The whole ask was that the tip changes
      each time the screen is reached, and a cursor that failed to persist
      would show tip one on every launch forever while every unit test still
      passed. The persistence is therefore driven through the real file, in a
      temporary directory, and the wrap is driven off the end of the
      catalogue.

The cursor file is redirected everywhere it is touched. The real one is
`INSTALL_DIR / ".tmt_tip"`, which is this repository when TMT is run on TMT,
and a test that wrote there would move the developer's own place in the
catalogue.
"""

import re
import shutil
import tempfile
from pathlib import Path

import agent_commands
import agent_config
import agent_tips

INSTALL_DIR = Path(agent_config.__file__).resolve().parent

# The narrowest terminal the full tip is written to fit: 80 columns, less the
# one spare column every row leaves at the right, less the header's indent.
# Anything wider shows the sentence; anything narrower degrades to the gesture,
# which is `agent_menu`'s business and is tested there.
ROOM_AT_80 = 80 - 1 - 3

# What the label costs on the front of the row: the word, a space, the
# separator glyph and a space.
LABEL_WIDTH = len("Tip") + 3


class Cursor:
    """The tip cursor, redirected into a temp directory and put back.

    `agent_config.TIP_FILE` is a module attribute and the real one lives in
    INSTALL_DIR -- the repository itself when TMT is run on TMT -- so a test
    that wrote to it would leave an untracked file in the working tree and
    move the reader's own position in the catalogue while it was at it.
    """

    def __init__(self, value=None):
        self.previous = agent_config.TIP_FILE
        self.box = Path(tempfile.mkdtemp(prefix="tmt_tipcursor_"))
        agent_config.TIP_FILE = self.box / ".tmt_tip"
        if value is not None:
            agent_config.TIP_FILE.write_text(str(value) + "\n", encoding="utf-8")

    def stored(self):
        """What is on disk now, as text, or None when nothing is."""
        try:
            return agent_config.TIP_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            return None

    def close(self):
        agent_config.TIP_FILE = self.previous
        shutil.rmtree(self.box, ignore_errors=True)


# --- the catalogue ----------------------------------------------------------

def test_there_are_enough_tips_that_a_user_does_not_see_one_twice_a_week():
    """A rotation short enough to notice is a rotation that reads as a repeat.

    Twenty-eight was the number asked for and is roughly a month of daily
    launches; the catalogue is allowed to grow past it and is not allowed to
    shrink back under it without this failing.
    """
    assert agent_tips.count() >= 28, agent_tips.count()
    assert agent_tips.count() == len(agent_tips.TIPS)


def test_every_tip_is_a_gesture_and_a_detail_and_neither_is_empty():
    """The two halves are what makes the row degrade instead of clipping. A
    tip with an empty gesture has nothing to fall back to, and one with an
    empty detail is a word on a screen with no reason given."""
    for entry in agent_tips.TIPS:
        assert isinstance(entry, tuple) and len(entry) == 2, entry
        gesture, detail = entry
        assert gesture.strip() == gesture and detail.strip() == detail, entry
        assert gesture and detail, entry


def test_no_tip_is_offered_twice():
    """Two rows saying the same thing waste two turns of a rotation whose
    whole value is that the next launch says something new."""
    gestures = [gesture for gesture, _ in agent_tips.TIPS]
    assert len(set(gestures)) == len(gestures), sorted(gestures)
    details = [detail for _, detail in agent_tips.TIPS]
    assert len(set(details)) == len(details), sorted(details)


def test_every_tip_fits_an_eighty_column_terminal_whole():
    """80 columns is the width TMT assumes when it cannot measure one, so it
    is the width the sentence has to survive at. A tip wider than this is not
    a bug that shows up as an overflow -- the row degrades and the sentence
    silently stops being shown -- which is why it is asserted rather than
    left to be noticed."""
    for gesture, detail in agent_tips.TIPS:
        width = LABEL_WIDTH + len(gesture) + 1 + len(detail)
        assert width <= ROOM_AT_80, (width, gesture, detail)


def test_no_tip_ends_in_a_full_stop():
    """The row is a label and a fragment, not prose. A full stop makes it read
    as a sentence that was cut off somewhere to the left."""
    for gesture, detail in agent_tips.TIPS:
        assert not detail.endswith("."), (gesture, detail)


def test_every_slash_command_a_tip_names_is_one_that_exists():
    """The half of "never fabricate" a machine can check.

    A tip is read as an instruction, so `/notes` being a real command and
    `/note` being a different real command both have to be true of the
    registry rather than of somebody's memory of it. The names come out of
    `agent_commands`, which is what would actually answer the user.
    """
    offered = set(agent_commands.names())
    named = set()
    for gesture, detail in agent_tips.TIPS:
        named.update(re.findall(r"/([a-z]+)", gesture + " " + detail))
    assert named, agent_tips.TIPS
    assert named <= offered, sorted(named - offered)


def test_the_tips_cover_the_commands_a_user_would_never_otherwise_find():
    """The point of the feature. A slash command is invisible until somebody
    is told about it, and these three are the ones with the most behind them:
    a plan, an independent review and a real verification are each a whole
    capability nobody reaches by guessing."""
    gestures = " ".join(gesture for gesture, _ in agent_tips.TIPS)
    for command in ("/plan", "/review", "/verify", "/note"):
        assert command in gestures, (command, gestures)


# --- the rotation -----------------------------------------------------------

def test_the_cursor_steps_by_one_and_comes_back_round():
    """Sequential rather than random: a user gets through the catalogue
    instead of meeting the same three tips all week."""
    assert agent_tips.following(0) == 1
    assert agent_tips.following(agent_tips.count() - 1) == 0
    seen = []
    position = 0
    for _ in range(agent_tips.count()):
        seen.append(agent_tips.tip_at(position))
        position = agent_tips.following(position)
    assert len(set(seen)) == agent_tips.count(), len(set(seen))
    assert position == 0, position


def test_no_index_is_refused():
    """The cursor is a number in a file a user can edit and a future version
    can write differently. Every one of them has to name a tip: an exception
    here would be raised in the middle of drawing the session header."""
    for index in (0, 5, agent_tips.count(), 10 ** 9, -1, -1000):
        assert agent_tips.tip_at(index) in agent_tips.TIPS, index
    for junk in ("", "seven", None, 3.7, object()):
        assert agent_tips.tip_at(junk) in agent_tips.TIPS, junk
        assert 0 <= agent_tips.following(junk) < agent_tips.count(), junk


# --- what survives a launch -------------------------------------------------

def test_the_tip_moves_on_and_the_move_is_written_down():
    """The whole of what was asked for: the next time this screen is reached,
    including the next time TMT is started, it says something else."""
    cursor = Cursor(0)
    try:
        first = agent_tips.next_tip()
        assert cursor.stored() == "1", cursor.stored()
        second = agent_tips.next_tip()
        assert cursor.stored() == "2", cursor.stored()
        assert first != second, (first, second)
        assert first == agent_tips.tip_at(1) and second == agent_tips.tip_at(2)
    finally:
        cursor.close()


def test_a_run_of_launches_walks_the_whole_catalogue_and_wraps():
    """Driven through the real file rather than through `following`, because
    what is being asked is whether the number that survives a launch is the
    one that comes back."""
    cursor = Cursor(0)
    try:
        seen = [agent_tips.next_tip() for _ in range(agent_tips.count())]
        assert len(set(seen)) == agent_tips.count(), len(set(seen))
        assert cursor.stored() == "0", cursor.stored()
        assert agent_tips.next_tip() == seen[0]
    finally:
        cursor.close()


def test_a_fresh_installation_starts_at_the_top_of_the_catalogue():
    """No file yet, which is every first launch. It reads as zero and the
    first thing shown is the tip after it -- not an error, and not silence."""
    cursor = Cursor()
    try:
        assert agent_config.read_saved_tip_cursor() == 0
        assert agent_tips.next_tip() == agent_tips.tip_at(1)
    finally:
        cursor.close()


def test_a_cursor_edited_into_nonsense_reads_as_the_start_and_never_raises():
    """The file is one integer in a directory a user can open. Every way of
    getting it wrong is the top of the catalogue, because a decoration must
    not be able to stop a session opening."""
    for junk in ("", "  ", "seven", "3.7", "12x", "\x00"):
        cursor = Cursor()
        try:
            agent_config.TIP_FILE.write_text(junk, encoding="utf-8")
            assert agent_config.read_saved_tip_cursor() == 0, junk
            assert agent_tips.next_tip() in agent_tips.TIPS, junk
        finally:
            cursor.close()


def test_a_cursor_that_cannot_be_stored_costs_a_repeat_and_nothing_else():
    """The one place this feature differs from every other saved setting.
    `set_auto_update` raises when a write fails, because a switch that
    silently did not persist would lie about itself; this returns None and
    shows the same tip again next time, because nothing about a tip is worth
    an exception in the middle of drawing a header."""
    cursor = Cursor(4)
    try:
        # A directory where the file should be: the write fails, the read of
        # it fails, and both are answered rather than raised.
        agent_config.TIP_FILE = cursor.box / "as_a_directory"
        agent_config.TIP_FILE.mkdir()
        assert agent_config.set_tip_cursor(9) is None
        assert agent_config.read_saved_tip_cursor() == 0
        assert agent_tips.next_tip() in agent_tips.TIPS
    finally:
        cursor.close()


def test_a_cursor_that_is_not_a_number_is_not_stored():
    """`set_tip_cursor` is the one write here, so it is the one place a
    non-number could be put in the file for the next launch to trip over."""
    cursor = Cursor(2)
    try:
        for junk in (None, "seven", object(), [1]):
            assert agent_config.set_tip_cursor(junk) is None, junk
            assert cursor.stored() == "2", (junk, cursor.stored())
        assert agent_config.set_tip_cursor("6") == 6
        assert cursor.stored() == "6"
    finally:
        cursor.close()


def test_the_cursor_belongs_to_the_installation_and_not_to_a_project():
    """It says how far through the catalogue this MACHINE has read, which is
    true of the person rather than of the directory they are standing in --
    so it lives beside the model and the effort level, and the existing test
    that no installation state follows the workspace covers it there."""
    assert Path(agent_config.TIP_FILE).resolve().parent == INSTALL_DIR
    assert Path(agent_config.TIP_FILE).name == ".tmt_tip"
