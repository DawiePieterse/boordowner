"""The AI model behind "Ask about this estimate": which provider, which
model, and one streamed chat call. Ported from the Weather Compare app's
site/js/ai.js, cloud half only - with the key held here on the server (see
config.AI_*) rather than typed into each browser.

Three providers, all free-tier friendly:

  gemini - Google's own streaming API (x-goog-api-key header).
  groq   - OpenAI-compatible chat/completions.
  custom - any other OpenAI-compatible chat/completions URL (Cloudflare
           Workers AI, a local Ollama/LM Studio, OpenRouter...).

Free tiers retire model names every few months. When the model in use is
refused as gone, the refusal's own "use X instead" is followed, else the
provider's model list is asked and a replacement picked by preference - and
remembered for the life of the process, so only the first question after a
retirement pays for it. A model the owner set explicitly (OWNER_AI_MODEL) is
never swapped behind their back.

Like every other outbound call here, nothing in this module touches a
database: routers/ai.py builds the figures, releases Boord's database, and
only then calls stream().
"""
import json as _json
import re as _re
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator, Optional

import config

PROVIDERS = {
    "gemini": {
        "name": "Google Gemini", "model": "gemini-3.8-flash",
        "models": "https://generativelanguage.googleapis.com/v1beta/models",
        # A general "flash" model, newest first; not the image/audio/live/embedding variants.
        "prefer": [r"^gemini-[\d.]+-flash$", r"^gemini-[\d.]+-flash-lite$", r"^gemini-.*flash"],
        "avoid": r"image|tts|audio|live|embedding|thinking|exp|preview",
    },
    "groq": {
        "name": "Groq", "model": "llama-3.3-70b-versatile",
        "endpoint": "https://api.groq.com/openai/v1/chat/completions",
        "models": "https://api.groq.com/openai/v1/models",
        "prefer": [r"llama.*70b", r"llama.*(?:8|17)b", r"llama", r"gpt-oss|qwen|deepseek|mixtral|gemma"],
        "avoid": r"whisper|tts|guard|embed|vision|orpheus|compound|saba|allam",
    },
    "custom": {
        "name": "Custom endpoint", "model": "@cf/meta/llama-3.1-8b-instruct",
        "prefer": [r"llama.*(?:70|8)b.*instruct", r"instruct", r"."],
        "avoid": r"embed|whisper|guard",
    },
}

TEMPERATURE = 0.2
MAX_OUTPUT_TOKENS = 1200   # a per-block review runs longer than a weather comparison
TIMEOUT_S = 60             # per socket read: a slow first token, not the whole answer

_resolved: dict = {}       # provider -> replacement model, see the module docstring
_resolved_lock = threading.Lock()


class AIError(Exception):
    """A provider refused or failed. The message is written for the owner."""


def settings() -> Optional[dict]:
    """The configured provider, or None when the feature is off: no key
    (and, for custom, no endpoint). Read from config on every call so a
    test can switch it."""
    provider = config.AI_PROVIDER if config.AI_PROVIDER in PROVIDERS else "custom"
    s = {"provider": provider, "api_key": config.AI_API_KEY,
         "endpoint": config.AI_ENDPOINT, "model": config.AI_MODEL}
    if provider == "custom":
        return s if s["endpoint"] else None
    return s if s["api_key"] else None


def provider_name(s: dict) -> str:
    return PROVIDERS[s["provider"]]["name"]


def model_for(s: dict) -> str:
    """The owner's choice, else a remembered replacement, else the default."""
    return s["model"] or _resolved.get(s["provider"]) or PROVIDERS[s["provider"]]["model"]


def remember_model(provider: str, model: Optional[str]) -> None:
    with _resolved_lock:
        if model:
            _resolved[provider] = model
        else:
            _resolved.pop(provider, None)


def pick_model(ids: list, prefer: list, avoid: str) -> Optional[str]:
    """The best model on a provider's list by its preference patterns, most
    specific first, newest name first within a pattern."""
    usable = sorted((i for i in ids if not _re.search(avoid, i)), reverse=True)
    for pattern in prefer:
        hit = next((i for i in usable if _re.search(pattern, i)), None)
        if hit:
            return hit
    return None


def suggested_model(message: str) -> Optional[str]:
    """"... is no longer available ... use models/gemini-3.8-flash" -> the name."""
    m = _re.search(r"use (?:models/)?([\w.-]+)", message or "", _re.I)
    return m.group(1) if m else None


def model_gone(message: str) -> bool:
    return bool(_re.search(r"no longer available|not found|does not exist|deprecated|"
                           r"decommissioned|not supported", message or "", _re.I))


def _auth_headers(s: dict) -> dict:
    if s["provider"] == "gemini":
        return {"x-goog-api-key": s["api_key"]}
    return {"Authorization": f"Bearer {s['api_key']}"} if s["api_key"] else {}


