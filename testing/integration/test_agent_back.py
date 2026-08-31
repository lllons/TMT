"""Tests for `/back`: stepping out to the menu without ending anything.

The feature is a promise made in three places at once, and each place can
break it on its own. The command has to say "go to the menu" without touching
the session. The menu has to say what it now means -- Resume rather than
Start, and an Exit that admits it ends a conversation that is still alive.
And while work is still running the Settings row has to be GONE rather than
merely inert, because a setting read in the middle of a request in flight is
a change nobody asked for landing where nobody can see it.

The removal is the part with a hazard in it. A row that vanishes under a
cursor tracked by INDEX slides that cursor onto the next row, and the next
row is Exit -- so the gesture that protects the session would be one
keystroke away from ending it. `main_menu` tracks the cursor by KEY, and
`test_a_row_vanishing_under_the_cursor_cannot_select_exit` is the test that
kills the index-based implementation.

Nothing here sleeps, reaches a network, calls a model, starts a real agent
thread or reads a real key. Every menu is driven through `main_menu` with a
scripted reader and a region that paints nowhere; `run_startup` is never
called, because it reads the terminal and the interactivity check in front of
it is exactly what a test cannot honestly satisfy.
"""

import io
import os

import agent_commands
import agent_manager
import agent_menu
import agent_session
import agent_ui
import TMT

from agent_live_renderer import LiveRegion

# A model id passed explicitly everywhere a frame is drawn, so no assertion
# here depends on whatever the developer happens to have saved.
MODEL = "minimax/minimax-m3:free"
WORKSPACE = "C:/probe_workspace"


def visible(text):
    """The row a reader sees, with every escape sequence removed.

    Colour is never the message in TMT, so everything about what a frame SAYS
    is asserted through this. A test reading the painted string would pass on
    a frame whose only Resume was a colour.
    """
    return agent_ui.strip_ansi(text)


# --- the harnesses ----------------------------------------------------------

class Terminal(io.StringIO):
    """A buffer that claims to be a terminal, so colour is exercised."""

    def isatty(self):
        return True

    @property
    def encoding(self):
        return "utf-8"


class Plain(io.StringIO):
    """A buffer that does not claim to be a terminal.

    `_supports_color` is `isatty() and not NO_COLOR`, so this is the honest
    way to get the uncoloured frame: setting NO_COLOR alone on a stream that
    still claims to be a tty is the trap the working notes record.
    """

    @property
    def encoding(self):
        return "utf-8"


class Ansi:
    """NO_COLOR out of the way and COLUMNS pinned, both put back on close.

    A developer running the suite with NO_COLOR set would otherwise see the
    colour assertions fail on their machine and nowhere else, and a frame
    whose width came from the real window would make the fitting sweep mean
    something different for every reader.
    """

    def __init__(self, columns=100):
        self.previous_no_colour = os.environ.get("NO_COLOR")
        self.previous_columns = os.environ.get("COLUMNS")
        os.environ.pop("NO_COLOR", None)
        os.environ["COLUMNS"] = str(columns)

    def close(self):
        for name, value in (("NO_COLOR", self.previous_no_colour),
                            ("COLUMNS", self.previous_columns)):
            os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value


class Keys:
    """A scripted key reader that cannot hang the suite.

    Running past the end of the script is a failure, not a wait: a menu still
    asking for keys after the script chose something has not returned, and a
    test that blocked on a real console would hang the whole run -- there is
    no per-test timeout here.

    A callable in the script is a HOOK rather than a key: it is run and
    stepped over on the way to the next key. That is how a test makes work
    start or finish BETWEEN two keystrokes, which is the only moment the
    vanishing row can be observed vanishing.
    """

    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0

    def __call__(self, *args, **kwargs):
        while self.script and callable(self.script[0]):
            self.script.pop(0)()
        self.calls += 1
        if not self.script:
            raise AssertionError(
                "the menu asked for key %d; the script provided %d"
                % (self.calls, self.calls - 1))
        return self.script.pop(0)

    @property
    def remaining(self):
        return len(self.script)


class Interrupting:
    """A reader that raises KeyboardInterrupt when asked for a key.

    Raised synchronously from inside the call the menu makes, which is where
    `_next_key` catches it. The working notes forbid firing a real interrupt
    at the interpreter in this suite -- it is asynchronous and lands in
    whatever runs next -- and this is not that: it never leaves the call.
    """

    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise KeyboardInterrupt()


class Frames:
    """A region that records frames instead of drawing them.

    Stronger than painting into a buffer for the one question that matters
    about a row that comes and goes: every frame is kept, so "Settings was
    absent and then present" is asserted on what was DRAWN rather than
    inferred from what was chosen at the end.
    """

    def __init__(self):
        self.frames = []

    def paint(self, lines):
        self.frames.append(list(lines))

    def clear(self):
        pass

    def show_cursor(self):
        pass

    def rows(self, index):
        """The frame exactly as it was painted, escapes and all."""
        return self.frames[index]

    def text(self, index):
        return "\n".join(visible(row) for row in self.frames[index])


