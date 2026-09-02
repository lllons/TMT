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
from collections import deque

from agent_ui import (
    GRADIENT_TICK, clip_to_width, cycle_text, display_width, encodable,
    gradient_phase, iter_graphemes, pad_to_width, plain_output, safe_write,
    visible_width, wrap_lines, wrap_words,
)


def _rendered_body(body, columns, stream):
    """The reply as it arrives, rendered as markdown and wrapped on words.

    Rendered every frame rather than once at the end, because this box IS the
    reply for as long as it is arriving -- and markdown that only appeared
    after the answer settled would mean the text visibly rearranged itself at
    the moment the user finished reading it. Partial markup costs nothing:
    an unclosed `**` is two asterisks until the closing pair arrives, which is
    what it means anyway.

    Imported inside the function for the reason every optional capability in
    this project is: a module that cannot be loaded must not take the live
    region down with it. The fallback keeps the word wrap, which is the half
    of this the reader would actually miss.
    """
    try:
        import agent_markdown
        return agent_markdown.render(body, columns, stream) or [""]
    except Exception:
        return wrap_words(body, columns) or [""]

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

FRAME = {"left": "『", "right": "』", "top": "┌", "top_end": "┐",
         "bottom": "└", "bottom_end": "┘", "rule": "─"}
ASCII_FRAME = {"left": "|", "right": "|", "top": "+", "top_end": "+",
               "bottom": "+", "bottom_end": "+", "rule": "-"}
BODY_LEFT = FRAME["left"]          # the default edges are two columns wide
BODY_RIGHT = FRAME["right"]

GLITCH_REVEAL_DURATION = 0.25   # seconds a character spends as a symbol
REVEAL_TICK = 0.012              # renderer refresh period
BACKLOG_ACCELERATE_AT = 48       # queued characters before the reveal speeds up
BACKLOG_DRAIN_TARGET = 0.35      # seconds allowed to clear a large backlog
FINALIZE_TIMEOUT = 2.0           # hard cap on waiting for the reveal queue
LIVE_BODY_LINES = 10             # visible lines of the live response area
_TAIL_CHARS = 4000               # text kept for redraw of the visible tail

_WHITESPACE = {" ", "\n", "\r", "\t", "\r\n", "\v", "\f"}


def symbols_supported(stream):
    """Whether the stream's encoding can carry the full symbol pool."""
    return encodable(stream, "".join(SYMBOL_POOL))


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


# The terminal's own caret, switched off and on again. A repaint walks the
# cursor up through every row of the region and back down, and the terminal
# draws it at each stop: on screen that is a caret flickering through rows
# nobody is typing in, several times a second, wherever the region happens to
# end. Suppressing it for the length of the write is the fix at the cause --
# the caret is not moved anywhere it should not be, it is simply not drawn
# while it is being moved. Nothing hides it for longer than one write, and
# `show_cursor` puts it back the moment the region gives the terminal up.
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"


