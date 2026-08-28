"""First-launch setup: ask for the OpenRouter API key and remember it.

Shown only when no key is found in the environment or in the saved key file,
and styled to match the THINKING animation and the progress bar: the same
repeating red -> orange -> green cycle, the same block characters, and icons
drawn from the live relay's symbol pool.
"""

import random
import shutil
import sys
import threading

import agent_config
from agent_live_renderer import SYMBOL_POOL, encodable
from agent_ui import GRADIENT_TICK, _supports_color, cycle_bar, cycle_text, gradient_phase

KEY_URL = "https://openrouter.ai/keys"
KEY_PREFIX = "sk-or-"
MAX_ATTEMPTS = 3
TITLE_MARGIN = "   "
SAVE_CURSOR = "\0337"
RESTORE_CURSOR = "\0338"

TITLE_ART = (
    "████████╗ ███╗   ███╗ ████████╗",
    "╚══██╔══╝ ████╗ ████║ ╚══██╔══╝",
    "   ██║    ██╔████╔██║    ██║   ",
    "   ██║    ██║╚██╔╝██║    ██║   ",
    "   ██║    ██║ ╚═╝ ██║    ██║   ",
    "   ╚═╝    ╚═╝     ╚═╝    ╚═╝   ",
)

ASCII_TITLE_ART = (
    " ______  __       __  ______ ",
    "|_    _||  \\     /  ||_    _|",
    "  |  |  |   \\   /   |  |  |  ",
    "  |  |  | |\\ \\_/ /| |  |  |  ",
    "  |  |  | | \\___/ | |  |  |  ",
    "  |__|  |_|       |_|  |__|  ",
)

# Icons for the panel gutter, in both a decorated and a plain flavour.
PANEL_ICONS = ("◈", "◆", "◇", "✎", "⚑", "➤")
ASCII_PANEL_ICONS = ("*", "+", "-", ">", "!", ">")
BOX = {"tl": "╔", "tr": "╗", "bl": "╚", "br": "╝", "h": "═", "v": "║"}
ASCII_BOX = {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|"}
ASCII_SPARKLES = ("*", "+", ".", "o", "x", "^", "~", "=")


def ascii_only(stream):
    """True when the terminal cannot render the decorated characters."""
    sample = "".join(TITLE_ART) + "".join(PANEL_ICONS) + "".join(BOX.values())
    return not encodable(stream, sample)


def sparkles(count, stream, plain=False, phase=None):
    pool = ASCII_SPARKLES if plain else SYMBOL_POOL
    return cycle_text(" ".join(random.choice(pool) for _ in range(count)), stream, phase)


def clean_key(raw):
    """Accept a pasted key however it arrives: quoted, exported, or bare."""
    key = (raw or "").strip()
    for prefix in ("set ", "export ", "$env:"):
        if key.lower().startswith(prefix):
            key = key[len(prefix):].strip()
    if key.lower().startswith("openrouter_api_key"):
        key = key.split("=", 1)[-1].strip()
    return key.strip('"').strip("'").strip()


def mask_key(key, plain=False):
    """A recognisable but non-revealing form of the key, for confirmation."""
    dot = "*" if plain else "•"
    if len(key) <= 12:
        return dot * len(key)
    return f"{key[:10]}{dot * 3}{key[-4:]}"


class TitleLoop:
    """Keeps the TMT letters cycling while the prompt waits for input.

    The title sits a fixed number of lines above the cursor, so each frame
    saves the cursor, repaints those rows, and puts the cursor back exactly
    where the user is typing.
    """

    def __init__(self, stream, art, margin=TITLE_MARGIN):
        self.stream = stream
        self.art = art
        self.margin = margin
        self.lines_below = 0
        self._stop = threading.Event()
        self._thread = None

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def enabled(self):
        """Animate only where it can be done without mangling the screen."""
        rows = shutil.get_terminal_size((80, 24)).lines
        return _supports_color(self.stream) and rows >= len(self.art) + 14

    def start(self, lines_below):
        if self.running or not self.enabled():
            return
        self.lines_below = lines_below
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="tmt-setup-title", daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.wait(GRADIENT_TICK):
            self.paint()

    def paint(self):
        """Repaint the title rows and put the cursor back where it was.

        DEC save/restore (ESC 7 / ESC 8) is used rather than plain cursor
        moves: the user may be part-way through typing their key, so the
        column has to come back exactly as it was, not just the row.
        """
        phase = gradient_phase()
        parts = [SAVE_CURSOR, "\033[%dA" % (self.lines_below + len(self.art))]
        for index, line in enumerate(self.art):
            parts.append("\r\033[2K" + cycle_text(self.margin + line, self.stream, phase))
            if index < len(self.art) - 1:
                parts.append("\n")
        parts.append(RESTORE_CURSOR)
        try:
            self.stream.write("".join(parts))
            self.stream.flush()
        except (ValueError, OSError, UnicodeEncodeError):
            self._stop.set()

    def stop(self):
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1)


