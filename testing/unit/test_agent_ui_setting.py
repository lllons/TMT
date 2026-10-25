"""The front-end setting (`UI: Python | TypeScript`) and the click-to-caret switch.

`.tmt_ui` is one word -- `python` or `typescript` -- saying which front end the
NEXT start runs, and `.tmt_mouse` is `off` or `on`, saying whether the
TypeScript front end may place the caret on a click. Both are copies of the
reply-format setting (`test_agent_reply_format`), so this file protects the same
things for them: the default, the forgiving read, the loud write, the refresh.
It then protects the three things that only exist once they are connected:

THE SETTINGS ROWS. Reachable, above the Danger Zone, saying in words which way
they are set, and -- for the UI row -- saying it takes effect on the next start,
because flipping it changes nothing on screen.

THE STARTUP DISPATCH. The Python UI is the default and the fallback, and no
saved value may stop TMT starting. A TypeScript front end that cannot start
(no Node, not built, no terminal) answers None and the Python UI runs; `--ci`
and piped runs never reach it whichever front end is saved; `--ui` and `TMT_UI`
override the saved choice for one run.

THE FLAGS. `--ui` takes the two words and nothing else; `--backend-stdio` is
accepted and hidden.

**Nothing here touches the real `.tmt_ui` or `.tmt_mouse`.** `Ui` and `Mouse`
redirect the file into a temporary directory, check the redirect took before a
byte is written, and put the path and the live value back. No Node is ever
started: `subprocess.Popen` is replaced for every test that could reach it.
"""

import contextlib
import io
import os
import sys
import tempfile
import shutil
import types
from pathlib import Path

import agent_commands
import agent_config
import agent_menu
import agent_tsui
import agent_ui
import TMT

from agent_live_renderer import LiveRegion
from test_agent_ci_wiring import ci
from test_agent_cli import prompt_boxes, run_session
from test_agent_launch import Frames, Keys

INSTALL_DIR = Path(agent_config.__file__).resolve().parent


# --- the harnesses ------------------------------------------------------------

class _Setting:
    """One of the two dotfiles, redirected into a temporary directory.

    The real ones are `INSTALL_DIR / ".tmt_ui"` and `".tmt_mouse"` -- the
    repository itself when TMT is run on TMT. A test that wrote there would
    change the developer's own setting and leave an untracked file behind.
    `name` is both what is written and the live global; `None` is no file at
    all, a fresh installation. `contents` is raw text for a hand-edited file and
    wins over `name` for the FILE only. The live global is restored as well as
    the path, because the setters and refreshers assign it.
    """

    FILE = VALUE = DEFAULT = NAME = None

    def __init__(self, name=None, contents=None):
        self.dir = Path(tempfile.mkdtemp(prefix="tmt_uisetting_"))
        self.previous_file = getattr(agent_config, self.FILE)
        self.previous_value = getattr(agent_config, self.VALUE)
        self.path = self.dir / self.NAME
        setattr(agent_config, self.FILE, self.path)
        # CHECKED BEFORE A BYTE IS WRITTEN: the line above is the whole
        # isolation, and the lines below write.
        self._must_be_mine()
        text = contents if contents is not None else (
            None if name is None else name + "\n")
        if text is not None:
            self.write(text)
        setattr(agent_config, self.VALUE,
                name if name is not None else getattr(agent_config, self.DEFAULT))

    def _must_be_mine(self):
        if self.dir not in Path(getattr(agent_config, self.FILE)).parents:
            self.close()
            raise AssertionError(
                "%s is %s, which is not inside the sandbox at %s. Nothing was "
                "written: a redirect that did not take would put a test's "
                "value over the real setting."
                % (self.FILE, getattr(agent_config, self.FILE), self.dir))

    def write(self, text):
        self._must_be_mine()
        self.path.write_text(text, encoding="utf-8")
        return self.path

    def aim_at(self, path):
        """Point the setting somewhere it cannot be written or read."""
        setattr(agent_config, self.FILE, Path(path))

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()
        return False

    def close(self):
        setattr(agent_config, self.FILE, self.previous_file)
        setattr(agent_config, self.VALUE, self.previous_value)
        shutil.rmtree(str(self.dir), ignore_errors=True)


class Ui(_Setting):
    FILE, VALUE, DEFAULT, NAME = "UI_FILE", "UI", "DEFAULT_UI", ".tmt_ui"


class Mouse(_Setting):
    FILE, VALUE, DEFAULT, NAME = "MOUSE_FILE", "MOUSE", "DEFAULT_MOUSE", ".tmt_mouse"


@contextlib.contextmanager
def patched(target, **attributes):
    """Replace attributes on a module or object for one block, then put them back."""
    saved = {name: getattr(target, name) for name in attributes}
    try:
        for name, value in attributes.items():
            setattr(target, name, value)
        yield
    finally:
        for name, value in saved.items():
            setattr(target, name, value)


@contextlib.contextmanager
def environment(**values):
    """Set (or, with None, remove) environment variables for one block."""
    saved = {name: os.environ.get(name) for name in values}
    try:
        for name, value in values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class _Tty(io.StringIO):
    def isatty(self):
        return True


@contextlib.contextmanager
def streams(stdin=True, stdout=True, stderr=True):
    """Swap the three standard streams for fakes that are, or are not, terminals."""
    fakes = [(_Tty if tty else io.StringIO)() for tty in (stdin, stdout, stderr)]
    saved = (sys.stdin, sys.stdout, sys.stderr)
    sys.stdin, sys.stdout, sys.stderr = fakes
    try:
        yield fakes
    finally:
        sys.stdin, sys.stdout, sys.stderr = saved


