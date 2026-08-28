"""Tests for streaming model output and its incremental JSON parsing."""

import io
import json
import threading

import agent_model
from TMT import stream_handler
from agent_live_renderer import LiveRelay
from agent_model import StreamingActionParser, StreamError, ask_model
from agent_ui import LiveUI

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