class Work:
    """What is still running, as the menu's `busy` callable sees it.

    A mutable phrase rather than a fixed one, because the whole reason
    `main_menu` takes a callable is that the answer changes while the menu is
    open. `calls` is how a test proves it was re-asked rather than remembered.
    """

    def __init__(self, phrase=""):
        self.phrase = phrase
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.phrase

    def start(self, phrase="1 agent"):
        self.phrase = phrase

    def finish(self):
        self.phrase = ""


def nowhere():
    """A live region that paints to a buffer and emits nothing.

    `ansi=False` makes `paint` a no-op, which is what a test wants when it is
    asking what the menu RETURNED rather than what it drew.
    """
    return LiveRegion(io.StringIO(), ansi=False)


def menu(*script, **kwargs):
    """Drive main_menu with a scripted script. Returns (choice, keys)."""
    keys = Keys(*script)
    region = kwargs.pop("region", None) or nowhere()
    choice = agent_menu.main_menu(stream=kwargs.pop("stream", None) or Plain(),
                                  key_reader=keys, region=region,
                                  model_id=MODEL, workspace=WORKSPACE,
                                  **kwargs)
    return choice, keys


def frame(**kwargs):
    """One startup frame, drawn at a known width, height, phase and model.

    Everything that could move is pinned: a frame built from the real window,
    the real clock's gradient phase or the developer's saved model is a frame
    that says something different for every reader.
    """
    stream = kwargs.pop("stream", None)
    return agent_menu.render_startup_frame(
        kwargs.pop("selected", 0), Terminal() if stream is None else stream,
        model_id=MODEL, workspace=WORKSPACE,
        size=kwargs.pop("size", (100, 40)), phase=kwargs.pop("phase", 0.2),
        **kwargs)


def session(turns=0):
    made = agent_session.Session(workspace=WORKSPACE)
    for number in range(turns):
        made.record("Question %d." % number, "Answer %d." % number)
    return made


def keys_of(items):
    return [item[0] for item in items]


# --- the command ------------------------------------------------------------

def test_back_is_a_command_the_user_can_find():
    """A command with no SUMMARY draws a blank row in the completion list
    under the prompt box, and one with no USAGE has nothing to print when it
    is given an argument it does not take. Both are enumerations every other
    command is in, so an addition that misses one fails only here."""
    assert "back" in agent_commands.names()
    assert agent_commands.SUMMARY["back"].strip()
    assert agent_commands.USAGE["back"] == "/back"
    assert "menu" in agent_commands.SUMMARY["back"]


def test_back_asks_the_loop_for_the_menu():
    """`to_menu` is the whole of the wiring: `_session_loop` reads it off the
    Result and calls `_return_to_menu`. A handler that forgot the flag would
    print a tidy report about going to the menu and then not go."""
    answer = agent_commands.dispatch("/back")
    assert isinstance(answer, agent_commands.Result)
    assert answer.to_menu is True
    assert answer.ok is True


def test_no_other_command_asks_for_the_menu():
    """The test that stops a later edit turning an ordinary command into an
    exit. `to_menu` takes the user off the session screen, so every command
    that is not `/back` must answer False -- and this asks all of them rather
    than the ones somebody remembered to think about."""
    for name in agent_commands.names():
        answer = agent_commands.dispatch("/" + name, None)
        assert isinstance(answer, agent_commands.Result), name
        if name == "back":
            assert answer.to_menu is True
            continue
        assert answer.to_menu is False, name


def test_back_reports_the_session_it_is_leaving_behind():
    """The command is the only thing that says what is being left, and a
    piped run -- which has no menu to draw and skips it -- sees nothing else.
    The turn count, the model and the workspace are the three facts that say
    which session is still there."""
    answer = agent_commands.dispatch("/back", session(turns=3))
    text = answer.text()
    assert "3 turns, kept" in text
    assert WORKSPACE in text
    assert "Model" in text


def test_back_gets_the_plural_right_for_one_turn():
    """A row reading "1 turns, kept" is the kind of detail that makes a user
    doubt the count beside it, and the count is the claim that the session
    survived."""
    answer = agent_commands.dispatch("/back", session(turns=1))
    assert "1 turn, kept" in answer.text()


def test_back_says_the_session_is_kept():
    """Nothing is ended, cleared, cancelled or waited for, and the user has
    no way to know that from the screen going blank. The note is the promise;
    without it `/back` is indistinguishable from `/clear` followed by a menu."""
    answer = agent_commands.dispatch("/back", session(turns=2))
    note = answer.note
    assert "Resume" in note
    assert "Nothing has been stopped or forgotten." in note


