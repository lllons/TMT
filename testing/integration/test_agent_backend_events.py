"""The headless backend, driven the way the TypeScript front end drives it.

`ReferenceClient` is the smallest front end there is: two pipes, one thread
reading events, and a `react` function that answers them. `drive` runs
`agent_backend.run` IN-PROCESS against it with a scripted model, so nothing
here needs a network or a key. The last tests start the real
`python TMT.py --backend-stdio` to prove the process itself keeps its stdout
to JSON lines, and pin the terminal UI's own output so the backend can be
shown not to have changed it.

The contract these tests hold the backend to is `docs/tui-protocol.md`.
"""

import contextlib
import datetime
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import agent_backend
import agent_config
import agent_menu
import agent_tips
import TMT
from test_agent_workspace import Workspace, remove_tree

REPO = Path(TMT.__file__).resolve().parent
FIXED = datetime.datetime(2026, 10, 25, 9, 30, 0)
HELLO = {"v": 1, "type": "hello", "columns": 100, "rows": 30}


# --- the harness -------------------------------------------------------------

class Pinned:
    """Every setting a driven session reads, pointed somewhere fixed and temporary.

    The reply format, the tip cursor, the project context, the checkpoint
    store, the clock and the terminal size: each is a fact about the machine,
    and a test that read them would be asking about the machine.
    """

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="tmt_backend_"))
        (self.dir / "protocol").write_text("json\n", encoding="utf-8")
        (self.dir / "context").write_text("off\n", encoding="utf-8")
        self.saved = (agent_config.PROTOCOL_FILE, agent_config.PROTOCOL,
                      agent_config.TIP_FILE, agent_config.PROJECT_CONTEXT_FILE,
                      agent_config.PROJECT_CONTEXT, agent_config.CHECKPOINT_DIR,
                      agent_menu._clock, os.environ.get("COLUMNS"),
                      os.environ.get("LINES"))
        agent_config.PROTOCOL_FILE = self.dir / "protocol"
        agent_config.PROTOCOL = "json"
        agent_config.TIP_FILE = self.dir / "tip"
        agent_config.PROJECT_CONTEXT_FILE = self.dir / "context"
        agent_config.CHECKPOINT_DIR = self.dir / "checkpoints"
        agent_menu._clock = lambda moment=None: FIXED if moment is None else moment
        os.environ["COLUMNS"], os.environ["LINES"] = "100", "30"
        return self

    def __exit__(self, *exc):
        (agent_config.PROTOCOL_FILE, agent_config.PROTOCOL, agent_config.TIP_FILE,
         agent_config.PROJECT_CONTEXT_FILE, agent_config.PROJECT_CONTEXT,
         agent_config.CHECKPOINT_DIR, agent_menu._clock, columns, lines) = self.saved
        for name, value in (("COLUMNS", columns), ("LINES", lines)):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        remove_tree(str(self.dir))
        return False


class Model:
    """A scripted model that reports what it streams the way the real parser does.

    Each reply is an action object, or a callable taking `on_event` for a
    reply that has to behave (block, print). `asked` keeps every request.
    """

    def __init__(self, replies):
        self.replies = list(replies)
        self.asked = []

    def __call__(self, messages, on_event=None, **kwargs):
        reply = self.replies[min(len(self.asked), len(self.replies) - 1)]
        self.asked.append([dict(message) for message in messages])
        if callable(reply):
            reply = reply(on_event)
        raw = json.dumps(reply)
        if on_event is not None:
            on_event(("first_content", ""))
            on_event(("output", len(raw)))
            on_event(("action", reply["action"]))
            if reply.get("progress"):
                on_event(("progress", reply["progress"]))
            message = reply.get("message") or ""
            if message:
                for piece in (message[:5], message[5:]):
                    on_event(("text", piece))
            if reply.get("next_step"):
                on_event(("next_step", reply["next_step"]))
            on_event(("usage", 42))
        return raw

    def said(self, index=-1):
        """Everything the model was handed in one request, as one string."""
        return "\n".join(str(message.get("content")) for message in self.asked[index])


