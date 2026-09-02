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
import re
import shutil
import sys
import threading
import time

import agent_config
import agent_models
from agent_live_renderer import LiveRegion
from agent_ui import (
    BOLD, DIM, GRADIENT_TICK, RESET, UNDERLINE, _color, _supports_color,
    clip_to_width, cycle_text, display_width, fit_to_width, gradient_phase,
    iter_graphemes, pad_to_width, plain_output, safe_write, visible_width,
    wrap_lines,
)

FALLBACK_VERSION = "0.1.1"

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

# What the first row says when the menu was reached by `/back` rather than by
# launching. It keeps the `start` key, because it is the same choice -- go and
# work -- and giving it a key of its own would mean every caller of this menu
# had to learn a second word for the same answer.
RESUME_ITEM = ("start", "Resume", "Go back to the session, which is still here")

# What is said INSTEAD of the Settings row while work is still running. The
# button is genuinely gone -- there is nothing to select and nothing to press
# -- and this is a dim line above the list rather than a row in it, because a
# button that silently vanished would read as a fault in TMT. Every refusal in
# this program says what happened and what would change it; this is that rule
# applied to an absence.
BUSY_NOTE = ("Settings are not offered while work is running: %s. "
             "Wait for it to finish, then /back again.")

# What Exit means when there is a session behind the menu. The word is the
# same and the consequence is not: this one ends a conversation that is still
# alive, and a menu that did not say so would let somebody lose it by pressing
# Enter on the row they pressed Enter on last time.
RESUME_EXIT_DETAIL = "Close TMT and end the session"


def menu_items(resuming=False, busy=False):
    """The menu's rows for this moment.

    Two substitutions and a removal, and all three are about what is true right
    now rather than about taste. Coming back from a session, Start is Resume --
    pressing it does not begin anything, it returns to something -- and Exit
    says that it ends the session, because the word is the same as it was at
    launch and the consequence is not. And while any work is still running
    Settings is not offered at all, because the provider, the key and the model
    are read while a request is in flight, and changing one underneath a
    running agent lands a change nobody asked for in the middle of a request
    that had already started.

    The keys are unchanged, so a caller acts on `start`, `settings`, `help`
    and `exit` exactly as it always has -- and `settings` simply is not among
    them while work is running, which is what makes the removal a guarantee
    rather than a hidden row somebody could still reach.
    """
    items = []
    for entry in MENU_ITEMS:
        if entry[0] == "start" and resuming:
            items.append(RESUME_ITEM)
        elif entry[0] == "settings" and busy:
            continue
        elif entry[0] == "exit" and resuming:
            items.append((entry[0], entry[1], RESUME_EXIT_DETAIL))
        else:
            items.append(entry)
    return tuple(items)

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
    ("body", "Type /back in a session to reach this menu without losing it."),
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
    # The one entry here that is a switch rather than a screen: Enter toggles
    # it in place and there is nothing further in. Its detail names what it
    # does NOT control, because that is the thing about it that is easy to get
    # wrong -- the launch screen is shown on every launch whatever this says,
    # and a user turning this off to stop seeing the splash would be turning
    # off the wrong thing.
    ("autoupdate", "Auto Update on Launch",
     "Check for a newer TMT after the launch screen"),
    # The second switch, beside the first and drawn the same way. Its detail
    # names WHERE the files go, because that is the thing about it a user
    # needs to know before deciding: this one writes into their project rather
    # than into TMT's own directory, which is true of nothing else in here.
    ("projectcontext", "Project Context",
     "Keep TMT_Context/notes.md and progress.md in each project"),
    # Last before Back, and it is the position that does the work: everything
    # above it changes what TMT does next, and this ends TMT. A row that can
    # remove the program does not sit between two rows that set a preference,
    # and the cursor starts at the top, so nobody arrives on it.
    ("danger", "Danger Zone", "Uninstall TMT from this machine"),
    ("back", "Back", "Return to the menu"),
)

# The one thing in the Danger Zone, and Back beside it. A section with a
# single entry looks like an oversight until you read what the entry is: the
# room around it IS the warning, and a screen of its own is what stops an
# Enter meant for the row above landing on this one.
DANGER_ITEMS = (
    ("uninstall", "Uninstall TMT", "Remove TMT's files and the tmtcode command"),
    ("back", "Back", "Leave everything as it is"),
)

# What has to be typed to go through with it. A word rather than a key: this
# is the only action in TMT that removes TMT, and the gap between "pressed
# Enter twice" and "typed a word that means it" is the whole safety of the
# screen. It is compared case-insensitively -- the deliberateness is in the
# nine characters, not in the shift key.
UNINSTALL_WORD = "UNINSTALL"


class _Uninstalled(object):
    """The answer Settings gives when there is no TMT left to go back to.

    A sentinel object rather than a string, because `settings_screen` returns
    a model id and any string could one day be one. Identity cannot collide
    with a model, so `chosen is UNINSTALLED` is exact and stays exact.
    """

    def __repr__(self):
        return "<uninstalled>"


UNINSTALLED = _Uninstalled()

# What the toggle reads as. Words rather than a glyph, so the row survives the
# escapes being stripped -- the rule every state in TMT is drawn by.
AUTO_UPDATE_LABELS = ("OFF", "ON")

# The same two words for the second switch. A separate name rather than a
# shared one so that either row can change its wording without silently
# changing the other's.
PROJECT_CONTEXT_LABELS = ("OFF", "ON")

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


def _option_row(is_selected, label, detail, stream, phase, width, label_width,
                suffix="", live=False):
    """One selectable row.

    The '>' marker carries the selection on its own; colour only reinforces
    it, because colour is not always available. `suffix` is protected from
    trimming -- it marks the active model, which a narrow terminal must not be
    allowed to hide.

    `live` is for a row that is saying something about itself rather than
    about being chosen: Resume, which means "the session you left is still
    running behind this menu". It rides the colour cycle whether or not the
    cursor is on it, so it goes on moving while the user reads Help -- which
    is exactly when it has something to say. A selected row already cycles, so
    this only changes the unselected case, and the word is still "Resume" with
    every escape stripped.
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
    if live:
        # The label alone, not the whole row: the detail beside it is ordinary
        # explanatory text and belongs at the one neutral like every other
        # unselected row's, and animating a sentence somebody is reading is
        # the thing the design rules refuse.
        return (cycle_text(line[:head_width], stream, phase, spread=0.7)
                + _dim(line[head_width:], stream))
    return line[:head_width] + _dim(line[head_width:], stream)


def _footer(stream, hints):
    glyph = _glyphs(stream)
    text = "    ".join(hints).replace("{up}", glyph["up"]).replace("{down}", glyph["down"])
    return _dim(" " + text, stream)


def _rule(stream, phase, width):
    return " " + _paint(_glyphs(stream)["rule"] * max(4, width - 1), stream, phase + 0.5, spread=1.2)


def render_startup_frame(selected=0, stream=None, model_id=None, workspace=None,
                         size=None, phase=None, resuming=False, busy=""):
    """The whole startup screen as a list of ready-to-paint lines.

    `resuming` is whether a session is waiting behind this menu, which turns
    Start into Resume. `busy` is what is still running, as a phrase for the
    note -- an empty string means nothing is, and is what removes the note and
    puts the Settings row back.
    """
    stream = sys.stdout if stream is None else stream
    columns, rows = _terminal(size)
    phase = gradient_phase() if phase is None else phase
    width = _content_width(columns)
    glyph = _glyphs(stream)
    model_id = model_id or agent_models.current_model()
    workspace = agent_config.ROOT_DIR if workspace is None else workspace
    items = menu_items(resuming=resuming, busy=bool(busy))

    label = agent_models.describe(model_id)
    if agent_models.is_overridden():
        label += "  (forced by OPENROUTER_MODEL)"
    # Measured over every label this menu can EVER show, not over the ones
    # showing now. Removing Settings shortens the longest label, so a column
    # measured from what is present would slide the whole detail column left
    # the moment work started and back again when it stopped -- a horizontal
    # reflow, on screen, triggered by a worker finishing somewhere else.
    label_width = max(display_width(item[1])
                      for item in MENU_ITEMS + (RESUME_ITEM,))

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
    if busy:
        # Above the list, dim, and not a row in it. The Settings button is
        # genuinely gone -- there is nothing there to select -- and this says
        # why, because a button that vanished without a word would read as a
        # fault rather than as a rule.
        for text in _wrap_words(BUSY_NOTE % busy, max(1, width - 1)):
            body.append(_dim(fit_to_width(" " + text, width), stream))
        body.append("")
    body.extend(
        _option_row(index == selected, item[1], item[2], stream, phase, width,
                    label_width, live=(resuming and item[0] == "start"))
        for index, item in enumerate(items)
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

# What the tip row is labelled with. It needs one, because a sentence hanging
# under the workspace with no label reads as a fact about this session rather
# than as advice about the program -- and because the label is the part that
# survives when the sentence will not fit.
TIP_LABEL = "Tip"


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


# The smallest window the session header will spend five rows on a wordmark
# in. Both halves are needed and they guard different things.
#
# The width is `render_banner`'s own threshold restated: below it that
# function falls back to a spaced word, which is a different fallback from the
# one this header wants -- it wants the date back on the same row.
#
# The height is the one that had to be chosen. The header is permanent
# scrollback and it scrolls away, so its cost is only ever on the first
# screen; but eight rows of it on a twelve-row window is most of the window
# spent before a question has been asked. Eighteen rows leaves ten for the
# session underneath, which is enough to be working in.
HEADER_LOGO_MIN_ROWS = 18


def _header_logo_fits(columns, rows):
    """Whether the session header has room to draw the block wordmark."""
    return (int(columns) >= LOGO_WIDTH + 3
            and int(rows) >= HEADER_LOGO_MIN_ROWS)


def _tip_parts(tip):
    """(gesture, detail) from whatever the caller passed, or None.

    Two shapes are accepted because two are natural: `agent_tips` hands over a
    pair, and a caller with one thing to say has one string. Anything else is
    no tip rather than an error -- this is a decoration, and a header that
    refused to draw because a tip was the wrong shape would be taking the
    session down with it.
    """
    if tip is None:
        return None
    if isinstance(tip, str):
        gesture, detail = tip, ""
    else:
        try:
            gesture, detail = tip
        except (TypeError, ValueError):
            return None
    gesture, detail = str(gesture).strip(), str(detail).strip()
    return (gesture, detail) if gesture else None


def _tip_row(tip, stream, room):
    """The tip, hanging at the header's indent, or "" when it will not fit.

    Two tiers and then silence, which is the launch screen's rule for its own
    subtitle applied to the other place TMT writes a sentence somebody is
    meant to act on: what is given up is a whole TIER, never the right-hand
    end of the words. Advice sawn through at the edge of the screen is worse
    than no advice -- the reader has to guess the rest of something they did
    not ask for -- while the gesture on its own is still true and still says
    the thing exists.

    Dim, like the date it hangs beside and for the same reason: on a screen
    whose one consequential fact is the directory TMT may write to, this is
    the bottom of the prominence ladder. It reads with every escape stripped,
    which is what the label is there for.
    """
    parts = _tip_parts(tip)
    if not parts:
        return ""
    gesture, detail = parts
    label = TIP_LABEL + " %s " % _glyphs(stream)["dot"]
    tiers = [label + gesture]
    if detail:
        tiers.insert(0, label + gesture + " " + detail)
    for text in tiers:
        if display_width(text) <= room:
            return _dim(" " * _HEADER_INDENT + text, stream)
    return ""


def _with_tip(lines, tip_line, rows):
    """The header with the tip under it, unless the window has no row spare.

    Appended AFTER the frame has been fitted to the window rather than passed
    through the fit, so the tip is the first thing given up on a short screen.
    Inside the frame it would compete with the workspace for the row the trim
    keeps, and the directory TMT may write to is not a thing to lose to a
    piece of advice.
    """
    if tip_line and len(lines) + 1 <= max(1, int(rows) - 1):
        return lines + [tip_line]
    return lines


def render_status_lines(stream=None, size=None, phase=None, moment=None,
                        provider_id=None, model_id=None, workspace=None,
                        tip=None):
    """The session header, as a list of ready-to-paint lines.

    The block wordmark, and under it the date and the workspace, each at the
    indent the prominence ladder already uses for detail belonging to the row
    above. That indent is the whole of the grouping -- one element, not three
    strings that happen to be adjacent -- and it costs nothing that a reader
    has to look past.

    A `tip` hangs at the same indent, under the workspace and dim, and is the
    one row here that is different every time the screen is drawn. It is
    passed IN rather than fetched: the rotation has to step exactly once per
    drawing of the header, and a renderer that stepped it itself would move
    the catalogue on every test that composed a frame and would put a write to
    disk inside a function whose whole job is to return strings. Its absence
    is the default, so a caller that says nothing gets the header exactly as
    it was before tips existed.

    **The wordmark is the same block the startup menu draws**, at the same
    size, through `render_banner` itself rather than a second copy of it. The
    session used to open on the word "TMT" in a single row, which is the
    smallest thing TMT ever wrote about itself and sat directly under a launch
    screen that had just filled the terminal with the same three letters.
    There are now exactly three sizes of the one wordmark and they descend in
    the order the user meets them: the launch screen doubles it, the startup
    menu and this header draw it as it is, and a terminal too narrow for
    either falls back to the plain word.

    It takes `BRAND_PHASE` and not the colour cycle, which is the difference
    between this and the menu's banner: **this is printed once, into
    scrollback, and never repainted.** A fixed phase is the rule for anything
    printed once -- a wordmark that is a different colour every launch is not
    a wordmark -- and the menu animates only because the menu is a live region
    that is being watched rather than read.

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

    # Hanging under the wordmark: what day the session opened on, and the
    # directory it may write to. The date is context, so it recedes to the one
    # neutral; the workspace is the one fact here with real consequences, so
    # it is not dimmed.
    room = max(1, width - _HEADER_INDENT)
    place = " " * _HEADER_INDENT + _shorten_middle(workspace, room, marker)
    tip_line = _tip_row(tip, stream, room)

    if _header_logo_fits(columns, rows):
        # The block wordmark, at a FIXED phase. `render_banner` is the one
        # thing that knows how to draw it -- the ASCII degradation and the
        # per-row gradient offset both live there -- so it is called rather
        # than copied.
        date = _dim(" " * _HEADER_INDENT
                    + _date_text(moment, max(1, room)), stream)
        lines = [""] + render_banner(stream, phase, columns) + [date, place]
        return _with_tip(_fit_height(lines, rows, keep_tail=1), tip_line, rows)

    # Too narrow or too short for the block. The date rejoins the wordmark on
    # one row, which is what this header was before the block arrived: on a
    # window this size the rows are worth more than the size of the letters.
    spent = display_width(" TMT" + separator)
    date = _date_text(moment, max(0, width - spent))
    brand = (" " + _paint("TMT", stream, phase, spread=BRAND_SPREAD)
             + _dim(separator + date, stream))
    return _with_tip(_fit_height(["", brand, place], rows, keep_tail=1),
                     tip_line, rows)