def test_back_leaves_the_conversation_alone():
    """The command must not be a disguised `/clear`. Dispatching it is the
    only chance this module has to touch the session, and it must not take
    it: the turns are still there and still say what they said."""
    live = session(turns=2)
    before = len(live)
    agent_commands.dispatch("/back", live)
    assert len(live) == before == 2
    assert live.turns[0].task == "Question 0."


def test_back_works_without_a_session():
    """A command must not need a session to be able to leave one. `/back`
    from a run that never built a Session -- a pipe, a test, the first line
    of a script -- still has to set the flag, or the loop would sit on the
    session screen with nothing to show for the command."""
    answer = agent_commands.dispatch("/back", None)
    assert answer.to_menu is True
    assert answer.rows == []
    assert "Nothing has been stopped" in answer.note


def test_back_with_a_stray_argument_is_refused_and_does_not_leave():
    """Pinned deliberately: `/back now` is an ERROR, not a departure. `back`
    is not in `_TAKES_ARGUMENT` and not a capability command, so `dispatch`
    refuses it before the handler runs -- and the refusal Result carries
    to_menu False, so a mistyped line cannot take the screen away. The
    alternative (ignoring the argument) would make `/back to the code` mean
    something the user did not type."""
    answer = agent_commands.dispatch("/back extra words")
    assert answer.ok is False
    assert answer.to_menu is False
    assert "takes no argument" in answer.title
    assert "/back" in answer.text()


def test_a_result_does_not_ask_for_the_menu_unless_it_says_so():
    """Every Result built before this field existed still means what it
    meant. A default of True -- or a truthy default of any kind -- would send
    the user to the menu after `/context`."""
    assert agent_commands.Result("Anything").to_menu is False
    assert agent_commands.Result("Anything", ["a row"]).to_menu is False
    assert agent_commands.Result("Anything", ok=False).to_menu is False
    assert agent_commands.Result("Anything", prompt_for="note").to_menu is False


def test_the_menu_flag_is_a_bool_and_lives_in_the_slots():
    """`Result` uses __slots__, so a field that was not declared raises on
    assignment rather than being quietly kept -- and the flag is coerced, so
    a handler passing a truthy object cannot leave the loop reading something
    that is not a bool."""
    assert "to_menu" in agent_commands.Result.__slots__
    assert agent_commands.Result("x", to_menu="yes").to_menu is True
    assert agent_commands.Result("x", to_menu=0).to_menu is False


def test_the_menu_flag_does_not_change_what_a_result_says():
    """`text()` is what a piped run prints and what most of the suite reads.
    A new field that leaked into it would change the output of every command
    that ever sets it, and would do it silently."""
    rows = [("Session", "2 turns, kept"), "a line of prose"]
    plain = agent_commands.Result("Title", rows, note="a note")
    leaving = agent_commands.Result("Title", rows, note="a note", to_menu=True)
    assert plain.text() == leaving.text()
    assert repr(plain) == repr(leaving)


# --- menu_items -------------------------------------------------------------

def test_the_launch_menu_is_unchanged():
    """The screen everybody already knows. `menu_items()` with no arguments
    is what `main` reaches, so a feature that quietly reshaped the default
    would change the first thing every user sees."""
    assert agent_menu.menu_items() == agent_menu.MENU_ITEMS
    assert keys_of(agent_menu.MENU_ITEMS) == ["start", "settings", "help", "exit"]


def test_resuming_swaps_the_label_and_keeps_the_key():
    """The key is what every caller acts on -- `run_startup` returns it and
    `main` branches on it -- so a Resume row with a key of its own would
    make the menu answer a word nothing understands, and TMT would exit
    instead of resuming. The label is free to change; the key is not."""
    items = agent_menu.menu_items(resuming=True)
    assert items[0][0] == "start"
    assert items[0][1] == "Resume"
    assert agent_menu.RESUME_ITEM[0] == "start"
    assert agent_menu.menu_items()[0][1] == "Start"


def test_resuming_changes_what_exit_means_and_nothing_else_about_it():
    """The word is the same as it was at launch and the consequence is not:
    at launch Exit closes a program that has started nothing, and here it
    ends a live conversation. The detail is the only warning there is, and
    the key and the label must not move -- an "Exit (ends session)" label
    would push the column and read as a different button."""
    launch = dict((item[0], item) for item in agent_menu.menu_items())
    resuming = dict((item[0], item) for item in agent_menu.menu_items(resuming=True))
    assert resuming["exit"][0] == launch["exit"][0] == "exit"
    assert resuming["exit"][1] == launch["exit"][1] == "Exit"
    assert resuming["exit"][2] == agent_menu.RESUME_EXIT_DETAIL
    assert resuming["exit"][2] != launch["exit"][2]
    assert "session" in agent_menu.RESUME_EXIT_DETAIL


