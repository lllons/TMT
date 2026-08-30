"""Tests for the AGENTS panel and the counter that rides beside it.

Two things are being protected here, and they pull in opposite directions.

The first is that the panel does what it says: it draws every agent the
manager can see, it is driven by the arrow keys, it kills what is selected, it
degrades in two named stages as the terminal narrows and it refuses below
thirty columns rather than drawing something unreadable. Every assertion about
what is on screen is made with the escapes stripped, because colour is
confirmation here and never the message.

The second is that a session with no agents in it is EXACTLY the session it
was before this existed. The prompt box and the live relay are the two
surfaces the whole interface rests on, and the cursor fix depends on two
frames of an untouched box being byte-identical. So several tests below assert
sameness rather than behaviour: a box with no manager draws the rows it always
drew, and a caption with no agents is the caption it always was.

The helpers come from test_agent_menu -- the fake terminal, the scripted key
reader and the temp-file sandbox. A second harness for the same box would
drift from the first one, and the drift would be silent.
"""

import datetime
import os
import re
import sys
from pathlib import Path

import agent_manager
import agent_panel

from test_agent_menu import (
    Console, Keys, Sandbox, Stdin, Terminal, Typing, editor, menu, visible,
)


class Clock:
    """A clock that only moves when a test says so.

    The retention window is five seconds of real elapsed time, and a test that
    proved it by sleeping would cost five seconds per assertion and still be
    measuring the machine rather than the rule. This is the pattern
    test_agent_live_renderer.drain() already uses.
    """

    def __init__(self, now=1000.0):
        self.now = float(now)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)
        return self.now


def register(count=2, clock=None):
    """A manager with `count` running workers in it, and its clock."""
    clock = Clock() if clock is None else clock
    manager = agent_manager.AgentManager(clock=clock)
    for number in range(count):
        record = manager.spawn("task number %d" % (number + 1))
        record.status = agent_manager.Status.RUNNING
        manager.set_activity(record.id, "Reading agent_ui.py")
    return manager, clock


class Screen:
    """COLUMNS and LINES pinned, and put back in close().

    `_terminal()` reads shutil.get_terminal_size, which reads those two
    variables first, and the box measures the terminal on every paint. Without
    this a test asserting what a 40-column window does would be asserting what
    the window the suite happened to run in does.
    """

    def __init__(self, columns=100, rows=24):
        self.previous = {name: os.environ.get(name)
                         for name in ("COLUMNS", "LINES")}
        os.environ["COLUMNS"] = str(columns)
        os.environ["LINES"] = str(rows)

    def close(self):
        for name, value in self.previous.items():
            os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value


def rows_of(box, state=None, size=(100, 24)):
    """The box as the terminal shows it, escapes removed."""
    return [visible(row) for row in box.lines(state if state is not None
                                              else editor(), size=size)]


def panel_text(records, width=28, **kwargs):
    """The panel column as the terminal shows it, escapes removed."""
    kwargs.setdefault("stream", Console())
    return [visible(row) for row in agent_panel.panel_rows(records, width,
                                                           **kwargs)]


# --- widths, and the two places the panel gives up ---------------------------

def test_the_panel_takes_a_share_of_the_width_inside_a_floor_and_a_ceiling():
    """Twenty-eight percent, clamped. The floor is the narrowest column a card
    still fits in and the ceiling stops a very wide window handing half the
    screen to three short cards."""
    assert agent_panel.panel_width(100) == 28
    assert agent_panel.panel_width(44) == agent_panel.PANEL_MIN == 18
    assert agent_panel.panel_width(400) == agent_panel.PANEL_MAX == 34
    assert agent_panel.panel_width(0) == agent_panel.PANEL_MIN


def test_a_wide_terminal_gets_two_columns_and_the_arithmetic_adds_up():
    """`_MIN_CONTENT` is 24 and the panel floor is 18 plus a 2 gutter, so two
    columns need 44 content columns -- 45 real ones once the spare column at
    the right has been given up. Forty-five is therefore the threshold and not
    a round number somebody liked."""
    mode, left, width = agent_panel.layout(45)
    assert mode == "two_column", mode
    assert width == agent_panel.PANEL_MIN
    assert left == menu()._MIN_CONTENT == 24
    assert left + agent_panel.GUTTER + width == 44 == 45 - 1

    mode, left, width = agent_panel.layout(100)
    assert mode == "two_column"
    assert left + agent_panel.GUTTER + width == 99


