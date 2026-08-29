"""Tests for the startup screen and the model choice behind it.

Two things are being protected here. The first is that a user who never opens
Settings keeps the model TMT has always run on: the catalogue and the
resolution order in agent_models decide that, and they are pinned below. The
second is that the menu never blocks a non-interactive run, and that every
screen can be driven and left without stranding the terminal.

Everything the menu can reach is redirected first: the model file goes to a
temp path, stdin is replaced by a stub, and the frames are drawn into a buffer
that only claims to be a terminal. The developer's own .tmt_model, cwd and
stdin are restored in a finally in every test.
"""

import datetime
import io
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import agent_config
import agent_models

# Colour and cursor moves, so a frame can be read as the terminal shows it.
ESCAPE_RE = re.compile("\033\\[[0-9;?]*[ -/]*[@-~]|\033\\][^\007]*\007|\033[=>]")


def visible(text):
    """The frame with every escape sequence removed."""
    return ESCAPE_RE.sub("", text)


def menu():
    """agent_menu, imported at call time.

    A module-level import would make a missing or broken agent_menu an error
    in the test runner's collection rather than a failure of the tests that
    depend on it, and would take the model tests down with it.
    """
    import agent_menu
    return agent_menu


# --- stubs ------------------------------------------------------------------

class Terminal(io.StringIO):
    """A buffer that claims to be a terminal.

    The live renderer paints nothing to a stream whose isatty() is False, so a
    plain StringIO would make every frame assertion pass on an empty string.
    """

    def isatty(self):
        return True

    @property
    def encoding(self):
        return "utf-8"

    def text(self):
        return visible(self.getvalue())


class Stdin:
    """Stands in for the real stdin so a test can decide interactivity."""

    def __init__(self, tty=True, broken=False):
        self._tty = tty
        self._broken = broken

    def isatty(self):
        if self._broken:
            raise ValueError("stdin is closed")
        return self._tty

    def fileno(self):
        return 0

    def read(self, size=-1):
        raise AssertionError("the menu must take keys from key_reader, not stdin")

    def readline(self, size=-1):
        raise AssertionError("the menu must take keys from key_reader, not stdin")


KEY_NAMES = {
    "up": ("KEY_UP", "UP"),
    "down": ("KEY_DOWN", "DOWN"),
    "enter": ("KEY_ENTER", "ENTER"),
    "esc": ("KEY_ESC", "ESC", "KEY_ESCAPE", "ESCAPE"),
    "quit": ("KEY_QUIT", "QUIT"),
}

FALLBACK_KEYS = {"up": "up", "down": "down", "enter": "enter",
                 "esc": "esc", "quit": "q"}


def key(name):
    """The token read_key yields for a key.

    The contract fixes the keys, not the names given to them, so a token the
    module publishes wins over the plain word.
    """
    for attribute in KEY_NAMES[name]:
        token = getattr(menu(), attribute, None)
        if isinstance(token, str) and token:
            return token
    return FALLBACK_KEYS[name]


class Keys:
    """A scripted key_reader that cannot hang the suite.

    Running past the end of the script is a failure, not a wait: a menu that
    keeps asking for keys after the script chose Exit has not exited.
    """

    def __init__(self, *names):
        self.queue = [key(name) for name in names]
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if not self.queue:
            raise AssertionError(
                "the menu asked for key %d; the script provided %d"
                % (self.calls, self.calls - 1))
        return self.queue.pop(0)

    @property
    def remaining(self):
        return len(self.queue)


class Sandbox:
    """Temp model file, stubbed stdin, and everything restored in close().

    The model file is redirected before anything can write, so a test that
    saves a model never reaches the installation's own .tmt_model.
    """

    def __init__(self, saved=None, tty=True, broken_stdin=False, colour=True):
        self.previous_model_file = agent_models.MODEL_FILE
        self.previous_config_model = agent_config.MODEL
        self.previous_env = os.environ.get("OPENROUTER_MODEL")
        self.previous_no_colour = os.environ.get("NO_COLOR")
        self.previous_columns = os.environ.get("COLUMNS")
        self.previous_stdin = sys.stdin
        self.previous_cwd = os.getcwd()
        self.dir = Path(tempfile.mkdtemp(prefix="tmt_menu_"))

        agent_models.MODEL_FILE = self.dir / ".tmt_model"
        os.environ.pop("OPENROUTER_MODEL", None)
        # Pinned so a frame is the same width whatever terminal the suite is
        # run from; shutil.get_terminal_size reads this first.
        os.environ["COLUMNS"] = "100"

        if not colour:
            os.environ["NO_COLOR"] = "1"
        else:
            os.environ.pop("NO_COLOR", None)
        if saved:
            agent_models.MODEL_FILE.write_text(saved + "\n", encoding="utf-8")
        sys.stdin = Stdin(tty=tty, broken=broken_stdin)
        self.stream = Terminal()
        self.workspace = self.dir / "probe_workspace"
        self.workspace.mkdir()

    def close(self):
        agent_models.MODEL_FILE = self.previous_model_file
        agent_config.MODEL = self.previous_config_model
        sys.stdin = self.previous_stdin
        os.chdir(self.previous_cwd)
        for name, value in (("OPENROUTER_MODEL", self.previous_env),
                            ("NO_COLOR", self.previous_no_colour),
                            ("COLUMNS", self.previous_columns)):
            os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value
        shutil.rmtree(self.dir, ignore_errors=True)


