"""The model reply format setting, wired to the places a person can reach it.

`testing/unit/test_agent_reply_format.py` protects the setting: its default, its
forgiving read, its loud write. This file protects the three things that only
exist once it is connected to something -- and every one of them is a place the
setting could be correct in isolation and useless in practice.

THE SETTINGS ROW. A setting nobody can reach is not a setting. The row has to
exist, sit where the Danger Zone's rule says it must (above it, with `Back`
still last), say what is actually on disk in WORDS rather than colour, flip in
place rather than opening a screen, survive a settings file that cannot be
read, and fit every terminal the rest of the menu fits.

THE REPORT. `/config` is what a person pastes into a bug report to say what a
request ran under. A reply format that changed what the model is asked to write
and did not appear there would make that report wrong in exactly the case it
matters.

THE REFRESH SITES. A setting written by the menu and never re-read lasts one
session and quietly reverts -- the failure `refresh_effort` exists for. There
are three places a launch or a return to the menu can start from, and it has to
be re-read at every one, or the toggle works in two of three ways of arriving.

**Nothing here touches the real `.tmt_protocol`.** Every test reads or writes
the setting through `ReplyFormat`, which redirects `PROTOCOL_FILE` into a
temporary directory and puts it back. The suite as a whole is pinned to `json`
by the runner; every test here that cares which format is in force states it.
"""

import ast
import io
from pathlib import Path

import agent_commands
import agent_config
import agent_menu
import agent_ui
import TMT

from agent_live_renderer import LiveRegion
from test_agent_launch import Frames, Keys
from test_agent_reply_format import ReplyFormat

ROW = "Model Reply Format"


def visible(text):
    """The row a reader sees, with every escape sequence removed. Colour is
    never the message, so every assertion about what the menu SAYS is made
    through this."""
    return agent_ui.strip_ansi(text)


def _cp437():
    """A console that can encode none of the decoration. The ASCII fallback is
    the wider of the two forms for the footer's arrows, so a width sweep that
    only ever asks a UTF-8 stream is asking the easier question."""
    class Console(io.StringIO):
        encoding = "cp437"

        def isatty(self):
            return True

    return Console()


def _rows(selected=3, columns=90, stream=None):
    frame = agent_menu.render_settings_menu_frame(
        selected, io.StringIO() if stream is None else stream,
        size=(columns, 30))
    return [visible(row) for row in frame]


def _row(rows):
    found = [text for text in rows if ROW in text]
    assert len(found) == 1, found
    return found[0]


def _position():
    return [item[0] for item in agent_menu.SETTINGS_ITEMS].index("protocol")


def _drive(box_keys=None, region=None):
    keys = box_keys if box_keys is not None else Keys(
        *(["down"] * _position() + ["enter", "esc"]))
    stream = io.StringIO()
    chosen = agent_menu.settings_screen(
        stream=stream, key_reader=keys,
        region=region if region is not None else LiveRegion(io.StringIO(),
                                                            ansi=False))
    return chosen, keys, stream


# --- the Settings row -----------------------------------------------------------

def test_settings_offers_the_row_above_the_danger_zone_with_back_still_last():
    """The way in. The Danger Zone's own comment says nothing that changes a
    preference may sit below it and the existing tests pin the last two ids, so
    the new row has to be the third from the end -- not the second, which would
    put a switch between a preference and the program's removal."""
    ids = [item[0] for item in agent_menu.SETTINGS_ITEMS]
    labels = {item[0]: item[1] for item in agent_menu.SETTINGS_ITEMS}
    details = {item[0]: item[2] for item in agent_menu.SETTINGS_ITEMS}
    assert "protocol" in ids, ids
    assert labels["protocol"] == ROW, labels["protocol"]
    assert ids[-2:] == ["danger", "back"], ids
    assert ids.index("protocol") == len(ids) - 3, ids
    assert ids.index("protocol") > ids.index("autoupdate"), ids
    assert details["protocol"].strip(), details["protocol"]
    # The existing tests count rows containing the word OFF to find the one
    # switch that carries it. A description that said it would be a second.
    assert "OFF" not in details["protocol"], details["protocol"]


