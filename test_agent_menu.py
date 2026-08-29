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
import threading
import time
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
    """The whole status presentation as the terminal shows it, escapes removed.

    Two pieces, because the facts are drawn in two places for a reason. The
    header carries what the session was started with and cannot change; the
    caption on the prompt box carries what can -- the clock, the provider and
    the model -- and is drawn again for every question. A test that looked at
    only one of them would be asking half the screen whether the whole screen
    is right.
    """
    stream = Console() if stream is None else stream
    facts.setdefault("phase", 0.25)
    lines = menu().render_status_lines(stream=stream, size=(columns, rows), **facts)
    caption = {key: facts[key] for key in ("moment", "provider_id", "model_id")
               if key in facts}
    lines.append(menu().prompt_caption(stream, menu()._content_width(columns),
                                       **caption))
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


def meter_session(added=1231, removed=123, sent=15400, back=30120, exact=True):
    import agent_session
    session = agent_session.Session(workspace="C:\\project")
    session.lines_added, session.lines_removed = added, removed
    session.tokens_in, session.tokens_out = sent, back
    session.tokens_out_exact = exact
    return session


def test_the_corner_meter_says_what_the_session_changed_and_what_it_cost():
    """Four figures in the top right: lines in, lines out, tokens sent,
    tokens generated. Green for what arrived and red for what left -- the two
    ends of the one gradient, used for what they already mean there.

    And it reads with the escapes stripped, because colour is confirmation and
    never the message."""
    session = meter_session()
    text = menu().meter_text(session, Console(), columns=120)
    bare = visible(text)
    assert bare == "+1231 lines, -123 lines, ~15k tokens in, 30k out", bare
    assert menu()._color("+1231", 95, Console()) in text, repr(text)
    assert menu()._color("-123", 10, Console()) in text, repr(text)

    # A terminal that cannot colour gets the same sentence.
    assert menu().meter_text(session, Console(tty=False), columns=120) == bare

    # Nothing has happened yet, so there is nothing to report -- not a row of
    # zeroes, which would read as a session that had tried and achieved none.
    import agent_session
    assert menu().meter_text(agent_session.Session(workspace="C:\\p"),
                             Console(), columns=120) == ""
    assert menu().meter_text(None, Console(), columns=120) == ""


def test_the_corner_meter_marks_the_figures_it_had_to_estimate():
    """No provider will count a request before it is sent, so tokens in are
    always an estimate and always carry the tilde. Tokens out carry it only
    when the provider did not report its own count. A number presented as
    measured when it was guessed is the one thing this project will not do."""
    exact = visible(menu().meter_text(meter_session(), Console(), columns=120))
    assert "~15k tokens in" in exact, exact
    assert "30k out" in exact and "~30k out" not in exact, exact

    guessed = visible(menu().meter_text(meter_session(exact=False), Console(),
                                        columns=120))
    assert "~15k tokens in" in guessed and "~30k out" in guessed, guessed


def test_the_corner_meter_gives_up_words_before_it_gives_up_figures():
    """It is a corner readout, so on a narrow terminal the labels go and the
    counts stay. What is given up is decided rather than incidental, and it
    never reaches the last column."""
    session = meter_session()
    for columns in (20, 30, 40, 60, 80, 120, 200):
        text = menu().meter_text(session, Console(), columns=columns)
        assert menu().visible_width(text) <= columns - 2, (columns, visible(text))
        if text:
            assert "+1231" in visible(text) and "-123" in visible(text), (columns, text)
    # Narrower than the two counts themselves and it says nothing at all
    # rather than a fragment of a number.
    assert menu().meter_text(session, Console(), columns=6) == ""


