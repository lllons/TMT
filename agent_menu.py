"""The startup screen: the menu TMT opens with.

Everything here draws through the shared surfaces in agent_ui and repaints
through the LiveRegion in agent_live_renderer, so the screen obeys the same
colour cycle, width measurement and encoding fallbacks as the rest of TMT.

The screen is optional by design. A run whose stdin is not a terminal -- the
test suite, a pipeline, anything scripted -- gets "start" back without a
single character being drawn or read, because a menu that blocks on raw input
there would hang the process rather than fail.
"""

import shutil
import sys
import time

import agent_config
import agent_models
from agent_live_renderer import LiveRegion
from agent_ui import (
    DIM, GRADIENT_TICK, RESET, _supports_color, cycle_text, display_width,
    fit_to_width, gradient_phase, pad_to_width, plain_output, safe_write,
)

FALLBACK_VERSION = "0.1.0"

# Five rows of block capitals: T (8 columns), M (10), T (8), single-spaced.
LOGO = (
    "████████ ███    ███ ████████",
    "   ██    ████  ████    ██   ",
    "   ██    ██ ████ ██    ██   ",
    "   ██    ██  ██  ██    ██   ",
    "   ██    ██      ██    ██   ",
)
LOGO_WIDTH = max(display_width(row) for row in LOGO)

MENU_ITEMS = (
    ("start", "Start", "Work on the project in this workspace"),
    ("settings", "Settings", "Choose which free model TMT runs on"),
    ("help", "Help", "What TMT does, and how to drive it"),
    ("exit", "Exit", "Close TMT without starting a session"),
)

HELP_LINES = (
    ("head", "TMT"),
    ("body", "A command-line coding agent. It works inside one project"),
    ("body", "directory -- the workspace on the startup screen -- and nothing"),
    ("body", "outside it."),
    ("gap", ""),
    ("head", "What it can do"),
    ("body", "Inspect a project and read the files in it"),
    ("body", "Reason about what the code does and where a fault is"),
    ("body", "Create and edit code, and patch existing files"),
    ("body", "Diagnose errors and explain what caused them"),
    ("body", "Run the commands it supports and report what happened"),
    ("body", "Work through a task with you, one step at a time"),
    ("gap", ""),
    ("head", "Models"),
    ("body", "Settings lists the free models TMT can run on; Enter saves the"),
    ("body", "choice for every later run. OPENROUTER_MODEL overrides it."),
    ("gap", ""),
    ("head", "Tips"),
    ("body", "Describe the outcome you want, not the keystrokes to reach it."),
    ("body", "Name the files you already know are involved."),
    ("body", "Ask for a commit or a push in words; TMT will not push unasked."),
    ("gap", ""),
    ("dim", "Read what TMT writes before you rely on it."),
)

_MAX_CONTENT = 72        # widest the screen grows on a wide terminal
_LOGO_ROW_SPREAD = 0.05  # gradient offset per logo row, so colour flows down

# Set while POSIX raw mode is in force, so any exit path can put the terminal
# back even if the read that armed it never returned normally.
_saved_termios = None


def _version():
    """TMT's version, from installed metadata when it is available."""
    try:
        from importlib.metadata import version
    except ImportError:
        return FALLBACK_VERSION
    try:
        return version("tmtcode")
    except Exception:
        return FALLBACK_VERSION


def _glyphs(stream):
    """Decoration for this stream, or ASCII when it cannot encode it."""
    if plain_output(stream):
        return {"rule": "-", "up": "Up", "down": "Down", "sep": "/", "dot": "-"}
    return {"rule": "─", "up": "↑", "down": "↓", "sep": "/", "dot": "·"}


def _terminal(size=None):
    """Columns and rows, re-read per frame so a resize is picked up."""
    if size is not None:
        columns, rows = size
        return int(columns), int(rows)
    measured = shutil.get_terminal_size((80, 24))
    return measured.columns, measured.lines


def _content_width(columns):
    return max(24, min(_MAX_CONTENT, columns - 2))


def _paint(text, stream, phase, spread=0.8):
    if not _supports_color(stream):
        return text
    return cycle_text(text, stream, phase, spread=spread)


