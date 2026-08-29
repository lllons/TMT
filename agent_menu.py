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
    fit_to_width, gradient_phase, iter_graphemes, pad_to_width, plain_output,
    safe_write, wrap_lines,
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
    ("settings", "Settings", "Provider, API key and the model TMT runs on"),
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
    ("head", "Providers and models"),
    ("body", "Settings holds three things: the provider TMT sends requests to,"),
    ("body", "the API key for it, and the model. The free models listed there"),
    ("body", "are OpenRouter's. Enter saves a choice for every later run."),
    ("body", "OPENROUTER_MODEL overrides the model, TMT_PROVIDER the provider."),
    ("gap", ""),
    ("head", "Tips"),
    ("body", "Describe the outcome you want, not the keystrokes to reach it."),
    ("body", "Name the files you already know are involved."),
    ("body", "Ask for a commit or a push in words; TMT will not push unasked."),
    ("gap", ""),
    ("dim", "Read what TMT writes before you rely on it."),
)

# The providers the screens offer and the order they are listed in. The names
# and the one-line notes are the menu's own: a screen has to draw before any
# adapter is loaded, and on an installation where the provider modules are
# missing entirely. agent_credentials owns the real list, and is preferred
# whenever it can be imported.
PROVIDER_ORDER = ("openrouter", "openai", "anthropic", "gemini")

PROVIDER_LABELS = {
    "openrouter": ("OpenRouter", "free models, the TMT default"),
    "openai": ("OpenAI", "GPT models, paid key"),
    "anthropic": ("Anthropic", "Claude models, paid key"),
    "gemini": ("Gemini", "Google models, paid key"),
}

# Where each provider issues keys. An adapter's own key_url wins when there is
# an adapter to ask; these are the fallback for when there is not.
PROVIDER_KEY_URLS = {
    "openrouter": "https://openrouter.ai/keys",
    "openai": "https://platform.openai.com/api-keys",
    "anthropic": "https://console.anthropic.com/settings/keys",
    "gemini": "https://aistudio.google.com/apikey",
}

SETTINGS_ITEMS = (
    ("provider", "AI Provider", "Which service answers a request"),
    ("key", "API Key", "The credential that service is given"),
    ("model", "Model", "Which model TMT runs on"),
    ("back", "Back", "Return to the menu"),
)

# One per keystroke, and the only thing the key screen ever echoes. ASCII on
# purpose: it is drawn on every terminal, including the ones that cannot carry
# the decorative glyphs.
MASK_CHAR = "*"

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


def _wrap_words(text, columns):
    """Break a message on spaces rather than mid-word.

    Measured in terminal columns, not counted in characters, and a single word
    too long for the row falls back to the measured clipper, so no message can
    overflow into a second screen line.
    """
    lines, current = [], ""
    for word in text.split():
        candidate = word if not current else current + " " + word
        if display_width(candidate) <= columns:
            current = candidate
            continue
        if current:
            lines.append(current)
        pieces = wrap_lines(word, columns)
        lines.extend(pieces[:-1])
        current = pieces[-1]
    if current:
        lines.append(current)
    return lines or [""]


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


def _credentials():
    """agent_credentials, or None when it cannot be loaded.

    Imported inside the call rather than at module scope so the startup screen
    still imports, and still runs, on an installation where the provider
    modules are absent or broken. A menu that cannot draw is worth less than an
    agent that still starts.
    """
    try:
        import agent_credentials
        return agent_credentials
    except Exception:
        return None


def _adapter(provider_id):
    """The agent_providers adapter for a provider, or None.

    Used only for what the menu cannot know on its own: where a key is issued,
    whether one looks well formed, and what the provider itself says about it.
    Every screen draws without it.
    """
    try:
        import agent_providers
    except Exception:
        return None
    for name in ("get", "get_provider", "provider", "adapter", "for_id"):
        factory = getattr(agent_providers, name, None)
        if callable(factory):
            try:
                return factory(provider_id)
            except Exception:
                return None
    for name in ("REGISTRY", "PROVIDERS_BY_ID", "PROVIDER_MAP", "ADAPTERS"):
        registry = getattr(agent_providers, name, None)
        if isinstance(registry, dict) and provider_id in registry:
            entry = registry[provider_id]
            try:
                return entry() if isinstance(entry, type) else entry
            except Exception:
                return None
    return None


