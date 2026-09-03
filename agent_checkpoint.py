"""Checkpoints: what the workspace looked like before a turn changed it.

TMT can rewrite a repository, and until this existed the only way back was
git -- which is no way back at all when the user had not committed, and no way
back at all for a file git never tracked. That is the reason people watch an
agent keystroke by keystroke: not that it is usually wrong, but that a wrong
turn is unrecoverable. An undo that works buys more trust than three new tools.

The division is the one every state module here keeps: no UI, no model, no
tools, no subprocess. What this module does own is its own store, exactly as
`agent_context` and `agent_memory` own theirs -- a state object that could not
write would need a second module to write for it, and the two would disagree
the first time either changed.

WHAT IS PROMISED, AND WHAT IS NOT
---------------------------------
A checkpoint is a snapshot of every workspace file small enough to hold, taken
once per turn, immediately before the first action that could change anything.
Restoring it puts every one of those files back byte for byte, deletes
everything created since, and names all of it before it does any of it.

It is a snapshot of the WHOLE workspace rather than of the paths an action
named, and that decision is what makes the feature worth having. A path-scoped
snapshot cannot cover `bash`: a command may touch anything, so a checkpoint
taken before `make` could not promise to restore what `make` rewrote, and the
honest version of that design refuses to undo any turn that ran a command --
which is most turns, which is no undo at all. Snapshotting everything costs one
walk and one read of the workspace per turn (measured at 0.36s over 1,555 files
and 70 MB in TMT's own repository) and covers commands, workers and every file
verb by the same mechanism.

What it still does NOT cover, and what the restore report therefore says out
loud every time a command ran during the turn:

  * anything written OUTSIDE the workspace. Under `agent_sandbox`'s LEVEL_POLICY
    -- which is every Windows host -- a permitted build tool running repository
    code can write anywhere, and no snapshot of this directory sees it.
  * a file too large to hold (`MAX_BLOB_BYTES`). Those are recorded by size and
    modification time instead, and a restore is REFUSED, by name, if one of them
    moved. Recording the size and refusing on it is the whole of the honesty
    here: the alternative is a restore that silently leaves one file at its new
    contents while claiming the workspace is back.
  * a workspace too large to snapshot at all (`MAX_SNAPSHOT_FILES`,
    `MAX_SNAPSHOT_BYTES`). No checkpoint is taken, the session is told once, and
    there is nothing to offer an undo of. Half a snapshot is worse than none.

`refusal()` fails CLOSED, which puts it with `agent_capabilities.refusal` and
`agent_delegation.refusal` rather than with `plan_block` and `review_block`.
Those three swallow and return "" because the worst outcome there is finished
work held hostage. The worst outcome HERE is a restore that half-happened, so
anything this module cannot be sure of is a refusal with the reason in it.

STORAGE
-------
Under `agent_config.CHECKPOINT_DIR`, in INSTALL_DIR, keyed by a hash of the
workspace path -- beside `.tmt_index/` and `.tmt_memory/` and for their reason:
nothing of TMT's goes in the workspace, and there is a test that says so.

    <store>/<workspace hash>/blobs/<first two>/<full sha256>
    <store>/<workspace hash>/turns/<id>.json

Blobs are content-addressed and shared, so the second checkpoint of a session
stores only what actually changed. Everything is read and written as BYTES and
never as text: this repository is a mix of CRLF and LF, and a restore that
"helpfully" normalised a line ending would be a restore that changed the file
it claimed to have put back.
"""

import hashlib
import json
import os
import stat as stat_module
import time
from pathlib import Path

import agent_config
import agent_file_ops

# Bumped only when the on-disk shape changes incompatibly. A manifest carrying
# any other version is ignored rather than guessed at -- the same rule
# `agent_memory` keeps, and for the same reason: a store read under the wrong
# assumption is worse than a store that is not read.
FORMAT = 1

# The per-file ceiling, and it is `agent_grep.MAX_FILE_BYTES` deliberately. A
# file too big for TMT to search is a file too big for TMT to keep sixteen
# copies of, and one number the user can learn is better than two.
MAX_BLOB_BYTES = 2_000_000