class ReferenceClient:
    """A front end with no screen: pipes, a reading thread, and `react`.

    `react(client, event)` returns the messages to send in answer to one
    event. Everything read is kept, raw and parsed, so a test can check the
    framing as well as the meaning.
    """

    def __init__(self, react):
        in_r, in_w = os.pipe()
        out_r, out_w = os.pipe()
        self.stdin = os.fdopen(in_r, "rb")           # the backend's stdin
        self.stdout = os.fdopen(out_w, "wb")         # the backend's stdout
        self._to_backend = os.fdopen(in_w, "wb")
        self._from_backend = os.fdopen(out_r, "rb")
        self._lock = threading.Lock()
        self.react = react
        self.lines = []
        self.events = []
        self._thread = threading.Thread(target=self._read, daemon=True)

    def start(self):
        self._thread.start()

    def send(self, message):
        line = message if isinstance(message, str) else json.dumps(message)
        with self._lock:
            if self._to_backend.closed:
                return
            try:
                self._to_backend.write((line + "\n").encode("utf-8"))
                self._to_backend.flush()
            except (OSError, ValueError):
                pass

    def close_input(self):
        """What a front end that died looks like from the backend: stdin ends."""
        with self._lock:
            if not self._to_backend.closed:
                self._to_backend.close()

    def _read(self):
        for raw in self._from_backend:
            self.lines.append(raw)
            event = json.loads(raw.decode("utf-8"))
            self.events.append(event)
            for message in self.react(self, event) or ():
                self.send(message)
            if event.get("type") == "exit":
                self.close_input()

    def finish(self):
        self.stdout.close()
        self._thread.join(10)
        self.close_input()
        self._from_backend.close()

    # --- reading what came back -----------------------------------------

    def types(self):
        return [event["type"] for event in self.events]

    def of(self, kind):
        return [event for event in self.events if event["type"] == kind]


def script(tasks=(), approvals=(), keys=(), also=None):
    """A `react` that submits `tasks`, answers approvals and questions in order.

    An approval answered with None is the front end dying instead of
    answering. When the tasks run out the client sends `quit`.
    """
    tasks, approvals, keys = list(tasks), list(approvals), list(keys)

    def react(client, event):
        kind = event["type"]
        if also is not None:
            extra = also(client, event)
            if extra is not None:
                return extra
        if kind == "prompt":
            if tasks:
                return [{"v": 1, "type": "submit", "text": tasks.pop(0)}]
            return [{"v": 1, "type": "quit"}]
        if kind == "approval" and approvals:
            answer = approvals.pop(0)
            if answer is None:
                client.close_input()
                return []
            return [{"v": 1, "type": "approval_reply", "id": event["id"],
                     "answer": answer}]
        # A refused answer is reported with the request's id and waited past,
        # so the next key in the script answers the same question again.
        if kind in ("question", "protocol_error") and keys and "id" in event:
            return [{"v": 1, "type": "answer", "id": event["id"], "key": keys.pop(0)}]
        return []
    return react


def drive(react, replies, files=None, git=False, before=None, deadline=60,
          dies=False):
    """Run the backend in-process against a `ReferenceClient`.

    Returns (exit code, client, model, files left in the workspace).
    `before(client)` may send messages after the hello and before the run.
    A script that never finishes is ended at `deadline` by closing the
    backend's stdin -- the front end dying -- so it fails rather than hangs;
    `dies` says the script means to end that way.
    """
    with Pinned():
        box = Workspace(git=git, files=files)
        model = replies if isinstance(replies, Model) else Model(replies)
        saved = TMT.ask_model
        TMT.ask_model = model
        client = ReferenceClient(react)
        client.start()
        client.send(HELLO)
        if before is not None:
            before(client)
        watchdog = threading.Timer(deadline, client.close_input)
        watchdog.start()
        try:
            args = TMT.parse_args(["--backend-stdio", "--dir", str(box.path)])
            code = agent_backend.run(args, stdin=client.stdin, stdout=client.stdout)
            left = sorted(item.name for item in box.path.iterdir()
                          if item.name != ".git")
        finally:
            watchdog.cancel()
            TMT.ask_model = saved
            client.finish()
            box.close()
    exits = client.of("exit")
    assert exits and (dies or exits[0]["reason"] != "eof"), \
        "the script did not finish by itself: %s" % client.types()[-8:]
    return code, client, model, left


