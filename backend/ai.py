"""The AI model behind Ask, Check, Compare and the daily brief: which
provider, which model, and the calls themselves - one streamed answer,
one JSON answer, one plain answer. Ported from the Weather Compare app's
site/js/ai.js, cloud half only - with the key held here on the server (see
config.AI_*) rather than typed into each browser.

Four providers:

  anthropic - Claude through the official SDK. The one that gets the full
              feature set: answers constrained to a JSON schema (the Check
              findings, verified block by block), prompt caching (follow-up
              questions re-read the figures at a tenth of the price), and
              tool use (Ask can look a block's history or a season's
              weather up for itself, and consult Boord Notes). Paid, a few
              cents a question, which is why the daily cap exists.
  gemini    - Google's own streaming API (x-goog-api-key header). Free tier.
  groq      - OpenAI-compatible chat/completions. Free tier.
  custom    - any other OpenAI-compatible chat/completions URL (Cloudflare
              Workers AI, a local Ollama/LM Studio, OpenRouter...).

The three free-tier providers get the same features where their APIs allow
(JSON mode for Check; no tools, no caching), and the same guard rails.

Free tiers retire model names every few months. When the model in use is
refused as gone, the refusal's own "use X instead" is followed, else the
provider's model list is asked and a replacement picked by preference - and
remembered for the life of the process, so only the first question after a
retirement pays for it. A model the owner set explicitly (OWNER_AI_MODEL) is
never swapped behind their back. Anthropic's model ids are stable, so that
machinery is not wired to it.

An answer cut off at the length limit, or one the provider declined, is
reported in words after whatever text did arrive - never passed off as a
whole answer (every provider says why it stopped; this module reads it).

Like every other outbound call here, nothing in this module touches a
database: routers/ai.py builds the figures, releases Boord's database, and
only then calls in. The one exception is a tool the model asks to run
(see stream_events): the router's run_tool opens and closes its own
sessions inside the call, so no session is ever held across the provider.
"""
import json as _json
import os
import re as _re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Callable, Iterator, Optional

import config

