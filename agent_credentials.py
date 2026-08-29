"""Per-provider API credentials and the provider TMT is currently using.

Storage is obfuscated at rest, not encrypted, and the difference is worth
stating plainly. TMT has no key material to encrypt with: it never asks for a
passphrase and has nowhere safe to keep one, so anything it could use to
encrypt would have to sit beside the ciphertext and would protect nothing. The
bytes written to .tmt_providers.json are therefore only encoded, which keeps a
key from being read at a glance, echoed by a stray `cat`, or found by a
plain-text search of a backup. Anyone who can read the file can recover the
key. The real controls are the filesystem permissions on it (0600 where the
platform honours them) and the .gitignore entry that keeps it out of commits.
Nothing here may be described to a user as encryption or as secure storage.

Nothing in this module returns a whole key except credential(), which exists so
a request can be made. masked() deliberately shows too little to reconstruct
one, and no key is placed in a log, an exception message or a repr.
"""

import base64
import json
import os
from pathlib import Path

import agent_config

# The provider ids and their environment variables are spelled out here rather
# than imported from agent_providers. The store has to answer "is there a key
# for this provider" before any adapter is loaded, and a credential must not
# become unreadable because an adapter module failed to import.
PROVIDERS = ("openrouter", "openai", "anthropic", "gemini")
DEFAULT_PROVIDER = PROVIDERS[0]

KEY_ENV = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

# Overrides the stored provider for one run, the way OPENROUTER_MODEL overrides
# the stored model.
PROVIDER_ENV = "TMT_PROVIDER"

# The provider whose key predates this store. agent_config.KEY_FILE holds it.
LEGACY_PROVIDER = "openrouter"

# Where a credential came from, for a UI that has to tell the user why a key it
# cannot see is in effect. Named after the source, like agent_config's git
# identity diagnostic.
SOURCE_ENV = "environment"
SOURCE_STORE = ".tmt_providers.json"
SOURCE_LEGACY_FILE = ".tmt_key"
SOURCE_NONE = "not set"

# Installation state, anchored to the modules and not to the workspace, for the
# reason agent_config gives for KEY_FILE: credentials belong to the install, so
# they must be the same wherever TMT is run from rather than scattered across
# every directory it visits.
STORE_FILE = agent_config.INSTALL_DIR / ".tmt_providers.json"

# Encoding tag, not a cipher name. It exists so a future format can be told
# apart from this one, and so a value that was never encoded is recognisable.
_ENCODING_TAG = "obf1:"
_ENCODING_PAD = b"tmt-provider-store"

_EMPTY_STORE = {"provider": "", "credentials": {}, "legacy_key_dismissed": False}


def _encode(key):
    """Obfuscate one key for storage. Reversible by anyone; see the module docstring."""
    raw = key.encode("utf-8")
    mixed = bytes(byte ^ _ENCODING_PAD[index % len(_ENCODING_PAD)]
                  for index, byte in enumerate(raw))
    return _ENCODING_TAG + base64.b64encode(mixed).decode("ascii")


def _decode(value):
    """The key behind a stored value, or "" when it cannot be read.

    A value without the tag is returned as it stands, so a file someone edited
    by hand still loads. A tagged value that will not decode yields "" rather
    than an exception: the caller's question is whether there is a usable key,
    and the answer for damaged bytes is no.
    """
    if not isinstance(value, str) or not value:
        return ""
    if not value.startswith(_ENCODING_TAG):
        return value.strip()
    try:
        mixed = base64.b64decode(value[len(_ENCODING_TAG):], validate=True)
    except (ValueError, TypeError):
        return ""
    raw = bytes(byte ^ _ENCODING_PAD[index % len(_ENCODING_PAD)]
                for index, byte in enumerate(mixed))
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return ""


def _restrict(path):
    """Keep the store readable only by its owner, where that means anything.

    POSIX honours 0600. Windows maps it to little more than the read-only bit
    and enforces access through ACLs this cannot set, so the call is made and a
    failure is passed over quietly: a permission TMT cannot tighten is not a
    reason to refuse to save the file.
    """
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_store():
    """The stored document, normalised, or an empty one.

    Never raises. A missing, unreadable or hand-damaged file means "nothing
    stored yet" rather than a failure TMT cannot start past, and the underlying
    error is not carried out of here because its text can quote the file.
    """
    store = dict(_EMPTY_STORE)
    store["credentials"] = {}
    try:
        contents = Path(STORE_FILE).read_text(encoding="utf-8")
    except OSError:
        return store
    try:
        loaded = json.loads(contents)
    except ValueError:
        return store
    if not isinstance(loaded, dict):
        return store
    provider = loaded.get("provider", "")
    if isinstance(provider, str) and provider.strip().lower() in PROVIDERS:
        store["provider"] = provider.strip().lower()
    credentials = loaded.get("credentials", {})
    if isinstance(credentials, dict):
        for name, value in credentials.items():
            if isinstance(name, str) and name.strip().lower() in PROVIDERS:
                store["credentials"][name.strip().lower()] = value
    store["legacy_key_dismissed"] = bool(loaded.get("legacy_key_dismissed", False))
    return store


