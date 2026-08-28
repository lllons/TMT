"""Git operations performed under TMT's own commit identity."""

import os
import re
import subprocess
from pathlib import Path

import agent_config

GIT_TIMEOUT = 30
# A push crosses the network and may wait on a credential helper.
PUSH_TIMEOUT = 120
MAX_DIFF_CHARS = 20000
MAX_DETAIL_CHARS = 500

# The token the shipped .tmt_git carries in place of a real address. An email
# nobody has verified on a GitHub account is attributed to nobody, so it is
# treated as no identity at all rather than as a usable one.
PLACEHOLDER_MARKER = "replace_with"
# Userinfo in a remote URL is a credential. It appears in remote listings and
# inside git's own error text, so it is removed from everything that leaves
# this module for the user or the model.
_URL_USERINFO = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://)[^/@\s]*@")

SETUP_HINT = (
    "TMT commits under its own git identity and never under yours, so it needs "
    "an address of its own.\n"
    "Put a line 'TMT_GIT_EMAIL=tmt-code@example.invalid' in the tracked .tmt_git "
    "file beside the TMT modules to set it for every clone, or in .tmt_git.local "
    "to set it on this machine only, or set the TMT_GIT_EMAIL environment "
    "variable. TMT_GIT_NAME sets the display name (default 'TMT code').\n"
    "The address is public commit metadata, not a credential: never put tokens, "
    "passwords or keys in either file.\n"
    "Your own git configuration is not read and not changed."
)

PLACEHOLDER_HINT = (
    "The TMT git email is still the placeholder the project ships with, so it "
    "identifies nobody and GitHub cannot attribute a commit to it.\n"
    "Replace it with a real address that is verified on the GitHub account "
    "representing TMT: edit the TMT_GIT_EMAIL line in the tracked .tmt_git file "
    "beside the TMT modules, or override it for this machine alone in "
    ".tmt_git.local, or set the TMT_GIT_EMAIL environment variable.\n"
    "Until then TMT refuses to commit rather than author commits under an "
    "address that belongs to no one."
)


class GitError(RuntimeError):
    """A git operation failed. The message is safe to show the user."""


def _config(name, default=""):
    # Read through the module object, at call time: setup writes these values
    # after import and tests replace them between cases.
    value = getattr(agent_config, name, None)
    if value is None or value == "":
        return default
    return str(value).strip()


def _local_identity_file():
    """The per-machine override's path, for the diagnostic only."""
    try:
        return agent_config.local_git_identity_path()
    except Exception:
        return ".tmt_git.local"


def _scrub(text):
    """Remove credentials embedded in remote URLs from git output."""
    if not text:
        return ""
    return _URL_USERINFO.sub(r"\1", str(text))


def _clip(text, limit):
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (truncated)"


def _git(args, cwd, env=None, timeout=GIT_TIMEOUT, check=True):
    """Run one git command. Never a shell, never inheriting a cwd by accident."""
    cmd = ["git"] + [str(a) for a in args]
    try:
        result = subprocess.run(
            cmd, cwd=str(cwd), env=env, shell=False, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except FileNotFoundError:
        raise GitError("git was not found on PATH.")
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]} timed out after {timeout} seconds.")
    except OSError as error:
        raise GitError(f"Could not run git: {error}")
    if check and result.returncode != 0:
        detail = _clip(_scrub(result.stderr) or _scrub(result.stdout), MAX_DETAIL_CHARS)
        raise GitError(f"git {args[0]} failed: {detail or 'no output'}")
    return result


