"""The tag protocol wired through everything that reads or writes a reply.

`test_agent_protocol` owns the grammar: what a tag reply means, character by
character. This module owns the WIRING -- that the format chosen in Settings
actually decides what `ask_model` asks a provider for and hands back, what the
two step loops read, what they say when a reply is wrong, what a carried
answer looks like on the next turn, and what happens when a model answers in
the format that is NOT in force.

Every scripted tag reply here is built from an action object with
`agent_protocol.render(obj, "tags")` rather than written out by hand. A grammar
change therefore moves these replies with it, and a failure here is always a
wiring failure rather than a test that spelled a tag wrongly.

The suite is pinned to JSON at the runner, so every tag test opts in with
`ReplyFormat("tags")`, and the JSON half of a pair says `ReplyFormat("json")`
explicitly rather than trusting the pin.

THE TWO RULES THE OWNER SET, and the tests that hold them:

  * Under JSON nothing changes: the request, the correction sentences and the
    carried answer are byte for byte what they were. The pins for that are in
    the modules that always had them; the correction texts are pinned again
    here, word for word, because they moved into `agent_protocol`.
  * A reply in the other format is ACCEPTED, never refused, and said: one
    warning row for the user and one line on the result the model is handed.
"""

import contextlib
import io
import json
import os
from pathlib import Path

import agent_actions
import agent_manager
import agent_model
import agent_multi
import agent_protocol
import agent_review
import agent_worker
import TMT
from agent_session import Session
from test_agent_agents import Clock, Executor
from test_agent_agents import Replies as ModelReplies
from test_agent_agents import run as run_agent
from test_agent_cli import Reporting
from test_agent_reply_format import ReplyFormat
from test_agent_review import Reviewer, result_json, state_after_work, worked_plan
from test_agent_review import run_review as review_through_dispatcher
from test_agent_stream import chunk_text, collect, texts
from test_agent_threading import capture_post_chat
from test_agent_workspace import Workspace

TAGS = agent_protocol.TAGS
JSON = agent_protocol.JSON


def tags(obj):
    """An action object as a tag reply, written by the protocol's own writer."""
    return agent_protocol.render(obj, TAGS)


def compact(obj):
    """An action object as the JSON a model sends."""
    return json.dumps(obj)


def on_disk(text):
    """The bytes `write_file` leaves for `text` on this platform.

    `write_text` translates newlines, so this is what "exact" means: the
    content the action carried, newline for newline, in the platform's own
    line ending -- the same bytes a JSON write_file has always produced.
    """
    return text.replace("\n", os.linesep).encode("utf-8")


# --- driving TMT.main ----------------------------------------------------------

def drive(answers, replies, real_model=False, files=None):
    """TMT.main through `answers`, the model answering `replies` in order.

    Returns (drawn, seen, console, written). `seen` is the message list of
    every request; `written` is every file left in the workspace, as bytes,
    keyed by its path relative to the root (TMT's own TMT_Context and .git
    left out).

    `real_model` keeps `agent_model.ask_model` in the loop and replaces only
    the transport under it, so the reply goes through the real streaming
    reader, the real finalisation and the real fabricated replies. Without it
    `ask_model` itself is the script, as `drive_session` does.
    """
    seen = []

    def next_reply(messages):
        seen.append([dict(message) for message in messages])
        return replies[min(len(seen) - 1, len(replies) - 1)]

    def scripted(messages, on_event=None):
        return next_reply(messages)

    def stream_chat(payload, on_usage=None, on_input_usage=None):
        text = next_reply(payload["messages"])
        for start in range(0, len(text), 7):
            yield text[start:start + 7]

    def post_chat(payload, spinner=True):
        content = next_reply(payload["messages"])
        return {"choices": [{"message": {"content": content}}]}, None

    box = Workspace(git=bool(files), files=files)
    screen = io.StringIO()
    saved = (TMT.console, TMT.ensure_api_key, TMT.run_startup,
             TMT.ensure_git_identity, TMT.ask_model,
             agent_model.stream_chat, agent_model._post_chat)
    previous_cwd = Path.cwd()
    written = {}
    try:
        os.chdir(str(box.path))
        console = TMT.console = Reporting(answers)
        TMT.ensure_api_key = lambda: True
        TMT.run_startup = lambda **kwargs: "start"
        TMT.ensure_git_identity = lambda *a, **k: None
        if real_model:
            TMT.ask_model = agent_model.ask_model
            agent_model.stream_chat = stream_chat
            agent_model._post_chat = post_chat
        else:
            TMT.ask_model = scripted
        with contextlib.redirect_stdout(screen):
            TMT.main([])
        for item in box.path.rglob("*"):
            relative = item.relative_to(box.path)
            if item.is_file() and relative.parts[0] not in (".git", "TMT_Context"):
                written[relative.as_posix()] = item.read_bytes()
    finally:
        os.chdir(str(previous_cwd))
        (TMT.console, TMT.ensure_api_key, TMT.run_startup,
         TMT.ensure_git_identity, TMT.ask_model,
         agent_model.stream_chat, agent_model._post_chat) = saved
        box.close()
    return screen.getvalue(), seen, console, written


def shown(drawn, row):
    """Whether `row` was drawn, however the transcript wrapped it."""
    return " ".join(row.split()) in " ".join(drawn.split())


def handed_back(seen, request):
    """What the model was told before its `request`-th request (0-based)."""
    return seen[request][-1]["content"]