def test_a_narrow_terminal_gives_the_whole_region_to_the_panel():
    """Stage one of the degradation. Between 30 and 44 columns there is no
    room for two, so the panel takes the region and the prompt box is not
    drawn -- it is not accepting input while the panel has focus, and a box
    that looked ready for input would be a lie about what is happening."""
    for columns in (30, 37, 44):
        mode, left, width = agent_panel.layout(columns)
        assert mode == "panel_only", (columns, mode)
        assert left == 0, columns
        assert width == columns - 1, columns
    assert agent_panel.can_open(30) == ""
    assert agent_panel.can_open(44) == ""


def test_below_thirty_columns_the_panel_refuses_and_says_why():
    """Stage two. A panel that opened into something unreadable would be worse
    than the sentence saying it cannot, and the sentence names both widths
    because that is the whole of what the reader can act on."""
    mode, left, width = agent_panel.layout(29)
    assert mode == "refused" and left == 0 and width == 0

    refusal = agent_panel.can_open(29)
    assert refusal, refusal
    assert "29" in refusal and "30" in refusal, refusal
    assert "/agents" in refusal, refusal

    state = agent_panel.PanelState(register()[0], stream=Console())
    assert state.open_panel(29) is False
    assert state.open is False
    assert state.message == refusal
    # And it still opens the moment there is room.
    assert state.open_panel(30) is True
    assert state.open is True and state.message == ""


# --- the cards ---------------------------------------------------------------

def test_a_card_names_the_agent_its_tokens_and_what_it_is_doing():
    """The card is the whole of what the panel says about one agent, and every
    part of it has to survive the colour being stripped."""
    manager, _ = register(1)
    record = manager.list()[0]
    manager.add_tokens(record.id, tokens_in=40000, tokens_out=2000,
                       input_exact=True, output_exact=True)

    lines = [visible(row) for row in
             agent_panel.card_lines(record, 28, stream=Console())]
    assert len(lines) == 2, lines
    assert "#1" in lines[0], lines
    assert "42k" in lines[0], lines
    assert "running" in lines[0], lines
    assert "Reading agent_ui.py" in lines[1], lines


def test_the_state_is_a_word_on_the_row_that_is_never_dropped():
    """Not a colour and not a glyph. Position on the gradient confirms it --
    a finished agent takes success's 95 and a failed one takes error's 10 --
    but the word is what carries it, so the panel reads on a pipe."""
    manager, clock = register(3)
    first, second, third = manager.list()
    manager.complete(first.id, "done with it")
    manager.kill(second.id)
    manager.fail(third.id, "it broke")

    words = [visible(agent_panel.card_lines(record, 28, stream=Console())[0])
             for record in (first, second, third)]
    assert "done" in words[0], words
    assert "killed" in words[1], words
    assert "failed" in words[2], words
    # And each takes a place on the one gradient rather than a colour of its
    # own: 95 is success, 60 is warning, 10 is error, 40 is background_agent.
    assert agent_panel._state_position(first) == 95
    assert agent_panel._state_position(second) == 60
    assert agent_panel._state_position(third) == 10
    assert agent_panel.AGENT_POSITION == 40


def test_a_selected_card_is_marked_and_the_mark_is_not_a_colour():
    """Selection has to read with the escapes removed, which means a marker in
    the first column rather than a highlight."""
    manager, _ = register(2)
    records = manager.visible_agents()
    rows = panel_text(records, selected=0)
    marked = [row for row in rows if row.startswith(">")]
    assert len(marked) == 1, rows
    assert "#1" in marked[0], rows

    rows = panel_text(records, selected=1)
    marked = [row for row in rows if row.startswith(">")]
    assert len(marked) == 1, rows
    assert "#2" in marked[0], rows


def test_kill_all_agents_is_the_last_entry_and_is_distinct_without_colour():
    """It is the one destructive thing on the panel. Set off by a blank row,
    marked '!' rather than by a hue, and it takes the red end of the gradient
    -- which is confirmation, not the message."""
    manager, _ = register(2)
    rows = panel_text(manager.visible_agents(), selected=0)
    assert "! %s" % agent_panel.KILL_ALL_LABEL in rows, rows
    index = rows.index("! %s" % agent_panel.KILL_ALL_LABEL)
    assert rows[index - 1] == "", rows      # a blank row, not a second rule
    # No card wears the '!', so the mark alone tells them apart.
    assert len([row for row in rows if row.startswith("!")]) == 1, rows

    # Selected, it takes the selection marker like any other entry.
    rows = panel_text(manager.visible_agents(), selected=2)
    assert "> %s" % agent_panel.KILL_ALL_LABEL in rows, rows
    assert agent_panel.DANGER_POSITION == 10


