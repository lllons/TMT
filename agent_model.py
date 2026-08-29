"""OpenRouter model communication, streaming and response normalization."""

import json
import re
import agent_config
from agent_config import (
    APP_TITLE, APP_URL, OPENROUTER_URL, STREAM_ENABLED,
    VERIFY_SSL, _json_mode_ok, _session, console, requests,
)
import agent_config as _config

JSON_MODE_REJECTIONS = ("response_format", "json_object", "structured output")

def clean_model_json(raw):
    return re.sub(r'"(\w+)"=(?!")', r'"\1":', raw)

def _headers(stream=False):
    headers = {
        # Read at call time: first-launch setup can supply the key after import.
        "Authorization": f"Bearer {agent_config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": APP_URL,
        "X-Title": APP_TITLE,
    }
    if stream:
        headers["Accept"] = "text/event-stream"
    return headers

def _model_for_request():
    """The model id for whichever provider is selected right now.

    Read here rather than taken from agent_config.MODEL, because a model id is
    only meaningful to the provider that issued it and the provider can change
    between requests.
    """
    try:
        import agent_models
        return agent_models.current_model()
    except Exception:
        return _config.MODEL


def selected_provider():
    """The provider to call and the credential for it, resolved at call time.

    Returns (provider, key, error). Settings can change either of the first two
    mid-session, so neither is bound at import. An empty credential is reported
    as an error here rather than sent, because a request without one comes back
    as an authentication failure that reads like a broken key rather than a
    missing one.
    """
    try:
        import agent_credentials
        import agent_providers
    except Exception as error:
        return None, "", f"Provider support is unavailable: {error}"
    try:
        provider = agent_providers.get_provider(agent_credentials.selected_provider())
    except Exception:
        provider = agent_providers.get_provider(agent_providers.DEFAULT_PROVIDER)
    key = agent_credentials.credential(provider.id)
    if not key:
        return None, "", (
            f"No API key is set for {provider.label}. Open Settings, choose "
            "API Key, and add one."
        )
    return provider, key, ""


def _post_chat(payload, spinner=True):
    import agent_providers
    provider, key, problem = selected_provider()
    if problem:
        return None, problem

    def call():
        return agent_providers.complete(
            provider, key, payload.get("messages") or [],
            model=payload.get("model"),
            max_tokens=payload.get("max_tokens", 4096),
            json_mode="response_format" in payload,
        )

    try:
        if spinner:
            with console.status("[bold cyan]Thinking...[/bold cyan]", spinner="dots"):
                text, tokens = call()
        else:
            text, tokens = call()
    except agent_providers.ProviderError as error:
        return None, str(error)
    except Exception as error:
        return None, f"{type(error).__name__}: {error}"
    # Re-shaped into the single internal form ask_model reads, so no provider's
    # own response layout escapes this module.
    data = {"choices": [{"message": {"content": text}}]}
    if tokens is not None:
        data["usage"] = {"completion_tokens": tokens}
    return data, None

class StreamError(RuntimeError):
    """The model stream failed, was rejected, or closed unexpectedly."""

def _completion_tokens(data):
    """The provider's own count of the tokens it generated, if it reports one.

    Exact, and covers the whole reply rather than the part shown to the user,
    so it supersedes any estimate once it arrives.
    """
    usage = (data or {}).get("usage") or {}
    for key in ("completion_tokens", "output_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and value >= 0:
            return value
    return None

def stream_chat(payload, on_usage=None):
    """Yield content fragments from the selected provider's stream.

    The payload is TMT's own shape rather than any provider's; the adapter
    converts it and normalises the stream, so every provider arrives here as
    the same sequence of text fragments the action parser already reads.
    """
    import agent_providers
    provider, key, problem = selected_provider()
    if problem:
        raise StreamError(problem)
    try:
        for fragment in agent_providers.stream_completion(
            provider, key, payload.get("messages") or [],
            model=payload.get("model"),
            max_tokens=payload.get("max_tokens", 4096),
            on_usage=on_usage,
            json_mode="response_format" in payload,
        ):
            yield fragment
    except agent_providers.ProviderError as error:
        raise StreamError(str(error))
    return


def _legacy_openrouter_stream(payload, on_usage=None):
    """The direct OpenRouter reader, kept for reference and for tests.

    Superseded by the provider adapters, which reproduce this exchange
    byte for byte for OpenRouter and add the other three.
    """
    response = _session.post(
        OPENROUTER_URL, headers=_headers(stream=True), json=payload,
        timeout=120, verify=VERIFY_SSL, stream=True,
    )
    # Server-sent events are UTF-8 by definition, but requests falls back to
    # ISO-8859-1 for any text/* response that does not name a charset, and
    # text/event-stream usually does not. Left to guess, it decodes every
    # multi-byte character into mojibake: an em dash arrives as three latin-1
    # characters, is written back out as UTF-8, and lands in the user's files
    # as a sequence nothing can read. Nothing downstream can undo that, so the
    # encoding is stated here rather than inferred.
    if getattr(response, "encoding", None) is not None:
        response.encoding = "utf-8"
    try:
        try:
            response.raise_for_status()
        except requests.HTTPError:
            raise StreamError(f"HTTP {response.status_code}: {response.text[:300]}")
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            line = line.strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("error"):
                error = event["error"]
                raise StreamError(error.get("message", str(error)) if isinstance(error, dict) else str(error))
            tokens = _completion_tokens(event)
            if tokens is not None and on_usage:
                on_usage(tokens)
            for choice in event.get("choices") or []:
                content = (choice.get("delta") or {}).get("content")
                if content:
                    yield content
    finally:
        close = getattr(response, "close", None)
        if close:
            close()

_STRING_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "/": "/", '"': '"', "\\": "\\"}

