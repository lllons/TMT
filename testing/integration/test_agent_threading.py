"""Tests for calling a model off the main thread, and for writing from several.

Three separate pieces of work meet here, and they meet because they are the
same problem seen from three sides: TMT is about to run more than one agent at
once, and everything it does today assumes there is exactly one.

  * ``ask_model`` read the model, the reply ceiling and the effort setting from
    module-level globals and printed a spinner whenever nobody was streaming.
    A background worker has its own model and its own ceiling, and a spinner
    painted from a background thread lands on top of the session's live region
    and moves the rows the next repaint is aiming at. Hence ``model=``,
    ``max_tokens=`` and ``quiet=``.

  * Nothing anywhere parsed a prompt-token count, which is why TMT's own corner
    meter has always marked its input figure ``~``. Every provider reports one,
    under a different name each; ``parse_stream_input`` is where that lives.

  * The file primitives are read-modify-write and were written for one caller.
    ``WRITE_LOCK`` makes any single write atomic.

Everything is driven through the public entry points rather than around them,
because a stand-in that bypasses ``ask_model`` proves nothing about the call a
worker will actually make.
"""

import io
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path

import agent_config
import agent_file_ops
import agent_model
import agent_providers


# --- harnesses ---------------------------------------------------------------


def remove_tree(path):
    """Delete a temp tree, including read-only files on Windows."""
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


class Workspace:
    """A throwaway directory pointed at by agent_config.ROOT_DIR.

    close() restores the previous root and must run in a finally block: a
    leaked root would point every later test at a deleted directory.
    """

    def __init__(self, files=None):
        self.previous_root = agent_config.ROOT_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_threading_")).resolve()
        for name, body in (files or {}).items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body.encode("utf-8"))
        agent_config.set_workspace_root(self.path)

    def read(self, name):
        return (self.path / name).read_text(encoding="utf-8")

    def close(self):
        agent_config.ROOT_DIR = self.previous_root
        remove_tree(self.path)


class FakeStatus:
    """What console.status returns: a context manager that paints a spinner.

    Recorded rather than suppressed, because "quiet printed nothing" is only
    worth asserting next to "the same call without quiet printed something".
    """

    def __init__(self, console, text):
        self.console = console
        self.text = text

    def __enter__(self):
        self.console.statuses.append(self.text)
        return self

    def __exit__(self, *exc):
        return False


class FakeConsole:
    """A console that records instead of writing, so a test can see the writes.

    Capturing stdout alone would not do: rich writes through its own file
    handle, and a console that had been swapped for a real one pointed
    somewhere else would still be a console the worker path reached.
    """

    def __init__(self):
        self.statuses = []
        self.prints = []

    def status(self, text, **kwargs):
        return FakeStatus(self, text)

    def print(self, *objects, **kwargs):
        self.prints.append(" ".join(str(obj) for obj in objects))

    def wrote_anything(self):
        return bool(self.statuses or self.prints)


class Response:
    """The parts of an HTTP response the provider adapters actually read."""

    def __init__(self, body, status_code=200, lines=None):
        self.text = body
        self.status_code = status_code
        self.encoding = "utf-8"
        self._lines = lines

    def iter_lines(self, decode_unicode=True):
        for line in self._lines or ():
            yield line

    def close(self):
        pass