WRITE = {"action": "write_file", "path": "notes.txt", "content": "one\ntwo\n",
         "progress": "Writing the notes."}
DELETE = {"action": "delete_file", "path": "notes.txt"}
ASK = {"action": "ask_user", "question": "Which colour?", "options": ["Red", "Blue"]}
ANSWER = {"action": "end_conversation", "message": "Done — wrote notes.txt ✓",
          "next_step": "Review the notes"}


def in_order(seen, expected):
    """Whether `expected` occurs in `seen` in that order, gaps allowed."""
    position = 0
    for item in seen:
        if position < len(expected) and item == expected[position]:
            position += 1
    return position == len(expected)


# --- a whole turn --------------------------------------------------------------

def test_a_whole_turn_arrives_as_events_in_the_order_it_happened():
    code, client, model, left = drive(
        script(tasks=["write the notes"], approvals=["y"], keys=["2"]),
        [WRITE, DELETE, ASK, ANSWER])
    assert code == 0, client.events[-3:]
    types = client.types()
    assert types[0] == "ready" and types[-1] == "exit", types
    expected = ["ready", "session", "prompt", "task", "turn_start",
                "tool_start", "tool_end", "event",                 # write_file
                "tool_start", "approval", "tool_end", "event",     # delete_file
                "tool_start", "question", "tool_end", "event",     # ask_user
                "delta", "delta", "final", "turn_end", "prompt", "exit"]
    assert in_order(types, expected), types

    ready = client.events[0]
    assert ready["v"] == 1
    for key in ("protocol", "mouse", "model", "provider", "workspace", "version",
                "columns", "rows", "effort", "configured"):
        assert key in ready, key
    assert (ready["columns"], ready["rows"]) == (100, 30), ready
    assert ready["protocol"] == "json" and ready["mouse"] in ("off", "on")

    tools = [(event["action"], event.get("status")) for event in client.of("tool_end")]
    assert tools == [("write_file", "done"), ("delete_file", "done"),
                     ("ask_user", "done")], tools
    rows = client.of("event")
    created = [row for row in rows if row["kind"] == "file_create"]
    assert created and created[0]["detail"]["added"] == 2, rows
    assert created[0]["level"] == 2 and created[0]["detail"]["paths"] == ["notes.txt"]

    approval = client.of("approval")[0]
    assert approval["action"] == "delete_file" and approval["pattern"] == ""
    assert [choice["key"] for choice in approval["choices"]] == ["y", "n"]
    assert "notes.txt" in approval["question"]

    question = client.of("question")[0]
    assert question["keys"] == ["1", "2"] and question["options"] == ["Red", "Blue"]
    assert question["question"] == "Which colour?" and "1. Red" in question["text"]
    assert 'The user chose 2: "Blue"' in model.said(), model.said()

    streamed = "".join(event["text"] for event in client.of("delta"))
    assert streamed == ANSWER["message"]
    assert client.of("final")[0]["text"] == ANSWER["message"]
    end = client.of("turn_end")[0]
    assert end["answer"] == ANSWER["message"] and end["outcome"] == ""
    assert end["suggestion"] == "Review the notes"
    # The next prompt's shadow text is the suggestion, as it is in the terminal.
    assert client.of("prompt")[1]["placeholder"] == "Review the notes"
    assert left == [], left                     # the approved delete really ran
    assert client.of("exit")[0]["reason"] == "quit"


def test_the_status_row_and_the_meter_are_sent_as_numbers():
    code, client, _, _ = drive(script(tasks=["write the notes"]), [WRITE, ANSWER])
    statuses = [(event["state"], event["label"], event["progress"])
                for event in client.of("status")]
    assert statuses[0] == ("thinking", "THINKING", None), statuses
    assert ("working", "Just Started!", 10) in statuses
    assert ("working", "FINALIZING", 95) in statuses
    assert ("working", "Complete!", 100) in statuses
    assert statuses[-1][0] == "idle"
    # The bar only ever moves forward within a turn, as the terminal's does.
    bars = [progress for state, _, progress in statuses if state == "working"]
    assert bars == sorted(bars), bars
    meters = client.of("meter")
    assert meters and meters[-1]["lines_added"] == 2, meters[-1:]
    assert meters[-1]["tokens_out"] > 0 and meters[-1]["tokens_in"] > 0
    caption = client.of("prompt")[0]["caption"]
    assert caption["clock"] == "09:30:00" and set(caption) == {"clock", "provider", "model"}