def _dim(text, stream):
    return DIM + text + RESET if _supports_color(stream) else text


def _fit_height(lines, rows, keep_tail=0):
    """Trim a frame to the terminal.

    A region taller than the screen scrolls away from the cursor moves that
    repaint it, which walks the frame down the terminal. The tail is the
    footer, which stays useful when the middle has to go.
    """
    limit = max(1, rows - 1)
    if len(lines) <= limit:
        return lines
    tail = lines[len(lines) - keep_tail:] if keep_tail else []
    return lines[:max(0, limit - len(tail))] + tail


def render_banner(stream=None, phase=None, columns=None):
    """The TMT logo painted across the shared red-orange-green cycle.

    Falls back to a plain wordmark when the terminal is too narrow for the
    block letters, and to '#' when the stream cannot encode them.
    """
    stream = sys.stdout if stream is None else stream
    columns = _terminal()[0] if columns is None else columns
    phase = gradient_phase() if phase is None else phase
    if columns < LOGO_WIDTH + 3:
        return [" " + _paint("T M T", stream, phase)]
    rows = [row.replace("█", "#") for row in LOGO] if plain_output(stream) else list(LOGO)
    return [" " + _paint(row, stream, phase + index * _LOGO_ROW_SPREAD)
            for index, row in enumerate(rows)]


def _field(name, value, stream, width, name_width=10):
    line = " " + pad_to_width(name, name_width) + value
    line = fit_to_width(line, width)
    if not _supports_color(stream):
        return line
    head = min(len(line), 1 + name_width)
    return _dim(line[:head], stream) + line[head:]


def _option_row(is_selected, label, detail, stream, phase, width, label_width, suffix=""):
    """One selectable row.

    The '>' marker carries the selection on its own; colour only reinforces
    it, because colour is not always available. `suffix` is protected from
    trimming -- it marks the active model, which a narrow terminal must not be
    allowed to hide.
    """
    line = " " + ("> " if is_selected else "  ") + pad_to_width(label, label_width)
    head_width = display_width(line)
    if detail:
        line += "  " + detail
    if suffix:
        line = fit_to_width(line, max(head_width, width - display_width(suffix))) + suffix
    line = fit_to_width(line, width)
    if not _supports_color(stream):
        return line
    if is_selected:
        return cycle_text(line, stream, phase, spread=0.7)
    return line[:head_width] + _dim(line[head_width:], stream)


def _footer(stream, hints):
    glyph = _glyphs(stream)
    text = "    ".join(hints).replace("{up}", glyph["up"]).replace("{down}", glyph["down"])
    return _dim(" " + text, stream)


def _rule(stream, phase, width):
    return " " + _paint(_glyphs(stream)["rule"] * max(4, width - 1), stream, phase + 0.5, spread=1.2)


def render_startup_frame(selected=0, stream=None, model_id=None, workspace=None,
                         size=None, phase=None):
    """The whole startup screen as a list of ready-to-paint lines."""
    stream = sys.stdout if stream is None else stream
    columns, rows = _terminal(size)
    phase = gradient_phase() if phase is None else phase
    width = _content_width(columns)
    glyph = _glyphs(stream)
    model_id = model_id or agent_models.current_model()
    workspace = agent_config.ROOT_DIR if workspace is None else workspace

    label = agent_models.describe(model_id)
    if agent_models.is_overridden():
        label += "  (forced by OPENROUTER_MODEL)"
    label_width = max(display_width(item[1]) for item in MENU_ITEMS)

    body = [
        "",
        " " + _paint("TMT", stream, phase) + _dim("  %s  v%s" % (glyph["dot"], _version()), stream),
        "",
        _field("Model", label, stream, width),
        _field("Workspace", str(workspace), stream, width),
        _rule(stream, phase, width),
    ]
    body.extend(
        _option_row(index == selected, item[1], item[2], stream, phase, width, label_width)
        for index, item in enumerate(MENU_ITEMS)
    )
    body.append("")
    body.append(_footer(stream, ("{up}/{down} Navigate", "Enter Select")))

    banner = render_banner(stream, phase, columns)
    lines = banner + body
    if len(lines) > max(1, rows - 1):
        lines = body          # the logo is the first thing given up for room
    return _fit_height(lines, rows, keep_tail=1)