def test_busy_removes_the_settings_row_entirely():
    """Removal, not disabling, and that is what makes it a guarantee. A
    greyed row is still a row: it can still be selected, Enter still lands on
    it, and every later edit has to remember to keep refusing it. There is no
    hidden row here to reach -- `settings` is simply not among the keys, so
    nothing downstream can be handed it."""
    items = agent_menu.menu_items(busy=True)
    assert "settings" not in keys_of(items)
    assert keys_of(items) == ["start", "help", "exit"]
    assert all("Settings" != item[1] for item in items)


def test_busy_removes_settings_whether_or_not_a_session_is_waiting():
    """The two flags are independent. Work can be running while the menu was
    reached by `/back` (the usual case) and the removal must not be a side
    effect of resuming -- an implementation that keyed one off the other
    would offer Settings to exactly the person whose agents are still going."""
    for resuming in (False, True):
        items = agent_menu.menu_items(resuming=resuming, busy=True)
        assert "settings" not in keys_of(items), resuming
        assert "start" in keys_of(items), resuming


def test_a_truthy_busy_is_enough_to_remove_the_row():
    """`render_startup_frame` passes `bool(busy)` and `main_menu` passes
    `bool(running())`, so this is only ever handed a bool today -- and the
    phrase itself is the obvious thing for a later caller to pass straight
    through. It has to mean the same thing when it does."""
    assert "settings" not in keys_of(agent_menu.menu_items(busy="2 agents"))
    assert "settings" in keys_of(agent_menu.menu_items(busy=""))
    assert "settings" in keys_of(agent_menu.menu_items(busy=False))


def test_every_item_is_three_non_empty_strings_in_every_combination():
    """`render_startup_frame` unpacks `item[1]` and `item[2]` into a row and
    measures `item[1]` for the label column. An entry that was short, or
    carried an empty label, would raise or draw a blank button -- and only in
    the combination nobody rehearsed."""
    for resuming in (False, True):
        for busy in (False, True):
            items = agent_menu.menu_items(resuming=resuming, busy=busy)
            assert items, (resuming, busy)
            for item in items:
                assert len(item) == 3, item
                for part in item:
                    assert isinstance(part, str) and part.strip(), item


def test_no_combination_invents_a_key():
    """Callers branch on a fixed set of four words. A new key would fall
    through `run_startup`'s chain to the `else` and be read as "start", so a
    row that meant something else would silently begin a session."""
    launch = set(keys_of(agent_menu.MENU_ITEMS))
    for resuming in (False, True):
        for busy in (False, True):
            items = agent_menu.menu_items(resuming=resuming, busy=busy)
            assert set(keys_of(items)) <= launch, (resuming, busy)
            assert len(set(keys_of(items))) == len(items), (resuming, busy)


# --- the frame --------------------------------------------------------------

def test_the_frame_says_resume_when_a_session_is_waiting():
    """The row is the only thing telling the user the session survived. A
    frame still reading Start says the opposite of what happened, and the
    first key most people press on that screen is Enter."""
    ansi = Ansi()
    try:
        text = "\n".join(visible(row) for row in frame(resuming=True))
        assert "Resume" in text
        assert "Start" not in text
    finally:
        ansi.close()


def test_a_busy_frame_drops_settings_and_says_why():
    """A button that vanished without a word reads as a fault in TMT rather
    than as a rule, so the note has to name what is running and what would
    bring the row back. Both halves are asserted: the absence is the
    guarantee and the sentence is the explanation.

    The absence is asserted against the option ROWS rather than against the
    word, because the note itself opens with "Settings are not offered" -- a
    frame searched for the bare word would report the button present at
    exactly the moment it had been removed."""
    ansi = Ansi()
    try:
        rows = frame(busy="1 agent")
        assert "Settings" not in option_rows(rows)
        assert set(option_rows(rows)) == {"Start", "Help", "Exit"}
        text = "\n".join(visible(row) for row in rows)
        assert "1 agent" in text
        assert "/back" in text
    finally:
        ansi.close()


def test_the_busy_note_is_dim_and_sits_above_the_list():
    """Above, because it explains an absence in the list below it, and a
    sentence printed under the rows would be read as a footer about the row
    the cursor is on. Dim, because it is not a button and must not compete
    with the ones that are -- a note drawn at full weight in the middle of a
    menu is the brightest thing on a screen made of choices."""
    ansi = Ansi()
    try:
        rows = frame(resuming=True, busy="2 agents")
        note = [index for index, row in enumerate(rows)
                if "not offered while work is running" in visible(row)]
        buttons = [index for index, row in enumerate(rows)
                   if visible(row).strip().startswith(("Resume", "> Resume",
                                                       "Help", "> Help",
                                                       "Exit", "> Exit"))]
        assert note, "the frame drew no busy note"
        assert buttons, "the frame drew no option rows"
        assert max(note) < min(buttons)
        assert rows[note[0]].startswith(agent_ui.DIM)
    finally:
        ansi.close()


