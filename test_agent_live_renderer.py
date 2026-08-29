"""Tests for the character-by-character reveal used by the live relay."""

import io
import re
import threading
import time

from agent_live_renderer import (
    ASCII_SYMBOL_POOL, GLITCH_REVEAL_DURATION, GlitchStream, LiveRegion,
    LiveRelay, SYMBOL_POOL, display_width, iter_graphemes, wrap_lines,
)

ANSI_RE = re.compile("\033\[[0-9;]*m")
# Every escape, not only the colour ones. The region also writes cursor moves
# and the show/hide pair, and "\033[?25l" carries a "?" that a test looking
# for a stray symbol in the text would otherwise find in a control sequence.
ESCAPE_RE = re.compile("\033\[[0-9;?]*[A-Za-z]")


def plain(text):
    """What the terminal shows, with every control sequence removed."""
    return ESCAPE_RE.sub("", text)


def drain(glitch, limit=5.0):
    deadline = time.monotonic() + limit
    while glitch.pending_count() and time.monotonic() < deadline:
        glitch.tick()
        time.sleep(0.004)
    return glitch.text


def test_new_characters_start_as_symbols_from_the_pool():
    glitch = GlitchStream()
    glitch.feed("Hello")
    display = glitch.display_text()
    assert display != "Hello"
    assert len(iter_graphemes(display)) == 5
    assert all(symbol in SYMBOL_POOL for symbol in iter_graphemes(display))


def test_each_character_gets_its_own_symbol():
    glitch = GlitchStream()
    glitch.feed("A" * 400)
    assert len(set(iter_graphemes(glitch.display_text()))) > 5


def test_a_symbol_is_stable_until_its_character_resolves():
    glitch = GlitchStream()
    glitch.feed("abc")
    first = glitch.display_text()
    assert glitch.display_text() == first
    glitch.tick()
    assert glitch.display_text() == first


def test_characters_resolve_left_to_right():
    glitch = GlitchStream()
    glitch.feed("abcdef")
    seen = []
    deadline = time.monotonic() + 2
    while glitch.pending_count() and time.monotonic() < deadline:
        glitch.tick()
        seen.append(glitch.text)
        time.sleep(0.004)
    revealed = [text for text in seen if text]
    assert all(later.startswith(earlier) for earlier, later in zip(revealed, revealed[1:]))
    assert "abcdef".startswith(revealed[0])
    assert glitch.text == "abcdef"


def test_reveal_takes_about_the_configured_duration_per_character():
    glitch = GlitchStream()
    glitch.feed("abcdefgh")
    start = time.monotonic()
    drain(glitch)
    elapsed = time.monotonic() - start
    assert 8 * GLITCH_REVEAL_DURATION * 0.5 < elapsed < 8 * GLITCH_REVEAL_DURATION * 3


def test_whitespace_is_never_disguised():
    glitch = GlitchStream()
    glitch.feed(" \n\t")
    assert glitch.display_text() == " \n\t"
    assert glitch.pending_count() == 0


def test_spaces_inside_text_stay_spaces():
    glitch = GlitchStream()
    glitch.feed("Hello world")
    # By grapheme, not by string index: a symbol standing in for one character
    # can itself be several code points, which shifts every later index.
    assert iter_graphemes(glitch.display_text())[5] == " "


def test_text_is_exact_at_every_moment():
    glitch = GlitchStream()
    for chunk in ("Hel", "lo ", "wor", "ld"):
        glitch.feed(chunk)
        assert "Hello world".startswith(glitch.exact_text())
    assert glitch.exact_text() == "Hello world"
    assert drain(glitch) == "Hello world"


def test_code_blocks_resolve_to_the_exact_source():
    code = 'print("Hello")\nif x <= 3:\n    return {"a": 1}\n'
    glitch = GlitchStream()
    glitch.feed(code)
    assert drain(glitch) == code


def test_unicode_is_not_corrupted():
    text = "héllo 你好 😀 👨‍👩‍👦 é"
    glitch = GlitchStream()
    glitch.feed(text)
    assert glitch.exact_text() == text
    assert drain(glitch) == text


def test_very_small_and_very_large_chunks_keep_order():
    text = "".join(chr(97 + index % 26) for index in range(3000))
    glitch = GlitchStream()
    for char in text[:50]:
        glitch.feed(char)
    glitch.feed(text[50:])
    assert drain(glitch) == text


def test_large_backlog_resolves_quickly_without_dropping_text():
    text = "x" * 5000
    glitch = GlitchStream()
    glitch.feed(text)
    start = time.monotonic()
    result = drain(glitch)
    assert result == text
    assert time.monotonic() - start < 1.0