def prompt_caption(stream=None, width=None, moment=None, provider_id=None,
                   model_id=None, session=None, agents="", manager=None):
    """The dim line above the prompt box: when, what will answer, what it cost.

    These are the three facts that can be different from one question to the
    next -- the clock always is, and the provider and the model are read from
    the modules that own them rather than from anything captured at launch --
    so they are stated on the box rather than in the header, and re-read every
    time the box is drawn. It also means they are still on screen once the
    header has scrolled away, which on a long session is most of the time.

    The row has two ends. On the right, what the next request runs under; on
    the left, with a `session`, what this one has cost and changed so far.
    Both dim, because this is the lowest thing in the prominence ladder --
    context for the question, never the question. The right end finishes where
    the rule below it does, so the two read as one component.

    What is given up first as the terminal narrows is decided rather than
    incidental. The meter goes before any of the facts on the right, because a
    running total is the least of them; then the provider, because which model
    answers says more than whose service it is; and the clock is the last
    thing standing.
    """
    stream = sys.stdout if stream is None else stream
    width = _content_width(_terminal()[0]) if width is None else int(width)
    moment = _clock(moment)
    separator = " %s " % _glyphs(stream)["dot"]
    provider, model, _ = status_facts(provider_id, model_id, "")

    clock = moment.strftime("%H:%M:%S")
    text = clock
    # `agents` is how many background agents are running, and it is the first
    # fact on this end to be given up as the terminal narrows: an agent count
    # is a thing that is true for a while, where the clock and the model are
    # what this question is about to run under. With none running the string
    # is empty and the candidates below are exactly the three this row has
    # always had, so a session with no agents draws the row it always drew.
    groups = [(clock, provider, model), (clock, model), (clock,)]
    if agents:
        groups.insert(0, (agents, clock, provider, model))
    for parts in groups:
        text = separator.join(parts)
        if display_width(text) <= max(0, width - 1):
            break
    text = fit_to_width(text, max(1, width - 1))
    right = max(0, width - display_width(text))

    # The meter takes the left end, and only what is left over after the row's
    # right-hand end has had its room. It is dropped whole rather than cut:
    # half a token count is worse than none.
    # `is not None`, not truthiness: a Session defines __len__, so one with no
    # turns recorded yet is falsy -- which is every session for the whole of
    # its first question, exactly when the meter is wanted most.
    meter = (meter_text(session, stream, columns=right - 1, manager=manager)
             if session is not None else "")
    if meter and visible_width(meter) < right - 1:
        return " " + meter + " " * (right - visible_width(meter) - 1) + _dim(text, stream)
    return " " * right + _dim(text, stream)


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
# The meter: what the session has changed and what it has cost.
#
# It rides on the prompt box's caption, at the opposite end from the clock.
# That row is dim, is redrawn every time the box repaints, and is inside the
# flow of the screen like everything else TMT draws.
#
# It was briefly pinned to row one instead, held there by narrowing the
# terminal's scrolling region to rows two and below. That worked, and the cost
# was the whole session: lines scrolled out of a narrowed region are discarded
# rather than pushed into the terminal's scrollback, so the history stopped
# accumulating and scrolling up no longer reached it. TMT's permanent surface
# IS that scrollback -- it is the only record a finished session leaves -- and
# nothing may be bought with it, least of all a readout. A fixed corner is
# worth having; it is not worth the record of the work.


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


def _agent_totals(manager):
    """Every background agent's spend, or None when there is nothing to add.

    Guarded, because the meter is a readout: a register that cannot answer
    must cost the row its extra figures, never the row itself.
    """
    if manager is None:
        return None
    try:
        totals = manager.totals()
    except Exception:
        return None
    if not (totals.get("tokens") or totals.get("lines_added")
            or totals.get("lines_removed") or totals.get("agents")):
        return None
    return totals


def meter_text(session, stream=None, columns=None, manager=None):
    """The corner readout for a session, painted, or "" when it says nothing.

    Green for what arrived and red for what left -- the two ends of the one
    gradient, used for exactly what they already mean there. Everything else
    is the one neutral, because the counts are the message and the colour only
    confirms them: with the escapes stripped this still reads
    "+1231 lines, -123 lines, ~15k context, 30k out".

    "Context" is what the request in flight carries, not what the session has
    spent. The two were the same figure under the first one's name, so a
    question answered in three steps read as a context that had tripled --
    the same prompt sent three times, counted three times, and reported as
    growth. The spend is still kept; it is `/context` that states it, where
    there is room to say which number is which.

    The tilde is not decoration. What goes out is estimated -- no provider
    will count a request before it is sent -- and tokens generated are the
    provider's own figure whenever it gives one. A number that was guessed is
    marked as guessed.
    """
    stream = sys.stdout if stream is None else stream
    if session is None:
        return ""
    # Lines a background agent wrote are lines this session wrote. The user
    # asked for one file and got one file; which thread held the pen is an
    # implementation detail of how TMT went about it, and a meter that
    # reported +0 while five workers rewrote the project would be telling the
    # truth about the main thread and a lie about the session.
    #
    # Tokens are kept apart, and that is a different judgement rather than an
    # inconsistency. `context` is how full the window of the request in
    # flight is -- one request, on one thread -- and adding five workers'
    # spend into it would describe a context that does not exist. What the
    # agents cost is real and is reported, next to how many are running,
    # where it reads as their spend rather than as the session's context.
    agents = _agent_totals(manager)
    lines_added = session.lines_added + (agents["lines_added"] if agents else 0)
    lines_removed = session.lines_removed + (agents["lines_removed"] if agents else 0)
    added, removed, sent, back = _meter_counts(
        lines_added, lines_removed, session.tokens_in, session.tokens_out)
    if not (lines_added or lines_removed
            or session.tokens_in or session.tokens_out):
        return ""
    columns = _terminal()[0] if columns is None else int(columns)
    # Marked as an estimate while a reply is still arriving, as well as when
    # the provider never gave a figure: part of what is on screen is then a
    # guess about text that has not finished being generated.
    out_mark = "" if (session.tokens_out_exact and not session.streaming) else "~"
    # What the background agents cost, as its own group. Marked `~` whenever
    # any figure inside it was estimated rather than reported, because a total
    # that mixes one measured number with one guessed number is a guess.
    spend = ""
    if agents and agents["tokens"]:
        # "agents" leads, because the figure trails it in both forms and
        # "22k agent" -- the other way round, shortened -- reads as a count of
        # agents rather than as what they cost.
        spend = "agents %s%s" % ("" if agents["exact"] else "~",
                                 _short_count(agents["tokens"]))
    full = "%s lines, %s lines, ~%s context, %s%s out" % (
        added, removed, sent, out_mark, back)
    short = "%s %s  ~%s ctx  %s%s out" % (added, removed, sent, out_mark, back)
    tiny = "%s %s" % (added, removed)
    if spend:
        full += ", %s tokens" % spend
        short += "  %s" % spend
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

    def __init__(self, rows=0, height=None):
        self.rows = max(0, int(rows))
        # The window height these rows were counted against. Taken on the
        # first paint rather than here, because a pad is built from
        # `opening_pad` and both are answered from the same terminal, so the
        # first question is the honest place to record which window it was.
        self.height = int(height) if height else None

    def above(self, height, size=None):
        """Blank rows to draw above a region `height` rows tall.

        A taller region -- the relay's, which carries the reply and the status
        row as well as the box -- needs fewer of them to keep its bottom edge
        in the same place. Clamped to the window, so a terminal made shorter
        mid-session cannot ask for a region taller than it is.

        Answered against the window as it is now, not as it was when the pad
        was worked out. `self.rows` counts the distance from the cursor to the
        foot, and resizing the window moves the foot: a window made taller
        opens rows below the box that nothing was holding, and the box was
        left stranded up the screen until enough output had been printed to
        spend what was left. The difference in height is the difference in
        that distance, so it is simply added.
        """
        rows = _terminal(size)[1]
        if self.height is None:
            self.height = rows
        room = max(0, rows - 1 - int(height))
        slack = self.rows + PROMPT_HEIGHT + (rows - self.height)
        return max(0, min(slack - int(height), room))

    def reset(self, rows=0, height=None):
        """Count the pad again, because the screen has been cleared under it.

        The one thing that invalidates a pad without the window changing size:
        `/back` draws the startup menu over the session and comes back to a
        cleared screen with a fresh header on it, so the distance from the
        cursor to the foot is a different number and every row this had spent
        is spent no longer. Reset IN PLACE rather than rebound, because the
        prompt box was handed this object when the session opened and a new
        one here would leave the box holding the old count -- the same rule
        every state object in TMT follows for the same reason.
        """
        self.rows = max(0, int(rows))
        self.height = int(height) if height else None
        return self.rows

    def take(self, lines):
        """Give up `lines` rows to something printed permanently."""
        self.rows = max(0, self.rows - max(0, int(lines)))
        return self.rows

    def spend(self, text):
        """Give up a row per line of `text`. Returns what is left.

        A row per newline, and one more when the text does not end in one:
        the writer supplies the missing newline, so that text still lands on
        a row of its own and still costs the pad one.
        """
        text = str(text or "")
        if not text:
            return self.rows
        return self.take(text.count("\n") + (0 if text.endswith("\n") else 1))


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