def test_an_idle_frame_puts_settings_back_and_drops_the_note():
    """The other half of the rule, and the one a naive implementation misses:
    a row removed while work ran has to come back when it stops, and the
    explanation for its absence has to go with it. An empty phrase is what
    says nothing is running."""
    ansi = Ansi()
    try:
        text = "\n".join(visible(row) for row in frame(resuming=True, busy=""))
        assert "Settings" in text
        assert "not offered while work is running" not in text
    finally:
        ansi.close()


def test_every_row_fits_the_terminal_in_every_combination():
    """Measured, never counted. A row drawn to the last column auto-wraps on
    the terminals that do, and costs a screen line the repaint arithmetic
    does not know about -- so the whole frame marches down the screen. The
    busy note is the new row here and it is the longest sentence on the
    screen, which is exactly the one a wrap would find."""
    ansi = Ansi()
    try:
        for stream in (Terminal(), Plain()):
            for columns in (40, 60, 80, 100, 132):
                for resuming in (False, True):
                    for busy in ("", "1 agent", "5 agents, a note, a review"):
                        rows = frame(stream=stream, size=(columns, 40),
                                     resuming=resuming, busy=busy)
                        for row in rows:
                            width = agent_ui.display_width(visible(row))
                            assert width <= columns - 1, (
                                columns, resuming, busy, width, visible(row))
    finally:
        ansi.close()


def test_the_whole_frame_reads_with_the_escapes_stripped():
    """Colour is never the message. Every word the frame is responsible for
    has to survive a terminal that shows none of it, which is every piped
    run and every reader who has set NO_COLOR."""
    ansi = Ansi()
    try:
        idle = "\n".join(visible(row) for row in frame(resuming=True, busy=""))
        assert "Resume" in idle and "Settings" in idle and "Exit" in idle
        assert agent_menu.RESUME_EXIT_DETAIL in idle
        busy = "\n".join(visible(row) for row in frame(resuming=True, busy="1 agent"))
        assert (agent_menu.BUSY_NOTE % "1 agent").split(".")[0] in busy
    finally:
        ansi.close()


def test_two_frames_of_the_same_phase_are_identical():
    """Nothing in the frame reads a hidden clock. `LiveRegion` skips a repaint
    when the composed lines are unchanged, and a frame that differed between
    two identical calls would repaint forever -- which on the menu means a
    cursor that will not sit still."""
    ansi = Ansi()
    try:
        for resuming in (False, True):
            for busy in ("", "1 agent"):
                first = frame(resuming=resuming, busy=busy)
                second = frame(resuming=resuming, busy=busy)
                assert first == second, (resuming, busy)
    finally:
        ansi.close()


# --- the Resume button's colour ---------------------------------------------

def label_span(row):
    """The part of a painted row before the first escape.

    An unselected, unanimated row is plain text up to its detail, so its
    label lands here whole. An animated one is escaped from the first column,
    so this comes back empty -- which is the discriminator these tests need,
    and it cannot be faked by the dim escape on the detail beside it.
    """
    return row.split("\033")[0]


def option_rows(rows):
    """{label: painted row} for every selectable row in a frame.

    Matched on the row's SHAPE -- `_option_row` writes a space, then the two
    columns the `>` marker lives in, then the label -- rather than on the
    words in it. The busy note begins with the word "Settings" and is
    deliberately not a row, so a looser match would find the button at
    exactly the moment it had been removed.
    """
    names = set(item[1] for item in agent_menu.MENU_ITEMS)
    names.add(agent_menu.RESUME_ITEM[1])
    found = []
    for row in rows:
        text = visible(row)
        if not (text.startswith("   ") or text.startswith(" > ")):
            continue
        label = text[3:].split("  ")[0].strip()
        if label in names:
            found.append((label, row))
    return dict(found)


def test_resume_carries_the_gradient_even_when_the_cursor_is_elsewhere():
    """This is the whole of what `live=` buys. Resume is not saying "you have
    selected me", it is saying "the session you left is still here" -- so it
    has to go on moving while the cursor is down on Exit, which is exactly
    when somebody is about to end that session. Every other unselected row
    stays plain, or the animation would say nothing at all."""
    ansi = Ansi()
    try:
        rows = option_rows(frame(selected=3, resuming=True))
        assert set(rows) >= {"Resume", "Help", "Exit"}
        assert label_span(rows["Resume"]) == ""
        assert "38;2;" in rows["Resume"]
        # Unselected and not live: the label is plain text, so it is still
        # sitting in front of the first escape on the row.
        assert "Help" in label_span(rows["Help"])
        assert "Settings" in label_span(rows["Settings"])
    finally:
        ansi.close()