def test_empty_feed_is_ignored():
    glitch = GlitchStream()
    glitch.feed("")
    assert glitch.pending_count() == 0
    assert glitch.display_text() == ""


def test_reveal_all_finishes_the_queue():
    glitch = GlitchStream()
    glitch.feed("pending text")
    assert glitch.reveal_all() == "pending text"
    assert glitch.pending_count() == 0
    assert glitch.display_text() == "pending text"


def test_ascii_fallback_pool_is_used_when_symbols_are_unsupported():
    glitch = GlitchStream(symbols=ASCII_SYMBOL_POOL)
    glitch.feed("abc")
    assert all(symbol in ASCII_SYMBOL_POOL for symbol in glitch.display_text())
    assert drain(glitch) == "abc"


def test_region_repaints_in_place_instead_of_reprinting():
    output = io.StringIO()
    region = LiveRegion(stream=output, ansi=True)
    region.paint(["one", "two"])
    region.paint(["one", "three"])
    text = output.getvalue()
    assert text.count("\033[2A") == 1
    assert text.count("three") == 1
    region.clear()
    # The rows are handed back with the caret on them. The region hid it for
    # the length of each repaint -- a caret dragged up and down through rows
    # nobody is typing in is what the flicker was -- and clearing is the
    # region giving the terminal up, so the last thing it writes is the caret
    # coming back.
    assert output.getvalue().endswith("\033[2A\033[?25h")


def test_a_repaint_never_draws_the_caret_on_a_row_being_repainted():
    """The bug this exists for was visible from across the room: a caret
    flickering at the foot of the terminal several times a second while
    nothing was being typed.

    The cause is that a repaint walks the cursor up through every row of the
    region and back down again, and the terminal draws it at each stop. So the
    caret is switched off inside the same write as the moves it is hiding --
    there is no window in which the terminal has been told to move it but not
    yet told to stop drawing it -- and switched back on only by whoever owns
    it next: the prompt box, once it has put it in the row being typed in, or
    `clear`, which is the region giving the terminal up.
    """
    output = io.StringIO()
    region = LiveRegion(stream=output, ansi=True)
    region.paint(["one", "two"])
    first = output.getvalue()
    assert first.startswith("\033[?25l"), repr(first[:12])
    assert "\033[?25h" not in first, repr(first)

    # And it stays off across repaints rather than being toggled per frame:
    # a caret switched on and off twelve times a second is the same flicker.
    output.truncate(0), output.seek(0)
    region.paint(["one", "three"])
    assert output.getvalue().count("\033[?25h") == 0, repr(output.getvalue())

    # Whoever takes the caret back gets it back.
    output.truncate(0), output.seek(0)
    region.show_cursor()
    assert output.getvalue() == "\033[?25h", repr(output.getvalue())
    # Asked twice, it is not written twice: it is already the caret's owner.
    output.truncate(0), output.seek(0)
    region.show_cursor()
    assert output.getvalue() == "", repr(output.getvalue())


def test_permanent_text_printed_past_the_region_keeps_the_caret_hidden():
    """`write_above` erases the region, prints, and paints it again three rows
    further down. Handing the caret back for the length of that print would
    put the flicker straight back -- once per progress line, which during a
    turn is most of them."""
    output = io.StringIO()
    region = LiveRegion(stream=output, ansi=True)
    region.paint(["status"])
    output.truncate(0), output.seek(0)
    assert region.write_above("Patched file: config.py") is True
    written = output.getvalue()
    assert "Patched file: config.py" in written
    assert "\033[?25h" not in written, repr(written)


def test_region_shrinks_cleanly():
    output = io.StringIO()
    region = LiveRegion(stream=output, ansi=True)
    region.paint(["a", "b", "c"])
    region.paint(["a"])
    assert region._drawn == 1


def test_relay_draws_a_response_box_and_finalizes_once():
    output = io.StringIO()
    relay = LiveRelay(stream=output, ansi=True)
    relay.set_status("███░░░ 35% Processing...")
    relay.feed("Inspecting the repository")
    assert relay.finish() == "Inspecting the repository"
    text = output.getvalue()
    assert "35% Processing..." in text
    assert "┌" in text and "『" in text
    assert not relay.running


def test_finalized_output_contains_no_leftover_symbols():
    output = io.StringIO()
    relay = LiveRelay(stream=output, ansi=True, symbols=ASCII_SYMBOL_POOL)
    relay.feed("Decoded")
    relay.finish()
    final_box = plain(output.getvalue()).rsplit("┌", 1)[-1]
    assert not set(final_box) & set("+*#@%&^~<>?")


