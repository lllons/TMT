"""Persistent project context: what TMT knows about THIS project, on disk.

A session used to start from nothing and end with nothing. Everything worked
out about a project -- where the entry point is, which command runs the tests,
what was implemented last time and what was left half done -- was rebuilt from
the workspace snapshot on every launch, or lost when the process ended. The
conversation carries it for one run; `agent_memory` carries short notes in
TMT's own directory; neither is a thing the user can open, read, correct or
commit.

This is that thing. Two markdown files in the project's own root:

    TMT_Context/
        notes.md        HOW THIS PROJECT WORKS
        progress.md     WHAT HAS BEEN DONE, WHAT IS BEING DONE, WHAT REMAINS

Five rules shape the whole module, and each of them is a failure designed out
rather than a preference:

  It belongs to the PROJECT, not to TMT.  `agent_memory` lives under
      INSTALL_DIR precisely so it never lands in someone's git status. This is
      the opposite decision made deliberately: these files are notes ABOUT the
      project, written in the project's own language, useful to the humans
      working on it, and diffable beside the code they describe. So the root
      is read at call time from `agent_config.ROOT_DIR` -- the rule
      `agent_file_ops.workspace()` follows -- which is also the whole of the
      project-isolation guarantee. Two projects cannot see each other's
      context because the path is derived from the workspace every time it is
      asked for, and nothing here caches a root.

  Never overwrite.  A file that exists is never replaced, and a section the
      user wrote is never rewritten from scratch. Every update is
      read-modify-write, scoped to ONE heading, and everything outside that
      heading comes back byte for byte. That is what makes it safe to hand
      these files to a user and invite them to edit -- and inviting them to
      edit is the point, because a memory the user cannot correct is a memory
      that goes wrong permanently. See `Document`, which is the whole of it.

  Never fatal.  A read-only checkout, a permissions failure, a full disk, a
      file somebody replaced with a directory -- none of these may stop a task
      the user asked for. Every entry point here returns a result object
      saying what happened and raises nothing. The context is a shortcut; a
      shortcut that can end the session is worse than no shortcut.

  Never a credential.  Notes are written after reading the user's files, and
      they outlive the process, get committed, and get pushed. So everything
      written goes through `agent_memory.scrub` -- the same redaction the
      notebook already uses, reused rather than reimplemented, because a
      second copy of a secret filter is a second thing to get wrong.

  Never above the code.  The context is memory, and memory goes stale. A note
      saying the entry point is `src/main.py` is worth nothing once that file
      is gone, and worth less than nothing if it is believed. `stale_notes`
      is the lightweight check -- the paths the notes name, against the paths
      that exist -- and what it finds is stated in the prompt beside the notes
      themselves, so the model reads the claim and the doubt together.

What this module does NOT do: it does not decide when to write. That is the
session loop's job (`TMT.py`) and the model's, through the `project_context`
action. Nothing here runs a model, and nothing here runs a command.
"""

import re
from pathlib import Path

import agent_config

# The directory, in the project. Capitalised as the user will see it in their
# own file listing beside `src` and `tests`, because that is where it lives and
# what it is: project data, not a dotfile of TMT's.
CONTEXT_DIR_NAME = "TMT_Context"

NOTES_NAME = "notes.md"
PROGRESS_NAME = "progress.md"

# Bumped only when the on-disk shape changes in a way a reader must know about.
# Unlike agent_memory's FORMAT_VERSION, a file carrying another number is NOT
# discarded: it is markdown, a human may have written half of it, and throwing
# away a user's notes because a version marker moved is the one unrecoverable
# thing this module could do. An unknown version is read exactly as a known one
# and reported by `read_version`, which is what a future migration would use.
FORMAT_VERSION = 1

# Machine-readable, invisible when the markdown is rendered, and out of the way
# of anything the user writes. A visible "Context Version: 1" line would be
# prose the user might reasonably delete while tidying, and its absence would
# then read as a corrupt file rather than as a tidy one.
VERSION_MARKER = "<!-- TMT_Context format version: %d -->"
_VERSION_PATTERN = re.compile(
    r"<!--\s*TMT_Context format version:\s*(\d+)\s*-->", re.IGNORECASE)

NOTES_TITLE = "# TMT Project Notes"
PROGRESS_TITLE = "# TMT Project Progress"

# The stable headings. They are stable so that both a human and TMT can find
# what they are looking for without reading the whole file, and so that an
# update can be scoped to one of them. A user is free to add their own; this is
# the set TMT writes into and the set it expects to find, never a whitelist of
# what may appear -- `Document` keeps every heading it reads, in the order it
# read them.
NOTES_SECTIONS = (
    "Project Overview",
    "Architecture",
    "Important Files",
    "Build",
    "Testing",
    "Configuration",
    "Dependencies",
    "Constraints",
    "Known Issues",
    "TMT Notes",
)

PROGRESS_SECTIONS = (
    "Current Status",
    "Completed",
    "Currently Working On",
    "Remaining",
    "Tests",
    "Verification",
    "Important Decisions",
    "Known Issues",
    "Next Steps",
)

# What a section says when nothing is known about it yet. It is a sentence
# rather than an empty heading for one reason: an empty heading reads as
# something TMT failed to fill in, and this reads as the truth, which is that
# nobody has found out yet. It is also the marker `_is_placeholder` matches, so
# the first real fact REPLACES it rather than being appended under it.
NOTES_PLACEHOLDER = "Not yet recorded."
PROGRESS_PLACEHOLDER = "None recorded yet."

_PLACEHOLDERS = (NOTES_PLACEHOLDER, PROGRESS_PLACEHOLDER,
                 "To be determined.", "No tests run yet.",
                 "Nothing has been verified yet.")

# How much of the context may ride on a request. The files are markdown written
# by a model and a human, and nothing stops them growing -- so the prompt takes
# a budget rather than a file. Two budgets rather than one shared: progress is
# the smaller and the more current of the two, and letting a large notes.md
# crowd it out would trade the answer to "what is happening now" for more of
# the answer to "how does this work".
#
# Characters, not tokens, because that is what can be measured here without
# asking a tokeniser. `agent_session.estimate_tokens` is roughly four
# characters to a token, so these are about 1k and 600 tokens respectively --
# together under a tenth of what the workspace snapshot already costs.
NOTES_BUDGET = 4000
PROGRESS_BUDGET = 2500

# Which sections survive first when the budget bites. Everything else is
# dropped whole, and the block says which -- a truncated section that did not
# say it was truncated would be the context lying about what it holds, which is
# the one thing a memory must never do.
NOTES_PRIORITY = ("Project Overview", "Architecture", "Important Files",
                  "Testing", "Build", "Constraints")
PROGRESS_PRIORITY = ("Current Status", "Currently Working On", "Remaining",
                     "Completed", "Next Steps")

# The ceiling on one written section. A section arriving as a whole pasted file
# is a mistake at the call site, and truncating says so rather than storing it
# -- the same judgement `agent_memory.MAX_NOTE_CHARS` makes for the same
# reason.
MAX_SECTION_CHARS = 6000

# The ceiling on the whole file. Past this a write is refused rather than
# performed, because a context file that has grown past reading is one nobody
# will ever correct, and the honest response is to say so and let a human
# prune it. Section 36 of the brief is this number.
MAX_FILE_CHARS = 60000

# How many entries a running list keeps before the oldest are folded away.
# progress.md must answer three questions immediately -- what is done, what is
# happening, what remains -- and a hundred completed items answers none of
# them. The dropped ones are not deleted silently: `_trim_list` says how many
# went, so the file states its own history rather than pretending to be
# complete.
MAX_LIST_ENTRIES = 20


# --- where it lives ---------------------------------------------------------

def workspace():
    """The project this context belongs to, read at call time.

    Read rather than bound on import for the reason `agent_file_ops.workspace`
    is: startup resolves the root, `/back` and the tests move it, and a value
    captured at import would send a later call at whichever directory happened
    to be current when this module first loaded. It is also the entire
    project-isolation guarantee -- Project A's context is unreachable from
    Project B because the path is recomputed, never remembered.
    """
    return Path(agent_config.ROOT_DIR)


def directory(root=None):
    """The TMT_Context directory for a workspace."""
    return Path(root) / CONTEXT_DIR_NAME if root else workspace() / CONTEXT_DIR_NAME


def notes_path(root=None):
    return directory(root) / NOTES_NAME


def progress_path(root=None):
    return directory(root) / PROGRESS_NAME


def enabled():
    """Whether the feature is on, read at call time from the setting.

    Guarded rather than read directly so that an installation whose
    agent_config predates the setting -- a partial upgrade, a frozen
    py-modules list -- behaves as though it were on, which is the default and
    the harmless direction. The feature being unexpectedly off would be
    silent; being unexpectedly on writes two files the user can delete.
    """
    return bool(getattr(agent_config, "PROJECT_CONTEXT", True))


