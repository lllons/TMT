"""Tests for the one field a key is typed into.

The defect these exist for: a key pasted into Settings did nothing at all. Not
a message, not a mask character -- nothing, on a screen whose only job is to
take one string. The cause was one invisible character. Selecting a key in a
browser, or copying the line out of a file, takes the newline at the end of it
with you, and `str.isprintable` is false for any string containing a newline,
so `normalize_text_key` dropped the WHOLE paste and the field stood still.

Two layers are driven here and the division is deliberate.

`api_key_screen` is driven against a FAKE CONSOLE rather than an injected
reader for the paste itself, because an injected reader goes straight into the
screen and never reaches `_drain_burst` -- and the drain is what turns the
characters a console delivers into the one multi-character read the bug lived
in. Everything after that read is the field's own logic, and an injected
reader is the right instrument for it: no timing, no console, no drain.

Nothing here presses Enter against the real store. `_store_key` is replaced
where a save is what is being measured, so no test writes a credential, asks a
provider about one, or needs one to be present.
"""

import io
import re
import sys
import types

ESCAPE_RE = re.compile("\033\\[[0-9;?]*[ -/]*[@-~]|\033\\][^\007]*\007|\033[=>]")

# A key of a shape every provider would recognise, and long enough that a mask
# count is unambiguous.
KEY = "sk-or-v1-0123456789abcdef0123456789abcdef"


def menu():
    """agent_menu, imported at call time, as the other menu tests import it."""
    import agent_menu
    return agent_menu


def visible(text):
    """The frames with every escape sequence removed."""
    return ESCAPE_RE.sub("", text)


class Terminal(io.StringIO):
    """A buffer that claims to be a terminal, so the region actually paints."""

    def isatty(self):
        return True

    @property
    def encoding(self):
        return "utf-8"


class Console:
    """A console that delivers a paste the way a real one does.

    One character per read, all of it already waiting. That is the whole shape
    of a paste as far as any program can see it: a terminal does not announce
    one, it delivers the characters it is made of as fast as the program will
    take them, through exactly the call that delivers a typed character.
    """

    def __init__(self, text):
        self.chars = list(text)

    def kbhit(self):
        return bool(self.chars)

    def getwch(self):
        return self.chars.pop(0)


class Scripted:
    """An injected reader: each call hands back the next thing in the list."""

    def __init__(self, reads):
        self.reads = list(reads)

    def __call__(self):
        if not self.reads:
            raise IndexError("the script ran out")
        return self.reads.pop(0)


def drive_console(provider_id, text):
    """Run the real screen against a console delivering `text`. Returns frames.

    The reader is the real default one, so `read_key` takes the path it takes
    on a machine with a console and the paste arrives through the drain. Only
    `is_interactive` is stepped around, because the stream is a buffer.
    """
    m = menu()
    console = Console(text)
    fake = types.ModuleType("msvcrt")
    fake.kbhit = console.kbhit
    fake.getwch = console.getwch
    had = sys.modules.get("msvcrt")
    sys.modules["msvcrt"] = fake
    pending = list(m._pending_keys)
    m._pending_keys[:] = []
    out = Terminal()
    try:
        result = m.api_key_screen(provider_id, stream=out, region=m.LiveRegion(out),
                                  key_reader=m._default_text_reader())
    finally:
        if had is None:
            sys.modules.pop("msvcrt", None)
        else:
            sys.modules["msvcrt"] = had
        m._pending_keys[:] = pending
    return result, visible(out.getvalue())


def drive_reader(provider_id, reads, store=None):
    """Run the real screen against an injected reader. Returns (result, frames).

    `store` replaces `_store_key`, so a test that presses Enter measures what
    the field handed over without writing a credential or asking a provider
    about one.
    """
    m = menu()
    out = Terminal()
    saved = m._store_key
    if store is not None:
        m._store_key = store
    try:
        result = m.api_key_screen(provider_id, stream=out, region=m.LiveRegion(out),
                                  key_reader=Scripted(reads))
    finally:
        m._store_key = saved
    return result, visible(out.getvalue())


def masked(frames):
    """The most characters any Key row was ever drawn with.

    The row carries a placeholder when nothing has been typed, and the mask
    character appears inside it ("only * is echoed"), so what is counted is the
    run of mask characters rather than every occurrence of one.
    """
    mask = menu().MASK_CHAR
    best = 0
    for row in frames.splitlines():
        for run in re.findall(re.escape(mask) + "+", row):
            best = max(best, len(run))
    return best


# --- the regression ---------------------------------------------------------

def test_a_key_pasted_with_the_newline_the_copy_took_with_it_reaches_the_field():
    """The defect, end to end, through the real drain.

    A trailing break is what a copy picks up, not what makes a paste a block,
    and the field used to answer the whole paste with nothing: no mask, no
    message, and then "Nothing was typed" when Enter was pressed. Every ending
    a console can deliver is checked, because the one that broke depends on
    where the key was copied from.
    """
    for tail in ("", "\n", "\r", "\r\n", "\r\n\r\n"):
        result, frames = drive_console("openrouter", KEY + tail + "\x1b")
        assert result is None, "Esc must save nothing"
        assert masked(frames) == len(KEY), (
            "a paste ending %r was taken as %d characters, not %d"
            % (tail, masked(frames), len(KEY)))