def provider_ids():
    """The provider ids to offer, from the store when it can be loaded."""
    store = _credentials()
    ids = [name for name in getattr(store, "PROVIDERS", ()) if isinstance(name, str)]
    return ids or list(PROVIDER_ORDER)


def provider_label(provider_id):
    return PROVIDER_LABELS.get(provider_id, (provider_id or "not set", ""))[0]


def provider_note(provider_id):
    return PROVIDER_LABELS.get(provider_id, ("", ""))[1]


def provider_key_url(provider_id):
    return getattr(_adapter(provider_id), "key_url", "") or PROVIDER_KEY_URLS.get(provider_id, "")


def current_provider():
    """The provider in force, or "" when TMT cannot tell."""
    store = _credentials()
    try:
        return store.selected_provider() if store else ""
    except Exception:
        return ""


def _provider_overridden():
    store = _credentials()
    try:
        return bool(store.is_provider_overridden()) if store else False
    except Exception:
        return False


def provider_has_key(provider_id):
    """Whether a key is available for a provider, from any source."""
    store = _credentials()
    if store is not None:
        try:
            return bool(store.has_credential(provider_id))
        except Exception:
            return False
    # Without the store, the OpenRouter key in agent_config is all there is.
    return provider_id == "openrouter" and bool(agent_config.OPENROUTER_API_KEY)


def provider_key_hint(provider_id):
    """A safe description of the key in force: masked, and where it came from.

    Never the key. The mask comes from the store, which is built to show too
    little to reconstruct one, and the source is what explains an environment
    variable outranking a key the user just typed.
    """
    store = _credentials()
    mask, origin = "", ""
    if store is not None:
        try:
            mask, origin = store.masked(provider_id), store.source(provider_id)
        except Exception:
            mask, origin = "", ""
    if not mask:
        return "key set" if provider_has_key(provider_id) else "no key stored"
    return "%s  from %s" % (mask, origin) if origin else mask


def provider_is_configured():
    """Whether TMT can already reach a model without asking for anything.

    False only when there is a credential store and it holds no key for the
    selected provider. Without the store there is nothing setup could write,
    so the answer is yes and the user goes straight to the menu they know.
    """
    store = _credentials()
    if store is None:
        return True
    try:
        return bool(store.has_credential(store.selected_provider()))
    except Exception:
        return True


def _select_provider(provider_id):
    """Record the provider choice. False when there was nowhere to record it."""
    store = _credentials()
    try:
        return bool(store and store.set_provider(provider_id))
    except Exception:
        return False


def _save_credential(provider_id, key):
    """Store a key for a provider. Returns (saved, reason_when_not).

    The key goes to the store when there is one. Without it, only OpenRouter
    has somewhere to go -- the .tmt_key file that predates the store -- and
    saying so is better than reporting a save that went nowhere.
    """
    store = _credentials()
    if store is not None:
        try:
            store.set_credential(provider_id, key)
            return True, ""
        except ValueError as error:
            return False, str(error)
        except OSError as error:
            return False, "The key could not be written: %s" % error
    if provider_id != "openrouter":
        return False, ("TMT cannot store a key for %s on this installation."
                       % provider_label(provider_id))
    try:
        agent_config.save_api_key(key)
    except OSError as error:
        return False, "The key could not be written: %s" % error
    return True, ""


def _shape_warning(provider_id, key):
    """What a local look at the key says about it, or "".

    A shape check is a doubt, never a verdict: key formats change, so a key
    that looks wrong is still stored and the doubt is reported beside it.
    """
    check = getattr(_adapter(provider_id), "looks_like_key", None)
    if not callable(check):
        return ""
    try:
        ok, reason = check(key)
    except Exception:
        return ""
    return "" if ok else (reason or "That does not look like a %s key."
                          % provider_label(provider_id))


