"""LLM service — async proxy to OpenAI Chat Completions API and Google Gemini API.

Provider selection is centralized in :func:`_resolve_provider`. A single app-wide
model choice (persisted in Settings and carried through the request via the
``active_model`` context variable) always wins, so flipping the model in Settings
re-routes *every* feature — OpenAI models go to OpenAI, Gemini models go to Gemini,
with no per-call-site branching.

All JSON extraction goes through :func:`clean_json_text` (Gemini wraps JSON in
```-fences; OpenAI usually does not), and every request goes through
:func:`_post_chat`, which retries ``429``/``5xx`` with backoff that honors the
provider's ``Retry-After`` / ``retryDelay`` hint — this keeps Gemini's free-tier
rate limits from surfacing as hard errors.
"""

import json
import logging
import time
import asyncio
import contextvars
import random
import re

import httpx

from ..database import async_session
from ..models import ApiMetric

logger = logging.getLogger(__name__)

# Request-scoped model context variable. Set by ``auth.get_user_api_key`` to the
# user's currently-selected model so provider routing is consistent across every
# LLM call made while handling a request (or a single user's scheduler run).
active_model = contextvars.ContextVar("active_model", default=None)

# ---------------------------------------------------------------------------
# Provider constants
# ---------------------------------------------------------------------------
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
# Gemini exposes an OpenAI-compatible surface, so the same payload shape works.
GEMINI_CHAT_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GEMINI_MODEL_PREFIX = "gemini-"
GEMINI_KEY_PREFIX = "AIzaSy"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

# Gemini retires model IDs on a schedule; requests to a retired ID return HTTP 404
# (Gemini 1.5 is fully shut down; Gemini 2.0 Flash was shut down 2026-06-01).
# Transparently upgrade any persisted-but-dead selection to a current equivalent so
# users who picked e.g. gemini-2.0-flash before it was retired keep working without
# having to re-open Settings.
DEPRECATED_GEMINI_MODELS = {
    "gemini-2.0-flash": "gemini-2.5-flash",
    "gemini-2.0-flash-001": "gemini-2.5-flash",
    "gemini-2.0-flash-lite": "gemini-2.5-flash",
    "gemini-1.5-flash": "gemini-2.5-flash",
    "gemini-1.5-flash-latest": "gemini-2.5-flash",
    "gemini-1.5-flash-8b": "gemini-2.5-flash",
    "gemini-1.5-pro": "gemini-2.5-pro",
    "gemini-1.5-pro-latest": "gemini-2.5-pro",
    "gemini-1.0-pro": "gemini-2.5-pro",
}


def canonical_model(model: str | None) -> str | None:
    """Map a retired Gemini model ID to a current replacement (pass-through otherwise)."""
    if not model:
        return model
    return DEPRECATED_GEMINI_MODELS.get(model, model)

# Retry policy for transient/rate-limit responses.
_MAX_RETRIES = 3
_MAX_RETRY_WAIT = 60.0
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Gemini 2.5 models spend part of ``max_tokens`` on internal "thinking" tokens, so a
# small cap can truncate — or entirely empty out — the visible answer. Give them a
# floor. ``max_tokens`` is only an upper bound, so raising it never lengthens a
# response the model considers complete; it just prevents premature truncation.
_GEMINI_THINKING_PREFIX = "gemini-2.5"
_GEMINI_MIN_OUTPUT_TOKENS = 4096


def _apply_token_floor(provider: str, model: str, max_tokens: int) -> int:
    """Raise ``max_tokens`` to a safe floor for Gemini 2.5 (thinking) models."""
    if provider == "gemini" and model.startswith(_GEMINI_THINKING_PREFIX):
        return max(max_tokens, _GEMINI_MIN_OUTPUT_TOKENS)
    return max_tokens


