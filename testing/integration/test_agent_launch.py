"""The launch sequence: the setting, the flow it drives, and the routing.

`testing/unit/test_agent_splash.py` protects the screen -- what is drawn, how
it degrades, how the state machine moves. `testing/unit/test_agent_update.py`
protects the git decisions -- what is fetched, what is refused, what may never
be run. This file protects the three things that only exist once those two are
wired together, and every one of them is a place the feature could be correct
in isolation and useless in practice.

THE SETTING. `Auto Update on Launch` is one boolean in one file, and it is the
only part of this feature a user can change. What matters about it is not that
it can be written but that it comes BACK: the whole point of storing it is the
next launch, so every persistence test here reads through a fresh
`read_saved_auto_update()` rather than trusting the module-level value the
setter just assigned. It also has to fail in one direction only -- a missing,
unreadable or hand-mangled file is not evidence that anybody asked for the
feature to be off, and it must never be what stops TMT starting.

THE MENU. A setting nobody can reach is not a setting. The row has to exist,
say what is actually on disk with the escapes stripped, toggle in place rather
than opening a screen, and survive a terminal too narrow to draw its own
description.

THE ROUTING. The splash comes before the API key, the setting decides only
whether the update runs, a current checkout never restarts, an applied one
restarts exactly once, and every refusal continues into TMT. Those are six
different wrong answers and each has a test.

**Nothing here touches the real `.tmt_autoupdate`.** `AUTO_UPDATE_FILE` lives
in INSTALL_DIR, which for anyone running TMT on TMT is this repository, so
every test that reads or writes the setting does it through `Setting`, which
redirects the module attribute at a temp directory and puts it back in a
`finally`. Nothing here sleeps, reaches a network, spawns a process, restarts
anything or reads a key.

`Workspace` comes from test_agent_workspace, which is where every other
end-to-end file in the suite takes it from. A second one would drift.
"""

import contextlib
import io
import os
import shutil
import tempfile
from pathlib import Path

import agent_config
import agent_menu
import agent_splash as S
import agent_ui
import agent_update as U
import TMT

from agent_live_renderer import LiveRegion
from test_agent_workspace import Workspace

INSTALL_DIR = Path(agent_config.__file__).resolve().parent


def visible(text):
    """The row a reader sees, with every escape sequence removed.

    Colour is never the message in TMT, so every assertion about what the
    menu SAYS is made through this. A test that read the painted string would
    pass on a frame whose only ON was a colour.
    """
    return agent_ui.strip_ansi(text)


# --- the harnesses ----------------------------------------------------------

class Setting:
    """The auto-update setting, redirected into a temp directory.

    `agent_config.AUTO_UPDATE_FILE` is a module attribute and the real one is
    `INSTALL_DIR / ".tmt_autoupdate"` -- the repository itself when TMT is run
    on TMT. A test that wrote there would silently change the developer's own
    setting and leave an untracked file in the working tree, and there is an
    existing test asserting that none of TMT's own state lands in a workspace.

    `AUTO_UPDATE` is restored as well as the path: `set_auto_update` and
    `refresh_auto_update` both assign to that global, and a leaked one would
    have a later test reading a value this file put there.
    """

    def __init__(self, contents=None):
        self.dir = Path(tempfile.mkdtemp(prefix="tmt_autoupdate_"))
        self.previous_file = agent_config.AUTO_UPDATE_FILE
        self.previous_value = agent_config.AUTO_UPDATE
        self.path = self.dir / ".tmt_autoupdate"
        agent_config.AUTO_UPDATE_FILE = self.path
        if contents is not None:
            self.write(contents)

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")
        return self.path

    def aim_at(self, path):
        """Point the setting somewhere it cannot be written or read."""
        agent_config.AUTO_UPDATE_FILE = Path(path)
        return agent_config.AUTO_UPDATE_FILE

    def close(self):
        agent_config.AUTO_UPDATE_FILE = self.previous_file
        agent_config.AUTO_UPDATE = self.previous_value
        shutil.rmtree(str(self.dir), ignore_errors=True)


class Keys:
    """A scripted key reader that cannot hang the suite.

    Running past the end of the script is a failure, not a wait: a screen that
    keeps asking for keys after Esc has not returned, and a sub-screen opened
    by mistake announces itself here rather than by blocking.
    """

    def __init__(self, *names):
        self.queue = list(names)
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if not self.queue:
            raise AssertionError(
                "the screen asked for key %d; the script provided %d"
                % (self.calls, self.calls - 1))
        return self.queue.pop(0)

    @property
    def remaining(self):
        return len(self.queue)


class Frames:
    """A LiveRegion-shaped sink that records frames instead of drawing them.

    Stronger than painting into a StringIO for the one question that matters
    about the toggle: every frame a screen paints is recorded, so "no
    sub-screen was opened" can be asserted on what was DRAWN rather than
    inferred from how many keys were eaten.
    """

    def __init__(self):
        self.frames = []

    def paint(self, lines):
        self.frames.append(list(lines))

    def clear(self):
        pass

    def show_cursor(self):
        pass

    def text(self):
        return "\n".join(visible(row) for frame in self.frames for row in frame)


