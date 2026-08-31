"""Typing while the agent works, and keeping the box on screen while it does.

Two defects with the same shape: the prompt box stopped being a prompt box at
the moment the user most wanted one. `/note` cleared it and then blocked for a
whole model round trip, so the screen looked like TMT had exited; and for the
length of a turn the box was inert, so anything the user thought of had to wait
until the work was over before it could even be typed.

Nothing here may dispatch anything on its own. A queued line is held until the
session loop is somewhere it can take one -- that is what keeps a background
reader from being able to reorder itself past a running turn.
"""

import io
import threading
import time

import agent_commands
import agent_menu
import TMT


class Keys:
    """A scripted key reader. Returns "" once exhausted, like a real tick."""

    def __init__(self, *strokes):
        self.strokes = list(strokes)
        self.lock = threading.Lock()

    def __call__(self, *args, **kwargs):
        with self.lock:
            if self.strokes:
                return self.strokes.pop(0)
        return ""


def drain(reader, typeahead, expected, timeout=2.0):
    """Wait until the scripted keys have been consumed, or give up.

    A real ceiling rather than a sleep: the reader is a thread, and a test
    that slept a fixed time would either be slow or be flaky depending on the
    machine it ran on.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with reader.lock:
            done = not reader.strokes
        if done and expected():
            return True
        time.sleep(0.01)
    return False


# --- typing while a turn runs ------------------------------------------------

def test_keys_typed_during_a_turn_reach_the_box():
    reader = Keys(*"hello")
    typed = agent_menu.TypeAhead(reader=reader)
    assert typed.start()
    try:
        assert drain(reader, typed, lambda: typed.text()[0] == "hello"), typed.text()
        value, cursor = typed.text()
        assert value == "hello" and cursor == 5, (value, cursor)
        # Nothing has been submitted, so nothing is queued.
        assert typed.pending() == 0
    finally:
        typed.stop()


def test_enter_queues_the_line_and_never_dispatches_it():
    """The whole safety property. Enter puts the line on a list and does
    nothing else; the session loop drains it when the turn is over. A queued
    task cannot interrupt a turn and cannot reorder itself past one."""
    reader = Keys(*list("first") + ["\r"] + list("second") + ["\r"])
    typed = agent_menu.TypeAhead(reader=reader)
    assert typed.start()
    try:
        assert drain(reader, typed, lambda: typed.pending() == 2), typed.pending()
        assert typed.take() == ["first", "second"]
        # Taken once. A second call must not hand the same work out again.
        assert typed.take() == []
        # And the line is empty again, ready for the next one.
        assert typed.text()[0] == ""
    finally:
        typed.stop()


def test_a_note_can_be_typed_during_a_turn_like_any_other_line():
    reader = Keys(*list("/note where is the retry limit") + ["\r"])
    typed = agent_menu.TypeAhead(reader=reader)
    assert typed.start()
    try:
        assert drain(reader, typed, lambda: typed.pending() == 1)
        assert typed.take() == ["/note where is the retry limit"]
    finally:
        typed.stop()


def test_editing_keys_work_in_the_queued_line():
    reader = Keys(*list("helo") + ["\x7f", "l", "o"])
    typed = agent_menu.TypeAhead(reader=reader)
    assert typed.start()
    try:
        assert drain(reader, typed, lambda: typed.text()[0] == "hello"), typed.text()
    finally:
        typed.stop()


def test_esc_clears_the_line_without_stopping_the_turn():
    """Esc is the line's, not the turn's. Ctrl-C is the turn's."""
    reader = Keys(*list("mistake") + ["\x1b"])
    typed = agent_menu.TypeAhead(reader=reader)
    assert typed.start()
    try:
        assert drain(reader, typed, lambda: typed.text()[0] == "")
        assert typed.pending() == 0, typed.pending()
        assert typed.active, "esc must not stop the reader"
    finally:
        typed.stop()


def test_ctrl_c_is_put_back_where_it_would_have_landed():
    """The one thing reading keys during a turn could quietly break.

    Nothing read stdin while the agent worked before, so Ctrl-C reached the
    console and Python raised KeyboardInterrupt in the main thread, which the
    loop catches and turns into "Task cancelled". A raw read consumes the
    keystroke instead -- msvcrt hands back "\\x03" as an ordinary character and
    no signal is ever raised -- so the reader has to put the exception back.
    """
    reader = Keys("\x03")
    raised = []
    typed = agent_menu.TypeAhead(reader=reader,
                                 interrupt=lambda: raised.append(True))
    assert typed.start()
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not raised:
            time.sleep(0.01)
    finally:
        typed.stop()
    assert raised, "Ctrl-C during a turn must still reach the main thread"
    # It is not swallowed into the line either: nothing was typed and nothing
    # was queued, because a cleared input line is a bad trade for a working
    # Ctrl-C and nobody asked for it.
    assert typed.text()[0] == "", typed.text()
    assert typed.take() == []