class StreamingActionParser:
    """Incrementally reads the model's action JSON out of arbitrary fragments.

    Network chunks split JSON anywhere — mid-key, mid-escape, mid-object — so
    nothing here assumes a fragment is valid JSON on its own. The parser keeps
    the exact received text, tracks structure as characters arrive, and reports
    the user-facing parts (``message`` values) the moment they are safely
    identifiable. Internal control data (paths, file content, action plumbing)
    is never reported as text.

    ``feed`` returns a list of events:
        ("text", str)       decoded user-facing characters
        ("action", str)     an action name finished parsing
        ("progress", str)   a completed top-level "progress" value
        ("next_step", str)  a completed top-level "next_step" value
        ("object", str)     the top-level JSON object is complete

    ``progress`` and ``next_step`` are reported the moment their closing quote
    arrives, so a progress line reaches the screen while the action that
    carries it is still being generated. They are not user-facing *text*: they
    never join the ("text", ...) stream, which still carries ``message`` values
    and nothing else.

    Only a TOP-LEVEL key of either name counts. The same name nested inside a
    ``files`` entry -- or, far more often, inside a ``content`` string that
    happens to hold JSON -- is arbitrary user data, and streaming it would put
    a file's own words on screen as if the agent had said them.

    The ``events`` array is deliberately not streamed. Its entries are only
    meaningful once the array closes, and the caller reads it from the
    completed object instead. An entry's own ``message`` is a display record
    rather than something the agent said, so it is kept out of the text stream
    as well.
    """

    USER_TEXT_KEYS = ("message",)
    # Reported as their own event, named after the key itself.
    LIVE_VALUE_KEYS = ("progress", "next_step")

    def __init__(self):
        self.raw = ""
        self.json_text = ""
        self.complete_json = None
        self._started = False
        self._stack = []
        self._keys = []
        self._owners = []
        self._awaiting_value = False
        self._in_string = False
        self._is_key = False
        self._emitting = False
        self._escape = False
        self._unicode = None
        self._high_surrogate = None
        self._token = []
        self._events = []
        self._text = []

    def feed(self, chunk):
        if not chunk:
            return []
        self.raw += chunk
        self._events, self._text = [], []
        for char in chunk:
            self._consume(char)
        self._flush()
        return self._events

    def _flush(self):
        """Emit the user-facing text decoded so far, keeping event order."""
        if self._text:
            self._events.append(("text", "".join(self._text)))
            self._text = []

    def _emit(self, event):
        self._flush()
        self._events.append(event)

    def result(self):
        """The exact JSON text of the reply, or None if it never completed."""
        return clean_model_json(self.complete_json) if self.complete_json else None

    def _current_key(self):
        return self._keys[-1] if self._keys else None

    def _at_top_level(self):
        """True while the value being read belongs to the outermost object.

        Structure characters are ignored inside strings, so the depth cannot
        move between a value's opening and closing quote; reading it at either
        end gives the same answer.
        """
        return len(self._stack) == 1 and len(self._keys) == 1

    def _consume(self, char):
        if self.complete_json is not None:
            return
        if not self._started:
            if char != "{":
                return
            self._started = True
            self._stack.append("{")
            self._keys.append(None)
            self._owners.append(None)
            self.json_text = "{"
            return
        self.json_text += char
        if self._in_string:
            self._consume_string(char)
            return
        if char == '"':
            self._in_string = True
            self._token = []
            self._is_key = bool(self._stack) and self._stack[-1] == "{" and not self._awaiting_value
            self._emitting = (
                not self._is_key
                and self._current_key() in self.USER_TEXT_KEYS
                and "events" not in self._owners
            )
            return
        if char == ":":
            self._awaiting_value = True
        elif char == ",":
            self._awaiting_value = False
            if self._stack and self._stack[-1] == "{" and self._keys:
                self._keys[-1] = None
        elif char in "{[":
            # Recorded before the push, so it names the key this container is
            # the value of. An "events" entry carries a "message" of its own,
            # and without this the parser would relay a display record to the
            # user as if the agent had said it.
            self._owners.append(self._current_key())
            self._stack.append(char)
            if char == "{":
                self._keys.append(None)
            self._awaiting_value = False
        elif char in "}]":
            if self._stack:
                if self._stack.pop() == "{" and self._keys:
                    self._keys.pop()
                if self._owners:
                    self._owners.pop()
            self._awaiting_value = False
            if not self._stack:
                self.complete_json = self.json_text
                self._emit(("object", self.complete_json))

    def _consume_string(self, char):
        if self._unicode is not None:
            self._unicode += char
            if len(self._unicode) == 4:
                digits, self._unicode = self._unicode, None
                self._push(self._decode_unicode(digits))
            return
        if self._escape:
            self._escape = False
            if char == "u":
                self._unicode = ""
                return
            self._push(_STRING_ESCAPES.get(char, char))
            return
        if char == "\\":
            self._escape = True
            return
        if char == '"':
            self._in_string = False
            self._high_surrogate = None
            token = "".join(self._token)
            if self._is_key:
                if self._stack and self._stack[-1] == "{" and self._keys:
                    self._keys[-1] = token
            elif self._current_key() == "action" and token:
                self._emit(("action", token))
            elif token and self._current_key() in self.LIVE_VALUE_KEYS and self._at_top_level():
                self._emit((self._current_key(), token))
            self._emitting = False
            self._token = []
            return
        self._push(char)

    def _decode_unicode(self, digits):
        try:
            code = int(digits, 16)
        except ValueError:
            return ""
        if 0xD800 <= code <= 0xDBFF:
            self._high_surrogate = code
            return ""
        if 0xDC00 <= code <= 0xDFFF and self._high_surrogate is not None:
            high, self._high_surrogate = self._high_surrogate, None
            return chr(0x10000 + ((high - 0xD800) << 10) + (code - 0xDC00))
        self._high_surrogate = None
        return chr(code)

    def _push(self, value):
        if not value:
            return
        self._token.append(value)
        if self._emitting:
            self._text.append(value)