# What a workspace may be and still get a checkpoint. Both are judgements, not
# measurements: TMT's own repository is 1,555 files and 70 MB of storable
# content, which is comfortably inside both, and nobody has watched a real
# project hit either and want a different number.
MAX_SNAPSHOT_FILES = 5000
MAX_SNAPSHOT_BYTES = 100_000_000

# Retention. Turns first because it is the one a user reasons in, bytes second
# because it is the one that protects the disk. Pruned oldest-first, and a blob
# is removed only once no surviving manifest names it.
MAX_TURNS = 20
MAX_STORE_BYTES = 200_000_000

# How much of the task line a checkpoint is labelled with. A checkpoint is
# picked out of a list by reading it, so the label has to be a sentence rather
# than an identifier -- and `agent_context` learned the other half of this the
# hard way: a task truncated at a character count reads as a sentence sawn
# through, so the cut prefers a clause boundary when one falls inside it.
MAX_LABEL_CHARS = 120
_CLAUSE_BREAKS = (". ", "; ", " -- ")

# What `before()` says when it cannot take a snapshot. Said once per session,
# by the loop, because a sentence repeated at the top of every turn is a
# sentence nobody reads by the third one.
TOO_LARGE = ("This workspace is too large to checkpoint (%s), so /undo has "
             "nothing to offer for it. Everything else works as it did.")

_NOTHING = "There is nothing to undo: no checkpoint has been taken yet."
_UNKNOWN = "There is no checkpoint %s. Use /checkpoints to see what there is."
_INCOMPLETE = ("Checkpoint %s cannot be restored: %s. Restoring part of a "
               "workspace and calling it undone is worse than not offering it.")
_MOVED = ("Checkpoint %s cannot be restored: %s is too large to have been "
          "held (over %s bytes) and has changed since. TMT has its size and "
          "not its contents, so putting the rest back would leave that file "
          "at its new contents and say the workspace was restored.")
_GONE = ("Checkpoint %s cannot be restored: %s is too large to have been held "
         "and is no longer there. TMT never had its contents to put back.")
_TOO_MANY = "the workspace has more than %d files"
_TOO_BIG = "the workspace holds more than %d bytes TMT would have to copy"
_UNREADABLE = "%s could not be read (%s)"

# Said in the restore report, every time, when the turn being undone ran a
# command. Not a refusal -- the workspace really is restored -- but the one
# thing a snapshot of this directory structurally cannot know about.
COMMAND_CAVEAT = ("A command ran during that turn (%s). Everything inside the "
                  "workspace is back; anything it wrote outside the workspace "
                  "is not, and TMT cannot see those to tell you about them.")

# Directories are left alone by a restore, and this is said rather than fixed.
# The walk yields files, so TMT cannot tell a directory created during the turn
# from one that was already there and already empty -- and removing the second
# kind would be the restore destroying something the turn never touched.
DIRECTORY_CAVEAT = ("Directories are left as they are. A folder created during "
                    "that turn is now empty rather than gone.")


class CheckpointError(Exception):
    """A checkpoint could not be taken or put back. Never raised at a caller
    that has an answer to give: the loop's hook swallows, and the commands ask
    `refusal()` first."""


# --- the store -------------------------------------------------------------

def store_dir():
    """The checkpoint store, read at call time and never bound at import.

    `agent_index` and `agent_memory` both do exactly this, for the reason they
    both state: the tests redirect INSTALL_DIR at a temporary directory, and a
    value captured at import would freeze whichever one happened to exist when
    this module first loaded.
    """
    return Path(agent_config.CHECKPOINT_DIR)


def workspace_dir(root=None):
    """This workspace's own corner of the store.

    Keyed by a hash of the absolute path rather than by the path itself: a
    path is not a filename on any platform, and two projects with the same
    basename must not share a history of each other's files.
    """
    resolved = Path(root or agent_file_ops.workspace()).resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8", "replace")).hexdigest()[:16]
    return store_dir() / digest


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _blob_path(base, digest):
    return base / "blobs" / digest[:2] / digest