def test_the_panel_draws_one_rule_and_counts_what_it_is_showing():
    """One rule per boundary. The header's rule marks that boundary and the
    blank row marks the other; two rules a blank line apart are not a
    boundary, they are a box with nothing in it."""
    manager, _ = register(2)
    rows = panel_text(manager.visible_agents(), width=28)
    assert rows[0] == "AGENTS 2", rows
    assert set(rows[1]) == {"─"}, rows
    assert len([row for row in rows if row and set(row) <= {"─", "-"}]) == 1, rows


def test_every_panel_row_is_measured_rather_than_counted():
    """`display_width`, never `len`. A full-width character counts two, and a
    row that overflowed would wrap and cost a screen line the repaint
    arithmetic does not know about."""
    manager, _ = register(1)
    record = manager.list()[0]
    manager.set_activity(record.id, "読み込み中 agent_ui.py")
    for width in (18, 24, 28, 34):
        for row in agent_panel.panel_rows(manager.visible_agents(), width,
                                          stream=Console()):
            assert menu().visible_width(row) <= width, (width, repr(row))


# --- tokens ------------------------------------------------------------------

def test_an_estimated_token_figure_says_it_is_estimated():
    """The rule is absolute everywhere in TMT: a guessed number is prefixed
    `~`. `tokens_in_exact` and `tokens_out_exact` are that signal, and they
    start False because nothing has been reported yet -- an unmarked zero
    would be a claim that the provider said zero."""
    manager, _ = register(1)
    record = manager.list()[0]

    manager.add_tokens(record.id, tokens_in=1200, tokens_out=800)
    assert agent_panel._token_text(record).startswith("~2k"), record

    manager.set_tokens(record.id, tokens_in=1200, tokens_out=800,
                       input_exact=True, output_exact=True)
    text = agent_panel._token_text(record)
    assert text.startswith("2k"), text
    assert "~" not in text, text


def test_the_card_shows_the_request_in_flight_as_a_plus():
    """`#1: 42k T +4120`: the running total, then the output of the request
    being streamed. Two figures because the total has to stay a total -- an
    agent on its fourth request cannot recover the fourth reply from it by
    subtraction."""
    manager, _ = register(1)
    record = manager.list()[0]
    manager.set_tokens(record.id, tokens_in=40000, tokens_out=2000,
                       input_exact=True, output_exact=True)
    manager.set_pending_output(record.id, 4120)

    text = agent_panel._token_text(record)
    # The total is abbreviated, the figure in flight is not. That difference
    # is the point: the total is read for its order of magnitude, while the
    # one beside it is the only number on the card that moves, and rounding a
    # live counter to `+4k` would hold it still for a thousand tokens at a
    # time -- which reads as a worker that has stopped.
    assert text == "42k T +4120", text
    assert "running" not in text, text     # the +N already says it is running

    # Still streaming, so the figure is the stream's own count and is marked.
    manager.set_tokens(record.id, output_exact=False)
    assert agent_panel._token_text(record) == "~42k T +~4120"

    # Nothing in flight, and the state word takes the slot back.
    manager.set_pending_output(record.id, 0)
    assert agent_panel._token_text(record).endswith("running")


def test_an_agent_with_nothing_reported_yet_draws_no_figures_at_all():
    """Nothing to report means nothing drawn, not a row of zeroes. It is the
    rule the corner meter already follows."""
    manager, _ = register(1)
    record = manager.list()[0]
    assert agent_panel._token_text(record) == "running", record
    assert "0" not in visible(agent_panel.card_lines(record, 28,
                                                     stream=Console())[0])


