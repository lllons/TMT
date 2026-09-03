"""Tests for streaming model output and its incremental JSON parsing."""

import io
import json
import threading

import agent_model
import agent_prompt
import agent_ui
from TMT import stream_handler
from agent_live_renderer import LiveRelay
from agent_model import StreamingActionParser, StreamError, ask_model
from agent_ui import LiveUI
from test_agent_credentials import Credentials

RESPOND = '{"action": "respond", "message": "Hello world"}'


def chunk_text(text, size):
    return [text[index:index + size] for index in range(0, len(text), size)]


def fake_stream(chunks):
    """Replace the transport with a scripted sequence of content fragments."""
    def stream_chat(payload, on_usage=None):
        for chunk in chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            if callable(chunk):
                chunk = chunk()
            yield chunk
    return stream_chat


def collect(chunks, monkey=None):
    events = []
    original = agent_model.stream_chat
    agent_model.stream_chat = fake_stream(chunks)
    try:
        raw = ask_model([{"role": "user", "content": "hi"}], on_event=events.append)
    finally:
        agent_model.stream_chat = original
    return raw, events


def texts(events):
    return "".join(value for kind, value in events if kind == "text")


def test_stream_chunks_are_received_in_order():
    raw, events = collect(chunk_text(RESPOND, 7))
    assert json.loads(raw)["message"] == "Hello world"
    assert texts(events) == "Hello world"
    assert [kind for kind, _ in events][0] == "first_content"


def test_many_chunks_form_one_json_object():
    raw, events = collect(chunk_text(RESPOND, 1))
    assert json.loads(raw) == {"action": "respond", "message": "Hello world"}
    assert [kind for kind, _ in events].count("object") == 1


def test_single_large_chunk_works():
    raw, events = collect([RESPOND])
    assert json.loads(raw)["message"] == "Hello world"
    assert texts(events) == "Hello world"


def test_partial_json_does_not_raise():
    parser = StreamingActionParser()
    for chunk in chunk_text('{"action": "respond", "message": "half', 3):
        parser.feed(chunk)
    assert parser.result() is None
    assert parser.raw.endswith("half")


def test_empty_chunks_are_ignored():
    raw, events = collect(["", '{"action":', "", ' "done", "message": "ok"}', ""])
    assert json.loads(raw)["message"] == "ok"
    assert texts(events) == "ok"


def test_internal_control_data_is_not_relayed():
    reply = '{"action": "write_file", "path": "secret.txt", "content": "api-key-here"}'
    raw, events = collect(chunk_text(reply, 5))
    assert texts(events) == ""
    assert ("action", "write_file") in events
    assert json.loads(raw)["content"] == "api-key-here"


def test_batch_relays_only_the_user_facing_message():
    reply = ('{"actions": [{"action": "write_file", "path": "a.txt", "content": "xyz"},'
             ' {"action": "respond", "message": "Wrote the file."}]}')
    raw, events = collect(chunk_text(reply, 9))
    assert texts(events) == "Wrote the file."
    assert json.loads(raw)["actions"][1]["message"] == "Wrote the file."


def test_multiline_code_block_and_unicode_survive_exactly():
    message = "Here you go:\n```py\nprint('héllo 😀')\n```\nDone — 你好"
    reply = json.dumps({"action": "respond", "message": message})
    raw, events = collect(chunk_text(reply, 3))
    assert json.loads(raw)["message"] == message
    assert texts(events) == message


def test_escape_sequences_split_across_chunks():
    reply = '{"action": "respond", "message": "a\\' + 'u00e9\\nb"}'
    raw, events = collect(chunk_text(reply, 1))
    assert json.loads(raw)["message"] == "aé\nb"
    assert texts(events) == "aé\nb"


def test_first_content_stops_thinking_and_final_json_reaches_95():
    output = io.StringIO()
    ui = LiveUI(stream=output, interval=0.01)
    relay = LiveRelay(stream=output, ansi=False)
    ui.attach_sink(relay.set_status)
    ui.start()
    assert not ui.progress_started
    original = agent_model.stream_chat
    agent_model.stream_chat = fake_stream(chunk_text(RESPOND, 4))
    try:
        raw = ask_model([{"role": "user", "content": "hi"}],
                        on_event=stream_handler(ui, relay, {"error": None}))
    finally:
        agent_model.stream_chat = original
    assert ui.progress_started
    assert relay.glitch.exact_text() == "Hello world"
    ui.final_event()
    assert ui._progress == 95
    relay.finish()
    ui.attach_sink(None)
    ui.complete()
    assert ui._progress == 100
    assert json.loads(raw)["message"] == "Hello world"