def test_relay_feed_never_blocks_on_the_animation():
    relay = LiveRelay(stream=io.StringIO(), ansi=True)
    start = time.monotonic()
    for _ in range(200):
        relay.feed("chunk of streamed text ")
    assert time.monotonic() - start < 0.5
    assert relay.finish() == "chunk of streamed text " * 200


def test_relay_reset_keeps_the_status_line_between_turns():
    output = io.StringIO()
    relay = LiveRelay(stream=output, ansi=True)
    relay.set_status("50% Processing...")
    relay.feed("intermediate")
    relay.reset()
    assert relay.glitch.exact_text() == ""
    assert not relay.streamed
    relay.feed("second turn")
    assert relay.finish() == "second turn"


def test_relay_abort_preserves_received_text_and_stops_workers():
    before = {thread.name for thread in threading.enumerate()}
    relay = LiveRelay(stream=io.StringIO(), ansi=True)
    relay.feed("partial answer")
    assert relay.abort() == "partial answer"
    assert not relay.running
    assert not {thread.name for thread in threading.enumerate()} - before


def test_no_worker_threads_remain_after_finish():
    before = {thread.name for thread in threading.enumerate()}
    relay = LiveRelay(stream=io.StringIO(), ansi=True)
    relay.feed("done")
    relay.finish()
    assert not {thread.name for thread in threading.enumerate()} - before


def test_wrapping_and_width_handle_wide_characters():
    assert display_width("你好") == 4
    assert wrap_lines("abcdef", 3) == ["abc", "def"]
    assert wrap_lines("a\nbb", 4) == ["a", "bb"]
    assert all(display_width(line) <= 4 for line in wrap_lines("你好你好", 4))


def visible_width(line):
    """Columns a painted line really occupies, ignoring colour escapes."""
    return display_width(ANSI_RE.sub("", line))


def test_every_painted_row_fits_inside_the_terminal(monkeypatch=None):
    """The frame edges are two columns wide each, so a row budgeting only four
    columns of chrome overflows, wraps, and desyncs LiveRegion's cursor moves.
    """
    import shutil as _shutil
    import agent_live_renderer as renderer

    original = renderer.shutil.get_terminal_size
    try:
        for columns in (24, 40, 80, 100, 120, 200):
            renderer.shutil.get_terminal_size = (
                lambda default=None, c=columns: _shutil.os.terminal_size((c, 24))
            )
            relay = LiveRelay(stream=io.StringIO(), ansi=True)
            relay.streamed = True
            body = "A response long enough to wrap several times. " * 6
            status = "███░░░ 23% Respond"
            painted = relay._compose(status, body)
            # The status row is the last thing in the region, under the reply
            # and under whatever footer the caller draws: it is the instrument
            # measuring the turn, and an instrument belongs beneath the thing
            # it is measuring.
            assert painted[-1] == status, painted[-1]
            widths = {visible_width(line) for line in painted[:-1]}
            assert max(widths) < columns, (columns, sorted(widths))
            # One box: every row of the frame is the same width.
            assert len(widths) == 1, (columns, sorted(widths))
    finally:
        renderer.shutil.get_terminal_size = original


def test_the_live_area_holds_the_reply_the_box_and_the_status_in_that_order():
    """The bottom of the screen is one block: the reply as it arrives, the
    prompt box, and the status row under it. All three are in the same region,
    so they keep their order and their distance from the foot of the window
    however much permanent output scrolls past above them.

    The status row is last because it is the instrument. It measures the turn
    the box above it asked for, and an instrument belongs under the thing it
    is measuring rather than floating above the reply."""
    footer = [" ------------", " > Working. Ctrl-C to stop.", " ------------"]
    relay = LiveRelay(stream=io.StringIO(), ansi=True, footer=lambda: list(footer))
    relay.streamed = True
    painted = relay._compose("### 42% Patching", "the reply so far")

    assert painted[-1] == "### 42% Patching", painted[-1]
    assert painted[-4:-1] == footer, painted[-4:-1]
    # And the reply's box is above the footer, not interleaved with it.
    body = painted[:-4]
    assert body and body[0].startswith("┌"), body
    assert body[-1].startswith("└"), body


def test_the_live_area_still_draws_with_no_footer_and_no_reply():
    """A relay with no footer is the shape every existing caller had, and a
    turn that has produced no text yet is a status row on its own."""
    bare = LiveRelay(stream=io.StringIO(), ansi=True)
    bare.streamed = True
    assert bare._compose("### 42%", "")[-1] == "### 42%"
    assert bare._compose("### 42%", "hello")[-1] == "### 42%"
    assert len(bare._compose("", "")) == 0

    footed = LiveRelay(stream=io.StringIO(), ansi=True, footer=lambda: [" > box"])
    assert footed._compose("", "") == [" > box"]
    assert footed._compose("### 42%", "") == [" > box", "### 42%"]


