"""Tests for the session context: what one question carries from the last.

The claim under all of these is narrow and checkable. A second question must
arrive at the provider with the first question and its answer in front of it,
in TMT's own message shape, bounded by the model's real window -- and none of
it may survive the process.
"""

import json
import os
import tempfile
from pathlib import Path

import agent_actions
import agent_models
import agent_session
import agent_ui
from agent_session import Session, Turn, estimate_tokens, files_touched


def event(kind, message="", **detail):
    return agent_ui.AgentEvent.make(kind, message, **detail)


def answered(session, task, answer, history=None):
    """Ask and answer one turn, the way the agent loop does."""
    messages, pinned = session.begin_turn(task, "SYSTEM")
    session.record(task, answer, history)
    return messages, pinned


# --- the thing the feature exists for ---------------------------------------

def test_a_second_question_carries_the_first_and_its_answer():
    """The whole point, stated as the user states it. "Now add percentage
    support" is a sentence that means nothing on its own; it means what it
    means because of the turn before it, and that turn has to be in the
    request or the model is answering a fragment."""
    session = Session(workspace="C:\\project")
    answered(session, "Create a calculator using the existing Calc.py architecture.",
             "Created the calculator in Calc.py.")

    messages, pinned = session.begin_turn("Now add percentage support.", "SYSTEM")
    roles = [message["role"] for message in messages]
    assert roles == ["system", "user", "assistant", "user"], roles
    assert messages[0]["content"] == "SYSTEM"
    assert "existing Calc.py architecture" in messages[1]["content"]
    assert "Created the calculator" in messages[2]["content"]
    assert messages[-1]["content"] == "Now add percentage support."
    # Everything up to and including the new task is fixed for this turn.
    assert pinned == len(messages) == 4, (pinned, messages)


def test_a_third_question_still_reaches_back_past_the_second():
    """Two turns of history, not one. A context that only ever remembered the
    previous turn would lose the decision that set the whole session up."""
    session = Session(workspace="C:\\project")
    answered(session, "Use the Calc.py architecture.", "Noted.")
    answered(session, "Add percentages.", "Percentages added.")

    messages = session.begin_turn("Now add square roots.", "SYSTEM")[0]
    joined = "\n".join(message["content"] for message in messages)
    assert "Calc.py architecture" in joined, joined
    assert "Percentages added" in joined, joined
    assert messages[-1]["content"] == "Now add square roots."


def test_the_files_a_turn_changed_are_carried_with_its_answer():
    """"Now add percentage support" is a sentence about a file, and the file
    is the part the answer most often leaves out. It is read off the events
    the turn actually emitted, so it is measured rather than described."""
    session = Session(workspace="C:\\project")
    history = [
        event("progress", "Writing the change."),
        event("file_create", "Wrote Calc.py", paths=("Calc.py",), lines=40),
        event("file_edit", "Patched tests/test_calc.py",
              paths=("tests/test_calc.py",), added=8, removed=1),
        event("file_read", "Read setup.py", paths=("setup.py",)),
    ]
    session.record("Build the calculator.", "Done.", history)

    carried = session.begin_turn("Now add percentages.", "SYSTEM")[0]
    assistant = carried[2]["content"]
    assert "Calc.py" in assistant, assistant
    assert "tests/test_calc.py" in assistant, assistant
    # A file that was only read was not changed, and saying it was would be
    # a claim about work that did not happen.
    assert "setup.py" not in assistant, assistant


def test_an_action_records_the_paths_it_named():
    """The path has to get onto the event before the session can read it off.
    Taken from the request, where a path is a fact, rather than parsed back
    out of a sentence."""
    made = agent_actions.action_event(
        "write_file", {"action": "write_file", "path": "Calc.py", "content": "x = 1\n"},
        "Wrote 1 line to Calc.py")
    assert made.detail.get("paths") == ("Calc.py",), made.detail

    batch = agent_actions.action_event(
        "write_files",
        {"action": "write_files",
         "files": [{"path": "a.py", "content": ""}, {"path": "b.py", "content": ""}]},
        "Wrote a.py\nWrote b.py")
    assert batch.detail.get("paths") == ("a.py", "b.py"), batch.detail

    # An action that names no path contributes none rather than an empty one.
    ran = agent_actions.action_event("git_status", {"action": "git_status"},
                                     "On branch main")
    assert "paths" not in ran.detail, ran.detail