class Updater:
    """A stand-in for `agent_update`: the real statuses, none of the git.

    It carries the real module's constants because `state_for_update` reads
    them off whichever module the result came FROM -- a stub with invented
    status strings would map every outcome to FAILED and the tests would all
    pass while asserting the wrong thing.
    """

    NAMES = ("CURRENT", "UPDATED", "AVAILABLE", "BLOCKED_DIRTY",
             "BLOCKED_DIVERGED", "NO_UPSTREAM", "NOT_A_REPO", "DISABLED",
             "ERROR")

    def __init__(self, status, headline="", detail=""):
        for name in self.NAMES:
            setattr(self, name, getattr(U, name))
        self.UpdateResult = U.UpdateResult
        self.result = U.UpdateResult(status, headline or status, detail)
        self.roots = []

    def check_and_update(self, root=None):
        self.roots.append(root)
        return self.result

    @property
    def calls(self):
        return len(self.roots)


class Refusing(Updater):
    """An updater that records the call and then explodes.

    For the paths that must never reach it. Recording first is the point: the
    assertion is `calls == 0`, which is airtight, and the exception is only
    there so a path that swallows the failure still fails something.
    """

    def __init__(self):
        Updater.__init__(self, U.CURRENT)

    def check_and_update(self, root=None):
        self.roots.append(root)
        raise AssertionError("the updater was called when it must not have been")


class FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class Git:
    """A TMTGit-shaped engine, so the restart guard can be driven with no repo.

    Only the calls `check_and_update` makes are answered, and every one is
    recorded, so a test can assert not just what came back but what was never
    run -- which for the loop guard is the whole assertion.
    """

    def __init__(self, behind=3, ahead=0, clean=True, upstream="origin/main"):
        self.root = Path(tempfile.gettempdir()) / "tmt-launch-fake"
        self.calls = []
        self.behind = behind
        self.ahead = ahead
        self.clean = clean
        self.upstream = upstream

    def subcommands(self):
        return [call[0] for call in self.calls]

    def _run(self, args, env=None, timeout=None, check=True):
        args = [str(argument) for argument in args]
        self.calls.append(args)
        if args[0] == "rev-parse":
            if "@{u}" in args:
                if not self.upstream:
                    return FakeResult(128, "", "fatal: no upstream")
                return FakeResult(0, self.upstream + "\n")
            return FakeResult(0, "abc1234\n")
        if args[0] == "rev-list":
            return FakeResult(0, "%d\t%d\n" % (self.ahead, self.behind))
        return FakeResult(0, "")

    def status(self):
        return {"branch": "main", "clean": self.clean, "staged": [],
                "unstaged": [] if self.clean else ["agent_ui.py"],
                "untracked": [], "root": str(self.root)}

    def remotes(self):
        return ["origin"]


def stage(updater, auto_update=None, root=None):
    """One run of the update stage with no terminal. Returns (state, restarts).

    `region=None` is what makes this instant: `_dwell` returns at once when
    there is nothing to paint, so no settled frame is held on screen. The
    key_reader is a tick that always finds nothing, which keeps `_run_check`
    out of its `time.sleep` fallback -- so this whole file runs without
    sleeping anywhere.
    """
    restarts = []
    state = S.SplashState()
    settled = S.run_update_stage(
        state, stream=io.StringIO(), region=None, clock=lambda: 0.0,
        key_reader=lambda: "", updater=updater, auto_update=auto_update,
        restart=lambda: restarts.append("restarted"), root=root)
    return settled, restarts


def launch(splash="exit", api_key=True, startup="exit"):
    """TMT.main with the launch sequence recorded. Returns (order, outcome).

    Every step of startup is replaced by a recorder so the ORDER can be read
    back, which is the only thing that proves the routing. The workspace is a
    real temp directory rather than a stub, because `resolve_workspace` runs
    for real ahead of everything here and an empty directory is the one shape
    it accepts without asking a question.
    """
    order = []
    box = Workspace()
    screen = io.StringIO()
    saved = (TMT.run_splash, TMT.ensure_api_key, TMT.run_startup,
             TMT.ensure_git_identity, agent_config.refresh_auto_update)
    previous_cwd = Path.cwd()
    try:
        os.chdir(str(box.path))

        def record(name, answer):
            def call(*args, **kwargs):
                order.append(name)
                return answer
            return call

        TMT.run_splash = record("splash", splash)
        TMT.ensure_api_key = record("api_key", api_key)
        TMT.run_startup = record("startup", startup)
        TMT.ensure_git_identity = record("git_identity", None)
        agent_config.refresh_auto_update = record("refresh_auto_update", True)
        with contextlib.redirect_stdout(screen):
            outcome = TMT.main([])
    finally:
        os.chdir(str(previous_cwd))
        (TMT.run_splash, TMT.ensure_api_key, TMT.run_startup,
         TMT.ensure_git_identity, agent_config.refresh_auto_update) = saved
        box.close()
    return order, outcome


# --- the setting, and whether it comes back ---------------------------------

def test_a_fresh_installation_has_auto_update_on():
    """The documented default, asserted from the state a fresh install is
    actually in: no file at all. A default that was only true of the constant
    and not of the read would be a feature nobody ever got."""
    box = Setting()
    try:
        assert not box.path.exists(), box.path
        assert agent_config.read_saved_auto_update() is True
        assert agent_config.DEFAULT_AUTO_UPDATE is True
    finally:
        box.close()