def test_live_output_appears_before_the_stream_finishes():
    relay = LiveRelay(stream=io.StringIO(), ansi=True)
    ui = LiveUI(stream=io.StringIO(), interval=0.01)
    ui.start()
    seen = {}

    def late_chunk():
        seen["mid_stream_text"] = relay.glitch.exact_text()
        return '"}'

    chunks = ['{"action": "respond", "message": "Live', ' text', late_chunk]
    original = agent_model.stream_chat
    agent_model.stream_chat = fake_stream(chunks)
    try:
        ask_model([{"role": "user", "content": "hi"}],
                  on_event=stream_handler(ui, relay, {"error": None}))
    finally:
        agent_model.stream_chat = original
    relay.finish()
    ui.stop()
    assert seen["mid_stream_text"] == "Live text"


def test_stream_error_after_content_reports_and_does_not_complete():
    output = io.StringIO()
    ui = LiveUI(stream=output, interval=0.01)
    relay = LiveRelay(stream=output, ansi=True)
    ui.attach_sink(relay.set_status)
    ui.start()
    state = {"error": None}
    chunks = ['{"action": "respond", "message": "part', StreamError("connection closed")]
    original = agent_model.stream_chat
    agent_model.stream_chat = fake_stream(chunks)
    try:
        raw = ask_model([{"role": "user", "content": "hi"}],
                        on_event=stream_handler(ui, relay, state))
    finally:
        agent_model.stream_chat = original
    assert state["error"] == "connection closed"
    assert relay.abort() == "part"
    ui.stop()
    assert "100% Complete!" not in output.getvalue()
    assert "OpenRouter error" in json.loads(raw)["message"]


def test_stream_failure_before_content_falls_back_to_a_single_request():
    calls = {"post": 0}

    def fake_post(payload, spinner=True):
        calls["post"] += 1
        return {"choices": [{"message": {"content": RESPOND}}]}, None

    original_stream, original_post = agent_model.stream_chat, agent_model._post_chat
    agent_model.stream_chat = fake_stream([StreamError("HTTP 400: no streaming")])
    agent_model._post_chat = fake_post
    try:
        raw = ask_model([{"role": "user", "content": "hi"}], on_event=lambda event: None)
    finally:
        agent_model.stream_chat, agent_model._post_chat = original_stream, original_post
    assert calls["post"] == 1
    assert json.loads(raw)["message"] == "Hello world"


def test_non_streaming_path_is_unchanged_without_a_handler():
    def fake_post(payload, spinner=True):
        assert "stream" not in payload
        return {"choices": [{"message": {"content": "noise " + RESPOND + " trailing"}}]}, None

    original = agent_model._post_chat
    agent_model._post_chat = fake_post
    try:
        raw = ask_model([{"role": "user", "content": "hi"}])
    finally:
        agent_model._post_chat = original
    assert json.loads(raw)["message"] == "Hello world"


def test_empty_stream_returns_the_empty_response_reply():
    raw, events = collect([])
    assert json.loads(raw)["message"] == "empty response from model"
    assert events == []


def test_incomplete_stream_is_salvaged_without_duplication():
    raw, events = collect(['{"action": "respond", "message": "cut off'])
    assert json.loads(raw)["message"] == "invalid JSON structure"
    assert texts(events) == "cut off"


def test_keyboard_interrupt_leaves_no_live_workers():
    before = {thread.name for thread in threading.enumerate()}
    ui = LiveUI(stream=io.StringIO(), interval=0.01)
    relay = LiveRelay(stream=io.StringIO(), ansi=True)
    ui.attach_sink(relay.set_status)
    ui.start()
    chunks = ['{"action": "respond", "message": "typing', KeyboardInterrupt()]
    original = agent_model.stream_chat
    agent_model.stream_chat = fake_stream(chunks)
    interrupted = False
    try:
        ask_model([{"role": "user", "content": "hi"}],
                  on_event=stream_handler(ui, relay, {"error": None}))
    except KeyboardInterrupt:
        interrupted = True
        relay.abort()
        ui.stop()
    finally:
        agent_model.stream_chat = original
    assert interrupted
    remaining = {thread.name for thread in threading.enumerate()} - before
    assert not remaining, remaining


def total_counted(events):
    """The token figure a LiveUI would show for this run of events."""
    ui = LiveUI(stream=io.StringIO())
    ui.start()
    ui.meaningful_output()
    for kind, value in events:
        if kind == "output":
            ui.add_output(value)
        elif kind == "usage":
            ui.settle_tokens(value)
    with ui._lock:
        total = ui._token_total()
    ui.stop()
    return total