class TMTGitIdentity:
    """The dedicated identity TMT commits under.

    Never falls back to the user's git config: an unset email is an error, not
    an invitation to commit as the human.
    """

    DEFAULT_NAME = "TMT code"

    def __init__(self, name, email, name_source="", email_source=""):
        self.name = (name or self.DEFAULT_NAME).strip() or self.DEFAULT_NAME
        self.email = (email or "").strip()
        self.name_source = name_source or "unknown"
        self.email_source = email_source or ("unknown" if self.email else "not set")

    @classmethod
    def resolve(cls):
        """The identity to commit under, read from agent_config at call time.

        Precedence, highest first: the TMT_GIT_* environment variables, the
        git-ignored .tmt_git.local, the tracked .tmt_git, then the built-in
        name. The email is never inferred from git config, and there is no
        default for it.
        """
        values = agent_config.resolve_git_identity()
        return cls(
            values.get("name", cls.DEFAULT_NAME), values.get("email", ""),
            values.get("name_source", ""), values.get("email_source", ""),
        )

    def is_placeholder(self):
        """Whether the email is still the address the project ships with."""
        return PLACEHOLDER_MARKER in self.email.lower()

    def validate(self):
        """Raise GitError with the setup instructions when the email is unusable.

        Unusable means empty, without an "@", carrying whitespace or angle
        brackets, or still holding the shipped placeholder. Every one of those
        would produce a commit GitHub can attribute to no one, which is worse
        than no commit at all.
        """
        if not self.email:
            raise GitError("No TMT git identity is configured.\n" + SETUP_HINT)
        if self.is_placeholder():
            raise GitError(
                f"The TMT git email is unusable: {self.email}\n" + PLACEHOLDER_HINT
            )
        unusable = (
            "@" not in self.email
            or any(character.isspace() for character in self.email)
            or "<" in self.email or ">" in self.email
        )
        if unusable:
            raise GitError(
                f"The configured TMT git email is not a valid address: {self.email}\n"
                + SETUP_HINT
            )

    def commit_environment(self, base_env=None):
        """A copy of base_env (default os.environ) with GIT_AUTHOR_NAME,
        GIT_AUTHOR_EMAIL, GIT_COMMITTER_NAME and GIT_COMMITTER_EMAIL set.
        Applies to one subprocess only; never touches global config."""
        self.validate()
        env = dict(os.environ if base_env is None else base_env)
        env["GIT_AUTHOR_NAME"] = self.name
        env["GIT_AUTHOR_EMAIL"] = self.email
        env["GIT_COMMITTER_NAME"] = self.name
        env["GIT_COMMITTER_EMAIL"] = self.email
        return env

    def signature(self):
        return f"{self.name} <{self.email}>"

    def describe(self):
        """Multi-line diagnostic naming the winning source for each half.

        Reports an address and a display name only: there is no credential in
        this identity to print.
        """
        lines = [
            "TMT git identity",
            f"  name:  {self.name}  (from {self.name_source})",
            f"  email: {self.email or '(not set)'}  (from {self.email_source})",
            f"  tracked identity file: {_config('GIT_IDENTITY_FILE', '.tmt_git')}",
            f"  per-machine override:  {_local_identity_file()}",
            "  precedence: TMT_GIT_* environment variables, then .tmt_git.local, "
            "then .tmt_git",
        ]
        try:
            self.validate()
            lines.append("  status: usable")
        except GitError as error:
            if self.is_placeholder():
                lines.append(
                    "  status: placeholder, not a real address - commits are refused"
                )
            else:
                lines.append(
                    "  status: not usable - commits are refused until this is set"
                )
            lines.append(str(error).split("\n", 1)[-1])
        return "\n".join(lines)