def test_the_real_interrupt_is_the_default():
    """The injectable one exists only so the test above cannot fire an
    asynchronous KeyboardInterrupt into a suite with no isolation. The
    default must still be the real thing."""
    assert agent_menu.TypeAhead().interrupt is agent_menu._interrupt_main


def test_a_run_with_no_raw_keys_reads_nothing_at_all():
    """`is_interactive` gates it, for the reason it gates everything else
    here: a wrong "yes" hangs the process on a read that can never be
    answered. Every piped, redirected and scripted run keeps the inert box it
    always had."""
    class NotATty(io.StringIO):
        def isatty(self):
            return False

    typed = agent_menu.TypeAhead(stream=NotATty(), instream=NotATty())
    assert typed.start() is False
    assert not typed.active
    assert typed.take() == []


def test_stop_waits_for_the_reader_to_leave_the_read():
    """Waited on rather than abandoned: the next thing the loop does is read
    keys on the main thread, and two readers on one stdin would take it in
    turns to swallow the user's characters."""
    typed = agent_menu.TypeAhead(reader=Keys())
    assert typed.start()
    assert typed.stop() is True
    assert not typed.active


def test_a_change_asks_the_region_to_repaint():
    """Without this the box would only redraw when the reply moved, and
    typing would appear in bursts."""
    beats = []
    reader = Keys(*"hi")
    typed = agent_menu.TypeAhead(reader=reader, on_change=lambda: beats.append(1))
    assert typed.start()
    try:
        assert drain(reader, typed, lambda: typed.text()[0] == "hi")
        assert beats, "a keystroke must mark the region dirty"
    finally:
        typed.stop()


def test_a_repaint_that_raises_never_ends_the_reader():
    """Decoration is never allowed to end anything."""
    reader = Keys(*"hi")

    def broken():
        raise RuntimeError("no")

    typed = agent_menu.TypeAhead(reader=reader, on_change=broken)
    assert typed.start()
    try:
        assert drain(reader, typed, lambda: typed.text()[0] == "hi")
        assert typed.active
    finally:
        typed.stop()


# --- what the box draws while it is being typed into -------------------------

class Tty(io.StringIO):
    def isatty(self):
        return True

    @property
    def encoding(self):
        return "utf-8"


def test_the_running_box_draws_what_is_being_typed():
    from agent_ui import strip_ansi

    stream = Tty()
    box = agent_menu.PromptBox(stream=stream)
    reader = Keys(*"add a test")
    typed = agent_menu.TypeAhead(reader=reader, stream=stream)
    box.typeahead = typed
    assert typed.start()
    try:
        assert drain(reader, typed, lambda: typed.text()[0] == "add a test")
        rows = [strip_ansi(row) for row in box.running_lines(
            agent_menu.TYPING_HINT, size=(100, 24))]
        assert any("add a test" in row for row in rows), rows
        # On the marker row, where a typed line belongs.
        assert any(row.startswith(" > ") and "add a test" in row
                   for row in rows), rows
    finally:
        typed.stop()


def test_an_empty_running_box_says_it_can_be_typed_into():
    """The old hint said only how to stop the work, which was the whole truth
    when the box was inert and is half of it now."""
    from agent_ui import strip_ansi

    stream = Tty()
    box = agent_menu.PromptBox(stream=stream)
    typed = agent_menu.TypeAhead(reader=Keys(), stream=stream)
    box.typeahead = typed
    assert typed.start()
    try:
        rows = " ".join(strip_ansi(row) for row in box.running_lines(
            "Working. Ctrl-C to stop.", size=(100, 24)))
        assert "Type to queue" in rows, rows
    finally:
        typed.stop()