def render_command(result, stream=None, size=None):
    """Draw a slash command's answer into the terminal's scrollback.

    Permanent, like everything else that outlives the turn it happened in: the
    user asked what the model is or cleared the conversation, and scrolling
    back to find out what they were told is the whole point of the record.
    Nothing here repaints.

    The title takes the wordmark's fixed gradient, so a command's answer reads
    as TMT speaking rather than as a result the agent produced -- it is the
    program answering about itself. The rows are the startup screen's field
    rows, label dim and value plain, because they are the same kind of thing:
    settled facts in two columns. A failed command's title takes the error
    position on the gradient, and says so in words as well, because colour is
    never the message.

    Returns the number of rows it drew, or False when the stream has gone.
    The count is what the caller spends from the pad holding the prompt box at
    the foot of the window.
    """
    stream = sys.stdout if stream is None else stream
    width = _content_width(_terminal(size)[0])
    lines = ["", " " + (_color(result.title, 10, stream) if not result.ok
                        else _paint(result.title, stream, BRAND_PHASE,
                                    spread=BRAND_SPREAD))]
    label_width = max([len(row[0]) for row in result.rows
                       if isinstance(row, tuple)] or [0]) + 2
    for row in result.rows:
        if isinstance(row, tuple):
            lines.append(_field(row[0], row[1], stream, width,
                                name_width=max(10, label_width)))
        else:
            lines.append(fit_to_width("   " + str(row), width) if str(row).strip()
                         else "")
    if result.note:
        lines.append(_dim(fit_to_width(" " + result.note, width), stream))
    if not safe_write(stream, "\n".join(lines) + "\n"):
        return False
    return len(lines)


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


def auto_update_text():
    """"ON" or "OFF" for the launch updater, read from disk each time.

    Read rather than cached so the row is right after a toggle without
    anything having to invalidate anything, and guarded to the default so a
    settings file that cannot be read draws a menu rather than stopping one.
    """
    try:
        return AUTO_UPDATE_LABELS[bool(agent_config.read_saved_auto_update())]
    except Exception:
        return AUTO_UPDATE_LABELS[bool(agent_config.DEFAULT_AUTO_UPDATE)]


def toggle_auto_update():
    """Flip the setting and return what it now says. Never raises.

    A failed write is reported by leaving the row where it was rather than by
    stopping the menu: the user is standing in Settings and the honest signal
    that nothing happened is that nothing changed on the row they are looking
    at.
    """
    try:
        agent_config.set_auto_update(not agent_config.read_saved_auto_update())
    except Exception:
        pass
    return auto_update_text()


def project_context_text():
    """"ON" or "OFF" for the project context, read from disk each time.

    The same shape as `auto_update_text` and for the same two reasons: read
    rather than cached so the row is right immediately after a toggle without
    anything having to invalidate anything, and guarded to the default so a
    settings file that cannot be read draws a menu rather than stopping one.
    """
    try:
        return PROJECT_CONTEXT_LABELS[
            bool(agent_config.read_saved_project_context())]
    except Exception:
        return PROJECT_CONTEXT_LABELS[
            bool(agent_config.DEFAULT_PROJECT_CONTEXT)]


def toggle_project_context():
    """Flip the setting and return what it now says. Never raises.

    A failed write is reported by leaving the row where it was rather than by
    stopping the menu, exactly as `toggle_auto_update` does: the user is
    standing in Settings and the honest signal that nothing happened is that
    nothing changed on the row they are looking at.

    Turning it off deletes nothing. Any TMT_Context directory already written
    belongs to the project and to whoever wrote it, and a setting is not
    consent to remove somebody's notes.
    """
    try:
        agent_config.set_project_context(
            not agent_config.read_saved_project_context())
    except Exception:
        pass
    return project_context_text()


def _settings_suffix(entry):
    """The value drawn on the right of a Settings row, or "" for a screen.

    A lookup rather than a chain of conditionals because there are two
    switches now and there will be a third: the row that opens a screen has no
    value to state, and the row that IS a value states it here. Both switches
    read their setting from disk per frame, so this is also what makes a
    toggle show its new state on the very next pass of `_drive`.
    """
    if entry == "autoupdate":
        return "  " + auto_update_text()
    if entry == "projectcontext":
        return "  " + project_context_text()
    return ""


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
        # The toggle carries its state on its OWN row, in the slot the model
        # picker uses to mark the active model, and deliberately not in the
        # field block above. The three fields up there each summarise a screen
        # you have to go into to see the value; this one has no screen, so its
        # value belongs where the thing you press is -- and stating it twice
        # would make a reader look in two places to find out what Enter does.
        _option_row(index == selected, item[1], item[2], stream, phase, width,
                    label_width,
                    suffix=_settings_suffix(item[0]))
        for index, item in enumerate(SETTINGS_ITEMS)
    )
    lines.append("")
    lines.append(_footer(stream, ("{up}/{down} Navigate", "Enter Select", "Esc Back")))
    return _fit_height(lines, rows, keep_tail=1)


def render_danger_frame(selected=0, stream=None, size=None, phase=None):
    """The Danger Zone: what is in here, and what it costs.

    The heading says what the section is for before the rows say what it does,
    because a user who arrived by pressing Enter on the wrong row has to be
    able to read their way out. Esc is on the footer as it is everywhere else,
    and Back is a row of its own as well -- the way out is never only a key
    somebody has to know.
    """
    stream = sys.stdout if stream is None else stream
    columns, rows = _terminal(size)
    phase = gradient_phase() if phase is None else phase
    width = _content_width(columns)
    label_width = max(display_width(item[1]) for item in DANGER_ITEMS)

    lines = ["", " " + _paint("Danger Zone", stream, phase), ""]
    for text in _wrap_words(
            "Nothing in here can be undone. TMT's own files go, and so does "
            "the tmtcode command; anything git ignores stays, which is your "
            "notes and TMT's saved key.", max(10, width - 1)):
        lines.append(_dim(" " + text, stream))
    lines.append(_rule(stream, phase, width))
    lines.extend(
        _option_row(index == selected, item[1], item[2], stream, phase, width,
                    label_width)
        for index, item in enumerate(DANGER_ITEMS)
    )
    lines.append("")
    lines.append(_footer(stream, ("{up}/{down} Navigate", "Enter Select", "Esc Back")))
    return _fit_height(lines, rows, keep_tail=1)


def render_uninstall_frame(plan, typed=0, message="", stream=None, size=None,
                           phase=None, report=None):
    """What an uninstall would remove, and the word that starts it.

    The plan is drawn as counted facts -- how many files go, how many stay,
    which commands are there to remove -- because the alternative is a
    sentence promising something, and this is the screen where a promise and
    what actually happens have to be the same list. `report` swaps it for
    what happened, which is the same shape read back.
    """
    stream = sys.stdout if stream is None else stream
    columns, rows = _terminal(size)
    phase = gradient_phase() if phase is None else phase
    width = _content_width(columns)
    heading = "Uninstall TMT" if report is None else "TMT has been removed"
    lines = ["", " " + _paint(heading, stream, phase), ""]

    if report is not None:
        for row in report.lines():
            for text in _wrap_words(row, max(10, width - 1)):
                lines.append(fit_to_width(" " + text, width))
        lines.append("")
        lines.append(_footer(stream, ("Enter Close TMT",)))
        return _fit_height(lines, rows, keep_tail=1)

    lines.append(_field("Removing", str(plan.root), stream, width, name_width=11))
    lines.append(_rule(stream, phase, width))
    if plan.refusal:
        for text in _wrap_words(plan.refusal, max(10, width - 1)):
            lines.append(fit_to_width(" " + text, width))
        lines.append("")
        lines.append(_footer(stream, ("Esc Back",)))
        return _fit_height(lines, rows, keep_tail=1)

    lines.append(_field("Removes", "%d file(s) TMT installed" % len(plan.tracked),
                        stream, width, name_width=11))
    lines.append(_field("Keeps", "%d file(s) git ignores" % len(plan.kept),
                        stream, width, name_width=11))
    for label, _argv in plan.commands:
        lines.append(_field("Removes", label, stream, width, name_width=11))
    lines.append("")
    for note in plan.notes:
        for text in _wrap_words(note, max(10, width - 1)):
            lines.append(_dim(" " + text, stream))
    lines.append("")
    # The mask is the api-key screen's: one mark per character, built from how
    # much was typed rather than from what. Here it is not secrecy -- it is
    # that the row must not become somewhere a half-typed word looks finished.
    marks = _glyphs(stream)["dot"] * typed
    # Three tiers, measured on the plain text and painted afterwards -- the
    # word carries the gradient, so the row cannot be fitted once it is built.
    # It gives up the instruction before it gives up the word: somebody who
    # can see UNINSTALL and a caret can work out what to do with them, where
    # "Type UNINST" is a screen that has told them nothing.
    for template in ("Type %s and press Enter:  ", "Type %s:  ", "%s:  "):
        if display_width(" " + (template % UNINSTALL_WORD) + marks) <= width:
            lines.append(" " + (template % _paint(UNINSTALL_WORD, stream, phase))
                         + marks)
            break
    else:
        lines.append(fit_to_width(" " + UNINSTALL_WORD, width))
    if message:
        lines.append(_dim(" " + fit_to_width(message, max(1, width - 1)), stream))
    lines.append("")
    lines.append(_footer(stream, ("Esc Back",)))
    return _fit_height(lines, rows, keep_tail=1)