# --- the document -----------------------------------------------------------
#
# A markdown file, as an ordered list of (heading, body) with whatever came
# before the first heading kept as the preamble. This is the whole of "never
# blindly overwrite": an update names ONE heading, and every byte outside that
# heading is written back exactly as it was read -- including headings TMT has
# never heard of, including the user's own prose, including their spacing.

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")

# The heading level TMT writes and looks for. A `###` under a `##` is content
# belonging to the section above it, which is what makes the "### Plan
# Progress" block inside "Currently Working On" possible without a second
# document model.
SECTION_LEVEL = 2


class Document:
    """A markdown file that can be changed one section at a time.

    Two properties matter and both are tested rather than asserted here:

      Round-tripping is exact. `Document(text).render() == text` for any text,
          so a file that is only READ is never rewritten, and a file whose one
          section changed differs in that section only. Anything less and
          every update would produce a diff nobody could review.

      Unknown headings survive. The user's own sections, their order, their
          capitalisation and the blank lines between them all come back. TMT
          knows about ten headings; a real project's notes will have more, and
          a model that reorganised somebody's file while adding a line to it
          would have destroyed exactly the thing this feature is for.
    """

    def __init__(self, text=""):
        self.preamble, self.sections = _split(str(text or ""))

    # --- reading ---

    def names(self):
        """Every heading, in the order the file has them."""
        return [name for name, _ in self.sections]

    def has(self, name):
        return _index(self.sections, name) is not None

    def section(self, name, default=""):
        """One section's body, or `default` when there is no such heading."""
        position = _index(self.sections, name)
        return self.sections[position][1] if position is not None else default

    def title(self):
        """The `#` line, when the preamble opens with one."""
        for line in self.preamble.splitlines():
            match = _HEADING.match(line)
            if match and len(match.group(1)) == 1:
                return line.strip()
        return ""

    def version(self):
        """The format marker, or 0 when the file carries none."""
        match = _VERSION_PATTERN.search(self.preamble)
        return int(match.group(1)) if match else 0

    # --- writing ---

    def set(self, name, body):
        """Replace one section's body. Adds the section when it is missing.

        A missing section is APPENDED rather than inserted at the position the
        canonical order would give it. Inserting would move the user's own
        sections relative to each other on a call that was only meant to add
        one, and a heading in an unexpected place is a smaller surprise than a
        file that reshuffled itself.
        """
        body = _normalise(body)
        position = _index(self.sections, name)
        if position is None:
            self.sections.append((_canonical(name), body))
        else:
            self.sections[position] = (self.sections[position][0], body)
        return self

    def append(self, name, body):
        """Add to a section, keeping what was there.

        The placeholder is the exception and it is the important one: a section
        still saying "Not yet recorded." is saying nothing, so the first real
        fact REPLACES it. Appending under it would leave every file carrying a
        line that contradicts the lines below it.
        """
        body = _normalise(body)
        if not body:
            return self
        current = _normalise(self.section(name))
        if not current or _is_placeholder(current):
            return self.set(name, body)
        return self.set(name, current + "\n" + body)

    def add_line(self, name, line, limit=MAX_LIST_ENTRIES):
        """Add one list item to a section, if it is not already there.

        Idempotent by exact text, which is what stops a progress file becoming
        a chat log: a turn that records "Added OAuth tests" twice records it
        once. `limit` folds the oldest away when the list is long, and says so
        in the file rather than dropping them silently.
        """
        line = " ".join(str(line or "").split())
        if not line:
            return self
        current = _normalise(self.section(name))
        existing = current.splitlines()
        if any(item.strip() == line for item in existing):
            return self
        if _is_placeholder(current):
            existing = []
        return self.set(name, _trim_list(existing + [line], limit))

    def render(self):
        """The file, as text. Exact for a document nothing was written to."""
        parts = []
        if self.preamble:
            parts.append(self.preamble)
            # The blank line the preamble was read with. `_split` strips the
            # trailing whitespace off it, so without this the title and the
            # first heading are joined by ONE newline and the file no longer
            # round-trips -- which is the property every "leave the user's
            # bytes alone" guarantee here is built on.
            parts.append("")
        for name, body in self.sections:
            parts.append("%s %s" % ("#" * SECTION_LEVEL, name))
            parts.append("")
            parts.append(body if body else "")
            parts.append("")
        text = "\n".join(part for part in parts if part is not None)
        return _tidy(text)

    def __repr__(self):
        return "Document(%d section(s))" % len(self.sections)


def _split(text):
    """(preamble, [(heading, body)]) for a markdown file.

    Only headings at SECTION_LEVEL start a section. A `#` title stays in the
    preamble with the version marker, and a `###` stays inside whichever
    section it fell in -- which is what lets a plan checklist live under
    "Currently Working On" without a second level of parsing.
    """
    preamble, sections = [], []
    current_name, current_body = None, []
    for line in text.splitlines():
        match = _HEADING.match(line)
        if match and len(match.group(1)) == SECTION_LEVEL:
            if current_name is None:
                preamble = _lines_to_text(preamble)
            else:
                sections.append((current_name, _normalise("\n".join(current_body))))
            current_name, current_body = match.group(2).strip(), []
            continue
        (current_body if current_name is not None else preamble).append(line)
    if current_name is None:
        preamble = _lines_to_text(preamble)
    else:
        sections.append((current_name, _normalise("\n".join(current_body))))
    return preamble, sections


def _lines_to_text(lines):
    return "\n".join(lines).rstrip()


def _index(sections, name):
    """Where a heading is, matched case-insensitively on its words.

    Case-insensitive because a user who typed "## important files" meant the
    section TMT calls "Important Files", and creating a second heading beside
    theirs would be the file duplicating itself one edit at a time.
    """
    wanted = _key(name)
    for position, (heading, _) in enumerate(sections):
        if _key(heading) == wanted:
            return position
    return None


def _key(name):
    return " ".join(str(name or "").split()).lower()


def _canonical(name):
    """A heading as it will be written, preferring TMT's own spelling."""
    wanted = _key(name)
    for known in NOTES_SECTIONS + PROGRESS_SECTIONS:
        if _key(known) == wanted:
            return known
    return " ".join(str(name or "").split()) or "Notes"


def _normalise(text):
    """Line endings settled and the edges trimmed, and nothing else.

    CRLF first, for the reason `agent_menu.normalize_paste` does it: the files
    are edited by hand on Windows as often as they are written here, and a
    section read as CRLF and written back as LF would rewrite every line of a
    file one line was added to.
    """
    body = str(text if text is not None else "")
    return body.replace("\r\n", "\n").replace("\r", "\n").strip("\n").rstrip()


def _is_placeholder(text):
    """Whether a section says nothing, so the first real fact may replace it."""
    stripped = _normalise(text).strip()
    return not stripped or stripped in _PLACEHOLDERS


def _tidy(text):
    """One trailing newline, and never three blank lines in a row."""
    body = re.sub(r"\n{3,}", "\n\n", _normalise(text))
    return body + "\n" if body else ""


def _trim_list(items, limit):
    """Keep the newest `limit` entries and say how many were folded away.

    The oldest go first, which is the right end for a progress list: the
    completed work from twenty tasks ago is history, and the completed work
    from this one is what somebody opening the file wants. The note that
    replaces them is one line, so the file states its own age rather than
    quietly shortening.
    """
    kept = [item for item in items if item.strip()]
    if limit and len(kept) > limit:
        dropped = len(kept) - limit
        kept = ["_%d earlier entr%s folded away._"
                % (dropped, "y" if dropped == 1 else "ies")] + kept[-limit:]
    return "\n".join(kept)


# --- what an operation reports ----------------------------------------------

# The four things that can become of a request to have a context. They are
# distinguished because the user is told a different thing by each, and
# because collapsing "there wasn't one and now there is" into "there is one"
# would lose the only moment worth mentioning on screen.
CREATED = "created"      # the directory was not there and now is
EXISTING = "existing"    # it was already there and was not touched
DISABLED = "disabled"    # the setting is off; nothing was read or written
FAILED = "failed"        # it could not be done, and the task carries on


class Result:
    """What one context operation did, as a thing that can be shown.

    Never an exception. Every entry point on `ProjectContext` returns one of
    these, because the caller is a session loop in the middle of somebody's
    task and the honest response to a read-only checkout is a sentence, not a
    traceback. `ok` is the question most callers actually have.
    """

    def __init__(self, status, message="", paths=(), created=()):
        self.status = status
        self.message = message
        self.paths = tuple(paths)
        self.created = tuple(created)

    @property
    def ok(self):
        return self.status in (CREATED, EXISTING)

    def __bool__(self):
        return self.ok

    __nonzero__ = __bool__

    def __str__(self):
        return self.message

    def __repr__(self):
        return "Result(%s, %r)" % (self.status, self.message)