def echoed(seen, request):
    """The assistant turn the `request`-th request carries as the model's own."""
    return seen[request][-2]["content"]


# --- the correction sentences --------------------------------------------------

def test_the_json_corrections_are_word_for_word_what_the_loops_always_said():
    """They moved into `agent_protocol`, and under JSON nothing about them may
    change. Spelled out as literals: comparing the function to itself would
    pass whatever somebody changed it to."""
    said = agent_protocol.correction
    assert said("unreadable", JSON, error="E") == (
        "INVALID: that reply could not be read as JSON. The parser said: E\n"
        "Reply with exactly one JSON object and nothing else -- no prose before "
        "it, no prose after it, no code fences. Emit the action you meant.")
    assert said("worker_unreadable", JSON, error="E") == (
        "INVALID: that reply could not be read as JSON. The parser said: E\n"
        "Reply with exactly one JSON object and nothing else -- no prose before "
        "it, no prose after it, no code fences.")
    assert said("prose_before_work", JSON) == (
        "That was prose, and nothing has run yet, so it announced work rather "
        "than reporting it. Emit the action you just described, as one JSON "
        "object.")
    assert said("worker_prose", JSON) == (
        "That was prose, and nothing has run yet, so it described work rather "
        "than doing it. Emit the action you just described, as one JSON object.")
    assert said("invalid", JSON, error="x") == "INVALID: x. Output a corrected action JSON."
    assert said("invalid_in_batch", JSON, invalid="i", ran="r") == (
        "INVALID: i\nRan before it:\nr\nOutput a corrected action JSON.")
    assert said("actions_not_list", JSON) == (
        "INVALID: 'actions' must be a non-empty list. Try again.")
    assert said("action_raised", JSON, action="a", raised="K", error="e") == (
        "INVALID: the action 'a' could not run with those arguments -- it raised "
        "K: e\nCheck the type of every key you sent: paths and text are strings, "
        "line numbers are unquoted numbers, flags are unquoted true or false. "
        "Emit a corrected action.")
    assert said("worker_action_raised", JSON, action="a", raised="K", error="e") == (
        "INVALID: the action 'a' could not run with those arguments -- it raised "
        "K: e\nCheck the type of every key you sent: paths and text are strings, "
        "line numbers are unquoted numbers, flags are unquoted true or false.")
    assert said("not_an_object", JSON) == "the reply was not a JSON object"
    assert said("stop_unreadable", JSON, attempts=3) == (
        "stopped: the model's reply could not be read as JSON after 3 attempts")
    assert said("stop_not_object", JSON, attempts=3) == (
        "stopped: the model did not send a JSON object after 3 attempts")
    assert said("batch_entry", JSON) == "every entry in 'actions' must be a JSON object"
    assert said("missing_action", JSON) == "Missing 'action' key in JSON"


def test_under_tags_every_correction_states_the_tag_contract_and_never_asks_for_json():
    """The one thing a correction must not do is teach the shape the prompt
    forbids. Every kind, so a kind added later is held to it too."""
    fields = dict(error="E", invalid="I", ran="R", action="a", raised="K", attempts=2)
    for kind in agent_protocol.correction_kinds():
        said = agent_protocol.correction(kind, TAGS, **fields)
        assert "JSON object" not in said, (kind, said)
        assert "action JSON" not in said, (kind, said)
        assert said != agent_protocol.correction(kind, JSON, **fields), kind
    unreadable = agent_protocol.correction("unreadable", TAGS, error="E")
    assert "/action_name/" in unreadable and "//action_name/" in unreadable, unreadable
    assert "no JSON" in unreadable and "no code fences" in unreadable, unreadable
    raised = agent_protocol.correction("action_raised", TAGS, action="a",
                                       raised="K", error="e")
    assert "true or false" in raised and "/item/" in raised, raised


def test_drift_is_said_only_when_the_reply_came_in_the_other_format():
    assert agent_protocol.drift(TAGS, JSON) == (
        "The reply was JSON but this session speaks tags. It was accepted this "
        "time; from now on reply in tags.")
    assert agent_protocol.drift(JSON, TAGS) == (
        "The reply was tags but this session speaks JSON. It was accepted this "
        "time; from now on reply in JSON.")
    for expected in (TAGS, JSON):
        assert agent_protocol.drift(expected, expected) == ""
        assert agent_protocol.drift(expected, None) == ""
        assert agent_protocol.drift_notice(expected, expected) == ""
        assert agent_protocol.drift_notice(expected, None) == ""
    assert "JSON" in agent_protocol.drift_notice(TAGS, JSON)
    assert "tags" in agent_protocol.drift_notice(JSON, TAGS)


# --- ask_model ---------------------------------------------------------------

END_HELLO = {"action": "end_conversation", "message": "Hello world",
             "progress": "Saying hello", "next_step": "Ask another"}


def test_a_streamed_tag_reply_reports_its_events_and_comes_back_as_written():
    """The tag streamer is the one selected, its five events are forwarded
    under their own names, and what comes back is the model's own text with
    only the whitespace around it trimmed -- so the history echoes what the
    model wrote."""
    text = tags(END_HELLO)
    with ReplyFormat("tags"):
        for size in (1, 5, len(text) + 2):
            raw, events = collect(chunk_text("\n " + text + "\n\n", size))
            assert raw == text, (size, raw)
            assert texts(events) == "Hello world", (size, events)
            assert ("action", "end_conversation") in events, (size, events)
            assert ("progress", "Saying hello") in events, (size, events)
            assert ("next_step", "Ask another") in events, (size, events)
            assert [kind for kind, _ in events][0] == "first_content"