def test_the_corner_meter_is_drawn_where_the_scrolling_cannot_reach_it():
    """Row one is kept out of the terminal's scrolling region, so the readout
    sits still while everything else moves under it. Without that it would be
    stamped over whatever was at the top of the window and then scrolled into
    the history as wreckage, and the history is the surface this project will
    not spoil.

    The sequence saves the cursor, clears row one, writes against the right
    edge and puts the cursor back -- as one string, because a restore is to an
    absolute row and a line printed in between would move the terminal out
    from under it."""
    text = menu().meter_text(meter_session(), Console(), columns=100)
    sequence = menu().meter_sequence(text, Console(), size=(100, 40))
    assert sequence.startswith("\0337") and sequence.endswith("\0338"), repr(sequence)
    assert "\033[1;1H\033[2K" in sequence, repr(sequence)
    # Against the right edge, ending on the last column TMT draws to -- the
    # spare one is left, as everywhere else, because a row filled to the last
    # column wraps on the terminals that auto-wrap.
    column = int(re.search(r"\033\[1;(\d+)H(?!\033\[2K)", sequence).group(1))
    assert column + menu().visible_width(text) - 1 == 99, (column, text)
    assert column > 1, "the readout is in the corner, not against the left edge"
    assert menu().meter_sequence("", Console()) == ""

    screen = Terminal()
    assert menu().reserve_top_row(screen, size=(100, 40)) is True
    assert screen.getvalue() == "\033[2;40r\033[2;1H", repr(screen.getvalue())
    back = Terminal()
    assert menu().release_top_row(back) is True
    assert back.getvalue() == "\033[r", repr(back.getvalue())

    # A window too short to spare a row keeps all of them, and a pipe has no
    # scrolling region to set.
    assert menu().reserve_top_row(Terminal(), size=(100, 4)) is False
    assert menu().reserve_top_row(Console(tty=False), size=(100, 40)) is False
    assert menu().release_top_row(Console(tty=False)) is False


def test_the_header_is_one_component_and_draws_no_rule_of_its_own():
    """The screen used to open with the header's rule and then, one blank line
    later, the prompt box's -- two near-identical dividers around an empty gap
    that read as a badly placed box. One rule now, drawn by the thing it
    belongs to.

    What holds the header together instead is the indent: the workspace hangs
    under the wordmark at the same offset the prominence ladder already uses
    for detail belonging to the row above it. That reads with the colour
    stripped, which a coloured rule would not have."""
    probe = Path(os.sep + "tmt_probe" + os.sep + "chosen_workspace")
    lines = [visible(line) for line
             in menu().render_status_lines(stream=Console(), size=(100, 40),
                                           workspace=probe, phase=0.25)]
    assert lines[0] == "", lines            # a blank line above, as structure
    body = [line for line in lines if line.strip()]
    assert len(body) == 2, body
    assert body[0].startswith(" TMT"), body
    assert body[1].startswith("   ") and body[1].strip() == str(probe), body
    # No rule anywhere in it, in either character set.
    joined = "\n".join(lines)
    assert "─" not in joined and "---" not in joined, joined


def test_the_settled_facts_are_in_the_header_and_the_moving_ones_on_the_box():
    """Each fact once, in the place that can keep it true. The date and the
    workspace were settled before the first request and cannot change while
    the loop runs, so they are printed once. The clock, the provider and the
    model can all be different from one question to the next, so they are
    stated on the box, which is drawn again for every question anyway."""
    moment = datetime.datetime(2026, 8, 29, 15, 42, 7)
    probe = Path(os.sep + "tmt_probe" + os.sep + "chosen_workspace")
    header = "\n".join(visible(line) for line in menu().render_status_lines(
        stream=Console(), size=(100, 40), workspace=probe, moment=moment,
        provider_id="openrouter", model_id="z-ai/glm-5.2:free"))
    caption = visible(menu().prompt_caption(Console(), 80, moment,
                                            provider_id="openrouter",
                                            model_id="z-ai/glm-5.2:free"))

    assert "29 Aug 2026" in header and str(probe) in header, header
    assert "15:42:07" not in header, header
    assert "OpenRouter" not in header and "GLM 5.2" not in header, header

    assert "15:42:07" in caption and "OpenRouter" in caption, caption
    assert "GLM 5.2" in caption, caption
    # Right-aligned, so it ends where the rule below it ends and the left
    # column stays clear for the markers and the '>'.
    assert caption.startswith(" "), repr(caption)
    assert menu().display_width(caption) == 80, menu().display_width(caption)


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
                # One spare column, and no upper bound on the other side: the
                # interface fills the window it was given. The floor is the
                # one known limit -- a terminal narrower than 24 columns
                # overflows it, and does so on purpose rather than by
                # collapsing into something unreadable.
                limit = max(24, columns - 1)
                for line in rows:
                    assert menu().display_width(line) <= limit, (
                        columns, line, menu().display_width(line))
                joined = "\n".join(rows)
                assert "TMT" in joined and "Task>" in joined, (columns, joined)
                # What survives a narrow terminal is decided rather than
                # incidental. The clock and which model answers are the last
                # two facts standing; the provider's name is given up before
                # either, because "GLM 5.2" says more about the next request
                # than "OpenRouter" does.
                assert "GLM 5.2" in joined, (columns, joined)
                assert ":" in joined, (columns, joined)
                if columns >= 40:
                    assert "OpenRouter" in joined, (columns, joined)

    # The plain console gets the ASCII set rather than replacement marks, and
    # nothing it was handed can fail to encode. The rule lives on the prompt
    # box now -- the header does not draw one -- so the box goes in too.
    box = menu().PromptBox(stream=plain_console)
    drawn = "\n".join(status(columns=60, stream=plain_console,
                             workspace=long_path, provider_id="openrouter",
                             model_id="z-ai/glm-5.2:free")
                      + [visible(line) for line
                         in box.lines(editor(SUGGESTION), size=(60, 24))])
    drawn.encode("cp1252")
    assert "\u2026" not in drawn and "\u2500" not in drawn and "\u00b7" not in drawn, drawn
    assert "..." in drawn and "---" in drawn, drawn


