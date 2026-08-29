"""The models TMT can run on, and which one is selected.

One source of truth. The catalogue, the persisted choice and the resolution
order all live here, so nothing else in the project decides what "the current
model" means or writes that decision anywhere of its own.
"""

import json
import os

import agent_config

# Free models on OpenRouter, taken from its live model list and filtered to
# text-capable chat models. The zero-priced list also carries audio models, a
# safety classifier and a router alias; none of those can drive a coding agent,
# so none of them are here.
FREE_MODELS = (
    {
        "id": "minimax/minimax-m3:free",
        "label": "MiniMax M3",
        "context": 1048576,
        "note": "very large context, good all-rounder",
    },
    {
        "id": "z-ai/glm-5.2:free",
        "label": "GLM 5.2",
        "context": 256000,
        "note": "strong on code",
    },
    {
        "id": "cohere/north-mini-code:free",
        "label": "Cohere North Mini Code",
        "context": 256000,
        "note": "built for code",
    },
    {
        "id": "nvidia/nemotron-3-super-120b-a12b:free",
        "label": "Nemotron 3 Super 120B",
        "context": 262144,
        "note": "large reasoning model",
    },
    {
        "id": "poolside/laguna-s-2.1:free",
        "label": "Poolside Laguna S 2.1",
        "context": 262144,
        "note": "code-focused",
    },
)

DEFAULT_MODEL = FREE_MODELS[0]["id"]

# Beside the modules, like the key and the git identity: the chosen model
# belongs to the installation, not to whichever project is open.
MODEL_FILE = agent_config.INSTALL_DIR / ".tmt_model"


def _selected_provider():
    """The provider whose model we are talking about, or the default."""
    try:
        import agent_credentials
        return agent_credentials.selected_provider()
    except Exception:
        return "openrouter"


def catalogue(provider_id=None):
    """The models on offer for a provider.

    OpenRouter keeps the curated free list verbatim. The others answer from
    their own adapter, which prefers what the provider's live model endpoint
    reports and falls back to a short built-in list when it cannot ask.
    """
    provider_id = provider_id or _selected_provider()
    if provider_id == "openrouter":
        return list(FREE_MODELS)
    try:
        import agent_providers
        models = list(getattr(agent_providers.get_provider(provider_id),
                              "FALLBACK_MODELS", []) or [])
    except Exception:
        models = []
    return models


def known_ids(provider_id=None):
    return [model["id"] for model in catalogue(provider_id)]


def _read_store():
    """The saved choices as {provider: model}.

    A file holding a bare id predates per-provider choices and belonged to
    OpenRouter, so it is read as that rather than discarded.
    """
    try:
        raw = MODEL_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            stored = json.loads(raw)
            return {k: v for k, v in stored.items() if isinstance(v, str)}
        except ValueError:
            return {}
    return {"openrouter": raw}


def read_saved_model(provider_id=None):
    """The model stored by Settings for a provider, or "" if none."""
    return _read_store().get(provider_id or _selected_provider(), "")


def provider_default(provider_id=None):
    """The model a provider should use when nothing has been chosen."""
    provider_id = provider_id or _selected_provider()
    if provider_id == "openrouter":
        return DEFAULT_MODEL
    try:
        import agent_providers
        return agent_providers.get_provider(provider_id).default_model
    except Exception:
        return DEFAULT_MODEL


def current_model(provider_id=None):
    """The model TMT should use for a provider, read at call time.

    A model id belongs to the provider that issued it: sending OpenRouter's id
    to Gemini asks for a model that does not exist there. The choice is
    therefore remembered per provider, and switching provider switches the
    model with it rather than carrying a meaningless id across.

    OPENROUTER_MODEL still wins for OpenRouter, so the existing override keeps
    behaving as it did.
    """
    provider_id = provider_id or _selected_provider()
    if provider_id == "openrouter":
        override = os.environ.get("OPENROUTER_MODEL", "").strip()
        if override:
            return override
    return read_saved_model(provider_id) or provider_default(provider_id)


def set_model(model_id, provider_id=None):
    """Persist a model choice for a provider and make it live.

    Raises ValueError for an id the provider does not offer: a typo that
    silently became the active model would only surface as a failed request
    much later.
    """
    provider_id = provider_id or _selected_provider()
    model_id = (model_id or "").strip()
    offered = known_ids(provider_id)
    if offered and model_id not in offered:
        raise ValueError(f"Not a model TMT offers for {provider_id}: {model_id!r}")
    if not model_id:
        raise ValueError("A model id is required.")
    store = _read_store()
    store[provider_id] = model_id
    MODEL_FILE.write_text(json.dumps(store, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
    if provider_id == _selected_provider():
        agent_config.MODEL = model_id
    return model_id


def describe(model_id=None, provider_id=None):
    """A short human label for a model id, falling back to the id itself."""
    model_id = model_id or current_model(provider_id)
    for model in catalogue(provider_id):
        if model["id"] == model_id:
            return model["label"]
    return model_id


def is_overridden():
    """Whether OPENROUTER_MODEL is forcing the choice.

    Settings can still write a preference, but it will not take effect while
    the environment overrides it, and saying so is better than appearing to
    ignore the user.
    """
    return bool(os.environ.get("OPENROUTER_MODEL", "").strip())