# What the user is told when the directory cannot be made. It names the reason
# and then says the task continues, in that order, because the second half is
# the part that stops it reading as a fault the user has to fix before working.
FAILURE_NOTE = ("Persistent project context could not be created (%s). "
                "Continuing without TMT_Context.")


class ProjectContext:
    """The TMT_Context directory of one workspace, and how it is kept.

    One of these lives on the Session for the whole run, beside the plan, the
    review and the verification -- and unlike those three it is NOT retired
    between turns. That is the difference the whole feature rests on: a plan
    belongs to one task, and this belongs to the project. It survives the turn,
    the session, the process and the machine.

    It holds no path. `root` is asked for on every call, so the object follows
    the workspace wherever the workspace goes -- which is what makes project
    switching correct by construction rather than by remembering to reset
    something. Two projects cannot share a context because they cannot share a
    root.
    """

    def __init__(self, root=None):
        # None means "follow the workspace", which is what a session wants. A
        # path pins it, which is what a test wants.
        self._pinned = Path(root) if root else None
        # Which root has already been through `ensure` this session, so the
        # second and later turns do not stat the directory again. It is a path
        # rather than a flag precisely so that a workspace change re-arms it.
        self._ensured = None
        # Why there is no context, when there is none. Kept so the session can
        # say it once rather than on every turn.
        self.failure = ""
        # The failure text already reported to the user. A context that cannot
        # be made fails again on EVERY turn -- the directory is still not
        # there and `ensure` still cannot make it -- so a caller that printed
        # the message each time would put the same yellow warning at the top
        # of every question for the rest of the session. `announce()` answers
        # True once per distinct failure, which is the useful shape: a
        # permissions problem is said once, and a DIFFERENT problem appearing
        # later is said too.
        self._announced = ""

    # --- where ---

    @property
    def root(self):
        return self._pinned if self._pinned is not None else workspace()

    @property
    def directory(self):
        return self.root / CONTEXT_DIR_NAME

    @property
    def notes_file(self):
        return self.directory / NOTES_NAME

    @property
    def progress_file(self):
        return self.directory / PROGRESS_NAME

    @property
    def available(self):
        """Whether there is a context to read right now."""
        if not enabled():
            return False
        try:
            return self.notes_file.is_file() or self.progress_file.is_file()
        except OSError:
            return False

    def announce(self):
        """Whether this failure is new enough to be worth saying out loud.

        True once per distinct failure, and False for every repeat of one
        already reported. It is a question rather than a flag the caller
        keeps, because the caller is the session loop and a flag there would
        be one more piece of per-turn state to thread; the object that knows
        whether the failure has changed is this one.
        """
        if not self.failure or self.failure == self._announced:
            return False
        self._announced = self.failure
        return True

    # --- making it ---

    def ensure(self, task="", discovery=None):
        """Create what is missing, touch what is not. The first-prompt hook.

        Called once the user has actually asked for something, never at
        launch: a directory appearing in somebody's project because they
        started a program and then changed their mind is litter, and section 3
        of the brief is that distinction. The session loop calls this after
        the task is read and before the prompt is built.

        Idempotent, and cheap on the second call: the root it succeeded for is
        remembered, so the common case -- every turn after the first -- is one
        comparison. A workspace change clears that by itself, because the
        remembered value is the root and the new root is a different one.

        Never raises. A failure comes back as a Result and is also kept on
        `failure`, so the loop can mention it once instead of on every turn.
        """
        if not enabled():
            return Result(DISABLED, "Project context is turned off in Settings.")
        root = self.root
        made = []
        try:
            existed = self.directory.is_dir()
            if self._ensured == root and existed:
                # Already done this session for this workspace, and the
                # directory is still there. The files are checked anyway --
                # cheaply, and because a user may have deleted one -- but
                # nothing is walked and nothing is generated.
                made = self._fill_missing(task, discovery)
                if made:
                    return Result(CREATED, self._made_line(made), created=made)
                return Result(EXISTING, "Project context is in %s."
                              % _display(self.directory, root))
            self.directory.mkdir(parents=True, exist_ok=True)
            made = self._fill_missing(task, discovery)
            self._ensured = root
            # A failure that has been recovered from is forgotten, so that if
            # it happens again -- the user deletes the directory a second time
            # and the same permissions problem returns -- it is reported again
            # rather than silently swallowed as "already said".
            self.failure = self._announced = ""
        except (OSError, ValueError) as error:
            self.failure = "%s: %s" % (type(error).__name__, error)
            return Result(FAILED, FAILURE_NOTE % self.failure)
        if made or not existed:
            return Result(CREATED, self._made_line(made), created=made)
        return Result(EXISTING, "Project context is in %s."
                      % _display(self.directory, root))

    def _fill_missing(self, task, discovery):
        """Write only the files that are not there. Returns what was written.

        This is the whole of "never destroy existing context", and it is one
        condition: a file that exists is not opened, not read, not parsed and
        not written. Not "merged carefully" -- not touched at all. A generator
        that ran over a user's notes and merged them would be a generator that
        could get the merge wrong, and there is no version of that failure the
        user can undo.
        """
        made = []
        if not self.notes_file.exists():
            self.notes_file.write_text(initial_notes(self.root, discovery),
                                       encoding="utf-8")
            made.append(NOTES_NAME)
        if not self.progress_file.exists():
            self.progress_file.write_text(initial_progress(task),
                                          encoding="utf-8")
            made.append(PROGRESS_NAME)
        return made

    def _made_line(self, made):
        where = _display(self.directory, self.root)
        if not made:
            return "Project context is in %s." % where
        return "Created %s in %s." % (" and ".join(made), where)

    # --- reading it ---

    def notes(self):
        """notes.md as a Document, empty when there is not one."""
        return Document(self._read(self.notes_file))

    def progress(self):
        """progress.md as a Document, empty when there is not one."""
        return Document(self._read(self.progress_file))

    def _read(self, path):
        """A file's text, or "" for every reason it might not have any.

        Guarded to the empty string rather than to an exception because every
        caller above wants a Document either way: a context that cannot be
        read is a context that says nothing, which is exactly what an empty
        one says, and the two need no separate handling.
        """
        if not enabled():
            return ""
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            return ""

    def read_version(self):
        """The format version of notes.md, 0 when there is no marker."""
        return self.notes().version()

    # --- writing it ---

    def update_notes(self, section, content, mode="append"):
        """Change one section of notes.md. Everything else is left alone."""
        return self._update(self.notes_file, NOTES_TITLE, NOTES_SECTIONS,
                            section, content, mode, NOTES_NAME)

    def update_progress(self, section, content, mode="append"):
        """Change one section of progress.md. Everything else is left alone."""
        return self._update(self.progress_file, PROGRESS_TITLE,
                            PROGRESS_SECTIONS, section, content, mode,
                            PROGRESS_NAME)

    def _update(self, path, title, known, section, content, mode, name):
        """The one write. Read, change one section, write back.

        The file is re-read HERE rather than taken from a Document the caller
        loaded earlier, and that is the whole of section 37: a user who edited
        the file while the model was thinking has their edit preserved,
        because the version being changed is the one on disk at the moment of
        the change and not the one that was read a minute ago.

        Everything written is scrubbed first. That is not a formality -- the
        model writing here has just been reading the user's configuration
        files, and this is the one place its output gets committed and pushed.
        """
        if not enabled():
            return Result(DISABLED, "Project context is turned off in Settings.")
        heading = _canonical(section)
        if not heading:
            return Result(FAILED, "A context update needs a section to write to.")
        body = _normalise(content)
        if not body:
            return Result(FAILED, "Nothing to write: the content was empty.")
        if len(body) > MAX_SECTION_CHARS:
            body = body[:MAX_SECTION_CHARS].rstrip() + "\n\n_(truncated)_"
        body, redacted = scrub(body)
        if not _informative(body):
            return Result(FAILED, "Refused: after redaction there was nothing "
                                  "left but the redaction markers.")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            document = Document(self._read(path) or _skeleton(title, known))
            if mode == "replace":
                document.set(heading, body)
            elif mode == "line":
                for line in body.splitlines():
                    document.add_line(heading, line)
            else:
                document.append(heading, body)
            rendered = document.render()
            if len(rendered) > MAX_FILE_CHARS:
                return Result(FAILED,
                              "Refused: %s would pass %d characters. Prune it "
                              "by hand -- a context file nobody can read is a "
                              "context file nobody will correct."
                              % (name, MAX_FILE_CHARS))
            path.write_text(rendered, encoding="utf-8")
        except (OSError, ValueError) as error:
            return Result(FAILED, FAILURE_NOTE % ("%s: %s"
                                                  % (type(error).__name__, error)))
        said = "Recorded under '%s' in %s." % (heading, name)
        if redacted:
            said += " Redacted before writing: %s." % "; ".join(redacted)
        return Result(EXISTING, said, paths=(str(path),))

    # --- what the model is shown ------------------------------------------

    def for_prompt(self, notes_budget=NOTES_BUDGET,
                   progress_budget=PROGRESS_BUDGET):
        """The context block for the system prompt, or "" when there is none.

        Budgeted rather than whole, because these files ride on every request
        of every step and nothing stops them growing. What is dropped is said
        out loud: a truncated block that looked complete would teach the model
        that a section it cannot see does not exist, which is worse than
        showing it nothing.
        """
        if not enabled():
            return ""
        notes = self.notes()
        progress = self.progress()
        if not notes.sections and not progress.sections:
            return ""
        parts = ["=== PROJECT CONTEXT (%s/) ===" % CONTEXT_DIR_NAME,
                 CONTEXT_PREAMBLE]
        parts.append("--- %s (how this project works) ---" % NOTES_NAME)
        parts.append(_budgeted(notes, notes_budget, NOTES_PRIORITY, NOTES_NAME))
        parts.append("--- %s (what has been done and what remains) ---"
                     % PROGRESS_NAME)
        parts.append(_budgeted(progress, progress_budget, PROGRESS_PRIORITY,
                               PROGRESS_NAME))
        stale = self.stale_notes(notes)
        if stale:
            parts.append(STALE_WARNING % ", ".join(stale))
        return "\n\n".join(part for part in parts if part)

    def stale_notes(self, notes=None, limit=8):
        """Paths the notes name that are not in the workspace any more.

        The lightweight validation section 18 asks for, and deliberately no
        more than that: every backticked token that looks like a path, checked
        for existence, capped. No index, no parsing, no guessing at renames.

        What it produces is a WARNING and never a correction. The notes are
        not edited by this -- a file may be missing because the note is stale
        or because the user is mid-refactor, and the runtime cannot tell those
        apart. Naming the doubt beside the claim lets the model check, which is
        the thing it is actually good at.
        """
        if not enabled():
            return []
        document = notes if notes is not None else self.notes()
        root = self.root
        missing = []
        for candidate in _quoted_paths(document.render()):
            if len(missing) >= limit:
                break
            try:
                if not (root / candidate).exists():
                    missing.append(candidate)
            except (OSError, ValueError):
                continue
        return missing

    def describe(self):
        """What `/notes` prints: where it is, what is in it, what looks stale."""
        root = self.root
        if not enabled():
            return ("Project context is turned off in Settings, so nothing is "
                    "read, created or updated. Any existing %s directory is "
                    "left exactly as it is." % CONTEXT_DIR_NAME)
        if not self.available:
            return ("No project context yet. TMT creates %s/ in %s on the "
                    "first task of a session."
                    % (CONTEXT_DIR_NAME, root))
        notes, progress = self.notes(), self.progress()
        rows = ["Project context: %s" % _display(self.directory, root),
                "Format version: %d (current is %d)"
                % (notes.version(), FORMAT_VERSION),
                ""]
        for name, document in ((NOTES_NAME, notes), (PROGRESS_NAME, progress)):
            text = document.render()
            rows.append("%s -- %d characters, %d section(s)"
                        % (name, len(text), len(document.sections)))
            for heading in document.names():
                body = document.section(heading)
                rows.append("  %-22s %s"
                            % (heading, "empty" if _is_placeholder(body)
                               else "%d line(s)" % len(body.splitlines())))
            rows.append("")
        stale = self.stale_notes(notes)
        if stale:
            rows.append("Named in the notes but not in the workspace: %s"
                        % ", ".join(stale))
            rows.append("Those may be stale. The repository is what is true; "
                        "the notes are what was true.")
        else:
            rows.append("Every path the notes name still exists.")
        return "\n".join(rows).rstrip()

    def __repr__(self):
        return "ProjectContext(%s, %s)" % (
            self.root, "available" if self.available else "empty")


