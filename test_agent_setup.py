"""Tests for first-launch API key setup."""

import io
import re
import threading
import time
import tempfile
from pathlib import Path

import agent_config
import agent_setup
from agent_setup import TitleLoop, clean_key, ensure_api_key, mask_key, run_setup
from agent_ui import cycle_text


class FakeTTY(io.StringIO):
    encoding = "utf-8"

    def isatty(self):
        return True


def temp_key_file():
    """Point the saved-key path at a throwaway file for the duration of a test."""
    directory = tempfile.mkdtemp(prefix="tmt-key-")
    return Path(directory) / ".tmt_key"


def sandbox(function):
    """Run a test with the key file and in-memory key isolated."""
    saved_path, saved_key = agent_config.KEY_FILE, agent_config.OPENROUTER_API_KEY
    agent_config.KEY_FILE, agent_config.OPENROUTER_API_KEY = temp_key_file(), ""
    try:
        return function()
    finally:
        agent_config.KEY_FILE, agent_config.OPENROUTER_API_KEY = saved_path, saved_key


SAVE_CURSOR = "\0337"
COLOUR_CODE = r"\033\[38;2;(\d+;\d+;\d+)m"


def strip_ansi(text):
    return re.sub(r"\033\[[0-9;]*[A-Za-z]", "", text)


def test_pasted_keys_are_cleaned_however_they_arrive():
    assert clean_key("  sk-or-v1-abc  ") == "sk-or-v1-abc"
    assert clean_key('"sk-or-v1-abc"') == "sk-or-v1-abc"
    assert clean_key("set OPENROUTER_API_KEY=sk-or-v1-abc") == "sk-or-v1-abc"
    assert clean_key("export OPENROUTER_API_KEY='sk-or-v1-abc'") == "sk-or-v1-abc"
    assert clean_key("") == ""
    assert clean_key(None) == ""


def test_masking_never_shows_the_whole_key():
    key = "sk-or-v1-0123456789abcdef"
    masked = mask_key(key)
    assert key not in masked
    assert masked.startswith("sk-or-v1-0") and masked.endswith("cdef")
    assert mask_key("short", plain=True) == "*****"


def test_setup_is_skipped_when_a_key_already_exists():
    def check():
        agent_config.OPENROUTER_API_KEY = "sk-or-v1-existing"
        output = FakeTTY()
        assert ensure_api_key(stream=output, ask=lambda prompt: "", animate=False)
        assert output.getvalue() == ""
    sandbox(check)


def test_entered_key_is_saved_and_becomes_live():
    def check():
        output = FakeTTY()
        assert ensure_api_key(stream=output, ask=lambda prompt: "sk-or-v1-typed", animate=False)
        assert agent_config.OPENROUTER_API_KEY == "sk-or-v1-typed"
        assert agent_config.KEY_FILE.read_text(encoding="utf-8").strip() == "sk-or-v1-typed"
        assert agent_config.read_saved_key() == "sk-or-v1-typed"
    sandbox(check)


def test_saved_key_is_never_echoed_in_full():
    def check():
        output = FakeTTY()
        ensure_api_key(stream=output, ask=lambda prompt: "sk-or-v1-secret-value-1234", animate=False)
        assert "sk-or-v1-secret-value-1234" not in output.getvalue()
    sandbox(check)


def test_empty_answers_are_retried_then_give_up_without_writing():
    def check():
        output = FakeTTY()
        answers = iter(["", "  ", ""])
        assert not ensure_api_key(stream=output, ask=lambda prompt: next(answers), animate=False)
        assert agent_config.OPENROUTER_API_KEY == ""
        assert not agent_config.KEY_FILE.exists()
        assert strip_ansi(output.getvalue()).count("That was empty") == 3
    sandbox(check)


def test_a_key_entered_after_a_slip_is_accepted():
    def check():
        answers = iter(["", "sk-or-v1-second-try"])
        assert ensure_api_key(stream=FakeTTY(), ask=lambda prompt: next(answers), animate=False)
        assert agent_config.OPENROUTER_API_KEY == "sk-or-v1-second-try"
    sandbox(check)