def test_the_plan_panel_is_sent_when_it_changes():
    plan = {"action": "plan", "operation": "create",
            "steps": ["Write the notes", "Check them"]}
    clear = {"action": "plan", "operation": "clear"}
    # Mid-sentence: a line that STARTS with /plan is the slash command.
    code, client, _, _ = drive(script(tasks=["write the notes, /plan it first"]),
                               [plan, clear, ANSWER])
    plans = [[step["title"] for step in event["steps"]] for event in client.of("plan")]
    # Sent at the start, and again each time it changed -- never when it did not.
    assert plans == [[], ["Write the notes", "Check them"], []], plans
    step = client.of("plan")[1]["steps"][0]
    assert set(step) == {"id", "position", "title", "status"}, step
    for kind in ("review", "verify", "agents"):
        assert client.of(kind), kind


# --- approvals, questions and the front end going away ---------------------

def test_a_refused_command_runs_nothing():
    command = {"action": "bash", "command": "rm notes.txt"}
    code, client, model, left = drive(
        script(tasks=["tidy up"], approvals=["n"]), [WRITE, command, ANSWER])
    approval = client.of("approval")[0]
    assert approval["action"] == "bash" and approval["pattern"], approval
    assert [choice["key"] for choice in approval["choices"]] == ["y", "a", "n"]
    assert approval["hint"].startswith("Type y and Enter"), approval["hint"]
    assert left == ["notes.txt"], left
    assert "declined" in model.said(2).lower() or "refus" in model.said(2).lower(), \
        model.said(2)


def test_a_dismissed_question_is_told_to_the_model_as_dismissed():
    code, client, model, _ = drive(script(tasks=["ask me"], keys=["9", ""]),
                                   [ASK, ANSWER])
    # "9" was never offered: it is reported and waited past, never read as a
    # choice, and the empty key that follows is the user pressing Esc.
    errors = client.of("protocol_error")
    assert len(errors) == 1 and errors[0]["id"] == client.of("question")[0]["id"], errors
    import agent_ask
    assert agent_ask.DISMISSED in model.said(), model.said()


def test_the_front_end_dying_mid_approval_denies_it_and_ends_cleanly():
    code, client, model, left = drive(
        script(tasks=["write then delete"], approvals=[None]), [WRITE, DELETE, ANSWER],
        dies=True)
    assert code == 0
    assert left == ["notes.txt"], left             # nothing was approved
    assert client.of("exit")[0]["reason"] == "eof"
    ends = client.of("turn_end")
    assert len(ends) == 1 and ends[0]["answer"] == "", ends
    assert len(model.asked) == 2, "the turn went on after nobody was watching"


def test_an_interrupt_stops_the_turn_and_the_session_carries_on():
    def slow(on_event):
        on_event(("first_content", ""))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            time.sleep(0.01)
        return ANSWER

    def interrupt(client, event):
        if event["type"] == "status" and event["label"] == "Just Started!" \
                and not client.of("turn_end"):
            return [{"v": 1, "type": "interrupt"}]
        return None

    started = time.monotonic()
    code, client, model, _ = drive(
        script(tasks=["take forever", "now answer"], also=interrupt), [slow, ANSWER])
    assert time.monotonic() - started < 15, "the interrupt never landed"
    ends = client.of("turn_end")
    assert [end["outcome"] for end in ends] == ["you stopped it with Ctrl-C", ""], ends
    assert ends[1]["answer"] == ANSWER["message"]
    notices = [event for event in client.of("notice") if event["level"] == "warning"]
    assert any("Task cancelled" in event["text"] for event in notices), notices
    assert code == 0 and client.of("exit")[0]["reason"] == "quit"


def test_an_interrupt_while_idle_is_ignored():
    def before(client):
        client.send({"v": 1, "type": "interrupt"})
    code, client, _, _ = drive(script(tasks=["/plan"]), [ANSWER], before=before)
    assert code == 0 and client.of("command"), client.types()