def _check_credential(provider_id, key):
    """What the provider says about a key, reported as it said it.

    TMT never decides that a key is valid. Only the provider can, it may not
    have been asked, and when there is nothing to ask that is what the message
    says instead.
    """
    label = provider_label(provider_id)
    check = getattr(_adapter(provider_id), "validate_credentials", None)
    if not callable(check):
        return "Saved. TMT has not checked it with %s; the first request will." % label
    try:
        result = check(key)
    except Exception as error:
        # The class name only: an adapter fault is not the user's problem, and
        # an exception message can carry a request that carried the key.
        return "Saved. TMT could not check it with %s (%s)." % (label, error.__class__.__name__)
    if isinstance(result, tuple) and len(result) == 2:
        accepted, message = result
    else:
        accepted, message = bool(result), ""
    message = str(message or ("%s accepted it." % label if accepted
                              else "%s did not confirm it." % label))
    # Belt and braces: nothing that came back may carry the key onto a screen.
    if key and key in message:
        message = message.replace(key, "the key")
    return "Saved. " + message


def _store_key(provider_id, key):
    """Save a typed key and report the outcome. Returns (message, saved)."""
    shape = _shape_warning(provider_id, key)
    saved, problem = _save_credential(provider_id, key)
    if not saved:
        return problem or "The key was not saved.", False
    message = _check_credential(provider_id, key)
    return (message + "  " + shape if shape else message), True


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

    # Which service will answer is as much a fact about the next request as
    # which model will, and a provider with no key has to say so here rather
    # than at the first request.
    provider_id = current_provider()
    provider_text = provider_label(provider_id) if provider_id else "not set"
    if provider_id and not provider_has_key(provider_id):
        provider_text += "  (no key yet)"

    body = [
        "",
        " " + _paint("TMT", stream, phase) + _dim("  %s  v%s" % (glyph["dot"], _version()), stream),
        "",
        _field("Provider", provider_text, stream, width),
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


def render_provider_frame(selected=0, active_id=None, stream=None, size=None, phase=None):
    """The provider chooser: which service TMT sends a request to."""
    stream = sys.stdout if stream is None else stream
    columns, rows = _terminal(size)
    phase = gradient_phase() if phase is None else phase
    width = _content_width(columns)
    ids = provider_ids()
    active_id = active_id or current_provider()
    label_width = max(display_width(provider_label(name)) for name in ids)

    lines = [
        "",
        " " + _paint("AI Provider", stream, phase),
        "",
        _dim(" The service TMT sends every request to.", stream),
    ]
    if _provider_overridden():
        # The same courtesy the model picker pays OPENROUTER_MODEL: a choice
        # that cannot take effect must not look as though it has.
        lines.append(_dim(" TMT_PROVIDER is set and forces the provider. A choice made", stream))
        lines.append(_dim(" here is saved, but applies only once that variable is unset.", stream))
    lines.append(_rule(stream, phase, width))
    for index, name in enumerate(ids):
        state = "key set" if provider_has_key(name) else "no key"
        lines.append(_option_row(
            index == selected, provider_label(name),
            pad_to_width(state, 8) + " " + provider_note(name),
            stream, phase, width, label_width,
            suffix="  (active)" if name == active_id else ""))
    # The id and the state of the key belong to the row the cursor is on, the
    # way the model picker's note and full id do.
    chosen = ids[selected % len(ids)]
    lines.append("")
    lines.append(_dim(fit_to_width(" %s  %s  %s" % (chosen, _glyphs(stream)["dot"],
                                                    provider_key_hint(chosen)), width), stream))
    lines.append("")
    lines.append(_footer(stream, ("{up}/{down} Navigate", "Enter Select", "Esc Back")))
    return _fit_height(lines, rows, keep_tail=1)


def render_key_frame(provider_id, typed=0, message="", stream=None, size=None,
                     phase=None, done=False):
    """The masked key entry screen.

    `typed` is a count, never the characters: the frame is built from how much
    was typed and has no access to what was typed, so no drawing path can put
    a key on screen. `done` is the state after a save, where there is an
    outcome to read and nothing left to type.
    """
    stream = sys.stdout if stream is None else stream
    columns, rows = _terminal(size)
    phase = gradient_phase() if phase is None else phase
    width = _content_width(columns)
    label = provider_label(provider_id)
    url = provider_key_url(provider_id)

    lines = [
        "",
        " " + _paint("API Key", stream, phase)
        + _dim("  %s  %s" % (_glyphs(stream)["dot"], label), stream),
        "",
        _dim(fit_to_width(" Stored beside TMT and git-ignored, obfuscated rather", width), stream),
        _dim(fit_to_width(" than encrypted, and never shown back to you.", width), stream),
    ]
    if url:
        lines.append(_dim(fit_to_width(" Get a key at %s" % url, width), stream))
    lines.append(_field("Current", provider_key_hint(provider_id), stream, width))
    lines.append(_rule(stream, phase, width))
    if not done:
        entry = MASK_CHAR * typed if typed else "paste or type it; only %s is echoed" % MASK_CHAR
        row = fit_to_width(" " + pad_to_width("Key", 6) + entry, width)
        lines.append(row if typed else _dim(row, stream))
    lines.append("")
    for text in (_wrap_words(message, width - 1) if message else [""]):
        lines.append(fit_to_width(" " + text, width))
    lines.append("")
    hints = ("Enter Continue", "Esc Back") if done else (
        "Enter Save", "Backspace Delete", "Esc Back")
    lines.append(_footer(stream, hints))
    return _fit_height(lines, rows, keep_tail=1)


def render_settings_menu_frame(selected=0, stream=None, model_id=None, size=None, phase=None):
    """Settings: what TMT talks to, with which key, as which model."""
    stream = sys.stdout if stream is None else stream
    columns, rows = _terminal(size)
    phase = gradient_phase() if phase is None else phase
    width = _content_width(columns)
    provider_id = current_provider()
    model_id = model_id or agent_models.current_model()
    label_width = max(display_width(item[1]) for item in SETTINGS_ITEMS)

    lines = [
        "",
        " " + _paint("Settings", stream, phase),
        "",
        _field("Provider", provider_label(provider_id) if provider_id else "not set",
               stream, width),
        _field("API Key", provider_key_hint(provider_id), stream, width),
        _field("Model", agent_models.describe(model_id), stream, width),
        _rule(stream, phase, width),
    ]
    lines.extend(
        _option_row(index == selected, item[1], item[2], stream, phase, width, label_width)
        for index, item in enumerate(SETTINGS_ITEMS)
    )
    lines.append("")
    lines.append(_footer(stream, ("{up}/{down} Navigate", "Enter Select", "Esc Back")))
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


# The only keys that mean something other than themselves while text is being
# typed. Everything else printable is a character of the key.
_TEXT_KEYS = {
    "\r": "enter", "\n": "enter", "\r\n": "enter", "enter": "enter",
    "\x1b": "esc", "esc": "esc",
    "\x7f": "backspace", "\x08": "backspace", "backspace": "backspace",
    "\x03": "interrupt", "interrupt": "interrupt",
}

# Navigation names a scripted reader may send into a text field. They are not
# text, so they are ignored rather than typed.
_TEXT_IGNORED = ("up", "down", "left", "right")


def normalize_text_key(key):
    """One keystroke for a text field, as (kind, value).

    ("end", None) when the input has ended, ("key", name) for the few keys
    that edit rather than type, ("char", text) for characters, and ("", "")
    for a tick or anything unprintable. A multi-character run is accepted
    whole, so a paste that arrives in one read is not split or dropped.
    """
    if key is None:
        return ("end", None)
    if key in _TEXT_KEYS:
        return ("key", _TEXT_KEYS[key])
    if key in _TEXT_IGNORED:
        return ("", "")
    if key and key.isprintable():
        return ("char", key)
    return ("", "")


def _read_key_windows(msvcrt, timeout, raw=False):
    if timeout is not None:
        deadline = time.monotonic() + timeout
        while not msvcrt.kbhit():
            if time.monotonic() >= deadline:
                return ""
            time.sleep(0.01)
    char = msvcrt.getwch()
    if char in ("\x00", "\xe0"):
        arrow = {"H": "up", "P": "down", "K": "left", "M": "right"}.get(msvcrt.getwch(), "")
        # An arrow is a name rather than text, so text entry ignores it.
        return "" if raw else arrow
    return char if raw else normalize_key(char)


def _read_key_posix(stream, timeout, raw=False):
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
            return char if raw else normalize_key(char)
        # An escape on its own is Esc; an escape with more behind it is an
        # arrow. Only pending bytes are read, so Esc never waits long.
        if not pending(0.05):
            return "esc"
        sequence = "\x1b" + take()
        if sequence[-1] in "[O":
            sequence += take()
        # Raw callers get the sequence itself; it carries an escape, which is
        # not printable, so text entry drops it rather than typing it.
        return sequence if raw else normalize_key(sequence)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, _saved_termios)
        _saved_termios = None