def test_the_activity_line_goes_before_the_token_line():
    """The order the cards give things up in. An agent whose label has gone is
    still identifiably running; an agent whose numbers have gone is prose."""
    manager, _ = register(3)
    for record in manager.list():
        manager.set_tokens(record.id, tokens_in=1000, tokens_out=1000)

    roomy = panel_text(manager.visible_agents(), width=28)
    assert any("Reading agent_ui.py" in row for row in roomy), roomy

    # Seven rows is header, rule, three token lines, the blank row and the
    # kill entry: exactly enough for the numbers and nothing else.
    tight = panel_text(manager.visible_agents(), width=28, height=7)
    assert len(tight) <= 7, tight
    assert not any("Reading agent_ui.py" in row for row in tight), tight
    for number in ("#1", "#2", "#3"):
        assert any(number in row for row in tight), (number, tight)
    assert any(agent_panel.KILL_ALL_LABEL in row for row in tight), tight


def test_a_panel_with_no_room_for_every_card_still_counts_them_all():
    """Truncating in silence would be a claim that those are all the agents
    there are, which is the one thing a register of background work may not
    say. The header carries the count and costs no extra row to do it, so a
    header reading AGENTS 5 above one card has already said the rest are not
    drawn."""
    manager, _ = register(5)
    rows = panel_text(manager.visible_agents(), width=28, height=5)
    assert len(rows) <= 5, rows
    assert rows[0] == "AGENTS 5", rows
    assert len([row for row in rows if row.lstrip().startswith("#")
                or row.startswith("> #")]) < 5, rows


def test_the_window_follows_the_selection_rather_than_trimming_it_away():
    """A list that scrolled the selected row off the bottom would leave the
    user driving something they cannot see."""
    manager, _ = register(5)
    rows = panel_text(manager.visible_agents(), width=28, height=5, selected=4)
    marked = [row for row in rows if row.startswith(">")]
    assert marked, rows
    assert "#5" in marked[0], rows


# --- composition -------------------------------------------------------------

def test_the_two_columns_compose_row_by_row_inside_the_width():
    """Each row of the live region is left content, a gutter, and the panel
    column. Drawn to `columns - 1`, because a row filled to the last column
    wraps on the terminals that auto-wrap."""
    manager, _ = register(2)
    right = agent_panel.panel_rows(manager.visible_agents(), 28,
                                   stream=Console())
    rows = agent_panel.compose(["one", "two"], right, 99)
    for row in rows:
        assert menu().visible_width(row) <= 99, (menu().visible_width(row), row)
    assert len(rows) == max(2, len(right))


def test_the_left_column_is_flush_with_the_bottom_and_the_panel_with_the_top():
    """Neither is taste. The prompt box is the last thing in the left column
    and has to stay at the foot of the window, so the blank rows a short left
    column needs go above it. The panel is read downward from its header, so
    the rows it needs go below it."""
    rows = agent_panel.compose(["box"], ["AGENTS 1", "a", "b"], 60)
    assert len(rows) == 3
    assert rows[0].strip() == "AGENTS 1", rows
    assert rows[-1].startswith("box"), rows
    assert rows[0].startswith(" "), rows


def test_an_overlong_left_row_is_cut_rather_than_wrapping_the_region():
    """A row that pushed the panel right would wrap, and a wrapped row costs a
    screen line the repaint arithmetic does not count -- which marches the
    whole frame down the screen on every repaint. Losing the paint on one row
    is recoverable; that is not."""
    rows = agent_panel.compose(["x" * 200], ["AGENTS 0"], 40)
    for row in rows:
        assert menu().visible_width(row) <= 40, (menu().visible_width(row), row)


# --- the keys ----------------------------------------------------------------

def test_the_panel_takes_four_keys_and_hands_everything_else_back():
    """Both spellings of each, because `read_key(raw=True)` returns the escape
    sequence on POSIX and the key's name on Windows. A panel that knew only
    one of them would be dead on the other platform."""
    for raw, name in (("\x1b[A", "up"), ("\x1bOA", "up"), ("up", "up"),
                      ("\x1b[B", "down"), ("\x1bOB", "down"), ("down", "down"),
                      ("\x1b[D", "left"), ("\x1bOD", "left"), ("left", "left"),
                      ("\x1b[C", "right"), ("right", "right"),
                      ("\r", "enter"), ("\n", "enter"), ("enter", "enter"),
                      ("\x1b", "esc"), ("esc", "esc")):
        assert agent_panel.panel_key(raw) == name, repr(raw)
    # Letters are letters. 'j' and 'k' drive the menus; here they are text.
    for raw in ("j", "k", "a", "\x7f", "\t", "\x03", "", None, 7):
        assert agent_panel.panel_key(raw) == "", repr(raw)


