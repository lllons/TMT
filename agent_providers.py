"""AI provider adapters: one HTTP shape per provider, behind one interface.

TMT reaches a model through exactly one call, so everything a provider needs
that differs -- its URL, its authentication header, the shape of its request
body, the shape of its stream -- is confined to an adapter here. The text that
comes out is identical whichever adapter produced it, which is what lets the
existing StreamingActionParser stay provider agnostic.

Two conversions in this module are load bearing and silent when wrong:

    OpenAI-compatible providers take the system prompt as a message with role
    "system". Anthropic takes it as a top-level "system" string and Gemini as
    "systemInstruction", and both reject or ignore a system role inside the
    message array. An adapter that leaves it there still gets replies -- it
    just gets replies from a model that was never given TMT's rules. Every
    adapter therefore lifts the system prompt out explicitly in
    convert_messages() rather than passing the array through.

    Gemini also names the assistant role "model". A wrong role there makes the
    model read its own previous turns as the user's instructions.

Transport is raw HTTP through agent_config._session, which is requests or a
urllib shim that cannot stream; agent_config.STREAM_ENABLED already reflects
that. No provider SDKs, no new dependencies.

No API key is ever logged, printed, put in a URL query string, or allowed into
an error message. Gemini is commonly shown with the key as a query parameter;
this module uses the x-goog-api-key header instead, and _send refuses outright
to fetch a URL the key appears in.
"""

import json
import re
import urllib.error
import urllib.request

import agent_config

PROVIDERS = ("openrouter", "openai", "anthropic", "gemini")
DEFAULT_PROVIDER = "openrouter"

DEFAULT_MAX_TOKENS = 4096
REQUEST_TIMEOUT = 120
ERROR_BODY_LIMIT = 300

# Mirrors agent_model.JSON_MODE_REJECTIONS. Duplicated rather than imported:
# agent_model is the module that will eventually call into this one, and the
# import would become a cycle the moment it does.
JSON_MODE_REJECTIONS = ("response_format", "json_object", "structured output")

ERROR_KINDS = ("auth", "rate_limit", "network", "not_found", "bad_request",
               "server", "malformed", "unknown")

# What the UI can usefully tell a user for each kind. Advice only; never the
# provider's own words, which may be anything.
ERROR_ADVICE = {
    "auth": "The provider rejected the API key. Check it in Settings.",
    "rate_limit": "The provider is rate limiting this key. Wait and try again.",
    "network": "The provider could not be reached. Check the connection.",
    "not_found": "The provider does not know that model or endpoint.",
    "bad_request": "The provider rejected the request as malformed.",
    "server": "The provider is having trouble at its end. Try again shortly.",
    "malformed": "The provider replied with something this version cannot read.",
    "unknown": "The provider call failed.",
}


class ProviderError(RuntimeError):
    """A provider call failed. The message is safe to show a user.

    Carries ``kind`` from ERROR_KINDS so the caller can advise rather than
    guess, and ``status`` when an HTTP status was involved. The message has
    already been passed through redact(), so it holds no key material.
    """

    def __init__(self, message, kind="unknown", status=None):
        super().__init__(message)
        self.kind = kind if kind in ERROR_KINDS else "unknown"
        self.status = status

    def advice(self):
        return ERROR_ADVICE.get(self.kind, ERROR_ADVICE["unknown"])


def classify_status(status):
    """The ProviderError kind an HTTP status implies.

    403 counts as auth: every provider here uses it for a key that exists but
    may not do this, which is a credential problem from the user's side.
    """
    if status in (401, 403):
        return "auth"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limit"
    if status in (400, 422):
        return "bad_request"
    if isinstance(status, int) and status >= 500:
        return "server"
    return "unknown"


# Provider error payloads name their own failure type; these are the names used
# by Anthropic and, for the overlapping ones, by the OpenAI-compatible APIs.
ERROR_TYPE_KINDS = {
    "authentication_error": "auth",
    "invalid_api_key": "auth",
    "permission_error": "auth",
    "permission_denied": "auth",
    "invalid_request_error": "bad_request",
    "not_found_error": "not_found",
    "rate_limit_error": "rate_limit",
    "insufficient_quota": "rate_limit",
    "overloaded_error": "server",
    "api_error": "server",
}