def test_counter_includes_the_json_plumbing_not_just_the_message():
    """A write_file reply is mostly path and file content. All of it counts."""
    reply = ('{"action":"write_file","path":"notes.txt",'
             '"content":"line one\\nline two\\nline three","message":"ok"}')
    _, events = collect(chunk_text(reply, 5))
    counted = sum(value for kind, value in events if kind == "output")
    assert counted == len(reply)
    relayed = sum(len(value) for kind, value in events if kind == "text")
    # The user-facing text is a small fraction of what the model generated.
    assert relayed < counted / 3


def test_every_generated_character_is_counted_once():
    _, events = collect(chunk_text(RESPOND, 3))
    assert sum(value for kind, value in events if kind == "output") == len(RESPOND)


def test_provider_usage_supersedes_the_character_estimate():
    """An exact count replaces the estimate rather than adding to it."""
    events = [("output", 4000), ("usage", 137)]
    assert total_counted(events) == 137


def test_requests_that_report_usage_and_requests_that_do_not_both_count():
    """First request settles exactly, the second is still being estimated."""
    events = [("output", 400), ("usage", 90), ("output", 40)]
    assert total_counted(events) == 90 + 10


def test_the_blocking_transport_counts_its_output_too():
    """A reply that never streamed still reaches the counter, exactly."""
    reply = '{"action":"respond","message":"no streaming here"}'
    original_stream, original_post = agent_model.stream_chat, agent_model._post_chat
    agent_model.stream_chat = fake_stream([StreamError("HTTP 400: no streaming")])
    agent_model._post_chat = lambda payload, spinner=True: (
        {"choices": [{"message": {"content": reply}}],
         "usage": {"completion_tokens": 11}}, None)
    events = []
    try:
        raw = ask_model([{"role": "user", "content": "hi"}], on_event=events.append)
    finally:
        agent_model.stream_chat, agent_model._post_chat = original_stream, original_post
    assert json.loads(raw)["action"] == "respond"
    assert ("output", len(reply)) in events
    assert ("usage", 11) in events
    assert total_counted(events) == 11


def test_a_usage_record_in_the_stream_is_not_relayed_as_content():
    """The usage chunk must reach the counter without polluting the reply."""
    original = agent_model.stream_chat

    def stream_with_usage(payload, on_usage=None):
        for piece in chunk_text(RESPOND, 6):
            yield piece
        if on_usage:
            on_usage(29)

    agent_model.stream_chat = stream_with_usage
    events = []
    try:
        raw = ask_model([{"role": "user", "content": "hi"}], on_event=events.append)
    finally:
        agent_model.stream_chat = original
    assert json.loads(raw) == json.loads(RESPOND)
    assert ("usage", 29) in events
    assert total_counted(events) == 29


class EncodingSensitiveResponse:
    """A stand-in that decodes the way requests actually decodes.

    requests assigns ISO-8859-1 to any text/* response without a charset, and
    iter_lines(decode_unicode=True) honours that. Reproducing the fallback here
    is the whole point: a fake that always decodes UTF-8 would pass whether or
    not the bug is present.
    """

    def __init__(self, body, encoding="ISO-8859-1"):
        self.body = body
        self.encoding = encoding
        self.status_code = 200
        self.text = ""

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=True):
        for line in self.body.split(b"\n"):
            if decode_unicode and self.encoding:
                yield line.decode(self.encoding, "replace")
            else:
                yield line

    def close(self):
        pass


def test_a_multibyte_character_survives_the_stream_intact():
    """An em dash must arrive as an em dash.

    The stream is UTF-8, but requests defaults text/* without a charset to
    ISO-8859-1. Decoded that way, every multi-byte character becomes several
    latin-1 characters, gets written back out as UTF-8, and reaches the user's
    files as bytes nothing can read. It is silent and it is not recoverable
    afterwards, so it has to be prevented here.
    """
    # The provider sends real UTF-8 bytes; ensure_ascii=False below keeps
    # them that way. An escaped \uXXXX would be pure ASCII with nothing
    # left to mis-decode, and the test could not fail.
    reply = '{"action":"respond","message":"an em dash \u2014 and an accent \u00e9"}'
    body = b"".join(
        b'data: ' + json.dumps({"choices": [{"delta": {"content": piece}}]}, ensure_ascii=False).encode("utf-8") + b"\n"
        for piece in (reply[:30], reply[30:])
    ) + b"data: [DONE]\n"

    # A credential of the test's own. `stream_chat` resolves one and refuses
    # before it posts, so with the transport replaced and nothing else done
    # this test measured whether the machine running it happened to have a key
    # -- green on a developer's checkout, StreamError on a fresh clone. The
    # key here is never sent: the only thing that could send it has just been
    # replaced two lines down.
    original = agent_model._session.post
    agent_model._session.post = lambda *a, **k: EncodingSensitiveResponse(body)
    try:
        with Credentials():
            received = "".join(agent_model.stream_chat({"model": "x"}))
    finally:
        agent_model._session.post = original

    assert "\u2014" in received, repr(received)
    assert "\u00e9" in received, repr(received)
    # The signature of the fault: an em dash read as latin-1 becomes these.
    assert "\u00e2\u0080\u0094" not in received, repr(received)
    assert json.loads(received)["message"] == "an em dash \u2014 and an accent \u00e9"