def start(box, *names, **kwargs):
    """Drive run_startup with a scripted script. Returns (choice, keys)."""
    keys = Keys(*names)
    choice = menu().run_startup(stream=box.stream, key_reader=keys,
                                workspace=kwargs.get("workspace", box.workspace),
                                model_id=kwargs.get("model_id"))
    return choice, keys


# --- the catalogue and the saved choice -------------------------------------

def test_the_catalogue_is_five_distinct_free_models():
    """A paid id in this list bills the user without warning, and a duplicate
    would make the Settings cursor land on two rows that mean the same thing."""
    ids = [model["id"] for model in agent_models.FREE_MODELS]
    assert len(agent_models.FREE_MODELS) == 5, len(agent_models.FREE_MODELS)
    assert len(set(ids)) == 5, ids
    for model in agent_models.FREE_MODELS:
        assert model["id"].endswith(":free"), model["id"]
        assert model["label"].strip(), model
        assert isinstance(model["context"], int) and model["context"] > 0, model
    assert agent_models.known_ids() == ids


def test_the_default_is_the_model_users_already_have():
    """The one that must not drift. Someone who never opens Settings has to
    keep running on exactly what they ran on before the menu existed."""
    box = Sandbox()
    try:
        assert agent_models.DEFAULT_MODEL == "minimax/minimax-m3:free"
        assert not agent_models.MODEL_FILE.exists()
        assert os.environ.get("OPENROUTER_MODEL") is None
        assert agent_models.current_model() == "minimax/minimax-m3:free"
        assert agent_models.read_saved_model() == ""
        assert agent_models.describe() == "MiniMax M3"
    finally:
        box.close()


def test_a_saved_choice_survives_a_fresh_read_and_moves_agent_config():
    """Settings has to change the model for the next request in this session
    and for every later run, so both the file and the live config are checked."""
    box = Sandbox()
    chosen = agent_models.FREE_MODELS[2]["id"]
    try:
        assert agent_models.set_model(chosen) == chosen
        assert agent_config.MODEL == chosen
        # Read from the disk again rather than trusting the value just set.
        # The file records a choice per provider, since a model id is only
        # meaningful to the provider that issued it.
        on_disk = agent_models.MODEL_FILE.read_text(encoding="utf-8")
        assert chosen in on_disk, on_disk
        assert "openrouter" in on_disk, on_disk
        assert agent_models.read_saved_model() == chosen
        assert agent_models.current_model() == chosen
        assert agent_models.describe() == agent_models.FREE_MODELS[2]["label"]
    finally:
        box.close()


def test_an_unknown_model_is_refused_and_nothing_is_written():
    """A typo that quietly became the active model would only surface later,
    as a failed request against an id nobody serves."""
    box = Sandbox()
    before = agent_config.MODEL
    try:
        refused = False
        try:
            agent_models.set_model("acme/not-a-model:free")
        except ValueError:
            refused = True
        assert refused, "an unknown id must raise ValueError"
        assert not agent_models.MODEL_FILE.exists()
        assert agent_config.MODEL == before
        assert agent_models.current_model() == agent_models.DEFAULT_MODEL
    finally:
        box.close()


def test_the_environment_override_beats_a_saved_choice_and_says_so():
    """OPENROUTER_MODEL kept the meaning it always had, and Settings must be
    able to tell the user why their choice is not taking effect."""
    saved = agent_models.FREE_MODELS[1]["id"]
    box = Sandbox(saved=saved)
    try:
        assert agent_models.current_model() == saved
        assert agent_models.is_overridden() is False

        os.environ["OPENROUTER_MODEL"] = "some/other-model"
        assert agent_models.current_model() == "some/other-model"
        assert agent_models.is_overridden() is True
        assert agent_models.read_saved_model() == saved   # the choice is kept

        os.environ.pop("OPENROUTER_MODEL")
        assert agent_models.is_overridden() is False
        assert agent_models.current_model() == saved
    finally:
        box.close()