# A reply that arrived as prose is shown at this length and no further. Long
# enough for the summary such a reply almost always is, short enough that a
# model which started writing an essay does not fill the screen with it.
PROSE_REPLY_LIMIT = 2000


def _prose_reply(full):
    """A plain-language reply, passed through as the answer.

    The model was asked for JSON and wrote a sentence instead. The work it
    describes has usually already happened -- the actions ran on earlier turns
    of the loop -- so the sentence is the summary of that work, and replacing
    it with "no JSON object found in response" told the user nothing at all
    about a task that had in fact been done.

    Nothing here is invented: the text shown is the model's own, trimmed and
    marked when it had to be trimmed. It is still a `done`, so the turn ends
    exactly where it ended before.
    """
    text = " ".join(str(full).split())
    if len(text) > PROSE_REPLY_LIMIT:
        text = text[:PROSE_REPLY_LIMIT].rstrip() + " […]"
    return json.dumps({"action": "done", "message": text})


# Marks an action object this module made up rather than one the model sent.
#
# Every failure here has to come back as a valid action, because the agent loop
# has no other shape to receive one in -- so a stream that died and a reply
# that could not be read both arrive as a `done` carrying an explanation. That
# is right for the screen and wrong for the record: the sentence in it is a
# machine's report of a failure, not the model's account of the work, and
# writing it into the session as the assistant's answer told the NEXT turn
# that the model had said "no JSON object found in response". This key is how
# the loop tells the two apart. It is stripped before anything is sent.
SYNTHETIC_KEY = "tmt_synthetic"


def is_synthetic(obj):
    """Whether an action object was made up here rather than sent by a model."""
    return bool(isinstance(obj, dict) and obj.get(SYNTHETIC_KEY))