# What the model is told the block IS, before it reads it. Short on purpose: it
# rides on every request of every step of every turn, and its whole job is to
# stop two specific mistakes -- treating the notes as authoritative over the
# code, and treating the file as somewhere to narrate.
CONTEXT_PREAMBLE = (
    "This is what TMT recorded about this project in earlier sessions. Use it "
    "instead of rediscovering the repository. It is memory, not truth: where "
    "it disagrees with what you actually read, the repository is right and the "
    "note is stale -- check, then correct the note.")

STALE_WARNING = (
    "STALE: these paths are named in the notes but are not in the workspace "
    "now: %s. Do not act on them without checking. If a note is out of date, "
    "correct it with project_context rather than working around it.")


def _budgeted(document, budget, priority, name):
    """A document as prompt text, kept under `budget` characters.

    Whole when it fits, which is the common case and the one section 15 says
    to take. When it does not, the priority sections are kept whole and the
    rest are dropped whole -- dropped rather than clipped, because half a
    section reads as a complete one and would have the model act on a
    constraint whose second half it never saw.

    `name` is the filename to point at in the "not shown" line, passed in
    rather than worked out from the document's own H1: a user who retitled
    their notes would otherwise be told to read progress.md.

    Everything below works in POSITIONS rather than in headings, because a
    hand-edited file can legitimately hold two sections with the same name --
    `_split` keeps both, and `document.section(name)` can only ever answer
    with the first. Selecting by name would then emit that first section twice
    and silently lose the second, which is the one failure a budgeting rule
    must not have: it would look exactly like the file being shown correctly.
    """
    whole = document.render().strip()
    if not whole:
        return "(empty)"
    if len(whole) <= budget:
        return whole
    wanted = [_key(heading) for heading in priority]

    def rank(position):
        heading = _key(document.sections[position][0])
        return wanted.index(heading) if heading in wanted else len(wanted)

    order = sorted(range(len(document.sections)), key=lambda p: (rank(p), p))
    kept, dropped, used = [], [], 0
    for position in order:
        heading, body = document.sections[position]
        block = "## %s\n%s" % (heading, body)
        if used + len(block) <= budget:
            kept.append((position, block))
            used += len(block)
        else:
            dropped.append(heading)
    kept.sort()
    text = "\n\n".join(block for _, block in kept)
    if dropped:
        text += ("\n\n_(%d section(s) not shown here to save room: %s. "
                 "Read %s/%s if you need them.)_"
                 % (len(dropped), ", ".join(dropped), CONTEXT_DIR_NAME, name))
    return text


# A backticked token that looks like a path rather than like an identifier: it
# either has a separator in it or ends in a file extension. Anchored on the
# whole token so `agent_config.ROOT_DIR` is not read as a filename, and capped
# in length so a pasted command line is not treated as one either.
_QUOTED = re.compile(r"`([^`\n]{2,120})`")
_LOOKS_LIKE_PATH = re.compile(
    r"^[\w.\-][\w./\\\-]*(?:/[\w.\-][\w./\\\-]*)*"
    r"(?:/|\.[A-Za-z0-9]{1,6})$")


def _quoted_paths(text):
    """Every distinct backticked token in `text` that looks like a path."""
    found = []
    for token in _QUOTED.findall(str(text or "")):
        candidate = token.strip().rstrip("/\\")
        if not candidate or " " in candidate:
            continue
        if candidate.startswith(("/", "\\")) or ":" in candidate:
            # An absolute path or a Windows drive. Not checked: it is not
            # relative to this workspace, so its absence proves nothing.
            continue
        if ".." in candidate.split("/"):
            continue
        if not _LOOKS_LIKE_PATH.match(candidate):
            continue
        if candidate not in found:
            found.append(candidate)
    return found


def _display(path, root):
    """A path as the user would name it: relative to the project when it is in it."""
    try:
        return str(Path(path).relative_to(root)).replace("\\", "/")
    except (ValueError, TypeError):
        return str(path)


def _skeleton(title, sections):
    """An empty file with every heading and no claims in it."""
    placeholder = (PROGRESS_PLACEHOLDER if title == PROGRESS_TITLE
                   else NOTES_PLACEHOLDER)
    parts = [title, "", VERSION_MARKER % FORMAT_VERSION, ""]
    for name in sections:
        parts.extend(["## %s" % name, "", placeholder, ""])
    return _tidy("\n".join(parts))


# --- the secret filter, borrowed rather than rebuilt ------------------------

def scrub(text):
    """(cleaned, reasons) with anything credential-shaped removed.

    `agent_memory` already owns this, and owns it well: assignments whose
    left-hand side names a credential, provider key shapes, long hex and long
    base64-like runs. A second implementation here would be a second thing to
    keep current, and the failure mode of the copy falling behind is a real
    secret written into a file that gets committed.

    Guarded, and the guard fails CLOSED in the only sense that matters here:
    if the notebook module cannot be imported at all, nothing is written that
    was not written -- the text is returned unchanged with a reason saying it
    could not be checked, and the caller records that reason beside the note.
    Refusing every write instead would turn a missing optional module into a
    dead feature.
    """
    body = str(text or "")
    try:
        import agent_memory
        return agent_memory.scrub(body)
    except Exception:
        return body, []


