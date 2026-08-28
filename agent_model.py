"""OpenRouter model communication and response normalization."""

import json
import re
from agent_config import (
    APP_TITLE, APP_URL, OPENROUTER_API_KEY, OPENROUTER_URL, VERIFY_SSL,
    MODEL, _json_mode_ok, _session, console, requests,
)

def clean_model_json(raw):
    return re.sub(r'"(\w+)"=(?!")', r'"\1":', raw)

def _post_chat(payload):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": APP_URL,
        "X-Title": APP_TITLE,
    }
    with console.status("[bold cyan]Thinking...[/bold cyan]", spinner="dots"):
        response = _session.post(OPENROUTER_URL, headers=headers, json=payload, timeout=120, verify=VERIFY_SSL)
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

def ask_model(messages):
    global _json_mode_ok
    base = {"model": MODEL, "max_tokens": 4096, "messages": messages}
    payload = dict(base)
    if _json_mode_ok:
        payload["response_format"] = {"type": "json_object"}
    data, error = _post_chat(payload)
    if error and _json_mode_ok and any(token in error.lower() for token in ("response_format", "json_object", "structured output")):
        console.print("[yellow]Model rejected JSON mode — retrying without it.[/yellow]")
        _json_mode_ok = False
        data, error = _post_chat(dict(base))
    if error:
        return json.dumps({"action": "done", "message": f"OpenRouter error — {error}"})
    try:
        full = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return json.dumps({"action": "done", "message": f"Unexpected response shape: {str(data)[:300]}"})
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