def test_turning_the_setting_off_survives_a_fresh_read_from_disk():
    """The whole point of storing it is the NEXT launch, so the assertion has
    to go through `read_saved_auto_update` rather than through the global the
    setter just assigned. A `set_auto_update` that only moved the variable
    would show OFF in the menu and be ON again tomorrow."""
    box = Setting()
    try:
        assert agent_config.set_auto_update(False) is False
        assert box.path.exists(), "nothing was written to disk"
        assert agent_config.read_saved_auto_update() is False
        # And again, from the file's own text, so it is not a cached answer.
        assert box.path.read_text(encoding="utf-8").strip() == "off"
    finally:
        box.close()


def test_turning_the_setting_on_survives_a_fresh_read_from_disk():
    """The same proof in the other direction. On is the default, so a broken
    writer would be invisible here unless the file is started at off first --
    which is why this test turns it off before it turns it on."""
    box = Setting("off\n")
    try:
        assert agent_config.read_saved_auto_update() is False
        assert agent_config.set_auto_update(True) is True
        assert agent_config.read_saved_auto_update() is True
        assert box.path.read_text(encoding="utf-8").strip() == "on"
    finally:
        box.close()


def test_toggling_off_then_on_then_off_ends_off():
    """A switch has to be a switch. Two writes in a row that both landed on
    the same value, or a writer that appended rather than replaced, would each
    pass a single-toggle test and fail this one."""
    box = Setting()
    try:
        for wanted in (False, True, False):
            agent_config.set_auto_update(wanted)
            assert agent_config.read_saved_auto_update() is wanted, wanted
        assert agent_config.read_saved_auto_update() is False
        assert box.path.read_text(encoding="utf-8").strip() == "off"
    finally:
        box.close()


def test_a_missing_file_reads_as_on_rather_than_as_a_refusal():
    """A file that was never written is a fresh installation, not somebody's
    decision. Defaulting to off there would silently disable the feature for
    every user who has not been into Settings."""
    box = Setting("off\n")
    try:
        assert agent_config.read_saved_auto_update() is False
        box.path.unlink()
        assert agent_config.read_saved_auto_update() is True
    finally:
        box.close()


def test_a_file_full_of_nonsense_reads_as_on_and_does_not_raise():
    """An unrecognised value is a typo in a file somebody edited by hand, and
    a typo is not evidence that the user wanted the feature off. It is also
    read at startup, so raising here would make a stray character in a config
    file the thing that stops TMT launching."""
    box = Setting()
    try:
        for nonsense in ("banana", "", "   ", "\n", "onoff", "0.5", "ON OFF",
                         "off, please", "\x00", "off" * 400):
            box.write(nonsense)
            assert agent_config.read_saved_auto_update() is True, repr(nonsense)
    finally:
        box.close()


def test_a_file_that_cannot_be_read_at_all_reads_as_on_and_does_not_raise():
    """Startup must not be stoppable by a bad config file. A directory where a
    file should be and a path whose parent does not exist are the two shapes
    an unreadable setting actually takes, and both are OSError -- which is
    what the reader guards against and the only thing it may guard against."""
    box = Setting()
    try:
        box.aim_at(box.dir)                       # a directory, not a file
        assert agent_config.read_saved_auto_update() is True
        box.aim_at(box.dir / "no" / "such" / "place")
        assert agent_config.read_saved_auto_update() is True
    finally:
        box.close()


def test_every_accepted_spelling_of_off_is_understood():
    """The file is meant to be editable by hand, so it is read forgivingly.
    Spelled out here rather than looped over the module's own tuple, because a
    test that reads the tuple it is checking passes whatever the tuple says --
    including after somebody quietly deletes an entry from it."""
    box = Setting()
    try:
        for word in ("off", "0", "false", "no", "disabled"):
            box.write(word + "\n")
            assert agent_config.read_saved_auto_update() is False, word
        assert set(agent_config._AUTO_UPDATE_OFF) == {
            "off", "0", "false", "no", "disabled"}
    finally:
        box.close()


def test_every_accepted_spelling_of_on_is_understood():
    """The same list for on. It matters less than the off list -- on is the
    default, so an unrecognised on-word still reads as on -- which is exactly
    why it needs its own test: a broken on-word is invisible from the outside
    and would only show up the day the default changed."""
    box = Setting()
    try:
        for word in ("on", "1", "true", "yes", "enabled"):
            box.write("off\n")
            assert agent_config.read_saved_auto_update() is False
            box.write(word + "\n")
            assert agent_config.read_saved_auto_update() is True, word
        assert set(agent_config._AUTO_UPDATE_ON) == {
            "on", "1", "true", "yes", "enabled"}
    finally:
        box.close()


def test_the_value_is_read_case_and_whitespace_insensitively():
    """A file edited in an editor arrives with a trailing newline, and one
    edited by a person arrives however they typed it. `OFF\\n`, ` off ` and
    `Off` are all the same answer, and a reader that only matched the exact
    lowercase word would treat every one of them as nonsense and turn the
    setting back on."""
    box = Setting()
    try:
        for text in ("OFF", "Off", " off ", "off\n", "\toff\r\n", "  OfF  \n"):
            box.write(text)
            assert agent_config.read_saved_auto_update() is False, repr(text)
        for text in ("ON", " on\n", "\tTrue  ", "YES\r\n"):
            box.write(text)
            assert agent_config.read_saved_auto_update() is True, repr(text)
    finally:
        box.close()