class Session:
    """agent_config._session, replaced. Answers one canned response."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def with_session(response):
    """Swap the shared HTTP session for one that answers ``response``."""
    previous = agent_config._session
    agent_config._session = Session(response)
    return previous


def sse(events):
    """The SSE lines a provider would send for these event objects."""
    return ["data: " + json.dumps(event) for event in events] + ["data: [DONE]"]


def capture_post_chat(reply, usage=None, errors=()):
    """Replace _post_chat with a recorder. Returns (restore, calls).

    ``errors`` is a list of error strings to return before succeeding, which
    is how the JSON-mode rejection path is reached without a provider.
    """
    calls = []
    remaining = list(errors)
    original = agent_model._post_chat

    def fake_post(payload, spinner=True):
        calls.append({"payload": payload, "spinner": spinner})
        if remaining:
            return None, remaining.pop(0)
        data = {"choices": [{"message": {"content": reply}}]}
        if usage:
            data["usage"] = dict(usage)
        return data, None

    agent_model._post_chat = fake_post

    def restore():
        agent_model._post_chat = original
    return restore, calls


REPLY = '{"action": "respond", "message": "ok"}'


# --- ask_model: the model and max_tokens overrides ---------------------------


def test_an_override_model_and_max_tokens_reach_the_payload():
    """The whole point of the parameters: a caller that is not the session has
    somewhere to say which model it wants and how long a reply it will take."""
    restore, calls = capture_post_chat(REPLY)
    try:
        raw = agent_model.ask_model([{"role": "user", "content": "hi"}],
                                    model="some/other-model", max_tokens=321)
    finally:
        restore()
    assert json.loads(raw)["message"] == "ok"
    assert calls[0]["payload"]["model"] == "some/other-model", calls[0]["payload"]
    assert calls[0]["payload"]["max_tokens"] == 321, calls[0]["payload"]


def test_without_overrides_the_payload_still_reads_the_session_settings():
    """Left at their defaults both parameters must be invisible: every existing
    caller passes neither, and both figures are read from a module-level global
    at call time so that /model and /effort take effect between two turns."""
    original_model = agent_model._model_for_request
    original_ceiling = agent_config.max_tokens_for_effort
    agent_model._model_for_request = lambda: "settings/model"
    agent_config.max_tokens_for_effort = lambda level=None: 4242
    restore, calls = capture_post_chat(REPLY)
    try:
        agent_model.ask_model([{"role": "user", "content": "hi"}])
    finally:
        restore()
        agent_model._model_for_request = original_model
        agent_config.max_tokens_for_effort = original_ceiling
    assert calls[0]["payload"]["model"] == "settings/model", calls[0]["payload"]
    assert calls[0]["payload"]["max_tokens"] == 4242, calls[0]["payload"]


def test_the_overrides_reach_the_streaming_payload_too():
    """A worker streams like everything else, so the streaming payload is the
    one that will actually carry these on a real run."""
    seen = {}
    original = agent_model.stream_chat

    def fake_stream(payload, on_usage=None, on_input_usage=None):
        seen["payload"] = payload
        yield REPLY

    agent_model.stream_chat = fake_stream
    try:
        raw = agent_model.ask_model([{"role": "user", "content": "hi"}],
                                    on_event=lambda event: None,
                                    model="worker/model", max_tokens=999)
    finally:
        agent_model.stream_chat = original
    assert json.loads(raw)["message"] == "ok"
    assert seen["payload"]["model"] == "worker/model", seen["payload"]
    assert seen["payload"]["max_tokens"] == 999, seen["payload"]


# --- ask_model: quiet --------------------------------------------------------


def run_with_console(quiet):
    """Drive the two printing paths in ask_model and report what was written.

    The JSON-mode rejection is forced with an error string the retry logic
    recognises, because that branch is the only other place in this module
    that writes to the terminal.
    """
    console = FakeConsole()
    original_console = agent_model.console
    original_flag = agent_model._json_mode_ok
    original_stdout = sys.stdout
    captured = io.StringIO()
    agent_model.console = console
    agent_model._json_mode_ok = True
    sys.stdout = captured
    restore, calls = capture_post_chat(REPLY, errors=["provider rejected response_format"])
    try:
        raw = agent_model.ask_model([{"role": "user", "content": "hi"}], quiet=quiet)
    finally:
        restore()
        sys.stdout = original_stdout
        agent_model.console = original_console
        agent_model._json_mode_ok = original_flag
    return raw, console, captured.getvalue(), calls


def test_quiet_reaches_no_printing_code_at_all():
    """A worker runs on a background thread. Anything it writes lands on top of
    the session's live region, and the region's next repaint then walks the
    cursor up through rows that have moved -- which is not a cosmetic fault,
    it is the frame arithmetic being wrong from then on."""
    raw, console, stdout, calls = run_with_console(quiet=True)
    assert json.loads(raw)["message"] == "ok"
    # It really did take the retry, so the printing branch really was reached.
    assert len(calls) == 2, calls
    assert console.statuses == [], console.statuses
    assert console.prints == [], console.prints
    assert stdout == "", repr(stdout)
    # And the spinner was refused at the argument, not merely unused.
    assert [call["spinner"] for call in calls] == [False, False], calls


def test_without_quiet_the_same_call_still_prints():
    """The companion assertion. Without it, quiet passing would prove nothing:
    a path that never printed under either flag would pass just as well."""
    _raw, console, _stdout, calls = run_with_console(quiet=False)
    assert any("JSON mode" in line for line in console.prints), console.prints
    # The spinner itself lives inside _post_chat, which is stubbed here, so
    # what is asserted is the instruction that reaches it. The spinner opening
    # for real is the next test.
    assert [call["spinner"] for call in calls] == [True, True], calls


def test_post_chat_opens_the_spinner_only_when_it_is_asked_to():
    """The other half of quiet, at the place the spinner actually opens.

    console.status paints a live rich spinner. Opened from a background thread
    it draws over the session's own live region, and every repaint after that
    is aiming at rows that have moved.
    """
    console = FakeConsole()
    original_console = agent_model.console
    original_select = agent_model.selected_provider
    original_complete = agent_providers.complete
    agent_model.console = console
    agent_model.selected_provider = lambda: (object(), "key", "")
    agent_providers.complete = lambda *args, **kwargs: (REPLY, 26, 1180)
    try:
        agent_model._post_chat({"messages": [], "model": "x"}, spinner=True)
        assert len(console.statuses) == 1, console.statuses
        agent_model._post_chat({"messages": [], "model": "x"}, spinner=False)
        assert len(console.statuses) == 1, console.statuses
    finally:
        agent_model.console = original_console
        agent_model.selected_provider = original_select
        agent_providers.complete = original_complete
    assert console.prints == [], console.prints


# --- parse_stream_input, per adapter ----------------------------------------


def test_the_base_provider_reports_no_input_count():
    """The default has to be None so an adapter that has not been taught the
    field says "I do not know" rather than "there was no prompt"."""
    assert agent_providers.Provider().parse_stream_input({"usage": {}}) is None


def test_each_adapter_reads_its_own_prompt_token_field():
    """Four providers, four names for the same number. Reading the wrong one
    silently reports nothing, which is indistinguishable from a provider that
    does not send it -- so each is checked against the shape it really sends."""
    cases = [
        ("openrouter", {"usage": {"prompt_tokens": 812, "completion_tokens": 40}}, 812),
        ("openai", {"usage": {"prompt_tokens": 1500, "completion_tokens": 12}}, 1500),
        # The newer OpenAI-family name, which several gateways send instead.
        ("openai", {"usage": {"input_tokens": 77}}, 77),
        ("anthropic", {"type": "message_delta",
                       "usage": {"input_tokens": 640, "output_tokens": 91}}, 640),
        ("gemini", {"usageMetadata": {"promptTokenCount": 533,
                                      "candidatesTokenCount": 12,
                                      "totalTokenCount": 545}}, 533),
    ]
    for provider_id, event, expected in cases:
        provider = agent_providers.get_provider(provider_id)
        assert provider.parse_stream_input(event) == expected, (provider_id, event)


def test_anthropic_reads_the_input_count_out_of_message_start():
    """This is the one that matters on a real stream. message_start carries the
    whole prompt count before any reply exists and message_delta never repeats
    it, so an adapter reading only the top level gets nothing all stream."""
    provider = agent_providers.get_provider("anthropic")
    message_start = {
        "type": "message_start",
        "message": {"id": "msg_01", "role": "assistant", "model": "claude-sonnet-5",
                    "content": [], "usage": {"input_tokens": 1204, "output_tokens": 1}},
    }
    assert provider.parse_stream_input(message_start) == 1204
    # And the output side keeps reading the same event, unchanged.
    assert provider.parse_stream_usage(message_start) == 1


def test_a_shape_that_reports_nothing_returns_none_and_never_zero():
    """None is "the provider said nothing"; 0 would be a claim that the request
    had no prompt, which is never true of a request that was sent. The meter
    prints what it is handed, so the difference is a number on screen."""
    silent = {
        "openrouter": {"choices": [{"delta": {"content": "hello"}}]},
        "openai": {"choices": [{"delta": {"content": "hello"}}], "usage": None},
        "anthropic": {"type": "content_block_delta",
                      "delta": {"type": "text_delta", "text": "hello"}},
        "gemini": {"candidates": [{"content": {"parts": [{"text": "hello"}]}}]},
    }
    for provider_id, event in silent.items():
        value = agent_providers.get_provider(provider_id).parse_stream_input(event)
        assert value is None, (provider_id, value)
    # An empty event, which is what a keep-alive decodes to.
    for provider_id in agent_providers.PROVIDERS:
        assert agent_providers.get_provider(provider_id).parse_stream_input({}) is None


# --- threading it out: stream_completion, complete, _post_chat ---------------


def test_stream_completion_hands_input_tokens_to_its_own_sink():
    """The two sinks stay separate. An input figure arriving on the output sink
    would be added to a running total it does not belong to."""
    provider = agent_providers.get_provider("anthropic")
    events = [
        {"type": "message_start",
         "message": {"usage": {"input_tokens": 2048, "output_tokens": 0}}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
        {"type": "message_delta", "usage": {"output_tokens": 17}},
    ]
    outputs, inputs = [], []
    previous = with_session(Response("", lines=sse(events)))
    try:
        text = "".join(agent_providers.stream_completion(
            provider, "sk-ant-testkey-000000", [{"role": "user", "content": "hi"}],
            model="claude-sonnet-5", on_usage=outputs.append,
            on_input_usage=inputs.append))
    finally:
        agent_config._session = previous
    assert text == "hi"
    assert inputs == [2048], inputs
    # message_start reports the output count too, as 0, and message_delta then
    # supersedes it. The input figure is sent once and never repeated, which is
    # exactly why it needs its own sink rather than a "last usage wins" rule.
    assert outputs == [0, 17], outputs


def test_complete_returns_text_output_and_input_tokens():
    """complete grew a third member, which is a breaking change to its one
    caller. Asserted here rather than inferred from _post_chat, because the
    tuple is what any future caller will unpack."""
    provider = agent_providers.get_provider("openrouter")
    body = json.dumps({"choices": [{"message": {"content": REPLY}}],
                       "usage": {"prompt_tokens": 1180, "completion_tokens": 26}})
    previous = with_session(Response(body))
    try:
        result = agent_providers.complete(
            provider, "sk-or-testkey-000000", [{"role": "user", "content": "hi"}],
            model="x/y")
    finally:
        agent_config._session = previous
    assert len(result) == 3, result
    text, output_tokens, input_tokens = result
    assert json.loads(text)["message"] == "ok"
    assert output_tokens == 26
    assert input_tokens == 1180


def test_a_provider_that_reports_no_usage_still_returns_three_members():
    provider = agent_providers.get_provider("openrouter")
    body = json.dumps({"choices": [{"message": {"content": REPLY}}]})
    previous = with_session(Response(body))
    try:
        text, output_tokens, input_tokens = agent_providers.complete(
            provider, "sk-or-testkey-000000", [{"role": "user", "content": "hi"}])
    finally:
        agent_config._session = previous
    assert json.loads(text)["message"] == "ok"
    assert output_tokens is None
    assert input_tokens is None


def test_post_chat_carries_the_input_figure_into_the_usage_record():
    """One internal shape. _post_chat re-expresses every provider's reply as a
    choices/usage object so nothing downstream has to know which adapter ran,
    and the prompt count has to travel inside it or it does not travel."""
    original_select = agent_model.selected_provider
    original_complete = agent_providers.complete
    agent_model.selected_provider = lambda: (object(), "key", "")
    agent_providers.complete = lambda *args, **kwargs: (REPLY, 26, 1180)
    try:
        data, error = agent_model._post_chat({"messages": [], "model": "x"},
                                             spinner=False)
    finally:
        agent_model.selected_provider = original_select
        agent_providers.complete = original_complete
    assert error is None, error
    assert data["usage"]["prompt_tokens"] == 1180, data
    assert data["usage"]["completion_tokens"] == 26, data
    assert agent_model._prompt_tokens(data) == 1180
    assert agent_model._completion_tokens(data) == 26


def test_prompt_tokens_reports_nothing_rather_than_zero():
    assert agent_model._prompt_tokens({}) is None
    assert agent_model._prompt_tokens({"usage": {}}) is None
    assert agent_model._prompt_tokens({"usage": {"prompt_tokens": "many"}}) is None
    assert agent_model._prompt_tokens({"usage": {"input_tokens": 9}}) == 9


# --- the ("input_usage", n) event -------------------------------------------


def test_the_input_usage_event_reaches_a_handler_from_the_blocking_path():
    events = []
    restore, _calls = capture_post_chat(REPLY, usage={"completion_tokens": 26,
                                                      "prompt_tokens": 1180})
    original = agent_model.stream_chat
    agent_model.stream_chat = lambda *a, **k: (_ for _ in ()).throw(
        agent_model.StreamError("HTTP 400: no streaming"))
    try:
        raw = agent_model.ask_model([{"role": "user", "content": "hi"}],
                                    on_event=events.append)
    finally:
        agent_model.stream_chat = original
        restore()
    assert json.loads(raw)["message"] == "ok"
    assert ("input_usage", 1180) in events, events
    assert ("usage", 26) in events, events


def test_no_input_usage_event_when_the_provider_reported_none():
    """Silence is the honest answer. An event carrying 0 would settle the
    meter on a figure nobody sent, and the estimate it replaced was at least
    marked as an estimate."""
    events = []
    restore, _calls = capture_post_chat(REPLY, usage={"completion_tokens": 26})
    original = agent_model.stream_chat
    agent_model.stream_chat = lambda *a, **k: (_ for _ in ()).throw(
        agent_model.StreamError("HTTP 400: no streaming"))
    try:
        agent_model.ask_model([{"role": "user", "content": "hi"}],
                              on_event=events.append)
    finally:
        agent_model.stream_chat = original
        restore()
    assert [kind for kind, _ in events if kind == "input_usage"] == [], events


def test_the_input_usage_event_reaches_a_handler_from_the_stream():
    events = []
    original = agent_model.stream_chat

    def fake_stream(payload, on_usage=None, on_input_usage=None):
        if on_input_usage:
            on_input_usage(2048)          # Anthropic sends this first of all
        yield REPLY
        if on_usage:
            on_usage(17)

    agent_model.stream_chat = fake_stream
    try:
        raw = agent_model.ask_model([{"role": "user", "content": "hi"}],
                                    on_event=events.append)
    finally:
        agent_model.stream_chat = original
    assert json.loads(raw)["message"] == "ok"
    assert ("input_usage", 2048) in events, events
    assert ("usage", 17) in events, events
    # It arrives before any generated text, which is what the provider does.
    kinds = [kind for kind, _ in events]
    assert kinds.index("input_usage") < kinds.index("first_content"), kinds


def test_a_stream_chat_written_before_input_tokens_still_works():
    """stream_chat is replaced wholesale by several tests and by anything else
    scripting a reply, and those stand-ins take (payload, on_usage). Binding
    happens at the call rather than at the first next(), so a two-argument form
    fails immediately and unmistakably -- and is retried without the new sink
    rather than turned into a stream failure the user would see."""
    events = []
    original = agent_model.stream_chat

    def old_style(payload, on_usage=None):
        yield REPLY
        if on_usage:
            on_usage(17)

    agent_model.stream_chat = old_style
    try:
        raw = agent_model.ask_model([{"role": "user", "content": "hi"}],
                                    on_event=events.append)
    finally:
        agent_model.stream_chat = original
    assert json.loads(raw)["message"] == "ok"
    assert ("usage", 17) in events, events
    assert [kind for kind, _ in events if kind == "input_usage"] == [], events


def test_the_session_handler_ignores_the_new_event_kind():
    """Every existing on_event handler has to survive a kind it has never seen.
    TMT.stream_handler is an if/elif chain with no else, so an unknown kind
    falls through -- asserted rather than assumed, because adding an else
    later would break every caller at once."""
    from TMT import stream_handler
    state = {"error": None}
    handle = stream_handler(None, None, state)
    handle(("input_usage", 1180))          # must not raise, must not touch state
    assert state == {"error": None}, state


# --- WRITE_LOCK --------------------------------------------------------------


def lock_is_free_elsewhere():
    """Whether another thread could take WRITE_LOCK at this moment.

    Asked from a NEW thread deliberately. WRITE_LOCK is re-entrant, so the
    thread already holding it would be let straight back in and would report
    the lock free when it is not. The probe releases what it took, in the same
    thread that took it, because an RLock left owned by a thread that has since
    exited can never be acquired again.
    """
    answer = []

    def probe():
        got = agent_file_ops.WRITE_LOCK.acquire(blocking=False)
        if got:
            agent_file_ops.WRITE_LOCK.release()
        answer.append(got)

    thread = threading.Thread(target=probe, name="lock-probe")
    thread.start()
    thread.join()
    return answer[0]


def watch_decode():
    """Report whether the lock was held when _decode_content ran.

    _decode_content is the one seam inside the critical section of write_file,
    append_file, patch_file and replace_lines, which is what makes a single
    probe answer for all four.
    """
    seen = {}
    original = agent_file_ops._decode_content

    def watching(content):
        seen["free"] = lock_is_free_elsewhere()
        return original(content)

    agent_file_ops._decode_content = watching

    def restore():
        agent_file_ops._decode_content = original
    return seen, restore


def test_the_write_lock_is_held_for_the_whole_body_of_every_write():
    """Not just around the write call. Each of these reads the file, works out
    the new text and writes it back, and the answer each returns describes the
    file as it was when it read it -- so a write landing in between makes both
    the file and the report wrong."""
    workspace = Workspace({"a.txt": "one\ntwo\nthree\n"})
    seen, restore = watch_decode()
    try:
        assert lock_is_free_elsewhere() is True, "the lock was held before any write"
        for call in (lambda: agent_file_ops.write_file("a.txt", "x"),
                     lambda: agent_file_ops.append_file("a.txt", "y"),
                     lambda: agent_file_ops.patch_file("a.txt", "x", "z"),
                     lambda: agent_file_ops.replace_lines("a.txt", 1, 1, "w")):
            seen.clear()
            result = call()
            assert "not found" not in result, result
            assert seen.get("free") is False, (result, seen)
            assert lock_is_free_elsewhere() is True, result
    finally:
        restore()
        workspace.close()


def test_copy_file_holds_the_lock_across_its_refusal_to_overwrite():
    """The refusal is the reason the whole body is locked. Checked outside the
    lock it is a promise another thread can break between the check and the
    copy, and not overwriting an existing file is the one thing copy_file
    says it will never do."""
    workspace = Workspace({"a.txt": "one\n"})
    seen = {}
    original = agent_file_ops.shutil.copy2

    def watching(src, dst):
        seen["free"] = lock_is_free_elsewhere()
        return original(src, dst)

    agent_file_ops.shutil.copy2 = watching
    try:
        result = agent_file_ops.copy_file("a.txt", "b.txt")
        assert result.startswith("Copied"), result
        assert seen.get("free") is False, seen
        assert lock_is_free_elsewhere() is True
    finally:
        agent_file_ops.shutil.copy2 = original
        workspace.close()


def test_the_write_lock_is_not_held_across_a_confirmation_prompt():
    """A deliberate exception, and the reason it is deliberate: delete_file
    reads stdin before it removes anything, and a lock held across a read of
    stdin is a lock held until a human answers it. Every other write in the
    process would queue behind that prompt."""
    workspace = Workspace({"a.txt": "one\n"})
    seen = {}
    original_input = getattr(agent_file_ops, "input", None)

    def confirm(prompt):
        seen["free_at_prompt"] = lock_is_free_elsewhere()
        return "y"

    agent_file_ops.input = confirm
    try:
        result = agent_file_ops.delete_file("a.txt")
        assert result.startswith("Deleted"), result
        assert seen["free_at_prompt"] is True, seen
        assert not (workspace.path / "a.txt").exists()
    finally:
        if original_input is None:
            del agent_file_ops.input
        else:
            agent_file_ops.input = original_input
        workspace.close()


class CountingLock:
    """A real RLock that records how many times it was entered.

    Delegates rather than reimplements, so the serialisation under test is the
    genuine article and this only observes it.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.entries = 0

    def __enter__(self):
        self._lock.acquire()
        self.entries += 1
        return self

    def __exit__(self, *exc):
        self._lock.release()
        return False

    def acquire(self, *args, **kwargs):
        return self._lock.acquire(*args, **kwargs)

    def release(self):
        return self._lock.release()