# --- session only -----------------------------------------------------------

def test_the_conversation_never_reaches_the_disk():
    """It belongs to the run. A task abandoned on Tuesday must not turn up as
    context on Wednesday, and the way to be sure of that is that there is no
    code here that writes anything."""
    source = Path(agent_session.__file__).read_text(encoding="utf-8")
    # Anything that could reach a file. `json.dump(` writes to one; `json.dumps`
    # returns a string and is how a carried turn is put back into the shape the
    # model speaks, which never leaves memory.
    for forbidden in ("write_text", "open(", "json.dump(", "pickle", "shelve",
                      "Path(", "os.remove"):
        assert forbidden not in source, forbidden

    # And a fresh session in a fresh working directory starts with nothing,
    # whatever an earlier one had in it.
    box = tempfile.mkdtemp(prefix="tmt_session_")
    previous = os.getcwd()
    try:
        first = Session(workspace=box)
        first.record("Remember the architecture.", "Noted.")
        assert len(first) == 1
        os.chdir(box)
        assert len(Session(workspace=box)) == 0
        assert Session(workspace=box).carried_messages() == []
    finally:
        os.chdir(previous)


def test_a_new_session_starts_empty_and_clear_empties_one():
    session = Session(workspace="C:\\project")
    session.record("One.", "Done.")
    session.record("Two.", "Done.")
    assert len(session) == 2
    session.clear()
    assert len(session) == 0
    assert session.begin_turn("Three.", "SYSTEM")[0][-1]["content"] == "Three."


# --- what is not recorded ---------------------------------------------------

def test_a_turn_that_produced_no_answer_is_still_recorded_and_says_why():
    """The bug this replaced: it took an answer to be recorded at all, so a
    stream failure, a circuit break, an unreadable reply or a turn that ran
    out of steps dropped the user's QUESTION along with it. The next question
    then arrived with no sign the exchange had ever happened, which is exactly
    what "it has no context between prompts" looked like from outside.

    The reason is stated rather than glossed, and it is stated by TMT -- it
    rides on the question, not on an assistant message the model never sent."""
    session = Session(workspace="C:\\project")
    session.record("Translate the README.", "", outcome="the stream failed")
    assert len(session) == 1

    carried = session.carried_messages()
    assert len(carried) == 1, carried          # no answer, so no assistant turn
    assert carried[0]["role"] == "user"
    assert "Translate the README." in carried[0]["content"]
    assert "the stream failed" in carried[0]["content"], carried[0]
    assert "[That turn ended with no answer" in carried[0]["content"]

    # The next question still sees it, which is the whole point.
    messages = session.begin_turn("Now do the Japanese one.", "SYSTEM")[0]
    assert len(messages) == 3, messages
    assert messages[-1]["content"] == "Now do the Japanese one."

    # A question with nothing in it is still not a turn.
    assert session.record("", "An answer to nothing.") is None
    assert len(session) == 1


def test_the_carried_answer_is_the_json_action_the_model_speaks_in():
    """Every other assistant message in a request is a JSON object -- that is
    the whole of what the system prompt demands. Dropping the previous turn's
    answer in as loose prose put an example of the forbidden shape in front of
    the model, in its own voice, immediately before asking it not to use that
    shape. The words are the model's own either way; only the wrapper is
    restored."""
    session = Session(workspace="C:\\project")
    session.record("Build it.", "Built it in Calc.py.")
    carried = session.carried_messages()
    assert carried[0]["role"] == "user"
    assistant = carried[1]
    assert assistant["role"] == "assistant"
    payload = json.loads(assistant["content"])       # loose prose would raise
    assert payload["action"] == "respond", payload
    assert payload["message"].startswith("Built it in Calc.py."), payload


def test_a_turn_that_only_used_git_still_carries_what_it_did():
    """A commit and a push touch no file the path list would catch, so a
    git-only turn used to carry nothing but its answer text -- and when that
    text was a machine's error string it carried nothing true at all."""
    session = Session(workspace="C:\\project")
    session.record("push to main", "Pushed.", [
        event("milestone", "Committed 6f0a4f5 on main"),
        event("milestone", "Pushed main to origin (github.com)"),
        event("progress", "Checking the remote."),
    ])
    carried = json.loads(session.carried_messages()[1]["content"])["message"]
    assert "Committed 6f0a4f5 on main" in carried, carried
    assert "Pushed main to origin" in carried, carried
    assert "Checking the remote" not in carried, carried