def test_the_interface_fills_the_window_it_was_given():
    """It used to stop growing at 72 columns, which on a wide terminal left
    the whole session in a strip down the left with the rest of the window
    unused -- and made the prompt box read as a panel dropped onto the
    terminal rather than as the interface of the thing running in it.

    The rule is the measure, because it is the one row that is drawn to the
    full width by construction. One spare column at the right, and nothing
    given up on the left."""
    for columns in (60, 100, 160, 240):
        rows = prompt_rows(editor(SUGGESTION), columns=columns)
        rule = rows[1]
        assert menu().display_width(rule) == columns - 1, (columns, len(rule))
        # And the header agrees with it, so the two read as one interface.
        header = status(columns=columns, workspace="C:\\Coding\\TMT",
                        provider_id="openrouter", model_id="z-ai/glm-5.2:free")
        for line in header:
            assert menu().display_width(line) <= columns - 1, (columns, line)


def test_the_prompt_box_is_at_the_foot_of_the_window_from_the_first_frame():
    """Two things are wanted at once and they look like they contradict: the
    box at the bottom from the moment the session opens, and the output
    reading downward from the header at the top. What reconciles them is that
    the gap between the two is inside the region, as blank rows above the box.

    The arithmetic is answerable only at the start, because the screen has
    just been cleared and the cursor is on a row we know. Everything below the
    reserved row, the header, the blank line the box writes and the box itself
    is pad."""
    rows, header = 30, 3
    used = menu()._TOP_ROW_RESERVED + header
    pad = menu().opening_pad(used, Terminal(), size=(80, rows))
    # The last row is not part of the box: a region is painted by writing each
    # row and a newline, so the cursor ends one below it.
    assert used + pad + 1 + menu().PROMPT_HEIGHT + 1 == rows, pad

    box = menu().PromptBox(stream=Terminal(), pad=menu().BottomPad(pad))
    frame = box.lines(editor(SUGGESTION), size=(80, rows))
    assert len(frame) == pad + menu().PROMPT_HEIGHT, (len(frame), pad)
    assert frame[:pad] == [""] * pad, frame[:pad]
    assert prompt_input_row(frame).strip().startswith(">"), frame

    # A pipe has no window to sit at the foot of.
    assert menu().opening_pad(used, Console(tty=False), size=(80, rows)) == 0


def test_a_permanent_line_takes_a_blank_row_instead_of_moving_the_box():
    """`write_above` erases the region, prints where it stood, and paints it
    again below. Painting it one row shorter puts it back on exactly the rows
    it already occupied, so the box does not move and the printed line lands
    in the space the pad gave up.

    The count never has to be exact: it only ever decreases, it is part of the
    region's own line list so no repaint arithmetic depends on it, and a row
    out means the box sits a row off the bottom until the next line of output
    corrects it."""
    pad = menu().BottomPad(10)
    assert pad.above(menu().PROMPT_HEIGHT, size=(80, 30)) == 10
    pad.spend("one line\n")
    assert pad.rows == 9
    pad.spend("two\nlines\n")
    assert pad.rows == 7
    pad.take(3)
    assert pad.rows == 4

    # A taller region needs fewer of them to keep its bottom edge in the same
    # place: the relay's carries the reply and the status row as well.
    assert pad.above(menu().PROMPT_HEIGHT, size=(80, 30)) == 4
    assert pad.above(menu().PROMPT_HEIGHT + 3, size=(80, 30)) == 1
    assert pad.above(menu().PROMPT_HEIGHT + 9, size=(80, 30)) == 0

    # It never goes below nothing, and never asks for a region taller than the
    # window -- a terminal made shorter mid-session must not be able to.
    pad.take(99)
    assert pad.rows == 0
    assert menu().BottomPad(40).above(4, size=(80, 12)) <= 12 - 4