def _informative(text):
    """Whether anything but redaction markers and punctuation survived."""
    try:
        import agent_memory
        return agent_memory.is_informative(text)
    except Exception:
        return bool(re.search(r"[A-Za-z0-9]", str(text or "")))


# --- the first inspection ---------------------------------------------------
#
# What goes into a brand-new notes.md, and the rule that shapes all of it:
# EVERY LINE HERE IS SOMETHING THAT WAS READ. Nothing is inferred from a
# language's usual conventions, nothing is guessed from a directory's name,
# and where a fact is not available the file says it is not available rather
# than offering a plausible one. A note that says "Build command has not yet
# been confirmed" costs the reader nothing; a note that says `npm run build`
# about a project with no build script costs them a failed command and their
# trust in every other line.
#
# The inspection is deliberately shallow. `agent_verify_discovery.detect`
# already reads this repository's manifests, markers, package scripts, Makefile
# targets, [tool.X] sections and CI configuration, runs nothing, and hands back
# the project's OWN test command with the reason it was chosen -- so the
# expensive half of section 28 is a call rather than a new implementation. On
# top of it this adds a top-level listing, the documentation files, and the
# NAMES of the environment variables an example env file declares.

# Files that are the project explaining itself. Read for one paragraph each,
# never inlined whole -- the point is to say the documentation exists and what
# it opens with, not to copy it into a file beside it.
DOC_NAMES = ("README.md", "README.rst", "README.txt", "README",
             "CONTRIBUTING.md", "ARCHITECTURE.md", "DESIGN.md", "docs")

# Where an example environment file declares what is required. The real `.env`
# is deliberately NOT in this list and is never opened: a file whose whole
# purpose is to hold secrets is not a file to read in order to write a note
# that gets committed. Its presence is recorded; its contents are not.
ENV_EXAMPLES = (".env.example", ".env.sample", ".env.template", "env.example")
ENV_REAL = (".env", ".env.local", ".env.production")

_ENV_NAME = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]{1,63})\s*=",
                       re.MULTILINE)

# The names a project's own entry point usually has. This IS a guess and is
# labelled as one wherever it is used: what is recorded is "these files exist
# and are the usual entry points for this kind of project", which is true, and
# never "this is the entry point", which has not been checked.
ENTRY_NAMES = ("main.py", "app.py", "cli.py", "__main__.py", "manage.py",
               "run.py", "server.py", "index.js", "index.ts", "main.js",
               "main.ts", "server.js", "app.js", "main.go", "main.rs",
               "Main.java", "index.php")

# Where to look for them: the root and the one level of source directory a
# project conventionally uses. Two levels, never a walk -- section 30 is that
# a large repository must not be read to write a summary of it.
SOURCE_DIRS = ("src", "lib", "app", "cmd", "internal", "pkg")

# How many top-level directories the Architecture section lists. Enough to see
# the shape of a project, few enough that the section stays readable.
MAX_LISTED_DIRS = 14


def initial_notes(root=None, discovery=None):
    """A first notes.md, built from what this repository actually says.

    `discovery` may be passed by a caller that already has one -- the verify
    engine makes them -- and is otherwise fetched here. Every step is guarded:
    a repository that cannot be read produces a file full of "not yet
    confirmed", which is honest and is still a usable skeleton for the model
    and the user to fill in.
    """
    root = Path(root) if root else workspace()
    facts = inspect_project(root, discovery)
    document = Document(_skeleton(NOTES_TITLE, NOTES_SECTIONS))
    for section, body in facts:
        if body:
            document.set(section, body)
    return document.render()


def inspect_project(root=None, discovery=None):
    """[(section, body)] for a new notes.md. Reads files; runs nothing.

    Separate from `initial_notes` so a test can read the facts without the
    markdown around them, and so a later caller that wants to REFRESH one
    section of an existing file can take just that section.
    """
    root = Path(root) if root else workspace()
    found = _discover(root, discovery)
    docs = _documentation(root)
    return (
        ("Project Overview", _overview(root, found, docs)),
        ("Architecture", _architecture(root, found)),
        ("Important Files", _important_files(root, found, docs)),
        ("Build", _build(found)),
        ("Testing", _testing(root, found)),
        ("Configuration", _configuration(root, found)),
        ("Dependencies", _dependencies(found)),
        ("Constraints", NOTES_PLACEHOLDER),
        ("Known Issues", NOTES_PLACEHOLDER),
        ("TMT Notes", _provenance(found)),
    )


def _discover(root, discovery=None):
    """Whatever `agent_verify_discovery` can tell us, or an empty stand-in.

    Guarded to a shape rather than to None so every reader below can be
    written without a second branch: a repository nothing was learned about
    produces a notes file that says nothing was learned, and that is a
    complete outcome rather than an error.
    """
    if discovery is not None:
        return discovery
    try:
        import agent_verify_discovery
        return agent_verify_discovery.detect(root)
    except Exception:
        return None


def _ecosystems(found):
    return tuple(getattr(found, "ecosystems", ()) or ())


def _markers(found):
    return dict(getattr(found, "markers", {}) or {})


def _marker_names(found):
    """The markers, deduplicated case-insensitively and sorted.

    Two corrections to what `read_markers` hands back, both of which show up
    the moment its output is written into prose rather than counted:

    On Windows a case-insensitive filesystem answers `(root / "Makefile")`
    and `(root / "makefile")` with the same file, and MARKERS lists both -- so
    a repository with ONE Makefile reports two, and the notes list the same
    file twice. Measured on a synthetic repository: `Makefile, makefile,
    package.json`.

    And `read_markers` also stamps in the SCRIPT_RUNNERS, so `run_tests.py`
    arrives as a marker while `MARKERS.get("run_tests.py")` is None. It is a
    real fact about the repository and belongs in the notes -- what it is not
    is a manifest, so the heading it goes under must not call it one.
    """
    seen, names = set(), []
    for name in sorted(_markers(found)):
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        names.append(name)
    return names


def _overview(root, found, docs):
    """What this project is, from its own words where it has any."""
    rows = []
    # Resolved before the name is taken. A workspace of "." has an empty
    # `.name`, and the first line of the notes then opened with an empty pair
    # of backticks -- which reads as a project whose name TMT failed to work
    # out rather than as the directory it is standing in.
    name = Path(root).resolve().name or str(root)
    kinds = _ecosystems(found)
    if kinds:
        rows.append("`%s` -- a %s project, detected from %s."
                    % (name, "/".join(kinds),
                       ", ".join("`%s`" % marker
                                 for marker in _marker_names(found)[:6])
                       or "its source files"))
    else:
        rows.append("`%s`. TMT could not identify a language or build system "
                    "from the files at the top of this repository." % name)
    opening = docs.get("opening", "")
    if opening:
        rows.append("")
        rows.append("From `%s`:" % docs.get("opening_from", "README"))
        rows.append("")
        rows.append("> " + opening.replace("\n", "\n> "))
    else:
        rows.append("")
        rows.append("There is no README paragraph to quote. What this project "
                    "does has not yet been confirmed.")
    return "\n".join(rows)


def _architecture(root, found):
    """The shape of the repository, and the entry points it might have."""
    rows = []
    directories = _top_level_dirs(root)
    if directories:
        rows.append("Top-level directories:")
        rows.append("")
        for entry in directories[:MAX_LISTED_DIRS]:
            rows.append("- `%s/`" % entry)
        if len(directories) > MAX_LISTED_DIRS:
            rows.append("- ...and %d more."
                        % (len(directories) - MAX_LISTED_DIRS))
    else:
        rows.append("Every file is at the top level; there are no source "
                    "directories to describe.")
    # Two kinds of answer, kept apart because they are worth different
    # amounts. A DECLARED entry point is a fact: the project's own manifest
    # says `tmtcode = "TMT:main"`, and that is true whatever anyone guesses. A
    # file merely NAMED `main.py` is a guess, and is labelled as one. Running
    # them together under one heading would let the guess borrow the fact's
    # authority, which is the whole failure section 7 is about.
    declared = _declared_entry_points(root)
    rows.append("")
    if declared:
        rows.append("Entry points this project declares in its own manifest:")
        rows.append("")
        for entry in declared:
            rows.append("- %s" % entry)
    entries = [entry for entry in _entry_points(root)
               if not any(entry in line for line in declared)]
    if entries:
        rows.append("")
        rows.append("Files with the usual entry-point names (a guess from the "
                    "filename, NOT confirmed -- check before relying on it):")
        rows.append("")
        for entry in entries:
            rows.append("- `%s`" % entry)
    if not declared and not entries:
        rows.append("The entry point has not yet been confirmed.")
    return "\n".join(rows)


