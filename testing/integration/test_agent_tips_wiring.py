"""The tip, driven through the loop that actually draws it.

`test_agent_tips` proves the catalogue and the rotation; this file proves the
two of them are connected to a screen. That seam is where the feature can be
silently absent -- a renderer that accepts a tip nobody passes it draws a
header identical to the one it drew before, and every unit test still passes.

Three things are asserted here and nowhere else:

  A session draws one.  `TMT.main` reaches the header through its own code
      path, not through a hand-composed frame, so the row on screen is the row
      a user gets.

  The next one differs.  Two sessions in a row, and the second says something
      else -- which is the whole of what was asked for, and it works only
      because the cursor survived the first session ending.

  `/back` counts as reaching the screen.  Coming back to a session redraws the
      header, so it steps the rotation too. `_return_to_menu` is driven for
      real; a test that only drove the launch would leave half the feature
      resting on a comment.

The cursor is redirected in every test that runs a session, for the reason the
unit file redirects it: the real one lives in INSTALL_DIR, which is this
repository when TMT is run on TMT.
"""

import contextlib
import io
import os
import shutil
import tempfile
from pathlib import Path

import agent_config
import agent_menu
import agent_tips
import agent_ui
import TMT

from test_agent_workspace import Workspace
from test_agent_cli import Replies


class Cursor:
    """The tip cursor, redirected into a temp directory and put back."""

    def __init__(self, value=0):
        self.previous = agent_config.TIP_FILE
        self.box = Path(tempfile.mkdtemp(prefix="tmt_tipwiring_"))
        agent_config.TIP_FILE = self.box / ".tmt_tip"
        agent_config.TIP_FILE.write_text(str(value) + "\n", encoding="utf-8")

    def stored(self):
        return agent_config.TIP_FILE.read_text(encoding="utf-8").strip()

    def close(self):
        agent_config.TIP_FILE = self.previous
        shutil.rmtree(self.box, ignore_errors=True)


def run_session(answers):
    """Drive TMT.main through `answers` turns and return everything it drew.

    The same harness `test_agent_cli` uses, kept here rather than imported so
    that file's helper stays free to change for its own reasons.
    """
    box = Workspace()
    screen = io.StringIO()
    saved = (TMT.console, TMT.ensure_api_key, TMT.run_startup,
             TMT.ensure_git_identity)
    previous_cwd = Path.cwd()
    try:
        os.chdir(str(box.path))
        TMT.console = Replies(answers)
        TMT.ensure_api_key = lambda: True
        TMT.run_startup = lambda **kwargs: "start"
        TMT.ensure_git_identity = lambda *a, **k: None
        with contextlib.redirect_stdout(screen):
            TMT.main([])
        return screen.getvalue()
    finally:
        os.chdir(str(previous_cwd))
        (TMT.console, TMT.ensure_api_key, TMT.run_startup,
         TMT.ensure_git_identity) = saved
        box.close()


def tip_rows(drawn):
    """Every tip row in what was drawn, without its label or its escapes."""
    rows = []
    for line in agent_ui.strip_ansi(drawn).splitlines():
        text = line.strip()
        if text.startswith(agent_menu.TIP_LABEL + " "):
            rows.append(text)
    return rows


def gesture_of(row):
    """The gesture a drawn tip row names, whichever tier it was drawn at.

    Longest first, because `/note` is a prefix of `/notes` and the shorter one
    matches a row belonging to the longer. Found by this file failing on a
    perfectly correct header.
    """
    text = row.split(None, 2)[-1]
    for gesture, _ in sorted(agent_tips.TIPS, key=lambda tip: -len(tip[0])):
        if text.startswith(gesture):
            return gesture
    return None


def test_a_session_opens_with_exactly_one_tip_under_its_header():
    """The header is drawn once per session, so the tip is too. Two of them
    would mean the header had been redrawn, which is the thing that pushed
    the conversation off the screen and was fixed by drawing it once."""
    cursor = Cursor(0)
    try:
        drawn = run_session(["", "", "quit"])
        rows = tip_rows(drawn)
        assert len(rows) == 1, rows
        assert gesture_of(rows[0]) is not None, rows
        # And it is the tip the cursor named, rather than whatever happened to
        # be first in the catalogue.
        assert gesture_of(rows[0]) == agent_tips.tip_at(1)[0], rows
    finally:
        cursor.close()