def uninstall_screen(stream=None, key_reader=None, region=None, module=None):
    """Ask for the word, and remove TMT when it is typed. True when it was.

    The plan is built once, before the question, and it is what the frame
    shows AND what `execute` is handed -- so a user cannot agree to one thing
    and get another. Esc anywhere before the word returns having done nothing.

    Once it has run, the only key that does anything is Enter, and what it
    does is leave: the program has just deleted itself, so there is nowhere
    for this screen to go back to.
    """
    stream = sys.stdout if stream is None else stream
    if key_reader is None:
        if not is_interactive(stream):
            return False
        key_reader = _default_text_reader()
    region = LiveRegion(stream) if region is None else region
    if module is None:
        try:
            import agent_uninstall as module
        except Exception:
            # A module that cannot be imported must not take the screen down
            # with it -- the same rule every lazily-imported capability here
            # follows. There is simply nothing to offer.
            return False

    plan = module.plan()
    typed, message, report = [], "", None
    while True:
        region.paint(render_uninstall_frame(plan, len(typed), message, stream,
                                            report=report))
        kind, value = _next_text_key(key_reader)
        if kind == "end":
            return report is not None
        if report is not None:
            # Anything at all closes it. There is no state left to protect and
            # no screen behind this one that still exists.
            if kind == "key" and value in ("enter", "esc", "interrupt", "eof"):
                return True
            continue
        if kind == "char":
            typed.extend(iter_graphemes(value))
            message = ""
            continue
        if kind != "key":
            continue
        if value in ("esc", "interrupt"):
            return False
        if value == "backspace":
            if typed:
                typed.pop()
            message = ""
            continue
        if value == "enter":
            if "".join(typed).strip().upper() != UNINSTALL_WORD:
                typed, message = [], (
                    "That is not the word. Type %s exactly, or press Esc to "
                    "leave TMT where it is." % UNINSTALL_WORD)
                continue
            if not plan.possible:
                typed, message = [], (
                    plan.refusal or "There is nothing here for TMT to remove.")
                continue
            # Painted before it starts, because removing a few hundred files
            # and running two package managers is seconds rather than an
            # instant, and a screen that sat still through it would read as a
            # program that had stopped.
            region.paint(render_uninstall_frame(plan, len(typed),
                                                "Removing TMT...", stream))
            report = module.execute(plan)
            typed, message = [], ""
    return report is not None


def danger_screen(stream=None, key_reader=None, region=None, text_reader=None):
    """The Danger Zone. True when TMT was uninstalled, False otherwise."""
    stream = sys.stdout if stream is None else stream
    if key_reader is None:
        if not is_interactive(stream):
            return False
        key_reader = _default_reader()
    if text_reader is None:
        text_reader = key_reader
    region = LiveRegion(stream) if region is None else region
    # Back, not Uninstall. The cursor never starts on the row that ends the
    # program: an Enter carried in from the screen before must land on the way
    # out rather than on the way through.
    state = {"selected": len(DANGER_ITEMS) - 1, "done": False}

    def render():
        return render_danger_frame(state["selected"], stream)

    def on_key(key):
        if key in (None, "esc", "quit", "interrupt"):
            return "done"
        if key == "up":
            state["selected"] = (state["selected"] - 1) % len(DANGER_ITEMS)
        elif key == "down":
            state["selected"] = (state["selected"] + 1) % len(DANGER_ITEMS)
        elif key == "enter":
            entry = DANGER_ITEMS[state["selected"]][0]
            if entry == "back":
                return "done"
            if entry == "uninstall":
                if uninstall_screen(stream=stream, key_reader=text_reader,
                                    region=region):
                    state["done"] = True
                    return "done"
        return None

    _drive(render, key_reader, region, on_key)
    return state["done"]


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
    # Tab completes a slash command and does nothing else. It reached here
    # before and came back as ("", "") -- a tab is not printable, so it fell
    # through to the ignored branch -- which is why it is free to take.
    "\t": "tab", "tab": "tab",
    "\x15": "clear",          # Ctrl-U, as it is in every shell
    "\x17": "delete_word",    # Ctrl-W
    "\x04": "eof", "eof": "eof",   # Ctrl-D
}

# Names a scripted reader may send into a text field that are not text and
# have nothing to do here. They are ignored rather than typed.
_TEXT_IGNORED = ("up", "down")


def normalize_text_key(key, allow_multiline=False):
    """One keystroke for a text field, as (kind, value).

    ("end", None) when the input has ended, ("key", name) for the keys that
    edit rather than type, ("char", text) for characters, and ("", "") for a
    tick or anything unprintable. A multi-character run is accepted whole, so
    a paste that arrives in one read is not split or dropped.

    `allow_multiline` decides what happens to a run with a line break in it.
    Off, the whole paste is dropped, because `str.isprintable` is false for
    any string containing a newline -- which is why pasting a block into the
    API key field has always done nothing, and is right there: a key has no
    line breaks and half of one would be worse than none.

    On, the run is taken whole, newlines and all. The task box wants it: a
    pasted error message or file is exactly the thing worth asking about, and
    dropping it silently is the least helpful answer available. A single
    newline is still Enter -- that is _TEXT_KEYS, checked first -- so this
    only ever applies to a run the terminal delivered in one read.
    """
    if key is None:
        return ("end", None)
    if key in _TEXT_KEYS:
        return ("key", _TEXT_KEYS[key])
    if key in _TEXT_IGNORED:
        return ("", "")
    if key and key.isprintable():
        return ("char", key)
    if allow_multiline and key and len(key) > 1:
        # Printable once the breaks and tabs it is allowed to carry are set
        # aside. Anything else in there is a control sequence, not a paste.
        #
        # A BARE carriage return counts as a break here. The console reports
        # the Enter inside a pasted block as "\r" and nothing else, so a run
        # that only knew about "\r\n" found an unprintable character in every
        # Windows paste and threw the whole block away.
        body = key.replace("\r\n", "\n").replace("\r", "\n")
        if body.replace("\n", "").replace("\t", "").isprintable():
            return ("char", body)
    return ("", "")


# The keys the console reports as a scan code behind a lead byte. They have no
# character of their own, so a name is the only thing either caller can act on.
_WINDOWS_EXTENDED = {"H": "up", "P": "down", "K": "left", "M": "right",
                     "G": "home", "O": "end", "S": "delete"}


# ---------------------------------------------------------------------------
# Bursts, and why a paste is not a keystroke.
#
# A terminal does not announce a paste. It delivers the characters the paste
# is made of, as fast as the program will take them, through exactly the call
# that delivers a typed character -- `msvcrt.getwch` here, one byte off the
# descriptor there. So nothing above this layer had ever seen a paste at all:
# it saw somebody typing very quickly, and the carriage return in the middle
# of a pasted block was read as Enter and submitted the line. A block of six
# lines was six tasks, each run against a workspace the one before it had
# already changed.
#
# The fix is where the misreading is: whatever is ALREADY waiting behind a
# character is read out and handed up as one read. Only what is already
# buffered is taken -- `kbhit`, or a select with no timeout, never a wait --
# so this cannot glue two keystrokes a human made into one paste. It would
# need them to arrive with no measurable gap between them.

# A ceiling on one coalesced read. Nothing is lost when it is reached: the
# rest of the paste is still on the console and arrives as the next burst.
# It exists so a console that reports input as always-ready cannot hold the
# reader inside one call forever.
_PASTE_DRAIN_MAX = 100000

# How long a burst already in progress waits for the rest of itself to arrive.
# Spent only after two characters have arrived with no gap between them, so a
# keystroke never pays it.
_PASTE_GRACE = 0.02

# Keystrokes read out while draining and not part of the burst. Draining is
# reading, so a Ctrl-C or an arrow key sitting behind a paste has already been
# taken off the console by the time it is recognised as not belonging to it.
# Queueing it here delivers it, in order, on the next read instead of
# dropping it.
_pending_keys = []

# One key from a run of pushed-back input: a full escape sequence where there
# is one, otherwise a single character.
_ESCAPE_RUN = re.compile(r"\x1b\[[0-9;]*[~A-Za-z]|\x1bO[A-Za-z]|\x1b|.", re.S)


def _paste_safe(char):
    """Whether `char` can be part of a pasted run rather than ending it."""
    return char in ("\r", "\n", "\t") or char.isprintable()


def _split_run(run):
    """A coalesced burst, as (what to deliver now, what to deliver next).

    The drain stops at the first character a paste cannot contain, and that
    character has already been read, so it is handed back rather than lost.
    At least one character is always delivered: a run that begins with a
    control character IS that control character, and returning nothing would
    lose the keystroke the caller is waiting on.
    """
    for index, char in enumerate(run):
        if not _paste_safe(char):
            index = index or 1
            return run[:index], _ESCAPE_RUN.findall(run[index:])
    return run, []


def _drain_burst(next_char, first, grace=_PASTE_GRACE, sleep=time.sleep):
    """Coalesce whatever is already waiting behind `first` into one read.

    `next_char` returns the next character with no waiting, "" when nothing
    is pending, and a key NAME (more than one character) for a key that has
    no character of its own. A name ends the run and is queued, because it is
    a keystroke rather than part of the paste.

    The grace is spent only ONCE A BURST IS ALREADY ESTABLISHED -- two
    characters with no gap at all between them, which is not something a
    person does. A single keystroke never waits for anything, so nothing here
    is felt while typing. What it buys is the boundary: a large paste does not
    always arrive in the console buffer in one piece, and without the pause
    the tail of it comes back as a second burst and goes into the field as a
    second paste. That is the same misreading as before, only smaller.
    """
    run, queued, total, waited = [first], [], len(first), False
    while total < _PASTE_DRAIN_MAX:
        char = next_char()
        if not char:
            if len(run) < 2 or waited or grace <= 0:
                break
            waited = True
            sleep(grace)
            continue
        waited = False
        if len(char) > 1:
            queued.append(char)
            break
        run.append(char)
        total += 1
        if not _paste_safe(char):
            break
    delivered, rest = _split_run("".join(run))
    _pending_keys.extend(rest)
    _pending_keys.extend(queued)
    return delivered


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
    if not raw:
        # The menu reads one key at a time and has no field to paste into.
        return normalize_key(char)

    def more():
        if not msvcrt.kbhit():
            return ""
        following = msvcrt.getwch()
        if following in ("\x00", "\xe0"):
            return _WINDOWS_EXTENDED.get(msvcrt.getwch(), "")
        return following

    return _drain_burst(more, char)


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
        #
        # One CHARACTER, which is not one byte. A byte at a time decoded on
        # its own turns every accented letter and every box-drawing character
        # of a pasted block into a replacement character, so the continuation
        # bytes the lead byte promises are read out with it.
        data = os.read(fd, 1)
        if not data:
            return ""
        lead = data[0] if isinstance(data[0], int) else ord(data[0])
        extra = 0
        if 0xF0 <= lead:
            extra = 3
        elif 0xE0 <= lead:
            extra = 2
        elif 0xC0 <= lead:
            extra = 1
        while extra and pending(0.05):
            data += os.read(fd, 1)
            extra -= 1
        return data.decode("utf-8", "replace")

    def escape_sequence():
        # An escape on its own is Esc; an escape with more behind it is an
        # arrow. Only pending bytes are read, so Esc never waits long.
        if not pending(0.05):
            return "\x1b"
        sequence = "\x1b" + take()
        if sequence[-1] in "[O":
            sequence += take()
            # Delete, Home and End arrive as ESC [ number ~. The parameter
            # bytes are read out here so the terminating '~' is not left
            # behind to be read next as a printable character and typed.
            while (sequence[-1].isdigit() or sequence[-1] == ";") and pending(0.05):
                sequence += take()
        return sequence

    def more():
        """The next character already waiting, for the burst drain."""
        if not pending(0):
            return ""
        char = take()
        if char != "\x1b":
            return char
        # Read the whole sequence out rather than leaving its tail on the
        # descriptor to be typed as "[D" a moment later. It comes back longer
        # than one character, which is how the drain knows it is a keystroke
        # rather than part of the paste.
        sequence = escape_sequence()
        return sequence if len(sequence) > 1 else "\x1b"

    _saved_termios = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        if timeout is not None and not pending(timeout):
            return ""
        char = take()
        if not char:
            return None
        if char != "\x1b":
            if not raw:
                return normalize_key(char)
            return _drain_burst(more, char)
        sequence = escape_sequence()
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
    if _pending_keys:
        # Read out behind a paste and queued rather than dropped. It is
        # answered before the console is asked for anything new, so the
        # keystroke keeps the place in the order the user made it in.
        key = _pending_keys.pop(0)
        return key if raw else normalize_key(key)
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