def _resolve_provider(api_key: str | None, model: str | None) -> tuple[str, str, str]:
    """Return ``(provider, url, resolved_model)`` for an LLM call.

    The app-wide selection (``active_model``) wins outright so a single Settings
    toggle switches the whole app between OpenAI and Gemini. When no selection is
    in context, fall back to the explicit ``model`` argument, then to the shape of
    the API key (Gemini keys start with ``AIzaSy``).
    """
    selected = canonical_model(active_model.get())
    if selected:
        if selected.startswith(GEMINI_MODEL_PREFIX):
            return "gemini", GEMINI_CHAT_URL, selected
        return "openai", OPENAI_CHAT_URL, selected

    model = canonical_model(model) or ""
    if model.startswith(GEMINI_MODEL_PREFIX) or (api_key or "").startswith(GEMINI_KEY_PREFIX):
        resolved = model if model.startswith(GEMINI_MODEL_PREFIX) else DEFAULT_GEMINI_MODEL
        return "gemini", GEMINI_CHAT_URL, resolved

    return "openai", OPENAI_CHAT_URL, model or DEFAULT_OPENAI_MODEL


def _log_request(provider: str, model: str, messages: list, has_tools: bool, max_tokens: int) -> None:
    """Log an outgoing LLM API request summary."""
    msg_summary = []
    for m in messages:
        role = m.get("role", "?")
        if role == "tool":
            msg_summary.append(f"tool(call_id={m.get('tool_call_id', '?')[:12]}…)")
        else:
            content = m.get("content", "")
            preview = (content[:120] + "…") if isinstance(content, str) and len(content) > 120 else content
            msg_summary.append(f"{role}: {preview}")

    logger.info(
        "%s REQUEST  model=%s  messages=%d  tools=%s  max_tokens=%d\n  %s",
        provider.upper(),
        model,
        len(messages),
        "yes" if has_tools else "no",
        max_tokens,
        "\n  ".join(msg_summary),
    )


def _log_response(provider: str, model: str, data: dict, elapsed_ms: int) -> None:
    """Log an incoming LLM API response summary."""
    usage = data.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", "?")
    completion_tokens = usage.get("completion_tokens", "?")
    total_tokens = usage.get("total_tokens", "?")

    choice = data.get("choices", [{}])[0]
    message = choice.get("message", {})
    finish = choice.get("finish_reason", "?")

    tool_calls = message.get("tool_calls")
    if tool_calls:
        calls = [tc["function"]["name"] for tc in tool_calls]
        content_summary = f"tool_calls=[{', '.join(calls)}]"
    else:
        content = message.get("content", "")
        content_summary = (content[:150] + "…") if isinstance(content, str) and len(content) > 150 else content

    logger.info(
        "%s RESPONSE model=%s  tokens=%s/%s/%s (prompt/completion/total)  "
        "finish=%s  elapsed=%dms\n  %s",
        provider.upper(),
        model,
        prompt_tokens,
        completion_tokens,
        total_tokens,
        finish,
        elapsed_ms,
        content_summary,
    )


async def _log_metric(provider: str, endpoint: str, latency: float, status: int):
    async def _write():
        try:
            async with async_session() as session:
                metric = ApiMetric(
                    provider=provider,
                    endpoint=endpoint,
                    method="POST",
                    status_code=status,
                    latency_ms=latency
                )
                session.add(metric)
                await session.commit()
        except Exception as e:
            logger.error(f"Failed to save {provider} metric: {e}")

    asyncio.create_task(_write())


# ---------------------------------------------------------------------------
# Rate-limit aware request execution
# ---------------------------------------------------------------------------