def _made_up(message):
    """A `done` carrying an explanation, marked as not the model's words."""
    return json.dumps({"action": "done", "message": message, SYNTHETIC_KEY: True})


def _extract_json(full):
    """Pull the first balanced JSON object out of a complete model reply."""
    if not full:
        return _made_up("empty response from model")
    start = full.find("{")
    if start == -1:
        # Prose, not a failure: the model wrote a sentence instead of JSON,
        # and that sentence is its own answer rather than a report about it.
        return _prose_reply(full) if full.strip() else \
            _made_up("no JSON object found in response")
    depth = 0
    for index, char in enumerate(full[start:]):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return clean_model_json(full[start:start + index + 1])
    return _made_up("invalid JSON structure")

def _error_reply(message):
    # The provider that actually failed, asked at the moment it failed. It
    # used to say "OpenRouter" whichever of the four had been called, which is
    # a false statement about where an error came from.
    provider = selected_provider()[0]
    label = getattr(provider, "label", "") or "The provider"
    return _made_up(f"{label} error — {message}")

def _ask_model_streaming(messages, on_event):
    """Consume the reply as a stream, reporting events as they arrive.

    Returns (reply_text, fall_back). ``fall_back`` is True only when the stream
    failed before any content was generated, so a non-streaming retry can never
    duplicate output the user has already seen.
    """
    global _json_mode_ok
    for attempt in (0, 1):
        payload = {"model": _model_for_request(), "max_tokens": 4096,
                   "messages": messages, "stream": True}
        if _json_mode_ok:
            payload["response_format"] = {"type": "json_object"}
        parser = StreamingActionParser()
        seen_content = False
        try:
            usage_sink = lambda tokens: on_event(("usage", tokens))
            for content in stream_chat(payload, on_usage=usage_sink):
                if not content:
                    continue
                if not seen_content:
                    seen_content = True
                    on_event(("first_content", ""))
                # Every character generated, action plumbing and file contents
                # included -- not only the part relayed to the user.
                on_event(("output", len(content)))
                for event in parser.feed(content):
                    on_event(event)
        except StreamError as error:
            message = str(error)
            if seen_content:
                on_event(("error", message))
                return _error_reply(message), False
            if attempt == 0 and _json_mode_ok and any(t in message.lower() for t in JSON_MODE_REJECTIONS):
                _json_mode_ok = False
                continue
            return None, True
        except Exception as error:  # dropped connection, decode failure, timeout
            message = f"{type(error).__name__}: {error}"
            if seen_content:
                on_event(("error", message))
                return _error_reply(message), False
            return None, True
        raw = parser.result()
        if raw:
            return raw, False
        if not parser.raw.strip():
            return '{"action":"done","message":"empty response from model"}', False
        return _extract_json(parser.raw), False
    return None, True

def ask_model(messages, on_event=None):
    """Return the model's JSON reply as text.

    With ``on_event`` supplied and streaming available, the reply is consumed
    from the provider's stream and events are reported as they arrive:
    ("first_content", ""), ("text", str), ("action", str), ("progress", str),
    ("next_step", str), ("object", str), ("output", int), ("usage", int) and
    ("error", str). Without it — or when the provider or transport cannot
    stream — a single blocking request is used instead.
    """
    global _json_mode_ok
    streaming = on_event is not None and STREAM_ENABLED
    if streaming:
        raw, fall_back = _ask_model_streaming(messages, on_event)
        if raw is not None:
            return raw
        if not fall_back:
            return _error_reply("stream ended without a reply")
    base = {"model": _model_for_request(), "max_tokens": 4096, "messages": messages}
    payload = dict(base)
    if _json_mode_ok:
        payload["response_format"] = {"type": "json_object"}
    data, error = _post_chat(payload, spinner=not streaming)
    if error and _json_mode_ok and any(token in error.lower() for token in JSON_MODE_REJECTIONS):
        console.print("[yellow]Model rejected JSON mode — retrying without it.[/yellow]")
        _json_mode_ok = False
        data, error = _post_chat(dict(base), spinner=not streaming)
    if error:
        return _error_reply(error)
    try:
        full = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return _made_up(f"Unexpected response shape: {str(data)[:300]}")
    if on_event:
        # A blocking reply is generated output too, and it always carries the
        # provider's own usage record, so it needs no estimating.
        on_event(("output", len(full)))
        tokens = _completion_tokens(data)
        if tokens is not None:
            on_event(("usage", tokens))
    return _extract_json(full)
