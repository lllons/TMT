"""Tests for the slash commands and the completion offered while typing them.

Two properties hold the rest up. A slash command never reaches the model, and
an ordinary task always does -- including one that merely starts with a slash,
because a path is not a command. Everything else here is what each command
says, and the one thing none of them may ever say: a credential.
"""

import io
import os
import re
import tempfile
from pathlib import Path

import agent_commands
import agent_config
import agent_models
import agent_session

ESCAPE_RE = re.compile("\033\\[[0-9;?]*[ -/]*[@-~]")


def visible(text):
    return ESCAPE_RE.sub("", text)


class Terminal(io.StringIO):
    """A buffer that claims to be a terminal, so colour is exercised."""

    def isatty(self):
        return True

    @property
    def encoding(self):
        return "utf-8"


class Settings:
    """Redirect the effort and model files, and put them back."""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="tmt_cmd_"))
        self.previous_effort_file = agent_config.EFFORT_FILE
        self.previous_effort = agent_config.EFFORT
        self.previous_model_file = agent_models.MODEL_FILE
        self.previous_config_model = agent_config.MODEL
        self.previous_env = os.environ.get("OPENROUTER_MODEL")
        agent_config.EFFORT_FILE = self.dir / ".tmt_effort"
        agent_models.MODEL_FILE = self.dir / ".tmt_model"
        os.environ.pop("OPENROUTER_MODEL", None)

    def close(self):
        agent_config.EFFORT_FILE = self.previous_effort_file
        agent_config.EFFORT = self.previous_effort
        agent_models.MODEL_FILE = self.previous_model_file
        agent_config.MODEL = self.previous_config_model
        os.environ.pop("OPENROUTER_MODEL", None)
        if self.previous_env is not None:
            os.environ["OPENROUTER_MODEL"] = self.previous_env
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)


def session(turns=0):
    made = agent_session.Session(workspace="C:\\project")
    for number in range(turns):
        made.record("Question %d." % number, "Answer %d." % number)
    return made


# --- parsing ----------------------------------------------------------------

def test_a_slash_command_is_recognised_and_a_task_is_not():
    """The fork the whole feature rests on. `parse` returning None is what
    sends a line to the model, so anything that is not certainly a command
    must come back as None rather than as an error."""
    assert agent_commands.parse("/context") == ("context", "")
    assert agent_commands.parse("  /config  ") == ("config", "")
    assert agent_commands.parse("/effort high") == ("effort", "high")
    assert agent_commands.parse("/model  minimax/minimax-m3:free") == (
        "model", "minimax/minimax-m3:free")

    for ordinary in ("fix the authentication bug", "", "   ", "what does / mean",
                     "use the /api endpoint"):
        assert agent_commands.parse(ordinary) is None, ordinary
    # A slash and nothing else is somebody who has started typing.
    assert agent_commands.parse("/") is None
    assert agent_commands.parse("/ hello") is None
    # A path is not a command, and answering "unknown command" to a sentence
    # that happens to begin with one would be worse than sending it on.
    assert agent_commands.parse("/usr/bin/python is broken") is None
    assert agent_commands.parse("/etc/hosts") is None
    assert agent_commands.parse(None) is None
    assert agent_commands.parse(42) is None


def test_command_names_are_case_insensitive_and_arguments_are_not():
    """A name is a word TMT chose, so its case is TMT's business. An argument
    is a model id, and case is part of one."""
    for spelling in ("/CONTEXT", "/Context", "/cOnTeXt"):
        assert agent_commands.parse(spelling) == ("context", ""), spelling
        assert agent_commands.dispatch(spelling).ok, spelling
    assert agent_commands.parse("/MODEL GLM-5.2") == ("model", "GLM-5.2")
    assert agent_commands.parse("/Effort HIGH") == ("effort", "HIGH")


def test_every_command_in_the_table_has_a_summary_and_a_usage_line():
    """The completion list and the error messages read from these, so a
    command added without them would offer a blank row or crash a refusal."""
    assert set(agent_commands.names()) == {"context", "config", "clear",
                                           "effort", "model", "note",
                                           "agents", "plan", "review"}
    for name in agent_commands.names():
        assert agent_commands.SUMMARY.get(name), name
        assert agent_commands.USAGE.get(name, "").startswith("/" + name), name


# --- completion -------------------------------------------------------------