# Anything shaped like a key that reaches an error string is removed even when
# it is not the key we sent: providers echo request material back, and an error
# string ends up on screen and in logs.
_KEY_SHAPED = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|AIza[A-Za-z0-9_\-]{10,})")
_REDACTED = "[redacted]"


def redact(text, key=""):
    """Remove key material from text that is about to be shown or raised."""
    text = "" if text is None else str(text)
    key = (key or "").strip()
    if key and len(key) >= 8:
        text = text.replace(key, _REDACTED)
    return _KEY_SHAPED.sub(_REDACTED, text)


def _body_excerpt(text, key=""):
    return redact(text, key)[:ERROR_BODY_LIMIT]


def streaming_supported():
    """Whether the transport can deliver a response incrementally.

    False when requests is absent: the urllib shim reads the whole body before
    returning, so a stream request would arrive complete and late rather than
    in fragments.
    """
    return bool(agent_config.STREAM_ENABLED)


def _session():
    """The shared HTTP session, read at call time.

    Read rather than bound at import so that replacing agent_config._session --
    which is how this module is tested -- actually takes effect.
    """
    return agent_config._session


def _guard_url(url, key):
    """Refuse to send a URL the key appears in.

    A key in a query string is logged by proxies, servers and browser history
    and cannot be unsent. This is the last line of defence behind using headers
    everywhere; it should never fire.
    """
    if key and len(key) >= 8 and key in url:
        raise ProviderError("refusing to send the API key in a URL", kind="bad_request")


def _guard_key(key):
    """Refuse to send a key that is empty or cannot go in a header.

    A newline or control character in a header value is header injection, and
    an empty key would produce a request that fails for a reason nothing could
    diagnose from the provider's answer.
    """
    key = key or ""
    if not key.strip():
        raise ProviderError("No API key is set for this provider.", kind="auth")
    if any(character in key for character in "\r\n\t\0"):
        raise ProviderError("The stored API key contains a line break and cannot be sent.",
                            kind="auth")


def _send(url, headers, payload, key, stream=False):
    """POST JSON through the shared session and return the response object."""
    _guard_key(key)
    _guard_url(url, key)
    session = _session()
    kwargs = {"headers": headers, "json": payload, "timeout": REQUEST_TIMEOUT,
              "verify": agent_config.VERIFY_SSL}
    if stream and streaming_supported():
        # The urllib shim takes no stream argument and could not honour one.
        # Asking anyway would fail the request outright rather than degrade to
        # a whole body, which iter_sse can still read.
        kwargs["stream"] = True
    try:
        response = session.post(url, **kwargs)
    except ProviderError:
        raise
    except Exception as error:  # connection refused, DNS failure, timeout, TLS
        raise ProviderError(redact(f"{type(error).__name__}: {error}", key), kind="network")
    # Server-sent events are UTF-8 by definition, but requests falls back to
    # ISO-8859-1 for a text/* response that does not name a charset, and
    # text/event-stream usually does not. Left to guess it turns every
    # multi-byte character into mojibake that nothing downstream can undo.
    if getattr(response, "encoding", None) is not None:
        try:
            response.encoding = "utf-8"
        except (AttributeError, TypeError):
            pass
    return response


def _get(url, headers, key):
    """GET JSON through the shared session, falling back to urllib.

    The urllib shim agent_config installs when requests is absent implements
    only post(), and the model listings are GETs; urllib is already a
    dependency of that shim, so this adds nothing new.
    """
    _guard_key(key)
    _guard_url(url, key)
    getter = getattr(_session(), "get", None)
    if callable(getter):
        try:
            response = getter(url, headers=headers, timeout=REQUEST_TIMEOUT,
                              verify=agent_config.VERIFY_SSL)
        except Exception as error:
            raise ProviderError(redact(f"{type(error).__name__}: {error}", key), kind="network")
        return _status_of(response), _text_of(response)
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        opened = urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT)
        return opened.status, opened.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")
    except Exception as error:
        raise ProviderError(redact(f"{type(error).__name__}: {error}", key), kind="network")


def _status_of(response):
    status = getattr(response, "status_code", None)
    if status is None:
        status = getattr(response, "status", None)
    return status


def _text_of(response):
    try:
        return getattr(response, "text", "") or ""
    except Exception:
        return ""


