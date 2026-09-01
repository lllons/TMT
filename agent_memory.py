"""Per-project memory: what TMT learned last session, still there this one.

A session used to begin knowing nothing. Everything worked out about a project
-- where the tests live, which module owns the width, the fact that heredocs
corrupt source here -- was rebuilt from the snapshot every time, or lost. This
is the small durable notebook that survives the process.

Three rules shape the whole file, and each of them is a failure that was
deliberately designed out:

  Never in the workspace.  Memory is TMT's own state, so it lives under
      INSTALL_DIR beside .tmt_key and .tmt_effort. A memory file written into
      the project would show up in the user's git status, get committed, and
      then be read back as if it were part of their code. A test in this repo
      asserts none of TMT's state lands in the workspace; this obeys it.

  Never fatal.  The file is a convenience, not a record of truth. Anything
      unreadable, truncated, hand-edited or written by a future version is
      discarded and started empty rather than raised. A notebook must not be
      able to stop a session opening, and deleting it must always be a safe
      thing for a user to do.

  Never a credential.  Notes are written by a model that has just been reading
      the user's files, and a note is the one thing here that outlives the
      process. So everything is scanned on the way in and secret-shaped text is
      redacted before it reaches the disk -- see _scrub() below. Storing a
      redacted secret quietly is fine. Storing a real one quietly is not, which
      is why every redaction is reported back in remember()'s return string.
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import agent_config

# Where the notebooks live, one file per workspace. A directory rather than a
# single file so that two projects cannot make each other's memory unreadable,
# and so deleting one project's memory is a plain `rm` of one file.
MEMORY_DIR_NAME = ".tmt_memory"

# Bumped only when the on-disk shape changes incompatibly. A file carrying any
# other number is discarded rather than migrated: the contents are notes, not
# data anyone paid for, and a wrong migration is worse than an empty notebook.
FORMAT_VERSION = 1

# The ceiling. Without one the file grows for the life of the project and is
# eventually read into a prompt whole. The oldest go first, and remember() says
# so out loud -- silently dropping a note the user watched TMT take would be
# the memory lying about what it holds.
MAX_ENTRIES = 200

# One note is a sentence or two. A note arriving as a whole pasted file is a
# mistake at the call site, and truncating says so rather than storing it.
MAX_NOTE_CHARS = 2000

# What recall() shows when the caller does not choose.
DEFAULT_RECALL_LIMIT = 20

REDACTED = "[redacted]"


# --- where it lives ---------------------------------------------------------

def _install_dir():
    """TMT's own directory, read at call time.

    Read rather than bound on import for the same reason ROOT_DIR is: the tests
    redirect agent_config.INSTALL_DIR at a temporary directory, and a value
    captured on import would send them straight at the developer's real memory
    file.
    """
    return Path(agent_config.INSTALL_DIR)


def _workspace():
    """The project this memory belongs to, read at call time."""
    return Path(agent_config.ROOT_DIR).resolve()


def memory_dir():
    """The directory holding every project's notebook. Not created here."""
    return _install_dir() / MEMORY_DIR_NAME


def _workspace_key(path):
    """A short stable name for a workspace path.

    A hash rather than the path itself because a path is not a filename: it has
    separators, a drive letter, spaces and characters Windows refuses. The
    readable path is stored *inside* the file instead, so a human can still
    tell whose notebook they are looking at.
    """
    text = str(Path(path).resolve())
    if os.name == "nt":
        # Windows reaches the same directory under either case, so C:\Coding
        # and c:\coding must not get two separate notebooks.
        text = text.lower()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def memory_path():
    """The memory file for the current workspace. Nothing is created."""
    return memory_dir() / ("%s.json" % _workspace_key(_workspace()))


# --- secret safety ----------------------------------------------------------
#
# Reimplemented here rather than imported from agent_providers, which has its
# own redact() for HTTP error bodies. That one guards a string on its way to a
# screen and lives a few seconds; this one guards a string on its way to a file
# that outlives the process, so the two want different aggression and neither
# should be able to loosen the other by being edited.