@contextlib.contextmanager
def installation(built=True):
    """A stand-in INSTALL_DIR, with or without `tui/dist/main.js` in it."""
    directory = Path(tempfile.mkdtemp(prefix="tmt_tsui_install_")).resolve()
    if built:
        (directory / "tui" / "dist").mkdir(parents=True)
        (directory / "tui" / "dist" / "main.js").write_text("// built\n")
    previous = agent_config.INSTALL_DIR
    agent_config.INSTALL_DIR = directory
    try:
        yield directory
    finally:
        agent_config.INSTALL_DIR = previous
        shutil.rmtree(str(directory), ignore_errors=True)


class _Process:
    """What `subprocess.Popen` returns, for a front end that never ran."""

    def __init__(self, code=0, interrupts=0):
        self.code, self.interrupts, self.killed = code, interrupts, False

    def wait(self, timeout=None):
        if self.interrupts:
            self.interrupts -= 1
            raise KeyboardInterrupt
        return self.code

    def kill(self):
        self.killed = True


def fake_popen(process=None, error=None):
    """A Popen that records its calls and starts nothing. Returns (popen, calls)."""
    calls = []

    def popen(command, **kwargs):
        calls.append((list(command), kwargs))
        if error is not None:
            raise error
        return process if process is not None else _Process()

    return popen, calls


def watching_popen():
    """A Popen that passes real commands through and records any that name Node.

    For the tests that drive `TMT.main`, where the session may legitimately start
    other processes: only a command that is the front end is recorded and
    refused, so the assertion is "Node was never started" and nothing else.
    """
    real = agent_tsui.subprocess.Popen
    node_calls = []

    def popen(command, *args, **kwargs):
        text = " ".join(str(part) for part in command)
        if "main.js" in text or "/fake/node" in text:
            node_calls.append(list(command))
            raise AssertionError("the TypeScript front end was started: %r" % (command,))
        return real(command, *args, **kwargs)

    return popen, node_calls


# --- the vocabulary and the default -------------------------------------------

def test_the_front_ends_the_clicks_and_the_defaults_are_what_was_asked_for():
    """Spelled out as literals, not read back off the constants."""
    assert agent_config.UIS == ("python", "typescript"), agent_config.UIS
    assert agent_config.DEFAULT_UI == "python"
    assert agent_config.ui_names() == ["python", "typescript"]
    agent_config.ui_names().append("xml")           # a copy: cannot reorder Settings
    assert agent_config.ui_names() == ["python", "typescript"]
    assert agent_config.MOUSES == ("off", "on"), agent_config.MOUSES
    assert agent_config.DEFAULT_MOUSE == "off"


def test_a_missing_file_is_a_fresh_installation_and_reads_as_the_default():
    with Ui(None) as box:
        assert not box.path.exists()
        assert agent_config.read_saved_ui() == "python"
        assert agent_config.refresh_ui() == "python"
        assert agent_config.UI == "python"
    with Mouse(None) as box:
        assert not box.path.exists()
        assert agent_config.read_saved_mouse() == "off"
        assert agent_config.refresh_mouse() == "off"
        assert agent_config.MOUSE == "off"


def test_a_file_that_cannot_be_read_is_the_default_and_never_an_error():
    """A directory where the file should be raises OSError, and a file that is
    not text raises ValueError on decode. Both are caught: a launch survives
    both, and neither is a request for the other value."""
    for box_type, read, refresh, default in (
            (Ui, agent_config.read_saved_ui, agent_config.refresh_ui, "python"),
            (Mouse, agent_config.read_saved_mouse, agent_config.refresh_mouse, "off")):
        with box_type(None) as box:
            box.aim_at(box.dir)                       # a directory: unreadable
            assert read() == default
            box.aim_at(box.dir / "no" / "such" / "place" / box.NAME)
            assert read() == default
        with box_type(None) as box:
            box.path.write_bytes(b"\xff\xfe\x00\x80 not text \xc3\x28")
            assert read() == default
            assert refresh() == default


def test_an_unrecognised_value_is_the_default_and_not_the_other_one():
    """Nonsense is a typo, not a request. In particular it must not read as the
    non-default value: a hand-mangled file is not evidence of a choice."""
    for text in ("", "\n", "ts", "typescrip", "typescripts", "python typescript",
                 "1", "true", "on", "node", "{\"ui\": \"typescript\"}",
                 "typescript,python"):
        with Ui(None, contents=text) as box:
            assert box.path.exists()
            assert agent_config.read_saved_ui() == "python", repr(text)
    for text in ("", "\n", "yes", "true", "1", "onn", "of", "on off", "enabled"):
        with Mouse(None, contents=text) as box:
            assert box.path.exists()
            assert agent_config.read_saved_mouse() == "off", repr(text)


def test_the_stored_word_is_read_case_and_whitespace_insensitively():
    """An editor adds a newline and a person types however they like."""
    for text in ("typescript", "TYPESCRIPT", "TypeScript", " typescript ",
                 "typescript\n", "\ttypescript\r\n"):
        with Ui(None, contents=text):
            assert agent_config.read_saved_ui() == "typescript", repr(text)
    for text in ("python", "PYTHON", " Python\n"):
        with Ui(None, contents=text):
            assert agent_config.read_saved_ui() == "python", repr(text)
    for text in ("on", "ON", "On", " on ", "on\n", "\ton\r\n"):
        with Mouse(None, contents=text):
            assert agent_config.read_saved_mouse() == "on", repr(text)
    for text in ("off", "OFF", " Off\n"):
        with Mouse(None, contents=text):
            assert agent_config.read_saved_mouse() == "off", repr(text)