# --- progress, events and next_step: the optional response-history fields ---


def feed_events(text, size=1):
    """Drive a parser directly, one chunk of ``size`` characters at a time."""
    parser = StreamingActionParser()
    events = []
    for chunk in chunk_text(text, size):
        events.extend(parser.feed(chunk))
    return parser, events


def values(events, kind):
    return [value for name, value in events if name == kind]


def test_progress_is_emitted_before_the_object_completes():
    """The point of the field: it reaches the screen while the action is
    still being generated, not when the reply lands."""
    reply = '{"action":"read_file","path":"a.txt","progress":"Reading a.txt now."}'
    parser = StreamingActionParser()
    progress_at, object_at = None, None
    for index, char in enumerate(reply):
        for kind, value in parser.feed(char):
            if kind == "progress":
                progress_at = index
                assert value == "Reading a.txt now."
            elif kind == "object":
                object_at = index
    assert progress_at is not None, "no progress event was emitted"
    assert object_at is not None
    assert progress_at < object_at, (progress_at, object_at)
    assert json.loads(parser.result())["progress"] == "Reading a.txt now."


def test_progress_and_next_step_never_appear_in_the_text_stream():
    reply = ('{"action":"respond","progress":"Summarising the change.",'
             '"message":"All set.","next_step":"Add a chart"}')
    for size in (1, 4, 13, len(reply)):
        _, events = feed_events(reply, size)
        assert texts(events) == "All set.", size
        assert values(events, "progress") == ["Summarising the change."], size
        assert values(events, "next_step") == ["Add a chart"], size


def test_no_protocol_json_leaks_into_the_text_stream():
    reply = ('{"action":"write_file","path":"a.txt","content":"x",'
             '"progress":"Writing a.txt.","events":[{"type":"file_create",'
             '"message":"Created a.txt","stage":"apply"}]}')
    _, events = feed_events(reply, 1)
    assert texts(events) == ""
    assert values(events, "progress") == ["Writing a.txt."]


def test_a_progress_key_inside_file_content_is_not_emitted():
    """File content is arbitrary user data and often holds JSON of its own."""
    content = '{"progress": "from the file", "next_step": "from the file"}'
    reply = json.dumps({"action": "write_file", "path": "data.json",
                        "content": content, "progress": "Writing data.json."})
    _, events = feed_events(reply, 1)
    assert values(events, "progress") == ["Writing data.json."]
    assert values(events, "next_step") == []


def test_a_progress_key_inside_a_files_entry_is_not_emitted():
    reply = ('{"action":"write_files","files":['
             '{"path":"a.json","content":"x","progress":"from the file"},'
             '{"path":"b.json","content":"y","next_step":"from the file"}],'
             '"progress":"Writing two files."}')
    _, events = feed_events(reply, 1)
    assert values(events, "progress") == ["Writing two files."]
    assert values(events, "next_step") == []


def test_progress_inside_a_batch_action_is_not_streamed():
    """Only a top-level key streams. Per-action fields inside an "actions"
    array are read from the finished object, the same way "events" is."""
    reply = ('{"actions":[{"action":"read_file","path":"a.txt",'
             '"progress":"Reading a.txt."},'
             '{"action":"respond","message":"Done.","next_step":"Edit a.txt"}]}')
    parser, events = feed_events(reply, 1)
    assert values(events, "progress") == []
    assert values(events, "next_step") == []
    assert texts(events) == "Done."
    assert json.loads(parser.result())["actions"][1]["next_step"] == "Edit a.txt"


def test_next_step_is_emitted_from_a_final_action():
    for reply in ('{"action":"respond","message":"Done.","next_step":"Run the tests"}',
                  '{"action":"done","next_step":"Run the tests"}'):
        _, events = feed_events(reply, 1)
        assert values(events, "next_step") == ["Run the tests"], reply