def test_start_is_not_animated_at_launch():
    """The counterpart, and the reason the flag is `resuming and start`
    rather than just `start`. At launch the first row has nothing to say
    about itself -- there is no session behind it -- and a permanently
    cycling row on the opening screen would be decoration."""
    ansi = Ansi()
    try:
        rows = option_rows(frame(selected=3, resuming=False))
        assert "Start" in label_span(rows["Start"])
    finally:
        ansi.close()


def test_the_sentence_beside_resume_is_not_animated():
    """Animating a sentence somebody is reading is the thing the design rules
    refuse: the gradient is for instruments, not for prose. The label is the
    instrument. The detail stays at the one neutral, and it stays there as an
    unbroken run of text -- a cycled sentence is escaped between every
    character, so its words no longer appear in the painted row at all."""
    ansi = Ansi()
    try:
        rows = option_rows(frame(selected=3, resuming=True))
        detail = agent_menu.RESUME_ITEM[2]
        assert detail in rows["Resume"]
        assert agent_ui.DIM in rows["Resume"]
        # And the selected row, which IS fully cycled, proves the assertion
        # above can fail: its detail is not a literal substring of anything.
        selected = option_rows(frame(selected=0, resuming=True))["Resume"]
        assert detail not in selected
    finally:
        ansi.close()


def test_resume_survives_a_terminal_with_no_colour():
    """Colour is never the message, and this is the row that most looks like
    it could be. With no colour support the whole row is plain text, and the
    word the user needs is still there and still readable."""
    rows = option_rows(frame(stream=Plain(), selected=3, resuming=True))
    assert "\033" not in rows["Resume"]
    assert "Resume" in rows["Resume"]
    assert agent_menu.RESUME_ITEM[2] in rows["Resume"]


def test_live_changes_only_the_label_span():
    """`live` must not become a licence to redraw the row. What the user
    reads is identical either way, and everything after the label -- the dim
    detail, its reset -- is byte for byte the same run. Anything else and the
    flag would be changing the layout as well as the colour."""
    ansi = Ansi()
    try:
        stream = Terminal()
        arguments = (False, "Resume", agent_menu.RESUME_ITEM[2], stream, 0.2, 60, 8)
        live = agent_menu._option_row(*arguments, live=True)
        inert = agent_menu._option_row(*arguments, live=False)
        assert visible(live) == visible(inert)
        assert live != inert
        assert live.endswith(inert[inert.index("\033"):])
        assert label_span(inert).strip() == "Resume"
        assert label_span(live) == ""
    finally:
        ansi.close()


# --- main_menu, and the vanishing row ---------------------------------------

def test_enter_on_the_first_row_resumes():
    """The gesture the whole feature exists for: `/back`, look at Help or
    Settings, press Enter. The key has to be `start`, because that is the
    only word `run_startup` reads as "go and work"."""
    choice, keys = menu("enter", resuming=True)
    assert choice == "start"
    assert keys.remaining == 0


def test_down_then_enter_opens_settings_when_nothing_is_running():
    """The ordinary case, and the baseline the busy case is measured against.
    Settings is the second row and one Down reaches it."""
    choice, _ = menu("down", "enter", resuming=True)
    assert choice == "settings"


def test_down_then_enter_reaches_help_when_settings_is_gone():
    """Not because Help moved, but because Settings is not there to move
    past. The removal is real: the row below Start is Help, and one Down
    lands on it. A greyed-but-present Settings would answer "settings" here."""
    choice, _ = menu("down", "enter", resuming=True, busy=lambda: "1 agent")
    assert choice == "help"


def test_a_row_vanishing_under_the_cursor_cannot_select_exit():
    """The hazard test, and the one that kills an index-based cursor. The
    user puts the cursor on Settings while nothing is running; a background
    agent starts; the Settings row is removed; they press Enter. With the
    cursor kept as an INDEX that Enter lands on whatever slid into slot 1 --
    and with Settings gone the rows are Start, Help, Exit, so the row under a
    cursor nobody moved is Help, and one further down is Exit: the gesture
    that protects the session becomes the gesture that ends it.

    Tracking the cursor by KEY means the key it was on is simply not in the
    list any more, and the cursor falls back to the first row. Whatever else
    changes, the answer may never be "exit"."""
    work = Work()
    choice, _ = menu("down", work.start, "enter",
                     resuming=True, busy=work)
    assert choice != "exit"
    assert choice == "start"