def test_a_tag_reply_with_prose_in_front_still_streams_under_tags():
    """The reply's first character is not a tag here, so it is the format in
    force -- and nothing else -- that picks the tag reader. The JSON reader
    would wait for a `{` that never comes and relay nothing."""
    text = "Okay, answering now.\n" + tags(END_HELLO)
    with ReplyFormat("tags"):
        raw, events = collect(chunk_text(text, 6))
    assert raw == text, raw
    assert texts(events) == "Hello world", events
    assert ("progress", "Saying hello") in events, events


def test_prose_with_braces_in_it_is_prose_under_tags_and_not_a_broken_object():
    """Under tags a reply is read as tags first. A sentence that happens to
    contain braces is the model's prose -- not the JSON format's "first `{`",
    which would hand it back as an unreadable object."""
    sentence = "I updated the mapping {a: 1} as you asked."
    with ReplyFormat("tags"):
        streamed, _events = collect(chunk_text(sentence, 4))
        blocked = blocking(sentence)
    for raw in (streamed, blocked):
        obj = agent_protocol.parse(raw)
        assert agent_model.is_prose(obj), raw
        assert obj["message"] == sentence, obj
    # Under JSON the same sentence is read exactly as it always was.
    with ReplyFormat("json"):
        assert blocking(sentence) == agent_model._extract_json(sentence)


def test_the_blocking_path_returns_the_tag_text_too():
    text = tags(END_HELLO)
    with ReplyFormat("tags"):
        restore, calls = capture_post_chat("  \n" + text + "\n")
        try:
            raw = agent_model.ask_model([{"role": "user", "content": "hi"}])
        finally:
            restore()
    assert raw == text, raw
    assert agent_protocol.parse(raw)["message"] == "Hello world"


def streamed_payload():
    seen = {}
    original = agent_model.stream_chat

    def fake_stream(payload, on_usage=None, on_input_usage=None):
        seen["payload"] = payload
        yield tags({"action": "end_conversation", "message": "ok"})

    agent_model.stream_chat = fake_stream
    try:
        agent_model.ask_model([{"role": "user", "content": "hi"}],
                              on_event=lambda event: None)
    finally:
        agent_model.stream_chat = original
    return seen["payload"]


def blocking_payload():
    restore, calls = capture_post_chat(tags({"action": "end_conversation",
                                             "message": "ok"}))
    try:
        agent_model.ask_model([{"role": "user", "content": "hi"}])
    finally:
        restore()
    return calls[0]["payload"]


def test_json_mode_is_asked_for_under_json_and_never_under_tags():
    """Under tags it would make the provider force the reply back into the
    shape the prompt has just told the model not to use. Both paths, because
    a guard on one would leave the other asking."""
    original = agent_model._json_mode_ok
    agent_model._json_mode_ok = True
    try:
        with ReplyFormat("tags"):
            assert "response_format" not in streamed_payload()
            assert "response_format" not in blocking_payload()
        with ReplyFormat("json"):
            assert streamed_payload()["response_format"] == {"type": "json_object"}
            assert blocking_payload()["response_format"] == {"type": "json_object"}
    finally:
        agent_model._json_mode_ok = original


def test_a_json_mode_rejection_retry_is_never_entered_under_tags():
    """Nothing asked for JSON mode, so an error that happens to name it cannot
    have been a refusal of it. One request, the provider's failure reported,
    and the flag left alone for the format that does use it."""
    original = agent_model._json_mode_ok
    agent_model._json_mode_ok = True
    try:
        with ReplyFormat("tags"):
            restore, calls = capture_post_chat(
                "unused", errors=["provider rejected response_format"])
            try:
                raw = agent_model.ask_model([{"role": "user", "content": "hi"}],
                                            quiet=True)
            finally:
                restore()
        assert len(calls) == 1, calls
        assert agent_model._json_mode_ok is True
        obj = agent_protocol.parse(raw)
        assert agent_model.synthetic_reason(obj) == agent_model.PROVIDER_FAILURE, obj

        # The streaming path has its own copy of the retry, and the same rule.
        def refusing(payload, on_usage=None, on_input_usage=None):
            raise agent_model.StreamError("provider rejected response_format")
            yield ""                                # a generator, as the real one is

        saved_stream = agent_model.stream_chat
        agent_model.stream_chat = refusing
        with ReplyFormat("tags"):
            restore, calls = capture_post_chat(
                tags({"action": "end_conversation", "message": "fell back"}))
            try:
                raw = agent_model.ask_model([{"role": "user", "content": "hi"}],
                                            on_event=lambda event: None, quiet=True)
            finally:
                restore()
                agent_model.stream_chat = saved_stream
        assert agent_model._json_mode_ok is True
        assert agent_protocol.parse(raw)["message"] == "fell back", raw
    finally:
        agent_model._json_mode_ok = original


