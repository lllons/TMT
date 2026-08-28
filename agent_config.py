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
# Workspace the agent is allowed to touch: an "output" folder beside this
# module, created on first launch.
ROOT_DIR = (Path(__file__).resolve().parent / "output").resolve()
ROOT_DIR.mkdir(parents=True, exist_ok=True)

# The key lives beside the modules in a git-ignored file, so a checkout on
# another machine simply starts empty and runs first-launch setup.
KEY_FILE = Path(__file__).resolve().parent / ".tmt_key"

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
GIT_IDENTITY_FILE = Path(__file__).resolve().parent / ".tmt_git"
GIT_IDENTITY_LOCAL_FILE = Path(__file__).resolve().parent / ".tmt_git.local"
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
TMT_GIT_ROOT = os.environ.get("TMT_GIT_ROOT", "")

MODEL = os.environ.get("OPENROUTER_MODEL", "minimax/minimax-m3:free")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
APP_TITLE = "Local File AI"
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