def test_an_action_without_the_new_fields_emits_exactly_what_it_did_before():
    for size in (1, 3, 7, len(RESPOND)):
        _, events = feed_events(RESPOND, size)
        kinds = [kind for kind, _ in events]
        assert kinds[0] == "action", size
        assert kinds[-1] == "object", size
        assert set(kinds[1:-1]) == {"text"}, (size, kinds)
        assert texts(events) == "Hello world", size


def test_the_events_array_is_not_streamed():
    """Deliberate: an entry only means anything once its object closes."""
    reply = ('{"action":"respond","message":"Green.","events":['
             '{"type":"test","message":"Ran 173 tests"},'
             '{"type":"success","message":"173 tests passed"}],'
             '"next_step":"Commit the fix"}')
    parser, events = feed_events(reply, 1)
    assert [kind for kind, _ in events if kind not in
            ("text", "action", "next_step", "object")] == []
    assert texts(events) == "Green."
    assert len(json.loads(parser.result())["events"]) == 2


def test_progress_survives_unicode_escapes_split_one_character_at_a_time():
    reply = ('{"action":"respond","progress":"caf\\u00e9 \\u2014 checking",'
             '"message":"ok"}')
    parser, events = feed_events(reply, 1)
    assert values(events, "progress") == ["café — checking"]
    assert texts(events) == "ok"
    assert json.loads(parser.result())["message"] == "ok"


def test_truncated_json_keeps_the_events_that_already_parsed():
    reply = '{"action":"respond","progress":"Halfway there.","message":"cut'
    parser, events = feed_events(reply, 1)
    assert values(events, "progress") == ["Halfway there."]
    assert texts(events) == "cut"
    assert parser.result() is None


def test_invalid_json_around_the_new_fields_does_not_raise():
    reply = '{"action":"respond",,,"progress":"Still fine.",}'
    parser, events = feed_events(reply, 1)
    assert values(events, "progress") == ["Still fine."]
    assert parser.complete_json is not None


def test_an_unknown_event_type_does_not_break_the_stream():
    reply = ('{"action":"respond","message":"hi","events":['
             '{"type":"not_a_real_type","message":"m"}],"next_step":"Try again"}')
    parser, events = feed_events(reply, 1)
    assert texts(events) == "hi"
    assert values(events, "next_step") == ["Try again"]
    assert json.loads(parser.result())["events"][0]["type"] == "not_a_real_type"


def test_a_non_string_progress_is_ignored_rather_than_emitted():
    reply = '{"action":"done","progress":123,"next_step":null,"message":"ok"}'
    parser, events = feed_events(reply, 1)
    assert values(events, "progress") == []
    assert values(events, "next_step") == []
    assert texts(events) == "ok"
    assert json.loads(parser.result())["progress"] == 123


def test_an_empty_progress_value_is_not_emitted():
    reply = '{"action":"done","progress":"","message":"ok"}'
    _, events = feed_events(reply, 1)
    assert values(events, "progress") == []


def test_progress_and_next_step_reach_the_caller_through_ask_model():
    reply = ('{"action":"respond","progress":"Writing the summary.",'
             '"message":"All set.","next_step":"Add a chart"}')
    raw, events = collect(chunk_text(reply, 5))
    assert ("progress", "Writing the summary.") in events
    assert ("next_step", "Add a chart") in events
    assert texts(events) == "All set."
    assert json.loads(raw)["next_step"] == "Add a chart"


def test_the_new_events_do_not_disturb_the_live_reply():
    """Whatever the interface does with them, the streamed reply is still the
    message and nothing else. Rendering them is a separate job."""
    ui = LiveUI(stream=io.StringIO(), interval=0.01)
    relay = LiveRelay(stream=io.StringIO(), ansi=False)
    ui.attach_sink(relay.set_status)
    ui.start()
    state = {"error": None, "progress_seen": set(), "next_step": None}
    handle = stream_handler(ui, relay, state)
    try:
        for event in (("first_content", ""), ("progress", "Reading a.txt."),
                      ("text", "Done."), ("next_step", "Run the tests"),
                      ("object", "{}")):
            handle(event)
    finally:
        relay.finish()
        ui.attach_sink(None)
        ui.stop()
    assert relay.glitch.exact_text() == "Done."
    assert state["error"] is None