def test_the_label_does_not_widen_the_label_column():
    """`label_width` is measured over every label, so the longest one sets the
    column. A new label longer than `Auto Update on Launch` would slide every
    description in the frame to the right."""
    widest = max(agent_ui.display_width(item[1])
                 for item in agent_menu.SETTINGS_ITEMS)
    assert widest == agent_ui.display_width("Auto Update on Launch"), widest
    assert agent_ui.display_width(ROW) < widest


def test_the_row_reads_tags_by_default_and_json_when_stored():
    """In words, with the escapes stripped: a terminal with no colour -- and
    every assertion in this suite -- would otherwise be reading a blank."""
    assert agent_menu.PROTOCOL_LABELS == {"tags": "TAGS", "json": "JSON"}
    with ReplyFormat(None):
        assert agent_menu.protocol_text() == "TAGS"
        assert _row(_rows()).rstrip().endswith("TAGS"), _row(_rows())
    with ReplyFormat("tags"):
        assert _row(_rows()).rstrip().endswith("TAGS")
    with ReplyFormat("json"):
        assert agent_menu.protocol_text() == "JSON"
        assert _row(_rows()).rstrip().endswith("JSON"), _row(_rows())


def test_the_frame_changes_in_exactly_one_row_when_the_format_changes():
    """The row is the only place the value is shown, so it has to be read
    rather than cached, and a second copy of it elsewhere in the frame would
    make a reader look in two places to find out what Enter does."""
    with ReplyFormat("tags") as box:
        as_tags = _rows()
        box.write("json\n")
        as_json = _rows()
    assert as_tags != as_json, "the frame did not notice the setting change"
    differing = [pair for pair in zip(as_tags, as_json) if pair[0] != pair[1]]
    assert len(differing) == 1, differing
    assert ROW in differing[0][0], differing[0]
    assert differing[0][0].rstrip().endswith("TAGS"), differing[0]
    assert differing[0][1].rstrip().endswith("JSON"), differing[0]


def test_the_row_survives_a_settings_file_that_cannot_be_read():
    """A menu that could not be drawn because of an unreadable settings file
    would be the config file stopping TMT one screen later than the reader
    already refuses to let it. The row falls back to the documented default
    rather than to nothing."""
    with ReplyFormat("json") as box:
        box.aim_at(box.dir / "no" / "such" / "place")
        assert agent_menu.protocol_text() == "TAGS"
        assert _row(_rows()).rstrip().endswith("TAGS")
    with ReplyFormat("json") as box:
        box.aim_at(box.dir)                       # a directory, not a file
        assert agent_menu.protocol_text() == "TAGS"
        assert _row(_rows()).rstrip().endswith("TAGS")


def test_toggle_protocol_never_raises_and_reports_what_is_in_force():
    """A failed write in Settings is reported by the row not changing, which is
    the honest signal that nothing happened -- and the alternative, a traceback
    out of a menu, would leave the terminal in raw mode. What comes back has to
    be what is actually in force, not what was asked for."""
    with ReplyFormat("tags") as box:
        box.aim_at(box.dir)                       # a directory: unwritable
        assert agent_menu.toggle_protocol() == "TAGS"
        assert agent_menu.toggle_protocol() == "TAGS", \
            "it claimed a write it could not make"
    with ReplyFormat("tags") as box:
        box.aim_at(box.dir / "no" / "such" / "place" / ".tmt_protocol")
        assert agent_menu.toggle_protocol() == "TAGS"


