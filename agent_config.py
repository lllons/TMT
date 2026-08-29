"""Configuration and optional dependency compatibility for the local agent."""

import json
import os
import re
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

try:
    import requests
    HAS_REQUESTS = True
except ModuleNotFoundError:
    HAS_REQUESTS = False
    class _Response:
        def __init__(self, response):
            self._response = response
            self.status_code = response.status
            self.text = response.read().decode("utf-8", errors="replace")

        def raise_for_status(self):
            if self.status_code >= 400:
                raise urllib.error.HTTPError(
                    self._response.url, self.status_code, self.text, None, None
                )

        def json(self):
            return json.loads(self.text)

    class _Session:
        def post(self, url, headers=None, json=None, timeout=None, verify=True):
            request = urllib.request.Request(
                url, data=json_module.dumps(json).encode("utf-8"),
                headers=headers or {}, method="POST"
            )
            try:
                return _Response(urllib.request.urlopen(request, timeout=timeout))
            except urllib.error.HTTPError as error:
                return _Response(error)

        def mount(self, *args, **kwargs):
            return None

    class _HTTPAdapter:
        pass

    class _Adapters:
        HTTPAdapter = _HTTPAdapter

    class _RequestsCompat:
        Session = _Session
        adapters = _Adapters
        HTTPError = urllib.error.HTTPError

    requests = _RequestsCompat()

try:
    from rich.console import Console
    from rich.panel import Panel
except ModuleNotFoundError:
    def _remove_console_markup(value):
        return re.sub(r"\[/?[A-Za-z][^\]]*\]", "", str(value))

    class Panel:
        def __init__(self, renderable, *args, **kwargs):
            self.renderable = renderable

        @classmethod
        def fit(cls, renderable, *args, **kwargs):
            return cls(renderable, *args, **kwargs)

        def __str__(self):
            return str(self.renderable)

    class Console:
        def print(self, *objects, **kwargs):
            print(*(_remove_console_markup(obj) for obj in objects))

        def input(self, prompt=""):
            return input(_remove_console_markup(prompt))

        @contextmanager
        def status(self, *args, **kwargs):
            yield

json_module = json
console = Console()
# The three directories TMT keeps apart, and must never conflate:
#
#   INSTALL_DIR   where TMT's own code and state live. Derived from this
#                 module's location, so it is right however TMT was launched
#                 and wherever it was installed.
#   ROOT_DIR      the project TMT may modify. Chosen once at startup.
#   Path.cwd()    consulted only to derive ROOT_DIR at startup, never after.
#
# Conflating the first two is the failure this design exists to prevent: it
# would make TMT edit its own source whenever it was run from its own folder,
# and lose track of its key and identity whenever it was run from anywhere
# else.
INSTALL_DIR = Path(__file__).resolve().parent

# The workspace TMT may modify: the directory it was started in, unless a path
# argument names another. Settled once at startup by set_workspace_root(), after the
# arguments that should decide it have been read. Nothing is created here --
# importing a module must not make directories on disk, and the workspace is
# somewhere the user already chose rather than somewhere TMT provides.
def default_workspace():
    """The directory TMT was started in.

    Read when startup asks for it rather than when this module is imported, so
    the answer cannot be decided before the arguments that should decide it.
    """
    return Path.cwd().resolve()


# A placeholder until startup replaces it. Anything importing agent_config
# outside a TMT session (the tests, tooling) gets a real directory rather than
# None, and no directory is created either way.
ROOT_DIR = default_workspace()


def set_workspace_root(path):
    """Point TMT at the workspace it may modify.

    Call once, at startup. Modules read agent_config.ROOT_DIR at call time
    rather than binding it on import, so this reaches all of them.
    """
    global ROOT_DIR
    ROOT_DIR = Path(path).expanduser().resolve()
    return ROOT_DIR


def _is_filesystem_root(path):
    return path.parent == path


def in_git_repo(path):
    """Whether `path` sits inside a git working tree.

    A filesystem check rather than a git call: this runs before anything else
    at startup, and the answer only decides how loudly to ask.
    """
    path = Path(path)
    for candidate in [path] + list(path.parents):
        if (candidate / ".git").exists():
            return True
    return False


def workspace_refusal(path):
    """Why `path` must never be a workspace, or "" if it may be one.

    A filesystem root or a home directory is not a project. Pointing something
    that can overwrite and delete at either is a mistake no confirmation
    should be able to talk anyone into, so these refuse rather than prompt.
    """
    try:
        path = Path(path).expanduser().resolve()
    except (OSError, RuntimeError) as error:
        return f"that path cannot be resolved ({error})."
    if not path.exists():
        return f"{path} does not exist. TMT selects a workspace; it does not create one."
    if not path.is_dir():
        return f"{path} is a file, not a directory."
    if _is_filesystem_root(path):
        return f"{path} is a filesystem root, which is never a project."
    if path == Path.home().resolve():
        return f"{path} is your home directory, which is never a project."
    return ""