def _next_text_key(key_reader, allow_multiline=False):
    """One keystroke from the reader, read as text rather than as a menu key."""
    try:
        return normalize_text_key(key_reader(), allow_multiline=allow_multiline)
    except (StopIteration, IndexError):
        return ("end", None)
    except KeyboardInterrupt:
        return ("key", "interrupt")


def _next_raw_key(key_reader):
    """One keystroke, exactly as the reader gave it.

    The seam the panel opens on. A keystroke has to be looked at twice now --
    once to ask whether the agents panel wants it, and once as text -- and
    `normalize_text_key` throws away the difference between the arrow keys and
    a tick, which is the difference the panel is asking about. So the raw
    stroke is taken here and each caller normalises it for itself.

    An exhausted reader comes back as None, which `normalize_text_key` already
    reads as the end of the input, and a KeyboardInterrupt comes back as the
    character the terminal would have sent for it. Both keep the caller's
    behaviour exactly what `_next_text_key` gave it.
    """
    try:
        return key_reader()
    except (StopIteration, IndexError):
        return None
    except KeyboardInterrupt:
        return "\x03"


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


# How tall the input may grow before it scrolls instead. Five rows is enough
# to see a short paragraph whole; past that the box would be eating the
# conversation it sits under, which is the thing the screen is actually for.
INPUT_MAX_ROWS = 5

# A paste of more than this many lines is folded to a token rather than typed
# into the field. One, so the rule is simply: if it is a line, it goes in as a
# line; if it is a block, it goes in as a token.
#
# It used to be a word count, and the word count was the wrong measurement.
# Length is not what makes a paste unreadable in a one-line field -- shape is.
# A four-hundred-character URL is still one line and the field wraps it, shows
# it and scrolls it perfectly well; a six-line traceback is six rows of a box
# five rows tall whether it is fifteen words or five hundred, and it is the
# one the user pasted to ask about rather than to read.
PASTE_LINE_THRESHOLD = 1

# What a folded paste looks like in the field. It states the size of what it
# stands for, because a placeholder that hid the amount would be worse than
# the wrapped text it replaced.
_PASTE_TOKEN = "[Pasted text #%d %s]"
_PASTE_PATTERN = re.compile(r"\[Pasted text #(\d+) [^\]]*\]")


def normalize_paste(text):
    """A pasted block as the field should hold it.

    Two things, both about line endings and neither of them cosmetic.

    The breaks are made uniform first. A console hands back a bare carriage
    return for the Enter inside a pasted block, a file dragged from Windows
    carries CRLF, and a heredoc carries LF; counting lines has to see all
    three the same way or the same paste folds or does not fold depending on
    where it was copied from.

    Then the trailing ones go. Selecting a line in an editor takes the newline
    at the end of it with you, and that one invisible character is the whole
    difference between a line and a block. It would fold a single line to a
    token that said "+2 lines", one of which is empty -- and a paste is text
    to put in the field, never an instruction to submit it.
    """
    return str(text).replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def paste_lines(text):
    """How many lines a pasted block occupies once its breaks are settled."""
    return normalize_paste(text).count("\n") + 1


def _paste_summary(text):
    """How much was pasted. Lines, because lines are what folded it."""
    return "+%d lines" % paste_lines(text)


def layout_field(text, inner):
    """Every visual row of `text`, as (start index, row text).

    The field wraps now rather than scrolling sideways. A long task used to
    run off the right-hand edge on one line, which made it unreadable while it
    was being written and made the terminal redraw the whole row for every
    keystroke -- the lag was the horizontal scroll, not the length.

    Measured in columns, never characters: a wide character occupies two, and
    breaking on a character count puts half of one past the edge and wraps the
    row a second time behind the arithmetic's back.

    One column is left for the caret, which sits after the last character
    rather than on it.
    """
    usable = max(1, int(inner) - 1)
    rows, start, used, index = [], 0, 0, 0
    for grapheme in iter_graphemes(text):
        if "\n" in grapheme:
            # An explicit break from a pasted block. It ends the row wherever
            # the row had got to.
            rows.append((start, text[start:index]))
            index += len(grapheme)
            start, used = index, 0
            continue
        step = display_width(grapheme)
        if used and used + step > usable:
            rows.append((start, text[start:index]))
            start, used = index, 0
        used += step
        index += len(grapheme)
    rows.append((start, text[start:index]))
    return rows


def caret_in(rows, text, cursor):
    """Which row the caret is on, and how many columns into it.

    Searched from the bottom so a cursor sitting exactly on a row boundary
    belongs to the row it is about to type into, not the one it just left.
    """
    for index in range(len(rows) - 1, -1, -1):
        start, _ = rows[index]
        if cursor >= start:
            return index, display_width(text[start:cursor])
    return 0, 0


def visible_field_rows(text, cursor, inner, max_rows=INPUT_MAX_ROWS):
    """The rows to draw as (start index, row text), and where the caret is.

    Everything `visible_field` returns, except that each row keeps the offset
    into `text` that `layout_field` worked out for it. That offset is what
    anything painting INSIDE the field needs: the field wraps at a column and
    not at a word, so a row is an arbitrary slice of the line and a token
    found by searching the row alone could be half of a longer one that
    happens to have wrapped. Matching against the whole line and mapping the
    answers back onto rows is the only way to be right about that.

    The window arithmetic lives here and only here, with `visible_field` a
    view onto it, because two copies of "which rows are on screen" would be
    two things to keep agreeing with the caret.
    """
    rows = layout_field(text, inner)
    caret_row, caret_column = caret_in(rows, text, cursor)
    max_rows = max(1, int(max_rows))
    top = 0
    if caret_row >= max_rows:
        top = caret_row - max_rows + 1
    top = min(top, max(0, len(rows) - max_rows))
    shown = rows[top:top + max_rows]
    return (shown, caret_row - top, caret_column,
            top, max(0, len(rows) - top - len(shown)))


def visible_field(text, cursor, inner, max_rows=INPUT_MAX_ROWS):
    """The rows to draw, and where the caret is among them.

    Returns (rows, caret row, caret column, hidden above, hidden below). Past
    `max_rows` the field scrolls: the window follows the caret, because the
    one row that must always be on screen is the one being typed into.
    """
    shown, caret_row, caret_column, above, below = visible_field_rows(
        text, cursor, inner, max_rows)
    return ([body for _, body in shown], caret_row, caret_column, above, below)


# Where the capability commands sit on the gradient, and how far across one
# token it travels. Red at the slash, through orange, to green at the last
# character -- the one thing on this surface that carries the gradient, and it
# carries the whole of it because what it marks is a switch the user has just
# turned on.
#
# A FIXED phase, and that is not a preference either. The prompt box repaints
# only when its composed frame differs from the last one, which is what stops
# the caret walking to the foot of the box and back twelve times a second
# while somebody sits and thinks. `gradient_phase()` reads the clock, so an
# animated token would make every frame different from the last and put that
# flicker straight back -- for a surface that is being typed into, which is
# the one place it is least tolerable. Fixed, the token is the same colour in
# every frame, two frames of an untouched box stay byte-identical, and the
# repaint is skipped exactly as it was before this existed.
CAPABILITY_PHASE = 0.0
CAPABILITY_SPREAD = 0.5


def capability_spans(text):
    """Every capability command in the text, as (start, end, name).

    A thin pass-through to `agent_capabilities.spans`, which is the one parser
    for these commands: what the box paints and what the runtime authorises
    are then the same question asked once. Guarded to nothing, because an
    editable install freezes its module list and a module the entry point
    cannot see must cost the user a colour rather than the prompt.
    """
    try:
        import agent_capabilities
        return agent_capabilities.spans(text)
    except Exception:
        return ()


def paint_capabilities(body, start, spans, stream):
    """One field row with its capability commands picked out.

    `start` is where `body` begins in the line, so a span found against the
    whole line lands on the right characters here even though the field wraps
    mid-word.

    Painted AFTER the row has been fitted, never before. `fit_to_width`
    measures with `display_width`, which counts the bytes of an escape
    sequence as columns, so a row styled first would be trimmed through the
    middle of an escape and leave half of one on the screen -- the rule
    `agent_panel._row` states in its own docstring. Fitting only ever removes
    a suffix, so a span past the end of what survived is simply dropped.

    Three ways out, in order. With colour, the gradient. With ANSI but no
    colour -- NO_COLOR on a real terminal -- weight and a rule, so the token
    is still marked as something other than prose. With neither, which is
    every piped, redirected and scripted run, the text exactly as it was: the
    row still reads `/plan`, which is the command spelled out, so nothing has
    been said in colour that was not also said in words.
    """
    if not spans or not body:
        return body
    coloured = _supports_color(stream)
    ansi = bool(getattr(stream, "isatty", lambda: False)())
    if not coloured and not ansi:
        return body
    end = start + len(body)
    out, cut = [], 0
    for begin, finish, _ in spans:
        if finish <= start or begin >= end:
            continue
        # Clipped to the row, so a command split across two visual rows is
        # painted on both of them rather than on neither.
        here, there = max(begin - start, 0), min(finish - start, len(body))
        if there <= cut:
            continue
        here = max(here, cut)
        out.append(body[cut:here])
        token = body[here:there]
        if coloured:
            out.append(cycle_text(token, stream, CAPABILITY_PHASE,
                                  spread=CAPABILITY_SPREAD))
        else:
            out.append(BOLD + UNDERLINE + token + RESET)
        cut = there
    out.append(body[cut:])
    return "".join(out)


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
        # Pasted blocks, kept whole, in the order they were pasted. `value`
        # holds a token where each one went, so the field stays readable and
        # the text is not lost -- `expanded()` puts them back.
        self.pastes = []

    def insert(self, text, pasted=False):
        """Insert text at the cursor, and dismiss the placeholder.

        Whole rather than per character: a paste arrives as one read, and
        splitting it would let anything applied per keystroke -- a limit, a
        repaint, a cursor move -- land in the middle of it.

        A paste of more than one line is folded to a token instead of being
        typed in. The field is one row that may grow to five, so a block put
        into it verbatim either fills the box or scrolls most of itself out of
        sight, and every break in it is a row the conversation underneath does
        not get. A single line is left exactly as it was pasted, however long
        it is: the field wraps and scrolls, and text you can read has no
        business being hidden behind a placeholder.

        The text is kept exactly and put back on submit; nothing is truncated
        and nothing is retyped.
        """
        if not text:
            return
        if pasted:
            text = normalize_paste(text)
            if not text:
                return
        # Same keystroke, not the next one: the placeholder stops being drawn
        # at the moment the first character exists, so there is no frame in
        # which both are on screen and none in which they are concatenated.
        self.placeholder_visible = False
        if pasted and paste_lines(text) > PASTE_LINE_THRESHOLD:
            self.pastes.append(text)
            text = _PASTE_TOKEN % (len(self.pastes), _paste_summary(text))
        self.value = self.value[:self.cursor] + text + self.value[self.cursor:]
        self.cursor += len(text)

    def expanded(self):
        """The line as the user meant it, with every folded paste put back.

        A token whose paste is missing is left exactly as it stands. It was
        typed by hand or edited past recognition, and inventing text for it
        would put words in the user's mouth.
        """
        if not self.pastes:
            return self.value

        def restore(match):
            index = int(match.group(1))
            if 1 <= index <= len(self.pastes):
                return self.pastes[index - 1]
            return match.group(0)

        return _PASTE_PATTERN.sub(restore, self.value)

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