def test_a_screen_opens_at_the_top_of_the_window():
    """The session starts where a reader starts: the top. Everything TMT then
    prints reads downward from the header -- the question, the work, the
    answer, in the order they happened -- and once the window is full the
    terminal scrolls and the prompt box, always the last thing written, sits
    at the foot of it and stays there.

    The viewport only. The scrollback above is somebody's shell session and
    not TMT's to throw away, so the sequence that would take it is not sent
    and the alternate screen buffer is not used -- that buffer would lose the
    session's own history, which is the terminal's scrollback, the moment the
    session ended."""
    screen = Terminal()
    assert menu().clear_screen(screen) is True
    assert screen.getvalue() == "\033[2J\033[H", repr(screen.getvalue())
    assert "\033[3J" not in screen.getvalue(), "the scrollback is not ours to clear"
    assert "\033[?1049h" not in screen.getvalue(), "no alternate screen buffer"

    # A pipe has no screen to clear, and the escape would land in whatever was
    # capturing the output.
    piped = Console(tty=False)
    assert menu().clear_screen(piped) is False
    assert piped.getvalue() == "", repr(piped.getvalue())


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
            assert menu().display_width(line) <= max(24, columns - 1), (
                columns, line)


# --- the prompt box, and the suggestion drawn in an empty one ----------------
#
# One property holds all of these up: the placeholder is drawn and never held.
# A suggestion that could reach the buffer would be submitted as a task the
# user never asked for, so the tests below check the buffer itself as well as
# what the box returns.

SUGGESTION = "Review the changed files"


class Typing:
    """A scripted raw-key reader for the prompt box.

    Running past the end of the script ends the input rather than waiting: a
    box still asking for keys after Enter has not returned, and the test says
    so instead of hanging the suite.
    """

    def __init__(self, *strokes):
        self.queue = list(strokes)
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if not self.queue:
            raise IndexError("the prompt box asked for key %d; the script had %d"
                             % (self.calls, self.calls - 1))
        return self.queue.pop(0)

    @property
    def remaining(self):
        return len(self.queue)


def editor(placeholder="", typed=""):
    """A LineEditor with `typed` entered through it, one key at a time."""
    made = menu().LineEditor(placeholder)
    for char in typed:
        made.handle("char", char)
    return made


def prompt_rows(state, columns=60, stream=None):
    """The prompt box as the terminal shows it, escapes removed.

    Four rows: the dim caption, the top rule, the line being typed, and the
    bottom rule. The line is at `menu()._INPUT_ROW`, which is also what the
    caret arithmetic counts back from, so a test that reaches for it by that
    name cannot disagree with the box about which row it is.
    """
    stream = Console() if stream is None else stream
    box = menu().PromptBox(stream=stream)
    return [visible(line) for line in box.lines(state, size=(columns, 24))]


def prompt_input_row(rows):
    """The row of a drawn box that the user is typing on.

    Counted from the bottom, as `_INPUT_ROW` is and as the caret placement is.
    The top of the frame moves -- the caption goes while a turn runs, and the
    blank rows holding the box against the foot of the window come and go --
    while the distance from the line to the bottom rule never changes.
    """
    return rows[-menu()._INPUT_ROW]


def test_the_prompt_box_draws_the_suggestion_and_never_answers_with_it():
    """The placeholder is shadow text: on screen while the box is empty, and
    no part of the buffer at any moment. Enter on an untouched box is an empty
    line, which is what the user entered."""
    state = editor(SUGGESTION)
    assert state.value == ""
    assert state.cursor == 0
    assert state.placeholder_visible is True

    rows = prompt_rows(state)
    assert len(rows) == 4, rows
    assert SUGGESTION in prompt_input_row(rows), rows
    # The marker carries the prompt on its own, with the colour stripped.
    assert prompt_input_row(rows).strip().startswith(">"), rows

    answer = menu().PromptBox(stream=Terminal(), reader=Typing("\r")).ask(SUGGESTION)
    assert answer == "", repr(answer)


def test_the_first_prompt_of_a_session_opens_with_a_real_placeholder():
    """An empty box with a bare '>' in it looks broken. The opening line is a
    placeholder like every other one -- drawn, never assigned -- and it is
    measured by the same rule the next-step hints are, so the first line of a
    session and every line after it read as the same kind of thing."""
    import agent_ui
    hint = agent_ui.OPENING_SUGGESTION
    ok, cleaned, _ = agent_ui.validate_suggestion(hint)
    assert ok, (hint, cleaned)
    assert agent_ui.count_words(hint) <= agent_ui.MAX_SUGGESTION_WORDS, hint

    state = editor(hint)
    assert state.value == "", repr(state.value)          # not the buffer
    assert state.placeholder_visible is True
    rows = prompt_rows(state)
    assert hint in prompt_input_row(rows), rows

    # And it is drawn dim, which is what separates it from something typed.
    painted = menu().PromptBox(stream=Console()).lines(state, size=(60, 24))
    assert menu().DIM in painted[menu()._INPUT_ROW], repr(painted[menu()._INPUT_ROW])
    typed = menu().PromptBox(stream=Console()).lines(editor(hint, "H"), size=(60, 24))
    assert menu().DIM not in typed[menu()._INPUT_ROW], repr(typed[menu()._INPUT_ROW])