# Provider key shapes, spelled out. These are unambiguous: nothing that is not
# a credential looks like this.
_KEY_SHAPES = re.compile(
    r"""(
        sk-[A-Za-z0-9_\-]{8,}                  # OpenAI, Anthropic, OpenRouter
      | AIza[A-Za-z0-9_\-]{10,}                # Google
      | gh[pousr]_[A-Za-z0-9]{16,}             # GitHub tokens
      | github_pat_[A-Za-z0-9_]{20,}           # GitHub fine-grained tokens
      | xox[baprs]-[A-Za-z0-9\-]{10,}          # Slack
      | AKIA[0-9A-Z]{16}                       # AWS access key id
      | eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}   # JWT
    )""",
    re.VERBOSE,
)

# Anything with the density of a key and none of the shape of prose. Both
# require length plus mixed character classes, because the cheap version of
# this rule -- "a long run of letters and digits" -- redacts ordinary
# identifiers and makes the memory useless.
_LONG_HEX = re.compile(r"\b[0-9a-fA-F]{32,}\b")
_LONG_BASE64ISH = re.compile(r"\b(?=[A-Za-z0-9+_=-]*[a-z])"
                             r"(?=[A-Za-z0-9+_=-]*[A-Z])"
                             r"(?=[A-Za-z0-9+_=-]*[0-9])"
                             r"[A-Za-z0-9+_=-]{32,}\b")

# The words that make a name a credential's name. Matched against the *parts*
# of the name, never as a substring: "auth" as a substring is inside "author",
# and redacting the value of author= would be a false positive that costs the
# user real information.
_SECRET_WORDS = {
    "key", "keys", "apikey", "token", "tokens", "secret", "secrets",
    "password", "passwords", "passwd", "pwd", "credential", "credentials",
    "cred", "creds", "auth", "bearer", "authorization", "access",
}

_CAMEL_BREAK = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# name = value / name: value, where the value is one unbroken run. The
# whitespace test is the discriminator that keeps English out: "the key: is
# important" has a value with spaces in it and is prose, "API_KEY: abc123def"
# does not and is not.
_ASSIGNMENT = re.compile(
    r"""(?P<name>[A-Za-z_][A-Za-z0-9_.\-]*)
        (?P<gap>\s*[:=]\s*)
        (?P<quote>["']?)
        (?P<value>[^\s"']{4,})
        (?P=quote)""",
    re.VERBOSE,
)


def _name_is_secretish(name):
    """Whether an assignment's left-hand side names a credential.

    Split on separators and on camelCase, then compare whole parts. Substring
    matching is what makes this rule useless: it catches author, monkey,
    keyboard and access_log along with the things it should.
    """
    parts = re.split(r"[_.\-]+", _CAMEL_BREAK.sub("_", name))
    return any(part.lower() in _SECRET_WORDS for part in parts if part)


def _scrub(text):
    """Return (cleaned_text, reasons) with anything credential-shaped removed.

    Redaction rather than refusal, because the note around a secret is usually
    the part worth keeping: "the OpenRouter key is in .tmt_key, it starts
    sk-or-..." is a genuinely useful note once the key itself is gone. The
    caller refuses only when nothing survives.
    """
    reasons = []

    def note(reason):
        if reason not in reasons:
            reasons.append(reason)

    def assignment(match):
        if not _name_is_secretish(match.group("name")):
            return match.group(0)
        note("an assignment to %r, which names a credential"
             % match.group("name"))
        return "%s%s%s" % (match.group("name"), match.group("gap"), REDACTED)

    # Assignments first: the value is redacted whole, so a key that would also
    # have matched a shape below is already gone and is reported by its name,
    # which is the more useful thing to tell the caller.
    cleaned = _ASSIGNMENT.sub(assignment, text)

    def shape(match):
        note("a value shaped like a provider API key")
        return REDACTED

    cleaned = _KEY_SHAPES.sub(shape, cleaned)

    def run(kind):
        def replace(match):
            note("a long %s run, which is the shape of a secret rather than "
                 "of prose" % kind)
            return REDACTED
        return replace

    cleaned = _LONG_HEX.sub(run("hexadecimal"), cleaned)
    cleaned = _LONG_BASE64ISH.sub(run("base64-like"), cleaned)
    return cleaned, reasons