class LiveRegion:
    """A block of terminal lines repainted in place, without full clears."""

    def __init__(self, stream=None, ansi=None):
        self.stream = sys.stdout if stream is None else stream
        if ansi is None:
            ansi = bool(getattr(self.stream, "isatty", lambda: False)())
        self.ansi = ansi
        self._drawn = 0
        self._last = None
        self._cursor_hidden = False
        # The relay worker and the status sink can both reach paint():
        # one interleaved cursor-move sequence would corrupt the region.
        self._paint_lock = threading.RLock()

    def paint(self, lines):
        with self._paint_lock:
            self._paint(lines)

    def _paint(self, lines):
        if not self.ansi or lines == self._last:
            return
        self._last = list(lines)
        # Hidden inside the same write as the cursor moves it is hiding, so
        # there is no window in which the terminal has been told to move the
        # caret but not yet told to stop drawing it.
        parts = [HIDE_CURSOR]
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
        self._cursor_hidden = True
        self._write("".join(parts))

    def show_cursor(self):
        """Draw the caret again, wherever it now stands.

        The counterpart to the suppression in `_paint`, and called by whoever
        owns the caret next: the prompt box, once it has moved it into the row
        being typed in, and `clear`, which is the region giving the terminal
        back. A region that only ever paints leaves it off, which is right --
        while a turn is running there is nothing to type into and no caret
        that belongs anywhere.
        """
        with self._paint_lock:
            if not self.ansi or not self._cursor_hidden:
                return
            self._cursor_hidden = False
            self._write(SHOW_CURSOR)

    def write_above(self, text, lines=None):
        """Print permanent text above the region, leaving the region below it.

        This is how anything that must outlive the turn reaches the terminal
        while a live region is on screen. The region is erased, the text is
        written where it stood -- so it scrolls into the terminal's own
        history like any other output -- and the region is then painted again
        underneath.

        Painted again rather than shifted: how far the terminal scrolled is
        something only the terminal knows, because a long line wraps and a
        full screen scrolls, so arithmetic that assumed a fixed offset would
        put the next repaint on the wrong rows. That was the bug that made
        earlier versions of this frame march down the screen.

        `lines` is the region as it should stand AFTER the text has been
        printed, for a caller whose frame depends on how much has been printed
        -- which is every caller holding blank rows against the foot of the
        window. Repainting the frame that was on screen before instead put the
        same number of rows back one row lower for each row printed, so the
        region crossed the bottom of the window and the terminal scrolled; the
        next composed repaint was then shorter and the whole block jumped up
        by exactly the rows that had been given up. That is what made the
        prompt box drift off the foot after a turn or two.

        Takes the paint lock, so the relay worker cannot interleave a repaint
        into the middle of the sequence.
        """
        if not text:
            return True
        with self._paint_lock:
            held = self._last if lines is None else list(lines)
            # Erased without giving the caret back: the region is about to be
            # painted again three lines further down, and showing the caret
            # for the length of one print only puts the flicker back.
            self._erase(restore=False)
            if not safe_write(self.stream, text if text.endswith("\n") else text + "\n"):
                self.ansi = False
                return False
            if held:
                self._paint(held)
            return True

    def clear(self):
        with self._paint_lock:
            self._erase(restore=True)

    def _erase(self, restore=True):
        if not self.ansi or not self._drawn:
            self._drawn, self._last = 0, None
            if restore:
                self.show_cursor()
            return
        parts = ["\033[%dA" % self._drawn]
        parts.extend("\r\033[2K\n" for _ in range(self._drawn))
        parts.append("\033[%dA" % self._drawn)
        # A region that is handing the rows back gives the caret back with
        # them; one that is only making room for a permanent line keeps it.
        if restore and self._cursor_hidden:
            parts.append(SHOW_CURSOR)
            self._cursor_hidden = False
        self._drawn, self._last = 0, None
        self._write("".join(parts))

    def _write(self, text):
        if not safe_write(self.stream, text):
            self.ansi = False