def workspace_needs_confirmation(path):
    """Whether this workspace should be confirmed out loud before starting.

    A git repository is its own undo, so it starts silently. A directory that
    already holds files and has no version control has no such safety net, and
    is also the shape of an accidental run from the wrong place.
    """
    path = Path(path).expanduser().resolve()
    if in_git_repo(path):
        return False
    try:
        return any(path.iterdir())
    except OSError:
        return True

# Installation state, deliberately anchored to the TMT modules and NOT to the
# workspace. The key and TMT's git identity belong to the installation, so they
# are the same wherever TMT is run from. Moving them alongside the workspace
# would scatter credentials across the filesystem and give TMT a different
# identity in every directory it visited. They must not follow the CWD.
KEY_FILE = INSTALL_DIR / ".tmt_key"

def read_saved_key():
    """The API key stored by first-launch setup, or "" when there is none."""
    try:
        return KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""

def save_api_key(key):
    """Store the key for future launches and make it live for this one."""
    global OPENROUTER_API_KEY
    OPENROUTER_API_KEY = key.strip()
    KEY_FILE.write_text(OPENROUTER_API_KEY + "\n", encoding="utf-8")
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass
    return OPENROUTER_API_KEY

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "") or read_saved_key()

# TMT's git identity lives in two files beside the modules. The tracked one
# ships with the project, so every clone commits under the same TMT identity
# instead of each user inventing an address; the ".local" one is git-ignored
# and overrides it on a single machine. A commit email is public metadata
# printed in every commit, not a credential, which is why the shipped file can
# be tracked. Credentials belong in neither file.
GIT_IDENTITY_FILE = INSTALL_DIR / ".tmt_git"
GIT_IDENTITY_LOCAL_FILE = INSTALL_DIR / ".tmt_git.local"
_DEFAULT_GIT_IDENTITY_FILE = GIT_IDENTITY_FILE
_DEFAULT_GIT_IDENTITY_LOCAL_FILE = GIT_IDENTITY_LOCAL_FILE

GIT_NAME_DEFAULT = "TMT code"

# The names the git_identity diagnostic reports a value's origin by.
GIT_SOURCE_ENV = "environment"
GIT_SOURCE_LOCAL_FILE = ".tmt_git.local"
GIT_SOURCE_TRACKED_FILE = ".tmt_git"
GIT_SOURCE_DEFAULT = "built-in default"
GIT_SOURCE_UNSET = "not set"

# Both spellings are accepted so a file written for either convention loads.
_GIT_IDENTITY_KEYS = {
    "name": "name", "tmt_git_name": "name",
    "email": "email", "tmt_git_email": "email",
}

def read_git_identity_file(path):
    """The name and email in one identity file, or {} when it is absent.

    key=value lines, keys matched case-insensitively in either the
    TMT_GIT_NAME/TMT_GIT_EMAIL or the shorter name/email spelling. Blank lines,
    "#" comments and unrecognised lines are ignored so a hand-edited file still
    loads.
    """
    values = {}
    try:
        contents = Path(path).read_text(encoding="utf-8")
    except OSError:
        return values
    for line in contents.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        field = _GIT_IDENTITY_KEYS.get(key.strip().lower())
        if field:
            values[field] = value.strip()
    return values

def local_git_identity_path():
    """Where the per-machine override lives.

    It follows a redirected GIT_IDENTITY_FILE, so pointing the tracked file at
    a temporary directory cannot leave a real .tmt_git.local on the machine
    still deciding the answer.
    """
    local = Path(GIT_IDENTITY_LOCAL_FILE)
    if local != Path(_DEFAULT_GIT_IDENTITY_LOCAL_FILE):
        return local
    tracked = Path(GIT_IDENTITY_FILE)
    if tracked != Path(_DEFAULT_GIT_IDENTITY_FILE):
        return tracked.with_name(tracked.name + ".local")
    return local

def read_saved_git_identity():
    """The name and email from the tracked .tmt_git file, or {}."""
    return read_git_identity_file(GIT_IDENTITY_FILE)

