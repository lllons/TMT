"""The pieces of `--backend-stdio`, one at a time: framing, routing, stand-ins.

`test_agent_backend_events` drives whole sessions through them; this module
pins each piece on its own, so a failure there can be read off a failure here.
The contract is `docs/tui-protocol.md`.
"""

import inspect
import io
import json
import os
import threading
import time

import agent_backend
import agent_config
import agent_ui
import TMT


def channel(lines=b"", text_out=False):
    """A Channel over in-memory streams: `lines` is everything the UI will send."""
    out = io.StringIO() if text_out else io.BytesIO()
    return agent_backend.Channel(io.BytesIO(lines), out), out


def sent(out):
    """The events written so far, parsed, one per line.

    Only whole lines: another thread may be part of the way through one.
    """
    raw = out.getvalue()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    return [json.loads(line) for line in raw.split("\n")[:-1] if line]


class Env:
    """COLUMNS and LINES put back afterwards: a `hello` sets them."""

    def __enter__(self):
        self.saved = (os.environ.get("COLUMNS"), os.environ.get("LINES"))
        return self

    def __exit__(self, *exc):
        for name, value in zip(("COLUMNS", "LINES"), self.saved):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        return False


# --- framing -----------------------------------------------------------------

def test_every_event_is_one_line_of_json_whatever_it_carries():
    awkward = "a\nb\r\nc\u2028d\u2029e\x85f \u2713 \u00e9 \ud800 \x1b[2K"
    for text_out in (False, True):
        ch, out = channel(text_out=text_out)
        assert ch.send("final", text=awkward)
        raw = out.getvalue()
        if text_out:
            raw = raw.encode("utf-8", "backslashreplace")
        assert raw.endswith(b"\n") and raw.count(b"\n") == 1, raw
        assert b"\r" not in raw and b"\x1b" not in raw
        line = raw.decode("utf-8")
        assert len(line.splitlines()) == 1, line           # not even U+2028 splits it
        event = json.loads(line)
        assert event == {"v": 1, "type": "final", "text": awkward}, event
        # Non-ASCII goes out as itself, not as an escape.
        assert "\u2713".encode("utf-8") in raw


def test_an_event_that_cannot_be_written_means_the_front_end_has_gone():
    class Broken(io.BytesIO):
        def write(self, data):
            raise BrokenPipeError

    ch = agent_backend.Channel(io.BytesIO(b""), Broken())
    gone = []
    ch.on_close = lambda: gone.append(True)
    assert ch.send("meter", tokens_in=1) is False
    assert ch.closed and gone == [True]
    assert ch.inbox.get_nowait() == ("eof",)
    assert ch.send("meter", tokens_in=2) is False          # and stays gone, quietly


# --- the input side ----------------------------------------------------------

def test_messages_are_routed_and_unusable_ones_are_reported():
    lines = b"\n".join([
        b'{"v":1,"type":"hello","columns":120,"rows":40}',
        b"not json",
        b"[1, 2]",
        b'{"type":"submit"}',
        b'{"type":"submit","text":"fix the \\u00e9 bug\\nsecond line"}',
        b'{"type":"from_the_future","x":1}',
        b"",
        b'\xef\xbb\xbf{"type":"resize","columns":90,"rows":20}',
        b'{"type":"reload"}',
        b'{"type":"quit"}',
    ]) + b"\n"
    with Env():
        ch, out = channel(lines)
        ch.ready = True
        ch._read()
        assert (os.environ["COLUMNS"], os.environ["LINES"]) == ("90", "20")
    inbox = []
    while not ch.inbox.empty():
        inbox.append(ch.inbox.get_nowait())
    assert [item[0] for item in inbox] == ["hello", "submit", "reload", "quit", "eof"]
    assert inbox[1][1] == "fix the \u00e9 bug\nsecond line"
    errors = sent(out)
    assert [event["type"] for event in errors] == ["protocol_error"] * 3, errors
    assert ch.closed


def test_nothing_is_answered_before_the_handshake():
    """`ready` has to be the first event, so a bad line before it is logged only."""
    ch, out = channel(b"garbage\n")
    ch._read()
    assert out.getvalue() == b""