class TMTGit:
    """Git workflow engine rooted at one repository.

    Every method returns plain data or raises GitError. Nothing here writes to
    global or repository git config, and no method can overwrite remote history.
    """

    def __init__(self, root=None, identity=None):
        self.root = _locate_root(root) if root is None else Path(str(root)).resolve()
        self._identity = identity

    @property
    def identity(self):
        # Resolved on use so configuration changed after construction is honoured.
        return self._identity or TMTGitIdentity.resolve()

    @classmethod
    def discover(cls, start=None):
        """Locate the repository containing `start` (default ROOT_DIR) via
        `git rev-parse --show-toplevel`. TMT_GIT_ROOT overrides. Raises GitError
        when there is no repository."""
        return cls(root=_locate_root(start))

    def _run(self, args, env=None, timeout=GIT_TIMEOUT, check=True):
        return _git(args, self.root, env=env, timeout=timeout, check=check)

    def _relative(self, path):
        """A repo-relative, forward-slashed path, or GitError if it escapes."""
        text = str(path).strip().replace("\\", "/")
        if not text:
            raise GitError("An empty path was given.")
        p = Path(text)
        if p.is_absolute():
            try:
                p = p.resolve().relative_to(self.root)
            except ValueError:
                raise GitError(f"Path is outside the repository: {path}")
        rel = p.as_posix()
        if rel == ".." or rel.startswith("../"):
            raise GitError(f"Path is outside the repository: {path}")
        return rel

    def _paths(self, paths):
        if paths is None:
            return []
        if isinstance(paths, str):
            paths = [paths]
        return [self._relative(p) for p in paths if str(p).strip()]

    def _branch_label(self):
        try:
            return self.current_branch()
        except GitError:
            return "(detached HEAD)"

    def status(self):
        """{'branch': str, 'staged': [paths], 'unstaged': [paths],
        'untracked': [paths], 'clean': bool} from `git status --porcelain=v1 -z`.
        Paths are repo-relative, forward-slashed."""
        raw = self._run(["status", "--porcelain=v1", "-z"]).stdout
        staged, unstaged, untracked = _parse_status(raw)
        return {
            "branch": self._branch_label(),
            "staged": staged,
            "unstaged": unstaged,
            "untracked": untracked,
            "clean": not (staged or unstaged or untracked),
            "root": self.root.as_posix(),
        }

    def diff(self, paths=None):
        """Staged and unstaged changes as unified diff text, truncated for the
        model. Returns '(no changes)' when the tree matches the index and HEAD."""
        targets = self._paths(paths)
        limit = ["--"] + targets if targets else []
        staged = self._run(["diff", "--no-color", "--cached"] + limit).stdout
        unstaged = self._run(["diff", "--no-color"] + limit).stdout
        sections = []
        if staged.strip():
            sections.append("Staged changes:\n" + staged.rstrip())
        if unstaged.strip():
            sections.append("Unstaged changes:\n" + unstaged.rstrip())
        if not sections:
            return "(no changes)"
        return _clip(_scrub("\n\n".join(sections)), MAX_DIFF_CHARS)

    def stage(self, paths):
        """Stage the given paths and return the ones git actually holds staged."""
        targets = self._paths(paths)
        if not targets:
            raise GitError("No paths were given to stage.")
        self._run(["add", "--"] + targets)
        raw = self._run(["diff", "--cached", "--name-only", "-z", "--"] + targets).stdout
        return [_normalise(p) for p in raw.split("\0") if p]

    def _staged_paths(self):
        raw = self._run(["diff", "--cached", "--name-only", "-z"]).stdout
        return [_normalise(p) for p in raw.split("\0") if p]

    def current_branch(self):
        """The checked-out branch name. Raises GitError on a detached HEAD."""
        result = self._run(["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
        name = result.stdout.strip()
        if not name:
            raise GitError(
                "HEAD is detached, so there is no current branch. Check out a "
                "branch before committing or pushing."
            )
        return name

    def branch_exists(self, branch):
        if not branch:
            return False
        result = self._run(
            ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], check=False
        )
        return result.returncode == 0

    def remotes(self):
        return [line.strip() for line in self._run(["remote"]).stdout.splitlines() if line.strip()]

    def default_remote(self):
        """Upstream's remote if the branch has one, else 'origin' when it
        exists, else the sole remote. Raises GitError when there is none.
        Never creates or rewrites a remote."""
        names = self.remotes()
        if not names:
            raise GitError(
                "This repository has no remote configured. TMT does not add "
                "remotes; add one yourself and ask again."
            )
        upstream = self._run(
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
            check=False,
        )
        tracked = upstream.stdout.strip()
        if upstream.returncode == 0 and "/" in tracked:
            candidate = tracked.split("/", 1)[0]
            if candidate in names:
                return candidate
        if "origin" in names:
            return "origin"
        if len(names) == 1:
            return names[0]
        raise GitError(
            "This repository has several remotes and no upstream for the current "
            "branch. Name the remote explicitly: " + ", ".join(names)
        )

    def remote_url(self, remote):
        """The remote's URL with any embedded credentials removed."""
        result = self._run(["remote", "get-url", remote], check=False)
        if result.returncode != 0:
            raise GitError(f"There is no remote named '{remote}'.")
        return _scrub(result.stdout.strip())

    def commit(self, message, paths=None, stage_all=False):
        """Commit under the TMT identity. Returns
        {'sha': str, 'short': str, 'author': 'Name <email>',
         'committer': 'Name <email>', 'branch': str, 'files': [paths]}.

        Identity is applied ONLY through commit_environment() on this
        subprocess. Raises GitError when the identity is unset, when nothing is
        staged, or when git fails. `stage_all` is the explicit
        commit-everything path; without it only `paths` are staged."""
        identity = self.identity
        # Checked before anything is staged: an unusable identity must leave the
        # repository exactly as it was found.
        identity.validate()
        text = (message or "").strip()
        if not text:
            raise GitError("A commit message is required.")
        if stage_all:
            self._run(["add", "--all"])
        elif paths:
            self.stage(paths)
        staged = self._staged_paths()
        if not staged:
            raise GitError(
                "Nothing is staged, so there is nothing to commit. Name the "
                "paths to commit, or ask to commit everything."
            )
        self._run(["commit", "--message", text], env=identity.commit_environment())
        record = self._run(
            ["log", "-1", "--format=%H%x00%h%x00%an%x00%ae%x00%cn%x00%ce"]
        ).stdout
        fields = record.strip("\n").split("\0")
        if len(fields) < 6:
            raise GitError("The commit was created but git did not report it back.")
        sha, short, author_name, author_email, committer_name, committer_email = fields[:6]
        mismatched = (
            author_name != identity.name or author_email != identity.email
            or committer_name != identity.name or committer_email != identity.email
        )
        if mismatched:
            # Better to stop loudly than to leave the user believing a commit
            # carries TMT's identity when it carries someone else's.
            raise GitError(
                f"Commit {short} was recorded as {author_name} <{author_email}> / "
                f"{committer_name} <{committer_email}> instead of "
                f"{identity.signature()}. The commit exists locally and was left "
                "untouched. Check for GIT_AUTHOR_* or GIT_COMMITTER_* overrides "
                "in the environment."
            )
        return {
            "sha": sha,
            "short": short,
            "author": f"{author_name} <{author_email}>",
            "committer": f"{committer_name} <{committer_email}>",
            "branch": self._branch_label(),
            "files": staged,
        }

    def push(self, branch=None, remote=None):
        """Plain push of an existing branch to an existing remote. There is no
        argument that makes it overwrite remote history, and a failure never
        changes the repository. Returns
        {'remote','branch','remote_url_host','summary'}; raises GitError
        carrying a classified, human-readable reason."""
        name = (branch or "").strip() or self.current_branch()
        if not self.branch_exists(name):
            raise GitError(
                f"There is no local branch named '{name}'. TMT does not create "
                "branches; push a branch that already exists."
            )
        target = (remote or "").strip() or self.default_remote()
        if target not in self.remotes():
            raise GitError(f"There is no remote named '{target}'.")
        host = _url_host(self.remote_url(target))
        env = dict(os.environ)
        # An interactive credential prompt would hang the subprocess until the
        # timeout; fail fast and report it as an authentication problem instead.
        env["GIT_TERMINAL_PROMPT"] = "0"
        result = self._run(
            ["push", target, name], env=env, timeout=PUSH_TIMEOUT, check=False
        )
        output = _scrub((result.stderr or "") + "\n" + (result.stdout or "")).strip()
        if result.returncode != 0:
            reason = classify_push_failure(output)
            detail = _clip(output, MAX_DETAIL_CHARS)
            raise GitError(
                f"Push of {name} to {target} ({host}) failed. {reason}\n"
                f"git said: {detail or 'nothing'}\n"
                "The commit is still in the local repository; nothing was changed."
            )
        return {
            "remote": target,
            "branch": name,
            "remote_url_host": host,
            "summary": _clip(output, MAX_DETAIL_CHARS) or f"Pushed {name} to {target}.",
        }


def _locate_root(start=None):
    override = _config("TMT_GIT_ROOT")
    candidate = override or (str(start) if start else "") or str(
        getattr(agent_config, "ROOT_DIR", Path.cwd())
    )
    path = Path(candidate).resolve()
    if path.is_file():
        path = path.parent
    if not path.is_dir():
        raise GitError(f"There is no directory at {path}, so no repository to use.")
    result = _git(["rev-parse", "--show-toplevel"], path, check=False)
    top = result.stdout.strip()
    if result.returncode != 0 or not top:
        raise GitError(
            f"{path} is not inside a git repository. Set TMT_GIT_ROOT to the "
            "repository you want TMT to work in."
        )
    return Path(top).resolve()


def _normalise(path):
    return path.replace("\\", "/").rstrip("/")


def _parse_status(raw):
    """Split `status --porcelain=v1 -z` into staged, unstaged and untracked.

    Renames and copies carry a second NUL-terminated field holding the original
    path, which belongs to the record before it and is not an entry of its own.
    """
    fields = raw.split("\0")
    staged, unstaged, untracked = [], [], []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4:
            continue
        x, y, path = entry[0], entry[1], _normalise(entry[3:])
        if x in "RC" or y in "RC":
            index += 1
        if x == "?" or y == "?":
            untracked.append(path)
            continue
        if x not in " ":
            staged.append(path)
        if y not in " ":
            unstaged.append(path)
    return staged, unstaged, untracked


def _url_host(url):
    """The host part of a remote URL, for telling the user where a push went."""
    text = _scrub(url)
    if not text:
        return "unknown host"
    if "://" in text:
        rest = text.split("://", 1)[1]
    elif "@" in text:
        # scp-style ssh remote: user@host:path
        rest = text.split("@", 1)[1]
    else:
        return "local path"
    host = rest.split("/", 1)[0].split(":", 1)[0]
    return host or "unknown host"


_PUSH_FAILURES = [
    ("authentication", (
        "authentication failed", "could not read username", "could not read password",
        "invalid username or password", "terminal prompts disabled",
        "permission denied (publickey", "authentication is not supported",
        "support for password authentication was removed", "bad credentials",
    ), "The remote rejected TMT's credentials. Git could not authenticate."),
    ("protected-branch", (
        "protected branch", "pre-receive hook declined", "gh006",
        "refusing to allow", "hook declined", "read-only",
    ), "The remote refused the update: the branch is protected by a rule or a "
       "server-side hook."),
    ("non-fast-forward", (
        "non-fast-forward", "fetch first", "updates were rejected",
        "behind its remote counterpart", "tip of your current branch is behind",
        "stale info", "cannot lock ref",
    ), "The remote branch has commits the local branch does not. Pull or "
       "rebase first; TMT will not overwrite remote history."),
    ("permission", (
        "403", "you do not have permission", "write access", "access denied",
        "insufficient permission", "permission denied", "unauthorized",
        "permission to", "not authorized",
    ), "The account behind the credentials is not allowed to write to this "
       "repository."),
    ("no-upstream", (
        "has no upstream branch", "no upstream configured",
        "does not match any", "src refspec",
    ), "The branch has no upstream and git could not work out what to push."),
    ("missing-remote", (
        "does not appear to be a git repository", "repository not found",
        "could not read from remote repository", "no such remote",
        "remote end hung up",
    ), "The remote repository could not be reached or does not exist at that URL."),
    ("network", (
        "could not resolve host", "connection timed out", "failed to connect",
        "network is unreachable", "operation timed out", "connection refused",
        "ssl", "tls", "proxy", "unable to access",
    ), "The remote could not be contacted. This looks like a network or proxy "
       "problem."),
]


def classify_push_failure(stderr):
    """Map git's stderr to one of: authentication, permission, protected-branch,
    non-fast-forward, no-upstream, missing-remote, network, unknown — as a short
    sentence for the user. Must not echo tokens or URLs containing credentials."""
    text = (stderr or "").lower()
    for _name, markers, sentence in _PUSH_FAILURES:
        if any(marker in text for marker in markers):
            return sentence
    return "Git refused the push and the reason is not one TMT recognises."