def test_the_running_box_says_what_is_happening_and_carries_no_caption():
    """While a turn runs the box is on screen but nothing typed into it would
    be read, so a box that looked ready for input would be a lie about what
    the program is doing. And the question it is waiting on already carries a
    caption in the scrollback above, so a second one here would be the same
    fact twice, moving under the reply as it arrives."""
    import agent_ui
    box = menu().PromptBox(stream=Console())
    rows = [visible(line) for line in box.running_lines(agent_ui.RUNNING_HINT,
                                                        size=(80, 24))]
    assert len(rows) == 3, rows                    # a rule, the line, a rule
    assert rows[0] == rows[2], rows
    assert rows[1].strip() == "> " + agent_ui.RUNNING_HINT, rows[1]
    # No clock, so nothing here ticks under the reply.
    assert not re.search(r"\d\d:\d\d:\d\d", "\n".join(rows)), rows
    # Shadow text, so it is dim and could never be mistaken for a typed line.
    painted = box.running_lines(agent_ui.RUNNING_HINT, size=(80, 24))
    assert menu().DIM in painted[1], repr(painted[1])


def test_the_question_is_written_into_scrollback_when_it_is_answered():
    """The box that collected it is a live region and is taken down the moment
    it is answered, so this is the only record of what was asked. The caption
    goes with it -- when, and what was about to answer -- and the rules do
    not: they said "type here", and this is a record of something said."""
    moment = datetime.datetime(2026, 8, 29, 15, 42, 7)
    screen = Console()
    assert menu().render_task("  fix   the parser  ", screen, size=(80, 24),
                              moment=moment) is True
    rows = visible(screen.getvalue()).rstrip("\n").split("\n")
    assert len(rows) == 2, rows
    assert "15:42:07" in rows[0], rows
    assert rows[1].strip() == "> fix the parser", rows        # whitespace normalised
    assert "─" not in screen.getvalue() and "---" not in screen.getvalue(), rows

    # Nothing asked, nothing written.
    quiet = Console()
    assert menu().render_task("   ", quiet) is True
    assert quiet.getvalue() == "", repr(quiet.getvalue())


def test_the_first_keystroke_replaces_the_suggestion_rather_than_appending():
    """The one that must not regress. A placeholder still in the buffer when
    the first character lands would submit "Review the changed filesR"."""
    state = editor(SUGGESTION)
    assert state.handle("char", "R") == "continue"
    assert state.value == "R", repr(state.value)
    assert state.cursor == 1
    assert state.placeholder_visible is False

    rows = prompt_rows(state)
    assert prompt_input_row(rows).strip() == "> R", rows
    assert SUGGESTION not in "\n".join(rows), rows

    answer = menu().PromptBox(stream=Terminal(),
                              reader=Typing("R", "\r")).ask(SUGGESTION)
    assert answer == "R", repr(answer)
    assert SUGGESTION not in answer, repr(answer)
    assert "Review" not in answer, repr(answer)


def test_the_suggestion_does_not_come_back_after_backspacing_to_empty():
    """It was dismissed by the first character typed and stays dismissed: a
    suggestion reappearing mid-edit reads as text about to be submitted."""
    state = editor(SUGGESTION, "R")
    assert state.handle("key", "backspace") == "continue"
    assert state.value == ""
    assert state.cursor == 0
    assert state.placeholder_visible is False
    assert "Review" not in "\n".join(prompt_rows(state)), prompt_rows(state)

    answer = menu().PromptBox(stream=Terminal(),
                              reader=Typing("R", "\x7f", "\r")).ask(SUGGESTION)
    assert answer == "", repr(answer)