def resolve_git_identity():
    """The identity TMT commits under and where each half of it came from.

    Returns {"name", "name_source", "email", "email_source"}, read at call time
    so a value changed after import is honoured. Precedence, highest first: the
    TMT_GIT_* environment variables, the git-ignored .tmt_git.local, the
    tracked .tmt_git, then a built-in default name.

    There is deliberately no default email. An unset email must fail loudly
    rather than let TMT commit as the human whose git config happens to be on
    the machine.
    """
    local = read_git_identity_file(local_git_identity_path())
    tracked = read_git_identity_file(GIT_IDENTITY_FILE)
    layers = (
        (GIT_SOURCE_ENV,
         os.environ.get("TMT_GIT_NAME", ""), os.environ.get("TMT_GIT_EMAIL", "")),
        (GIT_SOURCE_LOCAL_FILE, local.get("name", ""), local.get("email", "")),
        (GIT_SOURCE_TRACKED_FILE, tracked.get("name", ""), tracked.get("email", "")),
    )
    name, name_source = GIT_NAME_DEFAULT, GIT_SOURCE_DEFAULT
    email, email_source = "", GIT_SOURCE_UNSET
    for source, candidate, _ in layers:
        if candidate and candidate.strip():
            name, name_source = candidate.strip(), source
            break
    for source, _, candidate in layers:
        if candidate and candidate.strip():
            email, email_source = candidate.strip(), source
            break
    return {"name": name, "name_source": name_source,
            "email": email, "email_source": email_source}

_saved_git_identity = resolve_git_identity()
TMT_GIT_NAME = _saved_git_identity["name"]
TMT_GIT_EMAIL = _saved_git_identity["email"]
# Overrides repository discovery when TMT must work outside the folder it lives in.
def save_git_email(email):
    """Store TMT's co-author address for this machine and make it live now.

    Written to .tmt_git.local rather than the tracked .tmt_git, because an
    address typed on one machine is that machine's answer: committing it would
    push one user's setup onto every clone.
    """
    global TMT_GIT_EMAIL
    address = (email or "").strip()
    TMT_GIT_EMAIL = address
    existing = read_git_identity_file(GIT_IDENTITY_LOCAL_FILE)
    existing["email"] = address
    lines = ["# TMT's co-author identity on this machine. Identity only:",
             "# never put tokens, passwords or keys in this file."]
    for key, value in sorted(existing.items()):
        lines.append(f"TMT_GIT_{key.upper()}={value}")
    GIT_IDENTITY_LOCAL_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return address


TMT_GIT_ROOT = os.environ.get("TMT_GIT_ROOT", "")

# The active model. agent_models owns the catalogue and the persisted choice;
# this is kept as the name the rest of the project already reads, and is
# refreshed by agent_models.set_model. Read it through agent_config.MODEL rather
# than binding it on import, or a change made in Settings will not be seen.
MODEL = os.environ.get("OPENROUTER_MODEL", "").strip() or "minimax/minimax-m3:free"


def refresh_model():
    """Re-read the selected model. Called once at startup, after imports.

    agent_models imports agent_config, so agent_config cannot import it back at
    module scope; the lookup is deferred to here instead of inverting the
    dependency for one value.
    """
    global MODEL
    try:
        import agent_models
        MODEL = agent_models.current_model()
    except Exception:
        pass
    return MODEL


# --- effort ----------------------------------------------------------------
#
# How much work TMT is willing to spend on one task, in the two places that
# actually cost anything: how long a reply the provider is asked for, and how
# many rounds of the agent loop a single question may take.
#
# Those two and nothing else. A reasoning-effort field would be the obvious
# thing to send, but only some models on some providers accept one and the
# rest reject the request outright, so it would turn a setting into a
# provider-specific failure. `max_tokens` is understood by all four adapters
# and the loop bound is TMT's own, so both are real everywhere.
#
# Kept beside the model choice in INSTALL_DIR: it belongs to the installation,
# not to whichever project happens to be open.
EFFORT_FILE = INSTALL_DIR / ".tmt_effort"
DEFAULT_EFFORT = "medium"

# (max_tokens asked of the provider, rounds of the agent loop per question).
# Medium is exactly what TMT did before this setting existed, so a user who
# never touches it sees no change at all.
#
# 4096 is a floor, not a starting point: low spends fewer rounds but asks for
# replies of the same length. Every reply here is one JSON object, and the
# ones that matter carry a file's whole contents inside it -- so a max_tokens
# small enough to bite does not make the model terser, it cuts the object off
# mid-string. What comes back then is unparseable, the write never happens,
# and the work is lost. On Anthropic the field is documented as a hard stop
# rather than a ceiling, which is the same thing said out loud.
EFFORT_LEVELS = {
    "low": {"max_tokens": 4096, "rounds": 12},
    "medium": {"max_tokens": 4096, "rounds": 35},
    "high": {"max_tokens": 8192, "rounds": 60},
}