def test_every_reply_tmt_makes_up_is_written_in_the_format_in_force():
    """A fabricated reply is handed back to the model as its own assistant
    turn when the loop asks again, so under tags it must never be JSON. The
    markers survive the trip, because the loop's whole reading of a failure
    rests on them."""
    with ReplyFormat("tags"):
        made = {
            "empty": agent_model._extract_json(""),
            "structure": agent_model._extract_json('{"action":"end_conversation"'),
            "provider": agent_model._error_reply("HTTP 429 rate limited"),
            "prose": agent_model._prose_reply("I did the work."),
        }
        for name, raw in made.items():
            assert agent_protocol.detect(raw) == TAGS, (name, raw)
            assert not raw.lstrip().startswith("{"), (name, raw)
        obj = {name: agent_protocol.parse(raw) for name, raw in made.items()}
    assert agent_model.synthetic_reason(obj["empty"]) == agent_model.PARSE_FAILURE
    assert obj["empty"]["message"] == "empty response from model"
    assert agent_model.synthetic_reason(obj["structure"]) == agent_model.PARSE_FAILURE
    assert agent_model.synthetic_reason(obj["provider"]) == agent_model.PROVIDER_FAILURE
    assert "429" in obj["provider"]["message"]
    assert agent_model.is_prose(obj["prose"]) and not agent_model.is_synthetic(obj["prose"])
    assert obj["prose"]["message"] == "I did the work."
    # And under JSON, exactly the json.dumps it always was.
    with ReplyFormat("json"):
        assert agent_model._made_up("x") == json.dumps(
            {"action": "end_conversation", "message": "x",
             agent_model.SYNTHETIC_KEY: True,
             agent_model.SYNTHETIC_REASON: agent_model.PARSE_FAILURE})


def blocking(reply):
    restore, _calls = capture_post_chat(reply)
    try:
        return agent_model.ask_model([{"role": "user", "content": "hi"}])
    finally:
        restore()


def test_malformed_tags_prose_and_silence_each_come_back_as_their_own_kind():
    malformed = "/write_file/ /path/ a.txt //path/ /content/ never closed //write_file/"
    with ReplyFormat("tags"):
        broken = agent_protocol.parse(blocking(malformed))
        prose = agent_protocol.parse(blocking("I looked and it is fine."))
        empty = agent_protocol.parse(blocking("   \n"))
    # The parser's own sentence, so the model is told where and how.
    assert agent_model.synthetic_reason(broken) == agent_model.PARSE_FAILURE, broken
    assert "/content/ is never closed" in broken["message"], broken
    assert agent_model.is_prose(prose) and prose["message"] == "I looked and it is fine."
    assert empty["message"] == "empty response from model", empty


def test_a_json_reply_under_tags_comes_back_as_its_object_and_still_streams():
    """Accepted, not refused -- and relayed live, because a model that drifted
    should not have its answer appear all at once at the end."""
    reply = compact({"action": "end_conversation", "message": "Hi from JSON"})
    with ReplyFormat("tags"):
        raw, events = collect(chunk_text(reply, 4))
        assert raw == reply, raw
        assert texts(events) == "Hi from JSON", events
        # Prose in front of it is cut away, exactly as the JSON format does.
        assert blocking("Sure, here it is:\n" + reply) == reply


def test_a_tag_reply_under_json_is_read_as_tags_and_not_as_prose():
    """Read as JSON it has no object, so it would be prose -- and prose after
    work ENDS the turn with the model's tags printed as its answer."""
    text = tags({"action": "read_file", "path": "a.py"})
    with ReplyFormat("json"):
        raw = blocking("\n" + text + "\n")
        assert raw == text, raw
        assert not agent_model.is_prose(agent_protocol.parse(raw))
        streamed, events = collect(chunk_text(text, 3))
    assert streamed == text, streamed
    assert ("action", "read_file") in events, events


# --- the main loop -------------------------------------------------------------

def test_one_action_and_then_the_answer_through_the_real_loop():
    replies = [tags({"action": "write_file", "path": "a.txt",
                     "content": "one\ntwo\n", "progress": "Writing a.txt"}),
               tags({"action": "end_conversation", "message": "Wrote a.txt."})]
    with ReplyFormat("tags"):
        drawn, seen, console, written = drive(["do the thing", "quit"], replies)
    assert written.get("a.txt") == on_disk("one\ntwo\n"), written
    assert "Writing a.txt" in drawn, drawn
    assert "Wrote a.txt." in drawn, drawn
    assert len(seen) == 2, len(seen)
    # The history echoes the model's own tag text, never a JSON rewrite of it.
    assert echoed(seen, 1) == replies[0], echoed(seen, 1)
    assert "JSON" not in handed_back(seen, 1), handed_back(seen, 1)


def test_the_history_echo_is_the_models_own_text_through_the_real_model_call():
    """Through `agent_model` as well: what it hands the loop is the reply as
    written, trimmed of the whitespace around it and of nothing else."""
    first = tags({"action": "write_file", "path": "b.txt", "content": "b\n"})
    replies = ["\n  " + first + "\n\n",
               tags({"action": "end_conversation", "message": "Done."})]
    with ReplyFormat("tags"):
        drawn, seen, console, written = drive(["do the thing", "quit"], replies,
                                              real_model=True)
    assert written.get("b.txt") == on_disk("b\n"), written
    assert echoed(seen, 1) == first, repr(echoed(seen, 1))
    assert "Done." in drawn, drawn