def _context_label(context):
    if context >= 1000000:
        return "%dM ctx" % (context // 1000000)
    return "%dK ctx" % (context // 1000)


def _model_at(index):
    return agent_models.FREE_MODELS[index % len(agent_models.FREE_MODELS)]


def render_settings_frame(selected=0, active_id=None, stream=None, size=None, phase=None):
    """The model chooser."""
    stream = sys.stdout if stream is None else stream
    columns, rows = _terminal(size)
    phase = gradient_phase() if phase is None else phase
    width = _content_width(columns)
    active_id = active_id or agent_models.current_model()
    label_width = max(display_width(model["label"]) for model in agent_models.FREE_MODELS)

    lines = [
        "",
        " " + _paint("Settings", stream, phase),
        "",
        _dim(" The model TMT sends every request to.", stream),
    ]
    if agent_models.is_overridden():
        # Saying nothing here would let a choice look as though it had taken
        # effect while the environment quietly kept overriding it.
        lines.append(_dim(" OPENROUTER_MODEL is set and forces the model. A choice made", stream))
        lines.append(_dim(" here is saved, but applies only once that variable is unset.", stream))
    lines.append(_rule(stream, phase, width))
    for index, model in enumerate(agent_models.FREE_MODELS):
        lines.append(_option_row(
            index == selected, model["label"],
            pad_to_width(_context_label(model["context"]), 8),
            stream, phase, width, label_width,
            suffix="  (active)" if model["id"] == active_id else ""))
    # The note and the full id belong to whichever row the cursor is on: they
    # are what the choice actually means, and they do not fit on every row.
    chosen = _model_at(selected)
    lines.append("")
    lines.append(_dim(fit_to_width(" %s  %s" % (chosen["id"], chosen["note"]), width), stream))
    lines.append("")
    lines.append(_footer(stream, ("{up}/{down} Navigate", "Enter Save", "Esc Back")))
    return _fit_height(lines, rows, keep_tail=1)


def render_help_frame(stream=None, size=None, phase=None):
    """One screen of help. Not a copy of the README."""
    stream = sys.stdout if stream is None else stream
    columns, rows = _terminal(size)
    phase = gradient_phase() if phase is None else phase
    width = _content_width(columns)

    lines = ["", " " + _paint("Help", stream, phase), _rule(stream, phase, width)]
    for kind, text in HELP_LINES:
        if kind == "gap":
            lines.append("")
        elif kind == "head":
            lines.append(fit_to_width(" " + text, width))
        elif kind == "dim":
            lines.append(_dim(fit_to_width(" " + text, width), stream))
        else:
            lines.append(_dim(fit_to_width("   " + text, width), stream))
    lines.append("")
    lines.append(_footer(stream, ("Esc Back",)))
    return _fit_height(lines, rows, keep_tail=1)


def is_interactive(stream=None):
    """Whether a menu can be driven here at all.

    Any doubt counts as no: a wrong "yes" hangs the process on a read that
    will never be answered, while a wrong "no" only skips a menu.
    """
    stream = sys.stdout if stream is None else stream
    for candidate in (sys.stdin, stream):
        try:
            if not candidate.isatty():
                return False
        except Exception:
            return False
    return _key_backend() is not None


def _key_backend():
    """The module that can read one keystroke here, or None."""
    try:
        import msvcrt
        return msvcrt
    except ImportError:
        pass
    try:
        import termios  # noqa: F401
        import tty      # noqa: F401
        return sys.modules["tty"]
    except ImportError:
        return None


# Keystrokes, however they arrive, reduced to the names the screens act on.
# Raw sequences are accepted as well as names so a scripted reader can send
# either.
_KEY_NAMES = {
    "up": "up", "\x1b[A": "up", "\x1bOA": "up", "k": "up",
    "down": "down", "\x1b[B": "down", "\x1bOB": "down", "j": "down",
    "enter": "enter", "\r": "enter", "\n": "enter", "\r\n": "enter",
    "esc": "esc", "\x1b": "esc",
    "quit": "quit", "q": "quit", "Q": "quit",
    "interrupt": "interrupt", "\x03": "interrupt",
}


def normalize_key(key):
    """A key name, or "" for anything this screen ignores."""
    if key is None:
        return None
    return _KEY_NAMES.get(key, "")


def _read_key_windows(msvcrt, timeout):
    if timeout is not None:
        deadline = time.monotonic() + timeout
        while not msvcrt.kbhit():
            if time.monotonic() >= deadline:
                return ""
            time.sleep(0.01)
    char = msvcrt.getwch()
    if char in ("\x00", "\xe0"):
        return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(msvcrt.getwch(), "")
    return normalize_key(char)


def _read_key_posix(stream, timeout):
    import os
    import select
    import termios
    import tty
    global _saved_termios

    fd = stream.fileno()

    def pending(wait):
        return bool(select.select([fd], [], [], wait)[0])

    def take():
        # Read the descriptor directly: select knows about the descriptor,
        # not about anything a text wrapper may already have buffered.
        return os.read(fd, 1).decode("utf-8", "replace")

    _saved_termios = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        if timeout is not None and not pending(timeout):
            return ""
        char = take()
        if not char:
            return None
        if char != "\x1b":
            return normalize_key(char)
        # An escape on its own is Esc; an escape with more behind it is an
        # arrow. Only pending bytes are read, so Esc never waits long.
        if not pending(0.05):
            return "esc"
        sequence = "\x1b" + take()
        if sequence[-1] in "[O":
            sequence += take()
        return normalize_key(sequence)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, _saved_termios)
        _saved_termios = None


def read_key(stream=None, timeout=None):
    """Read one keystroke and return its name.

    Returns "" when nothing recognised arrived (including a timeout) and None
    when the input has ended. Raw mode, where it is needed at all, is restored
    in a finally on every path, so an exception cannot leave the terminal
    unusable.
    """
    stream = sys.stdin if stream is None else stream
    backend = _key_backend()
    if backend is None:
        return None
    try:
        if backend.__name__ == "msvcrt":
            return _read_key_windows(backend, timeout)
        return _read_key_posix(stream, timeout)
    except (KeyboardInterrupt, EOFError):
        return "interrupt"
    except (OSError, ValueError):
        # A closed or non-terminal stream cannot be read again; ending the
        # input is the honest answer and returns the caller to safety.
        return None


def _restore_terminal(stream):
    """Put the terminal back: cursor visible, no raw mode outstanding."""
    global _saved_termios
    if _saved_termios is not None:
        try:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _saved_termios)
        except Exception:
            pass
        _saved_termios = None
    if getattr(stream, "isatty", lambda: False)():
        safe_write(stream, "\033[?25h")