def _write_store(store):
    """Persist the document with owner-only permissions.

    Written to a neighbouring temporary file, restricted, then moved into
    place, so the store is never briefly world-readable and an interrupted
    write cannot leave a half-file where the credentials were.
    """
    path = Path(STORE_FILE)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(store, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _restrict(temporary)
    os.replace(str(temporary), str(path))
    _restrict(path)
    return store


def _known(provider_id):
    """Validate a provider id, defaulting to the selected one.

    An unknown id raises rather than reading as "no key", because a typo that
    quietly reported an unconfigured provider would send the user to re-enter a
    key they had already given.
    """
    if provider_id is None:
        return selected_provider()
    name = str(provider_id).strip().lower()
    if name not in PROVIDERS:
        raise ValueError(f"Not a provider TMT offers: {provider_id!r}")
    return name


def _legacy_key(store):
    """The pre-existing OpenRouter key in .tmt_key, or "".

    Read through agent_config so there is one definition of where that file
    lives, and skipped once the user has cleared the OpenRouter credential:
    .tmt_key stays on disk untouched, but it stops speaking for a credential
    the user has explicitly removed.
    """
    if store.get("legacy_key_dismissed"):
        return ""
    return agent_config.read_saved_key().strip()


def _resolve(provider_id):
    """(key, source) for one provider, highest precedence first.

    Environment variable, then the stored credential, then -- for OpenRouter
    alone -- the .tmt_key written by first-launch setup before this store
    existed, so nobody is asked again for a key they have already given.
    """
    name = _known(provider_id)
    from_env = os.environ.get(KEY_ENV[name], "").strip()
    if from_env:
        return from_env, SOURCE_ENV
    store = _read_store()
    stored = _decode(store["credentials"].get(name, ""))
    if stored:
        return stored, SOURCE_STORE
    if name == LEGACY_PROVIDER:
        legacy = _legacy_key(store)
        if legacy:
            return legacy, SOURCE_LEGACY_FILE
    return "", SOURCE_NONE


def selected_provider():
    """The provider TMT should use, read at call time.

    TMT_PROVIDER wins, so a one-off override behaves like OPENROUTER_MODEL. An
    unrecognised value there is ignored rather than obeyed, since acting on it
    would point every request at a provider that does not exist. Failing both,
    OpenRouter: the provider TMT used before there were others.
    """
    override = os.environ.get(PROVIDER_ENV, "").strip().lower()
    if override in PROVIDERS:
        return override
    return _read_store()["provider"] or DEFAULT_PROVIDER


def set_provider(provider_id):
    """Persist the provider choice and return the id stored."""
    name = _known(provider_id)
    store = _read_store()
    store["provider"] = name
    _write_store(store)
    return name


def is_provider_overridden():
    """Whether TMT_PROVIDER is forcing the choice.

    A preference can still be written while this is true, but it will not take
    effect, and saying so beats appearing to ignore the user.
    """
    return os.environ.get(PROVIDER_ENV, "").strip().lower() in PROVIDERS


def credential(provider_id=None):
    """The full key for a provider, or "" when there is none.

    The only function here that returns a whole key. It exists so a request can
    be made: hand the result to a request and nothing else, never to a log, a
    message or a screen.
    """
    return _resolve(provider_id)[0]


def source(provider_id=None):
    """Where the key in effect comes from: one of the SOURCE_ constants.

    Lets a screen explain an unexpected key without showing it -- an
    environment variable outranking what was just typed is otherwise silent.
    """
    return _resolve(provider_id)[1]


def has_credential(provider_id=None):
    """Whether a key is available for a provider from any source."""
    return bool(_resolve(provider_id)[0])


def set_credential(provider_id, key):
    """Store a key for a provider and return its masked form.

    Returns the mask rather than the key so a caller that echoes the result
    cannot print a credential by accident. An empty key is refused: clearing is
    what clear_credential is for, and a blank saved over a working key would
    look like a save and behave like a deletion.
    """
    name = _known(provider_id)
    value = (key or "").strip()
    if not value:
        raise ValueError("An API key cannot be empty.")
    store = _read_store()
    # A stored credential already outranks .tmt_key, so saving one does not
    # need to dismiss it. The flag stays reserved for a deliberate clear.
    store["credentials"][name] = _encode(value)
    _write_store(store)
    _refresh_config(name)
    return masked(name)


def clear_credential(provider_id=None):
    """Forget a stored key. True when something was actually removed.

    Removes only what this store holds. .tmt_key is left on disk exactly as it
    was -- it is the user's file, not TMT's to delete -- but clearing the
    OpenRouter credential also stops it being read, or the fallback would hand
    back the key the user just asked TMT to forget. An environment variable
    outranks the store and cannot be cleared from here; check source() when it
    matters whether a key is still in effect.
    """
    name = _known(provider_id)
    store = _read_store()
    removed = store["credentials"].pop(name, "") != ""
    if name == LEGACY_PROVIDER and not store["legacy_key_dismissed"]:
        removed = removed or bool(_legacy_key(store))
        store["legacy_key_dismissed"] = True
    _write_store(store)
    _refresh_config(name)
    return removed


def masked(provider_id=None):
    """A key rendered safe to display, or "" when there is none.

    Shows a short head and tail -- "sk-or-...4f2a" -- which is enough to tell
    two keys apart and never enough to reconstruct one. Short values give up
    the head as well, since on a short key those few characters would be a
    meaningful fraction of it.
    """
    key = _resolve(provider_id)[0]
    if not key:
        return ""
    if len(key) <= 8:
        return "..."
    if len(key) < 20:
        return "..." + key[-4:]
    return key[:6] + "..." + key[-4:]


def is_configured(provider_id=None):
    """Whether TMT can reach a model without asking for anything first."""
    return has_credential(provider_id)


def _refresh_config(provider_id):
    """Keep agent_config's OpenRouter key in step with the store.

    The rest of the project still reads agent_config.OPENROUTER_API_KEY, and a
    key changed in Settings has to reach this session rather than the next one.
    agent_models.set_model updates agent_config.MODEL for the same reason.
    """
    if provider_id == LEGACY_PROVIDER:
        agent_config.OPENROUTER_API_KEY = credential(LEGACY_PROVIDER)