def read_key(stream=None, timeout=None, raw=False):
    """Read one keystroke and return its name.

    Returns "" when nothing recognised arrived (including a timeout) and None
    when the input has ended. Raw mode, where it is needed at all, is restored
    in a finally on every path, so an exception cannot leave the terminal
    unusable.

    `raw` returns the character as typed instead of a menu key name. Text
    entry needs it: on the menu "k" means up and "q" means quit, and those are
    exactly the characters an API key is made of.
    """
    stream = sys.stdin if stream is None else stream
    backend = _key_backend()
    if backend is None:
        return None
    try:
        if backend.__name__ == "msvcrt":
            return _read_key_windows(backend, timeout, raw)
        return _read_key_posix(stream, timeout, raw)
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


def _next_text_key(key_reader):
    """One keystroke from the reader, read as text rather than as a menu key."""
    try:
        return normalize_text_key(key_reader())
    except (StopIteration, IndexError):
        return ("end", None)
    except KeyboardInterrupt:
        return ("key", "interrupt")


def _default_reader():
    return lambda: read_key(timeout=GRADIENT_TICK)


def _default_text_reader():
    return lambda: read_key(timeout=GRADIENT_TICK, raw=True)


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


def model_screen(stream=None, key_reader=None, region=None, active_id=None):
    """Choose the model. Returns the saved id, or None when nothing changed.

    The cursor starts on the model in force, so Enter without moving is a
    confirmation rather than an accidental change. This is the picker Settings
    used to be, unchanged: it is now what the Model entry opens.
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


def provider_screen(stream=None, key_reader=None, region=None, active_id=None):
    """Choose the provider. Returns the chosen id, or None when nothing was.

    Choosing here saves nothing on its own. provider_setup records the choice
    once it knows the provider can actually be reached, so backing out of the
    key screen cannot leave TMT pointed at a provider it has no key for.
    """
    stream = sys.stdout if stream is None else stream
    if key_reader is None:
        if not is_interactive(stream):
            return None
        key_reader = _default_reader()
    region = LiveRegion(stream) if region is None else region
    ids = provider_ids()
    active_id = active_id or current_provider()
    state = {"selected": ids.index(active_id) if active_id in ids else 0}

    def render():
        return render_provider_frame(state["selected"], active_id, stream)

    def on_key(key):
        if key in (None, "esc", "quit", "interrupt"):
            return ""         # cancelled; turned into None on the way out
        if key == "up":
            state["selected"] = (state["selected"] - 1) % len(ids)
        elif key == "down":
            state["selected"] = (state["selected"] + 1) % len(ids)
        elif key == "enter":
            return ids[state["selected"]]
        return None

    return _drive(render, key_reader, region, on_key) or None


def api_key_screen(provider_id=None, stream=None, key_reader=None, region=None):
    """Take an API key for one provider. Returns its id when one was saved.

    A mask is echoed, one character per keystroke; the key itself is never
    drawn, and the frame is built from how much was typed rather than from
    what. Enter with nothing typed is a message rather than a save, Esc leaves
    without writing anything, and the outcome afterwards is whatever the
    provider said -- TMT does not pronounce a key valid on its own.
    """
    stream = sys.stdout if stream is None else stream
    provider_id = provider_id or current_provider() or PROVIDER_ORDER[0]
    if key_reader is None:
        if not is_interactive(stream):
            return None
        key_reader = _default_text_reader()
    region = LiveRegion(stream) if region is None else region
    typed, message, saved = [], "", False

    while True:
        region.paint(render_key_frame(provider_id, len(typed), message, stream, done=saved))
        kind, value = _next_text_key(key_reader)
        if kind == "end":
            return provider_id if saved else None
        if kind == "":
            continue          # unrecognised, or an animation tick
        if saved:
            return provider_id            # the outcome has been read
        if kind == "char":
            # Split rather than appended: a paste that arrives as one read is
            # still one mask and one backspace per character the user sees.
            typed.extend(iter_graphemes(value))
            message = ""
            continue
        if value in ("esc", "interrupt"):
            return None
        if value == "backspace":
            if typed:
                typed.pop()
            message = ""
            continue
        if value == "enter":
            key = "".join(typed).strip()
            if not key:
                message = "Nothing was typed. Paste the key, or press Esc to go back."
                continue
            # Storing and checking both take a moment; say so rather than
            # leave a screen that looks as though the key did nothing.
            region.paint(render_key_frame(
                provider_id, len(typed),
                "Saving, and asking %s about it..." % provider_label(provider_id), stream))
            message, saved = _store_key(provider_id, key)
            if saved:
                typed = []    # nothing is kept in memory once it is stored


def provider_setup(stream=None, key_reader=None, region=None, text_reader=None):
    """Pick a provider and, when it has no key, take one.

    Returns the configured provider id, or None when nothing was configured.
    Esc in the key screen comes back to the provider list and Esc in the list
    leaves setup entirely, so a user with no key to hand is never trapped.
    """
    stream = sys.stdout if stream is None else stream
    scripted = key_reader is not None
    if key_reader is None:
        if not is_interactive(stream):
            return None
        key_reader = _default_reader()
    if text_reader is None and scripted:
        text_reader = key_reader
    region = LiveRegion(stream) if region is None else region
    while True:
        chosen = provider_screen(stream=stream, key_reader=key_reader, region=region)
        if not chosen:
            return None
        if provider_has_key(chosen):
            _select_provider(chosen)
            return chosen
        if api_key_screen(chosen, stream=stream, key_reader=text_reader, region=region):
            _select_provider(chosen)
            return chosen


def settings_screen(stream=None, key_reader=None, region=None, active_id=None,
                    text_reader=None):
    """Settings: provider, key and model. Returns a new model id, or None.

    Each entry opens a screen of its own and Esc there returns here, so the
    model picker that used to be the whole of Settings is one row further in
    and otherwise untouched.
    """
    stream = sys.stdout if stream is None else stream
    scripted = key_reader is not None
    if key_reader is None:
        if not is_interactive(stream):
            return None
        key_reader = _default_reader()
    if text_reader is None and scripted:
        text_reader = key_reader
    region = LiveRegion(stream) if region is None else region
    state = {"selected": 0, "model": active_id or agent_models.current_model(),
             "changed": None}

    def render():
        return render_settings_menu_frame(state["selected"], stream, state["model"])

    def on_key(key):
        if key in (None, "esc", "quit", "interrupt"):
            return "done"
        if key == "up":
            state["selected"] = (state["selected"] - 1) % len(SETTINGS_ITEMS)
        elif key == "down":
            state["selected"] = (state["selected"] + 1) % len(SETTINGS_ITEMS)
        elif key == "enter":
            entry = SETTINGS_ITEMS[state["selected"]][0]
            if entry == "back":
                return "done"
            if entry == "provider":
                provider_setup(stream=stream, key_reader=key_reader, region=region,
                               text_reader=text_reader)
            elif entry == "key":
                api_key_screen(current_provider(), stream=stream,
                               key_reader=text_reader, region=region)
            elif entry == "model":
                chosen = model_screen(stream=stream, key_reader=key_reader,
                                      region=region, active_id=state["model"])
                if chosen:
                    state["model"], state["changed"] = chosen, chosen
        return None

    _drive(render, key_reader, region, on_key)
    return state["changed"]


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
    # A scripted caller drives the text screens with the same reader; a real
    # terminal needs the raw one, which those screens build for themselves.
    text_reader = key_reader
    if key_reader is None:
        key_reader = _default_reader()

    region = LiveRegion(stream)
    workspace = agent_config.ROOT_DIR if workspace is None else workspace
    selected = 0
    try:
        _hide_cursor(stream)
        # Only when TMT has no way to reach a model at all. Anyone who already
        # has a provider goes straight to the menu and changes it in Settings.
        if not provider_is_configured():
            provider_setup(stream=stream, key_reader=key_reader, region=region,
                           text_reader=text_reader)
        while True:
            choice = main_menu(stream=stream, key_reader=key_reader, region=region,
                               model_id=model_id, workspace=workspace, selected=selected)
            if choice == "settings":
                selected = 1
                chosen = settings_screen(stream=stream, key_reader=key_reader,
                                         region=region, active_id=model_id,
                                         text_reader=text_reader)
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