def test_the_new_fields_do_not_change_validation():
    plain = {"action": "read_file", "path": "a.txt"}
    decorated = dict(plain, progress="Reading a.txt.",
                     events=[{"type": "file_read", "message": "Read a.txt"}])
    assert agent_prompt.validate_action(plain) is None
    assert agent_prompt.validate_action(decorated) is None
    assert agent_prompt.validate_action(
        {"action": "end_conversation", "message": "ok",
         "next_step": "Run the tests"}) is None
    # The other verb that talks, decorated the same way. Neither of them is a
    # special case in the validator and neither has ever needed to be.
    assert agent_prompt.validate_action(
        {"action": "send_message", "message": "ok",
         "progress": "Saying what is next."}) is None
    # A missing required key is still a missing required key.
    assert agent_prompt.validate_action(
        {"action": "read_file", "progress": "Reading."}) is not None


def prompt_rules():
    """The prompt's own instructions, without the workspace snapshot."""
    return agent_prompt.get_system_prompt().split("=== CURRENT WORKSPACE FILES")[0]


def test_the_system_prompt_requires_a_closing_summary_of_what_was_made():
    """The end_conversation message is the only thing the user ever reads, so a
    turn that ends without one has failed however much work it did -- and one
    that ends with "done" has told them nothing. The prompt has to say both, in
    the rules and in the behaviour section, because a model that skims one may
    still read the other."""
    rules = prompt_rules()
    assert "You HAVE to end every task with an end_conversation action" in rules, rules
    assert "YOU MUST FINISH BY SUMMARISING WHAT YOU MADE" in rules, rules
    # What a summary is: the files, and what was run and reported.
    for phrase in ("which files you created, changed or deleted",
                   "what you ran and what it reported",
                   "Name the files"):
        assert phrase in rules, phrase
    # And what it is not, shown rather than only described.
    assert '{"action":"end_conversation","message":"Finished."}' in rules, rules
    assert "A task that changed nothing still ends with an end_conversation" in rules

    # The wrong examples are wrong on purpose and must not be mistaken for the
    # right ones: every example in the section is still valid JSON and a valid
    # action, whichever side of the line it is illustrating.
    for line in agent_prompt.WORKFLOW_RULES.splitlines():
        line = line.strip()
        for marker in ("WRONG: ", "RIGHT: "):
            if line.startswith(marker):
                obj = json.loads(line[len(marker):])
                assert agent_prompt.validate_action(obj) is None, line


def test_the_system_prompt_teaches_progress_events_and_next_step():
    rules = prompt_rules()
    assert "=== PROGRESS, EVENTS AND NEXT STEP" in rules
    assert '"progress"' in rules and '"events"' in rules and '"next_step"' in rules
    assert "PUBLIC" in rules
    assert "NOT your private reasoning" in rules
    assert "chain-of-thought" in rules
    # Four is the ask, stated as a number and again as a count the model
    # can check its own line against.
    assert "FOUR WORDS" in rules
    assert "Not five" in rules
    assert "Count them" in rules
    # A progress line on every action that does work. This used to say the
    # opposite -- progress at milestones only, not on every action -- and the
    # reversal is deliberate, so it is asserted rather than left to drift back.
    assert 'Put a "progress" on EVERY action that does work' in rules
    # Repeating an action is allowed. Repeating it SILENTLY is not: the second
    # one has to say what is different about it, or the two are
    # indistinguishable from a stuck loop.
    assert "You MAY use the same action twice in a row" in rules
    assert "may NOT do is repeat it silently" in rules
    assert "must say what is DIFFERENT about this use" in rules
    assert "Never write a sentence you have already written" in rules
    # The two verbs that talk are exempt: they are already the thing said.
    assert "send_message and end_conversation are the exceptions" in rules
    assert "Never put a credential" in rules
    assert "never treated as their next message" in rules
    assert "never claim anything was done" in rules
    for event_type in agent_prompt.EVENT_TYPES:
        assert event_type in rules, event_type


def test_every_new_field_example_in_the_prompt_is_valid_and_obeys_its_own_rules():
    carrying = {"progress": 0, "events": 0, "next_step": 0}
    examples = 0
    for line in agent_prompt.PROGRESS_RULES.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        examples += 1
        obj = json.loads(line)          # an unparseable example is a broken example
        for entry in obj.get("actions", [obj]):
            assert agent_prompt.validate_action(entry) is None, line
            for field in carrying:
                if field in entry:
                    carrying[field] += 1
            for event in entry.get("events", []):
                assert event["type"] in agent_prompt.EVENT_TYPES, line
                assert event["message"], line
            if "next_step" in entry:
                # Only the ending carries one, which is what rule 8 of that
                # same section says: a send_message means the task is not over
                # and there is nothing yet to suggest.
                assert entry["action"] == "end_conversation", line
                # Four is the ask, and the prompt's own examples are
                # where a model would otherwise learn that five is fine.
                assert agent_ui.count_words(entry["next_step"]) <= (
                    agent_ui.TARGET_SUGGESTION_WORDS), line
    assert examples >= 6, examples
    assert all(count >= 2 for count in carrying.values()), carrying