def test_the_next_session_says_something_else():
    """What was actually asked for: launch TMT again and the tip has moved on.

    It works only because the cursor is written to disk -- the two sessions
    share no object, and the second one's `Session`, `AgentManager` and
    prompt box are all new. This is the test that fails if the store is
    dropped for a module global.
    """
    cursor = Cursor(0)
    try:
        first = tip_rows(run_session(["quit"]))
        second = tip_rows(run_session(["quit"]))
        third = tip_rows(run_session(["quit"]))
        assert first and second and third, (first, second, third)
        assert first[0] != second[0] != third[0], (first, second, third)
        assert first[0] != third[0], (first, third)
        assert cursor.stored() == "3", cursor.stored()
    finally:
        cursor.close()


def test_the_rotation_survives_the_end_of_the_catalogue():
    """Enough launches to run out of tips, which is where a cursor that only
    ever grew would put an index past the end of the list."""
    cursor = Cursor(agent_tips.count() - 1)
    try:
        rows = [tip_rows(run_session(["quit"]))[0]
                for _ in range(3)]
        assert gesture_of(rows[0]) == agent_tips.TIPS[0][0], rows
        assert gesture_of(rows[1]) == agent_tips.TIPS[1][0], rows
        assert len(set(rows)) == 3, rows
    finally:
        cursor.close()


def test_coming_back_through_the_menu_reaches_the_screen_and_moves_the_tip():
    """`/back` redraws the header, so it is the second place the rotation
    steps. Driven through `_return_to_menu` itself rather than asserted from
    the source, because what is being checked is that the tip reaches the
    screen this function draws."""
    cursor = Cursor(4)
    try:
        screen = io.StringIO()
        saved = (agent_menu.is_interactive, TMT.run_startup)
        try:
            agent_menu.is_interactive = lambda stream=None: True
            TMT.run_startup = lambda **kwargs: "start"
            pad = agent_menu.BottomPad(0)
            with contextlib.redirect_stdout(screen):
                resumed = TMT._return_to_menu(None, None, None, pad,
                                              "C:/probe_workspace")
        finally:
            agent_menu.is_interactive, TMT.run_startup = saved
        assert resumed is True
        rows = tip_rows(screen.getvalue())
        assert len(rows) == 1, rows
        assert gesture_of(rows[0]) == agent_tips.tip_at(5)[0], rows
        assert cursor.stored() == "5", cursor.stored()
    finally:
        cursor.close()


def test_a_run_that_cannot_draw_a_menu_does_not_spend_a_tip_on_nobody():
    """`/back` on a pipe resumes without clearing or redrawing anything, so
    there is no screen for a tip to be on -- and the rotation must not step
    for one nobody saw. It falls out of the early return rather than being
    guarded separately, which is why it is worth pinning."""
    cursor = Cursor(4)
    try:
        screen = io.StringIO()
        saved = agent_menu.is_interactive
        try:
            agent_menu.is_interactive = lambda stream=None: False
            with contextlib.redirect_stdout(screen):
                resumed = TMT._return_to_menu(None, None, None,
                                              agent_menu.BottomPad(0),
                                              "C:/probe_workspace")
        finally:
            agent_menu.is_interactive = saved
        assert resumed is True
        assert screen.getvalue() == "", repr(screen.getvalue())
        assert cursor.stored() == "4", cursor.stored()
    finally:
        cursor.close()


def test_both_places_the_header_is_drawn_ask_for_a_tip():
    """There are exactly two, and a third added later without one would draw
    a header that quietly stopped teaching anything. Read out of the source
    because the alternative is a comment asking the next person to remember.
    """
    source = Path(TMT.__file__).resolve().read_text(encoding="utf-8")
    calls = [line for line in source.splitlines() if "render_status(" in line
             and "def " not in line]
    assert len(calls) == 2, calls
    assert source.count("tip=agent_tips.next_tip()") == 2, source.count(
        "tip=agent_tips.next_tip()")


def test_the_tip_never_reaches_the_model():
    """It is decoration on the terminal, not something the model is told. A
    tip in the system prompt would spend tokens on every request of every
    step teaching a model about commands only the user can type.

    Built over an empty workspace on purpose. TMT's own repository is a
    workspace like any other, and the snapshot inlines the modules in it --
    so asking this question here would be answered by `agent_tips.py`'s own
    source and would fail on a prompt that never mentioned a tip. (It did.)
    """
    cursor = Cursor(0)
    box = Workspace(git=True)
    try:
        import agent_prompt
        box.use()
        agent_prompt.invalidate_prompt()
        prompt = agent_prompt.get_system_prompt()
        for gesture, detail in agent_tips.TIPS:
            assert detail not in prompt, (gesture, detail)
        assert agent_menu.TIP_LABEL + " · " not in prompt
    finally:
        agent_prompt.invalidate_prompt()
        box.close()
        cursor.close()