def run_setup(stream=None, ask=None, animate=True):
    """Draw the setup screen and return the key the user typed, or ""."""
    stream = sys.stdout if stream is None else stream
    ask = input if ask is None else ask
    plain = ascii_only(stream)
    art = ASCII_TITLE_ART if plain else TITLE_ART
    width = max(46, min(66, shutil.get_terminal_size((80, 24)).columns - 2))
    box = ASCII_BOX if plain else BOX
    icons = ASCII_PANEL_ICONS if plain else PANEL_ICONS
    arrow, caret = (">", ">") if plain else ("➤", "▸")
    tick, cross = ("OK", "x") if plain else ("✓", "✗")
    below = [0]

    def say(text=""):
        """Write a line and remember how far the cursor has moved on."""
        stream.write(text + "\n")
        stream.flush()
        below[0] += 1

    say()
    for line in art:
        stream.write(cycle_text(TITLE_MARGIN + line, stream) + "\n")
    stream.flush()
    below[0] = 0                       # count lines below the title from here

    say(TITLE_MARGIN + sparkles(12, stream, plain))
    say()
    say(cycle_text(box["tl"] + box["h"] * (width - 2) + box["tr"], stream))
    for icon, text in (
        (icons[0], "W E L C O M E   T O   T M T"),
        (icons[1], "No OpenRouter API key found on this machine."),
        (icons[2], f"Get one free at  {KEY_URL}"),
        (icons[3], f"It is saved to {agent_config.KEY_FILE.name} beside the code,"),
        (" ", "which is git-ignored — it never travels with a push."),
        (icons[4], "Or set OPENROUTER_API_KEY in your environment instead."),
    ):
        body = f"{icon}  {text}".ljust(width - 4)
        say(cycle_text(box["v"], stream) + " " + body + " " + cycle_text(box["v"], stream))
    say(cycle_text(box["bl"] + box["h"] * (width - 2) + box["br"], stream, gradient_phase() + 0.5))
    say()

    title = TitleLoop(stream, art)
    try:
        for attempt in range(MAX_ATTEMPTS):
            progress = 40 + attempt * 15
            say(f"  {cycle_bar(progress, stream, plain=plain)} {progress:>3}% Waiting for your key...")
            stream.write(f"  {cycle_text(f'{arrow} paste key {caret}', stream)} ")
            stream.flush()
            if animate:
                title.start(below[0])
            try:
                key = clean_key(ask(""))
            except (EOFError, KeyboardInterrupt):
                title.stop()
                say()
                say(f"  {cross} Setup cancelled — no key saved.")
                return ""
            title.stop()
            below[0] += 1              # the line the user just submitted
            if not key:
                say(f"  {cross} That was empty. Paste the key from {KEY_URL}.")
                continue
            if not key.startswith(KEY_PREFIX):
                say(f"  {cross} Heads up: OpenRouter keys usually start with {KEY_PREFIX}. Using it anyway.")
            say()
            say(f"  {cycle_bar(100, stream, plain=plain)} 100% Key saved!  {tick}")
            say(f"  {sparkles(3, stream, plain)}  {mask_key(key, plain)} "
                f"stored in {agent_config.KEY_FILE.name}  {sparkles(3, stream, plain, gradient_phase() + 0.5)}")
            say()
            return key
        say(f"  {cross} No key entered after {MAX_ATTEMPTS} tries — stopping here.")
        return ""
    finally:
        title.stop()


def ensure_api_key(stream=None, ask=None, animate=True):
    """True when a key is available, running first-launch setup if it is not."""
    if agent_config.OPENROUTER_API_KEY:
        return True
    key = run_setup(stream=stream, ask=ask, animate=animate)
    if not key:
        return False
    agent_config.save_api_key(key)
    return True