def build_request(messages: list, s: dict) -> tuple:
    """(url, headers, body, delta) for one streamed chat call. `delta` pulls
    the new text out of one parsed stream event."""
    headers = {"Content-Type": "application/json", **_auth_headers(s)}
    model = model_for(s)
    if s["provider"] == "gemini":
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "model" if m["role"] == "assistant" else "user",
                          "parts": [{"text": m["content"]}]}
                         for m in messages if m["role"] != "system"],
            "generationConfig": {"temperature": TEMPERATURE, "maxOutputTokens": MAX_OUTPUT_TOKENS},
        }
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{urllib.parse.quote(model, safe='')}:streamGenerateContent?alt=sse")

        def delta(j):
            parts = ((j.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            return "".join(p.get("text", "") for p in parts)
        return url, headers, body, delta

    body = {"model": model, "messages": messages, "stream": True,
            "temperature": TEMPERATURE, "max_tokens": MAX_OUTPUT_TOKENS}
    url = s["endpoint"] if s["provider"] == "custom" else PROVIDERS[s["provider"]]["endpoint"]

    def delta(j):
        choice = (j.get("choices") or [{}])[0]
        return (choice.get("delta") or {}).get("content") or ""
    return url, headers, body, delta


def _error_detail(raw: bytes) -> str:
    try:
        j = _json.loads(raw)
    except ValueError:
        return ""
    if isinstance(j, list) and j:   # Gemini sometimes wraps the error in a list
        j = j[0]
    if not isinstance(j, dict):
        return ""
    err = j.get("error")
    if isinstance(err, dict):
        return err.get("message") or ""
    if isinstance(err, str):
        return err
    errors = j.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return errors[0].get("message") or ""
    return j.get("message") or ""


def _failure(status: int, raw: bytes, name: str) -> AIError:
    detail = _error_detail(raw)
    if status == 429:
        return AIError(f"{name}: free-tier limit reached, try again shortly")
    if status in (401, 403):
        return AIError(f"{name}: the API key was rejected{f' ({detail})' if detail else ''}")
    return AIError(f"{name}: {detail or f'HTTP {status}'}")


def _post_stream(url: str, headers: dict, body: dict) -> Iterator[bytes]:
    """POSTs and yields the response's raw lines. A refusal raises
    urllib.error.HTTPError. Split out so tests can stand in for the network."""
    req = urllib.request.Request(url, data=_json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        for line in resp:
            yield line


def _get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return _json.loads(resp.read())


def list_models(s: dict) -> list:
    """The provider's model ids, or [] when it won't say."""
    p = PROVIDERS[s["provider"]]
    url = p.get("models") or _re.sub(r"/chat/completions/?$", "/models", s["endpoint"] or "")
    if not url or url == s["endpoint"]:
        return []
    try:
        j = _get_json(url, _auth_headers(s))
    except (OSError, ValueError):
        return []
    if s["provider"] == "gemini":
        return [m["name"].removeprefix("models/") for m in j.get("models") or []
                if "generateContent" in (m.get("supportedGenerationMethods") or []) and m.get("name")]
    return [m["id"] for m in j.get("data") or [] if m.get("id")]


def _sse(lines: Iterator[bytes]) -> Iterator[dict]:
    """Server-sent events -> each data payload, parsed."""
    for raw in lines:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            yield _json.loads(data)
        except ValueError:
            continue   # keep-alive or a partial line


def stream(messages: list, s: Optional[dict] = None, _retried: bool = False) -> Iterator[str]:
    """Asks the configured model and yields the answer's text as it arrives.
    Raises AIError with an owner-readable message on any failure."""
    s = s or settings()
    if not s:
        raise AIError("No AI provider is set up on the farm server")
    name = provider_name(s)
    url, headers, body, delta = build_request(messages, s)
    try:
        lines = _post_stream(url, headers, body)
        first = next(lines, None)   # a refusal surfaces here, before any text
    except urllib.error.HTTPError as e:
        err = _failure(e.code, e.read() if e.fp else b"", name)
        if not _retried and not s["model"] and model_gone(str(err)):
            remember_model(s["provider"], None)
            nxt = suggested_model(str(err)) or pick_model(
                list_models(s), PROVIDERS[s["provider"]]["prefer"], PROVIDERS[s["provider"]]["avoid"])
            if nxt and nxt != model_for(s):
                print(f"[ai] {name}: switching model to {nxt}", flush=True)
                remember_model(s["provider"], nxt)
                yield from stream(messages, s, _retried=True)
                return
        raise err from None
    except (OSError, ValueError) as e:
        raise AIError(f"{name} could not be reached ({e})") from None

    def all_lines():
        if first is not None:
            yield first
        yield from lines
    try:
        for event in _sse(all_lines()):
            text = delta(event)
            if text:
                yield text
    except (OSError, ValueError) as e:
        raise AIError(f"{name}: the answer was cut off ({e})") from None