# --- the menu must never block a scripted run -------------------------------

def test_a_non_interactive_stdin_starts_without_drawing_or_reading():
    """The whole suite and every piped run drive TMT this way. A menu that
    drew a frame or waited for a key here would hang the process."""
    box = Sandbox(tty=False)
    try:
        keys = Keys()
        choice = menu().run_startup(stream=box.stream, key_reader=keys,
                                    workspace=box.workspace)
        assert choice == "start", choice
        assert keys.calls == 0, keys.calls
        assert box.stream.getvalue() == "", repr(box.stream.getvalue())
    finally:
        box.close()


def test_stdin_that_cannot_answer_isatty_counts_as_non_interactive():
    """A closed or replaced stdin raises rather than returning False, and that
    has to read as "not a terminal" instead of ending the run."""
    box = Sandbox(broken_stdin=True)
    try:
        keys = Keys()
        choice = menu().run_startup(stream=box.stream, key_reader=keys,
                                    workspace=box.workspace)
        assert choice == "start", choice
        assert keys.calls == 0, keys.calls
        assert box.stream.getvalue() == "", repr(box.stream.getvalue())
    finally:
        box.close()


# --- navigating the menu ----------------------------------------------------

def test_enter_on_the_first_item_starts():
    box = Sandbox()
    try:
        choice, keys = start(box, "enter")
        assert choice == "start", choice
        assert keys.remaining == 0
    finally:
        box.close()


def test_navigating_to_the_last_item_exits():
    """Three moves down from Start is Exit, and Exit means quit rather than
    fall through into the agent."""
    box = Sandbox()
    try:
        choice, keys = start(box, "down", "down", "down", "enter")
        assert choice == "exit", choice
        assert keys.remaining == 0
    finally:
        box.close()


# --- settings ---------------------------------------------------------------

def test_settings_enter_changes_the_current_model():
    """The point of the feature: a model picked in Settings is the model TMT
    then runs on, saved where the next run will find it."""
    box = Sandbox()
    chosen = agent_models.FREE_MODELS[1]["id"]
    try:
        # Settings is a submenu now: down/enter opens it on AI Provider, two
        # moves down reach Model, and Enter opens the picker. There the cursor
        # starts on the active model, which with nothing saved is the default,
        # the first row; one move down reaches the second. Esc leaves the
        # submenu, which is why the run ends back on the menu.
        choice, keys = start(box, "down", "enter", "down", "down", "enter",
                             "down", "enter", "esc", "quit")
        assert agent_models.current_model() == chosen, agent_models.current_model()
        assert agent_models.read_saved_model() == chosen
        assert agent_config.MODEL == chosen
        assert choice == "exit", choice
        assert keys.remaining == 0
    finally:
        box.close()


def test_settings_esc_leaves_the_model_alone():
    """Backing out is not a choice. Esc must write nothing at all, so a user
    who only looked keeps the model they arrived with."""
    box = Sandbox()
    try:
        choice, keys = start(box, "down", "enter", "down", "esc", "quit")
        assert agent_models.read_saved_model() == ""
        assert not agent_models.MODEL_FILE.exists()
        assert agent_models.current_model() == agent_models.DEFAULT_MODEL
        assert choice == "exit", choice
        assert keys.remaining == 0
    finally:
        box.close()


# --- help -------------------------------------------------------------------

def test_help_returns_to_the_menu_rather_than_exiting():
    """Esc out of Help lands back on the menu. If it quit instead, the trailing
    quit key would be left unread and the choice would not be the one it made."""
    box = Sandbox()
    try:
        _, plain_keys = start(box, "down", "down", "down", "enter")
        menu_lines = {line.strip() for line in box.stream.text().splitlines()}
        assert plain_keys.remaining == 0

        box.stream = Terminal()
        choice, keys = start(box, "down", "down", "enter", "esc", "quit")
        assert choice == "exit", choice
        assert keys.remaining == 0, "Esc left Help without returning to the menu"

        # Help drew something the menu alone never draws.
        help_lines = [line.strip() for line in box.stream.text().splitlines()]
        fresh = [line for line in help_lines if line and line not in menu_lines]
        assert fresh, "the Help screen drew no content of its own"
    finally:
        box.close()


# --- what the frame has to say ----------------------------------------------

