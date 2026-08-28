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

# The git identity lives beside the modules in its own git-ignored file, for
# the same reason as the key: a fresh checkout starts with no identity rather
# than inheriting the identity of whoever cloned it.
GIT_IDENTITY_FILE = Path(__file__).resolve().parent / ".tmt_git"

def read_saved_git_identity():
    """The name and email stored in .tmt_git, or {} when there is no file.

    The file is key=value lines ("name=", "email="); blank lines, comments and
    lines without a separator are ignored so a hand-edited file still loads.
    """
    values = {}
    try:
        contents = GIT_IDENTITY_FILE.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in contents.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip().lower()] = value.strip()
    return values

_saved_git_identity = read_saved_git_identity()
TMT_GIT_NAME = os.environ.get("TMT_GIT_NAME", "") or _saved_git_identity.get("name", "") or "TMT code"
# Deliberately no fallback: an unset email must fail loudly rather than let TMT
# commit as the human whose git config happens to be on the machine.
TMT_GIT_EMAIL = os.environ.get("TMT_GIT_EMAIL", "") or _saved_git_identity.get("email", "")
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
    "git_status": [], "git_identity": [],
    "git_commit": ["message"], "git_push": [],
}
