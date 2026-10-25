"""`--backend-stdio`: the agent with no terminal, spoken to in JSON lines.

The TypeScript front end (`tui/`) owns the terminal and starts this process as
its backend. Everything the session loop would draw becomes an event on
stdout, and everything the user does comes back as a message on stdin. The
wire format is `docs/tui-protocol.md`, and that document is the contract: a
change to one is a change to both.

WHY THERE IS NO SECOND LOOP HERE
--------------------------------
There is exactly one agent loop, `TMT._session_loop`, and this module does not
copy it. A copy would be a second set of completion gates, a second checkpoint
path and a second reading of the policy, and the two would drift the first
time either changed.

Instead the loop's SCREENS are swapped for the length of the run. Everything
the loop draws or asks through is a module-level name in TMT.py -- the header,
the prompt box, the live region, the status row, the transcript, the answer
box, the approval and question askers, the console -- and `Headless` supplies
an object of the same shape for each one (`SEAMS`). They are installed over
TMT's own names when the run starts and put back when it ends. That is the
seam the test suite has always driven the loop through (`TMT.console`,
`TMT.ask_model`), and it is why TMT.py itself does not change: a run without
`--backend-stdio` executes the code it always executed, byte for byte, because
none of it was edited.

A rename in TMT.py that this module did not follow fails safe rather than
loud: the real screen would draw to stderr (stdout is rebound there for the
run) and read an empty stdin, so the session ends instead of corrupting the
protocol. `test_agent_backend_protocol` pins every name in SEAMS to the loop's
source, so the rename is caught there first.

Standard library only, like the rest of TMT.
"""

import io
import itertools
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

import agent_ask
import agent_config
import agent_menu
import agent_ui
from agent_actions import ACTION_LABELS, END_CONVERSATION

# Every event carries it as "v". A front end that does not know the version in
# `ready` must stop rather than guess at fields.
PROTOCOL_VERSION = 1

# The names in TMT.py this module stands in for during a run. Each one is what
# the session loop draws or asks through; nothing else in the loop touches the
# terminal.
SEAMS = ("console", "render_status", "render_task", "render_command",
         "render_response", "PromptBox", "TypeAhead", "LiveUI", "LiveRelay",
         "Transcript", "Session", "execute_action", "_command_approval",
         "_question_asker")

EXIT_OK = 0
EXIT_CRASHED = 1
EXIT_WORKSPACE = 2            # the workspace was refused, as a usage error is
EXIT_INTERRUPTED = 130        # the conventional code for a SIGINT

# How often a streaming reply may send a `meter`. The readout moves with every
# chunk of output, and one event per chunk would be most of the traffic.
METER_INTERVAL = 0.25

# How long the main thread sleeps between checks while it waits on the front
# end. Short, because a KeyboardInterrupt fired by `interrupt` is raised at the
# next check and the user is waiting for the turn to stop.
_POLL = 0.1


# --- the pipes -------------------------------------------------------------

class _Waiter:
    """One request the backend is blocked on: an approval or a question."""

    __slots__ = ("reply_type", "event", "reply")

    def __init__(self, reply_type):
        self.reply_type = reply_type
        self.event = threading.Event()
        self.reply = None


