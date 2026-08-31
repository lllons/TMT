"""The seams between the multi-agent modules and the session that owns them.

Everything here is a defect that survived its own module's tests because it
only appears where two modules meet: a refusal that reads correctly but names
the wrong reason, an answer that fits its Result and not the terminal, a
warning printed by the interpreter rather than by any code that could be
audited for printing. Each one was found by driving TMT by hand, and each is
pinned here so it cannot come back quietly.
"""

import io
import sys
import tempfile
import warnings
from pathlib import Path

import agent_commands
import agent_config
import agent_manager
import agent_menu
import agent_symbols
import agent_worker


class Workspace:
    """A throwaway workspace, with agent_config pointed at it."""

    def __init__(self, files=None):
        self.previous = agent_config.ROOT_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_integration_")).resolve()
        agent_config.ROOT_DIR = self.path
        for name, body in (files or {}).items():
            (self.path / name).write_text(body, encoding="utf-8")

    def close(self):
        agent_config.ROOT_DIR = self.previous


# --- a background agent has no terminal to be confirmed at -------------------

def test_deleting_is_refused_to_a_background_agent_for_the_real_reason():
    """`delete_file` and `delete_folder` call a bare blocking `input()`. From a
    worker thread that is worse than a stray print: it competes with the
    session's own reader for stdin and then blocks forever on a prompt nobody
    can see, and this suite has no per-test timeout, so a test that reached one
    would hang the whole run rather than fail.

    The refusal has to name that reason. Told only that it was "not a worker
    verb", a model would reasonably look for another route to the same effect;
    told the confirmation cannot happen, it reports the path instead."""
    for action in ("delete_file", "delete_folder"):
        worker = agent_worker._refusal(action, None, agent_worker.WORKER_FORBIDDEN)
        note = agent_worker._refusal(action, agent_worker.NOTE_ACTIONS,
                                     agent_worker.WORKER_FORBIDDEN)
        for refusal in (worker, note):
            assert refusal, action
            assert "no terminal to be asked at" in refusal, (action, refusal)
            # And it says what to do instead, rather than only saying no.
            assert "internal_response" in refusal, (action, refusal)

    # It is checked AHEAD of the other two sets, or the sentence would name
    # whichever of them happened to match first. Matched on the whole phrase
    # rather than on the word "terminal", which the ordinary worker refusal
    # also carries -- "your only terminal verb is internal_response" -- and
    # which therefore proves nothing about which branch answered.
    push = agent_worker._refusal("git_push", None, agent_worker.WORKER_FORBIDDEN)
    assert "no terminal to be asked at" not in push, push
    assert "terminal verb is internal_response" in push, push


def test_neither_delete_verb_is_offered_to_the_note_agent():
    """The note agent's whitelist is the enforcement. A blacklist would admit
    every action added after it was written; this asserts the whitelist never
    quietly grows one of these."""
    assert "delete_file" not in agent_worker.NOTE_ACTIONS
    assert "delete_folder" not in agent_worker.NOTE_ACTIONS


def test_an_ordinary_write_is_still_a_worker_verb():
    """The refusal above must not have widened into "workers cannot edit".
    Editing is the whole point of a worker; only the two actions that need a
    human at a keyboard are out."""
    assert agent_worker._refusal("write_file", None,
                                 agent_worker.WORKER_FORBIDDEN) == ""
    assert agent_worker._refusal("patch_file", None,
                                 agent_worker.WORKER_FORBIDDEN) == ""


# --- an answer has to fit the terminal, not just the Result ------------------

def test_a_note_answer_is_wrapped_on_words_rather_than_truncated():
    """The defect this exists for, found by running `/note` for real.

    `render_command` fits every row with `fit_to_width`, which TRUNCATES. That
    is right for the settled facts every other command returns -- a short label
    and a short value -- and wrong for a paragraph: the one command whose whole
    output is prose was the one command that could not show it, and the answer
    stopped mid-sentence at the right-hand edge.

    Wrapping it on the column instead split "1844" across two rows and cut
    "clear_screen" in half, so the break has to prefer a word boundary."""
    answer = ("The prompt box is the PromptBox class in agent_menu.py, defined "
              "at line 1844, and it is imported by TMT.py alongside BottomPad, "
              "clear_screen, opening_pad and render_command.")
    rows = agent_commands._wrapped_rows(answer, columns=80)

    # Nothing was lost: every word of the answer survives the wrapping.
    assert " ".join(" ".join(rows).split()) == " ".join(answer.split())
    # Every row fits inside what render_command will draw it in.
    for row in rows:
        assert agent_menu.display_width(row) <= 80 - 6, (len(row), row)
    # And it really did wrap rather than returning one long row.
    assert len(rows) > 1, rows
    # The two tokens the column-wrapper broke are each whole on some row.
    joined = "\n".join(rows)
    assert "1844" in joined and "clear_screen" in joined, joined
    for token in ("1844", "clear_screen"):
        assert any(token in row for row in rows), (token, rows)


def test_a_blank_line_in_an_answer_survives_as_a_blank_row():
    """The agent's own paragraph breaks are its own. Collapsing them would
    reflow an answer it deliberately shaped."""
    rows = agent_commands._wrapped_rows("first\n\nsecond", columns=80)
    assert rows == ["first", "", "second"], rows


def test_a_word_too_long_for_the_row_still_cannot_overflow():
    """A path or a token wider than the terminal falls back to the measured
    clipper, because a row that overflows costs a screen line the repaint
    arithmetic does not know about."""
    rows = agent_commands._wrapped_rows("x" * 200, columns=60)
    for row in rows:
        assert agent_menu.display_width(row) <= 60 - 6, row