def test_refresh_auto_update_moves_the_module_level_value_and_returns_it():
    """`refresh_effort` exists because a setting that is written and never
    re-read lasts one session and quietly reverts. This is the same wire for
    the same reason, and it has to do both halves: assign the global that the
    rest of the process reads, and hand the value back to its caller."""
    box = Setting("off\n")
    try:
        agent_config.AUTO_UPDATE = True
        assert agent_config.refresh_auto_update() is False
        assert agent_config.AUTO_UPDATE is False
        box.write("on\n")
        assert agent_config.refresh_auto_update() is True
        assert agent_config.AUTO_UPDATE is True
    finally:
        box.close()


def test_set_auto_update_makes_the_new_value_live_in_this_process_too():
    """Writing the file is half the job. The menu toggles it mid-session, and
    anything already holding `agent_config.AUTO_UPDATE` would carry the old
    value until the next launch if the setter did not assign it as well."""
    box = Setting()
    try:
        agent_config.set_auto_update(False)
        assert agent_config.AUTO_UPDATE is False
        agent_config.set_auto_update(True)
        assert agent_config.AUTO_UPDATE is True
    finally:
        box.close()


def test_a_write_that_cannot_be_made_is_raised_rather_than_lost():
    """The one path through this setting that does NOT default quietly, and
    deliberately: every read defaults because a launch must not be stoppable,
    but a toggle the user just pressed that silently did not persist would
    show ON in the menu and be OFF on the next launch. The menu catches it;
    the setter must not swallow it."""
    box = Setting()
    try:
        box.aim_at(box.dir)                       # a directory cannot be written
        raised = None
        try:
            agent_config.set_auto_update(False)
        except OSError as error:
            raised = error
        assert raised is not None, "a failed write reported success"
    finally:
        box.close()


def test_the_setting_belongs_to_the_installation_and_never_to_the_workspace():
    """The rule the model file and the effort level already follow. TMT is the
    same agent in every directory, so a setting that followed the workspace
    would be a different answer per project -- and would drop a file into the
    user's repository, which an existing test in the suite forbids outright."""
    box = Workspace(git=True)
    try:
        box.use()
        path = Path(agent_config.AUTO_UPDATE_FILE).resolve()
        assert path.parent == INSTALL_DIR, path
        assert path != box.path
        assert box.path not in path.parents, path
        assert path.name.startswith("."), path.name
    finally:
        box.close()


# --- the Settings menu ------------------------------------------------------

def test_settings_offers_the_toggle_and_back_is_still_last():
    """The way in. A setting with no row is a setting nobody can change, and
    `Back` staying last is what keeps the existing navigation -- and the three
    tests elsewhere that enumerate this list -- meaning what they meant."""
    ids = [item[0] for item in agent_menu.SETTINGS_ITEMS]
    labels = {item[0]: item[1] for item in agent_menu.SETTINGS_ITEMS}
    assert "autoupdate" in ids, ids
    assert labels["autoupdate"] == "Auto Update on Launch", labels["autoupdate"]
    assert ids[-1] == "back", ids
    assert ids.index("autoupdate") < ids.index("back"), ids
    # Every entry carries a description, and this one's says what it does not
    # control -- the splash is drawn whatever the setting says.
    detail = dict((item[0], item[2]) for item in agent_menu.SETTINGS_ITEMS)
    assert detail["autoupdate"].strip(), detail["autoupdate"]


def test_the_toggle_reads_on_and_off_as_words_not_as_colour():
    """Colour is never the message. With the escapes stripped the row still
    has to say which way the switch is set, or a terminal with no colour --
    and every assertion in this suite -- would be reading a blank."""
    box = Setting()
    try:
        assert agent_menu.AUTO_UPDATE_LABELS == ("OFF", "ON")
        agent_config.set_auto_update(True)
        assert agent_menu.auto_update_text() == "ON"
        agent_config.set_auto_update(False)
        assert agent_menu.auto_update_text() == "OFF"
    finally:
        box.close()


def test_the_frame_says_what_is_on_disk_and_changes_when_that_changes():
    """The row is the only place the value is shown, so it has to be read
    rather than cached: a frame built from a value captured at import would be
    right until the first toggle and wrong forever afterwards."""
    box = Setting()
    try:
        agent_config.set_auto_update(True)
        on = [visible(row) for row in agent_menu.render_settings_menu_frame(
            3, io.StringIO(), size=(90, 30))]
        agent_config.set_auto_update(False)
        off = [visible(row) for row in agent_menu.render_settings_menu_frame(
            3, io.StringIO(), size=(90, 30))]

        assert on != off, "the frame did not notice the setting change"
        differing = [pair for pair in zip(on, off) if pair[0] != pair[1]]
        assert len(differing) == 1, differing
        shown_on, shown_off = differing[0]
        assert "Auto Update on Launch" in shown_on, shown_on
        assert shown_on.rstrip().endswith("ON"), shown_on
        assert shown_off.rstrip().endswith("OFF"), shown_off
    finally:
        box.close()