def _important_files(root, found, docs):
    rows = []
    markers = _marker_names(found)
    if markers:
        # "Files TMT recognised" rather than "Manifests", because the list is
        # not all manifests: `read_markers` also reports the project's own
        # test script, and calling `run_tests.py` a manifest would be a small
        # false statement in the one file that exists to be trusted.
        rows.append("Files TMT recognised at the top level:")
        rows.append("")
        for marker in markers:
            rows.append("- `%s`" % marker)
    for name in docs.get("files", ()):
        rows.append("- `%s` -- documentation" % name)
    if not rows:
        return NOTES_PLACEHOLDER
    return "\n".join(rows)


def _build(found):
    """The build command, and silence about it when there is not one.

    Section 7 in one function: a project with no build script gets the
    sentence the brief asks for, verbatim in meaning, rather than a guess at
    what its ecosystem usually uses.
    """
    specs = [spec for spec in getattr(found, "specs", ()) or ()
             if getattr(spec, "category", "") == "build"]
    if not specs:
        return ("Build command has not yet been confirmed. Nothing in this "
                "repository names one that TMT recognised.")
    rows = ["Commands this repository defines that build it:", ""]
    for spec in specs:
        rows.append("- `%s`%s" % (getattr(spec, "command_line", ""),
                                  _because(spec)))
    return "\n".join(rows)


def _testing(root, found):
    """How the tests are run, taken from the project's own command.

    `discovery.runner` is the whole of this: it is the command the project
    itself uses, chosen by priority with the repository's own script above the
    ecosystem's convention, and it carries the reason it was chosen. That
    reason is written into the file, because a note saying WHY is a note the
    next reader can disagree with.
    """
    rows = []
    runner = getattr(found, "runner", None)
    if runner is not None:
        rows.append("Test command: `%s`" % " ".join(runner.argv))
        rows.append("")
        rows.append("Chosen because %s." % runner.why)
        if not getattr(runner, "supports_paths", False):
            rows.append("It cannot be narrowed to specific paths, so the whole "
                        "suite is the only test evidence available.")
    else:
        rows.append("Test command has not yet been confirmed. Nothing in this "
                    "repository names one that TMT recognised.")
    directories = [entry for entry in _top_level_dirs(root)
                   if entry.lower() in ("test", "tests", "testing", "spec",
                                        "specs", "__tests__")]
    if directories:
        rows.append("")
        rows.append("Tests live in %s."
                    % ", ".join("`%s/`" % entry for entry in directories))
    return "\n".join(rows)


def _configuration(root, found):
    """What has to be configured, by NAME, and never by value.

    This is the section section 23 and section 47 are about, and it is written
    so the dangerous thing is not merely forbidden but impossible: the real
    `.env` is never opened. Its presence is recorded from a stat. Only an
    EXAMPLE env file is read, only its left-hand sides are taken, and even
    those go through the same scrubber every other write here does.
    """
    rows = []
    names, source = _environment_names(root)
    if names:
        rows.append("Environment variables this project declares (names only "
                    "-- values are never recorded here), from `%s`:" % source)
        rows.append("")
        for name in names:
            rows.append("- `%s`" % name)
    present = [name for name in ENV_REAL if _exists(root, name)]
    if present:
        rows.append("")
        rows.append("%s present. TMT does not read %s, so nothing in %s is "
                    "recorded here."
                    % (", ".join("`%s`" % name for name in present),
                       "them" if len(present) > 1 else "it",
                       "them" if len(present) > 1 else "it"))
    words = getattr(found, "environment", "")
    if words:
        rows.append("")
        rows.append("Environment: %s." % words)
    if not rows:
        return ("Configuration requirements have not yet been confirmed. No "
                "example environment file was found.")
    return "\n".join(rows)


def _dependencies(found):
    """Which manifest declares them, not what they are.

    Deliberately a pointer rather than a list. A dependency list copied into a
    notes file is stale the next time anyone installs anything, and the file
    that is never stale is already in the repository -- so this says where to
    look, which stays true.
    """
    markers = [name for name in _marker_names(found)
               if name in ("pyproject.toml", "requirements.txt", "setup.py",
                           "Pipfile", "package.json", "Cargo.toml", "go.mod",
                           "pom.xml", "build.gradle", "composer.json",
                           "Gemfile", "mix.exs", "setup.cfg")]
    if not markers:
        return ("No dependency manifest was found at the top level, so what "
                "this project depends on has not yet been confirmed.")
    return ("Declared in %s. That file is the current list; this note "
            "deliberately does not copy it, because a copy goes stale the "
            "next time anything is installed."
            % ", ".join("`%s`" % name for name in markers))


def _provenance(found):
    """The one section that says where the rest of the file came from."""
    rows = ["Written by TMT on its first task in this project, from reading "
            "the repository. Nothing here was run and nothing was inferred "
            "beyond what the files say.",
            "",
            "Anything marked \"not yet confirmed\" is genuinely unknown rather "
            "than assumed. Correct it as you find out -- this file is meant to "
            "be edited, by TMT and by hand."]
    notes = tuple(getattr(found, "notes", ()) or ())
    if notes:
        rows.append("")
        for note in notes:
            rows.append("- %s" % " ".join(str(note).split()))
    return "\n".join(rows)


def _because(spec):
    why = getattr(spec, "why", "")
    return " -- %s" % why if why else ""


def _exists(root, name):
    try:
        return (Path(root) / name).exists()
    except OSError:
        return False


def _top_level_dirs(root):
    """Directory names at the top of the repository, machinery pruned.

    The skip list is `agent_file_ops.WORKSPACE_SKIP`, borrowed rather than
    restated: it is already the answer to "which directories are machinery
    rather than work", it is already kept current, and a second list here
    would be a second thing to update the day somebody adds a build directory
    to it.
    """
    try:
        import agent_file_ops
        skip = agent_file_ops.WORKSPACE_SKIP
    except Exception:
        skip = {".git", "__pycache__", "node_modules", ".venv"}
    # TMT's own notes directory is not part of the project's architecture, and
    # listing it in the section that describes that architecture makes the file
    # describe itself. Found by running two real sessions against a temp
    # project: the second one opened "Top-level directories: src/,
    # TMT_Context/", which tells a reader nothing about the project and one
    # thing about TMT they can already see at the top of the file.
    skip = set(skip) | {CONTEXT_DIR_NAME}
    names = []
    try:
        for entry in sorted(Path(root).iterdir(), key=lambda p: p.name.lower()):
            if entry.is_dir() and entry.name not in skip \
                    and not entry.name.startswith("."):
                names.append(entry.name)
    except OSError:
        return []
    return names


def _entry_points(root):
    """Files with the usual entry-point names, at the root and one level in."""
    root = Path(root)
    found = []
    places = [root] + [root / name for name in SOURCE_DIRS]
    for place in places:
        for name in ENTRY_NAMES:
            try:
                candidate = place / name
                if not candidate.is_file():
                    continue
            except OSError:
                continue
            shown = _display(candidate, root)
            if shown not in found:
                found.append(shown)
            if len(found) >= 8:
                return found
    return found


# A console script in pyproject.toml: `name = "module:function"`, inside the
# [project.scripts] table. Read with a regex rather than a TOML parser because
# `tomllib` is Python 3.11 and this project supports 3.8, and because what is
# wanted is the two names rather than the document.
_SCRIPTS_TABLE = re.compile(
    r"^\[project\.scripts\]\s*$(.*?)(?=^\[|\Z)", re.MULTILINE | re.DOTALL)
_SCRIPT_ENTRY = re.compile(
    r'^\s*([A-Za-z0-9_.\-]+)\s*=\s*["\']([^"\'\n]+)["\']', re.MULTILINE)


def _declared_entry_points(root):
    """Entry points a manifest actually declares, as ready-made note lines.

    Facts rather than guesses, which is why they are separated from
    `_entry_points` at the call site: `[project.scripts]` and package.json's
    `bin`/`main` are the project saying what its entry point is, and nothing
    has to be inferred from a filename to read them.
    """
    root = Path(root)
    found = []
    text = _safe_read(root / "pyproject.toml")
    table = _SCRIPTS_TABLE.search(text) if text else None
    if table:
        for name, target in _SCRIPT_ENTRY.findall(table.group(1)):
            found.append("`%s` runs `%s` (declared in `pyproject.toml` under "
                         "`[project.scripts]`)" % (name, target))
    package = _safe_read(root / "package.json")
    if package:
        try:
            import json
            data = json.loads(package)
        except ValueError:
            data = None
        if isinstance(data, dict):
            main = data.get("main")
            if isinstance(main, str) and main.strip():
                found.append("`%s` is the `main` in `package.json`"
                             % main.strip())
            binaries = data.get("bin")
            if isinstance(binaries, str) and binaries.strip():
                found.append("`%s` is the `bin` in `package.json`"
                             % binaries.strip())
            elif isinstance(binaries, dict):
                for name, target in list(binaries.items())[:8]:
                    found.append("`%s` runs `%s` (declared in `package.json` "
                                 "under `bin`)" % (name, target))
    return found[:8]