# --- the input side ----------------------------------------------------------

def test_unknown_messages_are_ignored_and_unusable_ones_are_reported():
    def first(client, event):
        if event["type"] == "prompt" and not client.of("command"):
            client.send({"v": 1, "type": "from_a_newer_front_end", "x": 1})
            client.send("this is not json")
            client.send({"v": 1, "type": "submit"})          # no text
            client.send([1, 2, 3])
            return [{"v": 1, "type": "submit", "text": "/plan"}]
        return None
    code, client, _, _ = drive(script(also=first), [ANSWER])
    assert code == 0
    errors = client.of("protocol_error")
    assert len(errors) == 3, errors
    assert client.of("command")[0]["title"], client.of("command")
    assert client.of("exit")[0]["reason"] == "quit"


def test_a_slash_command_answers_as_a_command_and_never_starts_a_turn():
    code, client, model, _ = drive(script(tasks=["/plan"]), [ANSWER])
    assert "turn_start" not in client.types()
    task = client.of("task")[0]
    assert task["text"] == "/plan" and task["caption"]["clock"] == "09:30:00"
    command = client.of("command")[0]
    assert command["ok"] is True and command["rows"], command
    assert set(command) >= {"title", "ok", "rows", "note", "prompt_for", "to_menu"}
    assert model.asked == []


def test_reload_rereads_the_settings_and_sends_them_back():
    # The prompt stays open across a reload: `settings` comes back and no
    # second `prompt` does, so the script quits on the settings.
    def reload(client, event):
        if event["type"] == "prompt":
            return [{"v": 1, "type": "reload"}]
        if event["type"] == "settings":
            return [{"v": 1, "type": "quit"}]
        return None
    code, client, _, _ = drive(script(also=reload), [ANSWER])
    settings = client.of("settings")
    assert len(settings) == 1 and settings[0]["protocol"] == "json", settings


def test_a_workspace_that_needs_confirming_is_asked_through_an_approval():
    code, client, _, _ = drive(script(approvals=["n"]), [ANSWER],
                               files={"main.py": "print(1)\n"})
    approval = client.of("approval")[0]
    assert approval["action"] == "workspace", approval
    assert code == agent_backend.EXIT_WORKSPACE
    assert client.of("exit")[0]["reason"] == "workspace"
    assert "session" not in client.types()


# --- the output side ---------------------------------------------------------

def test_stdout_carries_nothing_but_json_lines():
    odd = "line\nbreak   and   and \x85 ✓ é"

    def noisy(on_event):
        print("stray print from somewhere")
        sys.stdout.write("a stray write\n")
        return {"action": "end_conversation", "message": odd}

    captured = io.StringIO()
    saved = sys.stderr
    sys.stderr = captured
    try:
        code, client, _, _ = drive(script(tasks=["be noisy"]), [noisy])
    finally:
        sys.stderr = saved
    assert code == 0
    for raw in client.lines:
        assert raw.endswith(b"\n") and raw.count(b"\n") == 1, raw
        assert b"\r" not in raw, raw
        text = raw.decode("utf-8")
        assert len(text.splitlines()) == 1, raw          # Python's idea of a line
        event = json.loads(text)
        assert isinstance(event, dict) and event["v"] == 1 and event["type"], raw
    assert b"stray" not in b"".join(client.lines)
    assert "stray print from somewhere" in captured.getvalue()
    final = client.of("final")[0]
    assert final["text"] == odd                          # non-ASCII survives exactly
    raw = [line for line in client.lines if b'"final"' in line][0]
    assert "✓".encode("utf-8") in raw and b"\\u2028" in raw