def test_the_frame_draws_the_state_of_every_other_row_unchanged():
    """The toggle carries its value on its own row and nowhere else. A second
    copy in the field block above would make a reader look in two places to
    find out what Enter does, and would be a second thing to keep in step."""
    box = Setting()
    try:
        agent_config.set_auto_update(False)
        rows = [visible(row) for row in agent_menu.render_settings_menu_frame(
            3, io.StringIO(), size=(90, 30))]
        carrying = [row for row in rows if "OFF" in row]
        assert len(carrying) == 1, carrying
        assert "Auto Update on Launch" in carrying[0], carrying[0]
    finally:
        box.close()


def test_the_row_survives_a_settings_file_that_cannot_be_read():
    """A menu that could not be drawn because of an unreadable settings file
    would be the config file stopping TMT, one screen later than the reader
    already refuses to let it. The row falls back to the documented default
    rather than to nothing."""
    box = Setting()
    try:
        box.aim_at(box.dir / "no" / "such" / "place")
        assert agent_menu.auto_update_text() == "ON"
        rows = [visible(row) for row in agent_menu.render_settings_menu_frame(
            3, io.StringIO(), size=(90, 30))]
        assert any("Auto Update on Launch" in row and row.rstrip().endswith("ON")
                   for row in rows), rows
    finally:
        box.close()


def test_toggle_auto_update_never_raises_and_reports_what_is_in_force():
    """A failed write in Settings is reported by the row not changing, which
    is the honest signal that nothing happened -- and the alternative, a
    traceback out of a menu, would leave the terminal in raw mode. What comes
    back has to be what is actually in force, not what was asked for."""
    box = Setting()
    try:
        box.aim_at(box.dir)                       # a directory: unwritable
        assert agent_menu.toggle_auto_update() == "ON"
        assert agent_menu.toggle_auto_update() == "ON", "it claimed a write it could not make"
    finally:
        box.close()


def test_toggle_auto_update_flips_the_stored_value_when_it_can():
    """The other half of the same function. A `toggle` that never raised
    because it never wrote would pass the test above and do nothing at all."""
    box = Setting()
    try:
        agent_config.set_auto_update(True)
        assert agent_menu.toggle_auto_update() == "OFF"
        assert agent_config.read_saved_auto_update() is False
        assert agent_menu.toggle_auto_update() == "ON"
        assert agent_config.read_saved_auto_update() is True
    finally:
        box.close()


def test_the_toggle_keeps_its_state_on_a_terminal_too_narrow_for_its_words():
    """The state is protected from trimming and the description is not, which
    is the right way round: a row that gave up its ON/OFF to keep the sentence
    explaining what it does would hide the one thing the user came to read."""
    box = Setting()
    try:
        for value, word in ((True, "ON"), (False, "OFF")):
            agent_config.set_auto_update(value)
            for columns in (30, 40, 50, 60, 80, 120):
                rows = [visible(row) for row in
                        agent_menu.render_settings_menu_frame(
                            3, io.StringIO(), size=(columns, 30))]
                row = [text for text in rows if "Auto Update" in text]
                assert len(row) == 1, (columns, rows)
                assert row[0].rstrip().endswith(word), (columns, row[0])
    finally:
        box.close()


def test_every_settings_row_fits_the_terminal_it_was_drawn_for():
    """Measured, never counted -- a row filled past the last column wraps and
    costs a screen line the repaint arithmetic does not know about.

    The footer is excluded and that is not the toggle's doing: the hint row is
    a fixed string of three hints and overflows a terminal under about 42
    columns on every settings frame TMT has ever drawn, before and after this
    setting existed. Asserting it here would be asserting a pre-existing bug
    is correct; asserting the rest is what this feature can actually break.
    """
    box = Setting()
    try:
        for value in (True, False):
            agent_config.set_auto_update(value)
            for columns in (30, 40, 50, 60, 80, 120, 200):
                frame = agent_menu.render_settings_menu_frame(
                    3, io.StringIO(), size=(columns, 30))
                for row in frame[:-1]:
                    width = agent_ui.display_width(visible(row))
                    assert width <= columns - 1, (columns, width, visible(row))
    finally:
        box.close()


def test_driving_the_real_settings_screen_toggles_the_setting_on_disk():
    """The wire between a keystroke and the file. Everything above is a
    function called directly; this is the only test that proves Enter on that
    row reaches `set_auto_update` at all, through the real `_drive` loop and
    the real key normalisation."""
    box = Setting()
    try:
        agent_config.set_auto_update(True)
        position = [item[0] for item in agent_menu.SETTINGS_ITEMS].index("autoupdate")
        keys = Keys(*(["down"] * position + ["enter", "esc"]))
        region = LiveRegion(io.StringIO(), ansi=False)
        stream = io.StringIO()
        assert agent_menu.settings_screen(
            stream=stream, key_reader=keys, region=region) is None
        assert agent_config.read_saved_auto_update() is False
        assert keys.remaining == 0, keys.remaining
        assert stream.getvalue() == "", "the screen wrote past its own region"
    finally:
        box.close()