def test_the_prompt_still_carries_the_rules_that_came_before():
    """Guard against the new section being pasted over an existing one."""
    rules = prompt_rules()
    for heading in ("=== OUTPUT FORMAT - ABSOLUTE RULES ===",
                    "=== ACTIONS - REQUIRED KEYS AND AN EXAMPLE OF EACH ===",
                    "=== EDITING PREFERENCES - FOLLOW IN THIS ORDER ===",
                    "=== BEHAVIOUR ===",
                    "=== GIT ==="):
        assert heading in rules, heading
    assert "NEVER use write_file on a file that already exists" in rules
    assert "Never tell the user to run git config" in rules
    assert "Every task ends with an end_conversation action" in rules
    assert "Output EXACTLY ONE JSON object and nothing else" in rules


# --- a reply TMT had to make up, and one the model wrote in prose ------------
#
# Every failure in agent_model has to come back as a valid action, because the
# agent loop has no other shape to receive one in. That is right for the screen
# and wrong for the record: the sentence in such a reply is a machine's report
# about a failure, not the model's account of the work, and it used to be
# written into the session as the assistant's answer -- so the NEXT turn was
# told the model had said "no JSON object found in response".

def test_a_reply_tmt_made_up_says_so_and_is_still_a_usable_action():
    """The loop has to be able to act on it and the session has to be able to
    tell it apart. Both, from the same object."""
    for raw in (agent_model._extract_json(""),
                agent_model._extract_json("   "),
                agent_model._extract_json('{"action":"end_conversation"'),
                agent_model._error_reply("HTTP 429 rate limited")):
        obj = json.loads(raw)
        # The verb that ends turns is the verb TMT ends one with. It used to
        # fabricate `done`, which no longer exists; a fabricated reply that the
        # loop's terminal test did not recognise would leave a failed provider
        # call running the turn on instead of reporting it.
        assert obj["action"] == "end_conversation", obj
        assert obj["message"], obj
        assert agent_model.is_synthetic(obj), obj
        assert agent_prompt.validate_action(obj) is None, obj

    # A reply the model actually sent is not marked, whatever it says.
    real = json.loads(agent_model._extract_json(
        '{"action":"end_conversation","message":"ok"}'))
    assert agent_model.is_synthetic(real) is False, real
    assert agent_model.is_synthetic(None) is False
    assert agent_model.is_synthetic("end_conversation") is False


def test_prose_where_json_was_asked_for_is_shown_as_the_answer():
    """The model wrote a sentence instead of JSON. The work it describes has
    usually already happened -- the actions ran on earlier rounds of the loop
    -- so the sentence is the summary of that work. Replacing it with "no JSON
    object found in response" told the user nothing at all about a task that
    had in fact been done, and then carried that non-answer into the next
    turn as though the model had said it."""
    reply = "I committed the change and pushed it to main."
    obj = json.loads(agent_model._extract_json(reply))
    assert obj["action"] == "end_conversation"
    assert obj["message"] == reply, obj
    # The model's own words, so not marked as something TMT made up.
    assert agent_model.is_synthetic(obj) is False, obj

    # Long prose is cut, and says it was cut rather than just stopping.
    long_reply = "word " * 2000
    cut = json.loads(agent_model._extract_json(long_reply))["message"]
    assert len(cut) <= agent_model.PROSE_REPLY_LIMIT + 8, len(cut)
    assert cut.endswith("[…]"), cut[-20:]

    # Prose wrapped around a JSON object is still the JSON object.
    mixed = json.loads(agent_model._extract_json(
        'Sure! {"action":"end_conversation","message":"ok"} hope that helps'))
    assert mixed == {"action": "end_conversation", "message": "ok"}, mixed