def test_typing_a_slash_offers_the_commands_and_narrows_as_you_type():
    assert [name for name, _ in agent_commands.completions("/")] == [
        "/context", "/config", "/clear", "/effort", "/model", "/note",
        "/agents", "/plan", "/review"]
    assert [name for name, _ in agent_commands.completions("/mo")] == ["/model"]
    assert [name for name, _ in agent_commands.completions("/c")] == [
        "/context", "/config", "/clear"]
    assert [name for name, _ in agent_commands.completions("/co")] == [
        "/context", "/config"]
    assert agent_commands.completions("/zzz") == ()
    # An ordinary task offers nothing, which is why the box a session spends
    # its time in is unchanged.
    for ordinary in ("fix the bug", "", "/usr/bin"):
        assert agent_commands.completions(ordinary) == (), ordinary


def test_tab_completes_as_far_as_the_candidates_agree_and_no_further():
    """Never a guess: every character it adds is one every candidate has."""
    assert agent_commands.completed("/mo") == "/model "
    assert agent_commands.completed("/co") == "/con"      # context and config
    assert agent_commands.completed("/cl") == "/clear "
    assert agent_commands.completed("/con") == ""         # they diverge here
    assert agent_commands.completed("/") == ""            # nothing shared
    assert agent_commands.completed("fix the bug") == ""
    assert agent_commands.completed("/zzz") == ""


# --- what each command says -------------------------------------------------

def test_context_reports_the_conversation_and_what_it_runs_on():
    made = session(turns=3)
    made.tokens_in, made._tokens_out = 15400, 800
    made.lines_added, made.lines_removed = 42, 7
    result = agent_commands.dispatch("/context", made)
    assert result.ok
    text = result.text()
    for expected in ("Model", "Provider", "Workspace", "Turns", "Tokens", "Lines"):
        assert expected in text, expected
    assert "3 asked" in text, text
    assert "+42  -7" in text, text
    assert "Question 2." in text, text          # the most recent question
    # The estimate is marked as one wherever it is not a measurement.
    assert "~15k" in text, text

    # It works with no session at all, rather than raising.
    alone = agent_commands.dispatch("/context")
    assert alone.ok and "no conversation yet" in alone.text()


def test_config_reports_the_settings_a_request_runs_under():
    box = Settings()
    try:
        agent_config.set_effort("high")
        result = agent_commands.dispatch("/config", session())
        assert result.ok
        text = result.text()
        for expected in ("Model", "Provider", "Effort", "Streaming",
                         "Workspace", "API key"):
            assert expected in text, expected
        assert "high" in text, text
        assert "8192" in text, text            # what high actually changes
    finally:
        box.close()


def test_clear_forgets_the_conversation_and_keeps_everything_else():
    box = Settings()
    try:
        agent_config.set_effort("low")
        made = session(turns=4)
        made.lines_added = 11
        workspace, model = made.workspace, made.model_id

        result = agent_commands.dispatch("/clear", made)
        assert result.ok
        assert len(made) == 0, "the conversation is gone"
        assert made.carried_messages() == []
        # And nothing else moved.
        assert made.workspace == workspace
        assert made.model_id == model
        assert agent_config.EFFORT == "low"
        assert made.lines_added == 11, "the meter is not the conversation"
        text = result.text()
        assert "4 turns" in text, text
        assert "No files were touched" in text, text

        alone = agent_commands.dispatch("/clear")
        assert not alone.ok and "No session" in alone.text()
    finally:
        box.close()


def test_effort_shows_the_level_and_sets_it():
    box = Settings()
    try:
        agent_config.refresh_effort()
        shown = agent_commands.dispatch("/effort")
        assert shown.ok
        for level in ("low", "medium", "high"):
            assert level in shown.text(), level
        assert "/effort [low|medium|high]" in shown.text()

        for level in ("low", "medium", "high"):
            result = agent_commands.dispatch("/effort " + level)
            assert result.ok, result.text()
            assert agent_config.EFFORT == level
            assert agent_config.EFFORT_FILE.read_text(encoding="utf-8").strip() == level
            # It reaches the two things it is supposed to reach.
            assert str(agent_config.max_tokens_for_effort()) in result.text()
            assert str(agent_config.rounds_for_effort()) in result.text()

        # Case-insensitive, like the name.
        assert agent_commands.dispatch("/effort HIGH").ok
        assert agent_config.EFFORT == "high"
    finally:
        box.close()


def test_effort_refuses_anything_that_is_not_a_level():
    box = Settings()
    try:
        agent_config.set_effort("medium")
        for bad in ("maximum", "9", "hi", "low medium", ""):
            result = agent_commands.dispatch("/effort " + bad) if bad else None
            if result is None:
                continue
            assert not result.ok, bad
            assert "low, medium, high" in result.text(), result.text()
            assert agent_config.EFFORT == "medium", "a refusal changes nothing"
        assert agent_config.EFFORT_FILE.read_text(encoding="utf-8").strip() == "medium"
    finally:
        box.close()


