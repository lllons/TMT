import io
import time

from agent_ui import LiveUI, render_response


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
