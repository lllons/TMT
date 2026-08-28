"""Live relay renderer: streamed model text decoded character by character.

Text handed to :class:`LiveRelay` is never modified. Each incoming character is
stored exactly as received and shown as a random symbol until its reveal turn
comes around (~25 ms later), at which point the symbol is replaced by the real
character. Only the terminal ever sees a symbol.
"""

import re
import shutil
import sys
import threading
import time
import unicodedata
from collections import deque

SYMBOL_POOL = (
    "↖", "↗", "↘", "↙", "↑", "↓", "↔", "↕", "↩", "↪", "↰", "↱", "↲", "↳", "↺", "↻",
    "⇦", "⇧", "⇨", "⇩",
    "✓", "✗", "☑", "☒", "□", "◇", "▭", "▱",
    "◀", "▲", "▶", "▼",
    "©", "®", "℗", "™", "℠", "℡", "№", "§", "℁", "℀", "✆", "✇", "✃", "✎", "✐", "✑",
    "½", "⅓", "¼", "⅕", "⅛", "⅔", "¾",
    "+", "−", "×", "÷", "±", "≠", "≈", "≤", "≥",
    "₿", "Ξ", "Ł", "Ð", "ꜩ",
    "$", "¢", "€", "£", "₱", "¥", "₹", "₩", "₽", "﷼", "💱",
    "❂", "❍", "⚆", "⦼", "〄", "〶", "㉿", "⎋", "♽", "☢",
    "♔", "♘", "♙", "♕", "♖", "♝",
    "℃", "℉", "☼", "☽", "☾", "❅",
    "❀", "✿", "❁", "꧁", "꧂", "ꕥ", "❋", "✼", "❃", "❉",
    "☻", "☹", "⍢", "㋡",
    "❤️", "❤", "❣", "❥",
    "♪", "♫", "♬", "🎶",
    "♛", "♕", "♚", "♔",
    "♧", "♢", "♡", "♤",
    "★", "✪", "✯", "⋆", "⁂", "✦", "✶", "✧", "☆", "✰",
)

ASCII_SYMBOL_POOL = ("+", "-", "*", "#", "@", "%", "&", "^", "~", "=", "<", ">", "/", "\\", "|", "?")

GLITCH_REVEAL_DURATION = 0.025   # seconds a character spends as a symbol
REVEAL_TICK = 0.012              # renderer refresh period
BACKLOG_ACCELERATE_AT = 48       # queued characters before the reveal speeds up
BACKLOG_DRAIN_TARGET = 0.35      # seconds allowed to clear a large backlog
FINALIZE_TIMEOUT = 2.0           # hard cap on waiting for the reveal queue
LIVE_BODY_LINES = 10             # visible lines of the live response area
_TAIL_CHARS = 4000               # text kept for redraw of the visible tail

# Grapheme-ish clustering: keeps combining marks, variation selectors, ZWJ
# sequences and flag pairs attached to their base character.
_MARKS = "[̀-ͯ᪰-᫿᷀-᷿⃐-⃿︀-️︠-︯]"
_ZWJ = "‍"
_VARIATION_SELECTOR_16 = "️"
_GRAPHEME_RE = re.compile(
    "\r\n|[\U0001F1E6-\U0001F1FF]{2}|"
    "(?:.%s*(?:%s.%s*)*)" % (_MARKS, _ZWJ, _MARKS),
    re.DOTALL,
)

_WHITESPACE = {" ", "\n", "\r", "\t", "\r\n", "\v", "\f"}


def iter_graphemes(text):
    """Split text into user-perceived characters without breaking Unicode."""
    return [match.group() for match in _GRAPHEME_RE.finditer(text)] if text else []


def display_width(text):
    """Terminal columns occupied by text, treating wide characters as two."""
    width = 0
    for char in text:
        if unicodedata.combining(char) or char == _ZWJ:
            continue
        if char == _VARIATION_SELECTOR_16:
            width += 1
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def pad_to_width(text, width):
    return text + " " * max(0, width - display_width(text))


def clip_to_width(text, width):
    """Return (head, rest) where head fits within width columns."""
    used, index = 0, 0
    for grapheme in iter_graphemes(text):
        size = display_width(grapheme)
        if used + size > width:
            break
        used += size
        index += len(grapheme)
    if index == 0 and text:
        index = len(iter_graphemes(text)[0])
    return text[:index], text[index:]


