import io
import re
import shutil
import time

import agent_ui
from agent_ui import LiveUI, display_width, fit_to_width, render_response

ANSI_RE = re.compile("\033\[[0-9;]*m")


def visible(line):
    """The row as the terminal shows it, with colour escapes removed."""
    return ANSI_RE.sub("", line)


def with_columns(columns):
    """Pin the terminal size the UI reads. Returns the original to restore."""
    original = agent_ui.shutil.get_terminal_size
    agent_ui.shutil.get_terminal_size = (
        lambda default=None: shutil.os.terminal_size((columns, 24))
    )
    return original


FULL_WIDTH = "\uff34\uff28\uff29\uff2e\uff2b\uff29\uff2e\uff27"   # full-width THINKING


def test_thinking_transitions_to_progress_and_events_never_complete():
    output = io.StringIO()
    ui = LiveUI(stream=output, interval=0.01)
    ui.start()
    assert "THINKING" in output.getvalue()
    ui.meaningful_output()
    assert ui.progress_started
    ui.intermediate_event()
    assert ui._progress < 100
    ui.stop()


def test_final_sequence_renders_complete_before_response():
    output = io.StringIO()
    ui = LiveUI(stream=output)
    ui.start()
    ui.meaningful_output()
    ui.final_event()
    assert ui._progress == 95
    ui.complete()
    render_response("hello\n```py\nprint('x')\n```", output)
    text = output.getvalue()
    assert text.index("100% Complete!") < text.index("hello")
    assert "print('x')" in text


def test_animation_changes_without_blocking():
    output = io.StringIO()
    ui = LiveUI(stream=output, interval=0.01)
    ui.start()
    time.sleep(0.03)
    ui.stop()
    assert output.getvalue().count("\r") >= 2


def test_activity_readout_sits_against_the_right_edge():
    columns = 100
    original = with_columns(columns)
    ui = LiveUI(stream=io.StringIO())
    try:
        ui.start()
        ui.meaningful_output()
        ui.add_output(363 * 4)
        ui._render_progress("Respond")
        row = ui._last_render
        assert "thinking" in row and "363 tokens" in row
        # Progress bar hard left, readout hard right, one row, no overflow.
        assert row[0] in "\u2588\u2591"
        assert row.endswith(")")
        assert display_width(row) == columns - 1
        assert "\n" not in row
    finally:
        agent_ui.shutil.get_terminal_size = original
        ui.stop()


def test_readout_is_dropped_whole_when_the_row_is_too_narrow():
    original = with_columns(28)
    ui = LiveUI(stream=io.StringIO())
    try:
        ui.start()
        ui.meaningful_output()
        ui.add_output(363 * 4)
        ui._render_progress("A label long enough to crowd the row")
        row = ui._last_render
        assert "thinking" not in row          # dropped whole, never half-drawn
        assert display_width(row) <= 27
    finally:
        agent_ui.shutil.get_terminal_size = original
        ui.stop()


def test_token_count_accumulates_then_resets_with_the_next_task():
    ui = LiveUI(stream=io.StringIO())
    ui.start()
    ui.meaningful_output()
    for _ in range(363):
        ui.add_output(4)
    assert "363 tokens" in ui._activity()
    ui.stop()
    ui.start()                                # a new task starts from zero
    ui.meaningful_output()
    assert "tokens" not in ui._activity()
    ui.stop()


def test_readout_appears_with_the_progress_bar_and_not_before():
    original = with_columns(100)
    ui = LiveUI(stream=io.StringIO())
    try:
        ui.start()
        ui.add_output(363 * 4)
        assert ui._activity() == ""            # THINKING owns the row alone
        assert "thinking\u2026" not in ui._last_render
        assert "363" not in ui._last_render
        ui.meaningful_output()                 # the bar takes the row
        assert "thinking\u2026" in ui._activity()
        assert "363 tokens" in ui._last_render
    finally:
        agent_ui.shutil.get_terminal_size = original
        ui.stop()


def test_finished_row_drops_the_in_flight_readout():
    original = with_columns(100)
    try:
        ui = LiveUI(stream=io.StringIO())
        ui.start()
        ui.meaningful_output()
        ui.add_output(363 * 4)
        ui.complete()
        assert ui._activity() == ""
        assert "thinking" not in ui._last_render
        assert "100% Complete!" in ui._last_render
    finally:
        agent_ui.shutil.get_terminal_size = original


def test_a_full_width_thinking_style_cannot_overflow_the_row():
    """Full-width styles are two columns per character, so a row trimmed by
    character count would spill over and wrap onto a second screen line."""
    original = with_columns(40)
    ui = LiveUI(stream=io.StringIO())
    try:
        ui.start()
        ui._render(FULL_WIDTH * 6)
        assert display_width(ui._last_render) <= 39
    finally:
        agent_ui.shutil.get_terminal_size = original
        ui.stop()