def test_driving_it_again_toggles_the_setting_back():
    """A switch, not a one-way door. A screen that wrote a literal `off`
    rather than the opposite of what it read would pass the test above."""
    box = Setting()
    try:
        agent_config.set_auto_update(False)
        position = [item[0] for item in agent_menu.SETTINGS_ITEMS].index("autoupdate")
        for expected in (True, False):
            keys = Keys(*(["down"] * position + ["enter", "esc"]))
            agent_menu.settings_screen(stream=io.StringIO(), key_reader=keys,
                                       region=LiveRegion(io.StringIO(), ansi=False))
            assert agent_config.read_saved_auto_update() is expected, expected
    finally:
        box.close()


def test_selecting_the_toggle_does_not_open_a_screen():
    """It is the one entry in Settings that is a switch rather than a door.
    Every frame painted is asserted to still be the settings frame, so an
    Enter that opened the model picker -- or anything else -- is caught by
    what was drawn rather than by the script running out of keys."""
    box = Setting()
    try:
        agent_config.set_auto_update(True)
        position = [item[0] for item in agent_menu.SETTINGS_ITEMS].index("autoupdate")
        keys = Keys(*(["down"] * position + ["enter", "esc"]))
        region = Frames()
        agent_menu.settings_screen(stream=io.StringIO(), key_reader=keys,
                                   region=region)
        assert region.frames, "nothing was painted at all"
        for frame in region.frames:
            rendered = "\n".join(visible(row) for row in frame)
            assert "Auto Update on Launch" in rendered, rendered
            assert "Settings" in rendered, rendered
        # And the Enter really did something, so this is not passing because
        # the row was never selected.
        assert agent_config.read_saved_auto_update() is False
        # The frame after Enter shows the new value: the row is re-read on the
        # next pass of `_drive` rather than needing anything invalidated.
        assert any(row.rstrip().endswith("OFF")
                   for row in (visible(text) for text in region.frames[-1])), \
            region.frames[-1]
    finally:
        box.close()


def test_leaving_settings_by_any_other_row_changes_nothing():
    """The toggle is reached by Enter on its own row and by nothing else. A
    handler keyed on the selection index rather than on the entry id would
    flip the setting from whichever row happened to share a number."""
    box = Setting()
    try:
        agent_config.set_auto_update(True)
        ids = [item[0] for item in agent_menu.SETTINGS_ITEMS]
        keys = Keys(*(["down"] * ids.index("back") + ["enter"]))
        agent_menu.settings_screen(stream=io.StringIO(), key_reader=keys,
                                   region=Frames())
        assert agent_config.read_saved_auto_update() is True
        assert keys.remaining == 0, keys.remaining
    finally:
        box.close()


# --- the launch flow, without a terminal ------------------------------------

def test_run_splash_returns_start_and_draws_nothing_without_a_terminal():
    """The single line that keeps every piped run, every script and this whole
    suite working exactly as they did before the launch screen existed. It is
    checked first and unconditionally, so nothing else on that path -- not the
    reader, not the region, not the updater -- is ever reached."""
    stream = io.StringIO()
    assert S.run_splash(stream=stream) == "start"
    assert stream.getvalue() == "", repr(stream.getvalue())


def test_a_launch_with_no_terminal_never_reaches_the_updater():
    """The same rule stated as the thing that would actually hurt: a scripted
    run that silently fetched, or fast-forwarded, the checkout it was being
    run from. The refusal has to come before the update stage, not inside it."""
    updater = Refusing()
    assert S.run_splash(stream=io.StringIO(), updater=updater) == "start"
    assert updater.calls == 0, updater.roots


def test_the_setting_off_skips_the_check_and_never_restarts():
    """What the setting actually buys. Off means nothing is fetched at all --
    not a check whose result is discarded -- and the screen says "Starting"
    rather than claiming to have searched for something it never looked for."""
    updater = Refusing()
    settled, restarts = stage(updater, auto_update=False)
    assert settled.outcome == S.SKIPPED, settled.outcome
    assert settled.finished
    assert updater.calls == 0, updater.roots
    assert restarts == [], restarts


def test_a_checkout_that_is_already_current_is_reported_and_not_restarted():
    """The commonest launch there is, and the one place a restart would be a
    loop: a process that handed over every time it found itself up to date
    would never reach TMT at all."""
    updater = Updater(U.CURRENT)
    settled, restarts = stage(updater, auto_update=True)
    assert settled.outcome == S.CURRENT, settled.outcome
    assert updater.calls == 1, updater.roots
    assert restarts == [], restarts


def test_an_applied_update_restarts_exactly_once():
    """The code loaded into this process is the code that was on disk BEFORE
    the fast-forward, so carrying on here would run the old TMT while
    reporting the new one. Once, and not twice: the handover is the one thing
    on this screen that does not come back."""
    updater = Updater(U.UPDATED, "TMT updated to the latest version.")
    settled, restarts = stage(updater, auto_update=True)
    assert settled.outcome == S.UPDATED, settled.outcome
    assert updater.calls == 1, updater.roots
    assert restarts == ["restarted"], restarts


def test_every_refusal_continues_into_tmt_without_restarting():
    """Four different reasons an update may not be taken, and all four have to
    end the same way: the work untouched, the launch continuing, no handover.
    A refusal is an answer, and none of them is a reason to stop TMT starting."""
    for status in (U.BLOCKED_DIRTY, U.BLOCKED_DIVERGED, U.NO_UPSTREAM,
                   U.NOT_A_REPO):
        updater = Updater(status, "Update skipped.")
        settled, restarts = stage(updater, auto_update=True)
        assert settled.outcome == S.BLOCKED, (status, settled.outcome)
        assert settled.finished, status
        assert restarts == [], (status, restarts)