class Channel:
    """Events out, messages in.

    Lines are written whole under one lock, because writes come from more than
    one thread: a background agent nudges the panel from its own. Reading
    happens on one daemon thread for the whole run, because an `interrupt` has
    to be heard while the main thread is inside a model call.
    """

    def __init__(self, instream, outstream):
        self._in = getattr(instream, "buffer", instream)
        self._out = getattr(outstream, "buffer", outstream)
        self._binary = not isinstance(self._out, io.TextIOBase)
        self._write_lock = threading.Lock()
        self._state = threading.Lock()
        self._ids = itertools.count(1)
        self._waiting = {}
        self._eof = False
        self._broken = False
        self._announced = False
        self._thread = None
        # What the main thread picks up: submitted text, quit, reload, hello
        # and the end of the input. Approvals and answers do not go here; they
        # go straight to the request that is waiting for them.
        self.inbox = queue.Queue()
        # Until `ready` has gone out nothing may be answered on stdout, so a
        # protocol error before the handshake is logged instead.
        self.ready = False
        self.on_interrupt = None
        self.on_close = None

    @property
    def closed(self):
        """Whether the front end has gone: its stdin ended or its stdout broke."""
        return self._eof or self._broken

    # --- writing --------------------------------------------------------

    def send(self, event_type, **fields):
        """Write one event as one line. Returns whether it was written."""
        event = {"v": PROTOCOL_VERSION, "type": event_type}
        event.update(fields)
        line = json.dumps(event, ensure_ascii=False, default=str,
                          separators=(",", ":"))
        # JSON allows these three raw inside a string, and a reader that splits
        # on Python's idea of a line break (str.splitlines) would cut the event
        # in two. Escaped, every line break in the stream is a real "\n".
        line = (line.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
                .replace("\x85", "\\u0085"))
        with self._write_lock:
            if self._broken:
                return False
            try:
                if self._binary:
                    # backslashreplace only ever fires on a lone surrogate, and
                    # those can only sit inside a string, where "\udXXX" is a
                    # valid JSON escape.
                    self._out.write((line + "\n").encode("utf-8", "backslashreplace"))
                else:
                    self._out.write(line + "\n")
                self._out.flush()
                return True
            except (OSError, ValueError):
                self._broken = True
        self._lost()
        return False

    def problem(self, message, **fields):
        """A message from the front end that could not be used."""
        _log("protocol error: %s %s" % (message, fields or ""))
        if self.ready:
            self.send("protocol_error", message=message, **fields)

    # --- reading --------------------------------------------------------

    def start(self):
        self._thread = threading.Thread(target=self._read, daemon=True,
                                        name="tmt-backend-stdin")
        self._thread.start()

    def _read(self):
        try:
            while True:
                raw = self._in.readline()
                if not raw:
                    break
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                text = raw.strip().lstrip("\ufeff")
                if text:
                    self._take(text)
        except (OSError, ValueError):
            pass
        with self._state:
            self._eof = True
        self._lost()

    def _take(self, text):
        try:
            message = json.loads(text)
        except ValueError as error:
            self.problem("not JSON: %s" % error, line=text[:200])
            return
        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
            self.problem("a message is a JSON object with a string \"type\"",
                         line=text[:200])
            return
        kind = message["type"]
        if kind in ("approval_reply", "answer"):
            self._reply(message)
        elif kind == "interrupt":
            if self.on_interrupt is not None:
                self.on_interrupt()
        elif kind in ("hello", "resize"):
            _resize(message)
            if kind == "hello":
                self.inbox.put(("hello", message))
        elif kind == "submit":
            if not isinstance(message.get("text"), str):
                self.problem("submit needs \"text\", a string")
                return
            self.inbox.put(("submit", message["text"]))
        elif kind in ("quit", "reload"):
            self.inbox.put((kind,))
        else:
            # Ignored, never answered: an older backend talking to a newer
            # front end must keep working.
            _log("ignored a message of unknown type %r" % kind)

    def _reply(self, message):
        ident = message.get("id")
        with self._state:
            waiter = (self._waiting.get(ident)
                      if isinstance(ident, int) and not isinstance(ident, bool)
                      else None)
            if waiter is not None and waiter.reply_type == message["type"]:
                waiter.reply = message
                waiter.event.set()
                return
        # Late or duplicated: the request was answered already, or the turn it
        # belonged to has gone. Nothing is waiting, so nothing is said.
        _log("no request %r is waiting for a %s" % (ident, message["type"]))

    def _lost(self):
        """The front end has gone. Wake everything that is waiting on it."""
        with self._state:
            waiting = list(self._waiting.values())
            first = not self._announced
            self._announced = True
        for waiter in waiting:
            waiter.event.set()
        if first:
            self.inbox.put(("eof",))
            if self.on_close is not None:
                self.on_close()

    # --- the main thread's waits ----------------------------------------

    def wait_hello(self):
        """The front end's `hello`, or None when the input ended first."""
        while True:
            try:
                item = self.inbox.get(timeout=_POLL)
            except queue.Empty:
                continue
            if item[0] == "hello":
                return item[1]
            if item[0] == "eof":
                return None
            # `ready` has to be the first event, so nothing is answered yet.
            _log("dropped %r before the handshake" % (item[0],))

    def request(self, event_type, reply_type, accept=None, **fields):
        """Send a request and block until it is answered.

        Returns the reply message, or None when the front end went away
        before answering -- which every caller reads as "nobody is there",
        the same answer the terminal UI gives when it has no terminal.

        A reply that `accept` refuses is reported and waited past, so a
        front end that sent a key it was never offered cannot have it read as
        a choice.
        """
        waiter = _Waiter(reply_type)
        with self._state:
            if self.closed:
                return None
            ident = next(self._ids)
            self._waiting[ident] = waiter
        try:
            self.send(event_type, id=ident, **fields)
            while True:
                # Short waits, not one long one: the KeyboardInterrupt that an
                # `interrupt` fires is raised at the next check, and it is
                # deliberately not caught -- it ends the turn, as Ctrl-C at the
                # terminal's prompt does.
                while not waiter.event.wait(_POLL):
                    pass
                with self._state:
                    reply = waiter.reply
                    if reply is None:
                        return None
                    if accept is None or accept(reply):
                        return reply
                    waiter.reply = None
                    waiter.event.clear()
                self.problem("that reply does not answer request %d" % ident,
                             id=ident)
        finally:
            with self._state:
                self._waiting.pop(ident, None)


