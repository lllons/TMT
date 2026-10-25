"""First-launch setup: offer to take an API key, and let the user skip it.

Shown only when no provider is configured. It is drawn like the rest of TMT:
the menu's wordmark (same size, same colour cycle), the same one-column margin
and plain sentences, with no boxes, symbols or progress bar of its own.
"""

import shutil
import sys
import threading

import agent_config
import agent_menu
from agent_live_renderer import encodable
from agent_ui import (
    GRADIENT_TICK, _supports_color, cycle_text, gradient_phase, safe_write,
)

KEY_PREFIX = "sk-or-"
TITLE_MARGIN = " "
SAVE_CURSOR = "\0337"
RESTORE_CURSOR = "\0338"

ASCII_LOGO = tuple(row.replace("█", "#") for row in agent_menu.LOGO)


def ascii_only(stream):
    """True when the terminal cannot render the decorated characters."""
    return not encodable(stream, "".join(agent_menu.LOGO) + "—")


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

        The raw write here is deliberate, and is the one in this module that
        stays raw: a frame is a cursor move, six repainted rows and a restore,
        so `safe_write`'s replacement marks would land in the middle of the
        title with the cursor arithmetic already committed. Giving up the
        animation is the better degradation, which is what the except does --
        the words the user has to read all go through `say`.
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
    """Draw the setup screen; the key typed, None when skipped, "" when cancelled."""
    stream = sys.stdout if stream is None else stream
    ask = input if ask is None else ask
    plain = ascii_only(stream)
    art = ASCII_LOGO if plain else agent_menu.LOGO
    # The em dash is the only punctuation here a cp437 or ascii console cannot
    # encode, so it takes a plain form like every other decorated character. A
    # deliberate hyphen reads as a choice; a replacement mark reads as a bug.
    dash = "-" if plain else "—"
    below = [0]

    def say(text=""):
        """Write a line and remember how far the cursor has moved on.

        Through safe_write, never a raw stream.write. This screen is the only
        thing between a fresh install and a usable TMT and nothing above it
        catches anything -- ensure_api_key has no try, TMT.main calls it bare,
        and the entry point is sys.exit(main()). A character the console could
        not encode used to raise UnicodeEncodeError, so the user got a
        traceback instead of the key prompt with no way past it. Decoration
        must never end the run.
        """
        safe_write(stream, text + "\n")
        below[0] += 1

    say()
    for line in art:
        safe_write(stream, cycle_text(TITLE_MARGIN + line, stream) + "\n")
    below[0] = 0                       # count lines below the title from here

    say()
    say(" " + cycle_text("Welcome to TMT", stream))
    say(" No model provider is set up yet.")
    say(" Paste an API key to set one up now, or press Esc or Enter")
    say(" to skip and choose one later in Settings.")
    say(" A pasted key is saved beside the code,")
    say(f" which is git-ignored {dash} it never travels with a push.")
    say()

    title = TitleLoop(stream, art)
    try:
        safe_write(stream, " > ")
        if animate:
            title.start(below[0])
        try:
            key = clean_key((ask("") or "").replace(chr(27), ""))   # Esc, then Enter, skips
        except (EOFError, KeyboardInterrupt):
            title.stop()
            say()
            say(f" Setup cancelled {dash} no key saved.")
            return ""
        title.stop()
        below[0] += 1              # the line the user just submitted
        if not key:
            say(f" Skipped {dash} choose a model provider in Settings when you are ready.")
            return None
        if not key.startswith(KEY_PREFIX) and not _identified(key):
            say(" Heads up: that does not look like a key TMT recognises. Using it anyway.")
        say()
        say(f" Key saved: {mask_key(key, plain)}, stored beside the code.")
        say()
        return key
    finally:
        title.stop()


def _identified(key):
    """The provider a pasted key belongs to, or "" (OpenRouter's own prefix counts as none)."""
    try:
        import agent_menu
        return agent_menu.identify_provider(key)
    except Exception:
        return ""


GIT_SETUP_TITLE = "TMT Git Contributor Setup"


def run_git_setup(stream=None, ask=None):
    """Ask for TMT's co-author address and return it, or "" if declined.

    Only the TMT address is asked for. The user's own git identity is theirs to
    set and TMT never collects, changes or stands in for it.
    """
    stream = stream or sys.stdout
    ask = ask or (lambda prompt: input(prompt))
    rule = "=" * 50
    for line in (
        "", rule, GIT_SETUP_TITLE, rule, "",
        "TMT can be credited as a co-author on the commits it",
        "makes. You stay the author; TMT is added beside you.",
        "",
        "  Author:    your own git identity, unchanged",
        "  Co-author: TMT code",
        "",
        "For GitHub to credit it, this must be an address verified",
        "on the GitHub account representing TMT. Leave it blank to",
        "skip; commits will be refused until it is set.",
        "",
    ):
        safe_write(stream, line + "\n")
    try:
        answer = ask("TMT git email: ")
    except (EOFError, KeyboardInterrupt):
        safe_write(stream, "\n")
        return ""
    address = (answer or "").strip()
    if not address:
        safe_write(stream, "\nSkipped. Run git_identity later to see what to set.\n")
        return ""
    safe_write(stream, f"\nTMT will be credited as: TMT code <{address}>\n")
    return address


def ensure_git_identity(stream=None, ask=None):
    """Offer first-launch setup when TMT has no usable co-author address.

    Never blocks the session: everything except committing works without it,
    and a refused commit already explains exactly what to set.
    """
    try:
        import agent_git
        agent_git.TMTGitIdentity.resolve().validate()
        return True
    except Exception:
        pass
    address = run_git_setup(stream=stream, ask=ask)
    if not address:
        return False
    agent_config.save_git_email(address)
    return True


def ensure_api_key(stream=None, ask=None, animate=True):
    """True when TMT can start: a provider is configured, or the user skipped setup.

    False only when setup was cancelled. A pasted key is filed under the
    provider it identifies as; anything else goes where it always went, the
    OpenRouter key file. Skipping leaves nothing configured, and every call to a
    model then answers NO_PROVIDER.
    """
    if agent_config.OPENROUTER_API_KEY:
        return True
    try:
        import agent_credentials
        if agent_credentials.is_configured(agent_credentials.selected_provider()):
            return True
    except Exception:
        pass
    key = run_setup(stream=stream, ask=ask, animate=animate)
    if key is None:
        return True
    if not key:
        return False
    provider = _identified(key)
    if provider and provider != "openrouter":
        import agent_credentials
        agent_credentials.set_credential(provider, key)
        agent_credentials.set_provider(provider)
    else:
        agent_config.save_api_key(key)
    return True
