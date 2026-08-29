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


# --- the response history ----------------------------------------------------
#
# The surface these cover is the one that had to exist: everything shown during
# a turn used to be a single row repainted in place, so each message erased the
# one before it and a finished turn had kept nothing. What follows is mostly
# about absence -- that nothing goes missing -- which is why the assertions are
# about the whole sequence rather than about the newest entry.


class Recorder(io.StringIO):
    """A stream that reports an encoding and is not a terminal."""

    encoding = "utf-8"

    def isatty(self):
        return False


TURN = (
    ("progress", "Inspecting the existing implementation."),
    ("tool", "Read 5 files"),
    ("progress", "Found the shared provider abstraction."),
    ("file_edit", "Modified src/providers.py"),
    ("progress", "Running the integration tests."),
    ("tool", "Ran 1 shell command"),
    ("success", "173 tests passed."),
    ("next_step_suggestion", "Review the changed files"),
    ("final", "The provider work is done."),
)


def played_turn(stream=None):
    """Emit the whole reference turn and hand back the transcript."""
    transcript = agent_ui.Transcript(stream=stream or Recorder())
    for kind, message in TURN:
        transcript.emit_kind(kind, message)
    return transcript


def test_every_event_of_a_turn_survives_the_events_that_follow_it():
    """The defect this replaced: each new message overwrote the last, so a
    finished turn had kept only its final line. Every entry must still be
    there, in the order it happened, once the turn is over."""
    stream = Recorder()
    transcript = played_turn(stream)
    kinds = transcript.history.kinds()
    assert kinds == tuple(kind for kind, _ in TURN), kinds

    # Present in the history is not the same as present on screen. Both --
    # except for the two kinds something else on screen already is. The final
    # answer is drawn by render_response, and the suggestion is the shadow
    # text of the next prompt box; printing either here would put a second
    # copy of it on the screen in a different style.
    shown = stream.getvalue()
    for kind, message in TURN:
        if kind in ("final", "next_step_suggestion"):
            continue
        assert message in shown, (message, shown)

    # The specific claims from the request: an earlier progress message is
    # still there after two later ones, and the tool and edit events with it.
    first, second, third = (message for kind, message in TURN if kind == "progress")
    assert shown.index(first) < shown.index(second) < shown.index(third), shown
    assert "Read 5 files" in shown and "Modified src/providers.py" in shown, shown


def test_the_suggestion_is_recorded_and_never_printed():
    """It is the shadow text of the next prompt box and nothing else. Printed
    here as well, the user would be told in the reply about a line they are
    one row away from reading under their own cursor -- and would then see it
    twice, in two different styles, saying the same thing.

    Recorded all the same: a hint that was offered was offered, and the
    history is what the turn is answerable from afterwards."""
    stream = Recorder()
    transcript = played_turn(stream)
    hint = dict((kind, message) for kind, message in TURN)["next_step_suggestion"]
    assert "next_step_suggestion" in transcript.history.kinds()
    assert transcript.history.last("next_step_suggestion").message == hint
    assert hint not in stream.getvalue(), stream.getvalue()
    assert "Next:" not in stream.getvalue(), stream.getvalue()
    assert transcript.lines_for(
        agent_ui.AgentEvent.make("next_step_suggestion", hint)) == []


def test_the_suggestion_is_the_last_thing_before_the_final_answer():
    """Placement is the whole contract for the hint: it is a lead-in to the
    answer, not a footnote after it and not a banner before the work."""
    kinds = played_turn().history.kinds()
    assert kinds[-2:] == ("next_step_suggestion", "final"), kinds


def test_an_event_is_written_once_and_never_repainted():
    """The history is the terminal's own scrollback. A second copy of a line
    would mean the renderer is repainting something it has already committed,
    which is how a scrollback stops being a record."""
    stream = Recorder()
    played_turn(stream)
    body = stream.getvalue()
    for kind, message in TURN:
        if kind in ("final", "next_step_suggestion"):
            continue        # neither is drawn here; each has its own place
        assert body.count(message) == 1, (message, body.count(message))


def test_progress_is_visibly_quieter_than_a_file_edit():
    """A terminal cannot size type, so the hierarchy has to be carried by what
    it does have. Progress is dimmed and tight; a file edit is indented, set
    apart by a blank line, and not dimmed."""
    transcript = agent_ui.Transcript(stream=Recorder())
    progress = transcript.lines_for(agent_ui.AgentEvent.make("progress", "Checking the parser."))
    edit = transcript.lines_for(
        agent_ui.AgentEvent.make("file_edit", "Modified src/parser.py", added=18, removed=4))

    assert agent_ui.PROMINENCE["progress"] < agent_ui.PROMINENCE["file_edit"]
    # Indentation is the part that survives ANSI being stripped, and it must,
    # because colour is never the message.
    progress_indent = len(visible(progress[0])) - len(visible(progress[0]).lstrip())
    edit_indent = len(visible(edit[0])) - len(visible(edit[0]).lstrip())
    assert edit_indent > progress_indent, (edit_indent, progress_indent)
    # The measured facts ride along with the edit, and are real ones.
    assert any("+18 -4" in visible(line) for line in edit), edit


