"""The models TMT can run on, and which one is selected.

One source of truth. The catalogue, the persisted choice and the resolution
order all live here, so nothing else in the project decides what "the current
model" means or writes that decision anywhere of its own.
"""

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


def known_ids():
    return [model["id"] for model in FREE_MODELS]


def read_saved_model():
    """The model stored by Settings, or "" when nothing has been chosen."""
    try:
        return MODEL_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def current_model():
    """The model TMT should use, read at call time.

    OPENROUTER_MODEL still wins, so a one-off override on the command line
    behaves as it always did. Otherwise the choice made in Settings applies,
    and failing that the default.
    """
    return (os.environ.get("OPENROUTER_MODEL", "").strip()
            or read_saved_model()
            or DEFAULT_MODEL)


def set_model(model_id):
    """Persist a model choice and make it live for this session.

    Raises ValueError for an id that is not in the catalogue: a typo that
    silently became the active model would only surface as a failed request
    much later.
    """
    model_id = (model_id or "").strip()
    if model_id not in known_ids():
        raise ValueError(f"Not a model TMT offers: {model_id!r}")
    MODEL_FILE.write_text(model_id + "\n", encoding="utf-8")
    agent_config.MODEL = model_id
    return model_id


def describe(model_id=None):
    """A short human label for a model id, falling back to the id itself."""
    model_id = model_id or current_model()
    for model in FREE_MODELS:
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