class LiveRelay:
    """The live area at the foot of the screen while a turn runs.

    Top to bottom: the reply as it arrives, then whatever `footer` draws --
    the prompt box, in a session -- and the status row last of all. All of it
    is one region, repainted in place, so the three keep their order and their
    distance from the bottom of the window however much permanent output
    scrolls past above them.

    The status row is at the bottom because it is the instrument: it measures
    the turn that the box above it asked for, and an instrument belongs under
    the thing it is measuring rather than floating above the reply.

    A single background worker resolves symbols and repaints; feeding text from
    the stream thread never blocks and never waits on the animation.
    """

    def __init__(self, stream=None, ansi=None, symbols=None,
                 body_lines=LIVE_BODY_LINES, footer=None, pad=None,
                 panel=None, agent_rows=None):
        self.region = LiveRegion(stream, ansi)
        if symbols is None:
            symbols = SYMBOL_POOL if symbols_supported(self.region.stream) else ASCII_SYMBOL_POOL
        self.glitch = GlitchStream(symbols=symbols)
        self.body_lines = body_lines
        # footer() -> the rows drawn between the reply and the status row, or
        # None for none. A callable rather than a list so the rows are built
        # at the width the terminal has now: a window resized mid-turn is
        # redrawn at its new size, exactly as the prompt box already is.
        self.footer = footer
        # The blank rows that hold this region against the foot of the window,
        # or None to draw it wherever the cursor is. It is asked how many rows
        # a region of this height needs, so a taller one -- a long reply -- is
        # given fewer and the bottom edge does not move.
        self.pad = pad
        # panel(columns, rows) -> (left columns, join(left_rows) -> rows), or
        # None for a region with no panel in it. The agents panel is a column
        # inside THIS region, because this is the only part of the screen that
        # may be redrawn: everything above it is already in the terminal's own
        # scrollback and is the permanent record of the session.
        #
        # The hook hands back a width and a function rather than rows, so this
        # module needs to know only how wide its own column is. Every rule
        # about panel widths, gutters, which column is flush with which edge
        # and what a narrow terminal does stays in agent_panel, and this
        # module still has no idea what an agent is.
        #
        # `left columns` of 0 means the panel has taken the whole region,
        # which is what a terminal too narrow for two columns does. The reply
        # box and the prompt box are then not drawn at all -- the box is not
        # accepting input while the panel has focus, and one that looked ready
        # for input would be a lie about what the program is doing.
        self.panel = panel
        # agent_rows(columns) -> the rows drawn immediately UNDER the status
        # row, one per background agent. A callable rather than a list for the
        # reason `footer` is one: they are built at the width the terminal has
        # now, and they change on their own thread while the region stands.
        #
        # Under the status row rather than above it because that row is the
        # main agent's own bar, and these are subordinate to it: the thing the
        # user asked for first, then the things it delegated. They are the
        # last rows of the region, so a terminal too short to hold them all
        # gives them up before it gives up the reply.
        self.agent_rows = agent_rows
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

    def refresh(self):
        """Ask for one repaint because something outside this region changed.

        The loop repaints when the reply has moved on and when the status row
        has changed, and at no other time -- that is the repaint-on-a-timer
        this module deliberately gave up, and it is half the cursor fix. It
        also means a region whose FOOTER changed on another thread would sit
        there stale: an agent's activity label, a card ageing out, an agent
        finishing. None of those are things this module can see.

        So a caller that knows something changed says so, once, here. It is
        the manager's event bus that calls it in a session, which is why this
        takes no argument and reports nothing: it is a nudge, not a message.

        Nothing is painted from this thread. The flag is set and the region's
        own worker picks it up, because printing from a background thread on
        top of a live region is the one thing that must never happen.
        """
        if self._closed:
            return
        self._dirty.set()
        if not self.running:
            self._repaint()

    def write_above(self, text):
        """Print permanent text above the live area.

        The seam between the two surfaces this module now carries: everything
        the user keeps goes through here into the terminal's own scrollback,
        while the status row and the response box below stay temporary and
        keep being repainted in place.

        The region is handed a frame composed AFTER the print rather than
        being left to repaint the one that was on screen before it. The caller
        spends a blank row from the pad for every row it prints, so the frame
        that belongs under this text is shorter than the frame that was above
        it -- and repainting the taller one pushed the region past the foot of
        the window, scrolled, and then jumped it up again on the next compose.
        Composed outside the region's lock, so the two locks are never held in
        opposite orders.
        """
        return self.region.write_above(text, self._frame_now())

    def _frame_now(self):
        """The region as it should stand right now, or None if it has none."""
        if self._closed:
            return None
        with self._lock:
            status = self._status
            body = self.glitch.display_text() if self.streamed else ""
        return self._compose(status, body) or None

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
        last_paint = 0.0
        while not self._stop.wait(REVEAL_TICK):
            with self._lock:
                changed = self.glitch.tick()
                pending = self.glitch.pending_count()
            now = time.monotonic()
            # Repaint when the reply has moved on and when the status row has
            # changed, and at no other time. There used to be a third reason
            # -- keeping the border riding the colour cycle -- and it meant
            # the whole box was rewritten twelve times a second while the
            # reader was reading it, for no change in what it said. The
            # border does not carry the gradient any more, so the only
            # repaints left are the ones that have something new to show.
            if changed or self._dirty.is_set():
                self._dirty.clear()
                last_paint = now
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

    def _agent_rows(self, columns):
        """The per-agent rows under the status row, or none at all.

        Guarded like every other decoration here: a register that cannot
        answer costs the region its extra rows, never the turn. With no
        callable supplied this returns nothing and the region is exactly the
        region it was before background agents existed.
        """
        if self.agent_rows is None:
            return []
        try:
            return [row for row in (self.agent_rows(columns) or ()) if row]
        except Exception:
            return []

    def _footer_rows(self, size=None):
        """The rows between the reply and the status row, or none.

        Decoration is never allowed to end a turn, so a footer that raises is
        simply not drawn.

        `size` is `(columns, rows)` -- the same shape the prompt box already
        takes -- and it is passed only when a panel has narrowed the column
        the footer is drawn in. With no panel the footer is called with no
        arguments at all, exactly as it always was, so a caller that passed a
        zero-argument callable keeps working unchanged. A footer that does not
        accept the argument is called again without it, which degrades to a
        full-width box beside the panel rather than to no box at all.
        """
        if self.footer is None:
            return []
        try:
            if size is None:
                return list(self.footer() or ())
            try:
                return list(self.footer(size) or ())
            except TypeError:
                return list(self.footer() or ())
        except Exception:
            return []

    def _panel_frame(self, columns, rows):
        """(left columns, join) from the panel hook, or None for no panel."""
        if self.panel is None:
            return None
        try:
            return self.panel(columns, rows)
        except Exception:
            return None

    def _body_rows(self, body, columns, visible):
        """The reply box, `columns` wide, showing the last `visible` rows.

        `columns` is the box's outer width, spare column already given up by
        whoever worked it out: with no panel that is the window less one, and
        with one it is the left column the panel left behind.
        """
        # BODY_LEFT and BODY_RIGHT are East Asian wide: two columns each, not
        # one. Measure the chrome instead of assuming it. A row that reached
        # the terminal's auto-wrap would silently cost a second screen line,
        # which the cursor moves in LiveRegion.paint do not count, so every
        # repaint would land too high and march the frame down the screen.
        frame = ASCII_FRAME if plain_output(self.region.stream) else FRAME
        chrome = display_width(frame["left"]) + display_width(frame["right"]) + 2
        inner = max(10, columns - chrome)
        wrapped = _rendered_body(body, inner, self.region.stream)[-max(1, visible):]
        # Undecorated on purpose. This box holds the reply as it arrives, and
        # a reply is read rather than watched: the gradient belongs on the
        # instruments -- the bar, the thinking word -- not on the border of
        # the thing being read. Plain also means two consecutive frames of an
        # unchanged reply are identical, which is what lets the repaint be
        # skipped entirely.
        rule = frame["rule"] * (inner + chrome - 2)
        left, right = frame["left"], frame["right"]
        lines = [frame["top"] + rule + frame["top_end"]]
        # Padded by what the row SHOWS. A rendered row carries escapes and
        # `pad_to_width` measures the string it is handed, so padding the
        # styled text would count every escape as a column and leave the
        # right-hand edge of the box ragged as the reply arrives.
        lines.extend("%s %s%s %s"
                     % (left, line,
                        " " * max(0, inner - visible_width(line)), right)
                     for line in wrapped)
        lines.append(frame["bottom"] + rule + frame["bottom_end"])
        return lines

    def _lead(self, height):
        """The blank rows that hold a region this tall against the foot."""
        if self.pad is None:
            return []
        try:
            return [""] * self.pad.above(height)
        except Exception:
            return []

    def _compose(self, status, body):
        size = shutil.get_terminal_size((80, 24))
        width = max(24, size.columns)
        panel = self._panel_frame(width, size.lines)
        if panel is not None:
            return self._compose_with_panel(status, body, size, width, panel)
        footer = self._footer_rows()
        tail = footer + ([status] if status else []) + self._agent_rows(width)
        if not body:
            return self._lead(len(tail)) + tail
        # Keep the whole region on screen: a region taller than the terminal
        # would scroll away from the cursor moves that repaint it. The reply is
        # what gives up rows for the footer, because the footer is the box the
        # user is looking at and the status row is one line.
        room = size.lines - 3 - len(tail)
        visible = max(1, min(self.body_lines, room))
        lines = self._body_rows(body, width - 1, visible)
        return self._lead(len(lines) + len(tail)) + lines + tail

    def _compose_with_panel(self, status, body, size, width, panel):
        """The region with a right-hand column beside it.

        The status row and the per-agent rows are the two things left full
        width and below both columns. They are the instruments measuring the
        turn, and an instrument belongs under the thing it is measuring rather
        than beside it -- squeezing the bar and the token readout into the
        left column would cost them the room they need to say anything.

        **The agent rows are in the tail here for the same reason they are in
        the tail without a panel, and leaving them out was a real bug.** This
        branch used to compose `[status]` alone, which was invisible while the
        only thing that wanted the column was the agents panel: that panel is
        opened by a deliberate gesture and is shut while a turn runs, so this
        branch was almost never taken. A PLAN makes the column permanent, and
        from then on every session with a plan in it lost its agent bars the
        moment the plan appeared -- the two could never be on screen at once.
        It is the same root cause as the plan being drawn twice: a branch that
        was rehearsed only in the case where the column was rare.

        Only the panel is composed against the LEFT column here. Everything
        above this region is untouched: it is already printed, it is the
        session's permanent record, and repainting it is the one thing this
        module may never do.
        """
        left_columns, join = panel
        # Both instruments, in the order the no-panel branch puts them: the
        # main agent's own bar, then the agents it delegated to, which are
        # subordinate to it. `room` below already subtracts the whole tail, so
        # a terminal short of rows gives them up out of the REPLY rather than
        # growing the region past the window -- exactly as it does without a
        # panel.
        tail = ([status] if status else []) + self._agent_rows(width)
        left = []
        if left_columns:
            footer = self._footer_rows((left_columns + 1, size.lines))
            room = size.lines - 3 - len(tail) - len(footer)
            if body:
                left = self._body_rows(body, left_columns,
                                       max(1, min(self.body_lines, room)))
            left = left + footer
        rows = list(join(left))
        return self._lead(len(rows) + len(tail)) + rows + tail

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