# --- the interpreter must not comment on files TMT was asked to read ---------

def test_reading_a_file_does_not_print_a_warning_about_it():
    """`ast.parse` raises SyntaxWarning for things like an invalid escape in a
    string literal, and Python's warning machinery writes STRAIGHT TO STDERR,
    past everything TMT knows about what is on screen.

    It became serious when background agents arrived: a worker runs on its own
    thread, and anything printed from there lands on top of the live region and
    corrupts the repaint arithmetic. The worker never printed -- the
    interpreter printed on its behalf."""
    box = Workspace({"warns.py": 'BAD = "\\[not a real escape"\n'})
    captured = io.StringIO()
    saved = sys.stderr
    try:
        sys.stderr = captured
        with warnings.catch_warnings():
            # Forced on, so a run that happens to have them disabled globally
            # cannot pass this vacuously.
            warnings.simplefilter("always")
            result = agent_symbols.scan_file("warns.py")
    finally:
        sys.stderr = saved
        box.close()

    assert captured.getvalue() == "", captured.getvalue()
    # And the file was genuinely parsed rather than skipped to stay quiet.
    assert result["tier"] == agent_symbols.TIER_STRUCTURAL, result
    assert result["error"] == "", result


def test_a_file_that_truly_will_not_parse_is_still_reported_as_unparsed():
    """Suppressing warnings must not have suppressed errors with them. A real
    SyntaxError still raises, is still caught, and still falls back to the
    lexical reader -- which is the honest half-answer the tier system exists
    to label."""
    box = Workspace({"broken.py": "def (:\n"})
    try:
        result = agent_symbols.scan_file("broken.py")
    finally:
        box.close()
    assert result["error"].startswith("unparsed:"), result
    assert result["tier"] == agent_symbols.TIER_HEURISTIC, result


# --- /agents, and how the register reaches the commands that need it ---------

def test_agents_is_a_command_and_answers_without_a_register():
    """The unambiguous way in. The panel opens on Right Arrow at the end of the
    line, which is a gesture with nowhere to open into on a terminal too narrow
    for two columns -- and a gesture nobody has been told about is not a way in
    at all."""
    assert "agents" in agent_commands.names()
    assert agent_commands.SUMMARY.get("agents")
    assert agent_commands.USAGE.get("agents", "").startswith("/agents")

    # No register at all is the honest state of an install where background
    # agents were never wired in, and it answers rather than raising. It says
    # "unavailable" rather than "none are running", because those are
    # different facts and reporting the second for the first would tell a user
    # their agents had finished when they could never have started.
    result = agent_commands.dispatch("/agents")
    assert result is not None and result.ok, result
    assert "unavailable" in result.text(), result.text()

    # A register with nothing in it is the other fact, and says so.
    empty = agent_commands._agents("", None, agent_manager.AgentManager())
    assert "No background agents are running" in empty.text(), empty.text()


def test_agents_reports_the_register_it_is_given():
    manager = agent_manager.AgentManager()
    record = manager.spawn("count the modules")
    manager.set_activity(record.id, "Reading the tree")

    result = agent_commands._agents("", None, manager)
    text = result.text()
    assert record.id in text, text
    # It reads with the colour stripped, because colour is never the message.
    assert "Reading the tree" in text, text


def test_the_session_loop_hands_the_register_to_the_commands_that_need_it():
    """`dispatch` takes a session and no more, which is right for the five
    commands that only read settings. `/note` and `/agents` need the session's
    OWN register rather than a private one, or the note they start is invisible
    to the panel and to `/agents`."""
    import TMT

    seen = {}

    def watching(question, session=None, manager=None, timeout=None):
        seen["question"], seen["manager"] = question, manager
        return agent_commands.Result("Note", ["answered"])

    manager = agent_manager.AgentManager()
    saved = agent_commands.run_note
    try:
        agent_commands.run_note = watching
        answered = TMT._dispatch_command("/note where is the retry limit",
                                         None, manager)
    finally:
        agent_commands.run_note = saved

    assert answered is not None
    assert seen["question"] == "where is the retry limit", seen
    assert seen["manager"] is manager, seen

    # /agents gets it too, by the same route. Asserted on the register's own
    # CONTENT rather than on the call succeeding: routed the wrong way it
    # still returns a perfectly valid Result, just one built from no register
    # at all, so `ok` proves nothing about which path answered.
    running = manager.spawn("count the modules")
    result = TMT._dispatch_command("/agents", None, manager)
    assert result is not None and result.ok, result
    assert running.id in result.text(), result.text()
    assert "unavailable" not in result.text(), result.text()

    # And an ordinary task is still not a command, so it reaches the model
    # exactly as it always did -- including one that merely starts with a path.
    assert TMT._dispatch_command("/usr/bin/python is broken", None, manager) is None
    assert TMT._dispatch_command("add a subtract function", None, manager) is None


def test_a_bare_note_asks_for_its_question_rather_than_guessing_one():
    """The inline form is the one that works everywhere -- the piped reader
    takes one task per line, so a two-stage prompt is unreachable from a pipe
    or from this suite. Bare `/note` only sets the flag the session loop reads;
    it must never invent a question."""
    result = agent_commands.dispatch("/note")
    assert result is not None
    assert result.prompt_for == "note", result.prompt_for
    # Every other command leaves the flag alone, so nothing else can trigger a
    # second prompt by accident.
    for command in ("/config", "/context", "/effort", "/agents"):
        other = agent_commands.dispatch(command)
        assert other.prompt_for == "", (command, other.prompt_for)