def test_model_shows_the_model_and_changes_it():
    box = Settings()
    try:
        shown = agent_commands.dispatch("/model", session())
        assert shown.ok
        assert "/model [<model id or name>]" in shown.text()
        for entry in agent_models.FREE_MODELS:
            assert entry["id"] in shown.text(), entry["id"]

        chosen = agent_models.FREE_MODELS[2]
        result = agent_commands.dispatch("/model " + chosen["id"], session())
        assert result.ok, result.text()
        assert agent_models.current_model("openrouter") == chosen["id"]
        assert chosen["id"] in result.text()

        # By the name the menu shows, too, and case-insensitively.
        other = agent_models.FREE_MODELS[1]
        assert agent_commands.dispatch("/model " + other["label"].upper()).ok
        assert agent_models.current_model("openrouter") == other["id"]
    finally:
        box.close()


def test_model_refuses_one_it_does_not_offer_and_changes_nothing():
    box = Settings()
    try:
        chosen = agent_models.FREE_MODELS[0]
        agent_models.set_model(chosen["id"], "openrouter")
        result = agent_commands.dispatch("/model not-a-real-model")
        assert not result.ok
        assert "not-a-real-model" in result.text()
        assert agent_models.current_model("openrouter") == chosen["id"]
    finally:
        box.close()


# --- errors -----------------------------------------------------------------

def test_an_unknown_command_says_so_and_names_the_real_ones():
    result = agent_commands.dispatch("/bogus")
    assert not result.ok
    text = result.text()
    assert "no /bogus command" in text, text
    for name in agent_commands.names():
        assert "/" + name in text, name


def test_a_command_given_an_argument_it_does_not_take_says_so():
    for line in ("/context extra", "/config now", "/clear everything"):
        result = agent_commands.dispatch(line)
        assert not result.ok, line
        assert "takes no argument" in result.text(), line
        assert "Usage:" in result.text(), line


# --- the two properties everything else rests on ----------------------------

def test_a_slash_command_never_reaches_the_model():
    """Checked at the fork itself: `dispatch` answers, so the loop continues
    and no request is ever built."""
    for line in ("/context", "/config", "/clear", "/effort", "/effort high",
                 "/model", "/MODEL x", "/bogus", "/context extra"):
        assert agent_commands.dispatch(line, session()) is not None, line


def test_an_ordinary_task_is_never_answered_here():
    """The other half, and the one that would be worse to get wrong: a task
    swallowed by this module is a task the model never sees."""
    for line in ("fix the authentication bug", "add a percent operator",
                 "run the tests", "what does Calc.py do?",
                 "/usr/bin/python is broken", "use the /api endpoint",
                 "/", "/ hello", "", "   "):
        assert agent_commands.dispatch(line, session()) is None, line


# --- secrets ----------------------------------------------------------------

def test_no_command_ever_prints_a_credential():
    """`/config` and `/context` are the two places a key would be most
    convenient to show and the two places it would be most damaging to: they
    are read over shoulders and pasted into bug reports. `/config` says
    whether a key is set and nothing else about it -- not the value, and not
    a masked form of the value either."""
    import agent_credentials
    fake = "sk-or-v1-0123456789abcdef0123456789abcdef0123456789abcdef"
    previous = agent_credentials.credential
    try:
        agent_credentials.credential = lambda provider_id=None: fake
        for line in ("/context", "/config", "/clear", "/effort", "/model"):
            text = agent_commands.dispatch(line, session(turns=2)).text()
            assert fake not in text, line
            for fragment in (fake[:12], fake[-12:], fake[8:24]):
                assert fragment not in text, (line, fragment)
            assert "sk-or" not in text, line
        # It does say whether one is configured, which is the useful part.
        assert "API key  set" in agent_commands.dispatch("/config").text()
        agent_credentials.credential = lambda provider_id=None: ""
        assert "API key  not set" in agent_commands.dispatch("/config").text()
    finally:
        agent_credentials.credential = previous


def test_no_command_reads_a_secret_file_or_the_raw_key_attribute():
    """Belt and braces on the module itself: the names that return a whole
    key must not appear in it at all."""
    source = Path(agent_commands.__file__).read_text(encoding="utf-8")
    for forbidden in ("OPENROUTER_API_KEY", "read_saved_key", "KEY_FILE",
                      "_decode", "_resolve_key", "masked("):
        assert forbidden not in source, forbidden
    # `credential` appears exactly once, and only to ask whether one exists.
    assert source.count("credential(") == 1, source.count("credential(")