def _answer_after(ch, write, replies):
    """Run `ch.request` on a thread; send `replies` once its request is out."""
    result = {}

    def ask():
        result["reply"] = ch.request(
            "approval", "approval_reply",
            accept=lambda message: isinstance(message.get("answer"), str),
            question="Delete a.txt?")

    thread = threading.Thread(target=ask, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not ch._waiting and time.monotonic() < deadline:
        time.sleep(0.01)
    for reply in replies:
        write.write((json.dumps(reply) + "\n").encode("utf-8"))
        write.flush()
    return thread, result


def test_a_request_is_answered_only_by_its_own_reply():
    read_fd, write_fd = os.pipe()
    write = os.fdopen(write_fd, "wb")
    out = io.BytesIO()
    ch = agent_backend.Channel(os.fdopen(read_fd, "rb"), out)
    ch.ready = True
    ch.start()
    try:
        thread, result = _answer_after(ch, write, [
            {"type": "approval_reply", "id": 99, "answer": "y"},     # nobody asked
            {"type": "answer", "id": 1, "key": "1"},                 # wrong kind
            {"type": "approval_reply", "id": True, "answer": "y"},   # not an id
            {"type": "approval_reply", "id": 1, "answer": 1},        # not a word
        ])
        # Answered again only once the refusal is out, as a front end would:
        # a reply sent sooner could land before the bad one was looked at.
        deadline = time.monotonic() + 10
        while (not any(event["type"] == "protocol_error" for event in sent(out))
               and time.monotonic() < deadline):
            time.sleep(0.01)
        write.write(b'{"type":"approval_reply","id":1,"answer":" Y "}\n')
        write.flush()
        thread.join(10)
        assert result["reply"]["answer"] == " Y ", result
        events = sent(out)
        assert events[0]["type"] == "approval" and events[0]["id"] == 1, events
        assert events[0]["question"] == "Delete a.txt?"
        errors = [event for event in events if event["type"] == "protocol_error"]
        assert len(errors) == 1 and errors[0]["id"] == 1, events
        assert not ch._waiting
    finally:
        write.close()


def test_the_input_ending_answers_every_waiting_request_with_nobody():
    read_fd, write_fd = os.pipe()
    write = os.fdopen(write_fd, "wb")
    ch = agent_backend.Channel(os.fdopen(read_fd, "rb"), io.BytesIO())
    ch.start()
    thread, result = _answer_after(ch, write, [])
    write.close()
    thread.join(10)
    assert not thread.is_alive()
    assert result["reply"] is None and ch.closed
    assert ch.inbox.get(timeout=5) == ("eof",)
    # And a request made after the end is not even sent.
    assert ch.request("question", "answer", text="?") is None


def test_lines_are_split_the_same_whatever_the_reads_cut_them_into():
    """A real stdin is read in raw chunks (see `Channel._readline`): a line may
    arrive in pieces, several lines in one piece, and a last line with no
    newline; a multi-byte character cut by a chunk edge must survive."""
    read_fd, write_fd = os.pipe()
    ch = agent_backend.Channel(os.fdopen(read_fd, "rb"), io.BytesIO())
    big = "é" * 70000                      # 140 KB: past one 64 KB read
    lines = [json.dumps({"type": "submit", "text": big}).encode("utf-8") + b"\n",
             b'{"type":"submit","text":"a"}\n{"type":"submit","text":"b"}\n',
             b'{"type":"submit","text":"c"}']      # the last one has no newline

    def feed():
        with os.fdopen(write_fd, "wb") as write:
            for chunk in lines:
                write.write(chunk)
                write.flush()

    threading.Thread(target=feed, daemon=True).start()
    ch._read()
    texts = []
    while not ch.inbox.empty():
        item = ch.inbox.get_nowait()
        texts.append(item[1] if item[0] == "submit" else item[0])
    assert texts == [big, "a", "b", "c", "eof"], [text[:10] for text in texts]


# --- the stand-ins -----------------------------------------------------------

def test_the_stand_ins_cover_names_the_loop_really_draws_through():
    """A rename in TMT.py must fail here, not quietly route the terminal UI
    into a headless run."""
    source = "\n".join(inspect.getsource(function) for function in (
        TMT._session_loop, TMT.finish_response, TMT._blocking_command,
        TMT.resolve_workspace))
    for name in agent_backend.SEAMS:
        assert hasattr(TMT, name), name
        assert name + "(" in source or name + "." in source, name


def test_installing_puts_every_name_back_exactly():
    ch, _ = channel()
    port = agent_backend.Headless(ch, interrupt=lambda: None)
    before = {name: getattr(TMT, name) for name in agent_backend.SEAMS}
    try:
        with port.installed(TMT):
            for name in agent_backend.SEAMS:
                assert getattr(TMT, name) is not before[name], name
            raise RuntimeError("the run crashed")
    except RuntimeError:
        pass
    for name, value in before.items():
        assert getattr(TMT, name) is value, name


def test_console_markup_becomes_a_notice_with_a_level():
    ch, out = channel()
    console = agent_backend._Console(agent_backend.Headless(ch, interrupt=lambda: None))
    console.print("[red]Stream failed:[/red] boom")
    console.print("\n[yellow]Task cancelled. Returning to prompt.[/yellow]")
    console.print("[bold red]Circuit Breaker Tripped:[/bold red] stopped")
    console.print("[yellow]Unreadable reply, asking again:[/yellow] Text \\[/x] sits loose")
    console.print("Run TMT from inside a project, or name one with --dir.")
    console.print("")
    notices = [(event["level"], event["text"]) for event in sent(out)]
    assert notices == [
        ("error", "Stream failed: boom"),
        ("warning", "Task cancelled. Returning to prompt."),
        ("error", "Circuit Breaker Tripped: stopped"),
        ("warning", "Unreadable reply, asking again: Text [/x] sits loose"),
        ("info", "Run TMT from inside a project, or name one with --dir."),
    ], notices
    try:
        console.input("anything? ")
        raise AssertionError("a headless console must never read")
    except EOFError:
        pass


def test_the_status_row_moves_exactly_as_the_terminal_one_does():
    """A subclass, so the arithmetic is LiveUI's own; this pins that it stays so."""
    ch, out = channel()
    port = agent_backend.Headless(ch, interrupt=lambda: None)
    headless, terminal = agent_backend._Status(port), agent_ui.LiveUI(stream=io.StringIO())
    steps = [("start", ()), ("meaningful_output", ()), ("intermediate_event", ("Read",)),
             ("intermediate_event", ("Write",)), ("intermediate_event", ("Run",)),
             ("final_event", ())]
    try:
        for name, args in steps:
            getattr(headless, name)(*args)
            getattr(terminal, name)(*args)
            assert headless._progress == terminal._progress, name
    finally:
        headless.complete()
        terminal.complete()
    statuses = [(event["state"], event["label"], event["progress"])
                for event in sent(out) if event["type"] == "status"]
    assert statuses == [("thinking", "THINKING", None),
                        ("working", "Just Started!", 10),
                        ("working", "Read", 23), ("working", "Write", 35),
                        ("working", "Run", 46), ("working", "FINALIZING", 95),
                        ("working", "Complete!", 100), ("idle", "", None)], statuses
    assert [event["type"] for event in sent(out)][0] == "turn_start"


def test_an_interrupt_fires_only_while_a_turn_is_armed():
    fired = []
    port = agent_backend.Headless(channel()[0], interrupt=lambda: fired.append(1))
    port._interrupted()
    assert fired == []
    port.arm(True)
    port._interrupted()
    port.arm(False)
    port._interrupted()
    assert fired == [1]


def test_the_transcript_sends_what_the_terminal_prints_and_keeps_all_of_it():
    ch, out = channel()
    port = agent_backend.Headless(ch, interrupt=lambda: None)
    transcript = agent_backend._Transcript(port)
    transcript.emit_kind("progress", "Reading the parser.")
    transcript.emit_kind("next_step_suggestion", "Run the tests")
    transcript.emit(agent_ui.AgentEvent.make("file_edit", "Edited a.py", added=1,
                                             removed=2, paths=("a.py",)))
    assert transcript.emit(None) is None
    assert transcript.history.kinds() == ("progress", "next_step_suggestion", "file_edit")
    events = sent(out)
    assert [(event["kind"], event["level"]) for event in events] == \
        [("progress", 0), ("file_edit", 2)], events
    assert events[1]["detail"] == {"added": 1, "removed": 2, "paths": ["a.py"]}
    assert port.suggestion == "Run the tests"


def test_settings_carry_what_the_front_end_needs():
    saved = getattr(agent_config, "MOUSE", None)
    try:
        agent_config.MOUSE = "on"
        values = agent_backend.settings()
    finally:
        agent_config.MOUSE = saved
    assert set(values) == {"protocol", "mouse", "effort", "provider", "provider_label",
                           "model", "model_label", "configured", "version"}, values
    assert values["mouse"] == "on" and isinstance(values["configured"], bool)
    assert values["protocol"] in agent_config.PROTOCOLS
    json.dumps(values)