def _resize(message):
    """Take the front end's terminal size as this process's own.

    `shutil.get_terminal_size` reads COLUMNS and LINES first, and everything
    the agent lays out to a width -- the question block an `ask_user` puts,
    the meter's shorter forms, a command's rows -- asks it. Without this they
    would be laid out for an 80-column terminal that does not exist.
    """
    for key, name in (("columns", "COLUMNS"), ("rows", "LINES")):
        value = message.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            os.environ[name] = str(value)


def _log(text):
    """A line for the front end's log file. stderr is the only place for it."""
    try:
        sys.stderr.write("tmt-backend: %s\n" % text)
        sys.stderr.flush()
    except Exception:
        pass


# --- small readers ---------------------------------------------------------

_RED = re.compile(r"\[(?:bold )?red\]")
_YELLOW = re.compile(r"\[(?:bold )?yellow\]")
_MARKUP = re.compile(r"(?<!\\)\[(?:/|/?[A-Za-z#@][^\[\]]*)\]")


def _unmarkup(text):
    """Console markup as the words it stands for: "[red]x[/red]" is "x"."""
    return _MARKUP.sub("", str(text)).replace("\\[", "[")


def _plain(value):
    return agent_ui.strip_ansi(str(value if value is not None else ""))


def _caption(moment=None):
    """The three facts the prompt box's caption states, unpainted."""
    clock = agent_menu._clock(moment)
    try:
        provider, model, _ = agent_menu.status_facts()
    except Exception:
        provider = model = ""
    return {"clock": clock.strftime("%H:%M:%S"), "provider": str(provider),
            "model": str(model)}