def test_a_check_that_failed_continues_into_tmt_without_restarting():
    """No network, no git, a deleted remote. TMT could not find out, which is
    the absence of an answer rather than a refusal -- and the user still wants
    their agent. It must not be mistaken for a reason to hand over."""
    updater = Updater(U.ERROR, "TMT could not reach the update server.")
    settled, restarts = stage(updater, auto_update=True)
    assert settled.outcome == S.FAILED, settled.outcome
    assert restarts == [], restarts


def test_an_update_that_may_not_be_applied_is_blocked_and_not_skipped():
    """The distinction between "nothing was checked" and "something was found
    and not taken", which is the whole difference between the two quiet
    outcomes.

    DISABLED is the loop guard's own answer: this launch has already restarted
    once, so it looked, found an update and was not allowed to apply it. That
    is BLOCKED. SKIPPED is reserved for the setting being off, which is
    answered before the updater is called at all -- and reporting a DISABLED
    as SKIPPED would throw away a finding the user could act on. Both are
    driven here, in one test, because either mapped alone reads as correct.
    """
    disabled = Updater(U.DISABLED, "An update is waiting.")
    settled, restarts = stage(disabled, auto_update=True)
    assert settled.outcome == S.BLOCKED, settled.outcome
    assert disabled.calls == 1, disabled.roots
    assert restarts == [], restarts

    off = Refusing()
    settled, restarts = stage(off, auto_update=False)
    assert settled.outcome == S.SKIPPED, settled.outcome
    assert off.calls == 0, off.roots


def test_a_status_the_screen_has_never_heard_of_is_a_failure_not_a_guess():
    """The safe direction. A status this screen cannot describe is not one it
    may invent a sentence for, and FAILED is the state that continues into
    TMT -- so an updater that grew a new status cannot silently produce a
    launch that claims something untrue or one that never finishes."""
    updater = Updater("something-nobody-wrote-a-handler-for")
    settled, restarts = stage(updater, auto_update=True)
    assert settled.outcome == S.FAILED, settled.outcome
    assert restarts == [], restarts


def test_the_stage_reads_the_real_setting_when_it_is_not_told_one():
    """The wire between the menu and the launch, and the only test that
    crosses it. `auto_update=None` is what the application actually passes, so
    a stage that defaulted to True there would run the check for a user who
    had turned it off in Settings and never notice."""
    box = Setting()
    try:
        agent_config.set_auto_update(False)
        refused = Refusing()
        settled, restarts = stage(refused)
        assert settled.outcome == S.SKIPPED, settled.outcome
        assert refused.calls == 0, refused.roots

        agent_config.set_auto_update(True)
        updater = Updater(U.CURRENT)
        settled, restarts = stage(updater)
        assert settled.outcome == S.CURRENT, settled.outcome
        assert updater.calls == 1, updater.roots
        assert restarts == [], restarts
    finally:
        box.close()


def test_an_unreadable_setting_leaves_the_launch_checking_rather_than_stuck():
    """The default arriving at the place it matters. A configuration file
    nobody can read must not be able to silently disable the update -- and,
    more importantly, must not raise on the one path that runs before the user
    has typed anything."""
    box = Setting()
    try:
        box.aim_at(box.dir / "no" / "such" / "place")
        assert S.auto_update_enabled() is True
        updater = Updater(U.CURRENT)
        settled, restarts = stage(updater)
        assert settled.outcome == S.CURRENT, settled.outcome
        assert restarts == [], restarts
    finally:
        box.close()


def test_the_update_stage_is_handed_the_root_it_was_given():
    """The update source is TMT's own checkout and never the workspace. The
    stage does not choose it -- it passes what it was given straight through
    -- and a stage that dropped the argument would have `check_and_update`
    fall back to its own default, which is right today and would silently
    ignore a caller that meant otherwise."""
    updater = Updater(U.CURRENT)
    settled, restarts = stage(updater, auto_update=True, root="C:/somewhere/else")
    assert updater.roots == ["C:/somewhere/else"], updater.roots
    assert settled.outcome == S.CURRENT, settled.outcome


# --- restart safety and the loop guard --------------------------------------

def test_a_restart_runs_tmt_again_exactly_as_it_was_run_this_time():
    """Both launch shapes, and every argument preserved. `tmtcode --dir X` has
    to come back as `tmtcode --dir X` or an automatic update would quietly
    move the user out of the workspace they chose -- which is the kind of
    surprise that makes an update feel like something done TO somebody."""
    script = U.restart_command(["TMT.py", "--dir", "C:/work"], executable="/py")
    assert script == ["/py", "TMT.py", "--dir", "C:/work"], script
    console = U.restart_command(["tmtcode", "--dir", "C:/work"], executable="/py")
    assert console == ["tmtcode", "--dir", "C:/work"], console
    # No argv at all still produces something runnable rather than an empty list.
    assert U.restart_command([], executable="/py") == ["/py"]