# --- how it is drawn --------------------------------------------------------

def test_a_result_is_drawn_into_scrollback_and_fits_the_terminal():
    """Permanent, like every other thing that outlives the turn it happened
    in, and measured to the spare column like every other row."""
    import agent_menu
    import agent_ui
    for columns in (40, 60, 80, 120, 200):
        screen = Terminal()
        drawn = agent_menu.render_command(
            agent_commands.dispatch("/config", session(turns=2)),
            screen, size=(columns, 30))
        assert isinstance(drawn, int) and drawn > 0
        rows = screen.getvalue().split("\n")
        assert len(rows) == drawn + 1, (len(rows), drawn)
        for row in rows:
            assert agent_ui.visible_width(row) <= columns - 1, (columns, row)
        # It reads with the colour stripped, which is the rule for everything.
        plain = visible(screen.getvalue())
        assert "Configuration" in plain and "Effort" in plain, plain

    # A refusal says so in words as well as in colour.
    screen = Terminal()
    agent_menu.render_command(agent_commands.dispatch("/bogus"), screen,
                              size=(80, 30))
    assert "Unknown command" in visible(screen.getvalue())


def test_a_command_row_is_never_mistaken_for_a_prompt():
    """Anything reading the screen back counts a prompt by its ' > ' marker.
    A row offered under the line being typed must not wear one."""
    import agent_menu
    screen = Terminal()
    box = agent_menu.PromptBox(stream=screen,
                               completer=agent_commands.completions,
                               completed=agent_commands.completed)
    editor = agent_menu.LineEditor()
    editor.insert("/")
    rows = [visible(row) for row in box.lines(editor, size=(80, 24))]
    marker_rows = [row for row in rows if row.startswith(" > ")]
    assert len(marker_rows) == 1, marker_rows
    assert marker_rows[0].strip() == "> /", marker_rows
    for name in agent_commands.names():
        assert any("/" + name in row for row in rows), name


def test_the_caret_counts_the_rows_offered_under_the_line():
    """The commands sit between the line being typed and the bottom rule, so
    the caret is further from the foot of the frame than usual. `_frame`
    reports that distance and `_place` moves by it; a caret that assumed two
    would land on a suggestion row instead of on the line."""
    import agent_menu
    box = agent_menu.PromptBox(stream=Terminal(),
                               completer=agent_commands.completions,
                               completed=agent_commands.completed)

    def frame_for(typed):
        editor = agent_menu.LineEditor()
        if typed:
            editor.insert(typed)
        return box._frame(editor, size=(80, 24))

    rows, column, up = frame_for("fix the bug")
    assert len(rows) == 4 and up == agent_menu._INPUT_ROW, (len(rows), up)
    assert rows[-up].strip().startswith("> fix"), rows

    for typed, offered in (("/mo", 1), ("/c", 3), ("/", 9)):
        rows, column, up = frame_for(typed)
        assert len(rows) == 4 + offered, (typed, len(rows))
        assert up == agent_menu._INPUT_ROW + offered, (typed, up)
        # Counted from the bottom, `up` lands on the line being typed.
        assert visible(rows[-up]).strip() == "> " + typed, (typed, rows[-up])
        assert column == agent_menu._PROMPT_PREFIX + len(typed), (typed, column)


def test_the_effort_stored_on_disk_is_read_back_at_startup():
    """It is written beside the model choice and would otherwise never be
    read: /effort would last a session and quietly revert on the next
    launch, which is the failure the .tmt_model pattern exists to avoid."""
    box = Settings()
    try:
        agent_config.set_effort("high")
        agent_config.EFFORT = "medium"          # as a fresh import would have it
        assert agent_config.refresh_effort() == "high"
        assert agent_config.EFFORT == "high"

        # An unreadable or hand-edited file is the default, not a crash.
        agent_config.EFFORT_FILE.write_text("enormous\n", encoding="utf-8")
        assert agent_config.refresh_effort() == agent_config.DEFAULT_EFFORT
        agent_config.EFFORT_FILE.unlink()
        assert agent_config.refresh_effort() == agent_config.DEFAULT_EFFORT
    finally:
        box.close()

    # And the loop actually calls it, next to the model's own refresh.
    source = Path(TMT_SOURCE).read_text(encoding="utf-8")
    assert "agent_config.refresh_effort()" in source, "startup never reads it"


TMT_SOURCE = Path(agent_commands.__file__).resolve().parent / "TMT.py"