# What the box says while a turn runs and something has been typed into it.
# It replaces the "how to stop it" hint, because at that moment the line being
# written is the thing the user is looking at and the thing they need told
# about: it is not going to the model now.
QUEUED_HINT = "Enter queues this for when the current task finishes"

# And what it says when it is empty and can be typed into. It names both
# things the user can do from here, which is one more than it used to.
TYPING_HINT = "Working. Type to queue the next task. Ctrl-C to stop."


def _interrupt_main():
    """Raise KeyboardInterrupt in the main thread, as the console would have.

    Reading keys during a turn takes the Ctrl-C that used to reach the console
    directly: a raw read hands back "\\x03" as an ordinary character and no
    signal is ever raised. Without this, adding type-ahead would have silently
    removed the only way to stop a running task.
    """
    try:
        import _thread
        _thread.interrupt_main()
    except Exception:
        pass


def _queued_hint(count):
    """Shadow text for a box with lines waiting behind it.

    It states the number, for the reason the folded-paste token states its
    size: a placeholder that hid the amount would be worse than no placeholder
    at all, and "queued" alone leaves the user guessing whether the thing they
    typed twenty seconds ago actually landed.
    """
    count = max(0, int(count))
    if not count:
        return ""
    return "%d queued, and will run when this task finishes" % count


