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
# A FREE TIER IS SOMEBODY ELSE'S BUSINESS DECISION, AND THIS LIST ROTS.
# `minimax/minimax-m3:free` and `z-ai/glm-5.2:free` were both here until
# 2026-09-08, when both began answering HTTP 404 with "This model is
# unavailable for free. The paid version is available now" -- and the first of
# them was DEFAULT_MODEL, so a fresh install picked a dead model off its own
# first screen. Every entry below was checked against OpenRouter's live model
# listing on that date: zero-priced for both prompt and completion, text
# input, and each one sent a real request that came back with the JSON it was
# asked for. That is a measurement with a date on it, not a guarantee; when a
# model here starts 404ing, this is the list to re-check.
FREE_MODELS = (
    {
        "id": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "label": "Nemotron 3 Ultra 550B",
        "context": 1000000,
        "note": "frontier reasoning, very large context",
    },
    {
        "id": "nvidia/nemotron-3-super-120b-a12b:free",
        "label": "Nemotron 3 Super 120B",
        "context": 262144,
        "note": "large reasoning model",
    },
    {
        "id": "nvidia/nemotron-3.5-lightning:free",
        "label": "Nemotron 3.5 Lightning",
        "context": 1000000,
        "note": "fast, very large context",
    },
    {
        "id": "cohere/north-mini-code:free",
        "label": "Cohere North Mini Code",
        "context": 256000,
        "note": "built for code",
    },
    {
        "id": "poolside/laguna-s-2.1:free",
        "label": "Poolside Laguna S 2.1",
        "context": 262144,
        "note": "code-focused",
    },
    {
        # Last, and the note says what it is for rather than how big it is:
        # its vendor describes it as finance-tuned, which is the one thing a
        # reader choosing a model to write code with needs to know about it.
        "id": "inclusionai/ling-3.0-flash-fin:free",
        "label": "Ling 3.0 Flash Fin",
        "context": 262144,
        "note": "finance-tuned",
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
    saved = read_saved_model(provider_id)
    # A SAVED CHOICE THE CATALOGUE NO LONGER OFFERS IS DROPPED, and that is
    # what makes a catalogue change reach anybody who has already used
    # Settings. `set_model` refuses an id the provider does not offer, so a
    # saved id was valid when it was written -- what invalidates it later is
    # the list moving underneath it, which is exactly what happened on
    # 2026-09-08 when `minimax/minimax-m3:free` left the free tier. Without
    # this, updating TMT changed the catalogue and every existing user went on
    # running the dead model, because the file wins over the default.
    #
    # Only where the provider HAS a catalogue to check against: `set_model`
    # allows any id for a provider that offers no list, and dropping one of
    # those would throw away a deliberate choice.
    if saved:
        offered = known_ids(provider_id)
        if not offered or saved in offered:
            return saved
    return provider_default(provider_id)


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
