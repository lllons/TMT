"""Tests for the character-by-character reveal used by the live relay."""

import io
import threading
import time

from agent_live_renderer import (
    ASCII_SYMBOL_POOL, GLITCH_REVEAL_DURATION, GlitchStream, LiveRegion,
    LiveRelay, SYMBOL_POOL, display_width, iter_graphemes, wrap_lines,
)


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
    assert glitch.display_text()[5] == " "


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
    assert output.getvalue().endswith("\033[2A")


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
    final_box = output.getvalue().rsplit("┌", 1)[-1]
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