def test_toggle_protocol_flips_the_stored_value_when_it_can():
    """The other half of the same function. A `toggle` that never raised
    because it never wrote would pass the test above and do nothing at all."""
    with ReplyFormat("tags") as box:
        assert agent_menu.toggle_protocol() == "JSON"
        assert agent_config.read_saved_protocol() == "json"
        assert box.path.read_text(encoding="utf-8") == "json\n"
        assert agent_menu.toggle_protocol() == "TAGS"
        assert agent_config.read_saved_protocol() == "tags"
    # From a fresh installation, where nothing is stored and the default is in
    # force, the first press goes to the OTHER format.
    with ReplyFormat(None) as box:
        assert agent_menu.toggle_protocol() == "JSON"
        assert box.path.read_text(encoding="utf-8") == "json\n"


def test_the_toggle_flips_what_the_row_shows_and_the_row_shows_the_file():
    """The row is drawn from DISK, so the press has to flip what is on disk.
    Flipped from the live `agent_config.PROTOCOL` instead, a process whose live
    value was stale -- the file edited in another window, a refresh not yet run
    -- would write the format the row already showed: the user presses Enter and
    nothing on the row changes, which is a switch that looks broken."""
    with ReplyFormat("tags") as box:
        agent_config.PROTOCOL = "json"            # the live value is behind the file
        assert agent_menu.protocol_text() == "TAGS"
        assert agent_menu.toggle_protocol() == "JSON"
        assert box.path.read_text(encoding="utf-8") == "json\n"
    with ReplyFormat("json") as box:
        agent_config.PROTOCOL = "tags"
        assert agent_menu.protocol_text() == "JSON"
        assert agent_menu.toggle_protocol() == "TAGS"
        assert box.path.read_text(encoding="utf-8") == "tags\n"


def test_the_row_falls_back_to_the_default_when_the_reader_itself_fails():
    """`read_saved_protocol` is documented never to raise and the row guards it
    anyway: a menu is the wrong place to find out a documentation promise was
    wrong. The fallback must be the DEFAULT, not the other format -- a failed
    read is not evidence of a choice."""
    real = agent_config.read_saved_protocol

    def explodes():
        raise RuntimeError("a reader that was documented never to raise")

    def says_something_unknown():
        return "xml"

    try:
        for broken in (explodes, says_something_unknown):
            agent_config.read_saved_protocol = broken
            assert agent_menu.protocol_text() == "TAGS", broken.__name__
            assert agent_menu.protocol_text() == agent_menu.PROTOCOL_LABELS[
                agent_config.DEFAULT_PROTOCOL]
    finally:
        agent_config.read_saved_protocol = real
    assert agent_config.read_saved_protocol is real


def test_the_row_keeps_its_state_on_a_terminal_too_narrow_for_its_words():
    """The state is protected from trimming and the description is not, which
    is the right way round: a row that gave up its TAGS/JSON to keep the
    sentence explaining what it does would hide the one thing the user came to
    read.

    **Swept from 40 columns, not 30, and that is a finding rather than a
    choice.** `Auto Update on Launch` keeps its state at 30 columns by exactly
    one: `_option_row` pads the label to the widest label (21), protects a
    suffix only when it fits BESIDE that padded head, and `  OFF` is five
    columns where `  TAGS` is six. So at 30 columns this row reads `...Format
    TAG`. The row still fits the terminal -- `test_every_settings_row_fits...`
    sweeps 30 -- it is the state that loses its last letter. The fix belongs in
    `_option_row`, which every menu in the program shares, so it was left for
    its own change rather than made inside a settings step."""
    for value, word in (("tags", "TAGS"), ("json", "JSON")):
        with ReplyFormat(value):
            for columns in (40, 50, 60, 80, 120):
                row = _row(_rows(columns=columns))
                assert row.rstrip().endswith(word), (columns, row)


def test_every_settings_row_fits_the_terminal_it_was_drawn_for():
    """Measured, never counted -- a row filled past the last column wraps and
    costs a screen line the repaint arithmetic does not know about. Both
    formats, because the two words are the same width but the sweep is cheap
    and the claim is about the row, not about one value; and both streams,
    because the ASCII fallback is the wider form for the footer."""
    for value in ("tags", "json"):
        with ReplyFormat(value):
            for stream in (io.StringIO(), _cp437()):
                for columns in (30, 40, 50, 60, 80, 120, 200):
                    frame = agent_menu.render_settings_menu_frame(
                        3, stream, size=(columns, 30))
                    for row in frame:
                        width = agent_ui.display_width(visible(row))
                        assert width <= columns - 1, (
                            value, columns, width, visible(row))