# --- setting them ---------------------------------------------------------------

def test_a_set_front_end_is_stored_live_and_survives_a_fresh_read():
    """Every persistence assertion reads the file and `read_saved_ui()` rather
    than trusting the global the setter assigned: the point is the next launch."""
    with Ui(None) as box:
        assert agent_config.set_ui("typescript") == "typescript"
        assert agent_config.UI == "typescript"
        assert box.path.read_text(encoding="utf-8") == "typescript\n"
        assert agent_config.read_saved_ui() == "typescript"
        agent_config.UI = "python"                    # as a fresh import would have it
        assert agent_config.refresh_ui() == "typescript"
        assert agent_config.UI == "typescript"
        assert agent_config.set_ui("python") == "python"
        assert box.path.read_text(encoding="utf-8") == "python\n"
        assert agent_config.read_saved_ui() == "python"
        assert agent_config.set_ui("  TypeScript\n") == "typescript"
        assert box.path.read_text(encoding="utf-8") == "typescript\n"


def test_a_set_click_setting_is_stored_live_and_survives_a_fresh_read():
    with Mouse(None) as box:
        assert agent_config.set_mouse("on") == "on"
        assert agent_config.MOUSE == "on"
        assert box.path.read_text(encoding="utf-8") == "on\n"
        assert agent_config.read_saved_mouse() == "on"
        agent_config.MOUSE = "off"
        assert agent_config.refresh_mouse() == "on"
        assert agent_config.set_mouse(" OFF ") == "off"
        assert box.path.read_text(encoding="utf-8") == "off\n"
        assert agent_config.read_saved_mouse() == "off"


def test_set_ui_and_set_mouse_refuse_an_unknown_name_and_name_the_options():
    """The refusal leaves the file and the live value exactly as they were, and
    has the wording shape every other setter in the module uses."""
    with Ui("python") as box:
        before = box.path.read_text(encoding="utf-8")
        for bad in ("ts", "node", "Python 3", "", None, True, 0):
            raised = None
            try:
                agent_config.set_ui(bad)
            except ValueError as error:
                raised = error
            assert raised is not None, "accepted %r" % (bad,)
            assert "python" in str(raised) and "typescript" in str(raised), str(raised)
            assert agent_config.UI == "python", repr(bad)
            assert box.path.read_text(encoding="utf-8") == before, repr(bad)
        try:
            agent_config.set_ui("xml")
        except ValueError as error:
            assert str(error) == "UI is one of python, typescript; got 'xml'.", str(error)
        else:
            raise AssertionError("an unknown name was accepted")
    with Mouse("off") as box:
        before = box.path.read_text(encoding="utf-8")
        for bad in ("yes", "true", "1", "", None, True, 0):
            raised = None
            try:
                agent_config.set_mouse(bad)
            except ValueError as error:
                raised = error
            assert raised is not None, "accepted %r" % (bad,)
            assert "off" in str(raised) and "on" in str(raised), str(raised)
            assert agent_config.MOUSE == "off", repr(bad)
            assert box.path.read_text(encoding="utf-8") == before, repr(bad)


def test_a_write_that_cannot_be_made_is_raised_rather_than_lost():
    """The one path through either setting that does NOT default quietly: a
    toggle that silently did not persist would show one value in Settings and
    be the other on the next launch. The live value must not move either."""
    for box_type, name, other, setter, value_name in (
            (Ui, "python", "typescript", agent_config.set_ui, "UI"),
            (Mouse, "off", "on", agent_config.set_mouse, "MOUSE")):
        with box_type(name) as box:
            box.aim_at(box.dir / "no" / "such" / "place" / box.NAME)
            raised = None
            try:
                setter(other)
            except OSError as error:
                raised = error
            assert raised is not None, "a failed write reported success"
            assert getattr(agent_config, value_name) == name
        with box_type(name) as box:
            box.aim_at(box.dir)                       # a directory cannot be written
            raised = None
            try:
                setter(other)
            except OSError as error:
                raised = error
            assert raised is not None, "a failed write reported success"
            assert getattr(agent_config, value_name) == name


# --- where they live --------------------------------------------------------------