def _search_retry_delay(obj) -> float | None:
    """Recursively find a provider-supplied retry hint (e.g. Gemini's ``retryDelay: "17s"``)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("retryDelay", "retryAfter", "retry_after") and isinstance(v, str):
                m = re.match(r"\s*([0-9.]+)\s*s?", v)
                if m:
                    try:
                        return float(m.group(1))
                    except ValueError:
                        pass
            found = _search_retry_delay(v)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _search_retry_delay(item)
            if found is not None:
                return found
    return None


def _retry_delay_seconds(response: httpx.Response | None, attempt: int) -> float:
    """How long to wait before retrying, honoring server hints then exponential backoff."""
    if response is not None:
        header = response.headers.get("retry-after")
        if header:
            try:
                return min(float(header), _MAX_RETRY_WAIT)
            except ValueError:
                pass  # HTTP-date form is rare for these APIs; fall through to backoff
        try:
            hinted = _search_retry_delay(response.json())
            if hinted is not None:
                return min(hinted, _MAX_RETRY_WAIT)
        except Exception:
            pass
    # Exponential backoff with jitter: ~2s, 4s, 8s …
    return min(2.0 ** attempt, _MAX_RETRY_WAIT) + random.uniform(0.0, 0.75)


async def _post_chat(
    url: str,
    headers: dict,
    payload: dict,
    *,
    provider: str,
    endpoint_label: str,
    timeout: float,
) -> dict:
    """POST a chat request, retrying ``429``/``5xx`` with backoff. Returns the JSON body.

    Raises ``httpx.HTTPStatusError`` on a non-retryable (or retry-exhausted) status.
    Emits exactly one API metric for the whole attempt sequence.
    """
    start = time.monotonic()
    status_code = 200
    try:
        for attempt in range(1, _MAX_RETRIES + 2):  # attempts 1..(_MAX_RETRIES+1)
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(url, headers=headers, json=payload)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt <= _MAX_RETRIES:
                    delay = _retry_delay_seconds(None, attempt)
                    logger.warning(
                        "%s %s transport error (attempt %d/%d): %s — retrying in %.1fs",
                        provider, endpoint_label, attempt, _MAX_RETRIES, exc, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                status_code = 599
                raise

            if response.status_code in _RETRYABLE_STATUS and attempt <= _MAX_RETRIES:
                delay = _retry_delay_seconds(response, attempt)
                logger.warning(
                    "%s %s -> HTTP %d (attempt %d/%d) — retrying in %.1fs",
                    provider, endpoint_label, response.status_code, attempt, _MAX_RETRIES, delay,
                )
                await asyncio.sleep(delay)
                continue

            status_code = response.status_code
            response.raise_for_status()
            return response.json()

        # Unreachable: the final attempt either returns or raises above.
        raise RuntimeError("retry loop exhausted without a response")
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code == 404:
            # A 404 from the chat endpoint almost always means the model ID is
            # unknown to the provider (retired or misspelled). Surface a clear,
            # provider-correct message instead of the raw URL (whose path contains
            # the substring "openai" and confuses provider heuristics upstream).
            raise ValueError(
                f"Model '{payload.get('model')}' was rejected by {provider} "
                f"(HTTP 404 — it may be retired or misspelled). "
                f"Choose a current model in Settings."
            ) from exc
        raise
    except Exception:
        if status_code == 200:
            status_code = 500
        raise
    finally:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        await _log_metric(provider, endpoint_label, elapsed_ms, status_code)


# ---------------------------------------------------------------------------
# JSON extraction (centralized) — handles Gemini's ```-fenced JSON responses
# ---------------------------------------------------------------------------

def repair_truncated_json(json_str: str) -> str:
    """Repair truncated JSON by closing opened quotes, brackets, and braces."""
    s = json_str.strip()
    if not s:
        return s

    # Try parsing directly first
    try:
        json.loads(s)
        return s
    except ValueError:
        pass

    # Trace open braces, brackets, and quotes
    stack = []
    in_quote = False
    escaped = False

    i = 0
    clean_str = ""
    while i < len(s):
        char = s[i]
        clean_str += char

        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            in_quote = not in_quote
        elif not in_quote:
            if char == "{":
                stack.append("}")
            elif char == "[":
                stack.append("]")
            elif char == "}":
                if stack and stack[-1] == "}":
                    stack.pop()
            elif char == "]":
                if stack and stack[-1] == "]":
                    stack.pop()
        i += 1

    # If it was cut off inside a quote
    if in_quote:
        if clean_str.endswith("\\"):
            clean_str = clean_str[:-1]
        clean_str += '"'

    # Close any open structures in reverse order
    while stack:
        close_char = stack.pop()
        clean_str = clean_str.strip()
        if clean_str.endswith(","):
            clean_str = clean_str[:-1].strip()
        clean_str += close_char

    return clean_str


def _first_balanced_json(s: str) -> str | None:
    """Return the first balanced ``{...}`` / ``[...]`` block in ``s``.

    Ignores braces inside strings. If the block is truncated (never closes), returns
    from the opening brace to the end so ``repair_truncated_json`` can finish it.
    """
    start = -1
    opener = closer = ""
    for i, ch in enumerate(s):
        if ch in "{[":
            start = i
            opener = ch
            closer = "}" if ch == "{" else "]"
            break
    if start == -1:
        return None

    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return s[start:]  # truncated — let the repairer close it


def clean_json_text(text: str) -> str:
    """Extract a parseable JSON string from raw LLM output.

    Handles the shapes Gemini (and occasionally OpenAI) produce: ```json fenced
    blocks, leading/trailing conversational prose, trailing fences, and truncated
    output. This is the single place fences are stripped — callers that pass
    ``expect_json=True`` to :func:`call_llm` get cleaned JSON back automatically.
    """
    cleaned = (text or "").strip()

    # 1) Prefer the contents of a ```-fenced block if one is present anywhere.
    fence = re.search(r"```(?:json|JSON)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    elif cleaned.startswith("```"):
        # Opening fence with no close (truncated stream): drop the fence line.
        newline = cleaned.find("\n")
        cleaned = (cleaned[newline + 1:] if newline != -1 else cleaned[3:]).strip()

    # 2) Isolate the first balanced JSON value, discarding surrounding prose.
    candidate = _first_balanced_json(cleaned)
    if candidate is None:
        return cleaned
    candidate = candidate.strip()

    # 3) Parse as-is, else attempt a truncation repair.
    try:
        json.loads(candidate)
        return candidate
    except ValueError:
        repaired = repair_truncated_json(candidate)
        try:
            json.loads(repaired)
            return repaired
        except ValueError:
            return candidate


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def call_llm(
    api_key: str,
    model: str,
    messages: list,
    max_tokens: int = 1000,
    temperature: float = 1.0,
    expect_json: bool = False,
) -> str:
    """Call OpenAI or Gemini chat completions and return the assistant message content.

    Provider/model are resolved centrally (see :func:`_resolve_provider`), the
    request is retried on rate-limit/transient errors, and — when
    ``expect_json`` is set — the content is run through :func:`clean_json_text`
    so fenced Gemini JSON is stripped before it reaches the caller.

    Raises ``httpx.HTTPStatusError`` on non-2xx responses, or ``ValueError`` if
    the response body is unexpected.
    """
    provider, url, resolved_model = _resolve_provider(api_key, model)
    max_tokens = _apply_token_floor(provider, resolved_model, max_tokens)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": resolved_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    _log_request(provider, resolved_model, messages, has_tools=False, max_tokens=max_tokens)
    start = time.monotonic()
    data = await _post_chat(
        url, headers, payload,
        provider=provider, endpoint_label="chat/completions", timeout=120.0,
    )
    elapsed_ms = int((time.monotonic() - start) * 1000)
    _log_response(provider, resolved_model, data, elapsed_ms)

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise ValueError(f"Unexpected {provider.capitalize()} response structure: {data}") from exc

    if expect_json:
        content = clean_json_text(content)
    return content


async def call_llm_with_tools(
    api_key: str,
    model: str,
    messages: list,
    tools: list,
    max_tokens: int = 4096,
) -> dict:
    """Call OpenAI or Gemini with function calling (tools).

    Returns the full assistant message dict which may contain either:
    - ``content`` (final text response) or
    - ``tool_calls`` (list of function calls to execute)
    """
    provider, url, resolved_model = _resolve_provider(api_key, model)
    max_tokens = _apply_token_floor(provider, resolved_model, max_tokens)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": resolved_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "tools": tools,
    }

    _log_request(provider, resolved_model, messages, has_tools=True, max_tokens=max_tokens)
    start = time.monotonic()
    data = await _post_chat(
        url, headers, payload,
        provider=provider, endpoint_label="chat/completions (tools)", timeout=180.0,
    )
    elapsed_ms = int((time.monotonic() - start) * 1000)
    _log_response(provider, resolved_model, data, elapsed_ms)

    try:
        return data["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        raise ValueError(f"Unexpected {provider.capitalize()} response structure: {data}") from exc