def test_every_editing_key_edits_the_line():
    """A field that can only append is not an editor. Each key is driven
    through LineEditor.handle, where the whole of the editing lives."""
    state = editor(typed="hello world")
    assert (state.value, state.cursor) == ("hello world", 11)

    state.handle("key", "backspace")
    assert state.value == "hello worl", state.value
    state.handle("key", "home")
    assert state.cursor == 0
    state.handle("key", "delete")
    assert state.value == "ello worl", state.value
    state.handle("key", "right")
    assert state.cursor == 1
    state.handle("key", "left")
    assert state.cursor == 0
    state.handle("key", "end")
    assert state.cursor == len(state.value)
    state.handle("key", "delete_word")          # Ctrl-W
    assert state.value == "ello ", state.value
    state.handle("key", "clear")                # Ctrl-U
    assert (state.value, state.cursor) == ("", 0)

    # A character typed with the cursor moved back lands where the cursor is,
    # not at the end.
    state = editor(typed="ac")
    state.handle("key", "left")
    state.handle("char", "b")
    assert (state.value, state.cursor) == ("abc", 2)

    # Neither end of the line can be walked off.
    state.handle("key", "home")
    state.handle("key", "left")
    assert state.cursor == 0
    state.handle("key", "backspace")
    assert state.value == "abc", state.value
    state.handle("key", "end")
    state.handle("key", "right")
    assert state.cursor == 3
    state.handle("key", "delete")
    assert state.value == "abc", state.value


def test_the_keys_that_finish_a_line_say_which_way_it_finished():
    """Enter, Ctrl-C and the end of the input are three different outcomes,
    and only one of them is an answer."""
    assert editor().handle("key", "enter") == "submit"
    assert editor().handle("key", "interrupt") == "cancel"
    assert editor().handle("key", "esc") == "cancel"
    assert editor().handle("end", None) == "end"
    assert editor().handle("", "") == "continue"        # an animation tick
    assert editor().handle("key", "eof") == "end"       # Ctrl-D, empty line

    # Ctrl-D with something typed is the forward delete it is everywhere else.
    state = editor(typed="ab")
    state.handle("key", "home")
    assert state.handle("key", "eof") == "continue"
    assert state.value == "b", state.value

    # An abandoned line and an empty one both mean "ask me again", so both
    # come back as "". `cancelled` is how a caller that wants to say something
    # about it tells them apart. Only a genuinely ended input returns None,
    # because that is the one case where asking again would never be answered.
    for interrupt in ("\x03", "\x1b"):
        box = menu().PromptBox(stream=Terminal(), reader=Typing(interrupt))
        assert box.ask(SUGGESTION) == "", interrupt
        assert box.cancelled, interrupt

    ended = menu().PromptBox(stream=Terminal(), reader=Typing("\x04"))
    assert ended.ask(SUGGESTION) is None
    assert not ended.cancelled


def test_a_paste_delivered_as_one_read_lands_whole():
    """A paste arrives as a single multi-character read. Split into characters
    it would be reordered by anything applied per keystroke, and truncated by
    anything limited per keystroke."""
    pasted = "refactor agent_menu.py, then run the tests"
    assert menu().normalize_text_key(pasted) == ("char", pasted)

    state = editor(SUGGESTION)
    assert state.handle("char", pasted) == "continue"
    assert state.value == pasted, state.value
    assert state.cursor == len(pasted)
    assert state.placeholder_visible is False

    answer = menu().PromptBox(stream=Terminal(),
                              reader=Typing(pasted, "\r")).ask(SUGGESTION)
    assert answer == pasted, repr(answer)


def test_the_editing_keys_arrive_however_the_terminal_sends_them():
    """Terminals disagree about how they report a key. The field acts on the
    name, so every spelling of one key has to reduce to the same name."""
    expected = {
        "\r": "enter", "\n": "enter", "\x03": "interrupt",
        "\x7f": "backspace", "\x08": "backspace", "backspace": "backspace",
        "\x1b[3~": "delete", "\x1b[3": "delete", "delete": "delete",
        "\x1b[D": "left", "\x1bOD": "left", "left": "left",
        "\x1b[C": "right", "\x1bOC": "right", "right": "right",
        "\x1b[H": "home", "\x1b[1~": "home", "home": "home",
        "\x1b[F": "end", "\x1b[4~": "end", "end": "end",
        "\x15": "clear", "\x17": "delete_word", "\x04": "eof",
    }
    for stroke, name in expected.items():
        assert menu().normalize_text_key(stroke) == ("key", name), repr(stroke)

    # And what the API key screen already relies on is untouched: text is
    # text, a tick is nothing, and the end of the input is the end.
    assert menu().normalize_text_key("k") == ("char", "k")
    assert menu().normalize_text_key("") == ("", "")
    assert menu().normalize_text_key(None) == ("end", None)
    assert menu().normalize_text_key("up") == ("", "")
    assert menu().normalize_text_key("down") == ("", "")