def _hide_cursor(stream):
    if getattr(stream, "isatty", lambda: False)():
        safe_write(stream, "\033[?25l")


def _next_key(key_reader):
    """One key from the reader, with an exhausted reader reported as None."""
    try:
        return normalize_key(key_reader())
    except (StopIteration, IndexError):
        return None
    except KeyboardInterrupt:
        return "interrupt"


def _default_reader():
    return lambda: read_key(timeout=GRADIENT_TICK)


def _drive(render, key_reader, region, on_key):
    """Paint, read, act, repeat.

    `render` builds the frame afresh each pass, so a resize, a colour tick or
    a changed selection all show up. `on_key` returns None to keep looping or
    a value to return to the caller.
    """
    while True:
        region.paint(render())
        key = _next_key(key_reader)
        if key == "":
            continue          # unrecognised, or an animation tick
        outcome = on_key(key)
        if outcome is not None:
            return outcome


def main_menu(stream=None, key_reader=None, region=None, model_id=None,
              workspace=None, selected=0):
    """Run the startup menu and return the chosen action.

    One of "start", "settings", "help" or "exit". Exhausted input, Esc, q and
    Ctrl-C all answer "exit", so no path leaves the caller waiting.
    """
    stream = sys.stdout if stream is None else stream
    if key_reader is None:
        if not is_interactive(stream):
            return "start"
        key_reader = _default_reader()
    region = LiveRegion(stream) if region is None else region
    state = {"selected": selected % len(MENU_ITEMS)}

    def render():
        return render_startup_frame(state["selected"], stream, model_id, workspace)

    def on_key(key):
        if key in (None, "esc", "quit", "interrupt"):
            return "exit"
        if key == "up":
            state["selected"] = (state["selected"] - 1) % len(MENU_ITEMS)
        elif key == "down":
            state["selected"] = (state["selected"] + 1) % len(MENU_ITEMS)
        elif key == "enter":
            return MENU_ITEMS[state["selected"]][0]
        return None

    return _drive(render, key_reader, region, on_key)