def wrap_lines(text, width):
    lines = []
    for raw in text.split("\n"):
        raw = raw.replace("\t", "    ").replace("\r", "")
        while display_width(raw) > width:
            head, raw = clip_to_width(raw, width)
            lines.append(head)
        lines.append(raw)
    return lines


def symbols_supported(stream):
    """Whether the stream's encoding can carry the full symbol pool."""
    encoding = getattr(stream, "encoding", None) or ""
    if not encoding:
        return False
    try:
        "".join(SYMBOL_POOL).encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


class _Cell:
    """One received character plus the symbol standing in for it."""

    __slots__ = ("text", "symbol")

    def __init__(self, text, symbol):
        self.text = text
        self.symbol = symbol


class GlitchStream:
    """Queue of received characters resolving from symbols to real text.

    The exact text is preserved: resolved characters move, in order, from the
    pending queue into :attr:`text`. Feeding never blocks and never drops or
    reorders input, however fast it arrives.
    """

    def __init__(self, symbols=SYMBOL_POOL, random_module=None):
        import random as _random
        self.symbols = tuple(symbols) or ASCII_SYMBOL_POOL
        self._random = random_module or _random
        self._text = ""
        self._pending = deque()
        self._allowance = 0.0
        self._deadline = None
        self._last_tick = time.monotonic()

    @property
    def text(self):
        """Characters already revealed (exact model output)."""
        return self._text

    def exact_text(self):
        """Everything received so far, revealed or not — always exact."""
        return self._text + "".join(cell.text for cell in self._pending)

    def pending_count(self):
        return len(self._pending)

    def feed(self, text):
        """Queue newly received text. Whitespace is never disguised."""
        if not text:
            return
        for grapheme in iter_graphemes(text):
            if grapheme in _WHITESPACE:
                self._pending.append(_Cell(grapheme, grapheme))
            else:
                self._pending.append(_Cell(grapheme, self._random.choice(self.symbols)))
        self._skip_resolved()

    def _skip_resolved(self):
        """Move leading cells that already show their real text."""
        while self._pending and self._pending[0].symbol == self._pending[0].text:
            self._text += self._pending.popleft().text

    def tick(self, now=None):
        """Reveal whatever is due. Returns True when the display changed."""
        now = time.monotonic() if now is None else now
        elapsed, self._last_tick = max(0.0, now - self._last_tick), now
        if not self._pending:
            self._allowance = 0.0
            self._deadline = None
            return False
        backlog = len(self._pending)
        rate = 1.0 / GLITCH_REVEAL_DURATION
        # A large backlog resolves faster rather than lagging behind the model:
        # the whole queue is committed to finish within BACKLOG_DRAIN_TARGET.
        if backlog > BACKLOG_ACCELERATE_AT and self._deadline is None:
            self._deadline = now + BACKLOG_DRAIN_TARGET
        if self._deadline is not None:
            rate = max(rate, backlog / max(1e-3, self._deadline - now))
        self._allowance += rate * elapsed
        count = int(self._allowance)
        if count <= 0:
            return False
        self._allowance -= count
        for _ in range(count):
            if not self._pending:
                break
            self._text += self._pending.popleft().text
            self._skip_resolved()
        return True

    def reveal_all(self):
        """Resolve everything immediately (finalization safety net)."""
        while self._pending:
            self._text += self._pending.popleft().text
        self._allowance = 0.0
        self._deadline = None
        return self._text

    def display_text(self):
        """Current terminal representation: real text plus standing symbols."""
        tail = self._text[-_TAIL_CHARS:]
        if not self._pending:
            return tail
        return tail + "".join(cell.symbol for cell in self._pending)

    def reset(self):
        self._text = ""
        self._pending.clear()
        self._allowance = 0.0
        self._deadline = None


class LiveRegion:
    """A block of terminal lines repainted in place, without full clears."""

    def __init__(self, stream=None, ansi=None):
        self.stream = sys.stdout if stream is None else stream
        if ansi is None:
            ansi = bool(getattr(self.stream, "isatty", lambda: False)())
        self.ansi = ansi
        self._drawn = 0
        self._last = None

    def paint(self, lines):
        if not self.ansi or lines == self._last:
            return
        self._last = list(lines)
        parts = []
        if self._drawn:
            parts.append("\033[%dA" % self._drawn)
        for line in lines:
            parts.append("\r\033[2K" + line + "\n")
        extra = max(0, self._drawn - len(lines))
        for _ in range(extra):
            parts.append("\r\033[2K\n")
        if extra:
            parts.append("\033[%dA" % extra)
        self._drawn = len(lines)
        self._write("".join(parts))

    def clear(self):
        if not self.ansi or not self._drawn:
            self._drawn, self._last = 0, None
            return
        parts = ["\033[%dA" % self._drawn]
        parts.extend("\r\033[2K\n" for _ in range(self._drawn))
        parts.append("\033[%dA" % self._drawn)
        self._drawn, self._last = 0, None
        self._write("".join(parts))

    def _write(self, text):
        try:
            self.stream.write(text)
            self.stream.flush()
        except UnicodeEncodeError:
            self.stream.write(text.encode("ascii", "replace").decode("ascii"))
            self.stream.flush()
        except (ValueError, OSError):
            self.ansi = False