def test_multi_line_file_text_reaches_the_disk_exactly_trailing_newline_and_all():
    """The reason tags exist: a file is written exactly as it is. Quotes,
    braces, indentation, a blank line and the newline at the end."""
    content = ('def greet(name):\n'
               '    """Say {hello}."""\n'
               '\n'
               '    return "hi, " + name  # it\'s fine\n')
    replies = [tags({"action": "write_file", "path": "greet.py", "content": content}),
               tags({"action": "end_conversation", "message": "Written."})]
    with ReplyFormat("tags"):
        _drawn, _seen, _console, written = drive(["write it", "quit"], replies,
                                                 real_model=True)
    assert written.get("greet.py") == on_disk(content), written.get("greet.py")


def test_an_indented_patch_applies_through_the_real_loop():
    """`search` and `replace` keep their leading indent: they are file text."""
    source = "def f():\n    return 1\n"
    replies = [tags({"action": "patch_file", "path": "mod.py",
                     "search": "    return 1", "replace": "    return 2"}),
               tags({"action": "end_conversation", "message": "Patched."})]
    with ReplyFormat("tags"):
        _drawn, seen, _console, written = drive(["patch it", "quit"], replies,
                                                files={"mod.py": source})
    assert written.get("mod.py") == on_disk("def f():\n    return 2\n"), (
        written.get("mod.py"), handed_back(seen, 1))


def test_a_multi_tool_with_calls_runs_through_the_real_loop():
    replies = [tags({"action": "multi_tool", "progress": "Writing both",
                     "calls": [{"action": "write_file", "path": "x.txt", "content": "x\n"},
                               {"action": "write_file", "path": "y.txt", "content": "y\n"}]}),
               tags({"action": "end_conversation", "message": "Both written."})]
    with ReplyFormat("tags"):
        drawn, seen, _console, written = drive(["write both", "quit"], replies)
    assert written.get("x.txt") == on_disk("x\n"), written
    assert written.get("y.txt") == on_disk("y\n"), written
    assert "Both written." in drawn


def test_consecutive_blocks_run_as_a_batch_in_order():
    batch = tags({"actions": [
        {"action": "write_file", "path": "first.txt", "content": "1\n"},
        {"action": "append_file", "path": "first.txt", "content": "2\n"},
    ]})
    # Two blocks one after the other, not a wrapper: that is the batch form.
    assert batch.count("//write_file/") == 1 and "/actions/" not in batch, batch
    replies = [batch, tags({"action": "end_conversation", "message": "Batched."})]
    with ReplyFormat("tags"):
        drawn, seen, _console, written = drive(["do both", "quit"], replies)
    assert written.get("first.txt") == on_disk("1\n2\n"), written
    assert handed_back(seen, 1).startswith("Batch results:"), handed_back(seen, 1)
    assert "Batched." in drawn


def test_a_message_then_work_then_the_answer():
    replies = [tags({"action": "send_message", "message": "About to write."}),
               tags({"action": "write_file", "path": "m.txt", "content": "m\n"}),
               tags({"action": "end_conversation", "message": "Wrote m.txt."})]
    with ReplyFormat("tags"):
        drawn, seen, _console, written = drive(["go", "quit"], replies)
    assert "About to write." in drawn, drawn
    assert written.get("m.txt") == on_disk("m\n"), written
    assert "Wrote m.txt." in drawn, drawn
    assert len(seen) == 3, len(seen)


def test_prose_before_any_work_is_handed_back_in_the_words_of_tags():
    replies = ["I will write the file first.",
               tags({"action": "write_file", "path": "p.txt", "content": "p\n"}),
               tags({"action": "end_conversation", "message": "Done after all."})]
    with ReplyFormat("tags"):
        drawn, seen, _console, written = drive(["go", "quit"], replies,
                                               real_model=True)
        said = agent_protocol.correction("prose_before_work", TAGS)
    assert handed_back(seen, 1) == said, handed_back(seen, 1)
    assert "JSON" not in handed_back(seen, 1)
    # What it was handed back WITH is the prose reply, as tags.
    assert agent_protocol.detect(echoed(seen, 1)) == TAGS, echoed(seen, 1)
    assert written.get("p.txt") == on_disk("p\n"), written
    assert "Done after all." in drawn


def test_malformed_tags_are_handed_back_with_the_parsers_own_sentence():
    """Both routes: through `agent_model`, where the failure becomes a
    fabricated PARSE_FAILURE, and straight into the loop, where `parse`
    raises. Either way the model reads the sentence that says what was
    wrong, in the tag contract's words, and the turn carries on."""
    malformed = "/write_file/ /path/ a.txt //path/ /content/ abc //write_file/"
    for real_model in (True, False):
        replies = [malformed,
                   tags({"action": "end_conversation", "message": "Recovered."})]
        with ReplyFormat("tags"):
            drawn, seen, console, _written = drive(["go", "quit"], replies,
                                                   real_model=real_model)
        complaint = handed_back(seen, 1)
        assert complaint.startswith(
            "INVALID: that reply could not be read as tags. The parser said: "
            "/content/ is never closed"), (real_model, complaint)
        assert "/action_name/" in complaint, complaint
        assert "JSON object" not in complaint, complaint
        assert "Unreadable reply" in console.said(), console.said()
        assert "Recovered." in drawn, drawn