def _turns_dir(base):
    return base / "turns"


def _clear_readonly(path):
    """Take the read-only bit off, for Windows.

    `agent_uninstall` learned this the expensive way: git marks its object
    store read-only, a read-only file cannot be deleted or opened for writing
    on Windows at all, and the failure arrives as a PermissionError with
    nothing in it about which bit is wrong.
    """
    try:
        os.chmod(path, stat_module.S_IWRITE)
    except OSError:
        pass


def _write_atomic(path, data):
    """Write bytes through a temporary file in the same directory.

    Through a temporary file because a blob half-written is a blob whose name
    is a lie about its contents -- every later checkpoint that hashes to it
    would skip the write and reuse the damage.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with open(temporary, "wb") as handle:
            handle.write(data)
        if path.exists():
            _clear_readonly(path)
        os.replace(str(temporary), str(path))
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass


# --- the snapshot ----------------------------------------------------------

class Snapshot:
    """One turn's before-picture: what every file held, and what could not be held.

    `files` maps a workspace-relative POSIX path to the sha256 of its contents.
    `oversize` maps a path to [size, mtime_ns] for a file too large to store --
    enough to tell whether it moved, never enough to put it back, and the
    difference between those two is why `refusal` reads it.
    """

    __slots__ = ("id", "label", "created", "files", "oversize", "complete",
                 "reason", "commands", "root", "restored")

    def __init__(self, id="", label="", created=None, files=None, oversize=None,
                 complete=True, reason="", commands=(), root="", restored=False):
        self.id = str(id or "")
        self.label = str(label or "")
        self.created = float(created if created is not None else time.time())
        self.files = dict(files or {})
        self.oversize = {k: list(v) for k, v in (oversize or {}).items()}
        self.complete = bool(complete)
        self.reason = str(reason or "")
        self.commands = list(commands or ())
        self.root = str(root or "")
        self.restored = bool(restored)

    def __repr__(self):
        return "Snapshot(id=%r, files=%d, complete=%s)" % (
            self.id, len(self.files), self.complete)

    def to_json(self):
        return {"format": FORMAT, "id": self.id, "label": self.label,
                "created": self.created, "files": self.files,
                "oversize": self.oversize, "complete": self.complete,
                "reason": self.reason, "commands": self.commands,
                "root": self.root, "restored": self.restored}

    @classmethod
    def from_json(cls, data):
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            return None
        try:
            return cls(id=data.get("id", ""), label=data.get("label", ""),
                       created=data.get("created"), files=data.get("files"),
                       oversize=data.get("oversize"),
                       complete=data.get("complete", True),
                       reason=data.get("reason", ""),
                       commands=data.get("commands") or (),
                       root=data.get("root", ""),
                       restored=data.get("restored", False))
        except (TypeError, ValueError):
            return None

    def when(self):
        """The time it was taken, for a person rather than for arithmetic."""
        return time.strftime("%H:%M:%S", time.localtime(self.created))

    def summary(self):
        """One row for a list: the id, the time and what was being asked."""
        state = "" if self.complete else "  (cannot be restored)"
        return "%s  %s  %s%s" % (self.id, self.when(), self.label or "(no task)", state)


def _scan(root=None, sink=None):
    """(files, oversize, complete, reason) for the workspace as it stands.

    One traversal, and it is `agent_file_ops.iter_workspace_files` rather than
    an `os.walk` of this module's own -- the rule the grep/glob work settled:
    two walks would be two sets of pruning rules, two scan ceilings and two
    answers to what counts as machinery, and only one of them would be updated
    the day any of those changed.

    `sink(digest, data)` is called for each file when there is one, which is
    what makes this both the capture and the comparison. Reading the tree twice
    -- once to hash and once to store -- would be twice the work and a window
    in which the two could disagree.

    The limit passed to the walk is one MORE than the ceiling, so a workspace
    that is exactly at the ceiling is complete and one over it is detected here
    rather than silently truncated by the walk's own budget.
    """
    root = Path(root or agent_file_ops.workspace())
    files = {}
    oversize = {}
    total = 0
    seen = 0
    # The store must never end up INSIDE a snapshot, and that is not
    # hypothetical: when TMT is run ON TMT the install and the workspace are
    # the same directory, so the store sits in the tree being walked. Each
    # capture would then copy every blob the captures before it wrote, and the
    # store would square itself once per turn. `WORKSPACE_SKIP` is the shared
    # pruning list and is deliberately not forked for this -- it is about what
    # counts as machinery in ANY project, and this is one directory this one
    # module knows the location of.
    try:
        forbidden = store_dir().resolve()
    except OSError:
        forbidden = None
    for relative, absolute in agent_file_ops.iter_workspace_files(
            root, limit=MAX_SNAPSHOT_FILES + 1):
        seen += 1
        if seen > MAX_SNAPSHOT_FILES:
            return files, oversize, False, _TOO_MANY % MAX_SNAPSHOT_FILES
        if forbidden is not None:
            try:
                resolved = absolute.resolve()
                if resolved == forbidden or forbidden in resolved.parents:
                    seen -= 1        # it is not part of the workspace's budget
                    continue
            except OSError:
                pass
        # A file symlink is READ where it points, so one pointing out of the
        # workspace would be captured here and written back there by a restore.
        # `within_workspace` is the containment test with the refusal taken
        # off, which is exactly the question an entry a walk arrived at asks.
        if not agent_file_ops.within_workspace(absolute):
            continue
        key = agent_file_ops.posix(relative)
        try:
            info = absolute.stat()
        except OSError as error:
            return files, oversize, False, _UNREADABLE % (key, error)
        if info.st_size > MAX_BLOB_BYTES:
            oversize[key] = [info.st_size, info.st_mtime_ns]
            continue
        total += info.st_size
        if total > MAX_SNAPSHOT_BYTES:
            return files, oversize, False, _TOO_BIG % MAX_SNAPSHOT_BYTES
        try:
            data = absolute.read_bytes()
        except OSError as error:
            return files, oversize, False, _UNREADABLE % (key, error)
        digest = _digest(data)
        files[key] = digest
        if sink is not None:
            sink(digest, data)
    return files, oversize, True, ""


def capture(root=None, label="", commands=()):
    """Take a snapshot of the workspace now, storing what it holds.

    Uncommitted: it has no id and no manifest on disk until `commit` is called.
    That split is deliberate -- the blobs are written as the tree is read, so a
    turn that ends without changing anything costs the reads and leaves no
    manifest behind for the user to wade through.
    """
    base = workspace_dir(root)

    def sink(digest, data):
        target = _blob_path(base, digest)
        if not target.exists():
            _write_atomic(target, data)

    files, oversize, complete, reason = _scan(root, sink=sink)
    return Snapshot(label=label, files=files, oversize=oversize,
                    complete=complete, reason=reason, commands=commands,
                    root=str(Path(root or agent_file_ops.workspace()).resolve()))


def commit(snapshot, root=None):
    """Write the manifest, give the snapshot an id, and prune. Returns it.

    The id is one past the highest on disk rather than a count of what is
    there, so pruning the oldest never hands a new checkpoint a number the user
    has already seen in this session.
    """
    base = workspace_dir(root)
    turns = _turns_dir(base)
    turns.mkdir(parents=True, exist_ok=True)
    highest = 0
    for existing in turns.glob("*.json"):
        try:
            highest = max(highest, int(existing.stem))
        except ValueError:
            continue
    snapshot.id = "%04d" % (highest + 1)
    _write_atomic(turns / ("%s.json" % snapshot.id),
                  json.dumps(snapshot.to_json()).encode("utf-8"))
    prune(root)
    return snapshot


def history(root=None):
    """Every checkpoint for this workspace, newest first.

    Never raises. A manifest that cannot be read is left out rather than
    reported: this is the list a user reads to decide what to undo, and one
    damaged file must not take the other nineteen off the screen with it.
    """
    turns = _turns_dir(workspace_dir(root))
    out = []
    try:
        entries = sorted(turns.glob("*.json"))
    except OSError:
        return []
    for entry in entries:
        try:
            snapshot = Snapshot.from_json(json.loads(entry.read_text("utf-8")))
        except (OSError, ValueError):
            continue
        if snapshot is not None:
            out.append(snapshot)
    out.sort(key=lambda s: s.id, reverse=True)
    return out


def find(identifier="", root=None):
    """The checkpoint with this id, or the newest one when none is named."""
    known = history(root)
    if not known:
        return None
    wanted = str(identifier or "").strip()
    if not wanted:
        return known[0]
    for snapshot in known:
        if snapshot.id == wanted or snapshot.id.lstrip("0") == wanted.lstrip("0"):
            return snapshot
    return None


def prune(root=None):
    """Drop the oldest checkpoints past the ceilings, then the blobs nothing names.

    Turns first, bytes second, and the blob sweep last because a blob is shared:
    it may be dropped only once no surviving manifest names it, and working that
    out before the manifests have gone would keep whatever the dropped ones held.
    """
    base = workspace_dir(root)
    kept = history(root)
    doomed = kept[MAX_TURNS:]
    kept = kept[:MAX_TURNS]
    # Bytes, measured over the blobs the survivors actually name -- oldest
    # dropped until the rest fit. Measuring the directory instead would count
    # blobs that are already unreferenced and drop live checkpoints to reclaim
    # space the sweep below was about to give back for nothing.
    while kept:
        total = 0
        for snapshot in kept:
            for digest in set(snapshot.files.values()):
                try:
                    total += _blob_path(base, digest).stat().st_size
                except OSError:
                    continue
        if total <= MAX_STORE_BYTES or len(kept) <= 1:
            break
        doomed.append(kept.pop())
    for snapshot in doomed:
        try:
            (_turns_dir(base) / ("%s.json" % snapshot.id)).unlink()
        except OSError:
            pass
    # The sweep runs on EVERY commit, not only when a manifest was dropped,
    # because dropped manifests are not the only way a blob is orphaned. A
    # capture that hits a ceiling has already written everything it read
    # before it found out -- up to the whole ceiling's worth -- and that
    # snapshot is never committed, so nothing would ever name those blobs.
    #
    # The narrow cost: two TMT processes sharing one workspace, where one is
    # part way through a capture while the other commits, can have blobs the
    # first has written but not yet named swept from under it. Its restore
    # then reports "the stored copy is missing" for those paths rather than
    # doing something wrong, and one session per workspace is the ordinary
    # case by a long way.
    live = set()
    for snapshot in kept:
        live.update(snapshot.files.values())
    blobs = base / "blobs"
    try:
        candidates = list(blobs.glob("*/*"))
    except OSError:
        return
    for blob in candidates:
        if blob.name not in live:
            try:
                _clear_readonly(blob)
                blob.unlink()
            except OSError:
                pass


# --- restoring -------------------------------------------------------------

class RestorePlan:
    """What restoring a checkpoint would do, before any of it is done.

    The preview and the execution read the SAME object: `restore` takes a plan
    rather than a snapshot, so a user cannot agree to one thing and be given
    another. `agent_uninstall` settled that shape for the one other action here
    that cannot be undone, and there is a test asserting the identity.
    """

    __slots__ = ("snapshot", "recreate", "overwrite", "delete", "unchanged",
                 "refusal")

    def __init__(self, snapshot, recreate=(), overwrite=(), delete=(),
                 unchanged=0, refusal=""):
        self.snapshot = snapshot
        self.recreate = list(recreate)
        self.overwrite = list(overwrite)
        self.delete = list(delete)
        self.unchanged = int(unchanged)
        self.refusal = str(refusal or "")

    def __repr__(self):
        return "RestorePlan(recreate=%d, overwrite=%d, delete=%d)" % (
            len(self.recreate), len(self.overwrite), len(self.delete))

    def touches(self):
        """Every path this plan would change, in one sorted list."""
        return sorted(set(self.recreate) | set(self.overwrite) | set(self.delete))

    def empty(self):
        return not (self.recreate or self.overwrite or self.delete)


class RestoreResult:
    """What restoring actually did, path by path, and what it could not do."""

    __slots__ = ("snapshot", "restored", "deleted", "failed", "checkpoint", "notes")

    def __init__(self, snapshot, restored=(), deleted=(), failed=(),
                 checkpoint=None, notes=()):
        self.snapshot = snapshot
        self.restored = list(restored)
        self.deleted = list(deleted)
        self.failed = list(failed)
        self.checkpoint = checkpoint
        self.notes = list(notes or ())

    def ok(self):
        return not self.failed

    def moved(self):
        return len(self.restored) + len(self.deleted)


def refusal(snapshot, root=None):
    """Why this checkpoint may not be restored, or "" when it may.

    FAILS CLOSED, which is what puts it with `agent_capabilities.refusal` and
    `agent_delegation.refusal` rather than with the three completion gates. A
    gate that swallows is protecting finished work from being held hostage; a
    snapshot that cannot answer is protecting a workspace from being half
    rewritten, and those two want opposite defaults.
    """
    if snapshot is None:
        return _NOTHING
    try:
        if not snapshot.complete:
            return _INCOMPLETE % (snapshot.id or "?",
                                  snapshot.reason or "it was never finished")
        root = Path(root or agent_file_ops.workspace())
        for key, recorded in sorted(snapshot.oversize.items()):
            target = root / key
            try:
                info = target.stat()
            except OSError:
                return _GONE % (snapshot.id or "?", key)
            size, mtime = (list(recorded) + [None, None])[:2]
            if info.st_size != size or info.st_mtime_ns != mtime:
                return _MOVED % (snapshot.id or "?", key, MAX_BLOB_BYTES)
    except Exception as error:  # noqa: BLE001 - fail closed, see the docstring
        return ("This checkpoint could not be checked (%s: %s), so TMT will "
                "not restore it." % (type(error).__name__, error))
    return ""


def plan(snapshot, root=None):
    """What restoring would change. Reads the workspace; writes nothing.

    A refused plan carries the refusal and no paths at all, rather than the
    paths it would have moved -- a list of files beside a sentence saying it
    will not happen is a list somebody acts on.
    """
    held = refusal(snapshot, root)
    if held:
        return RestorePlan(snapshot, refusal=held)
    root = Path(root or agent_file_ops.workspace())
    now, now_oversize, complete, reason = _scan(root)
    if not complete:
        return RestorePlan(snapshot, refusal=_INCOMPLETE % (
            snapshot.id or "?", "the workspace cannot be read now: %s" % reason))
    recreate, overwrite, unchanged = [], [], 0
    for key, digest in snapshot.files.items():
        current = now.get(key)
        if current is None:
            # Not among the files scanned. It may have been deleted during the
            # turn, or it may have grown past the per-file ceiling since --
            # either way the stored contents are what belong there.
            recreate.append(key)
        elif current != digest:
            overwrite.append(key)
        else:
            unchanged += 1
    known = set(snapshot.files) | set(snapshot.oversize)
    delete = [key for key in list(now) + list(now_oversize) if key not in known]
    return RestorePlan(snapshot, recreate=sorted(recreate),
                       overwrite=sorted(overwrite), delete=sorted(delete),
                       unchanged=unchanged)


def restore(plan_object, root=None, checkpoint_first=True):
    """Carry out a plan. Returns a RestoreResult; raises only on a refused plan.

    Takes the PLAN, never a snapshot, so what was shown is what runs.

    The current workspace is checkpointed first, so an undo is itself
    undoable -- which is not a nicety: restoring is the most destructive thing
    TMT does to a tree, and it is the one action whose own mistake would
    otherwise have no way back.
    """
    if plan_object is None or plan_object.refusal:
        raise CheckpointError(
            plan_object.refusal if plan_object is not None else _NOTHING)
    snapshot = plan_object.snapshot
    root = Path(root or agent_file_ops.workspace())
    base = workspace_dir(root)
    taken = None
    if checkpoint_first and not plan_object.empty():
        try:
            taken = commit(capture(root, label="before undoing %s" % snapshot.id),
                           root)
        except Exception:
            # A snapshot that could not be taken must not stop the restore the
            # user asked for -- but they are told, in the result, rather than
            # left to find out by typing /undo twice.
            taken = None
    restored, deleted, failed = [], [], []
    for key in plan_object.recreate + plan_object.overwrite:
        digest = snapshot.files.get(key)
        source = _blob_path(base, digest) if digest else None
        if source is None or not source.exists():
            failed.append((key, "the stored copy is missing"))
            continue
        target = root / key
        try:
            # Asked of the target itself whether or not it exists. `resolve`
            # answers for a path that is not there yet, and asking about the
            # PARENT instead would miss the case that matters: a file symlink
            # pointing out of the workspace, which would be written THROUGH,
            # putting a stored blob somewhere the workspace does not reach.
            if not agent_file_ops.within_workspace(target):
                failed.append((key, "it is not inside the workspace"))
                continue
            _write_atomic(target, source.read_bytes())
            restored.append(key)
        except OSError as error:
            failed.append((key, str(error)))
    for key in plan_object.delete:
        target = root / key
        try:
            if not agent_file_ops.within_workspace(target):
                failed.append((key, "it is not inside the workspace"))
                continue
            if target.is_dir():
                # The walk yields files, so this cannot normally happen -- and
                # if it ever does, a directory is not what was recorded and is
                # not something a restore may remove.
                failed.append((key, "it is a directory"))
                continue
            _clear_readonly(target)
            target.unlink()
            deleted.append(key)
        except OSError as error:
            failed.append((key, str(error)))
    notes = []
    if snapshot.commands:
        notes.append(COMMAND_CAVEAT % ", ".join(
            "`%s`" % one for one in snapshot.commands[:3]))
    if plan_object.delete:
        notes.append(DIRECTORY_CAVEAT)
    if checkpoint_first and taken is None and not plan_object.empty():
        notes.append("TMT could not checkpoint the workspace before restoring, "
                     "so this undo cannot itself be undone.")
    _mark_restored(snapshot, root)
    return RestoreResult(snapshot, restored=restored, deleted=deleted,
                         failed=failed, checkpoint=taken, notes=notes)


def _mark_restored(snapshot, root=None):
    """Record that this checkpoint has been used, for the list to show.

    Guarded to nothing: the restore has already happened by the time this runs,
    and a manifest that could not be updated must not turn a completed undo
    into an exception.
    """
    if not snapshot.id:
        return
    try:
        snapshot.restored = True
        _write_atomic(_turns_dir(workspace_dir(root)) / ("%s.json" % snapshot.id),
                      json.dumps(snapshot.to_json()).encode("utf-8"))
    except Exception:
        pass


# --- what the loop asks ----------------------------------------------------

def will_mutate(action, obj):
    """Whether this action, which has NOT run yet, could change the workspace.

    Deliberately not `TMT.mutated`, and the difference is the tense. That one
    asks what an action DID and reads `agent_multi`'s record of the calls that
    actually ran; this one is asked before dispatch, when there is no record
    yet, so a multi_tool is judged on the calls it was ASKED for.

    Fails towards yes. A wrong yes costs one workspace walk; a wrong no costs
    the user their undo, silently, on the one turn they needed it.
    """
    if not action:
        return False
    if action in agent_config.MUTATING_ACTIONS:
        return True
    if action == "multi_tool":
        calls = (obj or {}).get("calls") if isinstance(obj, dict) else None
        for call in calls or ():
            if isinstance(call, dict) and will_mutate(str(call.get("action", "")), call):
                return True
        return False
    if action == "spawn_agent":
        # A worker writes on its own thread, through the same dispatcher, and
        # nothing here sees those writes coming. Snapshotting before the worker
        # starts is the only moment that covers them -- so a delegation counts
        # as a mutation unless its own contract says it cannot write.
        constraints = (obj or {}).get("constraints") if isinstance(obj, dict) else None
        if isinstance(constraints, dict) and constraints.get("read_only"):
            return False
        return True
    return False


def label_for(task):
    """A task line as a checkpoint label: one clause, or a cut with an ellipsis.

    `agent_context` learned this the expensive way -- the first progress file
    it ever wrote carried 160 characters of a 700-character instruction sawn
    through mid-word, which answered none of the questions the file existed to
    answer. A clause boundary inside the ceiling is preferred to the ceiling.
    """
    text = " ".join(str(task or "").split())
    if len(text) <= MAX_LABEL_CHARS:
        return text
    head = text[:MAX_LABEL_CHARS]
    best = -1
    for mark in _CLAUSE_BREAKS:
        best = max(best, head.rfind(mark))
    if best > MAX_LABEL_CHARS // 3:
        return head[:best].rstrip()
    return head.rstrip() + "..."


class Checkpointer:
    """One per session. Takes at most one snapshot per turn, lazily.

    Lazily because most turns change nothing: a question answered out of the
    workspace snapshot, a `/notes` read, a turn that ends in a refusal. Walking
    the tree for those would spend a third of a second and 70 MB of reads on
    every question TMT is ever asked.

    Once per turn because the snapshot is of the whole workspace, so the one
    taken before the first write already covers the fortieth -- and because a
    checkpoint per action would turn a `multi_tool` of thirty edits into thirty
    entries in a list the user has to read.

    Nothing here raises. It sits on the hot path of every action dispatch, and
    a checkpoint that fails must cost the user their undo and never their turn.
    """

    __slots__ = ("root", "enabled", "_snapshot", "_label", "_commands",
                 "_warned", "_failed")

    def __init__(self, root=None, enabled=True):
        self.root = root
        self.enabled = bool(enabled)
        self._snapshot = None
        self._label = ""
        self._commands = []
        self._warned = False
        self._failed = ""

    def begin(self, task):
        """A new turn. The last one's snapshot is committed, not carried."""
        self._snapshot = None
        self._label = label_for(task)
        self._commands = []
        self._failed = ""

    def note_command(self, command):
        """Remember a command that ran, for the restore report to name.

        What is kept is that a command RAN and what it was, never anything
        about what it did -- reading a program's output to decide what happened
        is the rule this codebase refuses, and it would be at its worst here.
        """
        text = " ".join(str(command or "").split())
        if text and text not in self._commands:
            self._commands.append(text[:200])

    def before(self, action, obj):
        """Called before every dispatch. Returns a sentence to show, or "".

        The sentence is the too-large notice and is returned once per session,
        because a workspace does not become small between one turn and the next
        and a warning repeated at the top of every question is one nobody reads.
        """
        if not self.enabled or not will_mutate(action, obj):
            return ""
        if action == "bash":
            self.note_command((obj or {}).get("command", "") if isinstance(obj, dict) else "")
        if self._snapshot is not None or self._failed:
            return ""
        try:
            snapshot = capture(self.root, label=self._label)
        except Exception as error:
            self._failed = "%s: %s" % (type(error).__name__, error)
            return ""
        if not snapshot.complete:
            # A snapshot that would refuse to restore is not written at all.
            # A list of checkpoints where half the rows say "cannot be
            # restored" teaches the user that /undo does not work, which is
            # worse than saying so once and offering nothing.
            self._failed = snapshot.reason
            if self._warned:
                return ""
            self._warned = True
            return TOO_LARGE % snapshot.reason
        self._snapshot = snapshot
        return ""

    def commit(self):
        """End of turn. Writes the manifest if a snapshot was taken.

        Returns the committed Snapshot, or None. Written at the END of the turn
        rather than when it was taken so the commands the turn ran are on it --
        the restore report names them, and they are not known until they have
        happened.
        """
        snapshot = self._snapshot
        self._snapshot = None
        if snapshot is None:
            return None
        try:
            snapshot.commands = list(self._commands)
            return commit(snapshot, self.root)
        except Exception:
            return None

    def taken(self):
        """Whether this turn has a snapshot waiting to be committed."""
        return self._snapshot is not None