def test_a_very_long_answer_is_cut_and_says_so():
    """One reply must not be able to crowd out every turn before it, and a cut
    that did not say it was a cut would carry a sentence forward with its
    meaning changed by where it stopped."""
    session = Session(workspace="C:\\project")
    session.record("Explain everything.", "word " * 2000)
    kept = session.turns[0].answer
    assert len(kept) < 2000 + 120, len(kept)
    assert "truncated" in kept, kept[-80:]


# --- the budget -------------------------------------------------------------

def test_the_budget_comes_from_the_active_models_real_window():
    """Not a number invented here. The catalogue entry for the model actually
    selected is where the provider's own figure lands."""
    session = Session(workspace="C:\\project")
    chosen = agent_models.FREE_MODELS[1]
    previous = os.environ.get("OPENROUTER_MODEL")
    try:
        os.environ["OPENROUTER_MODEL"] = chosen["id"]
        assert session.context_window() == chosen["context"], session.context_window()
        assert session.carry_budget() == int(chosen["context"] * agent_session.CONTEXT_SHARE)

        # A model TMT has no entry for is an unknown window, which is a reason
        # to send less rather than more.
        os.environ["OPENROUTER_MODEL"] = "someone/not-in-the-catalogue"
        assert session.context_window() == agent_session.FALLBACK_CONTEXT
    finally:
        os.environ.pop("OPENROUTER_MODEL", None)
        if previous is not None:
            os.environ["OPENROUTER_MODEL"] = previous


def test_the_oldest_turns_are_dropped_when_the_budget_runs_out():
    """Dropped, not summarised: a summary of a turn is a claim about what
    happened in it, and nothing here can make one that is true. The newest
    turn always survives, because a request without it is pointless."""
    session = Session(workspace="C:\\project", share=1.0)
    # Stated rather than read off the catalogue, so the arithmetic under test
    # is the dropping and not whichever model happens to be selected.
    session.context_window = lambda: 200
    for number in range(6):
        session.record("Question %d." % number, "A" * 200)
    assert session.carry_budget() == 200
    carried = session.carried()
    assert 0 < len(carried) < 6, len(carried)
    assert carried[-1].task == "Question 5.", carried[-1]
    # Whatever survives, it is a suffix of the conversation: history is
    # dropped from the far end, never from the middle.
    tasks = [turn.task for turn in session.turns]
    assert [turn.task for turn in carried] == tasks[-len(carried):]


def test_the_number_of_turns_carried_is_capped_whatever_the_budget_says():
    """Windows are large enough now that a budget alone would carry a whole
    day's work into every request, most of it irrelevant."""
    session = Session(workspace="C:\\project", share=1.0)
    for number in range(agent_session.MAX_TURNS_KEPT + 8):
        session.record("Question %d." % number, "Answer %d." % number)
    assert len(session.carried()) <= agent_session.MAX_TURNS_KEPT


def test_the_estimate_is_an_estimate_and_is_only_used_as_one():
    """Every provider counts differently and none of them will count before
    the request is sent. It decides how much history to carry and is never
    reported as a token figure."""
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * agent_ui.CHARS_PER_TOKEN) == 1
    assert estimate_tokens("a" * (agent_ui.CHARS_PER_TOKEN * 3 + 1)) == 4
    assert Turn("a" * 40, "b" * 40).size() >= 20


# --- provider independence --------------------------------------------------