def test_a_json_reply_under_tags_is_accepted_said_and_answered():
    """The owner's rule: the prompt is strict and the reader is not. The work
    runs, the user is shown one row saying so, the model's next result
    carries one line saying so, and the answer still reaches the screen."""
    replies = [compact({"action": "write_file", "path": "j.txt", "content": "j\n"}),
               tags({"action": "end_conversation", "message": "Back in tags."})]
    with ReplyFormat("tags"):
        drawn, seen, _console, written = drive(["go", "quit"], replies)
    assert written.get("j.txt") == on_disk("j\n"), written
    assert shown(drawn, agent_protocol.drift_notice(TAGS, JSON)), drawn
    # One row for the one reply that drifted; the answer, in tags, drew none.
    assert " ".join(drawn.split()).count(
        agent_protocol.drift_notice(TAGS, JSON)) == 1, drawn
    told = handed_back(seen, 1)
    assert told.endswith("\n" + agent_protocol.drift(TAGS, JSON)), told
    assert told.count(agent_protocol.drift(TAGS, JSON)) == 1, told
    assert "Back in tags." in drawn, drawn
    # The echo is still what the model wrote.
    assert echoed(seen, 1) == replies[0]


def test_a_drifted_final_answer_still_reaches_the_screen():
    """A JSON answer under tags. With prose in front of it the live tag reader
    sees no tag at all and relays nothing -- so the answer the user reads is
    the one drawn after the fact, and it must be drawn."""
    for reply in (compact({"action": "end_conversation", "message": "The answer, in JSON."}),
                  "Here you go: " + compact({"action": "end_conversation",
                                             "message": "The answer, in JSON."})):
        with ReplyFormat("tags"):
            drawn, _seen, _console, _written = drive(["ask", "quit"], [reply],
                                                     real_model=True)
        assert "The answer, in JSON." in drawn, (reply, drawn)
        assert shown(drawn, agent_protocol.drift_notice(TAGS, JSON)), drawn


def test_a_tag_reply_under_json_is_accepted_with_the_mirror_warning():
    replies = [tags({"action": "write_file", "path": "t.txt", "content": "t\n"}),
               compact({"action": "end_conversation", "message": "Back in JSON."})]
    with ReplyFormat("json"):
        drawn, seen, _console, written = drive(["go", "quit"], replies)
    assert written.get("t.txt") == on_disk("t\n"), written
    assert shown(drawn, agent_protocol.drift_notice(JSON, TAGS)), drawn
    assert handed_back(seen, 1).endswith("\n" + agent_protocol.drift(JSON, TAGS))
    assert "Back in JSON." in drawn


def test_the_drift_line_rides_on_every_kind_of_result_the_model_is_handed():
    """A batch, a message, and an answer held back by the plan gate -- singly
    and inside a batch. Each is a different line in the loop that builds what
    goes back, and a line that forgot the drift would teach nothing."""
    line = "\n" + agent_protocol.drift(TAGS, JSON)
    end = tags({"action": "end_conversation", "message": "Done."})
    with ReplyFormat("tags"):
        _d, batch_seen, _c, _w = drive(["go", "quit"], [
            compact({"actions": [
                {"action": "write_file", "path": "a.txt", "content": "a\n"},
                {"action": "write_file", "path": "b.txt", "content": "b\n"}]}),
            end])
        _d, message_seen, _c, _w = drive(["go", "quit"], [
            compact({"action": "send_message", "message": "About to start."}),
            end])
        plan = tags({"action": "plan", "operation": "create", "steps": ["Do it"]})
        finish = tags({"action": "plan", "operation": "update", "step": 1,
                       "status": "completed"})
        _d, held_seen, _c, _w = drive(["go /plan", "quit"], [
            plan, compact({"action": "end_conversation", "message": "Too early."}),
            finish, end])
        _d, batch_held_seen, _c, _w = drive(["go /plan", "quit"], [
            plan, compact({"actions": [
                {"action": "write_file", "path": "c.txt", "content": "c\n"},
                {"action": "end_conversation", "message": "Too early."}]}),
            finish, end])
    told = handed_back(batch_seen, 1)
    assert told.startswith("Batch results:") and told.endswith(line), told
    told = handed_back(message_seen, 1)
    assert told == TMT._MESSAGE_SENT + line, told
    told = handed_back(held_seen, 2)
    assert "BLOCKED" in told and told.endswith(line), told
    told = handed_back(batch_held_seen, 2)
    assert told.startswith("Batch results:") and "BLOCKED" in told, told
    assert told.endswith(line), told


def test_a_worker_is_told_about_drift_on_a_batch_and_a_message_too():
    line = "\n" + agent_protocol.drift(TAGS, JSON)
    finish = tags({"action": "internal_response", "response": "done"})
    manager = agent_manager.AgentManager(clock=Clock())
    with ReplyFormat("tags"):
        record = manager.spawn("batch")
        ask = ModelReplies([compact({"actions": [
            {"action": "read_file", "path": "a.py"},
            {"action": "read_file", "path": "b.py"}]}), finish])
        run_agent(record, manager, ask, Executor("x"))
        assert ask.calls[1]["messages"][-1]["content"].endswith(line)
        record = manager.spawn("message")
        ask = ModelReplies([compact({"action": "send_message", "message": "hi"}),
                            finish])
        run_agent(record, manager, ask, Executor("x"))
        assert ask.calls[1]["messages"][-1]["content"] == (
            agent_worker._MESSAGE_SENT + line)


def test_a_reply_in_the_format_in_force_carries_no_drift_line():
    replies = [compact({"action": "write_file", "path": "n.txt", "content": "n\n"}),
               compact({"action": "end_conversation", "message": "ok"})]
    with ReplyFormat("json"):
        drawn, seen, _console, _written = drive(["go", "quit"], replies)
    assert "accepted this time" not in handed_back(seen, 1)
    assert not shown(drawn, agent_protocol.drift_notice(JSON, TAGS))
    assert not shown(drawn, agent_protocol.drift_notice(TAGS, JSON))


