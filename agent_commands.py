"""Slash commands: the five things the user can ask TMT about itself.

A line that begins with `/` is answered here and never reaches the model. It is
the same input box, the same keystrokes and the same screen; the only thing
this module adds is a fork taken before a task is built, and a small table of
what each command does.

Nothing here owns any state. The provider comes from agent_credentials, the
model from agent_models, the workspace and the effort setting from
agent_config, the conversation from the Session the loop already holds. A
command reads them and writes back through the same functions Settings uses,
so `/model` and the Settings screen cannot disagree about what is selected.

**Nothing here ever prints a credential.** `/config` and `/context` are the two
places a key would be most convenient to show and the two places it would be
most damaging to: they are read over shoulders and pasted into bug reports.
Where a command has to say anything about a key at all it says whether one is
set, and never the value or any part of it.
"""

import re

import agent_config
import agent_models

# What makes a line a command: a leading slash and then one plain word. A
# slash on its own is somebody who has typed a slash and stopped, and
# "/usr/bin/python is broken" is a task that happens to start with a path.
# Neither is a command, and both go to the model exactly as they always did.
PREFIX = "/"
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

# What a command may be given after its name. Held here rather than parsed by
# each handler so the error for a stray argument reads the same everywhere.
_TAKES_ARGUMENT = ("effort", "model")


class Result:
    """What a command produced: a title, some rows, and whether it worked.

    `rows` are (label, value) pairs for a fact, or a bare string for a line of
    prose. The renderer decides how they are drawn; a handler decides only what
    is true.
    """

    __slots__ = ("title", "rows", "ok", "note")

    def __init__(self, title, rows=(), ok=True, note=""):
        self.title = str(title)
        self.rows = list(rows)
        self.ok = bool(ok)
        self.note = str(note or "")

    def __repr__(self):
        return "Result(title=%r, rows=%d, ok=%s)" % (self.title, len(self.rows), self.ok)

    def text(self):
        """The whole result as plain text, for tests and for a piped run."""
        lines = [self.title]
        for row in self.rows:
            if isinstance(row, tuple):
                lines.append("%s  %s" % row)
            else:
                lines.append(str(row))
        if self.note:
            lines.append(self.note)
        return "\n".join(lines)


def parse(text):
    """(name, argument) for a slash command, or None for an ordinary task.

    Case-insensitive on the name and never on the argument: `/MODEL GLM-5.2`
    is the model called GLM-5.2, asked for in a shout.

    Returns None -- not an error -- for anything that is not a command, so the
    caller's test is `parse(line) is None` and every other line goes to the
    model exactly as it did before this module existed.
    """
    if not isinstance(text, str):
        return None
    line = text.strip()
    if not line.startswith(PREFIX) or line == PREFIX:
        return None
    body = line[len(PREFIX):]
    name, _, argument = body.partition(" ")
    name = name.strip()
    if not name or not _NAME_RE.match(name):
        # "/ something" is a slash and then prose, and "/usr/bin/python is
        # broken" is a task that happens to start with a path. Neither is a
        # command, and both go to the model exactly as they always did. A
        # command name is one plain word; anything else is not one at all,
        # which is a better answer than "unknown command" to a sentence.
        return None
    return name.lower(), argument.strip()


def names():
    """Every command name, in the order they are offered."""
    return tuple(_HANDLERS)


def complete(text):
    """The command names a partly typed line could still become.

    `/` offers all of them, `/mo` offers `/model`, and a name already complete
    offers itself. Anything that is not the start of a command offers nothing,
    which is how a path or a task beginning with a slash stays silent.
    """
    if not isinstance(text, str):
        return ()
    line = text.lstrip()
    if not line.startswith(PREFIX):
        return ()
    typed = line[len(PREFIX):].split(" ")[0].lower()
    if " " in line.strip():
        # The name is settled and an argument is being typed; there is nothing
        # left to complete.
        return (typed,) if typed in _HANDLERS else ()
    return tuple(name for name in _HANDLERS if name.startswith(typed))


def completions(text):
    """(name, summary) for every command a partly typed line could become.

    What the prompt box draws under the line being typed. It is a plain
    function of the text: the box calls it while building a frame, and two
    frames of the same text must be equal or the repaint -- and the caret --
    starts moving on its own.
    """
    return tuple((PREFIX + name, SUMMARY[name]) for name in complete(text))


def completed(text):
    """The line as it would be after accepting the completion, or "".

    One match completes to it. Several complete as far as they agree, which
    is what a shell does and is never a guess: every character added is one
    every candidate has. Nothing to add returns "" and the key does nothing.
    """
    matches = complete(text)
    if not matches:
        return ""
    shared = matches[0]
    for name in matches[1:]:
        while not name.startswith(shared):
            shared = shared[:-1]
    if not shared:
        return ""
    line = text.lstrip()
    typed = line[len(PREFIX):]
    if len(shared) <= len(typed):
        # Already there. One match and nothing left to add means the name is
        # complete, so the space that would follow it is worth adding.
        return PREFIX + shared + " " if len(matches) == 1 and not text.endswith(" ") else ""
    return PREFIX + shared + (" " if len(matches) == 1 else "")