def test_the_conversation_is_kept_in_tmts_own_shape_not_a_providers():
    """The same conversation has to survive the provider or the model changing
    mid-session, which it cannot do if it is stored as one provider's request
    body. It is a plain role/content list, and the adapters convert it."""
    session = Session(workspace="C:\\project")
    session.record("Build it.", "Built.")
    for message in session.carried_messages():
        assert set(message) == {"role", "content"}, message
        assert message["role"] in ("user", "assistant"), message
        assert isinstance(message["content"], str)

    import agent_providers
    carried = session.begin_turn("Extend it.", "SYSTEM")[0]
    for provider_id in ("openrouter", "openai", "anthropic", "gemini"):
        provider = agent_providers.get_provider(provider_id)
        system, converted = provider.convert_messages(carried)
        assert system == "SYSTEM", (provider_id, system)
        # Every non-system turn survives the conversion, in order, and the
        # system prompt is lifted out rather than sent as a message.
        assert len(converted) == 3, (provider_id, converted)
        rendered = str(converted)
        assert "Build it." in rendered and "Built." in rendered, (provider_id, rendered)
        assert "Extend it." in rendered, (provider_id, rendered)


def test_the_session_reads_its_facts_rather_than_keeping_copies():
    """A session that remembered the model chosen at launch would keep sending
    to it after Settings had moved on, and the status line would be telling
    the truth while the request was not."""
    session = Session(workspace="C:\\project")
    previous_provider = os.environ.get("TMT_PROVIDER")
    previous_model = os.environ.get("OPENROUTER_MODEL")
    try:
        os.environ["TMT_PROVIDER"] = "openrouter"
        os.environ["OPENROUTER_MODEL"] = agent_models.FREE_MODELS[0]["id"]
        assert session.provider_id == "openrouter"
        assert session.model_id == agent_models.FREE_MODELS[0]["id"]

        os.environ["OPENROUTER_MODEL"] = agent_models.FREE_MODELS[2]["id"]
        assert session.model_id == agent_models.FREE_MODELS[2]["id"]

        os.environ["TMT_PROVIDER"] = "anthropic"
        assert session.provider_id == "anthropic"
    finally:
        for name, value in (("TMT_PROVIDER", previous_provider),
                            ("OPENROUTER_MODEL", previous_model)):
            os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value


# --- what the session has cost and changed ----------------------------------

def test_lines_are_counted_only_where_both_halves_are_known():
    """The readout in the corner is a count, not an impression. An event
    carries `added` and `removed` when the action that made it could count
    them -- a patch knows both, a write knows only what it wrote, because what
    it replaced was gone before anyone could count it. A missing half
    contributes nothing rather than a zero, which would read as "removed none"
    when the truth is "nobody knows"."""
    session = Session(workspace="C:\\project")
    session.count_history([
        event("file_edit", "Patched a.py", added=18, removed=4),
        event("file_edit", "Patched b.py", added=3, removed=1),
        event("file_create", "Wrote c.py", lines=40),      # no +/- to be had
        event("progress", "no counts here"),
    ])
    assert (session.lines_added, session.lines_removed) == (21, 5), (
        session.lines_added, session.lines_removed)

    # A write OVER an existing file reports what it wrote and nothing about
    # what it replaced -- that was gone before anyone could count it -- so it
    # moves neither total.
    written = agent_actions.action_event(
        "write_file", {"action": "write_file", "path": "d.py", "content": "a\nb\n"},
        "Wrote file: d.py")
    assert "added" not in written.detail and "removed" not in written.detail, written.detail
    session.count_event(written)
    assert (session.lines_added, session.lines_removed) == (21, 5)

    # A write to a path that did not exist is a different fact, and the action
    # says which it was. It gained every line it now has and lost none, and
    # that "none" is a measurement rather than a guess -- which is why a
    # session that only creates files must not read "+0 lines".
    created = agent_actions.action_event(
        "write_file", {"action": "write_file", "path": "e.py", "content": "a\nb\nc\n"},
        "Created file: e.py")
    assert created.detail.get("added") == 3 and created.detail.get("removed") == 0
    session.count_event(created)
    assert (session.lines_added, session.lines_removed) == (24, 5)

    # A batch claims both halves only when every one of its writes was a
    # creation. One overwrite among them and the removed count is unknowable
    # for the batch as a whole, so it is not given at all.
    entries = [{"path": "f.py", "content": "a\n"}, {"path": "g.py", "content": "b\nc\n"}]
    batch = agent_actions.action_event(
        "write_files", {"action": "write_files", "files": entries},
        "Created file: f.py\nCreated file: g.py")
    assert batch.detail.get("added") == 3 and batch.detail.get("removed") == 0
    mixed = agent_actions.action_event(
        "write_files", {"action": "write_files", "files": entries},
        "Created file: f.py\nWrote file: g.py")
    assert "added" not in mixed.detail and "removed" not in mixed.detail, mixed.detail

    # An append knows both halves, because it removed nothing.
    appended = agent_actions.action_event(
        "append_file", {"action": "append_file", "path": "d.py", "content": "c\n"},
        "Appended 1 line to d.py")
    session.count_event(appended)
    assert (session.lines_added, session.lines_removed) == (25, 5)

    session.count_event(None)
    session.count_history(None)
    assert (session.lines_added, session.lines_removed) == (25, 5)