EFFORT = DEFAULT_EFFORT


def read_saved_effort():
    """The effort level stored on disk, or the default.

    Anything unrecognised is the default rather than an error: a settings file
    that has been edited by hand should not stop TMT starting.
    """
    try:
        stored = EFFORT_FILE.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return DEFAULT_EFFORT
    return stored if stored in EFFORT_LEVELS else DEFAULT_EFFORT


def refresh_effort():
    """Re-read the stored effort. Called at startup, beside refresh_model."""
    global EFFORT
    EFFORT = read_saved_effort()
    return EFFORT


def set_effort(level):
    """Persist an effort level and make it live.

    Raises ValueError for anything not offered, so a typo cannot become the
    active setting and surface much later as a request that behaves oddly.
    """
    global EFFORT
    level = str(level or "").strip().lower()
    if level not in EFFORT_LEVELS:
        # Named in the order they mean something in, not alphabetically:
        # "high, low, medium" reads as a list of unrelated words.
        raise ValueError("Effort is one of %s; got %r."
                         % (", ".join(effort_names()), level))
    EFFORT_FILE.write_text(level + "\n", encoding="utf-8")
    EFFORT = level
    return level


def effort_names():
    """The levels, in the order they escalate rather than alphabetically."""
    return sorted(EFFORT_LEVELS,
                  key=lambda name: (EFFORT_LEVELS[name]["rounds"],
                                    EFFORT_LEVELS[name]["max_tokens"]))


def effort_settings(level=None):
    """What an effort level actually changes, read at request time."""
    return dict(EFFORT_LEVELS[(level or EFFORT) if (level or EFFORT) in EFFORT_LEVELS
                              else DEFAULT_EFFORT])


def max_tokens_for_effort(level=None):
    return effort_settings(level)["max_tokens"]


def rounds_for_effort(level=None):
    return effort_settings(level)["rounds"]


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# The app's own name. It is sent to OpenRouter as X-Title, so it is what
# requests are attributed to there as well as what TMT calls itself.
APP_TITLE = "TMT"
APP_URL = "http://localhost"
# Ask the provider to constrain the reply to a syntactically valid JSON
# object: no prose, no markdown fences, no stray text around it. This is a
# grammar constraint only -- our action schema is enforced by the system
# prompt and validate_action. Models that reject response_format fall back
# automatically (see JSON_MODE_REJECTIONS in agent_model).
USE_JSON_MODE = True
_json_mode_ok = USE_JSON_MODE
# Live relay: stream model output as it is generated. Requires the real
# requests package (the urllib fallback shim above cannot stream responses).
STREAM_ENABLED = HAS_REQUESTS and os.environ.get("TMT_STREAM", "1").lower() not in {"0", "false", "no", "off"}
FORCE_IPV4 = True
VERIFY_SSL = True

class _IPv4Adapter(requests.adapters.HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        kwargs["source_address"] = ("0.0.0.0", 0)
        return super().init_poolmanager(*args, **kwargs)

_session = requests.Session()
if FORCE_IPV4:
    _session.mount("https://", _IPv4Adapter())
if not VERIFY_SSL:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# A workspace can be a real project rather than a scratch folder, so every
# reader of it needs a ceiling. Without one, a large tree either floods the
# model's context or spends seconds being walked on every turn.
SNAPSHOT_MAX_FILES = 60          # files inlined into the prompt
SNAPSHOT_MAX_BYTES = 40000       # total inlined characters
SNAPSHOT_MAX_FILE_BYTES = 8000   # per-file ceiling, as before
WORKSPACE_MAX_SCAN = 20000       # directory entries examined before giving up
LIST_FILES_MAX = 400             # paths returned by list_files

MUTATING_ACTIONS = {
    "write_file", "append_file", "write_files", "patch_file", "delete_file",
    "rename_file", "replace_lines", "copy_file", "delete_folder",
}

REQUIRED_KEYS = {
    "write_file": ["path", "content"], "append_file": ["path", "content"],
    "write_files": ["files"], "patch_file": ["path", "search", "replace"],
    "delete_file": ["path"], "read_file": ["path"], "rename_file": ["path"],
    "run_python": ["path"], "run_file": ["path"], "create_folder": ["path"],
    "open_app": ["app"], "list_files": [], "search_files": ["query"],
    "read_lines": ["path"], "replace_lines": ["path", "start", "end", "content"],
    "copy_file": ["path"], "delete_folder": ["path"], "respond": ["message"],
    "done": [],
    "git_status": [], "git_identity": [], "git_diff": [],
    "git_commit": ["message"], "git_push": [],
}
