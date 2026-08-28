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
        ui.add_tokens(363)
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
        ui.add_tokens(363)
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
    for _ in range(363):
        ui.add_tokens()
    assert "363 tokens" in ui._activity()
    ui.stop()
    ui.start()                                # a new task starts from zero
    assert "tokens" not in ui._activity()
    ui.stop()


def test_finished_row_drops_the_in_flight_readout():
    original = with_columns(100)
    try:
        ui = LiveUI(stream=io.StringIO())
        ui.start()
        ui.meaningful_output()
        ui.add_tokens(363)
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
        ui.add_tokens(42)
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