def settings_screen(stream=None, key_reader=None, region=None, active_id=None):
    """Choose the model. Returns the saved id, or None when nothing changed.

    The cursor starts on the model in force, so Enter without moving is a
    confirmation rather than an accidental change.
    """
    stream = sys.stdout if stream is None else stream
    if key_reader is None:
        if not is_interactive(stream):
            return None
        key_reader = _default_reader()
    region = LiveRegion(stream) if region is None else region
    active_id = active_id or agent_models.current_model()
    ids = agent_models.known_ids()
    state = {"selected": ids.index(active_id) if active_id in ids else 0}

    def render():
        return render_settings_frame(state["selected"], active_id, stream)

    def on_key(key):
        if key in (None, "esc", "quit", "interrupt"):
            return ""         # cancelled; turned into None on the way out
        if key == "up":
            state["selected"] = (state["selected"] - 1) % len(ids)
        elif key == "down":
            state["selected"] = (state["selected"] + 1) % len(ids)
        elif key == "enter":
            try:
                return agent_models.set_model(ids[state["selected"]])
            except (ValueError, OSError):
                # An unwritable choice must not take the screen down with it.
                return ""
        return None

    return _drive(render, key_reader, region, on_key) or None


def help_screen(stream=None, key_reader=None, region=None):
    """Show the help until any exit key. Always returns None."""
    stream = sys.stdout if stream is None else stream
    if key_reader is None:
        if not is_interactive(stream):
            return None
        key_reader = _default_reader()
    region = LiveRegion(stream) if region is None else region

    def on_key(key):
        return "done" if key in (None, "esc", "quit", "enter", "interrupt") else None

    _drive(lambda: render_help_frame(stream), key_reader, region, on_key)
    return None


def run_startup(stream=None, key_reader=None, model_id=None, workspace=None):
    """Show the startup screen once and return what the user chose.

    Returns "start" to enter TMT, or "exit" to quit. Returns "start" without
    drawing anything when the terminal cannot support an interactive menu.

    The interactivity check comes first and is unconditional, including when a
    key_reader was supplied: a piped or scripted run must never be able to
    reach a read, whoever called it. The individual screens are the seam for
    driving the menu without a terminal.
    """
    stream = sys.stdout if stream is None else stream
    if not is_interactive(stream):
        return "start"
    if key_reader is None:
        key_reader = _default_reader()

    region = LiveRegion(stream)
    workspace = agent_config.ROOT_DIR if workspace is None else workspace
    selected = 0
    try:
        _hide_cursor(stream)
        while True:
            choice = main_menu(stream=stream, key_reader=key_reader, region=region,
                               model_id=model_id, workspace=workspace, selected=selected)
            if choice == "settings":
                selected = 1
                chosen = settings_screen(stream=stream, key_reader=key_reader,
                                         region=region, active_id=model_id)
                if chosen:
                    model_id = chosen
            elif choice == "help":
                selected = 2
                help_screen(stream=stream, key_reader=key_reader, region=region)
            elif choice == "exit":
                return "exit"
            else:
                return "start"
    except KeyboardInterrupt:
        return "exit"
    finally:
        # Whatever happened, including an exception on the way out: no stale
        # frames left for the agent to scroll, and a usable terminal.
        region.clear()
        _restore_terminal(stream)