def test_a_box_with_lines_waiting_says_how_many():
    """It states the number, for the reason the folded-paste token states its
    size: a placeholder that hid the amount leaves the user guessing whether
    what they typed twenty seconds ago actually landed."""
    from agent_ui import strip_ansi

    stream = Tty()
    box = agent_menu.PromptBox(stream=stream)
    reader = Keys(*list("one") + ["\r"] + list("two") + ["\r"])
    typed = agent_menu.TypeAhead(reader=reader, stream=stream)
    box.typeahead = typed
    assert typed.start()
    try:
        assert drain(reader, typed, lambda: typed.pending() == 2)
        rows = " ".join(strip_ansi(row) for row in box.running_lines(
            "Working. Ctrl-C to stop.", size=(100, 24)))
        assert "2 queued" in rows, rows
    finally:
        typed.stop()


def test_the_box_is_unchanged_when_no_reader_is_attached():
    """A box with no type-ahead behaves exactly as it did before any of this
    existed, which is what every piped run and every existing test gets."""
    stream = Tty()
    box = agent_menu.PromptBox(stream=stream)
    assert box.typeahead is None
    before = box.running_lines("Working. Ctrl-C to stop.", size=(100, 24))
    assert any("Ctrl-C to stop" in row for row in
               [__import__("agent_ui").strip_ansi(r) for r in before]), before


# --- a slow command keeps the screen looking like a screen -------------------

def test_a_blocking_command_holds_the_box_up_while_it_runs():
    """`PromptBox.ask` clears its region the moment it returns. For the five
    commands that only read settings that is invisible. `/note` is a model
    round trip, so the same code left the user looking at a terminal with no
    prompt box in it for several seconds, which reads as TMT having exited
    rather than as TMT thinking."""
    stream = Tty()
    box = agent_menu.PromptBox(stream=stream)
    drawn = []

    def run():
        # Whatever the region painted before the answer came back is what the
        # user was looking at for the length of the command.
        drawn.append(stream.getvalue())
        return agent_commands.Result("Note", ["answered"])

    result = TMT._blocking_command(run, box, None)
    assert result.text().startswith("Note"), result.text()
    painted = drawn[0]
    assert painted.strip(), "the screen was left empty while the command ran"
    # The box was there: its marker row and its rules.
    assert ">" in painted, painted


def test_the_region_is_taken_down_before_the_answer_is_printed():
    """`render_command` writes straight to the stream, and printing past a
    live region leaves its repaint arithmetic pointing at rows that have
    moved."""
    stream = Tty()
    box = agent_menu.PromptBox(stream=stream)
    TMT._blocking_command(lambda: agent_commands.Result("Note", ["x"]), box, None)
    written = stream.getvalue()
    # Asserted on the LAST thing written, not on an escape appearing anywhere:
    # the paint is full of escapes, so "an escape reached the stream" is true
    # whether or not the region was ever taken down. A paint hides the cursor
    # and only `clear` gives it back, so the stream ending with the show-cursor
    # sequence is the region having actually let go of the terminal.
    assert written.endswith("\033[?25h"), repr(written[-40:])


def test_a_command_that_raises_still_gives_the_terminal_back():
    """Whatever happened -- an answer, a timeout, or Ctrl-C -- the terminal
    goes back before anything else is written to it."""
    stream = Tty()
    box = agent_menu.PromptBox(stream=stream)

    def boom():
        raise RuntimeError("no")

    try:
        TMT._blocking_command(boom, box, None)
    except RuntimeError:
        pass
    else:
        raise AssertionError("the error should not have been swallowed")
    assert stream.getvalue().endswith("\033[?25h"), repr(stream.getvalue()[-40:])


def test_a_slow_command_runs_through_the_wrapper_and_a_fast_one_does_not():
    """Only `/note` blocks long enough to be worth holding the screen for."""
    import agent_manager

    wrapped = []
    manager = agent_manager.AgentManager()

    def slow(run):
        wrapped.append(1)
        return run()

    saved = agent_commands.run_note
    try:
        agent_commands.run_note = lambda *a, **k: agent_commands.Result("Note", [])
        TMT._dispatch_command("/note where", None, manager, slow=slow)
        assert wrapped == [1], wrapped
        TMT._dispatch_command("/config", None, manager, slow=slow)
        assert wrapped == [1], "a settings command must not hold the screen"
    finally:
        agent_commands.run_note = saved


def test_dispatch_still_works_with_no_wrapper_at_all():
    """With none supplied a slow command is simply called, so this stays
    drivable with no terminal -- which is what a piped run and this suite do."""
    import agent_manager

    saved = agent_commands.run_note
    try:
        agent_commands.run_note = lambda *a, **k: agent_commands.Result("Note", ["ok"])
        result = TMT._dispatch_command("/note where", None,
                                       agent_manager.AgentManager())
        assert result is not None and result.ok, result
    finally:
        agent_commands.run_note = saved