class TypeAhead:
    """Keystrokes taken while the main agent is working.

    The prompt box used to be inert for the whole of a turn: it was drawn with
    a hint in it and nothing typed into it was read, which is honest but means
    the user has to sit and wait to say the next thing. This reads keys on a
    background thread while the turn runs, so the box can be typed into, and
    holds finished lines until the loop is somewhere it can take one.

    **It never dispatches anything itself.** Pressing Enter puts the line on a
    list and nothing else happens; the session loop drains that list when the
    turn is over. That is what keeps this safe: a queued task cannot interrupt
    a turn, cannot reorder itself past one, and cannot reach the model from a
    thread that has no business talking to it.

    Two rules make the threading sound:

    **Only one reader at a time.** The main thread is inside `ask_model` for
    the whole of a turn and reads no keys, and this is stopped before
    `PromptBox.ask` runs, so stdin never has two readers. `stop()` waits for
    the thread to actually leave the read before returning.

    **Only when there are raw keys to read.** `is_interactive` gates it, so
    every piped, redirected and scripted run gets exactly the inert box it
    always had -- the same gate, for the same reason, as everywhere else in
    this module: a wrong "yes" here hangs the process on a read that can never
    be answered.
    """

    def __init__(self, stream=None, instream=None, reader=None, on_change=None,
                 interrupt=None):
        self.stream = sys.stdout if stream is None else stream
        self.instream = sys.stdin if instream is None else instream
        self.reader = reader
        # How a Ctrl-C read here is put back into the main thread. Injectable
        # only so it can be tested: firing a real asynchronous
        # KeyboardInterrupt into a suite with no isolation between tests lands
        # it in whatever happens to be running a moment later, which is a
        # flake nobody could reproduce.
        self.interrupt = interrupt if interrupt is not None else _interrupt_main
        # Called whenever the buffer changes, so the region holding the box
        # repaints. Without it the box would only redraw when the reply moved,
        # and typing would appear in bursts.
        self.on_change = on_change
        self.editor = LineEditor()
        self._queued = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._cancelled = False

    @property
    def active(self):
        return bool(self._thread and self._thread.is_alive())

    @property
    def cancelled(self):
        """Whether Ctrl-C or Esc was pressed at the box during the turn."""
        with self._lock:
            return self._cancelled

    def start(self):
        """Begin reading, or do nothing at all where reading is impossible."""
        if self.active:
            return False
        if self.reader is None and not is_interactive(self.stream, self.instream):
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._read, name="tmt-typeahead",
                                        daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout=1.0):
        """Stop reading and wait for the thread to leave the read.

        Waited on rather than abandoned: the next thing the loop does is read
        keys on the main thread, and two readers on one stdin would take it in
        turns to swallow the user's characters.
        """
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)
        return not (thread is not None and thread.is_alive())

    def _read(self):
        reader = self.reader if self.reader is not None else _default_text_reader()
        while not self._stop.is_set():
            try:
                kind, value = _next_text_key(reader, allow_multiline=True)
            except Exception:
                return
            if kind == "end":
                return
            if self._stop.is_set():
                # Checked again after the read: a key that arrived while the
                # turn was ending belongs to the box the main thread is about
                # to draw, not to this one.
                return
            if kind == "" :
                continue          # an animation tick, or a key with no meaning
            if self._apply(kind, value):
                if self.on_change is not None:
                    try:
                        self.on_change()
                    except Exception:
                        pass      # a repaint must never end the reader

    def _apply(self, kind, value):
        """Apply one keystroke. Returns whether anything visible changed."""
        if kind == "key" and value == "interrupt":
            # Ctrl-C, and it must still stop the turn.
            #
            # This is the one thing reading keys during a turn could quietly
            # break. Nothing read stdin while the agent worked before, so a
            # Ctrl-C reached the console and Python raised KeyboardInterrupt
            # in the main thread, which the loop catches and turns into
            # "Task cancelled". A raw read consumes the keystroke instead --
            # msvcrt hands back "\x03" as an ordinary character and no signal
            # is ever raised -- so the reader has to put the exception back
            # where it would have landed.
            #
            # Not handled here: this thread cannot cancel a turn, and swapping
            # a working Ctrl-C for a cleared input line would be a bad trade
            # nobody asked for.
            self._stop.set()
            try:
                self.interrupt()
            except Exception:
                pass
            return False
        with self._lock:
            if kind == "char" and len(value) > 1:
                self.editor.insert(value, pasted=True)
                return True
            if kind == "key" and value == "tab":
                return False      # completion is the asking box's, not this one's
            outcome = self.editor.handle(kind, value)
            if outcome == "submit":
                line = self.editor.expanded().strip()
                if line:
                    self._queued.append(line)
                self.editor = LineEditor()
                return True
            if outcome == "cancel":
                # Esc clears what has been typed; Ctrl-C is the turn's to
                # handle and reaches the main thread as a KeyboardInterrupt of
                # its own, so nothing here tries to stop the work.
                self._cancelled = True
                self.editor = LineEditor()
                return True
            if outcome == "end":
                self._stop.set()
                return False
            return True

    def text(self):
        """What is currently typed, for the box to draw."""
        with self._lock:
            return self.editor.value, self.editor.cursor

    def take(self):
        """Every finished line, in order, and clear the queue."""
        with self._lock:
            queued, self._queued = list(self._queued), []
            return queued

    def pending(self):
        """How many lines are waiting, without taking them."""
        with self._lock:
            return len(self._queued)


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
                 session=None, pad=None, completer=None, completed=None,
                 manager=None, panel=None):
        self.stream = sys.stdout if stream is None else stream
        self.instream = sys.stdin if instream is None else instream
        # The blank rows that hold the box against the foot of the window, or
        # None to draw it wherever the cursor is. See BottomPad.
        self.pad = pad
        # The session whose running totals the caption reports, or None for a
        # box that reports none. Held rather than copied: the figures are read
        # off it at the moment the row is drawn, so they are never stale.
        self.session = session
        # completer(text) -> ((name, summary), ...): the commands a partly
        # typed line could still become. completed(text) -> the line with the
        # completion accepted, for Tab. Both are plain functions of the text,
        # and that is what keeps two frames of the same line equal -- state
        # remembered here, or anything time-varying, would repaint under the
        # caret and put the flicker back.
        self.completer = completer
        self.completed = completed
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
        # The register of background agents, and the panel that draws it. The
        # panel is a column inside this box's own frame, because the frame is
        # a live region and the rows above it are not: everything higher up
        # the screen is already in the terminal's scrollback and is the
        # permanent record of the session.
        #
        # Both default to None and both are None for every box built the way
        # they were built before this existed, which is what makes the panel
        # cost a session without agents exactly nothing: no import, no frame
        # change, no extra key, no extra repaint.
        self.manager = manager
        self._panel_state = panel
        # The reader that takes keys while a turn is running, or None for a
        # box that is only ever typed into between turns. The session loop
        # attaches one; nothing else needs to, and with none attached this
        # class behaves exactly as it did before type-ahead existed.
        self.typeahead = None
        self.cancelled = False
        # When the box last asked something. The caller writes the question
        # into scrollback afterwards and stamps it with this, so the record
        # carries the time the user actually saw rather than a second one read
        # a moment later.
        self.asked_at = None

    def panel(self):
        """The right-hand column for this box, or None when there is none.

        agent_panel is imported inside the call rather than at module scope,
        the same way agent_credentials is: a menu that cannot draw is worth
        less than an agent that still starts, and the panel is the newest and
        least essential thing on the screen. It is also what keeps the import
        direction one way -- agent_menu reaches for agent_panel, and
        agent_panel reaches back for two formatters at call time, so neither
        module needs the other to load.

        A box built with a manager and no panel gets one made here, once, so
        the selection and the open state survive from one question to the
        next: the panel is a view onto the manager, and the manager is where
        an agent's state actually lives.

        A box with a SESSION gets one too, because the session is where the
        task's plan lives and the plan wants the same column. The plan is
        passed as a callable rather than as the object: the session empties
        its plan between turns, and a panel holding the object it was built
        with would go on drawing a task that is over. A box with neither still
        gets nothing, which is what makes this cost a plain box exactly what
        it always cost -- no import, no frame change, no extra repaint.
        """
        if self._panel_state is not None:
            return self._panel_state
        if self.manager is None and self.session is None:
            return None
        try:
            import agent_panel
        except Exception:
            return None
        self._panel_state = agent_panel.PanelState(
            self.manager, stream=self.stream,
            plan=lambda: getattr(self.session, "plan", None),
            # A callable for the reason the plan is one: the session empties
            # its review between turns, and a panel holding the object it was
            # built with would go on drawing the verdict on a finished task.
            review=lambda: getattr(self.session, "review", None),
            # And a third, for the third thing the session empties between
            # turns. A panel holding the object it was built with would go on
            # drawing the checks that ran for a task that is over.
            verify=lambda: getattr(self.session, "verify", None),
            # And a fourth, for what the user authorised. Re-read between
            # turns like the three above, so a panel holding the object it
            # was built with would go on saying a finished task's permissions
            # are this one's.
            capabilities=lambda: getattr(self.session, "capabilities", None))
        return self._panel_state

    def _agents_text(self):
        """The agent counter for the caption, or "" when there are none."""
        state = self.panel()
        if state is None:
            return ""
        try:
            return state.counter()
        except Exception:
            return ""

    def _panel_frame(self, size=None):
        """(left columns, join) for the right-hand column, or None for none.

        Measured against the terminal as it is now, like everything else the
        box draws, so a window narrowed past the panel's floor mid-edit closes
        the panel rather than drawing something unreadable into it.

        Whether there is a column at all is `PanelState.frame`'s decision and
        not this method's. It used to be asked here, as `state.open`, which
        was right while the agents panel was the only thing that wanted the
        column and silently wrong the moment the plan wanted it too: the live
        relay asks the state directly and would draw the plan while the box
        beside it did not.
        """
        state = self.panel()
        if state is None:
            return None
        columns, rows = _terminal(size)
        try:
            return state.frame(columns, rows)
        except Exception:
            return None

    def _panel_key(self, raw):
        """Give the open panel first refusal on a keystroke.

        Returns whether the panel took it. Everything the panel does not take
        falls through to the field, which is why a character typed while the
        panel is open is still a character.
        """
        state = self.panel()
        if state is None or not state.open:
            return False
        try:
            import agent_panel
        except Exception:
            return False
        return bool(state.handle(agent_panel.panel_key(raw)))

    def _open_panel(self, size=None):
        """Right Arrow at the end of the line. Returns whether it opened.

        Right Arrow already moves the caret, and binding it outright would
        break editing and several tests that describe it. At the END of the
        buffer it is a no-op -- there is nothing to its right to move onto --
        and that is the only place it is taken. Nothing that works today
        changes.
        """
        state = self.panel()
        if state is None:
            return False
        return state.open_panel(_terminal(size)[0])

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
                    placed = self._place(frame[0], frame[1], frame[2])
                    region.show_cursor()
                    shown = frame
                raw = _next_raw_key(reader)
                # A refusal answers one gesture and stops being true at the
                # next. It is drawn by the frame above, so it has been on
                # screen for exactly as long as the user has been looking at
                # it, and clearing it here is what takes it down.
                if self._panel_state is not None:
                    self._panel_state.message = ""
                # The panel gets first refusal on a keystroke, and only while
                # it is open. It takes Up, Down, Enter and Left and hands back
                # everything else, so a character typed while it is on screen
                # is still typed into the line behind it. Up and Down reach
                # the field as a tick today and Left is a caret move, so
                # nothing that works with the panel shut is touched.
                if self._panel_key(raw):
                    continue
                kind, value = normalize_text_key(raw, allow_multiline=True)
                # Right Arrow at the end of the line, where it moves nothing.
                # Taken before the editor sees it, and only when this box was
                # given a panel at all.
                if (kind == "key" and value == "right"
                        and self.panel() is not None
                        and editor.cursor >= len(editor.value)):
                    self._open_panel()
                    continue
                if kind == "char" and len(value) > 1:
                    # More than one character in a single read is a paste --
                    # no keystroke produces two. Only the editor is told, and
                    # only so it can decide whether the block is long enough
                    # to be worth folding.
                    editor.insert(value, pasted=True)
                    continue
                if kind == "key" and value == "tab":
                    # Taken before the editor sees it. Accepting a completion
                    # is an edit the user asked for, so it goes through the
                    # buffer like any other -- and a Tab that completes
                    # nothing does nothing at all, which is what a Tab in a
                    # line of prose should do.
                    self._accept_completion(editor)
                    continue
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
                    # Expanded, not as displayed: the token was a way of
                    # showing a long paste in a small box, never a way of
                    # shortening what the user actually said.
                    return editor.expanded()
                if outcome == "cancel":
                    self.cancelled = True
                    return ""
                return None
        finally:
            self._unplace(placed)
            _restore_terminal(self.stream)

    def lines(self, editor, size=None, phase=None, moment=None, caption=True,
              pad=True, column=True):
        """The painted box, as a list of rows."""
        return self._frame(editor, size, phase, moment, caption, pad, column)[0]

    def running_lines(self, hint="", size=None):
        """The box as it stands while the turn it asked for is running.

        Empty, with the hint drawn as shadow text: there is no caret in it and
        nothing typed into it would be read, so a box that looked ready for
        input would be a lie about what the program is doing. The hint says
        what is happening and how to stop it, which is the only thing the user
        can actually do from here.

        No clock and no provider on it. Those stamp a question with when it
        was asked and what will answer it, and the question this box is
        waiting on already carries them, a few rows up in the scrollback; a
        second copy moving under the reply as it arrives would be the same
        fact twice. The meter is not a second copy of anything -- it is the
        one thing here that changes while the turn runs, and this is where the
        eye already is -- so it gets the row on its own.

        Drawn by the live relay as the footer of its region, so it stays put
        at the foot of the window while the turn's output scrolls past above.
        No blank rows above it either: the relay pads that region as a whole,
        and a pad here would be counted twice and push the box off the bottom.
        """
        # What is being typed into it, when anything is. The box stays inert
        # in every run that cannot read raw keys, so a piped or scripted run
        # draws exactly the hint it always drew.
        editor = LineEditor(hint)
        typed = self.typeahead
        if typed is not None and typed.active:
            value, cursor = typed.text()
            if not value and not typed.pending():
                # The box can be typed into now, and nothing else on screen
                # says so. The old hint said only how to stop the work, which
                # was the whole truth when the box was inert and is half of it
                # now.
                editor = LineEditor(TYPING_HINT)
            if value:
                # A real editor rather than a placeholder, so the text is
                # drawn plain instead of dim and wraps like any other line.
                editor = LineEditor()
                editor.value, editor.cursor = value, cursor
                editor.placeholder_visible = False
            elif typed.pending():
                # Nothing on the line, but lines are waiting. Say so, because
                # the alternative is a box that looks untouched while the user
                # has already queued three questions into it.
                editor = LineEditor(_queued_hint(typed.pending()))
        # `column=False`: this box is the RELAY's footer, and the relay draws
        # the right-hand column around the whole region -- it has already
        # narrowed the `size` handed in here to the left column's width. A
        # column composed here as well would sit inside that one, and the plan
        # would be on screen twice, side by side.
        rows = self.lines(editor, size=size, caption=False, pad=False,
                          column=False)
        width = _content_width(_terminal(size)[0])
        meter = meter_text(self.session, self.stream, columns=width,
                           manager=self.manager)
        # The agent counter shares the meter's row rather than taking one of
        # its own. Both are running totals of what this session is spending,
        # they are read in the same glance, and a second row here would push
        # the box up the window every time an agent was spawned. With no
        # agents the string is empty and the row is exactly the meter's, as it
        # was before the counter existed.
        agents = self._agents_text()
        left = "  ".join(part for part in (meter, agents) if part)
        return ([" " + left] if left else []) + rows

    def _frame(self, editor, size=None, phase=None, moment=None, caption=True,
               pad=True, column=True):
        """(rows, caret column) for the box as it stands.

        `column` is whether this box composes the right-hand column ITSELF.
        True when the box is the live region -- `ask` -- and False when it is
        somebody else's footer, because then that somebody owns the column and
        has already narrowed the width being passed in. Drawing one here as
        well puts a second copy of the plan inside the first one's left column.

        The terminal is measured here rather than remembered, so a window
        resized between two keystrokes is drawn at its new width.

        Undecorated: the rules and the marker are the terminal's own colour.
        That is what makes two frames of an untouched box equal, which is what
        lets `ask` leave the caret alone -- and a box that is being typed into
        has no business carrying a colour that means progress.

        `caption` is off for the box the relay draws while a turn runs, which
        is not asking anything and so has nothing to stamp.

        Returns (rows, caret column, rows below the caret). The third is what
        `_place` counts back from the bottom, because the commands offered
        under a half-typed `/mo` sit between the line and the bottom rule and
        move the caret further from the foot of the frame.
        """
        stream = self.stream
        columns = _terminal(size)[0]
        # The panel, if one is open, takes its column out of the width before
        # anything else is measured. Everything below then draws the box at
        # the width that is left, which is the whole of what a second column
        # costs this method: `_frame` already re-measures the terminal on
        # every paint, so a narrower box is a width argument rather than a
        # rewrite.
        panel = self._panel_frame(size) if column else None
        if panel is not None:
            left, join = panel
            if not left:
                # Panel-only: too narrow for two columns. No box is drawn at
                # all. It is not accepting input while the panel has focus,
                # and one that looked ready for input would be a lie about
                # what the program is doing -- the rule `running_lines`
                # already follows. A caret distance of zero says the same
                # thing to `_place`: there is no input row to move into.
                rows = list(join([]))
                holding = self.pad if (pad and self.pad is not None) else None
                lead = [""] * (holding.above(len(rows), size) if holding else 0)
                return lead + rows, 0, 0
            columns = left + 1
        # A spare column, always: a row drawn to the last one wraps on the
        # terminals that auto-wrap, and costs a screen line the repaint
        # arithmetic does not know about.
        # Six is the narrowest box that is still a box: a margin, the marker,
        # a gap, one column of text and the spare column.
        limit = max(6, columns - 1)
        width = min(_content_width(columns), limit)
        inner = max(1, width - _PROMPT_PREFIX)

        head = [prompt_caption(stream, width, moment, session=self.session,
                               agents=self._agents_text(),
                               manager=self.manager)] if caption else []
        rule = " " + _glyphs(stream)["rule"] * (width - 1)
        field, caret_row, caret = self._field(editor, inner)
        # The marker belongs to the question, not to every row of it, so the
        # rows below the first are indented to line up under the text rather
        # than repeating a second ">" the user never typed.
        typed = []
        for index, body in enumerate(field):
            body = _dim(body, stream) if editor.placeholder_visible else body
            lead = " " + PROMPT_MARKER + " " if index == 0 else " " * _PROMPT_PREFIX
            typed.append(lead + body)
        offered = self._offered(editor, width) + self._refusal(width)
        rows = head + [rule] + typed + offered + [rule]
        # How far the caret sits above the foot of the frame: the bottom rule,
        # then anything offered under the field, then however many field rows
        # are below the one being typed on. With one row and nothing offered
        # this is _INPUT_ROW, which is what it always was.
        up = 1 + len(offered) + (len(typed) - caret_row)
        # The panel is composed last, against the finished box. The box rows
        # are flush with the BOTTOM of the composed block, so the caret is
        # still `up` rows above its foot and `_place` is unchanged.
        if panel is not None:
            rows = list(panel[1](rows))
        # Blank rows above, so the box sits at the foot of the window from the
        # moment the session opens rather than wherever the last reply
        # stopped. They are part of the region, so the repaint arithmetic
        # counts them like any other row, and they are given up one at a time
        # as permanent output fills the window from the top.
        holding = self.pad if (pad and self.pad is not None) else None
        lead = [""] * (holding.above(len(rows), size) if holding else 0)
        return lead + rows, _PROMPT_PREFIX + caret, up

    def _refusal(self, width):
        """The reason the panel would not open, as one row inside the box.

        It answers a gesture the user just made, it stops being true the
        moment anything else happens, and it is one line -- so it belongs on
        the temporary surface, drawn where the offered commands are drawn and
        gone with the next repaint that has something else to say. Printing it
        permanently would leave a sentence about a terminal width in the
        scrollback for the rest of the session.
        """
        state = self._panel_state
        message = getattr(state, "message", "") if state is not None else ""
        if not message:
            return []
        return [_dim(fit_to_width("   " + message, width), self.stream)]

    def _accept_completion(self, editor):
        """Take the completion Tab offers, through the buffer.

        Written by assigning `value` rather than by inserting text, because
        what is accepted replaces the whole line rather than adding to it --
        `/co` becomes `/con`, not `/co/con`. The caret goes to the end, which
        is where the user is about to keep typing.

        The placeholder is untouched either way: it is dismissed by the first
        character typed and there is no path back to it, so a line long enough
        to complete has already dismissed it.
        """
        if self.completed is None:
            return False
        try:
            line = self.completed(editor.value)
        except Exception:
            return False
        if not line or line == editor.value:
            return False
        editor.value = line
        editor.cursor = len(line)
        editor.placeholder_visible = False
        return True

    def _offered(self, editor, width):
        """The commands the line being typed could still become.

        Under the line rather than over it, which is the way round every
        shell puts them and the way round a reader expects: what you typed
        stays where you typed it and the list grows downward from it. The
        box grows upward as a whole, because `BottomPad` gives a taller frame
        fewer blank rows and its bottom edge does not move.

        Dim, indented, and never under a '>' -- that marker means "type
        here", and a row wearing it would be counted as a prompt by anything
        reading the screen back. Plain text, so it reads with the colour
        stripped and survives a console that can encode nothing decorative.

        Nothing at all unless the line is a command being typed: an ordinary
        task never grows a row, so nothing about the box a session spends its
        time in has changed.
        """
        if self.completer is None or editor.placeholder_visible:
            return []
        try:
            matches = self.completer(editor.value)
        except Exception:
            return []            # decoration is never allowed to end a run
        rows = []
        for name, summary in matches:
            rows.append(_dim(fit_to_width("   %-9s %s" % (name, summary), width),
                             self.stream))
        return rows

    def _field(self, editor, inner, max_rows=INPUT_MAX_ROWS):
        """The visible rows of the line, and where the caret sits in them.

        Returns (rows, caret row, caret column). The field wraps down the
        screen instead of scrolling sideways, up to `max_rows`, and scrolls
        vertically past that. The caret row is what `_frame` turns into the
        distance the caret has to be moved up from the foot of the box.

        The rows come back PAINTED, with any `/plan`, `/review` or `/verify`
        in them carrying the gradient. Done here rather than in `_frame`
        because this is where the row is fitted, and the two have to happen in
        this order and nowhere apart: fitting measures escape sequences as
        though they took columns, so a row painted first would be cut through
        the middle of one.

        Nothing about the caret changes. `caret_in` measures the raw editing
        buffer and `_place` moves the terminal cursor in columns, so neither
        of them ever looks at a row's content -- which is what makes it safe
        to put zero-width escapes into it.

        The placeholder never wraps. It is one line of shadow text standing in
        for an empty field, and a hint that grew the box would be a suggestion
        rearranging the screen before it had been taken.
        """
        if editor.placeholder_visible:
            # Not painted. The placeholder is TMT's own words standing in for
            # an empty field -- "Describe your next task" -- so a command
            # highlighted in it would be marking something the user has not
            # typed and has not authorised. `_frame` dims the whole row, and a
            # RESET from inside would end that dim early.
            return [fit_to_width(editor.placeholder, inner)], 0, 0
        shown, caret_row, caret_column, _, _ = visible_field_rows(
            editor.value, editor.cursor, inner, max_rows)
        spans = capability_spans(editor.value)
        rows = [paint_capabilities(fit_to_width(body, inner), start, spans,
                                   self.stream)
                for start, body in shown]
        return rows, caret_row, caret_column

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

    def _place(self, lines, column, up=_INPUT_ROW):
        """Move the caret into the input row. Returns the rows moved up.

        `up` is how far the input row sits above the foot of the frame, which
        `_frame` works out: two ordinarily, and more when commands are being
        offered under the line, and zero when no box was drawn at all --
        which is what a terminal too narrow for two columns does while the
        agents panel is open. Zero means there is no input row to move into,
        so the caret is left where the region put it rather than being sent
        up nowhere.
        """
        if up <= 0 or not self._ansi():
            return 0
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
              workspace=None, selected=0, resuming=False, busy=None,
              cursor=None):
    """Run the startup menu and return the chosen action.

    One of "start", "settings", "help" or "exit". Exhausted input, Esc, q and
    Ctrl-C all answer "exit", so no path leaves the caller waiting.

    `busy` is a CALLABLE answering what is still running, re-asked on every
    frame, because a background agent can finish while the menu is open and
    the Settings row has to come back when it does. A plain value would freeze
    the answer at the moment the menu opened.

    **The cursor is kept on a KEY and not on an index**, which is the whole of
    what makes a disappearing row safe. Settings is removed while work runs
    and comes back when it stops, so an index would slide by one under a
    cursor nobody moved -- and the row it slid onto is Exit, which ends the
    session. Tracking the name means the cursor stays on the thing the user
    put it on, and lands on the first row when that thing is gone.

    `cursor` is that key from the OUTSIDE, and it exists because the same bug
    came back through the caller. `run_startup` reopens this menu after
    Settings and after Help and says where to put the cursor; it used to say
    it as a number, and with Settings removed the number 2 is Exit rather than
    Help -- so reading Help during a busy session left the cursor on the row
    that ends it. `selected` is still taken, and is still an index, so every
    existing caller and test means what it meant; `cursor` wins when it names
    a row that is actually there.
    """
    stream = sys.stdout if stream is None else stream
    if key_reader is None:
        if not is_interactive(stream):
            return "start"
        key_reader = _default_reader()
    region = LiveRegion(stream) if region is None else region
    busy = (lambda: "") if busy is None else busy

    def running():
        """What is still running, as a phrase, guarded to nothing."""
        try:
            return str(busy() or "")
        except Exception:
            return ""

    opening = menu_items(resuming=resuming, busy=bool(running()))
    keys = [item[0] for item in opening]
    state = {"key": cursor if cursor in keys else opening[selected % len(opening)][0]}

    def place(items):
        """Where the cursor sits in `items`, and never off the end of them."""
        for index, item in enumerate(items):
            if item[0] == state["key"]:
                return index
        state["key"] = items[0][0]
        return 0

    def render():
        note = running()
        items = menu_items(resuming=resuming, busy=bool(note))
        return render_startup_frame(place(items), stream, model_id, workspace,
                                    resuming=resuming, busy=note)

    def on_key(key):
        if key in (None, "esc", "quit", "interrupt"):
            return "exit"
        items = menu_items(resuming=resuming, busy=bool(running()))
        index = place(items)
        if key == "up":
            state["key"] = items[(index - 1) % len(items)][0]
        elif key == "down":
            state["key"] = items[(index + 1) % len(items)][0]
        elif key == "enter":
            return items[index][0]
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
            if entry == "projectcontext":
                # Toggled in place beside the updater, and for the same
                # reason: it is a switch rather than a screen. `_drive`
                # rebuilds the frame on its next pass and `project_context_text`
                # re-reads the file, so the row shows the new value at once.
                toggle_project_context()
            elif entry == "autoupdate":
                # Toggled in place: it is a switch, not a screen, and it is
                # the only entry here that does not open one. The frame is
                # rebuilt on the next pass of `_drive`, which re-reads the
                # setting, so the row shows the new value immediately.
                toggle_auto_update()
            elif entry == "provider":
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
            elif entry == "danger":
                if danger_screen(stream=stream, key_reader=key_reader,
                                 region=region, text_reader=text_reader):
                    # TMT has just removed itself. There is no Settings to
                    # come back to and no menu behind it, so the answer goes
                    # straight out to `run_startup`, which closes.
                    state["changed"] = UNINSTALLED
                    return "done"
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