def _target(obj):
    """What an action is about, in the request's own words, or ""."""
    for key in ("path", "query", "pattern", "url", "app", "command", "question"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return ""


def _version():
    """TMT's version as package.json states it, or "" when it cannot be read."""
    try:
        path = Path(agent_config.__file__).resolve().parent / "package.json"
        return str(json.loads(path.read_text(encoding="utf-8")).get("version") or "")
    except Exception:
        return ""


def refresh_settings():
    """Re-read every saved setting a session runs under.

    The same set `main` and `_return_to_menu` read, and for their reason: a
    setting written by a screen and never re-read lasts until a restart.
    Not `refresh_auto_update`: the backend never updates itself.
    """
    agent_config.refresh_model()
    agent_config.refresh_effort()
    agent_config.refresh_protocol()
    agent_config.refresh_project_context()
    # Read defensively: the setting arrived with the TypeScript UI itself.
    refresh = getattr(agent_config, "refresh_mouse", None)
    if callable(refresh):
        refresh()


def settings():
    """The saved settings a front end needs, as `ready` and `settings` carry them."""
    provider_id = model_id = ""
    try:
        provider_id = str(agent_menu.current_provider() or "")
    except Exception:
        pass
    try:
        import agent_models
        model_id = str(agent_models.current_model(provider_id or None) or "")
    except Exception:
        pass
    try:
        provider, model, _ = agent_menu.status_facts()
    except Exception:
        provider, model = provider_id, model_id
    try:
        configured = bool(agent_config.OPENROUTER_API_KEY
                          or agent_menu.provider_is_configured())
    except Exception:
        configured = False
    return {"protocol": str(agent_config.PROTOCOL),
            "mouse": str(getattr(agent_config, "MOUSE", "off") or "off"),
            "effort": str(agent_config.EFFORT),
            "provider": provider_id, "provider_label": str(provider),
            "model": model_id, "model_label": str(model),
            "configured": configured, "version": _version()}


def _panels(session, manager):
    """(kind, payload) for each panel the terminal UI's right-hand column draws."""
    out = []
    if session is not None:
        plan = getattr(session, "plan", None)
        try:
            out.append(("plan", {"steps": plan.as_list() if plan is not None else []}))
        except Exception:
            pass
        review = getattr(session, "review", None)
        if review is not None:
            try:
                out.append(("review", {
                    "state": str(review.display), "headline": review.headline(),
                    "counts": dict(review.counts()), "cycles": review.cycles,
                    "required": bool(review.is_required(plan))}))
            except Exception:
                pass
        verify = getattr(session, "verify", None)
        if verify is not None:
            try:
                out.append(("verify", {
                    "state": str(verify.display), "headline": verify.headline(),
                    "checks": [check.as_dict() for check in verify.checks()],
                    "cycles": verify.cycles,
                    "required": bool(verify.is_required(plan))}))
            except Exception:
                pass
    if manager is not None:
        try:
            out.append(("agents", {"agents": [
                {"id": record.id, "number": record.number, "kind": str(record.kind),
                 "task": str(record.task or "")[:200], "status": str(record.status),
                 "activity": str(record.activity or "")}
                for record in manager.visible_agents()]}))
        except Exception:
            pass
    return out


# --- the stand-ins ---------------------------------------------------------

class _Status(agent_ui.LiveUI):
    """The turn's status row: LiveUI's own state machine, painted as events.

    A subclass rather than a copy, so the progress arithmetic -- where the bar
    starts, how much each action moves it, when it says FINALIZING -- is the
    terminal's own and cannot drift from it. Only the painting is replaced,
    and nothing animates: a front end draws its own frames.
    """

    def __init__(self, port):
        super().__init__()
        self._port = port

    def start(self):
        self._port.turn_started(self)
        super().start()
        # Interruptible from here and not before: the loop's `try` is the very
        # next statement, and an interrupt fired earlier would land outside it.
        self._port.arm(True)

    def stop(self, clear=True):
        self._port.arm(False)
        super().stop(clear)

    def _animate(self):
        return

    def _render(self, text, painter=None, optional=""):
        with self._lock:
            active = self._active
        if active:
            self._port.status("thinking", text, None)

    def _render_progress(self, label, estimate="calculating..."):
        with self._lock:
            if not self._active:
                return
            progress = self._progress
            self._label, self._estimate = label, estimate
        self._port.status("working", label, progress)

    def _clear(self):
        self._port.status("idle", "", None)

    def add_output(self, characters):
        super().add_output(characters)
        self._port.meter(soon=True)

    def settle_tokens(self, tokens):
        super().settle_tokens(tokens)
        self._port.meter()

    def turn_tokens(self):
        with self._lock:
            return self._token_total()


class _Relay:
    """LiveRelay's interface with nothing to paint: the reply streams as events."""

    running = False

    def __init__(self, port):
        self._port = port
        self._text = []
        self._closed = False
        self._said = False
        self.streamed = False

    def start(self):
        pass

    def set_status(self, text):
        # Only a slow command's relay is told a status directly (a turn's row
        # comes from `_Status`), and taking the region down clears it.
        if not self._closed and text:
            self._said = True
            self._port.status("working", _plain(text), None)

    def _done(self):
        # `finish`, `abort` and `release` are how every exit from a turn's
        # work starts, so each one also ends the window in which an
        # `interrupt` may fire: the loop's `else:` after them has no `except`.
        self._closed = True
        self._port.arm(False)
        if self._said:
            self._port.status("idle", "", None)

    def refresh(self):
        self._port.sync()

    def write_above(self, text):
        # Nothing the loop prints goes this way in headless mode -- the
        # transcript sends structured events -- so this is a fallback that
        # keeps unexpected text visible rather than dropping it.
        self._port.channel.send("text", text=_plain(text))

    def ask_below(self, text, read):
        self.write_above(text)
        return read()

    def feed(self, text):
        if not text or self._closed:
            return
        self._text.append(text)
        self.streamed = True
        self._port.channel.send("delta", text=text)

    def wait_for_reveal(self, timeout=None):
        return True

    def finish(self):
        self._done()
        return "".join(self._text)

    def abort(self):
        self._done()
        return "".join(self._text)

    def reset(self):
        if self.streamed:
            self._port.channel.send("reply_reset")
        self._text = []
        self.streamed = False

    def release(self):
        self.reset()
        self._port.arm(False)


class _Transcript(agent_ui.Transcript):
    """The turn's permanent record, sent as `event`s instead of printed rows.

    The history is kept exactly as the terminal's transcript keeps it, because
    the session record and the next-step hint are built from it. What is sent
    is what the terminal would have drawn: a `final` is the answer's own event
    and a `next_step_suggestion` is the next prompt's placeholder, so neither
    is sent here, as neither is printed there.
    """

    def __init__(self, port, history=None):
        super().__init__(history=history)
        self._port = port

    def emit(self, event):
        if event is None:
            return None
        self.history.append(event)
        level = agent_ui.PROMINENCE.get(event.kind, 0)
        if event.kind == "next_step_suggestion":
            self._port.suggestion = event.message
        elif level < 3:
            self._port.channel.send("event", kind=event.kind, message=event.message,
                                    stage=event.stage, level=level,
                                    detail=event.detail)
        return event


class _Prompt:
    """The prompt box: `ask` sends `prompt` and blocks for the next `submit`."""

    def __init__(self, port, session=None, manager=None, **unused):
        self._port = port
        port.manager = manager
        if session is not None:
            port.session = session
        self.session = session
        self.manager = manager
        self.stream = sys.stdout
        self.typeahead = None
        self.cancelled = False
        self.asked_at = None

    def ask(self, placeholder=""):
        self.cancelled = False
        self.asked_at = agent_menu._clock()
        return self._port.ask(self, placeholder)

    def panel(self):
        return None

    def running_lines(self, hint="", size=None):
        return []


class _Idle:
    """The type-ahead reader, which has nothing to read: the front end queues."""

    active = False

    def start(self):
        return False

    def stop(self, timeout=1.0):
        return False


class _Console:
    """`console` for the loop: each line it prints becomes a `notice`."""

    def __init__(self, port):
        self._port = port

    def print(self, *objects, **kwargs):
        text = " ".join(str(value) for value in objects)
        head = text.lstrip()
        level = ("error" if _RED.match(head)
                 else "warning" if _YELLOW.match(head) else "info")
        said = _unmarkup(text).strip()
        if said:
            self._port.channel.send("notice", level=level, text=said)

    def input(self, prompt=""):
        # Nothing in a headless run reads a console; the prompt box and the
        # workspace question both go to the front end. Ended input is the
        # honest answer if anything ever asks.
        raise EOFError

    @contextmanager
    def status(self, *args, **kwargs):
        yield


# --- the port --------------------------------------------------------------

class Headless:
    """The session loop's front end when the front end is a pipe.

    One per run. Holds the channel, the session and register the loop builds
    (captured as it builds them), and what a turn is doing right now.
    """

    def __init__(self, channel, interrupt=None):
        self.channel = channel
        # How an `interrupt` stops a turn: the same KeyboardInterrupt the
        # terminal's type-ahead reader raises for a Ctrl-C, so the loop ends
        # the turn on the path it already has. Injectable for tests only.
        self.interrupt = agent_menu._interrupt_main if interrupt is None else interrupt
        self.session = None
        self.manager = None
        self.suggestion = ""
        self.ended = "quit"
        self._loop = None
        self._real = {}
        self._live = None
        self._armed = False
        self._armed_lock = threading.Lock()
        self._tool = None
        self._tool_ids = itertools.count(1)
        self._task = ""
        self._status = None
        self._meter_at = 0.0
        self._sent = {}
        self._sent_lock = threading.Lock()
        channel.on_interrupt = self._interrupted
        channel.on_close = self._interrupted

    # --- installing over the loop's names --------------------------------

    @contextmanager
    def installed(self, module):
        """Put this front end in place of `module`'s screens, and take it out again."""
        stand_ins = {
            "console": _Console(self),
            "render_status": self.render_status,
            "render_task": self.render_task,
            "render_command": self.render_command,
            "render_response": self.render_response,
            "PromptBox": lambda *a, **k: _Prompt(self, **k),
            "TypeAhead": lambda *a, **k: _Idle(),
            "LiveUI": lambda *a, **k: _Status(self),
            "LiveRelay": lambda *a, **k: _Relay(self),
            "Transcript": lambda stream=None, history=None, writer=None:
                _Transcript(self, history=history),
            "Session": self.make_session,
            "execute_action": self.execute_action,
            "_command_approval": lambda *a, **k: self.approve,
            "_question_asker": lambda *a, **k: self.choose,
        }
        self._loop = module
        self._real = {name: getattr(module, name) for name in SEAMS}
        try:
            for name in SEAMS:
                setattr(module, name, stand_ins[name])
            yield self
        finally:
            for name, original in self._real.items():
                setattr(module, name, original)

    # --- the screens -----------------------------------------------------

    def render_status(self, stream=None, prompt=True, **facts):
        tip = facts.get("tip")
        if isinstance(tip, (tuple, list)):
            gesture, detail = (tuple(str(part) for part in tip) + ("", ""))[:2]
        else:
            gesture, detail = str(tip or ""), ""
        self.channel.send("session",
                          workspace=str(facts.get("workspace") or agent_config.ROOT_DIR),
                          tip={"gesture": gesture, "detail": detail})
        return 0

    def render_task(self, task, stream=None, size=None, moment=None):
        self._task = str(task or "")
        self.channel.send("task", text=self._task, caption=_caption(moment))
        return True

    def render_command(self, result, stream=None, size=None):
        rows = []
        for row in getattr(result, "rows", ()) or ():
            if isinstance(row, tuple):
                rows.append({"label": _plain(row[0] if row else ""),
                             "value": _plain(row[1] if len(row) > 1 else "")})
            else:
                rows.append({"text": _plain(row)})
        self.channel.send("command", title=_plain(getattr(result, "title", "")),
                          ok=bool(getattr(result, "ok", True)), rows=rows,
                          note=_plain(getattr(result, "note", "")),
                          prompt_for=str(getattr(result, "prompt_for", "") or ""),
                          to_menu=bool(getattr(result, "to_menu", False)))
        self.sync()
        # No rows were drawn, so the pad gives none up.
        return 0

    def render_response(self, response, stream=None):
        self.channel.send("final", text=str(response))

    # --- the prompt ------------------------------------------------------

    def ask(self, box, placeholder):
        self.sync()
        self.channel.send("prompt", placeholder=str(placeholder or ""),
                          caption=_caption(), meter=self._meter())
        while True:
            try:
                item = self.channel.inbox.get(timeout=_POLL)
            except queue.Empty:
                continue
            except KeyboardInterrupt:
                # A real Ctrl-C reached this process while nothing was running
                # (the front end should not let one through, but a delegated
                # screen can). The terminal's box answers it the same way: ask
                # again, with the notice.
                box.cancelled = True
                return ""
            kind = item[0]
            if kind == "submit":
                return item[1]
            if kind == "reload":
                refresh_settings()
                self.channel.send("settings", **settings())
                continue
            if kind == "hello":
                continue
            self.ended = "eof" if kind == "eof" else "quit"
            return None

    # --- asking ----------------------------------------------------------

    def approve(self, question, pattern=""):
        """`_command_approval`'s callable: the raw answer, or False for nobody."""
        pattern = str(pattern or "")
        loop = self._loop
        if pattern:
            hint = getattr(loop, "_APPROVE_KEYS", "%s") % pattern
            choices = [{"key": "y", "label": "Run it once"},
                       {"key": "a", "label": "Allow `%s` in this project from now on"
                        % pattern},
                       {"key": "n", "label": "Refuse"}]
        else:
            hint = getattr(loop, "_APPROVE_ONCE", "")
            choices = [{"key": "y", "label": "Allow it"},
                       {"key": "n", "label": "Refuse"}]
        tool = self._tool or {}
        reply = self.channel.request(
            "approval", "approval_reply",
            accept=lambda message: isinstance(message.get("answer"), str),
            question=str(question), pattern=pattern, hint=hint, choices=choices,
            action=str(tool.get("action") or ""))
        if reply is None:
            # Nobody is there to approve anything, which is no.
            return False
        # The raw answer, as the terminal's `input()` gives it: what "yes" and
        # "always" mean belongs to the caller, not to this.
        return reply["answer"].strip().lower()

    def confirm(self, prompt):
        """`resolve_workspace`'s question, put as an approval. Never blocks a dead UI."""
        text = " ".join(str(prompt or "").split())
        reply = self.channel.request(
            "approval", "approval_reply",
            accept=lambda message: isinstance(message.get("answer"), str),
            question=text, pattern="", hint=text,
            choices=[{"key": "y", "label": "Use it as the workspace"},
                     {"key": "n", "label": "Stop"}],
            action="workspace")
        return "n" if reply is None else reply["answer"]

    def choose(self, text, keys):
        """`_question_asker`'s callable: a key, "" for dismissed, None for nobody."""
        accepted = tuple(str(key) for key in keys)
        asked = None
        tool = self._tool
        if isinstance(tool, dict) and tool.get("action") == "ask_user":
            try:
                asked, _ = agent_ask.parse(tool)
            except Exception:
                asked = None
        reply = self.channel.request(
            "question", "answer",
            accept=lambda message: message.get("key") in (None, "") + accepted,
            text=_plain(text), keys=list(accepted),
            question=asked.question if asked is not None else None,
            options=list(asked.options) if asked is not None else None)
        if reply is None:
            return None
        return reply.get("key")

    # --- tools -----------------------------------------------------------

    def execute_action(self, obj, *args, **kwargs):
        real = self._real["execute_action"]
        action = obj.get("action") if isinstance(obj, dict) else None
        # The ending is the answer, not a tool: the terminal draws no row for
        # it and the answer arrives as `final`.
        if action is None or action == END_CONVERSATION:
            return real(obj, *args, **kwargs)
        ident = next(self._tool_ids)
        action = str(action)
        self._tool = obj
        self.channel.send("tool_start", id=ident, action=action,
                          label=ACTION_LABELS.get(action, action), target=_target(obj))
        try:
            result = real(obj, *args, **kwargs)
        except KeyboardInterrupt:
            self.channel.send("tool_end", id=ident, action=action, status="interrupted")
            raise
        except Exception as error:
            self.channel.send("tool_end", id=ident, action=action, status="raised",
                              error="%s: %s" % (type(error).__name__, error))
            raise
        finally:
            self._tool = None
        self.channel.send("tool_end", id=ident, action=action, status="done")
        self.sync()
        self.meter()
        return result

    # --- the turn --------------------------------------------------------

    def make_session(self, *args, **kwargs):
        """A real Session whose `record` also says the turn is over.

        `record` is the one place every turn reaches however it ended, and the
        only place its outcome is handed to anything -- which is what
        `turn_end` has to carry. The session itself is untouched.
        """
        session = self._real["Session"](*args, **kwargs)
        record = session.record

        def recorded(task, answer="", history=None, outcome=""):
            result = record(task, answer, history, outcome)
            self.turn_ended(task, answer, outcome)
            return result

        session.record = recorded
        self.session = session
        return session

    def turn_started(self, live):
        self._live = live
        self.suggestion = ""
        self.channel.send("turn_start", task=self._task)
        self.sync()

    def arm(self, armed):
        with self._armed_lock:
            self._armed = bool(armed)
        if not armed:
            self._live = None

    def turn_ended(self, task, answer, outcome):
        self.channel.send("turn_end", task=str(task or ""), answer=str(answer or ""),
                          outcome=str(outcome or ""), suggestion=self.suggestion)
        self.meter()
        self.sync()

    def _interrupted(self):
        """`interrupt`, or the front end going away: stop the turn if one runs.

        Only while a turn is running. Anywhere else the KeyboardInterrupt
        would land outside the loop's `try` and end the session instead.
        """
        with self._armed_lock:
            if self._armed:
                self.interrupt()

    # --- readouts --------------------------------------------------------

    def status(self, state, label, progress):
        payload = (state, label, progress)
        if payload == self._status:
            return
        self._status = payload
        self.channel.send("status", state=state, label=str(label or ""),
                          progress=progress)

    def meter(self, soon=False):
        now = time.monotonic()
        if soon and now - self._meter_at < METER_INTERVAL:
            return
        payload = self._meter()
        if payload is not None:
            self._meter_at = now
            self.channel.send("meter", **payload)

    def _meter(self):
        """What the terminal's corner readout is built from, as numbers."""
        session = self.session
        if session is None:
            return None
        try:
            totals = self.manager.totals() if self.manager is not None else {}
        except Exception:
            totals = {}
        try:
            payload = {
                "tokens_in": int(session.tokens_in),
                "tokens_out": int(session.tokens_out),
                "tokens_out_exact": bool(session.tokens_out_exact
                                         and not session.streaming),
                "lines_added": int(session.lines_added + (totals.get("lines_added") or 0)),
                "lines_removed": int(session.lines_removed
                                     + (totals.get("lines_removed") or 0)),
                "agent_tokens": int(totals.get("tokens") or 0)}
        except Exception:
            return None
        live = self._live
        if live is not None:
            payload["turn_tokens"] = live.turn_tokens()
            payload["elapsed"] = round(max(0.0, time.monotonic() - live._started_at), 1)
        return payload

    def sync(self):
        """Send each panel whose contents changed since it was last sent."""
        changed = []
        with self._sent_lock:
            for kind, payload in _panels(self.session, self.manager):
                if self._sent.get(kind) != payload:
                    self._sent[kind] = payload
                    changed.append((kind, payload))
        for kind, payload in changed:
            self.channel.send(kind, **payload)


# --- the entry point -------------------------------------------------------

def _loop_module():
    """TMT.py as the module whose session loop is actually running.

    `python TMT.py` runs it as `__main__`, where `import TMT` would load a
    second copy with globals of its own: installing over that copy would be
    installing over a loop nobody is running.
    """
    module = sys.modules.get("TMT")
    if module is not None:
        return module
    main = sys.modules.get("__main__")
    if main is not None and hasattr(main, "_session_loop"):
        return main
    import TMT
    return TMT


def _reconfigure(stream, errors):
    """Set a text stream's error handler, returning the old one (or None)."""
    old = getattr(stream, "errors", None)
    try:
        stream.reconfigure(errors=errors)
    except Exception:
        return None
    return old


def run(args, stdin=None, stdout=None):
    """Serve one session over JSON lines. Returns the process exit code.

    `stdin` and `stdout` default to the process's own; a test passes pipes.
    For the length of the run `sys.stdout` IS `sys.stderr` and `sys.stdin` is
    empty, so nothing but events can reach the front end's stdout and nothing
    can read the front end's messages out from under the channel.
    """
    loop = _loop_module()
    channel = Channel(sys.stdin if stdin is None else stdin,
                      sys.stdout if stdout is None else stdout)
    port = Headless(channel)
    saved = (sys.stdout, sys.stdin, os.environ.get("COLUMNS"), os.environ.get("LINES"))
    sys.stdout = sys.stderr
    sys.stdin = io.StringIO("")
    # What lands on stderr is a log; a character it cannot encode must not be
    # the thing that ends the session.
    errors = _reconfigure(sys.stderr, "backslashreplace")
    code, message = EXIT_OK, ""
    try:
        channel.start()
        hello = channel.wait_hello()
        if hello is not None:
            refresh_settings()
            candidate = args.directory or agent_config.default_workspace()
            try:
                shown = str(Path(candidate).expanduser().resolve())
            except Exception:
                shown = str(candidate)
            size = shutil.get_terminal_size((80, 24))
            channel.send("ready", workspace=shown, columns=size.columns,
                         rows=size.lines, **settings())
            channel.ready = True
            with port.installed(loop):
                root = loop.resolve_workspace(args.directory, ask=port.confirm)
                if root is None:
                    code, port.ended = EXIT_WORKSPACE, "workspace"
                else:
                    loop._session_loop(root)
    except KeyboardInterrupt:
        code, port.ended = EXIT_INTERRUPTED, "interrupted"
    except Exception as error:
        traceback.print_exc()
        code, port.ended = EXIT_CRASHED, "crashed"
        message = "%s: %s" % (type(error).__name__, error)
    finally:
        if channel.ready:
            channel.send("exit", code=code, reason=port.ended, message=message)
        sys.stdout, sys.stdin = saved[0], saved[1]
        for name, value in (("COLUMNS", saved[2]), ("LINES", saved[3])):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if errors is not None:
            _reconfigure(sys.stderr, errors)
    return code