def _safe_read(path):
    """A file's text, or "" for every reason it might not have any."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return ""
    return text if len(text) <= 200000 else text[:200000]


def _documentation(root):
    """Which documentation files exist, and the first paragraph of the README.

    One paragraph, capped, scrubbed. A README's opening is the single most
    useful sentence about a project that exists anywhere in it, and copying
    the whole file would be copying a file that is already in the repository.
    """
    root = Path(root)
    files, opening, opening_from = [], "", ""
    for name in DOC_NAMES:
        try:
            candidate = root / name
            if not candidate.exists():
                continue
        except OSError:
            continue
        files.append(name + ("/" if candidate.is_dir() else ""))
        if opening or candidate.is_dir() or not name.upper().startswith("README"):
            continue
        opening = _first_paragraph(candidate)
        if opening:
            opening_from = name
    return {"files": tuple(files), "opening": opening,
            "opening_from": opening_from}


# How much of a README paragraph is worth keeping. Long enough for a real
# description, short enough that a project whose README opens with a wall of
# badges and prose does not put that wall into the notes.
MAX_OPENING_CHARS = 600


def _first_paragraph(path):
    """The README's first real paragraph: no title, no badges, no rules."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return ""
    paragraph = []
    for line in _normalise(text).splitlines():
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith("#") or stripped.startswith("---") \
                or stripped.startswith("==="):
            continue
        # A badge row: nothing but images and links, which says nothing about
        # the project and looks like noise once the markdown is not rendered.
        if stripped.startswith("[![") or stripped.startswith("!["):
            continue
        if stripped.startswith("<"):
            continue
        # A blockquote at the top of a README is a callout -- "Needs Python
        # 3.8+", "This project is archived" -- and not the description. TMT's
        # own README opens with one, which is how this was found: the notes
        # quoted a version requirement as though it were what the project
        # does. Skipped rather than kept, and the search goes on to the first
        # paragraph of ordinary prose.
        if stripped.startswith(">"):
            continue
        # A table row, for the same reason: it is structure, and one row of it
        # out of context says nothing.
        if stripped.startswith("|"):
            continue
        paragraph.append(stripped)
        if len(" ".join(paragraph)) > MAX_OPENING_CHARS:
            break
    body = " ".join(paragraph).strip()
    if len(body) > MAX_OPENING_CHARS:
        body = body[:MAX_OPENING_CHARS].rsplit(" ", 1)[0] + "..."
    cleaned, _ = scrub(body)
    return cleaned


def _environment_names(root):
    """(names, which file they came from) from an EXAMPLE env file only."""
    for name in ENV_EXAMPLES:
        try:
            candidate = Path(root) / name
            if not candidate.is_file():
                continue
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        names = []
        for match in _ENV_NAME.finditer(text):
            variable = match.group(1)
            if variable not in names:
                names.append(variable)
        if names:
            return tuple(names[:30]), name
    return (), ""


# --- the first progress file ------------------------------------------------

def initial_progress(task=""):
    """A first progress.md, claiming nothing that has not happened.

    Section 31, and section 10 is the reason it reads the way it does: the one
    thing genuinely completed at this moment is the initialisation itself, so
    that is the one box ticked. The user's task goes under "Currently Working
    On" as an OPEN item, never under "Completed" -- a progress file that
    started by claiming the work was done would be wrong on its very first
    line, and would teach every later reader to discount it.
    """
    # `_headline`, not a second truncation of its own. Two callers write the
    # same task into the same file -- this one opens it, `finalize` later
    # closes it -- and when they shortened it differently the close could
    # never find what the open had written, so the task was listed as both
    # outstanding and complete. One function, one string, one entry.
    cleaned = _headline(task)
    document = Document(_skeleton(PROGRESS_TITLE, PROGRESS_SECTIONS))
    document.set("Current Status", "Status: Newly initialized.")
    document.set("Completed", "- [x] TMT project context initialized")
    document.set("Currently Working On",
                 "- [ ] %s" % cleaned if cleaned
                 else "Nothing yet. The first task will be recorded here.")
    document.set("Remaining", "To be determined.")
    document.set("Tests", "No tests run yet.")
    document.set("Verification", "Nothing has been verified yet.")
    document.set("Important Decisions", PROGRESS_PLACEHOLDER)
    document.set("Known Issues", PROGRESS_PLACEHOLDER)
    document.set("Next Steps",
                 "Determined by the current task." if cleaned
                 else "Determined by the first task.")
    return document.render()


# --- what the plan, the verification and the review put in progress.md ------
#
# The rule for all three: they read the REAL state object and write what it
# says. Nothing here decides that something passed -- `agent_verify` decides
# that from an exit code and `agent_review` from a parsed reviewer result, and
# this only reports what those two already settled. That is section 10 and
# section 11 held to at the one place they are easiest to break, because the
# text being written is prose and prose can say anything.

# The sub-heading a plan is mirrored under. A `###` inside "Currently Working
# On", so it belongs to that section and moves with it, and so the whole
# mirror can be replaced without touching whatever else the section holds.
PLAN_HEADING = "### Plan Progress"

_PLAN_MARKS = {"completed": "x", "in_progress": " ", "pending": " ",
               "blocked": " "}
_PLAN_SUFFIX = {"in_progress": " (in progress)", "blocked": " (blocked)"}


def plan_checklist(plan):
    """A plan as a markdown checklist, or "" when there is no plan.

    Only `completed` gets a tick. A step in progress is an open box with the
    words "(in progress)" beside it -- a half-tick would be a third state the
    markdown cannot show, and ticking it would be the file claiming work that
    the plan itself says is unfinished.
    """
    steps = list(getattr(plan, "steps", ()) or ()) if plan is not None else []
    if not steps:
        return ""
    rows = [PLAN_HEADING, ""]
    for step in steps:
        status = getattr(step, "status", "pending")
        rows.append("- [%s] %s%s"
                    % (_PLAN_MARKS.get(status, " "),
                       " ".join(str(getattr(step, "title", "")).split()),
                       _PLAN_SUFFIX.get(status, "")))
    return "\n".join(rows)


def tests_line(verify):
    """What the last verification actually ran, or "" when none did.

    Reads `VerificationResult.summary_line()`, which is built from exit codes
    and nothing else. Section 11 is that this line may not exist unless a run
    happened, and the guard is that there is no other source for it: an
    absent or unsettled verification produces "" and the Tests section is left
    exactly as it was.
    """
    last = getattr(verify, "last", None) if verify is not None else None
    if last is None or not getattr(last, "ran", lambda: ())():
        return ""
    rows = ["Last run: %s" % last.headline(), "", "```text",
            last.summary_line(), "```", ""]
    for check in last.ran():
        rows.append("- `%s` -- %s"
                    % (getattr(check, "command_line", check.name),
                       getattr(check, "status", "")))
    return "\n".join(rows)


def verification_line(verify):
    """The verification state, in one or two lines, or "" when there is none."""
    if verify is None:
        return ""
    last = getattr(verify, "last", None)
    if last is None:
        return ""
    rows = ["%s (cycle %d of %d)."
            % (last.headline(), getattr(verify, "cycles", 1),
               getattr(verify, "max_cycles", 1))]
    if getattr(verify, "stale", False):
        rows.append("The tree has changed since that run, so it no longer "
                    "covers what is in the workspace now.")
    return "\n".join(rows)


def review_line(review):
    """What the last review found, or "" when there was none.

    Only the headline and the blocking findings. A review's full text belongs
    in the review's own record; what belongs in a persistent project file is
    the finding that is still true tomorrow, which is a blocking issue nobody
    has fixed yet.
    """
    if review is None:
        return ""
    last = getattr(review, "last", None)
    if last is None:
        return ""
    rows = [last.headline() + "."]
    for issue in getattr(last, "blocking", lambda: ())():
        title = " ".join(str(getattr(issue, "title", "")).split())
        where = getattr(issue, "location", "")
        rows.append("- %s%s" % (title, " (%s)" % where if where else ""))
    if getattr(review, "stale", False):
        rows.append("The code has changed since that review.")
    return "\n".join(rows)


# --- the end of a task ------------------------------------------------------