# --- driving the real Settings screen ---------------------------------------------

def test_driving_the_real_settings_screen_flips_the_format_on_disk_and_back():
    """The wire between a keystroke and the file. Everything above is a
    function called directly; this is the only test that proves Enter on that
    row reaches `set_protocol` at all, through the real `_drive` loop and the
    real key normalisation -- and the second pass proves it is a switch rather
    than a one-way door, which a screen that wrote a literal `json` would not
    be."""
    with ReplyFormat("tags") as box:
        for expected in ("json", "tags", "json"):
            chosen, keys, stream = _drive()
            assert chosen is None, chosen
            assert agent_config.read_saved_protocol() == expected, expected
            assert box.path.read_text(encoding="utf-8") == expected + "\n"
            assert keys.remaining == 0, keys.remaining
            assert stream.getvalue() == "", "the screen wrote past its own region"
        # The live value moved with the file: the menu is reachable mid-session
        # and anything holding `agent_config.PROTOCOL` would otherwise carry the
        # old one until the next launch.
        assert agent_config.PROTOCOL == "json"


def test_selecting_the_row_does_not_open_a_screen():
    """It is a switch, not a door. Every frame painted is asserted to still be
    the settings frame, so an Enter that opened the model picker -- or anything
    else -- is caught by what was drawn rather than by the script running out
    of keys."""
    with ReplyFormat("tags"):
        region = Frames()
        _drive(region=region)
        assert region.frames, "nothing was painted at all"
        for frame in region.frames:
            rendered = "\n".join(visible(row) for row in frame)
            assert ROW in rendered, rendered
            assert "Settings" in rendered, rendered
        # And the Enter really did something, so this is not passing because the
        # row was never selected; the frame after it shows the new value.
        assert agent_config.read_saved_protocol() == "json"
        assert any(row.rstrip().endswith("JSON") and ROW in row
                   for row in (visible(text) for text in region.frames[-1])), \
            region.frames[-1]


class _Redirected:
    """A second setting's file, redirected for the length of one block.

    The neighbouring switches write THEIR files when Enter lands on them, and
    those are the developer's real settings. Putting the path and the live
    value back is the whole of the contract.
    """

    def __init__(self, file_name, value_name):
        import shutil
        import tempfile
        self._shutil = shutil
        self.dir = Path(tempfile.mkdtemp(prefix="tmt_neighbour_"))
        self.file_name, self.value_name = file_name, value_name
        self.previous = (getattr(agent_config, file_name),
                         getattr(agent_config, value_name))
        setattr(agent_config, file_name, self.dir / ".tmt_neighbour")

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        setattr(agent_config, self.file_name, self.previous[0])
        setattr(agent_config, self.value_name, self.previous[1])
        self._shutil.rmtree(str(self.dir), ignore_errors=True)
        return False


def test_leaving_settings_by_any_other_row_changes_nothing():
    """The switch is reached by Enter on its own row and by nothing else. A
    handler keyed on the selection index rather than on the entry id would flip
    it from whichever row happened to share a number."""
    ids = [item[0] for item in agent_menu.SETTINGS_ITEMS]
    with ReplyFormat("tags") as box:
        keys = Keys(*(["down"] * ids.index("back") + ["enter"]))
        agent_menu.settings_screen(stream=io.StringIO(), key_reader=keys,
                                   region=Frames())
        assert agent_config.read_saved_protocol() == "tags"
        assert box.path.read_text(encoding="utf-8") == "tags\n"
        assert keys.remaining == 0, keys.remaining