def test_tokens_sent_are_estimated_and_tokens_generated_are_exact_when_told():
    """No provider will count a request before it is sent, so what goes out is
    always an estimate. What comes back is the provider's own figure whenever
    it reports one, and the session records which it had, so the readout can
    say so rather than presenting a guess as a measurement."""
    session = Session(workspace="C:\\project")
    session.record_request([{"role": "system", "content": "a" * 400},
                            {"role": "user", "content": "b" * 400},
                            "not a message at all"])
    assert session.tokens_in == 2 * estimate_tokens("a" * 400), session.tokens_in

    session.record_reply("x" * 400, 97)
    assert session.tokens_out == 97, session.tokens_out
    assert session.tokens_out_exact is True

    # No figure from the provider: the reply's length is all there is, and the
    # whole readout drops to being labelled an estimate rather than quietly
    # mixing a measured number with a guessed one.
    session.record_reply("x" * 400, None)
    assert session.tokens_out == 97 + estimate_tokens("x" * 400)
    assert session.tokens_out_exact is False


def test_the_counters_belong_to_the_run_like_everything_else_here():
    """They are the session's, so a relaunch starts at zero for the same
    reason the conversation does."""
    session = Session(workspace="C:\\project")
    session.count_event(event("file_edit", "x", added=5, removed=2))
    session.record_request([{"role": "user", "content": "hello"}])
    assert session.lines_added and session.tokens_in
    fresh = Session(workspace="C:\\project")
    assert (fresh.lines_added, fresh.lines_removed) == (0, 0)
    assert (fresh.tokens_in, fresh.tokens_out) == (0, 0)
    assert fresh.tokens_out_exact is True
    # And clearing the conversation is not clearing the meter: the files were
    # still changed and the tokens were still spent.
    session.clear()
    assert session.lines_added == 5 and session.tokens_in


# --- the loop's own trimming --------------------------------------------

def test_trimming_a_long_turn_never_takes_the_question_with_it():
    """The loop adds an action and a result per step, and a long turn outgrows
    the trim limit. Trimming into the carried context would take the task out
    of the request and leave the model answering something nobody asked."""
    session = Session(workspace="C:\\project")
    session.record("Use the Calc.py architecture.", "Noted.")
    messages, pinned = session.begin_turn("Now add percentages.", "SYSTEM")
    for number in range(60):
        messages.append({"role": "assistant", "content": "action %d" % number})
        messages.append({"role": "user", "content": "result %d" % number})

    trimmed = agent_actions.trim_messages(messages, pinned)
    assert len(trimmed) < len(messages), (len(trimmed), len(messages))
    head = trimmed[:pinned]
    assert head == messages[:pinned], head
    assert head[-1]["content"] == "Now add percentages."
    assert "Calc.py architecture" in head[1]["content"]
    # The tail is the most recent work, and the gap is stated rather than
    # silently closed.
    assert trimmed[-1]["content"] == "result 59", trimmed[-1]
    assert any("trimmed" in message["content"] for message in trimmed), trimmed

    # The old shape still holds for a caller that carries no history.
    plain = [{"role": "system", "content": "S"}, {"role": "user", "content": "T"}]
    plain += [{"role": "user", "content": str(number)} for number in range(60)]
    assert agent_actions.trim_messages(plain)[:2] == plain[:2]


def test_files_touched_reads_events_and_never_guesses():
    assert files_touched(None) == ()
    assert files_touched([event("progress", "no files here")]) == ()
    assert files_touched([event("file_edit", "x", paths=("a.py",)),
                          event("file_edit", "y", paths=("a.py", "b.py"))]) == ("a.py", "b.py")