def test_cancelling_with_ctrl_c_saves_nothing():
    def check():
        output = FakeTTY()

        def cancel(prompt):
            raise KeyboardInterrupt

        assert not ensure_api_key(stream=output, ask=cancel, animate=False)
        assert not agent_config.KEY_FILE.exists()
        assert "Setup cancelled" in strip_ansi(output.getvalue())
    sandbox(check)


def test_end_of_input_saves_nothing():
    def check():
        def eof(prompt):
            raise EOFError

        assert not ensure_api_key(stream=FakeTTY(), ask=eof, animate=False)
        assert not agent_config.KEY_FILE.exists()
    sandbox(check)


def test_unexpected_key_shape_is_flagged_but_still_accepted():
    def check():
        output = FakeTTY()
        assert ensure_api_key(stream=output, ask=lambda prompt: "my-proxy-key", animate=False)
        assert "Heads up" in strip_ansi(output.getvalue())
        assert agent_config.OPENROUTER_API_KEY == "my-proxy-key"
    sandbox(check)


def test_screen_shows_the_title_the_panel_and_a_progress_bar():
    output = FakeTTY()
    run_setup(stream=output, ask=lambda prompt: "sk-or-v1-abcdefghijkl", animate=False)
    text = strip_ansi(output.getvalue())
    assert "████████╗" in text
    assert "W E L C O M E   T O   T M T" in text
    assert agent_setup.KEY_URL in text
    assert "100% Key saved!" in text
    assert "░" in text and "█" in text


def test_title_is_painted_with_the_progress_gradient():
    output = FakeTTY()
    run_setup(stream=output, ask=lambda prompt: "sk-or-v1-abcdefghijkl", animate=False)
    colours = set(re.findall(r"\033\[38;2;(\d+;\d+;\d+)m", output.getvalue()))
    assert len(colours) > 8, colours


def test_the_cycle_runs_red_to_orange_to_green_and_back():
    stream = FakeTTY()
    def swatch(phase):
        return re.findall(r"\033\[38;2;(\d+;\d+;\d+)m", cycle_text("X", stream, phase=phase))[0]

    assert swatch(0.0) == "220;38;38"                 # red, where the bar starts
    assert swatch(1 / 6) == "249;115;22"              # orange, a third of the way up
    assert swatch(0.5) == "74;222;128"                # green, at the top
    assert swatch(5 / 6) == "249;115;22"              # orange again on the way back
    assert swatch(1.0) == swatch(0.0)                 # and round to red, so it loops


def test_terminals_without_unicode_get_a_plain_screen():
    class AsciiTTY(FakeTTY):
        encoding = "cp1252"

    output = AsciiTTY()
    run_setup(stream=output, ask=lambda prompt: "sk-or-v1-abcdefghijkl", animate=False)
    text = strip_ansi(output.getvalue())
    text.encode("cp1252")                  # would raise if a symbol slipped through
    assert "|_    _|" in text
    assert "100% Key saved!" in text


def test_the_title_keeps_cycling_while_the_prompt_waits():
    output = FakeTTY()
    loop = TitleLoop(output, ("AAA", "BBB"))
    loop.enabled = lambda: True
    loop.start(lines_below=3)
    started = time.monotonic()
    while time.monotonic() - started < 0.4 and output.getvalue().count(SAVE_CURSOR) < 3:
        time.sleep(0.02)
    loop.stop()
    frames = output.getvalue().count(SAVE_CURSOR)
    assert frames >= 3, frames
    colours = set(re.findall(COLOUR_CODE, output.getvalue()))
    assert len(colours) > 3, colours          # the cycle actually moved on
    assert "\033[5A" in output.getvalue()      # 2 title rows + 3 lines below
    assert not loop.running


def test_stopping_the_title_leaves_no_worker_behind():
    before = {thread.name for thread in threading.enumerate()}
    loop = TitleLoop(FakeTTY(), ("AAA",))
    loop.enabled = lambda: True
    loop.start(lines_below=1)
    loop.stop()
    assert not {thread.name for thread in threading.enumerate()} - before


def test_the_title_does_not_animate_where_it_would_mangle_the_screen():
    plain = io.StringIO()                      # not a tty: no colour, no animation
    loop = TitleLoop(plain, ("AAA",))
    loop.start(lines_below=1)
    assert not loop.running
    assert plain.getvalue() == ""