def test_the_files_belong_to_the_installation_and_are_git_ignored():
    """The suite does not redirect these two, so the live attributes ARE the
    product's. Per-install choices, beside `.tmt_protocol`, and never source."""
    assert agent_config.UI_FILE == INSTALL_DIR / ".tmt_ui", agent_config.UI_FILE
    assert agent_config.MOUSE_FILE == INSTALL_DIR / ".tmt_mouse", agent_config.MOUSE_FILE
    lines = [line.strip() for line in
             (INSTALL_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert ".tmt_ui" in lines, "`.tmt_ui` is not ignored"
    assert ".tmt_mouse" in lines, "`.tmt_mouse` is not ignored"
    assert ".tmt_protocol" in lines, "the setting these were modelled on moved"


def test_the_sandbox_puts_the_paths_and_values_back():
    path, value = agent_config.UI_FILE, agent_config.UI
    mouse_path, mouse_value = agent_config.MOUSE_FILE, agent_config.MOUSE
    with Ui("typescript") as box, Mouse("on") as mouse:
        assert agent_config.UI_FILE == box.path
        assert agent_config.MOUSE_FILE == mouse.path
        assert agent_config.read_saved_ui() == "typescript"
        assert agent_config.read_saved_mouse() == "on"
    assert agent_config.UI_FILE == path and agent_config.UI == value
    assert agent_config.MOUSE_FILE == mouse_path and agent_config.MOUSE == mouse_value
    assert not box.dir.exists() and not mouse.dir.exists()


# --- the Settings rows -------------------------------------------------------------

def _rows(columns=100, selected=0):
    frame = agent_menu.render_settings_menu_frame(
        selected, io.StringIO(), size=(columns, 40))
    return [agent_ui.strip_ansi(row) for row in frame]


def _row(label, columns=100):
    found = [text for text in _rows(columns) if label in text]
    assert len(found) == 1, found
    return found[0]


def test_settings_offers_both_rows_above_the_danger_zone_with_back_last():
    ids = [item[0] for item in agent_menu.SETTINGS_ITEMS]
    labels = {item[0]: item[1] for item in agent_menu.SETTINGS_ITEMS}
    details = {item[0]: item[2] for item in agent_menu.SETTINGS_ITEMS}
    assert labels["ui"] == "UI" and labels["mouse"] == "Click To Place Caret", labels
    assert ids[-2:] == ["danger", "back"], ids
    assert ids.index("protocol") < ids.index("ui") < ids.index("mouse") < ids.index("danger"), ids
    assert "next start" in details["ui"].lower(), details["ui"]
    assert "typescript" in details["mouse"].lower(), details["mouse"]
    # Other tests count the rows that carry the word OFF to find one switch.
    for key in ("ui", "mouse"):
        assert "OFF" not in details[key] and "ON" not in details[key], details[key]
    widest = max(agent_ui.display_width(item[1]) for item in agent_menu.SETTINGS_ITEMS)
    assert widest == agent_ui.display_width("Auto Update on Launch"), widest


def test_the_ui_row_says_it_takes_effect_next_start_on_an_80_column_terminal():
    """The state is protected from trimming and the description is not, so the
    sentence has to be short enough to survive beside the LONGER state."""
    for value in ("python", "typescript"):
        with Ui(value):
            for columns in (80, 100, 120):
                row = _row("Takes effect", columns)
                assert "next start" in row, (columns, row)
                assert row.rstrip().endswith(value.upper()), (columns, row)


def test_the_rows_read_in_words_and_follow_the_file():
    assert agent_menu.UI_LABELS == {"python": "PYTHON", "typescript": "TYPESCRIPT"}
    assert agent_menu.MOUSE_LABELS == {"off": "OFF", "on": "ON"}
    with Ui(None), Mouse(None):
        assert agent_menu.ui_text() == "PYTHON"
        assert agent_menu.mouse_text() == "OFF"
        assert _row("Takes effect").rstrip().endswith("PYTHON")
        assert _row("Click To Place Caret").rstrip().endswith("OFF")
    with Ui("typescript"), Mouse("on"):
        assert agent_menu.ui_text() == "TYPESCRIPT"
        assert agent_menu.mouse_text() == "ON"
        assert _row("Takes effect").rstrip().endswith("TYPESCRIPT")
        assert _row("Click To Place Caret").rstrip().endswith("ON")


def test_each_row_changes_in_exactly_one_line_when_its_setting_changes():
    with Ui("python") as box:
        before = _rows()
        box.write("typescript\n")
        after = _rows()
    differing = [pair for pair in zip(before, after) if pair[0] != pair[1]]
    assert len(differing) == 1, differing
    assert "Takes effect" in differing[0][0], differing[0]
    with Mouse("off") as box:
        before = _rows()
        box.write("on\n")
        after = _rows()
    differing = [pair for pair in zip(before, after) if pair[0] != pair[1]]
    assert len(differing) == 1, differing
    assert "Click To Place Caret" in differing[0][0], differing[0]


def test_the_rows_survive_a_settings_file_or_reader_that_fails():
    """A menu that could not be drawn because of a settings file would be the
    config file stopping TMT one screen later than the reader refuses to."""
    with Ui("typescript") as ui, Mouse("on") as mouse:
        ui.aim_at(ui.dir)
        mouse.aim_at(mouse.dir / "no" / "such" / "place")
        assert agent_menu.ui_text() == "PYTHON"
        assert agent_menu.mouse_text() == "OFF"
    real = agent_config.read_saved_ui, agent_config.read_saved_mouse

    def explodes():
        raise RuntimeError("a reader that was documented never to raise")

    try:
        agent_config.read_saved_ui = agent_config.read_saved_mouse = explodes
        assert agent_menu.ui_text() == "PYTHON"
        assert agent_menu.mouse_text() == "OFF"
    finally:
        agent_config.read_saved_ui, agent_config.read_saved_mouse = real


def test_every_settings_row_fits_the_terminal_it_was_drawn_for():
    """Measured, never counted: a row filled past the last column wraps and
    costs a screen line the repaint arithmetic does not know about. The longest
    state, TYPESCRIPT, is the one that could push a row over."""
    with Ui("typescript"), Mouse("on"):
        for columns in (30, 40, 50, 60, 80, 120, 200):
            for selected in range(len(agent_menu.SETTINGS_ITEMS)):
                frame = agent_menu.render_settings_menu_frame(
                    selected, io.StringIO(), size=(columns, 40))
                for row in frame:
                    width = agent_ui.display_width(agent_ui.strip_ansi(row))
                    assert width <= columns - 1, (columns, width, row)


def _drive(entry, presses=1):
    """Press Enter `presses` times on one Settings row through the real screen."""
    ids = [item[0] for item in agent_menu.SETTINGS_ITEMS]
    stream = io.StringIO()
    region = Frames()
    keys = Keys(*(["down"] * ids.index(entry) + ["enter"] * presses + ["esc"]))
    chosen = agent_menu.settings_screen(stream=stream, key_reader=keys, region=region)
    assert chosen is None, chosen
    assert keys.remaining == 0, keys.remaining
    assert stream.getvalue() == "", "the screen wrote past its own region"
    return region


def test_driving_the_real_settings_screen_flips_each_file_on_disk_and_back():
    """The wire between a keystroke and the file, through the real `_drive`
    loop. A switch, not a one-way door; and the two rows own different files."""
    with Ui("python") as ui, Mouse("off") as mouse:
        for expected in ("typescript", "python", "typescript"):
            _drive("ui")
            assert agent_config.read_saved_ui() == expected, expected
            assert ui.path.read_text(encoding="utf-8") == expected + "\n"
            assert agent_config.read_saved_mouse() == "off"
            assert mouse.path.read_text(encoding="utf-8") == "off\n"
        for expected in ("on", "off", "on"):
            _drive("mouse")
            assert agent_config.read_saved_mouse() == expected, expected
            assert agent_config.read_saved_ui() == "typescript"


def test_selecting_either_row_does_not_open_a_screen():
    """Every frame painted is still the settings frame, and the frame after the
    Enter shows the new value on the row."""
    with Ui("python"), Mouse("off"):
        region = _drive("ui")
        assert region.frames
        for frame in region.frames:
            text = "\n".join(agent_ui.strip_ansi(row) for row in frame)
            assert "Settings" in text and "Takes effect" in text, text
        assert any("Takes effect" in row and row.rstrip().endswith("TYPESCRIPT")
                   for row in (agent_ui.strip_ansi(text) for text in region.frames[-1]))
        region = _drive("mouse")
        for frame in region.frames:
            text = "\n".join(agent_ui.strip_ansi(row) for row in frame)
            assert "Settings" in text and "Click To Place Caret" in text, text
        assert any("Click To Place Caret" in row and row.rstrip().endswith("ON")
                   for row in (agent_ui.strip_ansi(text) for text in region.frames[-1]))


def test_leaving_settings_by_another_row_changes_neither_setting():
    ids = [item[0] for item in agent_menu.SETTINGS_ITEMS]
    with Ui("python") as ui, Mouse("off") as mouse:
        keys = Keys(*(["down"] * ids.index("back") + ["enter"]))
        agent_menu.settings_screen(stream=io.StringIO(), key_reader=keys, region=Frames())
        assert ui.path.read_text(encoding="utf-8") == "python\n"
        assert mouse.path.read_text(encoding="utf-8") == "off\n"


def test_toggles_never_raise_and_report_what_is_in_force():
    """A failed write is reported by the row not changing, and the alternative,
    a traceback out of a menu, would leave the terminal in raw mode."""
    with Ui("python") as ui, Mouse("off") as mouse:
        ui.aim_at(ui.dir)                             # a directory: unwritable
        mouse.aim_at(mouse.dir)
        assert agent_menu.toggle_ui() == "PYTHON"
        assert agent_menu.toggle_mouse() == "OFF"
        _drive("ui")                                  # and through the real loop
        _drive("mouse")
    with Ui("python"), Mouse("off"):
        assert agent_menu.toggle_ui() == "TYPESCRIPT"
        assert agent_menu.toggle_ui() == "PYTHON"
        assert agent_menu.toggle_mouse() == "ON"
        assert agent_menu.toggle_mouse() == "OFF"
    # From a fresh installation the first press goes to the OTHER value.
    with Ui(None), Mouse(None):
        assert agent_menu.toggle_ui() == "TYPESCRIPT"
        assert agent_menu.toggle_mouse() == "ON"


def test_the_toggle_flips_the_file_and_not_a_stale_live_value():
    """The row is drawn from DISK, so the press has to flip what is on disk."""
    with Ui("python") as box:
        agent_config.UI = "typescript"                # the live value is behind the file
        assert agent_menu.ui_text() == "PYTHON"
        assert agent_menu.toggle_ui() == "TYPESCRIPT"
        assert box.path.read_text(encoding="utf-8") == "typescript\n"


# --- /config -------------------------------------------------------------------------

def test_config_reports_both_settings():
    with Ui("typescript"), Mouse("on"):
        result = agent_commands.dispatch("/config")
        assert result.ok
        text = result.text()
    ui = [line for line in text.splitlines() if line.split()[:1] == ["UI"]]
    assert len(ui) == 1, text
    assert ui[0].split()[1] == "typescript", ui[0]
    assert "next start" in ui[0], ui[0]
    mouse = [line for line in text.splitlines() if "Click to place caret" in line]
    assert len(mouse) == 1, text
    assert mouse[0].split()[-1] == "on", mouse[0]
    with Ui(None), Mouse(None):
        text = agent_commands.dispatch("/config").text()
    assert [line for line in text.splitlines()
            if line.split()[:1] == ["UI"]][0].split()[1] == "python"
    assert [line for line in text.splitlines()
            if "Click to place caret" in line][0].split()[-1] == "off"


# --- the flags ---------------------------------------------------------------------------

def test_parse_args_takes_ui_python_or_typescript_and_nothing_else():
    assert TMT.parse_args([]).ui is None
    assert TMT.parse_args(["--ui", "python"]).ui == "python"
    assert TMT.parse_args(["--ui", "typescript"]).ui == "typescript"
    assert TMT.parse_args(["--ui=typescript", "somewhere"]).directory == "somewhere"
    for bad in (["--ui", "ts"], ["--ui", "node"], ["--ui", ""], ["--ui"]):
        code, err = None, io.StringIO()
        with contextlib.redirect_stderr(err):
            try:
                TMT.parse_args(bad)
            except SystemExit as error:
                code = error.code
        assert code == 2, (bad, code)
    assert "python" in err.getvalue() or "--ui" in err.getvalue(), err.getvalue()
    # And the other flags are untouched by it.
    args = TMT.parse_args(["--ci", "--ui", "python", "run", "it"])
    assert args.ci is True and args.task_words == ["run", "it"], args


def test_backend_stdio_is_accepted_defaults_off_and_is_hidden_from_help():
    assert TMT.parse_args([]).backend_stdio is False
    assert TMT.parse_args(["--backend-stdio"]).backend_stdio is True
    shown, code = io.StringIO(), None
    with contextlib.redirect_stdout(shown):
        try:
            TMT.parse_args(["--help"])
        except SystemExit as error:
            code = error.code
    assert code == 0, code
    assert "--ui" in shown.getvalue()
    assert "backend-stdio" not in shown.getvalue(), "the flag is for the front end, not people"


# --- which front end a run asked for -----------------------------------------------------

def test_the_flag_beats_the_environment_beats_the_saved_choice():
    with Ui("typescript"):
        with environment(TMT_UI=None):
            assert agent_tsui.chosen_ui(None) == "typescript"
            assert agent_tsui.chosen_ui("python") == "python"
        with environment(TMT_UI="python"):
            assert agent_tsui.chosen_ui(None) == "python"
            assert agent_tsui.chosen_ui("typescript") == "typescript"
        with environment(TMT_UI=" TypeScript "):
            assert agent_tsui.chosen_ui(None) == "typescript"
    with Ui("python"), environment(TMT_UI="typescript"):
        assert agent_tsui.chosen_ui(None) == "typescript"
        assert agent_tsui.chosen_ui("python") == "python"


def test_a_value_that_is_not_a_front_end_is_skipped_never_refused():
    """A typo in the environment must not stop TMT starting any more than one in
    the file can: it reads as if it had not been set."""
    with Ui("typescript"), environment(TMT_UI="typescrpt"):
        assert agent_tsui.chosen_ui(None) == "typescript"
        assert agent_tsui.chosen_ui("banana") == "typescript"
    with Ui(None, contents="garbage"), environment(TMT_UI=None):
        assert agent_tsui.chosen_ui(None) == "python"


# --- finding Node and the build ------------------------------------------------------------

def test_find_node_wants_node_22_or_newer():
    which_missing = lambda name: None
    which_found = lambda name: "C:/fake/node.exe"
    with patched(agent_tsui.shutil, which=which_missing):
        assert agent_tsui.find_node() is None
    with patched(agent_tsui.shutil, which=which_found):
        for major, expected in ((None, None), (18, None), (21, None),
                                (22, "C:/fake/node.exe"), (24, "C:/fake/node.exe")):
            with patched(agent_tsui, _node_major=lambda node, m=major: m):
                assert agent_tsui.find_node() == expected, major


def test_the_node_version_is_read_from_its_own_answer_and_never_raises():
    def runner(stdout="", code=0, error=None):
        def run(command, **kwargs):
            if error is not None:
                raise error
            return types.SimpleNamespace(stdout=stdout, returncode=code)
        return run

    for answer, expected in (("v22.23.2\n", 22), ("v20.1.0", 20), ("24.0.0", 24),
                             ("nonsense", None), ("", None)):
        with patched(agent_tsui.subprocess, run=runner(stdout=answer)):
            assert agent_tsui._node_major("node") == expected, answer
    with patched(agent_tsui.subprocess, run=runner(stdout="v22.0.0", code=1)):
        assert agent_tsui._node_major("node") is None
    with patched(agent_tsui.subprocess, run=runner(error=OSError("no such file"))):
        assert agent_tsui._node_major("node") is None


def test_find_entry_is_the_built_main_js_or_none():
    with installation(built=False):
        assert agent_tsui.find_entry() is None
    with installation(built=True) as directory:
        assert agent_tsui.find_entry() == directory / "tui" / "dist" / "main.js"


def test_the_unavailable_reason_names_what_is_missing_and_the_build_command():
    build = "cd tui && npm install && npm run build"
    assert agent_tsui.BUILD_COMMAND == build
    with patched(agent_tsui, find_node=lambda: "node", find_entry=lambda: Path("x")):
        assert agent_tsui.unavailable_reason() is None
    with patched(agent_tsui, find_node=lambda: None, find_entry=lambda: Path("x")):
        reason = agent_tsui.unavailable_reason()
        assert "Node.js 22" in reason and build in reason, reason
    with patched(agent_tsui, find_node=lambda: "node", find_entry=lambda: None):
        reason = agent_tsui.unavailable_reason()
        assert "tui/dist/main.js" in reason and build in reason, reason
    with patched(agent_tsui, find_node=lambda: None, find_entry=lambda: None):
        assert build in agent_tsui.unavailable_reason()


# --- launch: None means "run the Python UI" ---------------------------------------------------

def _launch(argv=(), *, node="/fake/node", built=True, tty=(True, True, True),
            process=None, error=None, saved="typescript", env_ui=None, looked=None):
    """`agent_tsui.launch` with everything it could touch replaced.

    `looked` collects a mark each time Node is searched for. Returns
    (result, spawned commands, what reached stderr).
    """
    popen, calls = fake_popen(process=process, error=error)
    looked = [] if looked is None else looked
    with installation(built=built), Ui(saved), environment(TMT_UI=env_ui), \
            patched(agent_tsui, find_node=lambda: looked.append(1) or node), \
            patched(agent_tsui.subprocess, Popen=popen), \
            streams(*tty) as (_stdin, _stdout, stderr):
        result = agent_tsui.launch(list(argv))
        text = stderr.getvalue()
    return result, calls, text


def test_launch_runs_node_on_the_entry_with_the_original_arguments():
    process = _Process(code=7)
    with installation(built=True) as directory, Ui("typescript"), \
            environment(TMT_UI=None), patched(agent_tsui, find_node=lambda: "/fake/node"):
        popen, calls = fake_popen(process=process)
        with patched(agent_tsui.subprocess, Popen=popen), streams() as (_i, _o, err):
            code = agent_tsui.launch(["--dir", "somewhere", "--ui", "typescript"])
            assert err.getvalue() == "", err.getvalue()
    assert code == 7, code
    assert len(calls) == 1, calls
    command, kwargs = calls[0]
    assert command == ["/fake/node", str(directory / "tui" / "dist" / "main.js"),
                       "--dir", "somewhere", "--ui", "typescript"], command
    # Inherited stdio: nothing redirected, because Node owns the terminal.
    for stream in ("stdin", "stdout", "stderr"):
        assert stream not in kwargs, kwargs
    assert kwargs["env"]["TMT_PYTHON"] == sys.executable
    assert kwargs["env"]["TMT_ENTRY"] == str(directory / "TMT.py"), kwargs["env"]["TMT_ENTRY"]


def test_launch_answers_none_when_node_is_missing_or_the_build_is():
    for node, built, mentions in ((None, True, "Node.js 22"),
                                  ("/fake/node", False, "tui/dist/main.js"),
                                  (None, False, "npm run build")):
        result, calls, text = _launch(["--dir", "x"], node=node, built=built)
        assert result is None, (node, built, result)
        assert calls == [], "started a process it had no front end for"
        # ONE line, naming what is missing and the exact build command.
        assert len(text.strip().splitlines()) == 1, text
        assert mentions in text and "cd tui && npm install && npm run build" in text, text
        assert "Python UI" in text, text


def test_launch_answers_none_when_stdin_or_stdout_is_not_a_terminal():
    """Checked before Node is even looked for: a piped run never pays for a
    `node --version`, and gets the Python UI exactly as it always did."""
    for tty in ((False, True, True), (True, False, True), (False, False, True)):
        looked = []
        result, calls, text = _launch(tty=tty, node="/fake/node", looked=looked)
        assert result is None, tty
        assert calls == [], tty
        assert looked == [], "searched for Node on a run that cannot use it"
        assert "interactive terminal" in text, text
        assert len(text.strip().splitlines()) == 1, text


def test_the_fallback_notice_is_for_a_person_and_never_for_a_pipe():
    """One line on stderr, and only when stderr is a terminal: a line written
    into a log or a pipe would be noise on a run nobody is looking at."""
    for built, node in ((False, "/fake/node"), (True, None)):
        result, _calls, text = _launch(built=built, node=node, tty=(True, True, False))
        assert result is None
        assert text == "", repr(text)
    result, _calls, text = _launch(tty=(False, False, False))
    assert result is None and text == "", repr(text)


def test_an_explicit_python_override_answers_none_without_a_notice():
    for argv, env_ui in ((["--ui", "python"], None), (["--ui=python"], None),
                         ([], "python"), (["--ui", "typescript", "--ui", "python"], None)):
        result, calls, text = _launch(argv, env_ui=env_ui)
        assert result is None, (argv, env_ui)
        assert calls == [] and text == "", (argv, env_ui, text)
    # The saved choice alone, python: the same, so a stray call cannot start Node.
    result, calls, text = _launch([], saved="python")
    assert result is None and calls == [] and text == ""
    # And the flag can ask for TypeScript against a saved python.
    result, calls, _text = _launch(["--ui", "typescript"], saved="python")
    assert result == 0 and len(calls) == 1, (result, calls)


def test_launch_never_raises_into_main_when_the_process_will_not_spawn():
    for error in (OSError("exec format error"), PermissionError("denied"),
                  RuntimeError("anything at all")):
        result, calls, text = _launch(error=error)
        assert result is None, error
        assert len(calls) == 1
        assert "could not be started" in text and type(error).__name__ in text, text
        assert len(text.strip().splitlines()) == 1, text


def test_ctrl_c_while_the_front_end_runs_is_130_and_gives_node_time_to_leave():
    process = _Process(code=0, interrupts=1)
    result, calls, _text = _launch(process=process)
    assert result == 130, result
    assert process.killed is False, "Node must be allowed to restore the terminal"
    stubborn = _Process(code=0, interrupts=2)             # interrupted again while waiting
    result, calls, _text = _launch(process=stubborn)
    assert result == 130, result
    assert stubborn.killed is True


# --- main: the dispatch ----------------------------------------------------------------------

def test_main_runs_the_python_ui_when_typescript_is_saved_and_there_is_no_terminal():
    """The piped path, unchanged: stdin and stdout are not terminals, so the
    saved choice never reaches Node and the session runs exactly as before."""
    popen, node_calls = watching_popen()
    with Ui("typescript"), environment(TMT_UI=None), \
            patched(agent_tsui.subprocess, Popen=popen):
        drawn = run_session(["", "quit"])
    assert node_calls == [], node_calls
    assert prompt_boxes(drawn) == 2, drawn


def test_main_runs_the_python_ui_when_typescript_is_saved_but_not_built():
    """The milestone's own case: the choice is made, `tui/dist` does not exist,
    and TMT starts anyway -- with one line saying why."""
    popen, node_calls = watching_popen()
    err = io.StringIO()
    with Ui("typescript"), environment(TMT_UI=None), \
            patched(agent_tsui.subprocess, Popen=popen), \
            patched(agent_tsui, _is_tty=lambda stream: True,
                    find_node=lambda: "/fake/node", find_entry=lambda: None), \
            contextlib.redirect_stderr(err):
        drawn = run_session(["", "quit"])
    assert node_calls == [], node_calls
    assert prompt_boxes(drawn) == 2, drawn
    notice = err.getvalue().strip().splitlines()
    assert len(notice) == 1, notice
    assert "Python UI" in notice[0] and "npm run build" in notice[0], notice


def test_main_returns_the_front_ends_exit_code_without_resolving_a_workspace():
    seen = []

    def launched(argv):
        seen.append(list(argv))
        return 7

    def refuses(*args, **kwargs):
        raise AssertionError("the Python UI started")

    with Ui("typescript"), environment(TMT_UI=None), \
            patched(agent_tsui, launch=launched), \
            patched(TMT, resolve_workspace=refuses, run_splash=refuses):
        assert TMT.main(["--dir", "somewhere"]) == 7
        saved_argv = sys.argv
        try:
            sys.argv = ["tmtcode", "--ui", "typescript", "other"]
            assert TMT.main() == 7
        finally:
            sys.argv = saved_argv
    assert seen == [["--dir", "somewhere"], ["--ui", "typescript", "other"]], seen


def test_main_falls_through_to_the_python_ui_when_launch_answers_none():
    reached = []

    def workspace(*args, **kwargs):
        reached.append("workspace")
        return None                                   # stop right there

    with Ui("typescript"), environment(TMT_UI=None), \
            patched(agent_tsui, launch=lambda argv: None), \
            patched(TMT, resolve_workspace=workspace):
        assert TMT.main([]) is None
    assert reached == ["workspace"], reached


def test_main_never_asks_for_the_front_end_when_python_is_chosen():
    """Saved python, `--ui python` against a saved typescript, and TMT_UI=python
    against a saved typescript: `launch` is not called at all."""
    asked = []

    def launched(argv):
        asked.append(argv)
        return 9

    def workspace(*args, **kwargs):
        return None

    for saved, argv, env_ui in (("python", [], None),
                                ("typescript", ["--ui", "python"], None),
                                ("typescript", [], "python")):
        with Ui(saved), environment(TMT_UI=env_ui), \
                patched(agent_tsui, launch=launched), \
                patched(TMT, resolve_workspace=workspace):
            assert TMT.main(list(argv)) is None, (saved, argv, env_ui)
    assert asked == [], asked
    # And the flag and the variable can each ask for TypeScript.
    with Ui("python"), environment(TMT_UI=None), patched(agent_tsui, launch=launched):
        assert TMT.main(["--ui", "typescript"]) == 9
    with Ui("python"), environment(TMT_UI="typescript"), patched(agent_tsui, launch=launched):
        assert TMT.main([]) == 9


def test_ci_never_asks_for_the_front_end_whichever_one_is_saved():
    """`--ci` returns from `main` before the dispatch, so a pipeline behaves
    exactly as before with typescript saved, in the environment or on the line."""
    asked = []

    def launched(argv):
        asked.append(argv)
        return 9

    for argv, env_ui in ((["--ci", "fix it"], None),
                         (["--ci", "--ui", "typescript", "fix it"], None),
                         (["--ci", "fix it"], "typescript")):
        with Ui("typescript"), environment(TMT_UI=env_ui), \
                patched(agent_tsui, launch=launched):
            code, out, err, box, seen = ci(argv)
        try:
            assert code == 0, (argv, code, err)
            assert len(seen) == 1, "the scripted model was not asked"
        finally:
            box.close()
    assert asked == [], asked


def test_backend_stdio_goes_to_the_backend_before_any_workspace_or_front_end():
    """The hidden flag's dispatch, with `agent_backend` stood in for: whichever
    lane has written the real one, this proves the wiring and not the module."""
    received = []
    fake = types.ModuleType("agent_backend")
    fake.run = lambda args: received.append(args) or 42

    def refuses(*args, **kwargs):
        raise AssertionError("went past the backend")

    missing = object()
    previous = sys.modules.get("agent_backend", missing)
    try:
        sys.modules["agent_backend"] = fake
        with Ui("typescript"), environment(TMT_UI="typescript"), \
                patched(agent_tsui, launch=refuses), \
                patched(TMT, resolve_workspace=refuses, run_splash=refuses):
            assert TMT.main(["--backend-stdio", "--dir", "somewhere"]) == 42
        assert len(received) == 1 and received[0].backend_stdio is True, received
        assert received[0].directory == "somewhere", received[0]

        # A backend that is not there: one line on stderr and exit code 2,
        # never a traceback and never the Python UI.
        sys.modules["agent_backend"] = None
        err = io.StringIO()
        with patched(TMT, resolve_workspace=refuses, run_splash=refuses), \
                contextlib.redirect_stderr(err):
            assert TMT.main(["--backend-stdio"]) == 2
        lines = err.getvalue().strip().splitlines()
        assert len(lines) == 1 and "--backend-stdio" in lines[0], lines
    finally:
        if previous is missing:
            sys.modules.pop("agent_backend", None)
        else:
            sys.modules["agent_backend"] = previous