def test_the_prompt_box_borders_stay_well_formed_at_every_width():
    """Measured to columns - 1 at every width, empty and full. A row drawn to
    the last column wraps on the terminals that auto-wrap, which costs a
    screen line the repaint arithmetic does not know about."""
    long_text = "refactor the renderer so the input row scrolls sideways " * 3
    states = (editor(SUGGESTION), editor(typed=long_text),
              editor(typed="\u30d7\u30ed\u30b8\u30a7\u30af\u30c8" * 12))
    for columns in (20, 24, 40, 80, 100, 200):
        for state in states:
            rows = prompt_rows(state, columns=columns)
            assert len(rows) == 4, rows
            for row in rows:
                assert menu().display_width(row) <= columns - 1, (
                    columns, row, menu().display_width(row))
            assert prompt_input_row(rows).strip().startswith(">"), (columns, rows)
            # The rules are a single repeated glyph, and the same rule twice.
            assert set(rows[1].strip()) <= {"-", "\u2500"}, rows[1]
            assert rows[1] == rows[3], rows
            # The caption is the caption and nothing else: it carries no rule
            # glyph, so the box never reads as three dividers.
            assert not set(rows[0].strip()) & {"-", "\u2500"}, rows[0]


def test_a_long_line_scrolls_sideways_to_keep_the_cursor_in_view():
    """The line is one row however long it gets, so the row moves under the
    cursor rather than the text wrapping out of the box."""
    text = " ".join("word%d" % number for number in range(40))
    state = editor(typed=text)
    row = prompt_input_row(prompt_rows(state, columns=40))
    assert menu().display_width(row) <= 39, row
    assert row.rstrip().endswith(text[-6:]), row      # the cursor end is shown
    assert text[:6] not in row, row                   # the far end has scrolled

    state.handle("key", "home")
    row = prompt_input_row(prompt_rows(state, columns=40))
    assert row.strip().startswith("> " + text[:6]), row
    assert not row.rstrip().endswith(text[-6:]), row


def test_the_prompt_box_degrades_to_ascii_where_it_cannot_be_drawn():
    """A deliberate ASCII box reads as a choice; a row of replacement marks
    reads as a bug. cp1252 is what a plain Windows console reports."""
    plain_console = Console(encoding="cp1252", tty=False)
    drawn = "\n".join(prompt_rows(editor(SUGGESTION), stream=plain_console))
    drawn.encode("cp1252")               # nothing drawn can fail to encode
    assert "\u2500" not in drawn, drawn
    assert "---" in drawn, drawn
    assert "> " + SUGGESTION in drawn, drawn

    # The same box on a terminal keeps its meaning once colour is stripped.
    coloured = "\n".join(prompt_rows(editor(SUGGESTION), stream=Console()))
    assert "> " + SUGGESTION in coloured, coloured
    assert "\u2500" in coloured, coloured


def test_a_prompt_box_without_raw_keys_reads_a_line_instead_of_blocking():
    """The property the whole suite rests on. Where raw keys cannot arrive --
    a pipe, a redirect, a scripted run -- the box is drawn and a whole line is
    read, so nothing waits for a keystroke that can never come."""
    stream = Terminal()
    box = menu().PromptBox(stream=stream, instream=io.StringIO("fix the parser\n"))
    # A terminal to draw on, an input that is not one: exactly the piped case.
    assert menu().is_interactive(stream, box.instream) is False

    # Answered on a watchdog rather than called directly, so a box that does
    # wait for a keystroke here is a failure in five seconds instead of a
    # suite that never finishes.
    outcome = {}
    worker = threading.Thread(target=lambda: outcome.setdefault("answer", box.ask(SUGGESTION)),
                              daemon=True)
    started = time.monotonic()
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), "the box waited for a key that cannot arrive"
    assert outcome.get("answer") == "fix the parser", outcome
    assert time.monotonic() - started < 5, "the fallback blocked"
    assert SUGGESTION in stream.text(), stream.text()   # the box was still drawn

    # The end of the input is None; an empty line is an empty line; and
    # neither is ever the suggestion.
    ended = menu().PromptBox(stream=Terminal(), instream=io.StringIO("")).ask(SUGGESTION)
    assert ended is None, repr(ended)
    blank = menu().PromptBox(stream=Terminal(), instream=io.StringIO("\n")).ask(SUGGESTION)
    assert blank == "", repr(blank)