def test_the_field_takes_a_pasted_key_for_every_provider():
    """The screen is one screen and every provider reaches it, so the fix is
    one fix -- but "for all of them" is the thing that was asked for, and a
    loop is cheaper than trusting that it is shared."""
    for provider_id in menu().PROVIDER_ORDER:
        result, frames = drive_console(provider_id, KEY + "\r\n" + "\x1b")
        assert result is None
        assert masked(frames) == len(KEY), provider_id


def test_typing_a_key_one_character_at_a_time_is_unchanged():
    """The control. Typing always worked, and a fix to pasting that changed
    what typing does would be a worse bug than the one it replaced."""
    result, frames = drive_console("openrouter", KEY + "\x1b")
    assert result is None
    assert masked(frames) == len(KEY)


# --- what the field hands over ----------------------------------------------

def test_a_key_pasted_with_a_trailing_newline_is_stored_without_it():
    """Stripping it in the field is what makes this true. A key stored with a
    line break on the end is a key every request sends and every provider
    rejects, and the user has no way to see the character that did it."""
    seen = []

    def store(provider_id, key):
        seen.append((provider_id, key))
        return "Saved.", True

    result, frames = drive_reader("anthropic", [KEY + "\r\n", "enter", "esc"],
                                  store=store)
    assert seen == [("anthropic", KEY)], seen
    assert result == "anthropic"


def test_the_key_never_reaches_the_screen_however_it_arrived():
    """The property the mask exists for, asserted against a paste rather than
    against typing, because the paste is the path that changed."""
    result, frames = drive_console("openai", KEY + "\r\n" + "\x1b")
    assert result is None
    assert KEY not in frames
    assert KEY[:12] not in frames


# --- a paste that really is more than one line ------------------------------

def test_a_paste_of_more_than_one_line_says_so_rather_than_doing_nothing():
    """Half a key is worse than no key, so nothing is taken from it -- but
    silence is what the defect looked like, and answering a paste with nothing
    at all cannot be told from a field that is not reading the keyboard."""
    block = "OPENROUTER_API_KEY=%s\nexport OTHER=1" % KEY
    result, frames = drive_reader("openrouter", [block, "esc"])
    assert result is None
    assert masked(frames) == 1, "nothing may be taken from a block"
    assert "That paste is 2 lines" in frames, frames


def test_the_line_count_in_that_message_is_the_paste_s_own():
    """A count nobody measured is the fabrication rule broken on the one row
    that exists to explain what just happened."""
    for lines in (2, 3, 5):
        block = "\n".join(["line %d" % n for n in range(lines)])
        _, frames = drive_reader("gemini", [block, "esc"])
        assert "That paste is %d lines" % lines in frames, (lines, frames)


def test_a_block_leaves_what_was_already_typed_alone():
    """The message is a refusal of the paste, not of the field. A user who has
    typed half a key by hand must not lose it to a mistaken paste."""
    _, frames = drive_reader("openrouter", ["abcdef", "line one\nline two", "esc"])
    assert masked(frames) == 6, frames


# --- the rule itself --------------------------------------------------------

def test_normalize_text_key_strips_the_breaks_at_the_ends_and_no_others():
    """The one place the decision is made, so both single-line fields get it.

    A break at either end is punctuation the copy brought along; a break in the
    middle is a second line. Only the second is a block.
    """
    m = menu()
    for run in ("abc\n", "abc\r\n", "\nabc", "\r\nabc\r\n", "abc\r"):
        assert m.normalize_text_key(run) == ("char", "abc"), repr(run)
    assert m.normalize_text_key("a\nb") == ("block", "a\nb")
    assert m.normalize_text_key("a\r\nb") == ("block", "a\nb")
    # A paste of nothing but breaks is nothing to type and nothing to refuse.
    for run in ("\n\n", "\r\n", "\r\r"):
        assert m.normalize_text_key(run) in (("", ""), ("key", "enter")), repr(run)


def test_the_task_box_is_untouched_by_any_of_this():
    """`allow_multiline` still takes a block whole, breaks and all. The task
    box is the one field a pasted traceback belongs in, and folding it to a
    token is what that path is for."""
    m = menu()
    block = "line one\nline two\nline three"
    assert m.normalize_text_key(block, allow_multiline=True) == ("char", block)
    assert m.normalize_text_key(block + "\n", allow_multiline=True) == ("char", block + "\n")
    assert m.normalize_text_key("\x1b[A", allow_multiline=True) == ("", "")


def test_a_single_character_is_still_a_character_and_a_break_is_still_enter():
    """The keys that are read before any of this runs. A field that read a
    lone carriage return as a one-line paste would never submit anything."""
    m = menu()
    assert m.normalize_text_key("k") == ("char", "k")
    assert m.normalize_text_key("\r") == ("key", "enter")
    assert m.normalize_text_key("\n") == ("key", "enter")
    assert m.normalize_text_key("\r\n") == ("key", "enter")
    assert m.normalize_text_key("\x7f") == ("key", "backspace")
    assert m.normalize_text_key("\x1b") == ("key", "esc")
    assert m.normalize_text_key(None) == ("end", None)
    assert m.normalize_text_key("") == ("", "")