def test_the_circuit_breaker_still_trips_on_three_identical_tag_replies():
    # Unreadable -- loose text between key tags -- so each one is handed back
    # and it is the breaker, not an answer, that ends the turn.
    stuck = "/end_conversation/ /message/ stuck //message/ stray //end_conversation/"
    try:
        agent_protocol.parse(stuck)
    except agent_protocol.ProtocolError:
        pass
    else:
        raise AssertionError("the scripted reply must be unreadable")
    with ReplyFormat("tags"):
        _drawn, seen, console, _written = drive(["go", "quit"], [stuck] * 6)
    assert "Circuit Breaker" in console.said(), console.said()
    assert len(seen) == 4, len(seen)


# --- the session carry ---------------------------------------------------------

def test_the_carried_answer_is_a_tag_block_under_tags_and_json_under_json():
    with ReplyFormat("tags"):
        session = Session(workspace="C:\\project")
        session.record("Build it.", "Built it in Calc.py.")
        carried = session.carried_messages()[1]["content"]
    assert carried == tags({"action": "end_conversation",
                            "message": "Built it in Calc.py."}), carried
    assert agent_protocol.parse(carried)["message"] == "Built it in Calc.py."
    with ReplyFormat("json"):
        session = Session(workspace="C:\\project")
        session.record("Build it.", "Built it in Calc.py.")
        carried = session.carried_messages()[1]["content"]
    assert carried == json.dumps({"action": "end_conversation",
                                  "message": "Built it in Calc.py."}), carried


def test_the_carried_answer_keeps_its_facts_under_tags():
    import agent_ui
    with ReplyFormat("tags"):
        session = Session(workspace="C:\\project")
        session.record("push to main", "Pushed.", [
            agent_ui.AgentEvent.make("milestone", "Committed 6f0a4f5 on main")])
        carried = agent_protocol.parse(session.carried_messages()[1]["content"])
    assert carried["action"] == "end_conversation"
    assert "Committed 6f0a4f5 on main" in carried["message"], carried


def test_the_next_turns_request_carries_the_answer_in_the_format_in_force():
    for protocol, write in ((TAGS, tags), (JSON, compact)):
        replies = [write({"action": "end_conversation", "message": "First answer."}),
                   write({"action": "end_conversation", "message": "Second answer."})]
        with ReplyFormat(protocol):
            _drawn, seen, _console, _written = drive(
                ["first question", "second question", "quit"], replies)
        second = seen[1]
        assistant = [m for m in second if m["role"] == "assistant"]
        assert len(assistant) == 1, second
        content = assistant[0]["content"]
        assert agent_protocol.detect(content) == protocol, (protocol, content)
        carried = agent_protocol.parse(content)
        assert carried["action"] == "end_conversation", carried
        # The answer, plus whatever facts the turn carried with it.
        assert carried["message"].startswith("First answer."), carried
        expected = {"action": "end_conversation", "message": carried["message"]}
        assert content == (tags(expected) if protocol == TAGS
                           else json.dumps(expected)), (protocol, content)


# --- the worker loop -----------------------------------------------------------

def test_a_worker_under_tags_reads_tags_and_finishes_on_the_shorthand():
    finish = tags({"action": "internal_response", "response": "I read a.py."})
    assert finish.startswith("/internal_response/ I read"), finish  # the shorthand
    first = tags({"action": "read_file", "path": "a.py"})
    manager = agent_manager.AgentManager(clock=Clock())
    record = manager.spawn("read a.py")
    ask = ModelReplies([first, finish])
    execute = Executor("contents")
    with ReplyFormat("tags"):
        said = run_agent(record, manager, ask, execute)
    assert said == "I read a.py.", said
    assert execute.actions == ["read_file"], execute.actions
    assert ask.calls[1]["messages"][-2]["content"] == first


def test_a_worker_batch_under_tags_runs_every_block():
    batch = tags({"actions": [{"action": "read_file", "path": "a.py"},
                              {"action": "read_file", "path": "b.py"}]})
    finish = tags({"action": "internal_response", "response": "done"})
    manager = agent_manager.AgentManager(clock=Clock())
    record = manager.spawn("read both")
    ask = ModelReplies([batch, finish])
    execute = Executor("file contents")
    with ReplyFormat("tags"):
        run_agent(record, manager, ask, execute)
    assert execute.actions == ["read_file", "read_file"], execute.actions
    assert ask.calls[1]["messages"][-1]["content"].count("read_file: file contents") == 2