def _raise_for_status(response, key, provider_label):
    """Turn a non-2xx response into a classified ProviderError.

    status_code is read directly rather than through raise_for_status(),
    because the requests package and the urllib shim raise different exception
    types for it.
    """
    status = _status_of(response)
    if not isinstance(status, int) or status < 400:
        return
    detail = _error_message(_parse_json(_text_of(response)), key) or _body_excerpt(_text_of(response), key)
    kind = classify_status(status)
    raise ProviderError(f"{provider_label} HTTP {status}: {detail}".strip(), kind=kind, status=status)


def _parse_json(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _error_message(data, key=""):
    """The human part of a provider error body, redacted, or ""."""
    if not isinstance(data, dict):
        return ""
    error = data.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("type") or ""
    elif error:
        message = str(error)
    else:
        message = data.get("message") or ""
    return _body_excerpt(message, key)


def _error_kind(data, default="unknown"):
    """The kind named by a provider error body, or ``default``."""
    if not isinstance(data, dict):
        return default
    error = data.get("error")
    if not isinstance(error, dict):
        return default
    for field in ("type", "code", "status"):
        value = error.get(field)
        if isinstance(value, str) and value.lower() in ERROR_TYPE_KINDS:
            return ERROR_TYPE_KINDS[value.lower()]
    status = error.get("code")
    if isinstance(status, int):
        return classify_status(status)
    return default


def iter_sse(response):
    """Yield decoded JSON objects from a server-sent event response.

    Comments, keep-alives, event-name lines and the [DONE] sentinel are dropped
    here so no adapter has to know about SSE framing. A data line that is not
    JSON is skipped rather than fatal: providers send heartbeats in that form.
    """
    lines = getattr(response, "iter_lines", None)
    if callable(lines):
        source = lines(decode_unicode=True)
    else:
        source = _text_of(response).splitlines()
    for line in source:
        if not line:
            continue
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        line = line.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        event = _parse_json(data)
        if isinstance(event, dict):
            yield event


def _close(response):
    close = getattr(response, "close", None)
    if close:
        try:
            close()
        except Exception:
            pass


# JSON mode is a grammar constraint that keeps the model emitting a parseable
# action object. Providers that reject it are detected from their own error
# text and the constraint is dropped for the rest of the session, exactly as
# agent_model does today.
_json_mode_ok = agent_config.USE_JSON_MODE


def json_mode_enabled():
    return _json_mode_ok


def disable_json_mode():
    global _json_mode_ok
    _json_mode_ok = False


def is_json_mode_rejection(message):
    lowered = str(message or "").lower()
    return any(token in lowered for token in JSON_MODE_REJECTIONS)


class Provider:
    """One provider's wire format. Stateless; holds no key and no session.

    Subclasses supply the endpoints and override only the parts of the shape
    that actually differ. Every method that talks to the network raises
    ProviderError and nothing else.
    """

    id = ""
    label = ""
    key_env = ""
    key_url = ""
    default_model = ""
    chat_url = ""
    models_url = ""

    # Local shape check only. Advisory: a key can be the right shape and dead,
    # or an unusual shape and live, so this never decides anything on its own.
    key_prefix = ""
    key_min_length = 20

    supports_json_mode = False

    # Used only when list_models() cannot reach the provider. Kept short and
    # marked stale in each entry's note, because a hardcoded catalogue rots.
    FALLBACK_MODELS = ()

    # TMT's roles on the left, the provider's on the right. "system" is absent
    # on purpose: it is lifted out by convert_messages, never mapped.
    ROLE_MAP = {"user": "user", "assistant": "assistant"}

    def headers(self, key, stream=False):
        raise NotImplementedError

    def convert_messages(self, messages):
        """Split TMT's message list into (system_text, provider_messages).

        The system prompt is always lifted out, for every provider, so that the
        adapters that must place it elsewhere and the adapters that put it back
        as a message are visibly doing the same conversion. Several system
        messages join with a blank line; TMT sends one.
        """
        system_parts, converted = [], []
        for message in messages or ():
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user").strip().lower()
            content = message.get("content")
            content = "" if content is None else str(content)
            if role == "system":
                if content.strip():
                    system_parts.append(content)
                continue
            if not content:
                continue  # an empty turn is rejected outright by some providers
            converted.append(self.message(self.ROLE_MAP.get(role, "user"), content))
        return "\n\n".join(system_parts), converted

    def message(self, role, content):
        """One message in the provider's own shape."""
        return {"role": role, "content": content}

    def chat_payload(self, messages, model=None, stream=False,
                     max_tokens=DEFAULT_MAX_TOKENS, json_mode=None):
        """Return (url, body) for a completion request."""
        raise NotImplementedError

    def use_json_mode(self, json_mode):
        if json_mode is None:
            json_mode = json_mode_enabled()
        return bool(json_mode) and self.supports_json_mode

    def parse_stream_chunk(self, event):
        """The generated text carried by one stream event, or ""."""
        return ""

    def parse_stream_usage(self, event):
        """The provider's own output-token count, or None.

        Also accepts a complete non-streamed body, so the two paths report
        usage the same way.
        """
        return None

    def parse_stream_error(self, event):
        """A ProviderError for an error event, or None.

        Every provider here reports a mid-stream failure as an object with an
        "error" member, so the base handles all four.
        """
        if not isinstance(event, dict) or not event.get("error"):
            return None
        message = _error_message(event) or "the provider reported an error"
        return ProviderError(f"{self.label}: {message}", kind=_error_kind(event))

    def parse_response(self, data):
        """The full assistant text from a non-streamed reply."""
        raise NotImplementedError

    def _malformed(self, data):
        try:
            excerpt = json.dumps(data)
        except (TypeError, ValueError):
            excerpt = str(data)
        return ProviderError(
            f"{self.label} replied with an unexpected shape: {_body_excerpt(excerpt)}",
            kind="malformed")

    def list_models(self, key):
        """Models the provider reports for this key.

        Queries the provider's real models endpoint, because the only honest
        source for what exists today is the provider. Falls back to
        FALLBACK_MODELS when the call cannot be made, and those entries say so
        in their note.
        """
        try:
            status, text = _get(self.models_url, self.headers(key), key)
        except ProviderError:
            return list(self.FALLBACK_MODELS)
        if not isinstance(status, int) or status >= 400:
            return list(self.FALLBACK_MODELS)
        data = _parse_json(text)
        models = self.parse_models(data)
        return models or list(self.FALLBACK_MODELS)

    def parse_models(self, data):
        return []

    # The endpoint the key is checked against. It must be one that REJECTS a
    # bad key: OpenRouter's model list is public and answers 200 for any string,
    # so checking against it reported every typo as a working key. Defaults to
    # the model list, which does require the key on the other three.
    auth_url = ""

    def validate_credentials(self, key):
        """Check a key against the provider. Returns (valid, explanation).

        True is returned only after a request the provider answered without an
        authentication failure. Everything else is False, and the explanation
        distinguishes "the provider rejected this" from "this could not be
        checked" -- reporting a key as valid without having asked would be the
        one answer that cannot be recovered from later.

        An unexpected prefix is deliberately not grounds to refuse: only the
        provider knows what it issues, and looks_like_key is advice for the
        person typing, not a gate in front of the real check.
        """
        key = (key or "").strip()
        if not key:
            return False, "No key entered."
        if any(character in key for character in "\r\n\t\0"):
            return False, "That key contains a line break, so it cannot be sent as it stands."
        endpoint = self.auth_url or self.models_url
        if not endpoint:
            return False, ("There is no endpoint to check this key against, so it "
                           "has not been verified.")
        try:
            status, text = _get(endpoint, self.headers(key), key)
        except ProviderError as error:
            return False, f"Could not check the key: {error}. It has not been verified."
        if not isinstance(status, int):
            return False, "Could not check the key: the provider gave no HTTP status. It has not been verified."
        kind = classify_status(status)
        if kind == "auth":
            detail = _error_message(_parse_json(text), key)
            return False, f"{self.label} rejected this key" + (f": {detail}" if detail else ".")
        if status >= 400:
            detail = _error_message(_parse_json(text), key) or f"HTTP {status}"
            return False, f"Could not check the key: {detail}. It has not been verified."
        return True, f"{self.label} accepted this key."

    def looks_like_key(self, key):
        """A local shape check. Returns (plausible, reason).

        No network, no claim about validity: only validate_credentials can say
        a key works. The reason never contains any part of the key.
        """
        key = (key or "").strip()
        if not key:
            return False, "No key entered."
        if any(character.isspace() for character in key):
            return False, "That contains a space, so part of the key is probably missing."
        if len(key) < self.key_min_length:
            return False, f"That is too short to be a {self.label} key."
        if self.key_prefix and not key.startswith(self.key_prefix):
            return False, f"{self.label} keys normally start with {self.key_prefix}."
        return True, ""

    def describe(self):
        return f"{self.label} ({self.id})"


class _OpenAICompatible(Provider):
    """The OpenAI chat-completions shape, shared by OpenRouter and OpenAI.

    The system prompt goes back into the message array as a "system" message,
    which is what this family expects. It is still lifted out and reinserted
    rather than passed through untouched, so that the one place that decides
    where the system prompt goes is the same for all four providers.
    """

    supports_json_mode = True

    def headers(self, key, stream=False):
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        if stream:
            headers["Accept"] = "text/event-stream"
        return headers

    def chat_payload(self, messages, model=None, stream=False,
                     max_tokens=DEFAULT_MAX_TOKENS, json_mode=None):
        system, converted = self.convert_messages(messages)
        body = ([{"role": "system", "content": system}] if system else []) + converted
        payload = {"model": model or self.default_model,
                   "max_tokens": max_tokens,
                   "messages": body}
        if stream:
            payload["stream"] = True
        if self.use_json_mode(json_mode):
            payload["response_format"] = {"type": "json_object"}
        return self.chat_url, payload

    def parse_stream_chunk(self, event):
        parts = []
        for choice in event.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            content = (choice.get("delta") or {}).get("content")
            if content:
                parts.append(content)
        return "".join(parts)

    def parse_stream_usage(self, event):
        usage = (event or {}).get("usage") or {}
        for field in ("completion_tokens", "output_tokens"):
            value = usage.get(field)
            if isinstance(value, int) and value >= 0:
                return value
        return None

    def parse_response(self, data):
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise self._malformed(data)

    def parse_models(self, data):
        entries = (data or {}).get("data")
        if not isinstance(entries, list):
            return []
        models = []
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            models.append({
                "id": entry["id"],
                "label": entry.get("name") or entry.get("display_name") or entry["id"],
                "context": entry.get("context_length") or entry.get("context_window") or 0,
                "note": "",
            })
        return models


class OpenRouterProvider(_OpenAICompatible):
    """OpenRouter, the provider TMT has always used.

    The request is byte-for-byte what agent_model sends today, referer and
    title headers included: those identify the app to OpenRouter and changing
    them changes how requests are attributed.
    """

    id = "openrouter"
    label = "OpenRouter"
    key_env = "OPENROUTER_API_KEY"
    key_url = "https://openrouter.ai/keys"
    chat_url = agent_config.OPENROUTER_URL
    models_url = "https://openrouter.ai/api/v1/models"
    # The model list is public here, so it cannot answer "is this key real".
    # This endpoint describes the key behind the request and 401s without one.
    auth_url = "https://openrouter.ai/api/v1/key"
    key_prefix = "sk-or-"

    @property
    def default_model(self):
        """The model already in force, read at call time.

        Settings writes agent_config.MODEL through agent_models; binding it
        here on import would pin whatever was selected when TMT started.
        """
        return agent_config.MODEL

    def headers(self, key, stream=False):
        headers = super().headers(key, stream=stream)
        headers["HTTP-Referer"] = agent_config.APP_URL
        headers["X-Title"] = agent_config.APP_TITLE
        return headers

    def _curated(self):
        try:
            import agent_models
            return [dict(model) for model in agent_models.FREE_MODELS]
        except Exception:
            return []

    def list_models(self, key):
        """The curated free models first, then anything else free found live.

        The five in agent_models are the ones TMT ships with and they stay at
        the top with their own labels; the live query only adds to them, so a
        model disappearing from OpenRouter cannot empty the picker.
        """
        curated = self._curated()
        try:
            status, text = _get(self.models_url, self.headers(key), key)
        except ProviderError:
            return curated
        if not isinstance(status, int) or status >= 400:
            return curated
        known = {model["id"] for model in curated}
        extra = [model for model in self.parse_models(_parse_json(text))
                 if model["id"] not in known]
        return curated + extra

    def parse_models(self, data):
        """Free, text-producing chat models from OpenRouter's live list.

        Zero-priced is not the same as usable: the free tier also carries audio
        models, a safety classifier and router aliases. Output modality is the
        cheapest filter that removes them, and it is best effort -- entries
        that do not declare one are kept.
        """
        entries = (data or {}).get("data")
        if not isinstance(entries, list):
            return []
        free = {}
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            price = float_or_none((entry.get("pricing") or {}).get("prompt"))
            if price is None or price != 0:
                continue
            modalities = (entry.get("architecture") or {}).get("output_modalities")
            if isinstance(modalities, list) and modalities and "text" not in modalities:
                continue
            free[entry["id"]] = entry
        return [{"id": entry["id"],
                 "label": entry.get("name") or entry["id"],
                 "context": entry.get("context_length") or 0,
                 "note": "free"}
                for entry in free.values()]


class OpenAIProvider(_OpenAICompatible):
    """OpenAI's own chat completions endpoint."""

    id = "openai"
    label = "OpenAI"
    key_env = "OPENAI_API_KEY"
    key_url = "https://platform.openai.com/api-keys"
    chat_url = "https://api.openai.com/v1/chat/completions"
    models_url = "https://api.openai.com/v1/models"
    key_prefix = "sk-"
    default_model = "gpt-4o"

    # A guess, not a catalogue. OpenAI's current model ids cannot be verified
    # without a key, so these exist only to keep the picker from being empty
    # when discovery fails, and every entry says so.
    FALLBACK_MODELS = (
        {"id": "gpt-4o", "label": "GPT-4o", "context": 128000,
         "note": "fallback entry, may be out of date"},
        {"id": "gpt-4o-mini", "label": "GPT-4o mini", "context": 128000,
         "note": "fallback entry, may be out of date"},
        {"id": "gpt-4.1", "label": "GPT-4.1", "context": 128000,
         "note": "fallback entry, may be out of date"},
    )

    # Chat is one of several things this endpoint lists; these families cannot
    # answer a chat completion at all.
    NON_CHAT_PREFIXES = ("text-embedding", "whisper", "tts", "dall-e", "gpt-image",
                         "omni-moderation", "text-moderation", "babbage", "davinci",
                         "sora", "computer-use-preview")

    def parse_models(self, data):
        return [model for model in super().parse_models(data)
                if not model["id"].startswith(self.NON_CHAT_PREFIXES)]


class AnthropicProvider(Provider):
    """Anthropic's Messages API.

    Two differences that bite: the system prompt is a top-level string rather
    than a message, and max_tokens is required rather than optional.
    """

    id = "anthropic"
    label = "Anthropic"
    key_env = "ANTHROPIC_API_KEY"
    key_url = "https://console.anthropic.com/settings/keys"
    chat_url = "https://api.anthropic.com/v1/messages"
    models_url = "https://api.anthropic.com/v1/models"
    key_prefix = "sk-ant-"
    default_model = "claude-sonnet-5"
    API_VERSION = "2023-06-01"

    # Verified against the bundled Claude API reference rather than guessed, so
    # unlike the other fallbacks these are current ids, used when the live
    # listing cannot be reached.
    FALLBACK_MODELS = (
        {"id": "claude-opus-5", "label": "Claude Opus 5", "context": 200000,
         "note": "most capable"},
        {"id": "claude-sonnet-5", "label": "Claude Sonnet 5", "context": 200000,
         "note": "balanced"},
        {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5", "context": 200000,
         "note": "fastest"},
        {"id": "claude-opus-4-8", "label": "Claude Opus 4.8", "context": 200000,
         "note": "previous generation"},
        {"id": "claude-fable-5", "label": "Claude Fable 5", "context": 200000,
         "note": "writing focused"},
    )

    def headers(self, key, stream=False):
        headers = {
            "x-api-key": key,
            "anthropic-version": self.API_VERSION,
            "content-type": "application/json",
        }
        if stream:
            headers["accept"] = "text/event-stream"
        return headers

    def chat_payload(self, messages, model=None, stream=False,
                     max_tokens=DEFAULT_MAX_TOKENS, json_mode=None):
        system, converted = self.convert_messages(messages)
        payload = {"model": model or self.default_model,
                   # Required by this API, not merely a ceiling as elsewhere.
                   "max_tokens": max_tokens,
                   "messages": converted}
        if system:
            # Top level, never a message: a system role inside "messages" is
            # rejected, and dropping it would silently produce an agent that
            # answers without any of TMT's rules.
            #
            # Sent as one block carrying a cache breakpoint. The system prompt
            # is the largest and by far the most repeated thing in a request:
            # a turn takes several steps, the API is stateless, so the whole
            # prompt goes again on every one of them. The breakpoint does not
            # change what is sent -- it cannot, and TMT does not pretend the
            # count drops -- it lets the provider charge for reading the
            # prefix back rather than for reading it afresh. A prompt shorter
            # than the provider's minimum is simply not cached, which costs
            # nothing and needs no test here.
            payload["system"] = [{"type": "text", "text": system,
                                  "cache_control": {"type": "ephemeral"}}]
        if stream:
            payload["stream"] = True
        return self.chat_url, payload

    def parse_stream_chunk(self, event):
        if event.get("type") != "content_block_delta":
            return ""
        delta = event.get("delta") or {}
        if delta.get("type") != "text_delta":
            return ""
        return delta.get("text") or ""

    def parse_stream_usage(self, event):
        """Output tokens from message_delta, message_start, or a whole reply.

        message_start nests usage under "message"; message_delta and a complete
        non-streamed body carry it at the top level.
        """
        event = event or {}
        nested = event.get("message")
        for usage in (event.get("usage"), nested.get("usage") if isinstance(nested, dict) else None):
            if isinstance(usage, dict):
                value = usage.get("output_tokens")
                if isinstance(value, int) and value >= 0:
                    return value
        return None

    def parse_response(self, data):
        blocks = (data or {}).get("content")
        if not isinstance(blocks, list):
            raise self._malformed(data)
        parts = [block.get("text") or "" for block in blocks
                 if isinstance(block, dict) and block.get("type") == "text"]
        return "".join(parts)

    def parse_models(self, data):
        entries = (data or {}).get("data")
        if not isinstance(entries, list):
            return []
        return [{"id": entry["id"],
                 "label": entry.get("display_name") or entry["id"],
                 "context": entry.get("context_window") or 0,
                 "note": ""}
                for entry in entries if isinstance(entry, dict) and entry.get("id")]


class GeminiProvider(Provider):
    """Google's Generative Language API.

    Three differences at once: the system prompt is "systemInstruction", the
    assistant role is called "model", and message text lives in "parts". The
    key goes in the x-goog-api-key header -- this API also accepts it as a
    ?key= query parameter, which puts a credential into every proxy log and
    must not be used.
    """

    id = "gemini"
    label = "Gemini"
    key_env = "GEMINI_API_KEY"
    key_url = "https://aistudio.google.com/apikey"
    base_url = "https://generativelanguage.googleapis.com/v1beta"
    models_url = "https://generativelanguage.googleapis.com/v1beta/models"
    key_prefix = "AIza"
    key_min_length = 30
    default_model = "gemini-2.5-flash"
    supports_json_mode = True

    ROLE_MAP = {"user": "user", "assistant": "model"}

    # A guess, not a catalogue: Gemini's current ids cannot be verified without
    # a key. Used only when discovery fails, and each entry says so.
    FALLBACK_MODELS = (
        {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro", "context": 1048576,
         "note": "fallback entry, may be out of date"},
        {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "context": 1048576,
         "note": "fallback entry, may be out of date"},
    )

    def headers(self, key, stream=False):
        headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
        if stream:
            headers["Accept"] = "text/event-stream"
        return headers

    def message(self, role, content):
        return {"role": role, "parts": [{"text": content}]}

    @staticmethod
    def _model_path(model):
        """Gemini model ids appear both bare and as "models/<id>"."""
        model = (model or "").strip()
        return model[len("models/"):] if model.startswith("models/") else model

    def chat_payload(self, messages, model=None, stream=False,
                     max_tokens=DEFAULT_MAX_TOKENS, json_mode=None):
        system, converted = self.convert_messages(messages)
        payload = {"contents": converted,
                   "generationConfig": {"maxOutputTokens": max_tokens}}
        if system:
            # Not a turn in "contents": a system role there is rejected, and
            # leaving the prompt out entirely is the silent failure this exists
            # to prevent.
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if self.use_json_mode(json_mode):
            payload["generationConfig"]["responseMimeType"] = "application/json"
        name = self._model_path(model or self.default_model)
        method = "streamGenerateContent?alt=sse" if stream else "generateContent"
        return f"{self.base_url}/models/{name}:{method}", payload

    @staticmethod
    def _candidate_text(data):
        parts = []
        for candidate in (data or {}).get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            for part in (candidate.get("content") or {}).get("parts") or []:
                if isinstance(part, dict) and part.get("text"):
                    parts.append(part["text"])
        return "".join(parts)

    def parse_stream_chunk(self, event):
        return self._candidate_text(event)

    def parse_stream_usage(self, event):
        usage = (event or {}).get("usageMetadata") or {}
        value = usage.get("candidatesTokenCount")
        return value if isinstance(value, int) and value >= 0 else None

    def parse_response(self, data):
        if not isinstance(data, dict) or "candidates" not in data:
            raise self._malformed(data)
        return self._candidate_text(data)

    def parse_models(self, data):
        entries = (data or {}).get("models")
        if not isinstance(entries, list):
            return []
        models = []
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            methods = entry.get("supportedGenerationMethods")
            if isinstance(methods, list) and methods and "generateContent" not in methods:
                continue
            model_id = self._model_path(entry["name"])
            models.append({"id": model_id,
                           "label": entry.get("displayName") or model_id,
                           "context": entry.get("inputTokenLimit") or 0,
                           "note": ""})
        return models


_REGISTRY = {}
for _adapter in (OpenRouterProvider, OpenAIProvider, AnthropicProvider, GeminiProvider):
    _REGISTRY[_adapter.id] = _adapter()


def get_provider(provider_id):
    """The adapter for an id, raising ProviderError for anything unknown."""
    provider = _REGISTRY.get(str(provider_id or "").strip().lower())
    if provider is None:
        raise ProviderError(f"Not a provider TMT supports: {provider_id!r}", kind="not_found")
    return provider


def all_providers():
    """Every adapter, in PROVIDERS order, OpenRouter first as the default."""
    return [_REGISTRY[name] for name in PROVIDERS]


def stream_completion(provider, key, messages, model=None,
                      max_tokens=DEFAULT_MAX_TOKENS, on_usage=None, json_mode=None):
    """Yield generated text fragments from the provider's stream.

    Only generated content is yielded, so the fragments feed
    StreamingActionParser unchanged whichever provider produced them. A usage
    record goes to ``on_usage`` rather than into the text.
    """
    url, payload = provider.chat_payload(messages, model, stream=True,
                                         max_tokens=max_tokens, json_mode=json_mode)
    response = _send(url, provider.headers(key, stream=True), payload, key, stream=True)
    try:
        _raise_for_status(response, key, provider.label)
        for event in iter_sse(response):
            error = provider.parse_stream_error(event)
            if error is not None:
                raise error
            if on_usage is not None:
                tokens = provider.parse_stream_usage(event)
                if tokens is not None:
                    on_usage(tokens)
            text = provider.parse_stream_chunk(event)
            if text:
                yield text
    except ProviderError:
        raise
    except Exception as error:  # dropped connection, decode failure, timeout
        raise ProviderError(redact(f"{type(error).__name__}: {error}", key), kind="network")
    finally:
        _close(response)


def complete(provider, key, messages, model=None,
             max_tokens=DEFAULT_MAX_TOKENS, json_mode=None):
    """One blocking completion. Returns (text, output_tokens_or_None).

    A provider that rejects JSON mode has the constraint dropped and the call
    retried once, which is how TMT has always handled it: the constraint is
    what keeps replies parseable, so it is worth asking for and worth giving up
    rather than failing on.
    """
    for attempt in (0, 1):
        url, payload = provider.chat_payload(messages, model, stream=False,
                                             max_tokens=max_tokens, json_mode=json_mode)
        asked_for_json = "response_format" in payload or "responseMimeType" in payload.get("generationConfig", {})
        response = _send(url, provider.headers(key), payload, key)
        try:
            _raise_for_status(response, key, provider.label)
        except ProviderError as error:
            if attempt == 0 and asked_for_json and is_json_mode_rejection(str(error)):
                disable_json_mode()
                json_mode = False
                continue
            raise
        data = _parse_json(_text_of(response))
        if data is None:
            raise ProviderError(
                f"{provider.label} reply was not JSON: {_body_excerpt(_text_of(response), key)}",
                kind="malformed")
        error = provider.parse_stream_error(data)
        if error is not None:
            if attempt == 0 and asked_for_json and is_json_mode_rejection(str(error)):
                disable_json_mode()
                json_mode = False
                continue
            raise error
        return provider.parse_response(data), provider.parse_stream_usage(data)
    raise ProviderError(f"{provider.label} did not return a reply", kind="unknown")