def test_the_facts_on_an_event_are_only_the_ones_it_was_given():
    """Nothing on screen may be invented. An event with no counts gets no
    counts rather than a plausible-looking zero."""
    transcript = agent_ui.Transcript(stream=Recorder())
    bare = transcript.lines_for(agent_ui.AgentEvent.make("file_edit", "Touched a file"))
    assert not any(re.search(r"[+-]\d", visible(line)) for line in bare), bare


def test_the_history_cannot_be_rewritten_through_what_it_hands_out():
    """Append-only has to mean it. A caller holding the events must not be
    able to reach back into the record and change what happened."""
    history = agent_ui.ResponseHistory()
    history.append(agent_ui.AgentEvent.make("progress", "first"))
    events = history.events
    assert isinstance(events, tuple)
    try:
        events.append(agent_ui.AgentEvent.make("progress", "smuggled"))
    except AttributeError:
        pass
    assert len(history) == 1, history.events


def test_a_suggestion_over_five_words_is_refused_and_still_usable():
    """Five words is a hard limit, but a refusal cannot leave the caller with
    nothing: the trimmed form is still relevant to the turn, which a generic
    fallback would not be."""
    assert agent_ui.validate_suggestion("Run the integration tests")[0]
    assert agent_ui.validate_suggestion("Review changed files")[0]
    ok, cleaned, reason = agent_ui.validate_suggestion("Please run the integration tests now")
    assert not ok and reason
    assert agent_ui.count_words(cleaned) <= agent_ui.MAX_SUGGESTION_WORDS, cleaned
    assert cleaned, "a refused suggestion must still leave something showable"


def test_words_are_counted_the_way_a_reader_counts_them():
    """Punctuation is not a word, and a hyphenated form is one."""
    assert agent_ui.count_words("Run the tests.") == 3
    assert agent_ui.count_words("Run the integration tests") == 4
    assert agent_ui.count_words("Please run the integration tests now") == 6
    assert agent_ui.count_words("Re-run the test-suite") == 3
    assert agent_ui.count_words("") == 0
    assert agent_ui.count_words(None) == 0


def test_a_fallback_suggestion_never_claims_work_that_was_not_done():
    """The hint is generated when the model gave none, so it has to be read
    off the turn rather than guessed. A turn that ran no tests must not be
    told to go and look at test results."""
    empty = agent_ui.ResponseHistory()
    assert "test" not in agent_ui.fallback_suggestion(empty).lower()

    edited = agent_ui.ResponseHistory()
    edited.append(agent_ui.AgentEvent.make("file_edit", "Modified a.py"))
    assert agent_ui.fallback_suggestion(edited) == "Review the changed files"

    tested = agent_ui.ResponseHistory()
    tested.append(agent_ui.AgentEvent.make("test", "9 passed"))
    assert "test" in agent_ui.fallback_suggestion(tested).lower()

    for history in (None, empty, edited, tested):
        text = agent_ui.fallback_suggestion(history)
        assert agent_ui.validate_suggestion(text)[0], text


def test_an_event_the_model_malformed_is_kept_rather_than_dropped():
    """The model is not a trusted source of well-formed data, and a message it
    meant the user to see must not vanish because its type was misspelt."""
    assert agent_ui.AgentEvent.from_payload("not a dict") is None
    assert agent_ui.AgentEvent.from_payload({"type": "progress"}) is None
    assert agent_ui.AgentEvent.from_payload({"message": 7}) is None

    odd = agent_ui.AgentEvent.from_payload({"type": "invented", "message": "still say it"})
    assert odd is not None and odd.kind == "progress", odd
    assert odd.detail.get("reported_kind") == "invented", odd.detail
    assert odd.message == "still say it"


def test_the_transcript_degrades_to_ascii_rather_than_emitting_junk():
    """A console that cannot encode the markers gets a deliberate ASCII
    interface. A row of replacement marks reads as a bug."""
    narrow = NarrowStream()
    transcript = agent_ui.Transcript(stream=narrow)
    for kind in ("progress", "success", "warning", "error", "file_edit", "test"):
        for line in transcript.lines_for(agent_ui.AgentEvent.make(kind, "a message")):
            line.encode(narrow.encoding)        # raises if a glyph cannot be carried


def test_a_row_of_the_transcript_never_reaches_the_last_column():
    """Terminals disagree about what a row filled to the last column does, and
    a wrapped row costs a screen line the caller did not account for."""
    original = with_columns(40)
    try:
        transcript = agent_ui.Transcript(stream=Recorder())
        long_message = "a considerable message " * 8
        for kind in ("progress", "success", "file_edit", "next_step_suggestion"):
            for line in transcript.lines_for(agent_ui.AgentEvent.make(kind, long_message)):
                assert display_width(visible(line)) < 40, (kind, visible(line))
    finally:
        agent_ui.shutil.get_terminal_size = original