def test_up_and_down_move_the_selection_and_stop_at_the_ends():
    """The Kill All Agents row is the last entry, so a panel showing two
    agents has three."""
    manager, _ = register(2)
    state = agent_panel.PanelState(manager, stream=Console())
    state.open_panel(100)
    assert state.selected == 0

    assert state.handle("down") == "moved" and state.selected == 1
    assert state.handle("down") == "moved" and state.selected == 2
    assert state.handle("down") == "moved" and state.selected == 2
    assert state.handle("up") == "moved" and state.selected == 1
    assert state.handle("up") == "moved" and state.selected == 0
    assert state.handle("up") == "moved" and state.selected == 0
    assert agent_panel.entry_count(manager.visible_agents()) == 3


def test_enter_kills_the_selected_agent_and_the_last_entry_kills_them_all():
    """Killing is what a panel of running agents is for, and it is the only
    thing this one does. The hint row says so before Enter is pressed, which
    is the whole reason that row exists."""
    manager, _ = register(3)
    state = agent_panel.PanelState(manager, stream=Console())
    state.open_panel(100)
    state.handle("down")
    assert state.handle("enter") == "killed"
    assert manager.inspect("2").status == agent_manager.Status.KILLED
    assert manager.inspect("1").status != agent_manager.Status.KILLED
    assert manager.inspect("3").status != agent_manager.Status.KILLED

    state.selected = agent_panel.entry_count(manager.visible_agents()) - 1
    assert state.handle("enter") == "killed_all"
    assert manager.active_count() == 0

    rows = panel_text(manager.visible_agents(), width=28)
    assert any("Enter" in row for row in rows), rows


def test_a_closed_panel_takes_no_keys_at_all():
    """The panel captures Up, Down, Enter and Left only while it has focus.
    Shut, every one of them belongs to the field."""
    manager, _ = register(2)
    state = agent_panel.PanelState(manager, stream=Console())
    for intent in ("up", "down", "enter", "left", "esc"):
        assert state.handle(intent) == "", intent
    assert manager.active_count() == 2


# --- driving the box ---------------------------------------------------------

def test_right_arrow_at_the_end_of_the_line_opens_the_panel():
    """Right Arrow already moves the caret, and binding it outright would
    break editing and several tests that describe it. At the END of the buffer
    it moves nothing, and that is the only place it is taken."""
    screen, box = Screen(100, 24), None
    try:
        manager, _ = register(2)
        box = menu().PromptBox(stream=Terminal(), reader=Typing("\x1b[C"),
                               manager=manager)
        assert box.ask("") is None          # the script ran out, which is fine
        assert box.panel().open is True
    finally:
        screen.close()


def test_left_closes_the_panel_and_the_line_is_answered_normally():
    manager, _ = register(2)
    screen = Screen(100, 24)
    try:
        box = menu().PromptBox(stream=Terminal(),
                               reader=Typing("\x1b[C", "\x1b[D", "h", "i", "\r"),
                               manager=manager)
        assert box.ask("") == "hi"
        assert box.panel().open is False
    finally:
        screen.close()


def test_right_arrow_in_the_middle_of_a_line_still_moves_the_caret():
    """The one that must not regress. Nothing that works today changes, so a
    Right Arrow with text to its right is a caret move and nothing else."""
    manager, _ = register(1)
    screen = Screen(100, 24)
    try:
        box = menu().PromptBox(
            stream=Terminal(),
            reader=Typing("a", "b", "\x1b[H", "\x1b[C", "X", "\r"),
            manager=manager)
        assert box.ask("") == "aXb"
        assert box.panel().open is False
    finally:
        screen.close()


def test_the_arrows_drive_the_selection_through_the_box_itself():
    """Through `ask`, not through PanelState: a panel that works and is not
    wired to a key is a panel that does not exist."""
    manager, _ = register(2)
    screen = Screen(100, 24)
    try:
        box = menu().PromptBox(stream=Terminal(),
                               reader=Typing("\x1b[C", "\x1b[B", "\x1b[B",
                                             "\x1b[A"),
                               manager=manager)
        assert box.ask("") is None
        assert box.panel().selected == 1
    finally:
        screen.close()