def test_the_real_process_speaks_only_json_lines():
    """`python TMT.py --backend-stdio`, as the front end starts it. No model
    is reached: a command and a quit are enough to cross the handshake, the
    session header, a prompt and the exit."""
    box = Workspace()
    try:
        messages = [HELLO, {"v": 1, "type": "submit", "text": "/plan"},
                    {"v": 1, "type": "quit"}]
        stdin = "".join(json.dumps(message) + "\n" for message in messages)
        env = dict(os.environ)
        env.pop("TMT_UI", None)
        done = subprocess.run(
            [sys.executable, str(REPO / "TMT.py"), "--backend-stdio", "--dir",
             str(box.path)],
            input=stdin.encode("utf-8"), capture_output=True, timeout=120,
            cwd=str(box.path), env=env)
    finally:
        box.close()
    lines = done.stdout.split(b"\n")
    assert lines[-1] == b"", done.stdout[-200:]
    events = [json.loads(line.decode("utf-8")) for line in lines[:-1]]
    types = [event["type"] for event in events]
    assert done.returncode == 0, (done.returncode, done.stderr[-2000:])
    assert types[0] == "ready" and types[-1] == "exit", types
    assert in_order(types, ["ready", "session", "prompt", "task", "command",
                            "prompt", "exit"]), types
    assert events[-1]["reason"] == "quit", events[-1]