def test_a_footer_that_fails_is_not_drawn_and_does_not_end_the_turn():
    """Decoration is never allowed to end a run. A footer that raises is one
    fewer thing on screen, not a turn that stopped."""
    def broken():
        raise RuntimeError("the terminal went away")
    relay = LiveRelay(stream=io.StringIO(), ansi=True, footer=broken)
    relay.streamed = True
    painted = relay._compose("### 42%", "the reply")
    assert painted[-1] == "### 42%", painted


def test_the_reply_gives_up_rows_so_the_whole_region_stays_on_screen():
    """A region taller than the terminal scrolls away from the cursor moves
    that repaint it, which walks the frame down the screen. The reply is what
    shrinks, because the footer is the box the user is looking at and the
    status row is a single line."""
    import shutil as _shutil
    import agent_live_renderer as renderer

    original = renderer.shutil.get_terminal_size
    footer = [" ---", " > Working. Ctrl-C to stop.", " ---"]
    try:
        for lines in (12, 18, 24, 40):
            renderer.shutil.get_terminal_size = (
                lambda default=None, rows=lines: _shutil.os.terminal_size((80, rows))
            )
            relay = LiveRelay(stream=io.StringIO(), ansi=True,
                              footer=lambda: list(footer))
            relay.streamed = True
            painted = relay._compose("### 42%", "a reply that wraps. " * 40)
            assert len(painted) < lines, (lines, len(painted))
            assert painted[-4:] == footer + ["### 42%"], painted[-4:]
    finally:
        renderer.shutil.get_terminal_size = original


def test_every_way_a_turn_can_end_gives_the_caret_back():
    """The caret is suppressed for as long as the region owns the rows, and
    the region owns them for the length of a turn. So every path that ends one
    has to hand it back -- a finished turn, a cancelled one, and a turn that
    released the terminal to print an error. A caret that stayed hidden would
    be the flicker fix turned into a worse bug: an input line with nothing in
    it to show where you are typing."""
    for ending in ("finish", "abort", "release"):
        output = io.StringIO()
        relay = LiveRelay(stream=output, ansi=True)
        relay.set_status("### 42% Patching")
        relay.feed("the reply")
        getattr(relay, ending)()
        assert "\033[?25h" in output.getvalue(), (ending, repr(output.getvalue()[-60:]))
        assert output.getvalue().rstrip().endswith("\033[?25h"), (
            ending, repr(output.getvalue()[-60:]))


def test_a_repaint_never_grows_the_region_it_replaces():
    """Two frames of equal line count must repaint in place, not stack up."""
    output = io.StringIO()
    relay = LiveRelay(stream=output, ansi=True)
    relay.set_status("███░░░ 23% Respond")
    relay.feed("first chunk of the reply")
    relay.glitch.reveal_all()
    relay._repaint()
    drawn = relay.region._drawn
    relay.set_status("████░░ 31% Respond")
    relay._repaint()
    assert relay.region._drawn == drawn
    assert output.getvalue().count("[%dA" % drawn) >= 1
    relay.abort()


# --- permanent output against the repainting worker --------------------------

def test_history_written_while_the_relay_repaints_is_never_lost():
    """The two surfaces share one terminal and two threads reach it: the relay
    worker repaints the live region on its own clock, while the turn's
    permanent events arrive from the thread running the model.

    Both go through the region's paint lock, so an interleaved cursor-move
    sequence cannot corrupt the region and, more importantly, no permanent
    line can be dropped or reordered. A history that loses entries under load
    is the same failure as a history that never existed.
    """
    import threading

    class Screen(io.StringIO):
        encoding = "utf-8"

        def isatty(self):
            return True

    screen = Screen()
    relay = LiveRelay(stream=screen, ansi=True)
    relay.start()
    failures = []
    rounds = 200

    def temporary():
        try:
            for index in range(rounds):
                relay.set_status("bar %d" % index)
                relay.feed("x")
        except Exception as error:          # noqa: BLE001 - reported, not raised
            failures.append(error)

    def permanent():
        try:
            for index in range(rounds):
                relay.write_above("permanent %d\n" % index)
        except Exception as error:          # noqa: BLE001
            failures.append(error)

    threads = [threading.Thread(target=temporary), threading.Thread(target=permanent)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    relay.abort()

    assert not failures, failures
    body = screen.getvalue()
    kept = [index for index in range(rounds) if ("permanent %d" % index) in body]
    assert len(kept) == rounds, "%d of %d permanent lines survived" % (len(kept), rounds)
    positions = [body.index("permanent %d" % index) for index in kept]
    assert positions == sorted(positions), "permanent lines arrived out of order"