def run_startup(stream=None, key_reader=None, model_id=None, workspace=None,
                resuming=False, busy=None):
    """Show the startup screen once and return what the user chose.

    Returns "start" to enter TMT, or "exit" to quit. Returns "start" without
    drawing anything when the terminal cannot support an interactive menu.

    The interactivity check comes first and is unconditional, including when a
    key_reader was supplied: a piped or scripted run must never be able to
    reach a read, whoever called it. The individual screens are the seam for
    driving the menu without a terminal.

    `resuming` and `busy` are what `/back` brings: a session is waiting behind
    this menu, so Start reads Resume, and whatever `busy()` names is still
    running, so Settings is not offered. Both default to the launch shape, so
    the one call `main` makes is unchanged.
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
    cursor = None
    try:
        _hide_cursor(stream)
        # The menu is a screen, so it starts at the top of one rather than
        # halfway down whatever the shell last printed.
        clear_screen(stream)
        # **Nothing is asked for before the menu.** The credential used to be
        # taken here, before the first frame, so a first-time user met a form
        # where they expected a program: wordmark, then "paste your API key".
        # It is asked for when Start is chosen instead, a few lines down --
        # which is also the moment it is actually needed, and which leaves
        # Settings as a way to do it first for anyone who would rather.
        while True:
            choice = main_menu(stream=stream, key_reader=key_reader, region=region,
                               model_id=model_id, workspace=workspace,
                               selected=selected, resuming=resuming, busy=busy,
                               cursor=cursor)
            if choice == "settings":
                # Where the cursor goes when this menu reopens, said as the
                # ROW rather than as its position. It used to be a number, and
                # a number is wrong the moment a row can be missing: with work
                # running there is no Settings row, so index 2 is Exit and
                # reading Help left the cursor on the thing that ends the
                # session the user pressed /back to keep.
                cursor = choice
                chosen = settings_screen(stream=stream, key_reader=key_reader,
                                         region=region, active_id=model_id,
                                         text_reader=text_reader)
                if chosen is UNINSTALLED:
                    # Identity, not truthiness: Settings answers with a model
                    # id and this is the one answer that is not one. TMT has
                    # been removed, so there is no menu to redraw and nothing
                    # left to start.
                    return "exit"
                if chosen:
                    model_id = chosen
            elif choice == "help":
                cursor = choice
                help_screen(stream=stream, key_reader=key_reader, region=region)
            elif choice == "exit":
                return "exit"
            else:
                # Start. The credential is asked for HERE and nowhere before
                # it, so the first screen after the wordmark is the menu.
                #
                # `resuming` skips it outright: a session reached through
                # `/back` got past this at launch, and asking again would be a
                # question that has already been answered.
                if resuming or provider_is_configured():
                    return "start"
                cursor = "start"
                provider_setup(stream=stream, key_reader=key_reader,
                               region=region, text_reader=text_reader)
                if provider_is_configured():
                    return "start"
                # Nothing was configured -- Esc, or a key the user did not
                # have to hand. Back to the menu rather than into a session
                # that cannot reach a model, and with the cursor still on
                # Start so a second attempt is one keystroke.
    except KeyboardInterrupt:
        return "exit"
    finally:
        # Whatever happened, including an exception on the way out: no stale
        # frames left for the agent to scroll, and a usable terminal.
        region.clear()
        _restore_terminal(stream)