def _real_backend_that_ends(how):
    """The real process, ended by a `quit` message or by closing its stdin.

    Its stdin stays OPEN until the process has gone, as a live front end's
    does when it sends `quit`: the stdin reader thread is then still blocked in
    a read when the main thread finishes, and that is what interpreter shutdown
    used to die on. (`subprocess.run(input=...)` closes stdin first, so the
    thread has already ended and the bug cannot show.)
    """
    box = Workspace()
    try:
        env = dict(os.environ)
        env.pop("TMT_UI", None)
        proc = subprocess.Popen(
            [sys.executable, str(REPO / "TMT.py"), "--backend-stdio", "--dir",
             str(box.path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(box.path), env=env)
        watchdog = threading.Timer(120, proc.kill)
        watchdog.start()
        try:
            proc.stdin.write((json.dumps(HELLO) + "\n").encode("utf-8"))
            proc.stdin.flush()
            events = []
            for raw in proc.stdout:
                event = json.loads(raw.decode("utf-8"))
                events.append(event)
                if event["type"] == "prompt":
                    if how == "quit":
                        proc.stdin.write(b'{"v":1,"type":"quit"}\n')
                        proc.stdin.flush()
                    else:
                        proc.stdin.close()
                if event["type"] == "exit":
                    break
            code = proc.wait(timeout=60)
            stderr = proc.stderr.read()
        finally:
            watchdog.cancel()
            proc.kill()
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    pipe.close()
                except OSError:
                    pass
    finally:
        box.close()
    return events, code, stderr


def test_the_real_process_ends_cleanly_after_quit_with_stdin_still_open():
    events, code, stderr = _real_backend_that_ends("quit")
    assert events[-1]["type"] == "exit" and events[-1]["reason"] == "quit", events[-1]
    assert b"Fatal Python error" not in stderr, stderr[-2000:]
    assert code == 0, (code, stderr[-2000:])


def test_the_real_process_ends_cleanly_when_stdin_closes():
    events, code, stderr = _real_backend_that_ends("eof")
    assert events[-1]["type"] == "exit" and events[-1]["reason"] == "eof", events[-1]
    assert b"Fatal Python error" not in stderr, stderr[-2000:]
    assert code == 0, (code, stderr[-2000:])


# The real process with only the model replaced. The stand-in model starts a
# child that reads stdin, as a `bash` command or a test run would, and reports
# what that child saw; nothing here reaches a network.
_CHILD_PROGRAM = """
import json, subprocess, sys
sys.path.insert(0, {repo!r})
import TMT

def model(messages, on_event=None, **kwargs):
    try:
        done = subprocess.run(
            [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"],
            capture_output=True, text=True, timeout=20)
        seen = done.stdout.strip()
    except subprocess.TimeoutExpired:
        seen = "TIMEOUT"
    return json.dumps({{"action": "end_conversation", "message": seen}})

TMT.ask_model = model
sys.exit(TMT.main(["--backend-stdio", "--dir", {work!r}]))
"""


def test_a_task_starts_at_once_and_a_child_of_the_backend_sees_an_empty_stdin():
    """The front end's stdin is a live pipe. A child that inherited it hung for
    git's whole 30 second timeout (the first git call of a turn comes before
    `turn_start`), and one that read it would swallow the front end's messages.
    So `task` -> `turn_start` must be quick, and a child started with no stdin of
    its own must see the end of an empty one while the pipe stays open."""
    box = Workspace(git=True)
    proc = None
    try:
        env = dict(os.environ)
        env.pop("TMT_UI", None)
        program = _CHILD_PROGRAM.format(repo=str(REPO), work=str(box.path))
        proc = subprocess.Popen(
            [sys.executable, "-c", program],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(box.path), env=env)
        watchdog = threading.Timer(120, proc.kill)
        watchdog.start()
        try:
            def send(message):
                proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
                proc.stdin.flush()

            def read_until(kind):
                for raw in proc.stdout:
                    event = json.loads(raw.decode("utf-8"))
                    if event["type"] == kind:
                        return event
                raise AssertionError("stdout ended before %r: %r"
                                     % (kind, proc.stderr.read()[-2000:]))

            send(HELLO)
            read_until("prompt")
            send({"v": 1, "type": "submit", "text": "go"})
            asked = time.monotonic()
            read_until("turn_start")
            waited = time.monotonic() - asked
            final = read_until("final")
            read_until("prompt")
            send({"v": 1, "type": "quit"})
            read_until("exit")
            proc.wait(timeout=60)
        finally:
            watchdog.cancel()
    finally:
        if proc is not None:
            proc.kill()
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    pipe.close()
                except OSError:
                    pass
        box.close()
    assert waited < 15, "task -> turn_start took %.1fs" % waited
    assert final["text"] == "''", final


def test_the_backend_points_descriptor_0_at_the_null_device_and_gives_it_back():
    """In-process, with a real descriptor 0 swapped for a pipe: the channel is
    moved to a private copy, a child with no stdin of its own reads an empty
    one, and descriptor 0 is the pipe again afterwards -- even while the reader
    thread is blocked in a read, which on Windows made a `dup2` from the
    reader's own descriptor wait for the front end and the process never end."""
    try:
        saved = os.dup(0)
    except OSError:
        return                             # no descriptor 0 here to swap
    in_r, in_w = os.pipe()
    os.dup2(in_r, 0)
    os.close(in_r)
    try:
        class Stdin:                       # what `sys.stdin` is, as far as the channel can tell
            def fileno(self):
                return 0

        channel = agent_backend.Channel(Stdin(), io.BytesIO())
        spare = agent_backend._private_stdin(channel)
        assert spare is not None and channel._fd not in (0, spare)
        assert not os.get_inheritable(channel._fd)
        lines = []
        reader = threading.Thread(target=lambda: lines.append(channel._readline()),
                                  daemon=True)
        reader.start()
        time.sleep(0.3)                    # blocked in its read now
        done = subprocess.run(
            [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"],
            capture_output=True, text=True, timeout=20)
        assert done.stdout.strip() == "''", (done.stdout, done.stderr)
        giver = threading.Thread(target=agent_backend._restore_stdin, args=(spare,),
                                 daemon=True)
        giver.start()
        giver.join(10)
        assert not giver.is_alive(), "restoring descriptor 0 waited for the reader"
        os.write(in_w, b'{"v":1,"type":"quit"}\n')
        reader.join(10)
        assert lines == [b'{"v":1,"type":"quit"}\n'], lines      # the channel kept the pipe
        os.write(in_w, b"back\n")
        assert os.read(0, 16) == b"back\n"                       # descriptor 0 is the pipe again
        os.close(channel._fd)
    finally:
        os.close(in_w)
        os.dup2(saved, 0)
        os.close(saved)
    # A channel that does not read descriptor 0 is left alone.
    other = agent_backend.Channel(io.BytesIO(b""), io.BytesIO())
    assert agent_backend._private_stdin(other) is None and other._fd is None


# --- the terminal UI is not changed by any of this ------------------------------

# What the terminal UI drew for `_python_ui_session` when the backend was
# written, with the clock, the tip, the provider and model, the system prompt,
# the terminal size and the workspace pinned. If the Python UI is changed ON
# PURPOSE, regenerate it: print(repr(_python_ui_session())) and paste.
GOLDEN = "".join([
    '\n',
    ' ████████ ███    ███ ████████\n',
    '    ██    ████  ████    ██   \n',
    '    ██    ██ ████ ██    ██   \n',
    '    ██    ██  ██  ██    ██   \n',
    '    ██    ██      ██    ██   \n',
    ' Sun 25 Oct 2026\n',
    ' ws\n',
    ' Tip \xb7 /notes shows what TMT knows\n',
    '\n',
    ' ' * 72 + '09:30:00 \xb7 Provider \xb7 Model\n',
    ' ' + '─' * 98 + '\n',
    ' > Describe your first task\n',
    ' ' + '─' * 98 + '\n',
    ' ' * 72 + '09:30:00 \xb7 Provider \xb7 Model\n',
    ' > write the notes\n',
    ' \xb7 Writing the notes.\n',
    '\n',
    '   ▸ Created file: notes.txt\n',
    '     +2 -0\n',
    '\n',
    ' ▲ Delete cancelled\n',
    ' ▸ Ask User Which colour?\n',
    '\r\x1b[2K' + '█' * 12 + ' 100% Complete!\r\x1b[2K ┌' + '─' * 96 + '┐\n',
    ' │  Done — wrote notes.txt ✓' + ' ' * 70 + '│\n',
    ' └' + '─' * 96 + '┘\n',
    '\r\x1b[2K\n',
    '\n',
    ' +2 lines, -0 lines, ~230 context, 168 out' + ' ' * 30 + '09:30:00 \xb7 Provider \xb7 Model\n',
    ' ' + '─' * 98 + '\n',
    ' > Review the notes\n',
    ' ' + '─' * 98 + '\n',
    '\n',
    '\n',
    ' +2 lines, -0 lines, ~230 context, 168 out' + ' ' * 30 + '09:30:00 \xb7 Provider \xb7 Model\n',
    ' ' + '─' * 98 + '\n',
    ' > Review the notes\n',
    ' ' + '─' * 98 + '\n',
    '\n',
    '===== console =====\n',
])


def _python_ui_session():
    """The terminal UI through one scripted turn, as the suite always drives it."""
    with Pinned():
        box = Workspace()
        screen = io.StringIO()
        console = _Console(["write the notes", "", "quit"])
        names = ("console", "ensure_api_key", "run_startup", "ensure_git_identity",
                 "ask_model", "get_system_prompt")
        saved = {name: getattr(TMT, name) for name in names}
        saved_facts, saved_tip = agent_menu.status_facts, agent_tips.next_tip
        previous = Path.cwd()
        try:
            os.chdir(str(box.path))
            TMT.console = console
            TMT.ensure_api_key = lambda: True
            TMT.run_startup = lambda **kwargs: "start"
            TMT.ensure_git_identity = lambda *a, **k: None
            TMT.ask_model = Model([WRITE, DELETE, ASK, ANSWER])
            TMT.get_system_prompt = lambda *a, **k: "You are TMT."
            agent_menu.status_facts = lambda *a, **k: ("Provider", "Model", "ws")
            agent_tips.next_tip = lambda: ("/notes", "shows what TMT knows")
            with contextlib.redirect_stdout(screen):
                TMT.main([])
        finally:
            os.chdir(str(previous))
            for name, value in saved.items():
                setattr(TMT, name, value)
            agent_menu.status_facts, agent_tips.next_tip = saved_facts, saved_tip
            workspace = str(box.path)
            box.close()
    drawn = screen.getvalue() + "\n===== console =====\n" + "\n".join(console.printed)
    return drawn.replace(workspace, "<WS>")


class _Console:
    def __init__(self, answers):
        self.answers = list(answers)
        self.printed = []

    def input(self, prompt=""):
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def print(self, *args, **kwargs):
        self.printed.append(" ".join(str(value) for value in args))


def test_the_python_ui_draws_exactly_what_it_drew_before_the_backend():
    """Byte for byte, before and after a headless run in the same process:
    every name the backend stands in for is put back, and the terminal UI's
    output for a fixed turn is the one recorded when the backend was written."""
    originals = {name: getattr(TMT, name) for name in agent_backend.SEAMS}
    first = _python_ui_session()
    drive(script(tasks=["write the notes"], approvals=["y"], keys=["1"]),
          [WRITE, DELETE, ASK, ANSWER])
    for name, original in originals.items():
        assert getattr(TMT, name) is original, name
    second = _python_ui_session()
    assert first == second, (first, second)
    assert first == GOLDEN, "\n" + first