def suggestion(text):
    """The single completion to draw beside a partly typed command, or "".

    Only when it is unambiguous. Two candidates and nothing is drawn: a
    suggestion that guesses is worse than none, because the user reads it as
    what will happen if they press Tab.
    """
    matches = complete(text)
    if len(matches) != 1:
        return ""
    typed = text.lstrip()[len(PREFIX):]
    if typed.lower() == matches[0] or " " in text.strip():
        return ""
    return matches[0][len(typed):]


def dispatch(text, session=None):
    """Run a slash command. Returns a Result, or None for an ordinary task.

    The one entry point the agent loop calls. `session` is the loop's own
    Session; a command that does not need it works without one, so this module
    can be exercised on its own.
    """
    parsed = parse(text)
    if parsed is None:
        return None
    name, argument = parsed
    handler = _HANDLERS.get(name)
    if handler is None:
        return _unknown(name)
    if argument and name not in _TAKES_ARGUMENT:
        return Result("/%s takes no argument" % name,
                      ["It was given %r." % argument,
                       "Usage: %s" % USAGE[name]], ok=False)
    return handler(argument, session)


def _unknown(name):
    """A command that does not exist, and the nearest ones that do."""
    near = [offered for offered in _HANDLERS if offered.startswith(name[:2])]
    rows = ["TMT has no /%s command." % name]
    if near:
        rows.append("Did you mean: " + ", ".join("/" + one for one in near) + "?")
    rows.append("Available: " + ", ".join("/" + one for one in _HANDLERS))
    return Result("Unknown command", rows, ok=False)


# --- the commands ----------------------------------------------------------

def _facts(session):
    """Provider, model and workspace, from the modules that own them."""
    provider = model = ""
    try:
        import agent_credentials
        provider = agent_credentials.selected_provider() or ""
    except Exception:
        provider = ""
    try:
        model = agent_models.current_model(provider or None)
    except Exception:
        model = ""
    workspace = getattr(session, "workspace", None) or agent_config.ROOT_DIR
    return provider, model, workspace


def _model_label(model_id, provider_id):
    try:
        label = agent_models.describe(model_id, provider_id or None)
    except Exception:
        label = ""
    if label and label != model_id:
        return "%s  (%s)" % (label, model_id)
    return model_id or "not set"


def _context(argument, session):
    """What the conversation looks like right now."""
    provider, model, workspace = _facts(session)
    rows = [("Model", _model_label(model, provider)),
            ("Provider", provider or "not set"),
            ("Workspace", str(workspace))]
    if session is None:
        rows.append("No session is running, so there is no conversation yet.")
        return Result("Context", rows)

    turns = session.turns
    rows.append(("Turns", "%d asked, %d carried into the next request"
                 % (len(turns), len(session.carried()))))
    window = session.context_window()
    budget = session.carry_budget()
    used = sum(turn.size() for turn in session.carried())
    # Labelled an estimate because it is one: no provider counts a request
    # before it is sent, and this is characters over a constant.
    rows.append(("Carried", "~%s of ~%s tokens budgeted (%s window)"
                 % (_count(used), _count(budget), _count(window))))
    # Two figures, said apart, because they answer different questions and
    # were once one number answering neither. The first is how big the last
    # request was -- how full the window is. The second is every request added
    # up, which is larger by however many steps the questions took, because a
    # stateless API is sent the whole prompt again for each one.
    rows.append(("Tokens", "~%s in the last request, ~%s sent in all, %s%s out"
                 % (_count(session.tokens_in), _count(session.tokens_sent),
                    "" if session.tokens_out_exact and not session.streaming else "~",
                    _count(session.tokens_out))))
    rows.append(("Lines", "+%d  -%d" % (session.lines_added, session.lines_removed)))
    if turns:
        rows.append("")
        rows.append("Most recent first:")
        for turn in reversed(turns[-5:]):
            rows.append("  " + _one_line(turn.task, 60))
    return Result("Context", rows)


def _config(argument, session):
    """The settings a request runs under. No secrets, by construction."""
    provider, model, workspace = _facts(session)
    rows = [("Model", _model_label(model, provider)),
            ("Provider", provider or "not set"),
            ("Effort", "%s  (%d max tokens, %d rounds per task)"
             % (agent_config.EFFORT,
                agent_config.max_tokens_for_effort(),
                agent_config.rounds_for_effort())),
            ("Streaming", "on" if agent_config.STREAM_ENABLED else "off"),
            ("JSON mode", "on" if agent_config.USE_JSON_MODE else "off"),
            ("Workspace", str(workspace)),
            ("Install", str(agent_config.INSTALL_DIR))]
    # Whether a key exists. Not the key, and not a masked form of it either:
    # this row is read over shoulders and pasted into bug reports, and the
    # only thing anyone needs from it is whether one is configured. The
    # Settings screen shows the masked form, where the user went looking for
    # it deliberately.
    try:
        import agent_credentials
        rows.append(("API key", "set" if agent_credentials.credential(provider or None)
                     else "not set"))
    except Exception:
        rows.append(("API key", "unavailable"))
    if agent_models.is_overridden():
        rows.append("OPENROUTER_MODEL is set, so it overrides the saved model.")
    return Result("Configuration", rows)