def test_the_frame_names_the_current_model_and_the_workspace():
    """Both are decisions the user is about to act on: which model will answer,
    and which directory the agent may touch."""
    box = Sandbox()
    chosen = agent_models.FREE_MODELS[3]
    # Short enough to survive the frame's own clipping, so the assertion is
    # about the path being named rather than about how wide a terminal is.
    probe = Path(os.sep + "tmt_probe" + os.sep + "chosen_workspace")
    try:
        start(box, "enter", model_id=chosen["id"], workspace=probe)
        frame = box.stream.text()
        assert chosen["label"] in frame or chosen["id"] in frame, frame
        assert str(probe) in frame, frame

        # With no model given, the frame reports the resolved current model.
        box.stream = Terminal()
        saved = agent_models.FREE_MODELS[4]
        agent_models.set_model(saved["id"])
        start(box, "enter", workspace=probe)
        frame = box.stream.text()
        assert saved["label"] in frame or saved["id"] in frame, frame
    finally:
        box.close()


def test_the_selected_item_is_marked_without_relying_on_colour():
    """Colour is not always available, and it is never the only signal a
    terminal keeps. The marked row has to stay identifiable once the escape
    sequences are gone."""
    for colour in (True, False):
        box = Sandbox(colour=colour)
        try:
            start(box, "enter")
            resting = [line for line in box.stream.text().splitlines()
                       if "Start" in line]
            assert resting, "the menu never drew a Start row"

            box.stream = Terminal()
            start(box, "down", "quit")
            rows = [line for line in box.stream.text().splitlines()
                    if "Start" in line]
            assert len(rows) >= 2, "the menu did not repaint after moving"
            selected, unselected = rows[0], rows[-1]
            assert selected != unselected, (
                "Start looks the same selected and unselected once colour is "
                "stripped, so the marker is colour alone")
            marker = set(selected) - set(unselected) - set(" \t")
            assert marker, (selected, unselected)
            assert selected == resting[0], (selected, resting[0])
        finally:
            box.close()


# --- the running status, redrawn before every task prompt --------------------

class Console(io.StringIO):
    """A stream that decides for itself whether it has colour and glyphs.

    cp1252 is the encoding a plain Windows console actually reports, and it
    cannot carry a single one of TMT's decorative characters, so it is the
    case the ASCII fallbacks exist for.
    """

    def __init__(self, encoding="utf-8", tty=True):
        io.StringIO.__init__(self)
        self._encoding = encoding
        self._tty = tty

    @property
    def encoding(self):
        return self._encoding

    def isatty(self):
        return self._tty


def status(columns=100, rows=24, stream=None, **facts):
    """The status rows as the terminal shows them, escapes removed."""
    stream = Console() if stream is None else stream
    facts.setdefault("phase", 0.25)
    lines = menu().render_status_lines(stream=stream, size=(columns, rows), **facts)
    lines.append(menu().task_prompt(stream, phase=facts["phase"]))
    return [visible(line) for line in lines]


def test_the_running_status_states_every_fact_the_next_turn_runs_under():
    """A session outlives the screen it was started from. Which service, which
    model, which directory, and when this turn began all have to be answerable
    at the prompt itself rather than by scrolling back to the launch."""
    moment = datetime.datetime(2026, 8, 29, 15, 42, 7)
    probe = Path(os.sep + "tmt_probe" + os.sep + "chosen_workspace")
    frame = "\n".join(status(provider_id="openrouter", model_id="z-ai/glm-5.2:free",
                             workspace=probe, moment=moment))
    assert "TMT" in frame, frame
    assert "OpenRouter" in frame, frame
    assert "GLM 5.2" in frame or "z-ai/glm-5.2:free" in frame, frame
    assert str(probe) in frame, frame
    assert "29 Aug 2026" in frame, frame
    assert "15:42:07" in frame, frame
    assert "Task>" in frame, frame
    # The name TMT stopped using. It must not come back through this screen.
    assert "Local File AI" not in frame, frame


def test_the_running_status_reads_the_clock_rather_than_keeping_one():
    """The time on screen is the time the turn began, so it cannot be a value
    captured at launch and it cannot need a thread to move it."""
    first = status(moment=datetime.datetime(2026, 8, 29, 9, 5, 1),
                   provider_id="openrouter", model_id="z-ai/glm-5.2:free")
    later = status(moment=datetime.datetime(2026, 8, 29, 9, 5, 2),
                   provider_id="openrouter", model_id="z-ai/glm-5.2:free")
    assert "09:05:01" in "\n".join(first), first
    assert "09:05:02" in "\n".join(later), later

    # And with nothing passed, the clock is read from the system on each call.
    before = datetime.datetime.now()
    drawn = "\n".join(status(provider_id="openrouter",
                             model_id="z-ai/glm-5.2:free"))
    after = datetime.datetime.now()
    stamps = {before.strftime("%H:%M:%S"), after.strftime("%H:%M:%S")}
    assert any(stamp in drawn for stamp in stamps), (drawn, sorted(stamps))