def test_an_untouched_prompt_box_neither_repaints_nor_moves_the_caret():
    """The reported bug, from the other end. The reader returns every 80ms
    whether a key arrived or not, and the loop used to redraw the box and walk
    the caret down to the foot of it and back on every one of those passes --
    so a prompt nobody was typing at flickered twelve times a second.

    Two things stop it, and both are checked here. The box does not animate,
    so two frames of an untouched prompt are identical; and the loop only
    takes the caret out of the input row when the frame it is about to paint
    is different from the one on screen."""
    stream = Terminal()
    # Twenty animation ticks -- what two and a half seconds of sitting still
    # looks like to the box -- and then Enter.
    typed = Typing(*(["" for _ in range(20)] + ["\r"]))
    answer = menu().PromptBox(stream=stream, reader=typed).ask(SUGGESTION)
    assert answer == "", repr(answer)
    assert typed.remaining == 0

    raw = stream.getvalue()
    # The box was drawn once, not once per tick. The marker row is the one
    # that changes when anything at all changes, so counting it counts paints.
    assert stream.text().count("> " + SUGGESTION) == 1, stream.text()
    # And the caret was put in the input row once and left there. `_place`
    # moves it up two rows; one of those is one placement.
    assert raw.count("\033[2A") == 1, raw.count("\033[2A")

    # Typing does move it, because then there is something new to show.
    busy = Terminal()
    menu().PromptBox(stream=busy, reader=Typing("h", "i", "\r")).ask(SUGGESTION)
    assert busy.getvalue().count("\033[2A") >= 3, busy.getvalue().count("\033[2A")


def test_the_caret_is_hidden_for_the_repaint_and_shown_in_the_input_row():
    """The caret belongs in the row being typed in and nowhere else. It is
    switched off for the length of the repaint, moved into the row, and only
    then switched back on, so it is never drawn on a row it is only passing
    through."""
    stream = Terminal()
    menu().PromptBox(stream=stream, reader=Typing("h", "\r")).ask(SUGGESTION)
    raw = stream.getvalue()
    assert "\033[?25l" in raw, repr(raw)
    # The caret comes back immediately after being moved into the input row
    # and not a byte sooner: what is between those two points is the move
    # itself, which is the thing that must not be watched happening. Matching
    # them as one sequence is what says "in that order, with nothing else
    # between them" -- two separate searches would pass just as happily on a
    # caret restored much later, by the teardown, having flickered all the way
    # down the box first.
    placed_then_shown = re.compile("\033\\[2A\r(?:\033\\[[0-9]+C)?\033\\[\\?25h")
    assert placed_then_shown.search(raw), repr(raw)
    # And the terminal is left with a caret however the box ended.
    assert raw.rstrip().endswith("\033[?25h"), repr(raw[-40:])


def test_the_box_is_taken_down_once_the_question_is_answered():
    """It is a live region, not a record. The question goes into scrollback in
    the transcript's own voice; the frame that collected it is drawn again a
    moment later at the foot of the turn. Left behind instead, every question
    of the session would sit on screen inside a box that no longer accepts
    anything, and an empty prompt would stack an empty box each time."""
    stream = Terminal()
    menu().PromptBox(stream=stream, reader=Typing("h", "i", "\r")).ask(SUGGESTION)
    raw = stream.getvalue()
    # LiveRegion.clear walks up over the four rows, erases each, and walks
    # back up: that is the box being taken down rather than left painted.
    taken_down = "\033[4A" + "\r\033[2K\n" * 4 + "\033[4A"
    assert taken_down in raw, repr(raw[-120:])
    assert raw.index(taken_down) > raw.rindex("hi"), "taken down before the last paint"

    # The degraded path has no region to take down, so its box stays -- which
    # is right: a piped run has no repaint and the box is its only record.
    piped = Terminal()
    menu().PromptBox(stream=piped, instream=io.StringIO("fix it\n")).ask(SUGGESTION)
    assert "\033[2K" not in piped.getvalue(), repr(piped.getvalue())
    assert SUGGESTION in piped.text(), piped.text()


def test_the_prompt_box_repaints_in_place_rather_than_reprinting_itself():
    """Four rows repainted through LiveRegion, not a fresh box per keystroke
    walking the conversation up the screen."""
    stream = Terminal()
    typed = Typing("h", "i", "\r")
    answer = menu().PromptBox(stream=stream, reader=typed).ask(SUGGESTION)
    assert answer == "hi", repr(answer)
    assert typed.remaining == 0
    raw = stream.getvalue()
    assert "\033[4A" in raw, "the box was reprinted rather than repainted"
    assert stream.text().count("> hi") >= 1, stream.text()