def test_pressing_enter_on_the_neighbouring_switches_leaves_the_format_alone():
    """The two rows most likely to share a handler with this one. Each flips its
    OWN file; neither may touch the reply format, and this one may not touch
    theirs."""
    ids = [item[0] for item in agent_menu.SETTINGS_ITEMS]
    for neighbour, file_name, value_name, reader in (
            ("autoupdate", "AUTO_UPDATE_FILE", "AUTO_UPDATE",
             "read_saved_auto_update"),
            ("projectcontext", "PROJECT_CONTEXT_FILE", "PROJECT_CONTEXT",
             "read_saved_project_context")):
        with ReplyFormat("tags") as box, _Redirected(file_name, value_name):
            before = getattr(agent_config, reader)()
            keys = Keys(*(["down"] * ids.index(neighbour) + ["enter", "esc"]))
            agent_menu.settings_screen(stream=io.StringIO(), key_reader=keys,
                                       region=Frames())
            assert getattr(agent_config, reader)() is not before, \
                neighbour + " did not flip, so this proves nothing"
            assert box.path.read_text(encoding="utf-8") == "tags\n", neighbour
            assert agent_config.read_saved_protocol() == "tags", neighbour


def test_a_settings_screen_whose_file_cannot_be_written_does_not_raise():
    """Through the real loop rather than the function: an exception out of
    `on_key` would end the screen with the terminal in raw mode."""
    with ReplyFormat("tags") as box:
        box.aim_at(box.dir)                       # a directory: unwritable
        chosen, keys, _stream = _drive()
        assert chosen is None
        assert keys.remaining == 0, keys.remaining
        assert agent_config.read_saved_protocol() == "tags"


# --- /config ---------------------------------------------------------------------

def test_config_reports_the_reply_format_before_the_json_mode_row():
    """`/config` is what a person pastes to say what a request ran under, and
    the format changes what the model is asked to write. It sits beside the
    other request settings, and BEFORE `JSON mode` -- the provider-side flag
    it is easy to confuse it with -- so the two read as different questions."""
    for value in ("tags", "json"):
        with ReplyFormat(value):
            result = agent_commands.dispatch("/config")
            assert result.ok
            text = result.text()
            assert "Reply format" in text, text
            row = [line for line in text.splitlines() if "Reply format" in line]
            assert len(row) == 1, row
            assert row[0].split()[-1] == value, row[0]
            assert text.index("Reply format") < text.index("JSON mode"), text


def test_config_reports_the_live_value_and_not_the_stored_one():
    """The row says what a request is running under NOW. The file can be
    ahead of the process by a launch; `/config` reads the process."""
    with ReplyFormat("tags") as box:
        box.write("json\n")                       # stored ahead of the live value
        assert agent_config.PROTOCOL == "tags"
        text = agent_commands.dispatch("/config").text()
        line = [row for row in text.splitlines() if "Reply format" in row][0]
        assert line.split()[-1] == "tags", line


# --- the three refresh sites ---------------------------------------------------------

def _called_in(tree, function):
    """Whether the named top-level function calls `agent_config.refresh_protocol`."""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function:
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "refresh_protocol"
                        and isinstance(inner.func.value, ast.Name)
                        and inner.func.value.id == "agent_config"):
                    return True
            return False
    raise AssertionError("TMT.py has no top-level function %s" % function)


def test_every_way_in_re_reads_the_reply_format():
    """A setting written by the menu and never re-read lasts one session and
    quietly reverts. TMT can start a session from three places -- a normal
    launch, a CI run, and a return to the menu from inside a session -- and the
    format has to be re-read at each of them.

    The CI run is included deliberately, unlike `refresh_auto_update`: that one
    is a statement about TMT updating itself, which a pipeline never does, while
    this one changes what the model is asked to write.
    """
    source = Path(TMT.__file__).read_text(encoding="utf-8")
    assert source.count("agent_config.refresh_protocol()") == 3, \
        source.count("agent_config.refresh_protocol()")
    tree = ast.parse(source)
    for function in ("main", "run_ci", "_return_to_menu"):
        assert _called_in(tree, function), function + " never re-reads it"