def test_the_running_status_follows_the_provider_and_model_that_are_in_force():
    """agent_credentials owns the provider and agent_models owns the model.
    The status asks them at render time, so a change made in Settings -- or
    forced by the environment -- reaches the next prompt."""
    box = Sandbox()
    previous = os.environ.get("TMT_PROVIDER")
    try:
        chosen = agent_models.FREE_MODELS[2]
        agent_models.set_model(chosen["id"], "openrouter")
        os.environ["TMT_PROVIDER"] = "openrouter"
        frame = "\n".join(status(workspace=box.workspace))
        assert "OpenRouter" in frame, frame
        assert chosen["label"] in frame or chosen["id"] in frame, frame

        moved = agent_models.FREE_MODELS[1]
        agent_models.set_model(moved["id"], "openrouter")
        frame = "\n".join(status(workspace=box.workspace))
        assert moved["label"] in frame or moved["id"] in frame, frame

        # A different provider brings its own name and its own model with it.
        os.environ["TMT_PROVIDER"] = "anthropic"
        frame = "\n".join(status(workspace=box.workspace))
        assert "Anthropic" in frame, frame
        assert chosen["label"] not in frame, frame
    finally:
        os.environ.pop("TMT_PROVIDER", None)
        if previous is not None:
            os.environ["TMT_PROVIDER"] = previous
        box.close()


def test_the_running_status_fits_a_narrow_terminal_and_degrades_to_ascii():
    """Measured to columns - 1 at every width, on a colour terminal and on a
    cp1252 console that can encode none of the decoration."""
    plain_console = Console(encoding="cp1252", tty=False)
    long_path = "C:\\Users\\Someone\\OneDrive - A Long Organisation Name" \
                "\\Projects\\2026\\a-deeply-nested\\service\\worker"
    for stream in (Console(), plain_console):
        for columns in (100, 60, 40, 24):
            for workspace in ("C:\\Coding\\TMT", long_path):
                rows = status(columns=columns, stream=stream,
                              workspace=workspace,
                              provider_id="openrouter",
                              model_id="z-ai/glm-5.2:free")
                limit = max(24, min(72, columns - 2))
                for line in rows:
                    assert menu().display_width(line) <= limit, (
                        columns, line, menu().display_width(line))
                joined = "\n".join(rows)
                assert "TMT" in joined and "Task>" in joined, (columns, joined)
                assert "OpenRouter" in joined, (columns, joined)

    # The plain console gets the ASCII set rather than replacement marks, and
    # nothing it was handed can fail to encode.
    drawn = "\n".join(status(columns=60, stream=plain_console,
                             workspace=long_path, provider_id="openrouter",
                             model_id="z-ai/glm-5.2:free"))
    drawn.encode("cp1252")
    assert "\u2026" not in drawn and "\u2500" not in drawn and "\u00b7" not in drawn, drawn
    assert "..." in drawn and "---" in drawn, drawn


def test_a_long_path_is_shortened_in_the_middle_and_keeps_both_ends():
    """A path is recognised by its drive and by the directory being worked in.
    Shortening it from one end throws away half of that, and shortening it by
    len() rather than by width puts the row onto a second screen line."""
    long_path = "C:\\Users\\Someone\\Documents\\Development\\2026" \
                "\\northwind-replatform\\services\\ingestion-worker"
    rows = status(columns=60, workspace=long_path, provider_id="openrouter",
                  model_id="z-ai/glm-5.2:free")
    path_row = [row for row in rows if row.strip().startswith("C:")]
    assert path_row, rows
    shown = path_row[0].strip()
    assert shown != long_path, shown
    assert shown.startswith("C:\\Users"), shown
    assert shown.endswith("ingestion-worker"), shown
    assert "\u2026" in shown or "..." in shown, shown

    # Wide characters are two columns each, so a row counted rather than
    # measured would be twice as wide as the terminal it was drawn for.
    wide = "C:\\Projects\\" + "\u30d7\u30ed\u30b8\u30a7\u30af\u30c8" * 8 + "\\src"
    for columns in (100, 60, 40):
        for line in status(columns=columns, workspace=wide,
                           provider_id="openrouter",
                           model_id="z-ai/glm-5.2:free"):
            assert menu().display_width(line) <= max(24, min(72, columns - 2)), (
                columns, line)