def with_counting_lock():
    counting = CountingLock()
    original = agent_file_ops.WRITE_LOCK
    agent_file_ops.WRITE_LOCK = counting

    def restore():
        agent_file_ops.WRITE_LOCK = original
    return counting, restore


def test_a_batch_write_re_enters_the_lock_rather_than_deadlocking():
    """write_files takes the lock for the batch and write_file takes it again
    for each entry. That is the whole reason WRITE_LOCK is an RLock: a plain
    Lock would deadlock against itself on the first file and never return."""
    # Asked of the real lock, before the counting stand-in is put in its place:
    # taken twice from one thread and granted both times. A plain Lock refuses
    # the second and write_files hangs, which is a failure that cannot be
    # caught by running write_files -- the test would simply never come back.
    assert agent_file_ops.WRITE_LOCK.acquire(blocking=False)
    try:
        assert agent_file_ops.WRITE_LOCK.acquire(blocking=False), \
            "WRITE_LOCK is not re-entrant, so write_files would deadlock"
        agent_file_ops.WRITE_LOCK.release()
    finally:
        agent_file_ops.WRITE_LOCK.release()

    workspace = Workspace()
    counting, restore = with_counting_lock()
    try:
        result = agent_file_ops.write_files([
            {"path": "one.txt", "content": "1"},
            {"path": "two.txt", "content": "2"},
        ])
        assert "Created file: one.txt" in result, result
        assert "Created file: two.txt" in result, result
        # Once for the batch, once per file.
        assert counting.entries == 3, counting.entries
        assert workspace.read("two.txt") == "2"
    finally:
        restore()
        workspace.close()