def test_the_restart_counter_travels_in_the_environment_and_reads_back():
    """The environment is the only channel a fresh interpreter is guaranteed
    to read, and the copy matters: the process about to be replaced may still
    have to report a failure with its own environment intact."""
    original = {U.RESTART_ENV_VAR: "0", "KEEP": "me"}
    raised = U.restart_env(original)
    assert raised[U.RESTART_ENV_VAR] == "1", raised[U.RESTART_ENV_VAR]
    assert raised["KEEP"] == "me"
    assert original[U.RESTART_ENV_VAR] == "0", "the caller's environment was mutated"
    assert U.restarts_used(raised) == 1
    assert U.restarts_used({}) == 0


def test_a_garbage_counter_reads_as_already_restarted_rather_than_crashing():
    """The value arrives from the environment, so a user can set it to
    anything -- and no value they can set may either crash the launch or buy
    an extra restart. Every one of these fails towards the ceiling, which
    means "no more handovers"."""
    for hostile in ("banana", "-1", "1e9", "9999999999999999999999x",
                    "\x00", "1.5", "0x1", "+", "1 2"):
        used = U.restarts_used({U.RESTART_ENV_VAR: hostile})
        assert used >= U.MAX_UPDATE_RESTARTS, (hostile, used)
    # A mapping that cannot even be asked answers the same way.
    assert U.restarts_used(object()) >= U.MAX_UPDATE_RESTARTS
    # Empty and whitespace-only are the ABSENT case, not the hostile one: a
    # variable that is set to nothing is a launch that has not restarted, and
    # reading it as the ceiling would stop the first update on any machine
    # whose shell exports empty variables.
    for absent in ("", "  ", "\t\n"):
        assert U.restarts_used({U.RESTART_ENV_VAR: absent}) == 0, repr(absent)


def test_a_launch_that_has_already_restarted_looks_but_does_not_move():
    """The whole loop guard in one assertion, driven with no process spawned
    and no real repository. The replacement process is EXPECTED to check again
    -- that is the screen the user sees -- so the guard sits on applying an
    update, not on looking for one. It still fetches; it never merges."""
    engine = Git(behind=4)
    spent = {U.RESTART_ENV_VAR: str(U.MAX_UPDATE_RESTARTS)}
    result = U.check_and_update(git=engine, env=spent)
    assert result.status == U.DISABLED, result.as_dict()
    assert "fetch" in engine.subcommands(), engine.calls
    assert "merge" not in engine.subcommands(), engine.calls
    assert result.behind == 4, result.as_dict()


def test_a_first_launch_is_allowed_to_apply_the_update_it_finds():
    """The other side of the guard, so the test above is not passing because
    nothing is ever applied. With the counter unset the same engine and the
    same commits produce a fast-forward."""
    engine = Git(behind=4)
    result = U.check_and_update(git=engine, env={})
    assert result.status == U.UPDATED, result.as_dict()
    assert result.should_restart is True
    assert "merge" in engine.subcommands(), engine.calls


# --- the routing through main ----------------------------------------------

def test_main_shows_the_launch_screen_and_the_menu_before_it_asks_anything():
    """The routing the launch sequence is for, and it changed on 2026-09-02:
    a first-time user meets the wordmark, and then the MENU, before they meet
    a form. It used to be wordmark then "paste your API key", which is a
    question in front of a program the user has not been shown yet.

    Exit from the menu therefore asks for nothing at all -- somebody who
    launched TMT to look at it can leave without being asked for a
    credential. The order is asserted rather than the existence of the calls,
    because the same calls in the wrong order is the feature inverted.
    """
    order, outcome = launch(splash="start", api_key=True, startup="exit")
    assert order[0] == "splash", order
    assert "startup" in order, order
    assert "api_key" not in order, order
    assert outcome == 0, outcome


def test_a_launch_screen_that_says_exit_stops_before_anything_else_runs():
    """Ctrl-C on the splash closes TMT rather than dropping into it. Nothing
    after it may run -- not the credential prompt, not the startup menu, not a
    session -- or the one gesture for backing out of a launch would start one."""
    order, outcome = launch(splash="exit")
    assert order == ["splash"], order
    assert outcome == 0, outcome


def test_startup_re_reads_the_auto_update_setting():
    """`refresh_effort` exists because a setting written by the menu and never
    re-read lasts one session and quietly reverts. This one is stored the same
    way and would have the same bug, so the launch has to read it -- and it
    has to read it AFTER the splash, since that is the run it applies to
    next."""
    order, outcome = launch(splash="start", api_key=True, startup="exit")
    assert "refresh_auto_update" in order, order
    assert order.index("splash") < order.index("refresh_auto_update"), order
    assert order.index("refresh_auto_update") < order.index("startup"), order


def test_a_launch_with_no_api_key_still_reaches_the_menu_first():
    """The other end of the same change, and the inverse of what this test
    asserted until 2026-09-02.

    The menu comes first whatever the credential situation is. Only once
    Start has been chosen does the question arise, and a refusal there ends
    the launch rather than starting a session that cannot reach a model.
    """
    order, outcome = launch(splash="start", api_key=False, startup="start")
    assert order[0] == "splash", order
    assert order.index("startup") < order.index("api_key"), order
    assert outcome is None, outcome

    # And choosing Exit never asks at all.
    order, outcome = launch(splash="start", api_key=False, startup="exit")
    assert "api_key" not in order, order
    assert outcome == 0, outcome