# Each entry: the display name and default model; `prefer`/`avoid` (free
# tiers, whose model names retire - see _switch_model; an entry without them
# is never swapped); `sdk` for the provider called through its own SDK
# (structured output, caching, tools); `key_env`/`key_file` for where a key
# may come from besides OWNER_AI_API_KEY.
PROVIDERS = {
    "anthropic": {
        "name": "Anthropic Claude", "model": "claude-sonnet-5-5", "sdk": True,
        "key_env": ["ANTHROPIC_API_KEY"], "key_file": True,
    },
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
MAX_OUTPUT_TOKENS = 2500   # a per-block review runs long; the model is told when it is cut short
TIMEOUT_S = 60             # per socket read: a slow first token, not the whole answer

# Anthropic: the SDK's own timeout and retries. No retries on a streamed
# answer - the browser gives up on a slow first line (ai-panel.js
# FIRST_BYTE_MS) and a retry after that is money spent on nobody.
ANTHROPIC_TIMEOUT_S = 85.0
ANTHROPIC_MAX_TOKENS = 4000
MAX_TOOL_ROUNDS = 6        # lookups the model may chain in one answer

_resolved: dict = {}       # provider -> replacement model, see the module docstring
_resolved_lock = threading.Lock()


class AIError(Exception):
    """A provider refused or failed. The message is written for the owner."""


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
_key_file_cache = {"path": None, "mtime": None, "key": ""}


def _read_key_file(path: str) -> str:
    """The key file's contents, re-read only when the file changes -
    settings() runs on every AI request and must not cost a disk read."""
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return ""
    if _key_file_cache["path"] != path or _key_file_cache["mtime"] != mtime:
        with open(path, encoding="utf-8") as f:
            _key_file_cache.update(path=path, mtime=mtime, key=f.read().strip())
    return _key_file_cache["key"]


def _api_key(provider: str) -> str:
    """OWNER_AI_API_KEY, else the provider's own environment variable, else
    its key file - so the key Boord Notes already has on this server can
    serve this app too."""
    p = PROVIDERS[provider]
    key = config.AI_API_KEY or next((os.environ.get(v, "").strip() for v in p.get("key_env", [])
                                     if os.environ.get(v, "").strip()), "")
    if not key and p.get("key_file") and config.AI_KEY_FILE:
        key = _read_key_file(config.AI_KEY_FILE)
    return key


def settings() -> Optional[dict]:
    """The configured provider, or None when the feature is off: no key
    (and, for custom, no endpoint). Read from config on every call so a
    test can switch it."""
    provider = config.AI_PROVIDER if config.AI_PROVIDER in PROVIDERS else "custom"
    s = {"provider": provider, "api_key": _api_key(provider),
         "endpoint": config.AI_ENDPOINT, "model": config.AI_MODEL}
    if provider == "custom":
        return s if s["endpoint"] else None
    return s if s["api_key"] else None


def provider_name(s: dict) -> str:
    return PROVIDERS[s["provider"]]["name"]


def model_for(s: dict) -> str:
    """The owner's choice, else a remembered replacement, else the default."""
    return s["model"] or _resolved.get(s["provider"]) or PROVIDERS[s["provider"]]["model"]


def has_tools(s: Optional[dict]) -> bool:
    """Whether Ask can look things up for itself (tool use): the SDK providers."""
    return bool(s) and bool(PROVIDERS[s["provider"]].get("sdk"))


def _require(s: Optional[dict]) -> dict:
    s = s or settings()
    if not s:
        raise AIError("No AI provider is set up on the farm server")
    return s


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


# --------------------------------------------------------------------------- #
# The daily cap
# --------------------------------------------------------------------------- #
# The tailnet is the only access control (no sign-in), so a stray device or
# a runaway script could otherwise spend without limit - on the paid
# provider in money, on the free ones in the day's quota. In memory on
# purpose: a restart resets it, a fine failure mode for a cost guard. Same
# design as Boord Notes.
_usage_lock = threading.Lock()
_usage = {"day": None, "count": 0}


def spend_call() -> None:
    today = date.today()
    with _usage_lock:
        if _usage["day"] != today:
            _usage["day"], _usage["count"] = today, 0
        if _usage["count"] >= config.AI_DAILY_LIMIT:
            raise AIError("The daily limit for AI help has been reached. It resets tomorrow.")
        _usage["count"] += 1


def calls_today() -> int:
    with _usage_lock:
        return _usage["count"] if _usage["day"] == date.today() else 0


# --------------------------------------------------------------------------- #
# Gemini / OpenAI-compatible plumbing
# --------------------------------------------------------------------------- #
def _auth_headers(s: dict) -> dict:
    if s["provider"] == "gemini":
        return {"x-goog-api-key": s["api_key"]}
    return {"Authorization": f"Bearer {s['api_key']}"} if s["api_key"] else {}


def build_request(messages: list, s: dict, json_mode: bool = False) -> tuple:
    """(url, headers, body, delta) for one chat call - streamed, or with
    `json_mode` a single JSON reply (the provider's JSON mode where it has
    one; the schema itself travels in the prompt). `delta` pulls the new
    text out of one parsed stream event, or the whole text out of a
    non-streamed reply."""
    headers = {"Content-Type": "application/json", **_auth_headers(s)}
    model = model_for(s)
    if s["provider"] == "gemini":
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        gen = {"temperature": TEMPERATURE, "maxOutputTokens": MAX_OUTPUT_TOKENS}
        if json_mode:
            gen["responseMimeType"] = "application/json"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "model" if m["role"] == "assistant" else "user",
                          "parts": [{"text": m["content"]}]}
                         for m in messages if m["role"] != "system"],
            "generationConfig": gen,
        }
        method = "generateContent" if json_mode else "streamGenerateContent?alt=sse"
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{urllib.parse.quote(model, safe='')}:{method}")

        def delta(j):
            parts = ((j.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            return "".join(p.get("text", "") for p in parts)
        return url, headers, body, delta

    body = {"model": model, "messages": messages, "stream": not json_mode,
            "temperature": TEMPERATURE, "max_tokens": MAX_OUTPUT_TOKENS}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    url = s["endpoint"] if s["provider"] == "custom" else PROVIDERS[s["provider"]]["endpoint"]

    def delta(j):
        choice = (j.get("choices") or [{}])[0]
        if json_mode:
            return (choice.get("message") or {}).get("content") or ""
        return (choice.get("delta") or {}).get("content") or ""
    return url, headers, body, delta


def finish_reason(s: dict, j: dict) -> Optional[str]:
    """Why the model stopped, from one parsed event or reply, or None when
    the event does not say (most streamed chunks don't)."""
    if s["provider"] == "gemini":
        return (j.get("candidates") or [{}])[0].get("finishReason")
    return (j.get("choices") or [{}])[0].get("finish_reason")


# What a provider calls "ran out of room" and "won't answer this".
_CUT_SHORT = {"MAX_TOKENS", "length", "max_tokens"}
_DECLINED = {"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "content_filter", "refusal"}


def _check_finish(reason: Optional[str], name: str) -> None:
    if reason in _CUT_SHORT:
        raise AIError(f"{name}: the answer was cut short at its length limit - ask something narrower")
    if reason in _DECLINED:
        raise AIError(f"{name} declined to answer this one")


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


def _post_stream(url: str, headers: dict, body: dict, timeout: float = TIMEOUT_S) -> Iterator[bytes]:
    """POSTs and yields the response's raw lines. A refusal raises
    urllib.error.HTTPError. Split out so tests can stand in for the network."""
    req = urllib.request.Request(url, data=_json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
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


def _switch_model(s: dict, err: AIError) -> bool:
    """A free-tier model retired under us: pick its replacement, remember
    it, and say whether there is one to retry with."""
    if s["model"] or "prefer" not in PROVIDERS[s["provider"]] or not model_gone(str(err)):
        return False
    remember_model(s["provider"], None)
    nxt = suggested_model(str(err)) or pick_model(
        list_models(s), PROVIDERS[s["provider"]]["prefer"], PROVIDERS[s["provider"]]["avoid"])
    if not nxt or nxt == model_for(s):
        return False
    print(f"[ai] {provider_name(s)}: switching model to {nxt}", flush=True)
    remember_model(s["provider"], nxt)
    return True


def stream(messages: list, s: Optional[dict] = None, _retried: bool = False) -> Iterator[str]:
    """The HTTP providers' streamed answer, as text as it arrives (the SDK
    provider goes through _anthropic_events; stream_events dispatches).
    Raises AIError with an owner-readable message on any failure - after
    the text, when the answer was cut short or the provider stopped it
    part-way, so the browser can show what arrived with a note."""
    s = _require(s)
    name = provider_name(s)
    url, headers, body, delta = build_request(messages, s)
    try:
        lines = _post_stream(url, headers, body)
        first = next(lines, None)   # a refusal surfaces here, before any text
    except urllib.error.HTTPError as e:
        err = _failure(e.code, e.read() if e.fp else b"", name)
        if not _retried and _switch_model(s, err):
            yield from stream(messages, s, _retried=True)
            return
        raise err from None
    except (OSError, ValueError) as e:
        raise AIError(f"{name} could not be reached ({e})") from None

    def all_lines():
        if first is not None:
            yield first
        yield from lines
    reason = None
    try:
        for event in _sse(all_lines()):
            text = delta(event)
            if text:
                yield text
            reason = finish_reason(s, event) or reason
    except (OSError, ValueError) as e:
        raise AIError(f"{name}: the answer was cut off ({e})") from None
    _check_finish(reason, name)


def _strip_fences(text: str) -> str:
    """A small model in JSON mode still sometimes wraps the object in a
    ```json fence, or leads with a sentence. Keep the outermost {...}."""
    m = _re.search(r"\{.*\}", text, _re.S)
    return m.group(0) if m else text


def _complete_json_http(messages: list, s: dict, _retried: bool = False) -> dict:
    name = provider_name(s)
    url, headers, body, delta = build_request(messages, s, json_mode=True)
    try:
        raw = b"".join(_post_stream(url, headers, body))
    except urllib.error.HTTPError as e:
        err = _failure(e.code, e.read() if e.fp else b"", name)
        if not _retried and _switch_model(s, err):
            return _complete_json_http(messages, s, _retried=True)
        raise err from None
    except (OSError, ValueError) as e:
        raise AIError(f"{name} could not be reached ({e})") from None
    try:
        j = _json.loads(raw)
    except ValueError:
        raise AIError(f"{name}: the reply could not be read") from None
    _check_finish(finish_reason(s, j), name)
    try:
        return _json.loads(_strip_fences(delta(j)))
    except ValueError:
        raise AIError(f"{name}: the reply was not the JSON the app asked for - try again") from None


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #
_anthropic = {"key": None, "client": None}


def _anthropic_client(s: dict):
    """One SDK client per key. Split out so tests can stand in for it."""
    if _anthropic["client"] is None or _anthropic["key"] != s["api_key"]:
        import anthropic
        _anthropic["key"] = s["api_key"]
        _anthropic["client"] = anthropic.Anthropic(api_key=s["api_key"], timeout=ANTHROPIC_TIMEOUT_S,
                                                   max_retries=0)
    return _anthropic["client"]


def _anthropic_kwargs(messages: list, s: dict, effort: str, **output_config) -> dict:
    """One request's arguments. Caching: the system prompt and the first
    user turn (the figures - the bulk of every request) carry a cache
    breakpoint, so a follow-up within five minutes re-reads them from
    cache. The figures must therefore be byte-identical between a question
    and its follow-ups: routers/ai.py's build_messages keeps them in the
    first turn, verbatim."""
    msgs = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
    if msgs:
        msgs[0]["content"] = [{"type": "text", "text": msgs[0]["content"], "cache_control": {"type": "ephemeral"}}]
    kw = dict(model=model_for(s), max_tokens=ANTHROPIC_MAX_TOKENS, messages=msgs,
              output_config={"effort": effort, **output_config})
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    if system:
        kw["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    return kw


def _anthropic_fail(e: Exception) -> AIError:
    """The SDK's typed errors, most specific first, each as a sentence for
    the owner. Imported here: the SDK is only needed with this provider."""
    import anthropic
    if isinstance(e, anthropic.AuthenticationError):
        return AIError("Anthropic: the API key was rejected")
    if isinstance(e, anthropic.RateLimitError):
        return AIError("Anthropic: busy right now, try again in a minute")
    if isinstance(e, anthropic.APIConnectionError):
        return AIError("Anthropic could not be reached - check the server's internet connection")
    status = getattr(e, "status_code", "?")
    print(f"[ai] Anthropic API error {status}: {e}", flush=True)
    return AIError(f"Anthropic: the request failed (HTTP {status})")


def _log_usage(what: str, usage) -> None:
    print(f"[ai] {what}: in={getattr(usage, 'input_tokens', 0)} "
          f"cached={getattr(usage, 'cache_read_input_tokens', 0) or 0} "
          f"out={getattr(usage, 'output_tokens', 0)}", flush=True)


def _anthropic_events(messages: list, s: dict, tools: Optional[list], run_tool: Optional[Callable],
                      effort: str) -> Iterator[dict]:
    """The streamed answer as events: {"t": text} as it arrives, {"step":
    sentence} each time the model looks something up. With `tools`, the
    model may ask for a lookup; run_tool(name, input) answers it and the
    model continues - up to MAX_TOOL_ROUNDS times in one answer."""
    import anthropic
    client = _anthropic_client(s)
    name = provider_name(s)
    kwargs = _anthropic_kwargs(messages, s, effort)
    by_name = {t["name"]: t for t in tools or []}
    if tools:
        kwargs["tools"] = [{"name": t["name"], "description": t["description"],
                            "input_schema": t["input_schema"]} for t in tools]
    msgs = kwargs["messages"]
    for _round in range(MAX_TOOL_ROUNDS + 1):
        try:
            with client.messages.stream(**kwargs) as st:
                for text in st.text_stream:
                    yield {"t": text}
                final = st.get_final_message()
        except anthropic.APIError as e:
            raise _anthropic_fail(e) from None
        _log_usage("ask", final.usage)
        _check_finish(final.stop_reason, name)
        if final.stop_reason != "tool_use" or not run_tool:
            return
        # The whole assistant turn goes back as-is (thinking blocks included,
        # which the API requires unchanged), then every result in one message.
        msgs.append({"role": "assistant", "content": final.content})
        results = []
        for block in final.content:
            if block.type != "tool_use":
                continue
            yield {"step": by_name[block.name]["step"](block.input)}
            try:
                out = run_tool(block.name, dict(block.input))
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": _json.dumps(out, default=str)})
            except Exception as e:  # noqa: BLE001 - the model is told, and carries on
                print(f"[ai] tool {block.name} failed: {e}", flush=True)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": f"Error: {e}", "is_error": True})
        msgs.append({"role": "user", "content": results})
    raise AIError(f"{name}: too many lookups for one answer - ask something narrower")


def _anthropic_json(messages: list, schema: dict, s: dict, effort: str) -> dict:
    import anthropic
    client = _anthropic_client(s)
    kwargs = _anthropic_kwargs(messages, s, effort, format={"type": "json_schema", "schema": schema})
    try:
        # One retry here: nothing is streaming, and a review is worth a second go.
        response = client.with_options(max_retries=1).messages.create(**kwargs)
    except anthropic.APIError as e:
        raise _anthropic_fail(e) from None
    _log_usage("json", response.usage)
    _check_finish(response.stop_reason, provider_name(s))
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return _json.loads(text)
    except ValueError:
        raise AIError(f"{provider_name(s)}: the reply could not be read - try again") from None


# --------------------------------------------------------------------------- #
# The three calls routers/ai.py makes
# --------------------------------------------------------------------------- #
def stream_events(messages: list, s: Optional[dict] = None, tools: Optional[list] = None,
                  run_tool: Optional[Callable] = None, effort: str = "low") -> Iterator[dict]:
    """The streamed answer as {"t": text} pieces, plus {"step": ...} when the
    model looks something up (Claude only - the other providers answer from
    the figures sent and never see `tools`). Counts one call against the
    daily cap. Raises AIError, possibly after some text."""
    s = _require(s)
    spend_call()
    if PROVIDERS[s["provider"]].get("sdk"):
        yield from _anthropic_events(messages, s, tools, run_tool, effort)
        return
    for text in stream(messages, s):
        yield {"t": text}


def complete_text(messages: list, s: Optional[dict] = None, effort: str = "low") -> str:
    """The whole answer at once (the daily brief)."""
    return "".join(ev.get("t", "") for ev in stream_events(messages, s, effort=effort))


def complete_json(messages: list, schema: dict, s: Optional[dict] = None, effort: str = "medium") -> dict:
    """One answer shaped by `schema`: constrained server-side with Claude;
    JSON mode plus the schema in the prompt with the others, so the caller
    still checks what came back (routers/ai.py does, block by block)."""
    s = _require(s)
    spend_call()
    if PROVIDERS[s["provider"]].get("sdk"):
        return _anthropic_json(messages, schema, s, effort)
    # The schema goes into the last user turn: the free-tier JSON modes
    # take a MIME type or {"type": "json_object"}, not a schema.
    messages = [dict(m) for m in messages]
    messages[-1]["content"] += ("\n\nReply with one JSON object only, matching this schema exactly:\n"
                                + _json.dumps(schema))
    out = _complete_json_http(messages, s)
    if not isinstance(out, dict):
        raise AIError(f"{provider_name(s)}: the reply was not the JSON the app asked for - try again")
    return out