def test_the_system_prompt_is_sent_as_one_cacheable_block():
    """A turn takes several steps and the API is stateless, so the whole
    system prompt -- the largest thing in the request by far -- goes again on
    every step. It cannot be sent less often; what it can be is marked, so the
    provider charges for reading the prefix back rather than for reading it
    afresh.

    The breakpoint changes nothing about what is sent, and the readout does
    not pretend the count drops. This asserts the shape the API needs, since
    a `system` field of the wrong type is rejected outright and there is no
    live request here to find that out."""
    import agent_providers
    provider = agent_providers.get_provider("anthropic")
    messages = [{"role": "system", "content": "the rules"},
                {"role": "user", "content": "list the files"}]
    _url, payload = provider.chat_payload(messages, model="claude-sonnet-5")

    assert payload["system"] == [{"type": "text", "text": "the rules",
                                  "cache_control": {"type": "ephemeral"}}], payload["system"]
    # Lifted out, not left in the conversation: a system role inside
    # "messages" is rejected by this API.
    assert [message["role"] for message in payload["messages"]] == ["user"], payload["messages"]
    # It must survive being serialised -- that is the only form it is ever
    # sent in.
    assert json.loads(json.dumps(payload))["system"][0]["cache_control"] == {"type": "ephemeral"}

    # No system prompt, no field at all, rather than an empty block the API
    # would have to reject.
    _url, bare = provider.chat_payload([{"role": "user", "content": "hi"}],
                                       model="claude-sonnet-5")
    assert "system" not in bare, bare


def test_an_error_reply_names_the_provider_that_actually_failed():
    """It said "OpenRouter" whichever of the four had been called, which is a
    false statement about where an error came from."""
    import agent_credentials, os as _os
    previous = _os.environ.get("TMT_PROVIDER")
    try:
        for provider_id, label in (("anthropic", "Anthropic"), ("gemini", "Gemini")):
            _os.environ["TMT_PROVIDER"] = provider_id
            agent_credentials.credential = lambda pid: "test-key"
            message = json.loads(agent_model._error_reply("HTTP 500"))["message"]
            assert message.startswith(label), (provider_id, message)
            assert "HTTP 500" in message, message
    finally:
        _os.environ.pop("TMT_PROVIDER", None)
        if previous is not None:
            _os.environ["TMT_PROVIDER"] = previous


def test_every_worked_example_is_exactly_what_the_model_should_emit():
    """The worked examples are the prompt's main teaching device: a model that
    reads nothing else copies the shape of the nearest one. So each has to be
    valid JSON, a valid action, and obey every rule the prompt states
    elsewhere -- an example that broke a rule would teach breaking it.

    The BAD lines are excluded by construction: they are the ones that are not
    valid, which is the point of them."""
    lines = [line.strip() for line in agent_prompt.ANSWERING_EXAMPLES.splitlines()]
    examples = [line[len("You emit:"):].strip() for line in lines
                if line.startswith("You emit:")]
    assert len(examples) >= 12, len(examples)

    ending_seen = 0
    for line in examples:
        obj = json.loads(line)                    # an unparseable example is broken
        entries = obj.get("actions", [obj])
        assert entries, line
        for entry in entries:
            assert agent_prompt.validate_action(entry) is None, line
            if "next_step" in entry:
                assert entry["action"] == "end_conversation", line
                # The prompt asks for four words; its own examples must not be
                # the place a model learns that five is fine.
                assert agent_ui.count_words(entry["next_step"]) <= \
                    agent_ui.TARGET_SUGGESTION_WORDS, line
                assert not entry["next_step"].rstrip().endswith((".", "?", "!")), line
            for event in entry.get("events", []):
                assert event["type"] in agent_prompt.EVENT_TYPES, line
        if entries[-1]["action"] == "end_conversation":
            ending_seen += 1
        # A batch that does work must finish it, exactly as the rules say.
        if len(entries) > 1:
            assert entries[-1]["action"] == "end_conversation", line
    assert ending_seen >= 8, ending_seen


def test_the_prompt_says_plainly_that_prose_reaches_nobody():
    """The header used to open with "You chat naturally with the user", which
    is exactly what a capable model then did -- it read a licence to answer in
    prose and took it. Conversation happens INSIDE the message field; that has
    to be the first thing the prompt establishes, not a rule buried at nine."""
    rules = prompt_rules()
    assert "chat naturally with the user" not in rules, rules[:400]
    assert "It goes to a JSON parser" in rules, rules[:400]
    assert "you are not writing TO the user" in rules
    assert "There is no situation, none, in which the right answer is text outside JSON" in rules
    # Every kind of turn the model might think is an exception is named as one
    # that is not: a greeting, a refusal, and a question back.
    assert "A greeting is an end_conversation whose message is a greeting" in rules
    assert "A refusal is an end_conversation whose message explains why" in rules
    assert ("A question back to the user is an end_conversation whose message "
            "asks it") in rules
    # And the worked examples cover those same cases, so the rule is shown as
    # well as stated.
    examples = agent_prompt.ANSWERING_EXAMPLES
    for situation in ("greets you or makes small talk", "You will not do it",
                      "There was nothing to do", "Something failed",
                      "need something from the user first",
                      "refers to something from earlier in this session"):
        assert situation in examples, situation