def _is_informative(text):
    """Whether anything but redaction markers and punctuation is left.

    A note that was nothing but a pasted key comes out of _scrub as
    "[redacted]" and is worth refusing: storing it teaches the next session
    nothing and only records that a secret was once handled here.
    """
    remainder = text.replace(REDACTED, " ")
    return bool(re.search(r"[A-Za-z0-9]", remainder))


# The same two functions, under names another module may call. They exist
# because `agent_context` writes markdown into the user's own repository --
# files that get committed and pushed -- and it needs exactly this filter. A
# second copy of a secret scrubber over there would be a second thing to keep
# current, and the failure mode of the copy falling behind is a real credential
# written into a file somebody publishes.
#
# Thin wrappers rather than a rename, so nothing inside this module changes and
# every existing call site keeps meaning what it meant. The privacy of `_scrub`
# was never about the redaction being secret; it was about it belonging to the
# notebook. It belongs to both now, and says so here.

def scrub(text):
    """(cleaned, reasons) with anything credential-shaped removed.

    The public name for `_scrub`. See its docstring for what is redacted and
    why redaction rather than refusal.
    """
    return _scrub(str(text or ""))


def is_informative(text):
    """Whether anything but redaction markers and punctuation survived."""
    return _is_informative(str(text or ""))


# --- the file ---------------------------------------------------------------

def _empty(workspace):
    return {"version": FORMAT_VERSION, "workspace": str(workspace),
            "next_id": 1, "entries": []}


def _valid_entry(entry):
    return (isinstance(entry, dict)
            and isinstance(entry.get("id"), str) and entry["id"]
            and isinstance(entry.get("note"), str)
            and isinstance(entry.get("kind"), str)
            and isinstance(entry.get("timestamp"), str)
            and isinstance(entry.get("tags"), list)
            and all(isinstance(tag, str) for tag in entry["tags"]))


def _load():
    """The stored notebook, or a fresh empty one.

    Every failure lands in the same place on purpose: absent, unreadable, not
    JSON, JSON of the wrong shape, a version this build does not know. None of
    them is worth a traceback during startup, and the recovery for all of them
    is identical -- begin again with nothing, which is exactly the state a
    first-ever session is in and is therefore known to work.
    """
    workspace = _workspace()
    try:
        raw = memory_path().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return _empty(workspace)
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return _empty(workspace)
    if not isinstance(data, dict) or data.get("version") != FORMAT_VERSION:
        return _empty(workspace)
    entries = data.get("entries")
    if not isinstance(entries, list):
        return _empty(workspace)
    # Individual bad entries are dropped rather than condemning the file: one
    # hand-edited line should not cost a project its whole notebook.
    data["entries"] = [entry for entry in entries if _valid_entry(entry)]
    if not isinstance(data.get("next_id"), int) or data["next_id"] < 1:
        data["next_id"] = len(data["entries"]) + 1
    data["workspace"] = str(workspace)
    return data


def _save(data):
    """Write the notebook, replacing the old file in one step.

    Written to a neighbour and renamed over the top so that an interrupted save
    leaves the previous notebook intact rather than a half a JSON document. The
    directory is created here and nowhere else: importing this module must not
    make directories, which is the rule agent_config already keeps.
    """
    path = memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    os.replace(str(temporary), str(path))