def test_painted_row_carries_no_more_columns_than_the_plain_one():
    """Colour escapes must not count toward the row width."""
    original = with_columns(80)
    ui = LiveUI(stream=io.StringIO())
    try:
        ui.start()
        ui.meaningful_output()
        ui.add_output(42 * 4)
        ui._render_progress("Respond")
        painted = ui.stream.getvalue().rsplit("\r", 1)[-1]
        assert display_width(visible(painted).lstrip("\033[2K")) <= 79
    finally:
        agent_ui.shutil.get_terminal_size = original
        ui.stop()


def test_fit_to_width_measures_columns_not_characters():
    assert fit_to_width("abc", 10) == "abc"
    assert display_width(fit_to_width(FULL_WIDTH, 5)) <= 5
    assert display_width(fit_to_width("hello world", 5)) == 5


class NarrowStream(io.TextIOBase):
    """A stream that genuinely cannot encode the decorative glyphs.

    Writing through a pipe on Windows produces exactly this: stdout falls back
    to cp1252, and every box-drawing or block character raises on the way out.
    """

    encoding = "cp1252"

    def __init__(self):
        self.chunks = []

    def write(self, text):
        text.encode(self.encoding)          # raises just as the console does
        self.chunks.append(text)
        return len(text)

    def flush(self):
        pass

    def getvalue(self):
        return "".join(self.chunks)


def test_a_stream_that_cannot_encode_the_glyphs_does_not_end_the_run():
    """Decoration must never cost the task.

    The progress bar wrote block characters straight to stdout with no
    fallback, so a cp1252 stream raised UnicodeEncodeError out of the render
    and killed TMT after the work was already done.
    """
    original = with_columns(100)
    out = NarrowStream()
    try:
        assert agent_ui.plain_output(out) is True
        ui = LiveUI(stream=out, interval=0.01)
        ui.start()
        ui.meaningful_output()
        ui.add_output(800)
        ui._render_progress("Git Push")
        ui.final_event()
        ui.complete()
        # The reply is the model's, so it carries whatever Unicode it likes
        # no matter which glyphs the interface picked for itself.
        render_response("Committed a841564 ✓ pushed 🚀 to main.", out)
        text = out.getvalue()
        assert "100% Complete!" in text
        assert "Committed a841564" in text and "to main." in text
        # The interface chose ASCII rather than emitting replacement marks.
        assert "#" in text and "█" not in text
    finally:
        agent_ui.shutil.get_terminal_size = original


def test_the_completion_line_is_drawn_exactly_once():
    """The animation thread used to repaint the finished row after complete()
    had already drawn it, doubling it wherever no cursor could overwrite."""
    original = with_columns(100)
    out = NarrowStream()
    try:
        ui = LiveUI(stream=out, interval=0.01)
        ui.start()
        ui.meaningful_output()
        time.sleep(0.05)                    # let the animation run a few ticks
        ui.complete()
        time.sleep(0.05)                    # and prove it does not come back
        assert out.getvalue().count("100% Complete!") == 1
    finally:
        agent_ui.shutil.get_terminal_size = original


def test_the_response_box_is_sized_by_columns_not_characters():
    """A reply containing wide characters used to overflow the row and wrap,
    because the box was wrapped with len() but drawn with box characters."""
    for columns in (40, 60, 100):
        original = with_columns(columns)
        try:
            buffer = io.StringIO()
            render_response("wide: \u4f60\u597d\u4e16\u754c and plain text " * 4, buffer)
            rows = [row for row in buffer.getvalue().split("\n") if row]
            widths = {display_width(row) for row in rows}
            assert len(widths) == 1, (columns, sorted(widths))
            assert widths.pop() < columns, columns
        finally:
            agent_ui.shutil.get_terminal_size = original


def test_the_response_box_survives_a_reply_with_no_content():
    original = with_columns(80)
    try:
        buffer = io.StringIO()
        render_response("", buffer)
        rows = [row for row in buffer.getvalue().split("\n") if row]
        assert len(rows) == 3                # top, one empty body row, bottom
        assert len({display_width(row) for row in rows}) == 1
    finally:
        agent_ui.shutil.get_terminal_size = original


def test_an_undeclared_encoding_counts_as_capable():
    """An in-memory buffer holds str, so nothing can fail to encode into it.
    Treating a missing encoding as incapable made every test stream ASCII."""
    assert agent_ui.encodable(io.StringIO(), agent_ui.DECORATION) is True
    assert agent_ui.plain_output(io.StringIO()) is False
    assert agent_ui.plain_output(NarrowStream()) is True


def test_safe_write_reports_a_dead_stream_instead_of_raising():
    class Closed(io.TextIOBase):
        encoding = "utf-8"
        def write(self, text):
            raise ValueError("I/O operation on closed file")

    assert agent_ui.safe_write(Closed(), "anything") is False
    assert agent_ui.safe_write(io.StringIO(), "fine") is True

    # Content the stream cannot encode is degraded, never raised: this is the
    # guard that keeps a model reply full of emoji from ending the run.
    narrow = NarrowStream()
    assert agent_ui.safe_write(narrow, "rocket 🚀 done") is True
    assert "rocket" in narrow.getvalue() and "done" in narrow.getvalue()