def test_replace_across_locks_each_applied_write_and_locks_nothing_on_preview():
    """Preview writes nothing, so it takes nothing -- and apply takes the lock
    per file rather than for the length of the walk, which could be hundreds
    of files and would stall every other worker for all of it."""
    workspace = Workspace({"a.txt": "alpha\n", "b.txt": "alpha\n", "c.txt": "beta\n"})
    counting, restore = with_counting_lock()
    try:
        preview = agent_file_ops.replace_across("alpha", "omega", glob="*.txt")
        assert "Preview only" in preview, preview
        assert counting.entries == 0, counting.entries
        assert workspace.read("a.txt") == "alpha\n"

        applied = agent_file_ops.replace_across("alpha", "omega", glob="*.txt",
                                                apply=True)
        assert "2 files changed" in applied, applied
        assert counting.entries == 2, counting.entries
        assert workspace.read("a.txt") == "omega\n"
        assert workspace.read("c.txt") == "beta\n"
    finally:
        restore()
        workspace.close()


def test_two_threads_appending_the_same_file_lose_nothing():
    """The lost update, which is what a shared file primitive actually gets
    wrong. append_file reads the file, builds the new text and writes it back;
    two of those interleaved each read the same original and the second writes
    over the first's addition, and nothing anywhere reports it.

    A yield is injected between the read and the write, at _decode_content,
    which append_file calls after reading and before writing. Under the lock
    the other thread cannot be there to take it; without the lock it is an
    invitation.
    """
    rounds = 30
    workspace = Workspace({"log.txt": "start\n"})
    original = agent_file_ops._decode_content

    def yielding(content):
        time.sleep(0.0005)          # hand the interpreter over at the worst moment
        return original(content)

    agent_file_ops._decode_content = yielding
    previous_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)

    def writer(tag):
        for index in range(rounds):
            agent_file_ops.append_file("log.txt", "%s%d" % (tag, index))

    try:
        threads = [threading.Thread(target=writer, args=(tag,), name="writer-" + tag)
                   for tag in ("A", "B")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        text = workspace.read("log.txt")
    finally:
        sys.setswitchinterval(previous_interval)
        agent_file_ops._decode_content = original
        workspace.close()

    lines = [line for line in text.splitlines() if line]
    assert lines[0] == "start", lines[:3]
    missing = [tag + str(index) for tag in ("A", "B") for index in range(rounds)
               if tag + str(index) not in lines]
    assert not missing, "lost updates: %r" % (missing[:10],)
    # One intact file, not a mixture: every line is whole and nothing is doubled.
    assert len(lines) == 1 + 2 * rounds, (len(lines), lines[:5])


def test_only_one_of_two_racing_writers_reports_creating_the_file():
    """write_file answers "Created" or "Wrote" from an existence check inside
    its own body, and that answer is the only thing the user is told about what
    happened to the file. Two threads racing on a path that does not exist can
    both see it missing and both report creating it, which is a false statement
    about the disk from the one action that is allowed to make it.
    """
    paths = 25
    workspace = Workspace()
    original = agent_file_ops._decode_content

    def yielding(content):
        time.sleep(0.0002)
        return original(content)

    agent_file_ops._decode_content = yielding
    previous_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    created = []
    try:
        for index in range(paths):
            name = "race%d.txt" % index
            gate = threading.Barrier(2)
            answers = []

            def writer(body):
                gate.wait()
                answers.append(agent_file_ops.write_file(name, body))

            threads = [threading.Thread(target=writer, args=(body,))
                       for body in ("first", "second")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            created.append(sum(1 for answer in answers
                               if answer.startswith("Created")))
            # And the file is one of the two whole texts, never a splice.
            assert workspace.read(name) in ("first", "second"), name
    finally:
        sys.setswitchinterval(previous_interval)
        agent_file_ops._decode_content = original
        workspace.close()
    assert created == [1] * paths, created


def test_path_validation_is_not_relaxed_by_the_lock():
    """safe_path behaves exactly as it did. The lock makes a write atomic; it
    says nothing about where a write is allowed to land, and a worker gets no
    more room than the main agent does."""
    workspace = Workspace({"inside.txt": "kept\n"})
    outside = Path(tempfile.mkdtemp(prefix="tmt_outside_")).resolve()
    victim = outside / "victim.txt"
    victim.write_text("untouched", encoding="utf-8")
    try:
        escape = os.path.relpath(str(victim), str(workspace.path))
        for call in (lambda: agent_file_ops.write_file(escape, "overwritten"),
                     lambda: agent_file_ops.append_file(escape, "more"),
                     lambda: agent_file_ops.patch_file(escape, "untouched", "gone"),
                     lambda: agent_file_ops.copy_file("inside.txt", escape)):
            raised = False
            try:
                call()
            except ValueError as error:
                raised = True
                assert "Blocked unsafe path" in str(error), error
            assert raised, "an escape from the workspace was not refused"
        assert victim.read_text(encoding="utf-8") == "untouched"
        # The lock is not left held by a refused call.
        assert lock_is_free_elsewhere() is True
    finally:
        workspace.close()
        remove_tree(outside)