def test_settings_comes_back_when_the_work_finishes():
    """`busy` is a callable and is re-asked on every frame, so a worker that
    finishes while the menu is open puts the row back without the user having
    to leave and come again. A plain value would freeze the answer at the
    moment the menu opened, and the only way back to Settings would be to
    quit TMT."""
    work = Work("2 agents")
    region = Frames()
    choice, _ = menu(work.finish, "down", "enter",
                     resuming=True, busy=work, region=region)
    assert choice == "settings"
    # Drawn busy first, then idle: the row was genuinely absent and came
    # back. Asked of the option rows rather than of the words, because the
    # busy note itself opens with "Settings are not offered".
    assert "Settings" not in option_rows(region.rows(0))
    assert "2 agents" in region.text(0)
    assert "Settings" in option_rows(region.rows(1))
    assert "2 agents" not in region.text(1)
    assert work.calls > 1


def test_a_busy_register_that_raises_does_not_lock_settings_away():
    """Guarded to "", which is the safe direction here rather than the
    cautious one. A register that cannot answer must not be able to take
    Settings away for the rest of the session -- the user would have no way
    to change a provider or a key, and nothing on screen would say why. It
    must also not take the menu down with it."""
    def broken():
        raise RuntimeError("the register is unavailable")

    choice, _ = menu("down", "enter", resuming=True, busy=broken)
    assert choice == "settings"


def test_a_busy_register_that_answers_nonsense_is_read_as_a_phrase():
    """`running()` coerces with `str(busy() or "")`, so a register answering
    a count rather than a phrase still removes the row instead of raising
    somewhere inside the frame builder. Zero is falsy and means idle, which
    is the reading a count would want anyway."""
    choice, _ = menu("down", "enter", resuming=True, busy=lambda: 3)
    assert choice == "help"
    choice, _ = menu("down", "enter", resuming=True, busy=lambda: 0)
    assert choice == "settings"


def test_every_way_out_of_the_menu_still_exits():
    """Esc, q, exhausted input and Ctrl-C all answer "exit", so no path
    leaves the caller waiting on a menu that has nothing left to read. This
    is what stops a scripted or broken terminal hanging the process, and
    `/back` adds two new ways to reach this screen without changing it."""
    for name in ("esc", "quit", "q"):
        choice, _ = menu(name, resuming=True)
        assert choice == "exit", name
    exhausted = agent_menu.main_menu(stream=Plain(), key_reader=lambda: None,
                                     region=nowhere(), model_id=MODEL,
                                     workspace=WORKSPACE, resuming=True)
    assert exhausted == "exit"
    reader = Interrupting()
    interrupted = agent_menu.main_menu(stream=Plain(), key_reader=reader,
                                       region=nowhere(), model_id=MODEL,
                                       workspace=WORKSPACE, resuming=True,
                                       busy=lambda: "1 agent")
    assert interrupted == "exit"
    assert reader.calls == 1


def test_the_cursor_starts_where_the_caller_put_it():
    """`run_startup` remembers which row opened a sub-screen and reopens the
    menu on it, so the opening index has to be taken against the items as
    they are NOW -- a shorter list must not be indexed off the end, and the
    cursor must always come to rest on a row that exists.

    Deliberately NOT asserting which row that is when Settings has gone.
    `run_startup` hands the index back as a hardcoded number (1 after
    Settings, 2 after Help), and with Settings removed index 2 is Exit rather
    than Help -- so opening Help from a busy menu leaves the cursor on the
    row that ends the session. That is reported rather than pinned here: it
    is the same hazard `main_menu`'s key-tracking exists to prevent, reaching
    the menu through the caller instead of through a keystroke, and a test
    asserting "exit" would make the fix look like a regression."""
    choice, _ = menu("enter", resuming=True, selected=2)
    assert choice == "help"
    busy_keys = keys_of(agent_menu.menu_items(resuming=True, busy=True))
    assert "settings" not in busy_keys
    choice, _ = menu("enter", resuming=True, selected=2, busy=lambda: "1 agent")
    assert choice in busy_keys
    # And an index past the end of the shorter list still lands on a row.
    choice, _ = menu("enter", resuming=True, selected=3, busy=lambda: "1 agent")
    assert choice in busy_keys


# --- TMT._still_running -----------------------------------------------------

def test_nothing_running_is_no_phrase():
    """The empty string is what puts the Settings row back, so it has to be
    the answer both when there is no register at all -- a piped run never
    builds one -- and when the register is simply idle."""
    assert TMT._still_running(None) == ""
    assert TMT._still_running(agent_manager.AgentManager()) == ""