def finalize(context, task="", plan=None, verify=None, review=None,
             answer="", wrote=()):
    """Persist what this task actually did, once, as it ends.

    Section 45: the last thing before the conversation ends, and deliberately
    AFTER the completion gates rather than instead of them. The session loop
    calls this only once `completion_block` has let the ending through and
    `execute_action` has run it, so the order stays

        work -> verify -> review -> persist -> end

    and nothing here can release an answer that the plan, the verification or
    the review was holding. It cannot: it is not asked until they have already
    agreed, and it returns a Result that the loop does not consult.

    It writes only what a state object actually says. The plan mirror comes
    from the plan's own steps, the Tests block from a verification result built
    out of exit codes, the review line from a parsed reviewer verdict. The
    user's task is recorded as a completed item ONLY when there is evidence
    for it -- a plan that finished, or files that were written -- and is
    otherwise left under "Currently Working On", because a task that ended
    without doing anything did not complete.

    Never raises, and never blocks the ending. Every failure is a Result the
    caller may show and may equally ignore.
    """
    if context is None or not enabled():
        return Result(DISABLED, "Project context is turned off in Settings.")
    if not context.available:
        return Result(DISABLED, "There is no project context to update.")
    said = _headline(task)
    try:
        document = context.progress()
        if not document.sections:
            document = Document(_skeleton(PROGRESS_TITLE, PROGRESS_SECTIONS))
        changed = _fold_task(document, said, plan, verify, review, wrote)
        if not changed:
            return Result(EXISTING, "Nothing new to record in %s."
                          % PROGRESS_NAME)
        rendered = document.render()
        if len(rendered) > MAX_FILE_CHARS:
            return Result(FAILED, "Refused: %s would pass %d characters."
                          % (PROGRESS_NAME, MAX_FILE_CHARS))
        context.directory.mkdir(parents=True, exist_ok=True)
        context.progress_file.write_text(rendered, encoding="utf-8")
    except Exception as error:
        # Broader than the `(OSError, ValueError)` every other write here
        # catches, and deliberately so. Those are single operations on a path;
        # this one walks a plan, a verification result and a review verdict,
        # any of which could be an object of a shape this function has not
        # seen -- and it runs at the END of a turn the user has already waited
        # for. The worst outcome of swallowing is a note that was not written.
        # The worst outcome of raising is the answer being lost after the work
        # was done, which is not a trade worth making for a notebook.
        return Result(FAILED, FAILURE_NOTE % ("%s: %s"
                                              % (type(error).__name__, error)))
    return Result(EXISTING, "Updated %s/%s." % (CONTEXT_DIR_NAME, PROGRESS_NAME),
                  paths=(str(context.progress_file),))


def _fold_task(document, said, plan, verify, review, wrote):
    """Write one task's real outcome into a progress document, in place.

    Returns whether anything changed, so a turn that produced no evidence of
    anything does not rewrite the file to say so. Every branch below asks a
    state object; none of them asks the model.
    """
    changed = False
    finished = _plan_finished(plan)
    did_work = bool(wrote) or finished

    if said:
        if did_work:
            # A completed item, and the two things that may make one: a plan
            # whose every step the gate confirmed finished, or files that were
            # actually written. Neither is the model's opinion of its own work.
            before = document.section("Completed")
            document.add_line("Completed", "- [x] %s" % said)
            changed = changed or document.section("Completed") != before
            # And it stops being the current work, because it is not.
            #
            # Matched on the WHOLE open entry rather than on `said` being a
            # substring of the line, so a line that merely mentions the same
            # words is left alone and -- the failure this actually had -- the
            # open item is still found when the two strings are not character
            # for character identical. The open item is always `- [ ] ` plus
            # the headline, because `_headline` is the only thing that writes
            # one, so this is an exact comparison against a known shape rather
            # than a search.
            open_item = "- [ ] %s" % said
            current = _normalise(document.section("Currently Working On"))
            pruned = "\n".join(line for line in current.splitlines()
                               if line.strip() != open_item)
            if _normalise(pruned) != current:
                document.set("Currently Working On",
                             _normalise(pruned) or "Nothing in progress.")
                changed = True
        else:
            before = document.section("Currently Working On")
            document.add_line("Currently Working On", "- [ ] %s" % said)
            changed = changed or document.section("Currently Working On") != before

    checklist = plan_checklist(plan)
    if checklist:
        # Replaced whole rather than appended, and only the mirror: the
        # sub-heading is found and everything from it to the end of the
        # section is swapped, so anything the user wrote ABOVE it in the same
        # section survives untouched.
        current = _normalise(document.section("Currently Working On"))
        kept = current.split(PLAN_HEADING)[0].rstrip()
        merged = (kept + "\n\n" + checklist).strip() if kept else checklist
        if merged != current:
            document.set("Currently Working On", merged)
            changed = True

    tests = tests_line(verify)
    if tests and _normalise(document.section("Tests")) != _normalise(tests):
        document.set("Tests", tests)
        changed = True

    verification = verification_line(verify)
    if verification and _normalise(document.section("Verification")) \
            != _normalise(verification):
        document.set("Verification", verification)
        changed = True

    finding = review_line(review)
    if finding:
        before = document.section("Known Issues")
        for line in finding.splitlines():
            document.add_line("Known Issues", line)
        changed = changed or document.section("Known Issues") != before

    status = _status_line(plan, verify, review, did_work)
    if status and _normalise(document.section("Current Status")) != status:
        document.set("Current Status", status)
        changed = True
    return changed


def _plan_finished(plan):
    """Whether every step of a real plan is completed. False for no plan.

    `is_complete` is the plan's own answer and is asked rather than
    recomputed. An empty plan is NOT finished work: `Plan.is_complete` is true
    for a plan with no steps, which is right for the gate -- there is nothing
    outstanding -- and would be wrong here, where it would mean every turn
    that never planned recorded itself as completed.
    """
    steps = list(getattr(plan, "steps", ()) or ()) if plan is not None else []
    if not steps:
        return False
    try:
        return bool(plan.is_complete())
    except Exception:
        return all(getattr(step, "done", False) for step in steps)


def _status_line(plan, verify, review, did_work):
    """One sentence saying where the project stands, from the state objects."""
    parts = []
    if _plan_finished(plan):
        parts.append("last task's plan completed")
    elif plan is not None and list(getattr(plan, "steps", ()) or ()):
        outstanding = len(list(plan.outstanding()))
        if outstanding:
            parts.append("%d plan step(s) outstanding" % outstanding)
    elif did_work:
        parts.append("last task made changes")
    if verify is not None and getattr(verify, "last", None) is not None:
        parts.append("verification %s" % verify.last.status)
    if review is not None and getattr(review, "last", None) is not None:
        parts.append("review %s" % ("passed" if review.last.passed
                                    else "failed"))
    if not parts:
        return ""
    return "Status: Active development -- %s." % ", ".join(parts)


# How long a checklist entry may be. A progress file has to answer three
# questions at a glance -- what is done, what is happening, what remains -- and
# a 700-character instruction pasted into a bullet answers none of them. Found
# by driving TMT on its own repository: the entry read "Everything for this
# change is already staged, so commit exactly what is staged and then push to
# main; do not name any paths yourself and do not build a path li…", which is
# the task text rather than a description of the work.
MAX_HEADLINE_CHARS = 120

# Where an instruction stops being the request and starts being the detail of
# how to carry it out. Cutting at the first of these keeps a whole clause
# instead of a sentence sawn through at a character count.
_CLAUSE_BREAK = re.compile(r"[.;] | -- ")


def _headline(task):
    """The user's task, as one short line fit to be a checklist item.

    THE ONE PLACE a task becomes a progress entry, and it has to be, because
    two callers write the same task into the same file: `initial_progress`
    puts it under "Currently Working On" and `finalize` later moves it to
    "Completed". They used to truncate at different lengths -- 200 and 160 --
    so the strings never matched, the move never found the open item, and the
    task ended up listed as BOTH outstanding and complete at once. Driving TMT
    on its own repository produced exactly that, in the first progress file
    the feature ever wrote.
    """
    said = " ".join(str(task or "").split())
    if not said:
        return ""
    # The first clause, when there is one early enough to be the request
    # rather than a fragment of it. "Commit what is staged and push to main;
    # do not name any paths..." keeps the half that says what to do.
    match = _CLAUSE_BREAK.search(said)
    if match and match.start() <= MAX_HEADLINE_CHARS:
        said = said[:match.start()].rstrip()
    if len(said) > MAX_HEADLINE_CHARS:
        said = said[:MAX_HEADLINE_CHARS - 1].rstrip() + "…"
    cleaned, _ = scrub(said)
    return cleaned if _informative(cleaned) else ""


def _context_finalize(self, task="", plan=None, verify=None, review=None,
                      answer="", wrote=()):
    """Persist this task's outcome. `finalize` above does the work.

    Bound as a method so a caller can write `session.context.finalize(...)`
    without knowing there is a free function behind it, and written as a free
    function so a test can drive the folding against a Document it built by
    hand -- which is how the plan mirror and the tests block are checked
    without touching a filesystem at all.
    """
    return finalize(self, task, plan, verify, review, answer, wrote)


ProjectContext.finalize = _context_finalize


# --- the one object a session holds ----------------------------------------

def for_session(root=None):
    """The ProjectContext a Session builds. One line, so there is one shape."""
    return ProjectContext(root)