def test_a_character_typed_while_the_panel_is_open_is_still_typed():
    """The panel takes four keys. Everything else falls through to the field,
    so the line being written is not lost behind it."""
    manager, _ = register(1)
    screen = Screen(100, 24)
    try:
        box = menu().PromptBox(stream=Terminal(),
                               reader=Typing("\x1b[C", "o", "k", "\x1b[D", "\r"),
                               manager=manager)
        assert box.ask("") == "ok"
    finally:
        screen.close()


def test_the_open_panel_draws_beside_the_box_inside_the_window():
    """Two columns: the box on the left at the reduced width, the panel on the
    right, and the whole row still one column short of the window."""
    manager, _ = register(2)
    screen = Screen(100, 24)
    try:
        box = menu().PromptBox(stream=Console(), manager=manager)
        box.panel().open_panel(100)
        rows = rows_of(box)
        assert any("AGENTS 2" in row for row in rows), rows
        assert any(row.lstrip().startswith("> ") for row in rows), rows
        for row in rows:
            assert menu().display_width(row) <= 99, (len(row), row)
    finally:
        screen.close()


def test_a_terminal_too_narrow_for_two_columns_draws_no_box_at_all():
    """Stage one of the degradation, through the box. It is not accepting
    input while the panel has focus, and a box that looked ready for input
    would be a lie about what the program is doing."""
    manager, _ = register(2)
    screen = Screen(40, 24)
    try:
        box = menu().PromptBox(stream=Console(), manager=manager)
        box.panel().open_panel(40)
        rows = rows_of(box, editor("Describe your first task"), size=(40, 24))
        assert any("AGENTS 2" in row for row in rows), rows
        # Nothing of the box: no marker row, and not a character of the
        # placeholder that would sit in it.
        assert not any(row.lstrip().startswith("> D") for row in rows), rows
        assert not any("Describe" in row for row in rows), rows
        # No input row means no caret to place: `_place` is told zero and
        # leaves the caret where the region put it.
        assert box._frame(editor(), size=(40, 24))[2] == 0
    finally:
        screen.close()


def test_the_box_says_why_it_would_not_open_and_takes_the_line_down_again():
    """A refusal answers one gesture and stops being true at the next, so it
    is drawn on the temporary surface and cleared by the following key rather
    than left in the scrollback for the rest of the session."""
    manager, _ = register(1)
    screen = Screen(28, 24)
    try:
        box = menu().PromptBox(stream=Console(), manager=manager)
        assert box._open_panel(size=(28, 24)) is False
        rows = rows_of(box, size=(28, 24))
        assert any("too" in row or "28" in row for row in rows), rows
        assert not any("AGENTS" in row for row in rows), rows

        box.panel().message = ""
        assert not any("28 columns" in row
                       for row in rows_of(box, size=(28, 24)))
    finally:
        screen.close()


# --- the counter -------------------------------------------------------------

def test_the_counter_says_how_many_and_nothing_when_there_are_none():
    assert agent_panel.counter_text(0) == ""
    assert agent_panel.counter_text(1) == "1 agent"
    assert agent_panel.counter_text(3) == "3 agents"
    assert agent_panel.counter_text(-2) == ""


def test_the_counter_rides_on_the_caption_beside_the_facts():
    """It is a fact about the session, so it goes where the session's facts
    are -- and it is the first of them given up as the terminal narrows,
    because a count of background work says less than what the next question
    runs under."""
    sandbox = Sandbox()
    try:
        caption = visible(menu().prompt_caption(Console(), 100,
                                                agents="2 agents"))
        assert "2 agents" in caption, caption
        assert menu().display_width(caption) == 100, caption

        narrow = visible(menu().prompt_caption(Console(), 24,
                                               agents="2 agents"))
        assert "2 agents" not in narrow, narrow
    finally:
        sandbox.close()


def test_the_counter_follows_the_manager_through_the_box():
    manager, clock = register(2)
    sandbox = Sandbox()
    try:
        box = menu().PromptBox(stream=Console(), manager=manager)
        assert box._agents_text() == "2 agents"
        caption = visible(box.lines(editor(), size=(100, 24))[0])
        assert "2 agents" in caption, caption

        manager.kill_all()
        clock.advance(agent_manager.RETENTION_SECONDS + 1)
        assert box._agents_text() == ""
        assert "agent" not in visible(box.lines(editor(), size=(100, 24))[0])
    finally:
        sandbox.close()