def test_a_worker_is_corrected_in_the_words_of_tags():
    finish = tags({"action": "internal_response", "response": "done"})
    manager = agent_manager.AgentManager(clock=Clock())
    with ReplyFormat("tags"):
        # Unreadable.
        record = manager.spawn("one")
        ask = ModelReplies(["/write_file/ /path/ a.py //path/ /content/ x //write_file/",
                            finish])
        run_agent(record, manager, ask, Executor())
        complaint = ask.calls[1]["messages"][-1]["content"]
        assert complaint.startswith("INVALID: that reply could not be read as tags."), complaint
        assert "JSON" not in complaint.split("The parser said:")[0], complaint
        assert "/action_name/" in complaint, complaint
        # A refused verb: the sentence names the verb and the way out.
        record = manager.spawn("two")
        ask = ModelReplies([tags({"action": "git_push"}), finish])
        execute = Executor()
        run_agent(record, manager, ask, execute)
        refused = ask.calls[1]["messages"][-1]["content"]
        assert "REFUSED" in refused and "internal_response" in refused, refused
        assert execute.actions == [], execute.actions

        # An action that raised: the typing advice is the tag protocol's.
        def raising(obj, context):
            raise TypeError("start must be a number")

        record = manager.spawn("three")
        ask = ModelReplies([tags({"action": "read_lines", "path": "a.py",
                                  "start": "x"}), finish])
        run_agent(record, manager, ask, raising)
        raised = ask.calls[1]["messages"][-1]["content"]
        assert "true or false" in raised and "/item/" in raised, raised
        assert "unquoted" not in raised, raised


def test_a_worker_accepts_a_json_reply_under_tags_and_is_told_once():
    finish = tags({"action": "internal_response", "response": "done"})
    manager = agent_manager.AgentManager(clock=Clock())
    record = manager.spawn("read it")
    ask = ModelReplies([compact({"action": "read_file", "path": "a.py"}), finish])
    execute = Executor("contents")
    with ReplyFormat("tags"):
        assert run_agent(record, manager, ask, execute) == "done"
    assert execute.actions == ["read_file"], execute.actions
    told = ask.calls[1]["messages"][-1]["content"]
    assert told.endswith("\n" + agent_protocol.drift(TAGS, JSON)), told


# --- the reviewer --------------------------------------------------------------

def test_the_reviewer_under_tags_returns_its_json_verdict_inside_a_response_leaf():
    """The verdict stays JSON, inside the tag that carries it: the wire format
    and the verdict's format are two different things."""
    verdict = json.dumps(result_json(status="PASS", summary="It holds up."))
    for reply in (
            tags({"action": "internal_response", "progress": "Finished reviewing",
                  "response": verdict}),
            tags({"action": "internal_response", "response": verdict})):
        if "progress" in reply:
            assert "/response/" in reply, reply          # a /response/ leaf
        review = state_after_work()
        with ReplyFormat("tags"):
            with Reviewer(reply):
                out = review_through_dispatcher(
                    review, plan=worked_plan(),
                    manager=agent_manager.AgentManager())
        assert out.startswith("REVIEW PASSED"), out
        assert review.state == agent_review.PASSED, review.state


def test_the_reviewers_agenda_reminder_shows_a_tag_block_under_tags():
    verdict = tags({"action": "internal_response",
                    "response": json.dumps(result_json())})
    review = state_after_work()
    with ReplyFormat("tags"):
        with Reviewer(tags({"action": "read_file", "path": "no_such_file.txt"}),
                      verdict) as reviewer:
            review_through_dispatcher(review, plan=worked_plan(),
                                      manager=agent_manager.AgentManager())
    told = reviewer.briefs[1][-1]["content"]
    assert "/review_agenda/" in told, told
    assert '{"action":"review_agenda"' not in told, told
    with ReplyFormat("json"):
        assert agent_worker._agenda_missing() == (
            "\n\nReminder: you have not declared your agenda, and the person "
            "waiting can see that you are working but not what you are working "
            "through. Emit {\"action\":\"review_agenda\",\"operation\":\"create\","
            "\"items\":[...]} with the four to eight things you are checking, "
            "then carry on.")


# --- the hints embedded in results ---------------------------------------------

def test_the_hints_that_show_an_action_show_it_in_the_format_in_force():
    with ReplyFormat("json"):
        assert agent_actions.execute_action(
            {"action": "review_agenda", "operation": "show"}, None) == \
            agent_actions._REVIEW_AGENDA_ELSEWHERE
        refused = agent_actions.adopt_verb({"action": "run_file", "path": "x.zzz"})
        assert '{"action":"bash","command":"..."}' in refused[agent_actions.LEGACY_BLOCKED_KEY]
    with ReplyFormat("tags"):
        said = agent_actions.execute_action(
            {"action": "review_agenda", "operation": "show"}, None)
        assert "/review/ //review/" in said and '{"action"' not in said, said
        refused = agent_actions.adopt_verb({"action": "run_file", "path": "x.zzz"})
        blocked = refused[agent_actions.LEGACY_BLOCKED_KEY]
        assert "/bash/" in blocked and '{"action"' not in blocked, blocked


def test_multi_tool_says_its_shape_in_the_format_in_force():
    with ReplyFormat("json"):
        _templates, needs = agent_multi.entries({"action": "multi_tool", "calls": []})
        assert needs == agent_multi._NEEDS_CALLS
        _templates, loop = agent_multi.entries({"action": "multi_tool", "calls": [
            {"action": "end_conversation", "message": "x"}]})
        assert "\"actions\" batch" in loop, loop
    with ReplyFormat("tags"):
        _templates, needs = agent_multi.entries({"action": "multi_tool", "calls": []})
        assert "/calls/" in needs and "\"calls\"" not in needs, needs
        _templates, loop = agent_multi.entries({"action": "multi_tool", "calls": [
            {"action": "end_conversation", "message": "x"}]})
        assert "\"actions\"" not in loop and "own block" in loop, loop
        _templates, entry = agent_multi.entries({"action": "multi_tool",
                                                  "calls": ["read a.py"]})
        assert "JSON" not in entry and "action block" in entry, entry