def _clear(argument, session):
    """Forget the conversation. Everything else is left exactly as it was."""
    if session is None:
        return Result("Nothing to clear", ["No session is running."], ok=False)
    before = len(session)
    provider, model, workspace = _facts(session)
    session.clear()
    rows = [("Cleared", "%d turn%s" % (before, "" if before == 1 else "s")),
            ("Model", _model_label(model, provider)),
            ("Effort", agent_config.EFFORT),
            ("Workspace", str(workspace))]
    return Result("Conversation cleared", rows,
                  note="The next question starts fresh. No files were touched.")


def _effort(argument, session):
    """Show or set how much work TMT will spend on one task."""
    levels = agent_config.effort_names()
    if not argument:
        rows = [("Effort", agent_config.EFFORT)]
        for level in levels:
            settings = agent_config.EFFORT_LEVELS[level]
            marker = "> " if level == agent_config.EFFORT else "  "
            rows.append("%s%-7s %d max tokens, %d rounds per task"
                        % (marker, level, settings["max_tokens"], settings["rounds"]))
        rows.append("")
        rows.append("Usage: %s" % USAGE["effort"])
        return Result("Effort", rows)
    try:
        chosen = agent_config.set_effort(argument)
    except ValueError as error:
        return Result("That is not an effort level",
                      [str(error), "Usage: %s" % USAGE["effort"]], ok=False)
    settings = agent_config.effort_settings(chosen)
    return Result("Effort set to %s" % chosen,
                  [("Max tokens", str(settings["max_tokens"])),
                   ("Rounds per task", str(settings["rounds"]))],
                  note="It applies from the next question.")


def _model(argument, session):
    """Show or change the model the next request goes to."""
    provider, current, _ = _facts(session)
    try:
        catalogue = agent_models.catalogue(provider or None)
    except Exception:
        catalogue = []
    if not argument:
        rows = [("Model", _model_label(current, provider)),
                ("Provider", provider or "not set")]
        if catalogue:
            rows.append("")
            rows.append("Offered by this provider:")
            for entry in catalogue:
                marker = "> " if entry["id"] == current else "  "
                rows.append("%s%s  %s" % (marker, entry.get("label") or entry["id"],
                                          entry["id"]))
        rows.append("")
        rows.append("Usage: %s" % USAGE["model"])
        return Result("Model", rows)
    try:
        chosen = agent_models.set_model(_resolve(argument, catalogue), provider or None)
    except ValueError as error:
        rows = [str(error)]
        if catalogue:
            rows.append("Offered: " + ", ".join(entry["id"] for entry in catalogue))
        return Result("That is not a model TMT offers", rows, ok=False)
    except OSError as error:
        return Result("The model could not be saved", [str(error)], ok=False)
    # agent_config.MODEL is what the rest of the project reads; set_model keeps
    # it in step for the selected provider, and this covers the rest.
    agent_config.refresh_model()
    return Result("Model set", [("Model", _model_label(chosen, provider)),
                                ("Provider", provider or "not set")],
                  note="It applies from the next request.")


def _resolve(argument, catalogue):
    """A model id from what the user typed.

    An exact id wins. Failing that a label match, case-insensitively, so
    `/model GLM 5.2` finds the model the menu calls GLM 5.2. Anything else is
    passed through untouched for agent_models.set_model to refuse by name,
    which keeps one place deciding what is offered.
    """
    typed = argument.strip()
    folded = typed.casefold()
    for entry in catalogue or ():
        if entry.get("id") == typed:
            return typed
    for entry in catalogue or ():
        if str(entry.get("label", "")).casefold() == folded:
            return entry["id"]
    for entry in catalogue or ():
        if str(entry.get("id", "")).casefold() == folded:
            return entry["id"]
    return typed


# --- small helpers ---------------------------------------------------------

def _count(value):
    """A token figure at the size a row can carry."""
    value = max(0, int(value or 0))
    if value >= 1000000:
        return "%.1fM" % (value / 1000000.0)
    if value >= 1000:
        return "%dk" % (value // 1000)
    return str(value)


def _one_line(text, limit):
    """One line of a question, for a list of them."""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit - 1].rstrip() + "…"


_HANDLERS = {
    "context": _context,
    "config": _config,
    "clear": _clear,
    "effort": _effort,
    "model": _model,
}

SUMMARY = {
    "context": "what the conversation looks like now",
    "config": "the settings a request runs under",
    "clear": "forget the conversation, keep everything else",
    "effort": "how much work TMT spends on one task",
    "model": "which model answers",
}

USAGE = {
    "context": "/context",
    "config": "/config",
    "clear": "/clear",
    "effort": "/effort [low|medium|high]",
    "model": "/model [<model id or name>]",
}
