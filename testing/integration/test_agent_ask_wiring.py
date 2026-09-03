"""`ask_user` as the session actually reaches it: the dispatcher, and the terminal.

`test_agent_ask` drives the shape. This drives the halves that only exist once
it is wired in -- the branch in `execute_action`, the `choose` callable the
session builds, the refusal every background agent gets, and the fact that a
turn CARRIES ON after the answer rather than ending on it.

The terminal half is driven against a fake `msvcrt`, not through an injected
reader. That distinction is the whole reason this file exists: an injected
reader goes straight into the code under test and never reaches
`agent_menu.read_key`, which is where Ctrl-C, Esc and the digit are actually
told apart. `output/drive_key_screen.py` was written for the same reason after
a paste bug survived every test that used an injected reader.

What is pinned, and why each is worth a test of its own:

- the turn CARRIES ON. `end_conversation` is the only verb that ends one, and a
  model that had to end a turn to ask a question would throw away everything it
  had read to get there;
- there is nobody to ask in a piped run, and the model is told exactly that
  rather than being told a person declined -- and it is never blocked on a
  keystroke that will not arrive;
- every background agent is refused, because nobody is watching a worker and
  its question would be drawn nowhere;
- Ctrl-C at the question stops the turn. msvcrt hands it back as an ordinary
  character and raises no signal, so a raw read that did not turn it back into
  the exception would swallow the one gesture that stops a running turn;
- the question is written through `write_above` and never printed, because
  printing past a live region leaves its repaint arithmetic pointing at rows
  that have moved -- the defect that put ten stray box tops on a real terminal.
"""

import io
import json
import re
import sys
from pathlib import Path

import agent_actions
import agent_ask as A
import agent_config
import agent_menu
import agent_prompt
import agent_worker
import TMT

from test_agent_cli import drive_session
from test_agent_workspace import Workspace


REQUEST = {"action": "ask_user",
           "question": "What should the database layer use?",
           "options": ["use nodejs", "use standard python", "something else"]}


class Keyboard:
    """A fake `msvcrt`, so the real `read_key` is what reads the keys.

    An injected reader would go straight into `choose` and never reach the
    layer that tells a digit from Esc from Ctrl-C, which is the layer this
    file is about.
    """

    def __init__(self, keys):
        self.keys = list(keys)
        self.__name__ = "msvcrt"

    def kbhit(self):
        # Always False, so `_drain_burst` finds nothing waiting and hands back
        # exactly one character. A real console reports True while a run is
        # still arriving, which is how a paste is recognised -- and coalescing
        # is the one behaviour a one-key question must not get.
        return False

    def getwch(self):
        if not self.keys:
            # Loudly, rather than returning a filler character forever: the
            # loop under test waits for a key it recognises, so a fake that
            # kept answering would hang the suite instead of failing it.
            raise AssertionError("read past the end of the scripted keys")
        return self.keys.pop(0)

    getch = getwch


class Relay:
    """A live region that records what was written above it."""

    def __init__(self):
        self.above = []

    def write_above(self, text):
        self.above.append(text)


class Pad:
    def __init__(self):
        self.spent = []

    def spend(self, text):
        self.spent.append(text)

    def take(self, rows):
        return rows


class Box:
    typeahead = None


def asker(keys, interactive=True, relay=None):
    """The real `_question_asker`, with the keyboard and the tty gate faked."""
    panel = {"relay": relay}
    choose = TMT._question_asker(Box(), panel, Pad())
    saved = (agent_menu._key_backend, agent_menu.is_interactive)
    # ONE keyboard, handed back every time. `read_key` asks for the backend on
    # every call, so a lambda that CONSTRUCTED one would re-serve the first
    # key for ever -- and the loop under test waits for a key it recognises,
    # so that is a hang rather than a failure.
    keyboard = Keyboard(keys)
    agent_menu._key_backend = lambda: keyboard
    agent_menu.is_interactive = lambda *a, **k: interactive
    # Anything another test left queued behind a paste would be answered
    # before the console is asked for anything new, which would make this
    # test read a key it never scripted.
    del agent_menu._pending_keys[:]
    return choose, saved


def restore(saved):
    agent_menu._key_backend, agent_menu.is_interactive = saved
    del agent_menu._pending_keys[:]


# --- the dispatcher ----------------------------------------------------------

