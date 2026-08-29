"""The screens TMT draws for itself: the startup menu, and the running status.

Two surfaces, one idiom. `render_startup_frame` is the menu TMT opens with;
`render_status` is the header the running session opens with, drawn once and
followed thereafter by `render_prompt` alone. They share the same brand row,
the same rule and the same measured fields, so the running session looks like
a continuation of the screen it was started from rather than a different
program.

Everything here draws through the shared surfaces in agent_ui and repaints
through the LiveRegion in agent_live_renderer, so the screen obeys the same
colour cycle, width measurement and encoding fallbacks as the rest of TMT.

The screen is optional by design. A run whose stdin is not a terminal -- the
test suite, a pipeline, anything scripted -- gets "start" back without a
single character being drawn or read, because a menu that blocks on raw input
there would hang the process rather than fail.
"""

import datetime
import shutil
import sys
import time

import agent_config
import agent_models
from agent_live_renderer import LiveRegion
from agent_ui import (
    DIM, GRADIENT_TICK, RESET, _color, _supports_color, clip_to_width,
    cycle_text, display_width, fit_to_width, gradient_phase, iter_graphemes,
    pad_to_width, plain_output, safe_write, visible_width, wrap_lines,
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

# The narrowest the screen is drawn for. There is deliberately no widest: the
# interface fills the window it was given. It used to stop growing at 72
# columns, which on a wide terminal left the rules, the box and the whole
# session sitting in a strip down the left with the rest of the window unused
# -- and made the box look like a panel dropped onto the terminal rather than
# the interface of the thing running in it.
_MIN_CONTENT = 24
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
    """How wide a row may be drawn, in columns.

    The window, less the one spare column every row leaves at the right: a row
    drawn to the last column wraps on the terminals that auto-wrap, and costs
    a screen line the repaint arithmetic does not know about.

    Floored rather than capped. A terminal narrower than the floor overflows
    it, which is a known and accepted limit; a terminal wider than the window
    does not exist.
    """
    return max(_MIN_CONTENT, columns - 1)


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


# ---------------------------------------------------------------------------
# The running session screen: a header, whatever the turn printed, and the
# prompt box at the bottom.
#
# The startup screen answers "what is about to run". Once a session is under
# way part of that answer keeps moving -- Settings rewrites the provider and
# the model, TMT_PROVIDER and OPENROUTER_MODEL override them, and the clock
# never stops -- and part of it cannot: the workspace was settled before the
# first request and the date will not change under anyone.
#
# So the two are drawn in different places. What is settled goes in the header
# and is printed once; what moves is stated on the prompt box, which is drawn
# again for every question anyway. Nothing here keeps a copy of either: every
# fact is read from the module that owns it at the moment the row is drawn.

TASK_PROMPT = "Task> "

# Where the fixed, non-animating gradient starts and how far it travels.
# Anything printed once into scrollback is being read rather than watched, so
# it takes a point on the gradient rather than the ambient cycle -- and a
# fixed point rather than whatever `gradient_phase()` happened to return,
# because a wordmark that is a different colour every launch is not a
# wordmark. The span deliberately starts past red: red is the error end of
# the scale, and a header is not an error.
BRAND_PHASE = 0.15
BRAND_SPREAD = 0.35

# The header's second row hangs under the first, at the indent the prominence
# ladder already uses for detail belonging to the row above it.
_HEADER_INDENT = 3


def _clock(moment=None):
    """The local wall clock, read now.

    The one seam a test uses to freeze it, and the reason no caller is offered
    somewhere to keep a time: a stored time is a time that goes stale.
    """
    return datetime.datetime.now() if moment is None else moment


def _shorten_middle(text, columns, marker="…"):
    """Drop the middle of a string so the whole fits in `columns` columns.

    Measured, never counted: a path is exactly the kind of string that carries
    characters wider than one column, and trimming it by len() puts the row
    onto a second screen line. Head and tail are taken in turn, so a Windows
    path keeps both its drive and the directory actually being worked in --
    the two ends a reader needs to recognise it.
    """
    text = str(text)
    if columns <= 0:
        return ""
    if display_width(text) <= columns:
        return text
    marker_width = display_width(marker)
    if columns <= marker_width:
        return fit_to_width(text, columns)
    budget = columns - marker_width
    clusters = iter_graphemes(text)
    left, right = 0, len(clusters) - 1
    head, tail, used, take_head = [], [], 0, True
    while left <= right:
        cluster = clusters[left] if take_head else clusters[right]
        size = display_width(cluster)
        if used + size > budget:
            break
        if take_head:
            head.append(cluster)
            left += 1
        else:
            tail.append(cluster)
            right -= 1
        used += size
        take_head = not take_head
    return "".join(head) + marker + "".join(reversed(tail))


def _date_text(moment, columns):
    """The date, giving up the weekday and then the year as room runs out."""
    for pattern in ("%a %d %b %Y", "%d %b %Y", "%d %b"):
        text = moment.strftime(pattern)
        if display_width(text) <= columns:
            return text
    return moment.strftime("%d %b")


def provider_title(provider_id):
    """What the provider calls itself.

    agent_providers is the authority, so its adapter's own label is preferred;
    the menu's table answers for an installation where the adapters cannot be
    imported, which is the same reason every other screen here can draw
    without them.
    """
    if not provider_id:
        return "not set"
    return getattr(_adapter(provider_id), "label", "") or provider_label(provider_id)


def status_facts(provider_id=None, model_id=None, workspace=None):
    """(provider, model, workspace) for the status row, read at call time.

    Each comes from the module that owns it -- agent_credentials for the
    provider, agent_models for the model, agent_config for the workspace --
    and none of it is remembered here. A model chosen in Settings has to reach
    the next prompt rather than the next launch, and an agent_config.MODEL
    captured at import would not carry it there.
    """
    if provider_id is None:
        provider_id = current_provider()
    if model_id is None:
        try:
            model_id = agent_models.current_model(provider_id or None)
        except Exception:
            model_id = ""
    try:
        model = agent_models.describe(model_id, provider_id or None)
    except Exception:
        model = model_id
    workspace = agent_config.ROOT_DIR if workspace is None else workspace
    return (provider_title(provider_id),
            str(model or model_id or "not set"),
            str(workspace))


def render_status_lines(stream=None, size=None, phase=None, moment=None,
                        provider_id=None, model_id=None, workspace=None):
    """The session header, as a list of ready-to-paint lines.

    Two rows and no border. The wordmark and the date are the heading; the
    workspace hangs under them at the indent the prominence ladder already
    uses for detail belonging to the row above. That indent is the whole of
    the grouping -- one element, not two strings that happen to be adjacent --
    and it costs nothing that a reader has to look past.

    There is deliberately no rule at the bottom. There used to be, and the
    prompt box draws one of its own two lines below it, so the screen opened
    with two near-identical dividers around an empty gap that read as a badly
    placed box. One rule, drawn by the thing it belongs to.

    The volatile facts -- the clock, the provider, the model -- are not here.
    They are stated by `prompt_caption` on the box itself, where they are
    re-read for every question rather than frozen at launch. What is left
    here is what cannot change while the session runs.

    Every row is trimmed to measured width before it is painted, so none can
    overflow onto a second screen line, and what is given up first is decided
    rather than incidental: the weekday goes before the year, and a long path
    is shortened through the middle so both its ends survive.
    """
    stream = sys.stdout if stream is None else stream
    columns, rows = _terminal(size)
    phase = BRAND_PHASE if phase is None else phase
    moment = _clock(moment)
    width = _content_width(columns)
    separator = " %s " % _glyphs(stream)["dot"]
    marker = "..." if plain_output(stream) else "…"

    workspace = status_facts(provider_id, model_id, workspace)[2]

    # The heading: whose interface this is, and what day the session opened
    # on. The date is context, so it recedes to the one neutral; the wordmark
    # carries the gradient at a fixed point on it, so TMT is the same colour
    # every launch instead of whatever the colour cycle happened to be on.
    spent = display_width(" TMT" + separator)
    date = _date_text(moment, max(0, width - spent))
    brand = (" " + _paint("TMT", stream, phase, spread=BRAND_SPREAD)
             + _dim(separator + date, stream))

    # Hanging under it: the directory this session may write to. It is the
    # one fact here with real consequences, so it is not dimmed.
    room = max(1, width - _HEADER_INDENT)
    place = " " * _HEADER_INDENT + _shorten_middle(workspace, room, marker)

    return _fit_height(["", brand, place], rows, keep_tail=1)


def prompt_caption(stream=None, width=None, moment=None, provider_id=None,
                   model_id=None):
    """The dim line above the prompt box: when, and what will answer.

    These are the three facts that can be different from one question to the
    next -- the clock always is, and the provider and the model are read from
    the modules that own them rather than from anything captured at launch --
    so they are stated on the box rather than in the header, and re-read every
    time the box is drawn. It also means they are still on screen once the
    header has scrolled away, which on a long session is most of the time.

    Right-aligned so it ends where the rule below it ends: the two read as one
    component, and the left column stays clear for the markers and the '>'.
    Dim, because it is the lowest thing in the prominence ladder -- context
    for the question, never the question.

    What is given up first as the terminal narrows is decided: the provider
    goes before the model, because which model answers is the more useful of
    the two, and the clock is the last thing standing.
    """
    stream = sys.stdout if stream is None else stream
    width = _content_width(_terminal()[0]) if width is None else int(width)
    moment = _clock(moment)
    separator = " %s " % _glyphs(stream)["dot"]
    provider, model, _ = status_facts(provider_id, model_id, "")

    clock = moment.strftime("%H:%M:%S")
    text = clock
    for parts in ((clock, provider, model), (clock, model), (clock,)):
        text = separator.join(parts)
        if display_width(text) <= max(0, width - 1):
            break
    text = fit_to_width(text, max(1, width - 1))
    pad = max(0, width - display_width(text))
    return " " * pad + _dim(text, stream)


def clear_screen(stream=None):
    """Erase the visible screen and put the cursor at the top of it.

    Called as a screen opens -- the startup menu, and the session behind it --
    so each begins at the top of the window rather than wherever the shell
    left the cursor. Everything TMT then prints reads downward from there:
    the header, the question, the work, the answer, in the order they
    happened, filling the window from the top. Once the window is full the
    terminal scrolls, and from that point the prompt box -- always the last
    thing written -- sits at the foot of the screen and stays there.

    The viewport only. `\\033[3J` would take the scrollback with it, and that
    scrollback is somebody's shell session, not TMT's to throw away. It is
    deliberately not sent, and TMT's own history is safe for the opposite
    reason: it has not been written yet.

    Nothing is written where the stream is not a terminal -- a pipe has no
    screen to clear, and the escape would land in whatever was capturing the
    output. Returns whether it did anything.
    """
    stream = sys.stdout if stream is None else stream
    if not getattr(stream, "isatty", lambda: False)():
        return False
    return safe_write(stream, "\033[2J\033[H")


# ---------------------------------------------------------------------------
# The meter in the top right corner.
#
# What the session has changed and what it has cost, in one short row that
# never scrolls. It is the only thing TMT draws outside the flow of the
# screen, and it earns that by never moving: the scrolling region is set to
# start at row two, so row one is TMT's and the terminal's own scrolling never
# touches it. Without that the readout would be stamped over whatever happened
# to be at the top of the window and then scrolled into the history as
# wreckage, and the history is the one surface this project will not spoil.
#
# Everything about it degrades. A stream that is not a terminal gets nothing;
# a terminal that ignores the sequence simply scrolls as it always did, and
# the readout is repainted over row one instead of being pinned there, which
# is untidy rather than broken. The region is always released again.

_TOP_ROW_RESERVED = 1


def _meter_counts(added, removed, tokens_in, tokens_out):
    """The four figures as text, and nothing about how they were arrived at."""
    return ("+%d" % added, "-%d" % removed,
            _short_count(tokens_in), _short_count(tokens_out))


def _short_count(value):
    """A token count at the size a corner readout can carry."""
    value = max(0, int(value))
    if value >= 1000000:
        return "%.1fM" % (value / 1000000.0)
    if value >= 1000:
        return "%dk" % (value // 1000)
    return str(value)


def meter_text(session, stream=None, columns=None):
    """The corner readout for a session, painted, or "" when it says nothing.

    Green for what arrived and red for what left -- the two ends of the one
    gradient, used for exactly what they already mean there. Everything else
    is the one neutral, because the counts are the message and the colour only
    confirms them: with the escapes stripped this still reads
    "+1231 lines, -123 lines, ~15k tokens in, 30k out".

    The tilde is not decoration. Tokens sent are estimated -- no provider will
    count a request before it is sent -- and tokens generated are the
    provider's own figure whenever it gives one. A number that was guessed is
    marked as guessed.
    """
    stream = sys.stdout if stream is None else stream
    if session is None:
        return ""
    added, removed, sent, back = _meter_counts(
        session.lines_added, session.lines_removed,
        session.tokens_in, session.tokens_out)
    if not (session.lines_added or session.lines_removed
            or session.tokens_in or session.tokens_out):
        return ""
    columns = _terminal()[0] if columns is None else int(columns)
    out_mark = "" if session.tokens_out_exact else "~"
    full = "%s lines, %s lines, ~%s tokens in, %s%s out" % (
        added, removed, sent, out_mark, back)
    short = "%s %s  ~%s in  %s%s out" % (added, removed, sent, out_mark, back)
    tiny = "%s %s" % (added, removed)
    for text in (full, short, tiny):
        if display_width(text) <= max(0, columns - 2):
            break
    else:
        return ""
    if not _supports_color(stream):
        return text
    # Only the two counts are coloured, and each takes the position it already
    # holds on the gradient: green is arrived, red is gone.
    painted = text.replace(added, _color(added, 95, stream), 1)
    return painted.replace(" " + removed, " " + _color(removed, 10, stream), 1)


# ---------------------------------------------------------------------------
# Holding the prompt box at the foot of the window.
#
# Two things are wanted at once and they look like they contradict: the box at
# the bottom from the moment the session opens, and the session's output
# reading downward from the header at the top. What reconciles them is that
# the gap between the two is *inside* the live region, as blank rows above the
# box, and a permanent line printed into the region takes one of them.
#
# `LiveRegion.write_above` erases the region, prints where it stood, and paints
# it again below. Painting it again one row shorter puts it back on exactly the
# rows it already occupied, so the box does not move and the printed line lands
# in the space the pad gave up. The output fills the window from the top while
# the box stays where the eye left it, and when the pad is gone the box is at
# the bottom and the terminal's own scrolling keeps it there.
#
# The count never has to be exact. It only ever decreases, it is part of the
# region's own line list so no repaint arithmetic depends on it, and being a
# row or two out means the box sits a row or two off the bottom until the next
# line of output corrects it.

PROMPT_HEIGHT = 4        # the caption, a rule, the line, a rule
_PROMPT_LEAD = 1         # the blank line the box writes above itself


class BottomPad:
    """Blank rows that hold the live region against the foot of the window."""

    def __init__(self, rows=0):
        self.rows = max(0, int(rows))

    def above(self, height, size=None):
        """Blank rows to draw above a region `height` rows tall.

        A taller region -- the relay's, which carries the reply and the status
        row as well as the box -- needs fewer of them to keep its bottom edge
        in the same place. Clamped to the window, so a terminal made shorter
        mid-session cannot ask for a region taller than it is.
        """
        rows = _terminal(size)[1]
        room = max(0, rows - _TOP_ROW_RESERVED - 1 - int(height))
        return max(0, min(self.rows + PROMPT_HEIGHT - int(height), room))

    def take(self, lines):
        """Give up `lines` rows to something printed permanently."""
        self.rows = max(0, self.rows - max(0, int(lines)))
        return self.rows

    def spend(self, text):
        """Give up a row per line of `text`. Returns what is left."""
        return self.take(str(text or "").count("\n"))


def opening_pad(used, stream=None, size=None):
    """Blank rows that put the first prompt box at the foot of the window.

    Answerable only here, and only because the screen has just been cleared:
    the cursor is on a row we know, so the distance to the bottom is
    arithmetic rather than a guess. It never has to be answered again.

    `used` is how many rows have been written since the top of the window --
    the reserved row, if there is one, plus the header.

    The last row of the window is not part of the box. A region is painted by
    writing each of its rows and a newline, so the cursor ends one row below
    it, and a box drawn onto the last row would put the cursor past the bottom
    and scroll the window by one before the session had begun.
    """
    stream = sys.stdout if stream is None else stream
    if not getattr(stream, "isatty", lambda: False)():
        return 0
    rows = _terminal(size)[1]
    return max(0, rows - int(used) - _PROMPT_LEAD - PROMPT_HEIGHT - 1)


def reserve_top_row(stream=None, size=None):
    """Keep row one out of the terminal's scrolling, for the meter.

    Sets the scrolling region to rows two and below and leaves the cursor at
    the top of it, so everything TMT prints from here scrolls under a row it
    can never reach. Must be paired with `release_top_row`, on every path out
    including a crash -- a terminal left with a scrolling region is a terminal
    that behaves oddly for whatever the user runs next.
    """
    stream = sys.stdout if stream is None else stream
    if not getattr(stream, "isatty", lambda: False)():
        return False
    rows = _terminal(size)[1]
    if rows <= _TOP_ROW_RESERVED + 4:
        return False          # too short to give a row away
    return safe_write(stream, "\033[%d;%dr\033[%d;1H"
                              % (_TOP_ROW_RESERVED + 1, rows, _TOP_ROW_RESERVED + 1))


def release_top_row(stream=None):
    """Give the whole window back to the terminal's own scrolling."""
    stream = sys.stdout if stream is None else stream
    if not getattr(stream, "isatty", lambda: False)():
        return False
    return safe_write(stream, "\033[r")


def meter_sequence(text, stream=None, size=None):
    """The escape sequence that draws `text` in the top right corner.

    Saves the cursor, jumps to row one, clears it, writes the readout against
    the right edge, and puts the cursor back. It is emitted as one string, at
    the front of a repaint and before anything that could scroll, because a
    restore is to an absolute row: a line printed between the save and the
    restore would move the terminal out from under it.
    """
    stream = sys.stdout if stream is None else stream
    if not text:
        return ""
    columns = _terminal(size)[0]
    width = visible_width(text)
    column = max(1, columns - width)
    return "\0337\033[1;1H\033[2K\033[1;%dH%s\0338" % (column, text)


def task_prompt(stream=None, phase=None):
    """The prompt the user answers, painted but never carried by colour.

    The word is the message and the gradient only confirms it, so this is
    still a prompt with the ANSI stripped.
    """
    stream = sys.stdout if stream is None else stream
    phase = gradient_phase() if phase is None else phase
    return " " + _paint(TASK_PROMPT.rstrip(), stream, phase) + " "


def render_status(stream=None, prompt=True, **facts):
    """Draw the status header once, and leave the cursor after the prompt.

    Called a single time as the session opens. The header states what the
    session runs under -- the service, the model, the directory, and when it
    began -- and none of those change while the loop is running, so repeating
    it before every prompt only pushed the conversation off the screen. Later
    turns get `render_prompt` instead.

    There is deliberately no thread behind the clock. A background ticker
    would have to repaint the row the reader is sitting on, fighting the input
    line and the live renderer for the same region, and it would animate
    something the user is trying to read.

    Returns the number of rows it drew, or False when the stream has gone so
    a caller can stop drawing to it. The count is what tells the caller how
    far down the window the cursor now is, which is the one moment that is
    answerable and the one the opening pad is worked out from. Decoration is
    never allowed to end the run.
    """
    stream = sys.stdout if stream is None else stream
    lines = render_status_lines(stream=stream, **facts)
    if not safe_write(stream, "\n".join(lines) + "\n"):
        return False
    if not prompt:
        # The caller draws its own input -- the prompt box does, and a second
        # bare "Task>" above it would be two prompts for one question.
        return len(lines)
    return len(lines) if safe_write(stream, task_prompt(stream)) else False


def render_task(task, stream=None, size=None, moment=None):
    """Write the question that was just asked into the terminal's scrollback.

    The box that collected it is a live region and is taken down the moment it
    is answered, so without this the session would keep its replies and lose
    every question that produced them. This is the permanent half: the caption
    that was above the box, and the line as it was typed under the same marker
    it was typed under.

    No rules. The box's rules said "type here", and this is a record of
    something already said -- drawing them again would put an input surface in
    the scrollback that nothing can be entered into.

    Returns False when the stream has gone, like the rest of the drawing here.
    """
    stream = sys.stdout if stream is None else stream
    task = " ".join(str(task or "").split())
    if not task:
        return True
    width = _content_width(_terminal(size)[0])
    caption = prompt_caption(stream, width, moment)
    row = " " + PROMPT_MARKER + " " + fit_to_width(task, max(1, width - _PROMPT_PREFIX))
    return safe_write(stream, caption + "\n" + row + "\n")


def render_prompt(stream=None, phase=None):
    """Draw the task prompt on its own, for every turn after the first.

    The blank line is the whole of the separation between the reply above and
    the question below: the header has already said what the session is, and
    saying it again each turn would bury the answer the user just read.

    Returns False when the stream has gone, matching `render_status`.
    """
    stream = sys.stdout if stream is None else stream
    if not safe_write(stream, "\n"):
        return False
    return safe_write(stream, task_prompt(stream, phase))


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


def is_interactive(stream=None, instream=None):
    """Whether a menu can be driven here at all.

    Any doubt counts as no: a wrong "yes" hangs the process on a read that
    will never be answered, while a wrong "no" only skips a menu.

    `instream` is where the keys would come from, and defaults to the real
    stdin. The prompt box passes its own, because a caller can hand it an
    input that is not the process's.
    """
    stream = sys.stdout if stream is None else stream
    instream = sys.stdin if instream is None else instream
    for candidate in (instream, stream):
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
# typed. Everything else printable is a character of the key. Both the raw
# sequence and the name are accepted, so a scripted reader can send either and
# a terminal that reports a key one way is not treated as a different key.
_TEXT_KEYS = {
    "\r": "enter", "\n": "enter", "\r\n": "enter", "enter": "enter",
    "\x1b": "esc", "esc": "esc",
    "\x7f": "backspace", "\x08": "backspace", "backspace": "backspace",
    "\x03": "interrupt", "interrupt": "interrupt",
    # Editing keys. A field that can only append is not an editor, and every
    # one of these is a key the terminal sends whether it is acted on or not.
    "\x1b[3~": "delete", "\x1b[3": "delete", "delete": "delete",
    "\x1b[D": "left", "\x1bOD": "left", "left": "left",
    "\x1b[C": "right", "\x1bOC": "right", "right": "right",
    "\x1b[H": "home", "\x1bOH": "home", "\x1b[1~": "home", "\x01": "home",
    "home": "home",
    "\x1b[F": "end", "\x1bOF": "end", "\x1b[4~": "end", "\x05": "end",
    "end": "end",
    "\x15": "clear",          # Ctrl-U, as it is in every shell
    "\x17": "delete_word",    # Ctrl-W
    "\x04": "eof", "eof": "eof",   # Ctrl-D
}

# Names a scripted reader may send into a text field that are not text and
# have nothing to do here. They are ignored rather than typed.
_TEXT_IGNORED = ("up", "down")


def normalize_text_key(key):
    """One keystroke for a text field, as (kind, value).

    ("end", None) when the input has ended, ("key", name) for the keys that
    edit rather than type, ("char", text) for characters, and ("", "") for a
    tick or anything unprintable. A multi-character run is accepted whole, so
    a paste that arrives in one read is not split or dropped.
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


# The keys the console reports as a scan code behind a lead byte. They have no
# character of their own, so a name is the only thing either caller can act on.
_WINDOWS_EXTENDED = {"H": "up", "P": "down", "K": "left", "M": "right",
                     "G": "home", "O": "end", "S": "delete"}


def _read_key_windows(msvcrt, timeout, raw=False):
    if timeout is not None:
        deadline = time.monotonic() + timeout
        while not msvcrt.kbhit():
            if time.monotonic() >= deadline:
                return ""
            time.sleep(0.01)
    char = msvcrt.getwch()
    if char in ("\x00", "\xe0"):
        # The name, to both callers. The menu acts on up and down and ignores
        # the rest; a text field acts on the editing keys, and there is no
        # character it could type in their place.
        return _WINDOWS_EXTENDED.get(msvcrt.getwch(), "")
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
            # Delete, Home and End arrive as ESC [ number ~. The parameter
            # bytes are read out here so the terminating '~' is not left
            # behind to be read next as a printable character and typed.
            while (sequence[-1].isdigit() or sequence[-1] == ";") and pending(0.05):
                sequence += take()
        # Raw callers get the sequence itself; it carries an escape, which is
        # not printable, so a field that does not know the sequence drops it
        # rather than typing it.
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


# ---------------------------------------------------------------------------
# The task prompt.
#
# A dim caption, a rule, a marked row, a rule. It is the primary surface of
# the screen -- the only bordered thing below the answer -- and the only one
# with a live caret in it, which is prominence enough without decoration.
#
# Undecorated on purpose. The gradient marks what is alive or measuring, and
# this is neither: it is a surface being read and typed into, and the design
# rule for those is explicit. It also buys the cursor fix below: two frames
# of an untouched box are now byte-identical, so there is nothing to repaint
# while the user sits and thinks, and nothing to move the caret for.
#
# The suggestion drawn in an empty box is a placeholder and nothing else. It
# is held beside the buffer rather than in it, so there is no editing path,
# and no accident of ordering, that can submit a suggestion the user never
# typed.

PROMPT_MARKER = ">"

# Where the line being typed sits, counted from the BOTTOM of the frame: the
# bottom rule is one, the line is two. Counted that way because the top of the
# frame moves -- the caption is dropped while a turn runs, and the blank rows
# holding the box against the foot of the window come and go as output fills
# it -- while the distance from the line to the bottom rule never changes.
_INPUT_ROW = 2
_PROMPT_PREFIX = 3       # columns before the field: a margin, the marker, a gap
_WORD_BREAK = " \t"


class LineEditor:
    """Pure editing logic for one line. No terminal, no I/O.

    `value` is what the user typed and nothing else: it starts empty and the
    placeholder is never assigned to it, at any point, by any key. That is the
    whole of why a suggestion cannot be returned as a task.
    """

    def __init__(self, placeholder=""):
        self.placeholder = str(placeholder or "")
        self.value = ""
        self.cursor = 0
        self.placeholder_visible = bool(self.placeholder)

    def insert(self, text):
        """Insert text at the cursor, and dismiss the placeholder.

        Whole rather than per character: a paste arrives as one read, and
        splitting it would let anything applied per keystroke -- a limit, a
        repaint, a cursor move -- land in the middle of it.
        """
        if not text:
            return
        # Same keystroke, not the next one: the placeholder stops being drawn
        # at the moment the first character exists, so there is no frame in
        # which both are on screen and none in which they are concatenated.
        self.placeholder_visible = False
        self.value = self.value[:self.cursor] + text + self.value[self.cursor:]
        self.cursor += len(text)

    def handle(self, kind, value):
        """Apply one (kind, value) keystroke from `normalize_text_key`.

        Returns "continue" to keep editing, "submit" for a finished line,
        "cancel" for a line the user abandoned, and "end" when the input is
        over.
        """
        if kind == "end":
            return "end"
        if kind == "char":
            self.insert(value)
            return "continue"
        if kind != "key":
            return "continue"        # an animation tick, or a key with no meaning here
        if value == "enter":
            return "submit"
        if value in ("interrupt", "esc"):
            return "cancel"
        if value == "eof":
            # Ctrl-D ends the input on an empty line, exactly as it does at a
            # shell; with something typed it is the forward delete instead.
            if not self.value:
                return "end"
            self._delete()
        elif value == "backspace":
            self._backspace()
        elif value == "delete":
            self._delete()
        elif value == "left":
            self.cursor -= self._back_step()
        elif value == "right":
            self.cursor += self._forward_step()
        elif value == "home":
            self.cursor = 0
        elif value == "end":
            self.cursor = len(self.value)
        elif value == "clear":
            self.value, self.cursor = "", 0
        elif value == "delete_word":
            self._delete_word()
        return "continue"

    # Movement is by grapheme rather than by index: a cursor that steps one
    # code point can land inside a combining sequence, and everything drawn
    # from that offset is then cut through a character.
    def _back_step(self):
        clusters = iter_graphemes(self.value[:self.cursor])
        return len(clusters[-1]) if clusters else 0

    def _forward_step(self):
        clusters = iter_graphemes(self.value[self.cursor:])
        return len(clusters[0]) if clusters else 0

    def _backspace(self):
        size = self._back_step()
        if not size:
            return
        # An empty line does not bring the placeholder back. It was dismissed
        # by the first character typed and stays dismissed for this prompt:
        # a suggestion reappearing under a cursor mid-edit reads as text the
        # user is about to submit.
        self.value = self.value[:self.cursor - size] + self.value[self.cursor:]
        self.cursor -= size

    def _delete(self):
        size = self._forward_step()
        if size:
            self.value = self.value[:self.cursor] + self.value[self.cursor + size:]

    def _delete_word(self):
        head = self.value[:self.cursor]
        stripped = head.rstrip(_WORD_BREAK)
        cut = max((stripped.rfind(char) for char in _WORD_BREAK), default=-1)
        self.value = head[:cut + 1] + self.value[self.cursor:]
        self.cursor = cut + 1


class PromptBox:
    """The bordered task prompt, repainted in place while it is typed.

    Draws a dim caption, a rule, the line under a '>' marker, and a rule.
    What comes back is the line as typed: the placeholder is drawn dim inside
    an empty box and is never any part of the answer.

    The caret is the terminal's own and it stays in the input row. It is moved
    out only for a repaint that has something new to show, and the repaint
    itself is written with the caret suppressed, so it is never drawn anywhere
    but where the user is typing.
    """

    def __init__(self, stream=None, instream=None, reader=None, line_reader=None,
                 corner=None, pad=None):
        self.stream = sys.stdout if stream is None else stream
        self.instream = sys.stdin if instream is None else instream
        # The blank rows that hold the box against the foot of the window, or
        # None to draw it wherever the cursor is. See BottomPad.
        self.pad = pad
        # corner() -> the escape sequence for the readout in the top right, or
        # "". Handed to the region the box paints through, so the readout is
        # refreshed by the same write that draws the box and never by one of
        # its own.
        self.corner = corner
        # reader() -> one raw keystroke. Injecting one is how a test drives
        # the box; left alone it reads through read_key, as every other
        # screen does.
        self.reader = reader
        # line_reader() -> a whole line, or None at the end of the input. The
        # degraded path uses it instead of reading the stream directly, so a
        # caller that already has a reader worth keeping -- the rich console,
        # with its own encoding and history handling -- keeps it rather than
        # having a second one grow up beside it.
        self.line_reader = line_reader
        self.cancelled = False
        # When the box last asked something. The caller writes the question
        # into scrollback afterwards and stamps it with this, so the record
        # carries the time the user actually saw rather than a second one read
        # a moment later.
        self.asked_at = None

    def ask(self, placeholder=""):
        """Take one line. Returns the text, or None when the input ended.

        Enter on an untouched box returns "" -- the empty line the user
        actually entered, never the suggestion that was drawn in it. Esc and
        Ctrl-C also return "", and set `cancelled`: an abandoned line and an
        empty one both mean "ask me again", and only the caller cares which
        it was.
        """
        self.cancelled = False
        # Blank lines are structure. The box arrives under the header, or
        # under a reply that ends in a border, and butted straight against
        # either it reads as part of it rather than as the next question.
        # Written once, outside the repainted region, so the editing that
        # follows does not redraw it.
        safe_write(self.stream, "\n")
        editor = LineEditor(placeholder)
        # The time this question was asked, fixed for the life of the box. A
        # clock re-read on every repaint would change under the reader's own
        # cursor, and animating something being typed into is the one thing
        # the interface is not allowed to do.
        moment = self.asked_at = _clock()
        reader = self.reader
        if reader is None:
            if not is_interactive(self.stream, self.instream):
                # No raw keys to be had: a pipe, a redirect, or the test
                # suite. Waiting on a keystroke that cannot arrive would hang
                # the run, so the box is drawn and the line is read as a line.
                return self._read_line(editor, moment=moment)
            reader = _default_text_reader()
        region = LiveRegion(self.stream)
        region.set_corner(self.corner)
        placed, shown = 0, None
        try:
            while True:
                frame = self._frame(editor, moment=moment)
                if frame != shown:
                    # Something actually changed -- a character, a caret move,
                    # a resize. Only then is the caret taken out of the input
                    # row, and it is put straight back below.
                    #
                    # This is the whole of the flicker fix. The loop used to
                    # do this on every pass, and the reader below returns
                    # every 80ms whether a key arrived or not, so an untouched
                    # prompt walked the caret down to the foot of the box and
                    # back twelve times a second for as long as anyone looked
                    # at it.
                    placed = self._unplace(placed)
                    region.paint(frame[0])
                    placed = self._place(frame[0], frame[1])
                    region.show_cursor()
                    shown = frame
                kind, value = _next_text_key(reader)
                outcome = editor.handle(kind, value)
                if outcome == "continue":
                    continue
                placed = self._unplace(placed)
                # The box is taken down with the answer. It is a live region,
                # not a record: what the user asked is written into scrollback
                # by the caller, in the transcript's own voice, and the box
                # itself is drawn again a moment later at the foot of the turn
                # with the running task in it. Left behind instead, every
                # question of the session would sit on screen inside a frame
                # that no longer accepts anything, and an empty prompt would
                # stack an empty box each time it was answered.
                region.clear()
                if outcome == "submit":
                    return editor.value
                if outcome == "cancel":
                    self.cancelled = True
                    return ""
                return None
        finally:
            self._unplace(placed)
            _restore_terminal(self.stream)

    def lines(self, editor, size=None, phase=None, moment=None, caption=True,
              pad=True):
        """The painted box, as a list of rows."""
        return self._frame(editor, size, phase, moment, caption, pad)[0]

    def running_lines(self, hint="", size=None):
        """The box as it stands while the turn it asked for is running.

        Empty, with the hint drawn as shadow text: there is no caret in it and
        nothing typed into it would be read, so a box that looked ready for
        input would be a lie about what the program is doing. The hint says
        what is happening and how to stop it, which is the only thing the user
        can actually do from here.

        No caption. The caption stamps a question with when it was asked and
        what will answer it, and the question this box is waiting on already
        carries one, three rows up in the scrollback. A second copy of it,
        moving under the reply as it arrives, would be the same fact twice.

        Drawn by the live relay as the footer of its region, so it stays put
        at the foot of the window while the turn's output scrolls past above.
        No blank rows above it either: the relay pads that region as a whole,
        and a pad here would be counted twice and push the box off the bottom.
        """
        return self.lines(LineEditor(hint), size=size, caption=False, pad=False)

    def _frame(self, editor, size=None, phase=None, moment=None, caption=True,
               pad=True):
        """(rows, caret column) for the box as it stands.

        The terminal is measured here rather than remembered, so a window
        resized between two keystrokes is drawn at its new width.

        Undecorated: the rules and the marker are the terminal's own colour.
        That is what makes two frames of an untouched box equal, which is what
        lets `ask` leave the caret alone -- and a box that is being typed into
        has no business carrying a colour that means progress.

        `caption` is off for the box the relay draws while a turn runs, which
        is not asking anything and so has nothing to stamp.
        """
        stream = self.stream
        columns = _terminal(size)[0]
        # A spare column, always: a row drawn to the last one wraps on the
        # terminals that auto-wrap, and costs a screen line the repaint
        # arithmetic does not know about.
        # Six is the narrowest box that is still a box: a margin, the marker,
        # a gap, one column of text and the spare column.
        limit = max(6, columns - 1)
        width = min(_content_width(columns), limit)
        inner = max(1, width - _PROMPT_PREFIX)

        head = [prompt_caption(stream, width, moment)] if caption else []
        rule = " " + _glyphs(stream)["rule"] * (width - 1)
        text, caret = self._field(editor, inner)
        body = _dim(text, stream) if editor.placeholder_visible else text
        row = " " + PROMPT_MARKER + " " + body
        rows = head + [rule, row, rule]
        # Blank rows above, so the box sits at the foot of the window from the
        # moment the session opens rather than wherever the last reply
        # stopped. They are part of the region, so the repaint arithmetic
        # counts them like any other row, and they are given up one at a time
        # as permanent output fills the window from the top.
        lead = [""] * (self.pad.above(len(rows), size) if self.pad else 0)
        return lead + rows, _PROMPT_PREFIX + caret

    def _field(self, editor, inner):
        """The visible slice of the line, and where the caret sits in it.

        The row scrolls sideways rather than wrapping, so a long task stays on
        one line with the caret in view. Both ends are measured in columns:
        an offset counted in characters puts a wide one half off the edge.
        """
        if editor.placeholder_visible:
            return fit_to_width(editor.placeholder, inner), 0
        text, cursor = editor.value, editor.cursor
        start = 0
        # One column is kept for the caret itself, which sits after the last
        # character rather than on it.
        while display_width(text[start:cursor]) > inner - 1:
            head, _ = clip_to_width(text[start:], 1)
            if not head:
                break
            start += len(head)
        visible, _ = clip_to_width(text[start:], inner)
        return fit_to_width(visible, inner), display_width(text[start:cursor])

    def _read_line(self, editor, moment=None):
        """Draw the box once, then take a whole line from the input.

        The degraded path, and the one every scripted run takes. It reads a
        line and returns it; nothing here can block on a key.
        """
        for line in self.lines(editor, moment=moment):
            if not safe_write(self.stream, line + "\n"):
                break
        if self.line_reader is not None:
            try:
                return self.line_reader()
            except (EOFError, KeyboardInterrupt):
                return None
        try:
            line = self.instream.readline()
        except (OSError, ValueError, EOFError, KeyboardInterrupt):
            return None
        if not line:
            return None              # the input ended
        return line.rstrip("\r\n")

    def _ansi(self):
        return bool(getattr(self.stream, "isatty", lambda: False)())

    def _place(self, lines, column):
        """Move the caret into the input row. Returns the rows moved up."""
        if not self._ansi():
            return 0
        up = _INPUT_ROW          # counted from the bottom; see the constant
        parts = ["\033[%dA" % up, "\r"]
        if column:
            parts.append("\033[%dC" % column)
        safe_write(self.stream, "".join(parts))
        return up

    def _unplace(self, up):
        """Put the caret back where LiveRegion expects to find it."""
        if up and self._ansi():
            safe_write(self.stream, "\r\033[%dB" % up)
        return 0


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
        # The menu is a screen, so it starts at the top of one rather than
        # halfway down whatever the shell last printed.
        clear_screen(stream)
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