def test_the_running_box_carries_the_counter_on_the_meter_row():
    """The row the eye is already on while a turn runs. A second row for the
    counter would push the box up the window every time an agent was
    spawned."""
    manager, _ = register(1)
    sandbox = Sandbox()
    try:
        box = menu().PromptBox(stream=Console(), manager=manager)
        rows = [visible(row) for row in
                box.running_lines("Working.", size=(100, 24))]
        assert "1 agent" in rows[0], rows
    finally:
        sandbox.close()


# --- retention ---------------------------------------------------------------

def test_a_finished_card_stays_for_five_seconds_and_then_the_counter_drops_it():
    """Filtered on read against an injected clock, so the panel and the
    counter lose it together and neither needs a timer."""
    manager, clock = register(1)
    state = agent_panel.PanelState(manager, stream=Console())
    record = manager.list()[0]
    manager.complete(record.id, "finished")

    assert state.count() == 1
    assert state.counter() == "1 agent"
    assert any("done" in row for row in panel_text(state.records()))

    clock.advance(agent_manager.RETENTION_SECONDS - 0.5)
    assert state.count() == 1, "still inside the retention window"

    clock.advance(1.0)
    assert state.count() == 0
    assert state.counter() == ""
    # The card is gone; the result the main AI may still want is not.
    assert manager.result(record.id) == "finished"


# --- the live relay ----------------------------------------------------------

def test_the_relay_puts_the_panel_beside_its_own_rows():
    """The panel is a column inside the region that is already alive. The
    status row stays full width underneath both, because it is the instrument
    measuring the turn and an instrument belongs under what it measures."""
    import agent_live_renderer

    manager, _ = register(2)
    state = agent_panel.PanelState(manager, stream=Console())
    state.open_panel(100)
    screen = Screen(100, 24)
    try:
        relay = agent_live_renderer.LiveRelay(
            stream=Console(),
            footer=lambda size=None: ["  > the box"],
            panel=lambda columns, rows: state.frame(columns, rows))
        rows = [visible(row) for row in relay._compose("bar 40%", "")]
        assert rows[-1] == "bar 40%", rows
        assert any("AGENTS 2" in row for row in rows[:-1]), rows
        assert any("> the box" in row for row in rows[:-1]), rows
        for row in rows:
            assert menu().display_width(row) <= 100, (len(row), row)
    finally:
        screen.close()


def test_a_change_outside_the_region_can_ask_it_to_repaint():
    """The relay repaints when the reply moves on and when the status row
    changes, and at no other time -- the repaint-on-a-timer is gone and that
    is half the cursor fix. An agent's activity label changing on a worker
    thread is invisible to it, so whoever knows says so, once, and the
    region's own worker does the painting. Nothing is printed from the caller's
    thread."""
    import agent_live_renderer

    screen = Screen(100, 24)
    try:
        painted = []
        relay = agent_live_renderer.LiveRelay(stream=Console(),
                                              footer=lambda: ["  > the box"])
        relay._repaint = lambda: painted.append(True)
        relay.refresh()
        assert painted, "a stopped region repaints where it stands"
        relay._closed = True
        painted[:] = []
        relay.refresh()
        assert not painted, "a torn-down region paints nothing"
    finally:
        screen.close()


def test_the_relay_with_no_panel_composes_exactly_what_it_always_did():
    """The panel costs a session that has none precisely nothing: same rows,
    same footer call, same widths. Two frames of an untouched region being
    identical is what lets the repaint be skipped, and that is half the cursor
    fix."""
    import agent_live_renderer

    screen = Screen(100, 24)
    try:
        made = []

        def footer():
            made.append(True)
            return ["  > the box"]

        relay = agent_live_renderer.LiveRelay(stream=Console(), footer=footer)
        plain = relay._compose("bar 40%", "some reply text")
        assert plain[-1] == "bar 40%", plain
        assert "  > the box" in plain, plain
        assert made, "the zero-argument footer must still be called with none"
        assert any(row.startswith("┌") for row in plain), plain
        for row in plain:
            assert menu().display_width(row) <= 99, (len(row), row)
    finally:
        screen.close()


# --- what must not have changed ---------------------------------------------