def test_the_verb_is_registered_everywhere_a_verb_has_to_be():
    assert agent_config.REQUIRED_KEYS["ask_user"] == ["question", "options"]
    assert agent_actions.ACTION_LABELS["ask_user"] == "Ask User"
    assert agent_prompt.validate_action(dict(REQUEST)) is None
    assert "missing required keys" in agent_prompt.validate_action(
        {"action": "ask_user"})
    text = (Path(agent_config.__file__).resolve().parent
            / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^\s*"agent_ask",\s*$', text, re.M), \
        "agent_ask is missing from pyproject.toml's py-modules"


def test_the_answer_comes_back_through_execute_action():
    asked = []

    def choose(text, keys):
        asked.append((text, keys))
        return "2"

    said = agent_actions.execute_action(dict(REQUEST), {"choose": choose})
    assert "use standard python" in said, said
    assert asked and asked[0][1] == ("1", "2", "3"), asked
    assert "1. use nodejs" in asked[0][0], asked[0][0]


def test_a_run_with_nobody_watching_is_told_so_and_is_never_blocked():
    """A context with no `choose` is the honest state of a pipe, a script, the
    suite and every background agent. The model is told to decide for itself,
    because one left with no answer and no instruction asks again."""
    for context in ({}, {"choose": None}, {"choose": "not callable"}):
        said = agent_actions.execute_action(dict(REQUEST), context)
        assert said == A.NO_TERMINAL, said


def test_a_terminal_that_fails_mid_question_has_not_chosen_anything():
    def explode(text, keys):
        raise RuntimeError("the console went away")

    said = agent_actions.execute_action(dict(REQUEST), {"choose": explode})
    assert said == A.DISMISSED, said


def test_a_ctrl_c_at_the_question_is_not_swallowed_by_the_action():
    """It means the user wants the turn to stop, and the loop already knows
    how to end one. Catching it here would answer the question with a
    keystroke that meant the opposite."""
    def interrupt(text, keys):
        raise KeyboardInterrupt

    try:
        agent_actions.execute_action(dict(REQUEST), {"choose": interrupt})
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("Ctrl-C must reach the session loop")


def test_a_malformed_request_is_refused_without_anybody_being_asked():
    asked = []
    said = agent_actions.execute_action(
        {"action": "ask_user", "question": "q", "options": ["only one"]},
        {"choose": lambda text, keys: asked.append(keys) or "1"})
    assert said.startswith("REFUSED:"), said
    assert asked == [], "nobody should have been asked a question that is not one"


# --- the terminal half -------------------------------------------------------

def test_a_digit_is_read_by_the_real_key_reader():
    choose, saved = asker(["2"])
    try:
        assert choose("Which?", ("1", "2", "3")) == "2"
    finally:
        restore(saved)


def test_anything_that_is_not_an_offered_key_leaves_the_question_up():
    """A mistyped letter must not be read as a choice, and must not count as a
    dismissal either -- the question simply stays there."""
    choose, saved = asker(["a", "z", "9", "4", "3"])
    try:
        assert choose("Which?", ("1", "2", "3")) == "3"
    finally:
        restore(saved)


def test_esc_dismisses_and_the_end_of_input_does_not_mean_the_same_thing():
    """Both let go of the session; they are different facts about why."""
    choose, saved = asker(["\x1b"])
    try:
        assert choose("Which?", ("1", "2")) == ""
    finally:
        restore(saved)
    choose, saved = asker([], interactive=False)
    try:
        assert choose("Which?", ("1", "2")) is None
    finally:
        restore(saved)


def test_ctrl_c_at_the_question_becomes_the_exception_the_loop_ends_turns_on():
    """msvcrt hands Ctrl-C back as an ordinary character and raises no signal,
    so a raw read that did not turn it back into the exception would swallow
    the one gesture that stops a running turn."""
    choose, saved = asker(["\x03"])
    try:
        choose("Which?", ("1", "2"))
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("Ctrl-C must raise out of the question")
    finally:
        restore(saved)


def test_the_question_goes_through_write_above_and_is_never_printed():
    """Printing past a live region leaves its repaint arithmetic pointing at
    rows that have moved, which is what put ten stray box tops on a real
    terminal when the deletions asked with a bare input()."""
    relay = Relay()
    screen = io.StringIO()
    choose, saved = asker(["1"], relay=relay)
    stdout = sys.stdout
    try:
        sys.stdout = screen
        assert choose("Which one?", ("1", "2")) == "1"
    finally:
        sys.stdout = stdout
        restore(saved)
    assert relay.above == ["Which one?"], relay.above
    assert screen.getvalue() == "", screen.getvalue()


def test_the_type_ahead_reader_is_stopped_for_the_length_of_the_question():
    """Two readers on one stdin take it in turns to swallow the user's
    characters, so the one that reads for the whole turn has to let go."""
    events = []

    class Typed:
        active = True

        def stop(self):
            events.append("stop")
            return True

        def start(self):
            events.append("start")

    box = Box()
    box.typeahead = Typed()
    choose = TMT._question_asker(box, {"relay": Relay()}, Pad())
    saved = (agent_menu._key_backend, agent_menu.is_interactive)
    _kb = Keyboard(["1"])
    agent_menu._key_backend = lambda: _kb
    agent_menu.is_interactive = lambda *a, **k: True
    try:
        assert choose("Which?", ("1", "2")) == "1"
    finally:
        restore(saved)
    assert events == ["stop", "start"], events


def test_the_reader_is_started_again_even_when_the_question_is_interrupted():
    events = []

    class Typed:
        active = True

        def stop(self):
            events.append("stop")
            return True

        def start(self):
            events.append("start")

    box = Box()
    box.typeahead = Typed()
    choose = TMT._question_asker(box, {"relay": Relay()}, Pad())
    saved = (agent_menu._key_backend, agent_menu.is_interactive)
    _kb = Keyboard(["\x03"])
    agent_menu._key_backend = lambda: _kb
    agent_menu.is_interactive = lambda *a, **k: True
    try:
        choose("Which?", ("1", "2"))
    except KeyboardInterrupt:
        pass
    finally:
        restore(saved)
    assert events == ["stop", "start"], events


# --- isolation ---------------------------------------------------------------

def test_no_background_agent_can_ask_a_question():
    """Nobody is watching a worker, so its question would be drawn nowhere and
    answered by nobody. Two-sided, as every isolation here is: refused by the
    loop, and absent from the prompts those agents are given."""
    assert "ask_user" in agent_worker.WORKER_NEEDS_TERMINAL
    assert "ask_user" not in agent_worker.NOTE_ACTIONS
    assert "ask_user" not in agent_worker.REVIEW_ACTIONS
    import agent_delegation
    assert "ask_user" not in agent_delegation.READ_ONLY_ACTIONS
    import agent_subprompts
    box = Workspace()
    try:
        box.use()
        agent_prompt.invalidate_prompt()
        for build in (agent_subprompts.worker_prompt,
                      agent_subprompts.note_prompt,
                      agent_subprompts.review_prompt):
            try:
                text = build("do the thing")
            except TypeError:
                text = build()
            assert "ask_user" not in text, build.__name__
        # And the main agent IS taught it, in the same breath.
        assert "ask_user" in agent_prompt.get_system_prompt()
    finally:
        box.close()


def test_asking_changes_nothing_in_the_workspace():
    """It reads a keystroke. A verb in MUTATING_ACTIONS would make a passed
    review and a passed verification stale, and would take a checkpoint of the
    whole workspace, for a question."""
    assert "ask_user" not in agent_config.MUTATING_ACTIONS
    assert not TMT.mutated("ask_user", dict(REQUEST))
    import agent_checkpoint
    assert not agent_checkpoint.will_mutate("ask_user", dict(REQUEST))


def test_asking_does_not_nudge_the_model_towards_answering():
    """`agent_actions.READ_ONLY_ACTIONS` appends "now output an
    end_conversation" to a result. The loop this verb belongs to ends in the
    WORK, not in an answer -- a model told to answer straight after being
    given a decision would report the decision instead of acting on it."""
    assert "ask_user" not in agent_actions.READ_ONLY_ACTIONS
    said = agent_actions.build_result_message(
        "ask_user", "The user chose 1: \"use nodejs\".", dict(REQUEST))
    assert "end_conversation" not in said, said


# --- a whole turn ------------------------------------------------------------

def test_a_turn_carries_on_after_the_question_rather_than_ending_on_it():
    """The whole point. `end_conversation` is the only verb that ends a turn,
    and a model that had to end one to ask would throw away everything it had
    read to get there."""
    screen, seen, _ = drive_session(
        ["set up the database layer", "quit"],
        [json.dumps(dict(REQUEST, progress="Asking which stack to use.")),
         json.dumps({"action": "write_file", "path": "db.py",
                     "content": "import sqlite3\n",
                     "progress": "Writing it the way you chose."}),
         json.dumps({"action": "end_conversation",
                     "message": "Wrote db.py with the standard library."})])
    # Three requests: the question, the write, the answer. A turn that ended
    # on the question would have made one.
    assert len(seen) == 3, len(seen)
    # And the answer to the question was handed back to the model.
    handed = seen[1][-1]["content"]
    assert "nobody to ask" in handed or "There is nobody" in handed, handed
    assert "Wrote db.py" in screen


def test_a_piped_run_is_told_nobody_was_there_rather_than_being_told_no():
    """`drive_session` is a pipe, so this is the real path a scripted TMT
    takes -- and the model must not conclude a person declined."""
    _, seen, _ = drive_session(
        ["set up the database", "quit"],
        [json.dumps(dict(REQUEST, progress="Asking.")),
         json.dumps({"action": "end_conversation", "message": "Chose python."})])
    handed = seen[1][-1]["content"]
    assert A.NO_TERMINAL.split(".")[0] in handed, handed
    assert "dismissed" not in handed, handed


def test_the_transcript_row_says_what_was_asked():
    """Without it the fortieth question in a session is the same row as the
    first -- the gap `glob`'s `pattern` and `web_fetch`'s `url` both had."""
    event = agent_actions.action_event(
        "ask_user", dict(REQUEST), 'The user chose 1: "use nodejs".')
    assert "What should the database layer use?" in event.message, event.message
