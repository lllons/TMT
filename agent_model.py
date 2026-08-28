"""OpenRouter model communication, streaming and response normalization."""

import json
import re
import agent_config
from agent_config import (
    APP_TITLE, APP_URL, OPENROUTER_URL, STREAM_ENABLED,
    VERIFY_SSL, MODEL, _json_mode_ok, _session, console, requests,
)

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

def _post_chat(payload, spinner=True):
    if spinner:
        with console.status("[bold cyan]Thinking...[/bold cyan]", spinner="dots"):
            response = _session.post(OPENROUTER_URL, headers=_headers(), json=payload, timeout=120, verify=VERIFY_SSL)
    else:
        response = _session.post(OPENROUTER_URL, headers=_headers(), json=payload, timeout=120, verify=VERIFY_SSL)
    try:
        response.raise_for_status()
    except requests.HTTPError:
        return None, f"HTTP {response.status_code}: {response.text[:300]}"
    try:
        data = response.json()
    except ValueError:
        return None, f"reply was not JSON: {response.text[:300]}"
    if "error" in data:
        error = data["error"]
        return None, error.get("message", str(error)) if isinstance(error, dict) else str(error)
    return data, None

class StreamError(RuntimeError):
    """The model stream failed, was rejected, or closed unexpectedly."""

def stream_chat(payload):
    """Yield content fragments from the provider's server-sent event stream.

    Only generated content is yielded: SSE comments, keep-alives, role deltas
    and usage metadata never reach the caller.
    """
    response = _session.post(
        OPENROUTER_URL, headers=_headers(stream=True), json=payload,
        timeout=120, verify=VERIFY_SSL, stream=True,
    )
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
        ("text", str)     decoded user-facing characters
        ("action", str)   an action name finished parsing
        ("object", str)   the top-level JSON object is complete
    """

    USER_TEXT_KEYS = ("message",)

    def __init__(self):
        self.raw = ""
        self.json_text = ""
        self.complete_json = None
        self._started = False
        self._stack = []
        self._keys = []
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

    def _consume(self, char):
        if self.complete_json is not None:
            return
        if not self._started:
            if char != "{":
                return
            self._started = True
            self._stack.append("{")
            self._keys.append(None)
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
            self._emitting = not self._is_key and self._current_key() in self.USER_TEXT_KEYS
            return
        if char == ":":
            self._awaiting_value = True
        elif char == ",":
            self._awaiting_value = False
            if self._stack and self._stack[-1] == "{" and self._keys:
                self._keys[-1] = None
        elif char in "{[":
            self._stack.append(char)
            if char == "{":
                self._keys.append(None)
            self._awaiting_value = False
        elif char in "}]":
            if self._stack:
                if self._stack.pop() == "{" and self._keys:
                    self._keys.pop()
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

def _extract_json(full):
    """Pull the first balanced JSON object out of a complete model reply."""
    if not full:
        return '{"action":"done","message":"empty response from model"}'
    start = full.find("{")
    if start == -1:
        return '{"action":"done","message":"no JSON object found in response"}'
    depth = 0
    for index, char in enumerate(full[start:]):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return clean_model_json(full[start:start + index + 1])
    return '{"action":"done","message":"invalid JSON structure"}'

def _error_reply(message):
    return json.dumps({"action": "done", "message": f"OpenRouter error — {message}"})

def _ask_model_streaming(messages, on_event):
    """Consume the reply as a stream, reporting events as they arrive.

    Returns (reply_text, fall_back). ``fall_back`` is True only when the stream
    failed before any content was generated, so a non-streaming retry can never
    duplicate output the user has already seen.
    """
    global _json_mode_ok
    for attempt in (0, 1):
        payload = {"model": MODEL, "max_tokens": 4096, "messages": messages, "stream": True}
        if _json_mode_ok:
            payload["response_format"] = {"type": "json_object"}
        parser = StreamingActionParser()
        seen_content = False
        try:
            for content in stream_chat(payload):
                if not content:
                    continue
                if not seen_content:
                    seen_content = True
                    on_event(("first_content", ""))
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
    ("first_content", ""), ("text", str), ("action", str), ("object", str) and
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
    base = {"model": MODEL, "max_tokens": 4096, "messages": messages}
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
        return json.dumps({"action": "done", "message": f"Unexpected response shape: {str(data)[:300]}"})
    return _extract_json(full)