def _now():
    """ISO-8601, UTC, seconds. Never a relative date.

    "yesterday" stops being true the moment it is read back, and a memory is
    read back by definition.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_tags(tags):
    if tags is None:
        return []
    if isinstance(tags, str):
        tags = re.split(r"[,\s]+", tags)
    cleaned = []
    for tag in tags:
        tag = str(tag).strip().lower()
        if tag and tag not in cleaned:
            cleaned.append(tag)
    return cleaned


# --- the public four --------------------------------------------------------

def remember(note, tags=None, kind="note"):
    """Store one note for this workspace and report exactly what was stored.

    The return string is the only thing the user ever sees of this, so it
    carries every departure from "stored as given": a redaction, a truncation,
    or an old note dropped to stay under the cap.
    """
    text = "" if note is None else str(note).strip()
    if not text:
        return "Nothing to remember: the note was empty."

    warnings = []
    if len(text) > MAX_NOTE_CHARS:
        text = text[:MAX_NOTE_CHARS].rstrip()
        warnings.append("Truncated to %d characters; a note is a sentence, "
                        "not a file." % MAX_NOTE_CHARS)

    text, reasons = _scrub(text)
    if reasons and not _is_informative(text):
        return ("Refused to remember: the note was %s and nothing else, so "
                "there is nothing safe left to store. Credentials are never "
                "written to memory." % reasons[0])
    if reasons:
        warnings.append("Redacted before storing: %s. Credentials are never "
                        "written to memory." % "; ".join(reasons))

    kind = (str(kind).strip().lower() or "note")
    data = _load()
    entry = {
        "id": "m%d" % data["next_id"],
        "note": text,
        "kind": kind,
        "tags": _clean_tags(tags),
        "timestamp": _now(),
    }
    data["next_id"] += 1
    # Appended, and nothing already in the list is touched. An id or a
    # timestamp that shifted when an unrelated note arrived would make forget()
    # unusable and every recorded time a lie.
    data["entries"].append(entry)
    dropped = 0
    if len(data["entries"]) > MAX_ENTRIES:
        dropped = len(data["entries"]) - MAX_ENTRIES
        data["entries"] = data["entries"][dropped:]
        warnings.append("At the %d-entry limit, so %d oldest %s dropped."
                        % (MAX_ENTRIES, dropped,
                           "note was" if dropped == 1 else "notes were"))
    _save(data)

    summary = "Remembered [%s] at %s (%d stored)." % (
        entry["id"], entry["timestamp"], len(data["entries"]))
    return " ".join([summary] + warnings)


def recall(query=None, limit=None, kind=None):
    """Notes for this workspace, newest first, as text.

    The header reports both the number held and the number shown, because a
    view that has been cut and does not say so reads as the whole notebook and
    invites acting on an absence that is not real.
    """
    data = _load()
    entries = data["entries"]
    total = len(entries)

    wanted_kind = str(kind).strip().lower() if kind else ""
    if wanted_kind:
        entries = [e for e in entries if e["kind"] == wanted_kind]

    terms = [t for t in re.split(r"\s+", str(query).strip().lower()) if t] if query else []
    if terms:
        def matches(entry):
            haystack = (entry["note"] + " " + " ".join(entry["tags"])).lower()
            return all(term in haystack for term in terms)
        entries = [e for e in entries if matches(e)]

    matched = len(entries)
    try:
        cap = int(limit) if limit is not None else DEFAULT_RECALL_LIMIT
    except (TypeError, ValueError):
        cap = DEFAULT_RECALL_LIMIT
    cap = max(1, cap)

    newest_first = list(reversed(entries))[:cap]

    filters = []
    if query:
        filters.append("matching %r" % str(query))
    if wanted_kind:
        filters.append("of kind %r" % wanted_kind)
    described = (" " + " ".join(filters)) if filters else ""

    header = "Memory for %s -- %d stored, %d%s, %d shown." % (
        data["workspace"], total, matched, described, len(newest_first))
    if not newest_first:
        if total:
            return header + "\nNothing matched. The notebook is not empty; this query found none of it."
        return header + "\nNothing remembered for this workspace yet."

    lines = [header]
    for entry in newest_first:
        tags = (" tags: " + ", ".join(entry["tags"])) if entry["tags"] else ""
        lines.append("[%s] %s (%s)%s" % (entry["id"], entry["timestamp"],
                                         entry["kind"], tags))
        lines.append("  " + entry["note"])
    return "\n".join(lines)


def forget(identifier):
    """Remove one note by id, and say truthfully whether one went.

    "Forgotten" for a note that was never there would leave the caller certain
    something was removed that is still on disk, so the miss is reported as a
    miss and the file is not rewritten at all.
    """
    wanted = str(identifier or "").strip()
    if not wanted:
        return "Nothing to forget: no id was given."
    data = _load()
    kept = [entry for entry in data["entries"] if entry["id"] != wanted]
    if len(kept) == len(data["entries"]):
        return ("Nothing forgotten: no entry with id %r. %d stored."
                % (wanted, len(data["entries"])))
    data["entries"] = kept
    _save(data)
    return "Forgot [%s]. %d stored." % (wanted, len(kept))