def test_a_box_with_no_manager_draws_the_rows_it_always_drew():
    """Four rows: the caption, a rule, the line, a rule. No panel, no counter,
    no extra key and no extra repaint."""
    sandbox = Sandbox()
    try:
        # The moment is fixed for both frames. The caption states the clock,
        # and a real one re-read between two calls makes them differ for a
        # reason that has nothing to do with the box -- which is exactly why
        # `ask` fixes the moment for the life of a question rather than
        # reading it per repaint.
        moment = datetime.datetime(2026, 8, 30, 15, 42, 7)
        box = menu().PromptBox(stream=Console())
        assert box.panel() is None
        rows = box.lines(editor("Describe your first task"), size=(80, 24),
                         moment=moment)
        assert len(rows) == 4, rows
        assert box._frame(editor(), size=(80, 24))[2] == menu()._INPUT_ROW

        # And two frames of an untouched box are byte-identical, which is what
        # lets LiveRegion skip the repaint.
        again = box.lines(editor("Describe your first task"), size=(80, 24),
                          moment=moment)
        assert rows == again
    finally:
        sandbox.close()


def test_a_caption_with_no_agents_is_the_caption_it_always_was():
    """Byte-identical, not merely similar: the counter is inserted as an extra
    candidate group and an empty one has to leave the three that were there
    exactly as they were."""
    sandbox = Sandbox()
    try:
        moment = datetime.datetime(2026, 8, 30, 15, 42, 7)
        for width in (100, 60, 40, 24):
            plain = menu().prompt_caption(Console(), width, moment)
            assert plain == menu().prompt_caption(Console(), width, moment,
                                                  agents="")
            # And the row still begins where it always began. An empty counter
            # joined into the group would put a bare separator in front of the
            # clock, which is a fact with nothing on the other side of it.
            text = visible(plain).strip()
            assert text[:1].isdigit(), (width, text)
    finally:
        sandbox.close()


def test_the_hint_shortens_rather_than_being_cut_through():
    """The one row on the panel that must not be elided. A card cut in the
    middle still says which agent it is; "Enter kil...t closes" says nothing
    and reads as a fault."""
    manager, _ = register(1)
    for width, expected in ((34, "Enter kills, Left closes"),
                            (18, "Enter kills"),
                            (8, None)):
        rows = panel_text(manager.visible_agents(), width=width)
        said = [row for row in rows if row.startswith("Enter")]
        if expected is None:
            assert not said, (width, rows)
            continue
        assert said == [expected], (width, rows)
        assert "…" not in said[0], (width, said)


def test_the_panel_never_reaches_for_the_two_banned_escapes():
    """DECSTBM discards the lines that scroll out of a narrowed region and the
    alternate screen buffer throws the history away wholesale. TMT's permanent
    surface IS that scrollback, and the panel is the feature most tempted by
    both -- which is exactly why it may not have either."""
    source = Path(agent_panel.__file__).resolve()
    for path in (source, source.with_name("agent_menu.py"),
                 source.with_name("agent_live_renderer.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#") or "DECSTBM" in line:
                continue
            assert "1049" not in line, (path.name, line)
            assert not re.search(r"\\033\[[^\"']*r[\"']", line), (path.name, line)


def test_the_module_is_clean_utf_8_and_needs_nothing_outside_the_library():
    """A heredoc has corrupted this repository five times, once writing a NUL
    byte into a module. The rest of the suite guards every module; this one
    guards the newest."""
    text = Path(agent_panel.__file__).resolve().read_bytes().decode("utf-8")
    assert "\x00" not in text
    assert "import requests" not in text and "import rich" not in text


# --- the alternate way in ----------------------------------------------------

def test_agents_report_says_the_same_things_the_cards_do():
    """`/agents` is the unambiguous alternate, and the only way in on a
    terminal too narrow for the panel to open into. It goes to the permanent
    surface, so it is a record rather than a frame."""
    manager, _ = register(2)
    manager.set_tokens("1", tokens_in=1000, tokens_out=1000,
                       input_exact=True, output_exact=True)
    text = agent_panel.agents_report(manager)
    assert "AGENTS 2" in text, text
    assert "#1: 2k T running" in text, text
    assert "Reading agent_ui.py" in text, text
    assert "task number 1" in text, text

    assert agent_panel.agents_report(None) == (
        "Background agents are unavailable in this session.")
    empty = agent_manager.AgentManager(clock=Clock())
    assert agent_panel.agents_report(empty) == "No background agents are running."