class LiveRelay:
    """Status line plus a live response box fed by the model stream.

    A single background worker resolves symbols and repaints; feeding text from
    the stream thread never blocks and never waits on the animation.
    """

    def __init__(self, stream=None, ansi=None, symbols=None, body_lines=LIVE_BODY_LINES):
        self.region = LiveRegion(stream, ansi)
        if symbols is None:
            symbols = SYMBOL_POOL if symbols_supported(self.region.stream) else ASCII_SYMBOL_POOL
        self.glitch = GlitchStream(symbols=symbols)
        self.body_lines = body_lines
        self.streamed = False
        self._status = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._dirty = threading.Event()
        self._thread = None
        self._closed = False

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def start(self):
        if self.running or self._closed:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="tmt-live-relay", daemon=True)
        self._thread.start()

    def set_status(self, text):
        """Sink for the progress/status line drawn above the response box."""
        if self._closed:
            return
        with self._lock:
            if text == self._status:
                return
            self._status = text
        self._dirty.set()
        if not self.running:
            self._repaint()

    def feed(self, text):
        """Relay newly received model text. Returns immediately."""
        if not text or self._closed:
            return
        with self._lock:
            self.glitch.feed(text)
            self.streamed = True
        self._idle.clear()
        self._dirty.set()
        self.start()

    def _loop(self):
        while not self._stop.wait(REVEAL_TICK):
            with self._lock:
                changed = self.glitch.tick()
                pending = self.glitch.pending_count()
            if changed or self._dirty.is_set():
                self._dirty.clear()
                self._repaint()
            if not pending:
                self._idle.set()

    def _repaint(self):
        if self._closed:
            return
        with self._lock:
            status = self._status
            body = self.glitch.display_text() if self.streamed else ""
        self.region.paint(self._compose(status, body))

    def _compose(self, status, body):
        size = shutil.get_terminal_size((80, 24))
        width = max(24, size.columns)
        lines = [status] if status else []
        if not body:
            return lines
        inner = max(10, width - 4)
        # Keep the whole region on screen: a region taller than the terminal
        # would scroll away from the cursor moves that repaint it.
        visible = max(1, min(self.body_lines, size.lines - 4))
        wrapped = wrap_lines(body, inner)[-visible:]
        lines.append("┌" + "─" * (inner + 2) + "┐")
        lines.extend("『 " + pad_to_width(line, inner) + " 』" for line in wrapped)
        lines.append("└" + "─" * (inner + 2) + "┘")
        return lines

    def wait_for_reveal(self, timeout=FINALIZE_TIMEOUT):
        """Block until every queued symbol has resolved to its character."""
        if not self.streamed:
            return True
        if not self.running:
            with self._lock:
                self.glitch.reveal_all()
            self._repaint()
            return True
        resolved = self._idle.wait(timeout)
        if not resolved:
            with self._lock:
                self.glitch.reveal_all()
            self._repaint()
        return resolved

    def finish(self):
        """Resolve the queue, tear the region down, return the exact text."""
        self.wait_for_reveal()
        self._shutdown()
        text = self.glitch.exact_text()
        self._closed = True
        self.region.clear()
        return text

    def abort(self):
        """Stop relaying immediately, keeping whatever text was received."""
        self._shutdown()
        text = self.glitch.exact_text()
        self._closed = True
        self.region.clear()
        return text

    def reset(self):
        """Drop the response box but keep the status line for the next turn."""
        self._shutdown()
        with self._lock:
            self.glitch.reset()
            self.streamed = False
        self._idle.set()
        self._repaint()

    def release(self):
        """Hand the terminal back so other output can be printed normally."""
        self._shutdown()
        self.region.clear()
        with self._lock:
            self.glitch.reset()
            self.streamed = False
            self._status = ""
        self._idle.set()

    def _shutdown(self):
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1)