def test_a_running_worker_is_counted_and_the_plural_is_right():
    """The phrase is printed into a sentence the user reads, and "1 agents"
    beside an explanation of why a button has gone is the kind of detail that
    makes the explanation itself look wrong."""
    register = agent_manager.AgentManager()
    register.spawn("one")
    assert TMT._still_running(register) == "1 agent"
    register.spawn("two")
    assert TMT._still_running(register) == "2 agents"


def test_a_note_and_a_review_are_named_although_active_count_ignores_them():
    """`active_count()` counts neither -- both live in slots of their own,
    off the fleet -- so a version that asked it alone would report nothing
    running and offer Settings while a reviewer read the tree or a note
    walked the workspace. Both are asserted, because each is a separate
    branch and one of them can be forgotten on its own."""
    register = agent_manager.AgentManager()
    register.spawn("a question", kind="note")
    assert register.active_count() == 0
    assert TMT._still_running(register) == "a note"

    register = agent_manager.AgentManager()
    register.spawn("audit the diff", kind="review")
    assert register.active_count() == 0
    assert TMT._still_running(register) == "a review"


def test_everything_running_is_named_at_once():
    """The phrase is what the note on the menu says is running, and it has to
    account for all three kinds -- a user told "1 agent" while a review is
    also going would wait for the wrong thing to finish."""
    register = agent_manager.AgentManager()
    register.spawn("one")
    register.spawn("a question", kind="note")
    register.spawn("audit the diff", kind="review")
    assert TMT._still_running(register) == "1 agent, a note, a review"


def test_finished_work_is_not_still_running():
    """Retention keeps a finished record around so its card can be read for a
    few seconds longer, and a version that counted records rather than live
    ones would keep Settings away after everything had stopped -- with no
    gesture anywhere that would bring it back."""
    register = agent_manager.AgentManager()
    worker = register.spawn("one")
    note = register.spawn("a question", kind="note")
    review = register.spawn("audit the diff", kind="review")
    assert TMT._still_running(register) != ""
    worker.status = agent_manager.Status.COMPLETED
    note.status = agent_manager.Status.FAILED
    review.status = agent_manager.Status.KILLED
    assert TMT._still_running(register) == ""


def test_a_register_that_cannot_answer_does_not_lock_settings_away():
    """Guarded to "" rather than raising, and the direction matters: an
    exception here would come out of `/back` and take the session screen
    down, and a cautious "assume busy" would leave the user unable to reach
    Settings for the rest of the run with nothing on screen explaining it."""
    class Broken:
        def active_count(self):
            raise RuntimeError("the register is unavailable")

    class HalfBroken:
        def active_count(self):
            return 1

        def note(self):
            raise RuntimeError("no note slot")

    assert TMT._still_running(Broken()) == ""
    assert TMT._still_running(HalfBroken()) == ""


# --- BottomPad.reset --------------------------------------------------------

def test_reset_recounts_the_pad_and_forgets_the_window():
    """`/back` draws the menu over the session and comes back to a cleared
    screen with a fresh header, so the distance from the cursor to the foot
    is a different number and every row the pad had spent is spent no longer.
    The recorded height has to go with it: `above()` adds the difference
    between the window now and the window the count was made against, so a
    stale height would offset every later frame by that difference forever."""
    pad = agent_menu.BottomPad(12, height=30)
    assert pad.rows == 12 and pad.height == 30
    pad.take(5)
    assert pad.rows == 7
    assert pad.reset(20) == 20
    assert pad.rows == 20
    assert pad.height is None


def test_reset_clamps_and_defaults_the_way_the_constructor_does():
    """It is the constructor's job done again on an object that already
    exists, so it has to agree with it: a negative count is zero, no argument
    is zero, and a height of zero is no height rather than a window zero rows
    tall."""
    pad = agent_menu.BottomPad(9, height=40)
    assert pad.reset(-3) == 0
    assert pad.rows == 0
    pad.reset(6, height=50)
    assert pad.height == 50
    pad.reset(6, height=0)
    assert pad.height is None
    pad.reset()
    assert pad.rows == 0


def test_reset_is_in_place_because_the_prompt_box_holds_the_object():
    """The prompt box was handed this pad when the session opened and holds
    it for the life of the session. A `reset` that rebound -- `pad =
    BottomPad(...)` in `_return_to_menu` -- would leave the box measuring
    against the count from before the screen was cleared, with nothing
    raising anywhere: the box would simply sit at the wrong height for the
    rest of the run. Same rule every state object in TMT follows, and the
    same failure the plan's `retire()` was written to avoid."""
    pad = agent_menu.BottomPad(12, height=30)
    holder = {"pad": pad}          # stands in for the prompt box's reference
    assert pad.reset(4) == 4
    assert holder["pad"] is pad
    assert holder["pad"].rows == 4
    assert holder["pad"].height is None
    # And the object goes on working: the new count is what is spent from.
    holder["pad"].take(1)
    assert pad.rows == 3
