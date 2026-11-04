"""LLM provider. Default: Meta model API (OpenAI-compatible chat completions).

Configure with::

    META_API_BASE_URL=https://api.llama.com/v1   # or an internal endpoint
    META_API_KEY=<key>
    META_MODEL=<model id>
    META_TIMEOUT_SECONDS=60  # per-request read timeout (optional)

Point META_API_BASE_URL at any OpenAI-compatible chat-completions endpoint.
Absent credentials => offline demo mode (templates + heuristics).
"""

from __future__ import annotations

import json
import logging
import os
import re as _re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_last_error: Optional[str] = None
_last_content: Optional[str] = None
_last_finish_reason: Optional[str] = None

#: Hard ceiling for the automatic truncation retry in chat_json().
MAX_JSON_TOKENS = 16000


def last_error() -> Optional[str]:
    """Reason the most recent LLM call failed (None after a success)."""
    return _last_error


def last_content() -> Optional[str]:
    """Raw content of the most recent LLM response (None if no response)."""
    return _last_content


def last_finish_reason() -> Optional[str]:
    """Finish reason of the most recent LLM response (None if unknown)."""
    return _last_finish_reason

DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"


def llm_config() -> dict:
    return {
        "base_url": os.environ.get("META_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        "api_key": os.environ.get("META_API_KEY", ""),
        "model": os.environ.get("META_MODEL", DEFAULT_MODEL),
    }


def llm_enabled() -> bool:
    cfg = llm_config()
    return bool(cfg["base_url"] and cfg["api_key"] and cfg["model"])


def provider_name() -> str:
    return "meta" if llm_enabled() else "offline-demo"


def model_name() -> str:
    return llm_config()["model"]


def chat(
    messages: list[dict],
    max_tokens: int = 4000,
    json_mode: bool = False,
    timeout_seconds: Optional[float] = None,
) -> Optional[str]:
    """Raw chat call. Returns content string, or None when unavailable.

    timeout_seconds overrides META_TIMEOUT_SECONDS for calls whose output
    legitimately takes longer than the default (e.g. multi-file export).
    """
    if not llm_enabled():
        return None
    cfg = llm_config()
    payload: dict = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    global _last_error, _last_content, _last_finish_reason
    _last_content = None
    _last_finish_reason = None
    timeout = timeout_seconds if timeout_seconds else _timeout_seconds()
    timeout_env = None if timeout_seconds else "META_TIMEOUT_SECONDS"
    logger.info("llm chat model=%s base=%s messages=%d timeout=%ss",
                cfg["model"], cfg["base_url"], len(messages), timeout)
    try:
        content, finish_reason = _post(cfg, headers, payload, timeout)
    except Exception as first:
        # Retry once as plain chat only when the endpoint rejected the
        # JSON format (400-class). Timeouts and connection errors fail
        # fast instead of doubling the hang.
        if json_mode and _is_format_rejection(first):
            try:
                payload = {k: v for k, v in payload.items()
                           if k != "response_format"}
                content, finish_reason = _post(cfg, headers, payload, timeout)
            except Exception as exc:
                _last_error = _describe(exc, timeout, timeout_env)
                logger.warning("llm chat failed model=%s: %s",
                               cfg["model"], _last_error)
                return None
        else:
            _last_error = _describe(first, timeout, timeout_env)
            logger.warning("llm chat failed model=%s: %s",
                           cfg["model"], _last_error)
            return None
    _last_content = content
    _last_finish_reason = finish_reason
    if not content:
        _last_error = (f"empty response from model (finish_reason="
                       f"{finish_reason or 'unknown'})")
        logger.warning("llm chat failed model=%s: %s",
                       cfg["model"], _last_error)
        return None
    logger.info("llm chat ok model=%s chars=%d finish_reason=%s",
                cfg["model"], len(content), finish_reason)
    _last_error = None
    return content


def _timeout_seconds() -> float:
    try:
        return max(5.0, float(os.environ.get("META_TIMEOUT_SECONDS", "60")))
    except ValueError:
        return 60.0


def _post(cfg: dict, headers: dict, payload: dict,
          timeout: float) -> tuple[Optional[str], Optional[str]]:
    """POST a chat completion; return (content, finish_reason)."""
    limits = httpx.Timeout(connect=10.0, read=timeout, write=30.0,
                           pool=10.0)
    with httpx.Client(timeout=limits) as client:
        resp = client.post(
            f"{cfg['base_url']}/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        choices = resp.json().get("choices") or [{}]
        choice = choices[0] or {}
        message = choice.get("message") or {}
        return message.get("content"), choice.get("finish_reason")


def _is_format_rejection(exc: Exception) -> bool:
    resp = getattr(exc, "response", None)
    return (resp is not None
            and getattr(resp, "status_code", None) in (400, 415, 422))


def _describe(exc: Exception, timeout: float,
              timeout_env: Optional[str] = "META_TIMEOUT_SECONDS") -> str:
    if isinstance(exc, httpx.ConnectTimeout):
        return ("connection timed out after 10s — host unreachable "
                "(check META_API_BASE_URL, VPN, or proxy)")
    if isinstance(exc, httpx.TimeoutException):
        hint = f", or raise {timeout_env}" if timeout_env else ""
        return (f"no response after {timeout:g}s — endpoint accepted the "
                f"connection but never replied (check META_MODEL / server "
                f"load{hint})")
    if isinstance(exc, httpx.ConnectError):
        return ("connection failed — endpoint unreachable "
                f"(check META_API_BASE_URL): {exc}")
    return _short(exc)


def _short(exc: Exception) -> str:
    text = exc_str(exc)
    return text if len(text) <= 200 else text[:197] + "..."


def exc_str(exc: Exception) -> str:
    try:
        return f"{type(exc).__name__}: {exc}"
    except Exception:
        return type(exc).__name__


def _strip_fences(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]  # opening fence (``` or ```json)
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def chat_json(system: str, user: str, max_tokens: int = 4000,
              timeout_seconds: Optional[float] = None,
              _retried: bool = False) -> Optional[list | dict]:
    """JSON chat call with offline fallback (returns None).

    A response cut off by the token budget is retried once with a doubled
    budget instead of surfacing a raw JSONDecodeError.
    """
    global _last_error, _last_content, _last_finish_reason
    _last_error = None
    _last_content = None
    _last_finish_reason = None
    content = chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        json_mode=True,
        timeout_seconds=timeout_seconds,
    )
    if not content:
        if _last_error is None:
            _last_error = "empty response from LLM"
        return None
    text = _strip_fences(content)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        if _looks_truncated(text, exc, _last_finish_reason):
            if not _retried and max_tokens < MAX_JSON_TOKENS:
                bigger = min(max_tokens * 2, MAX_JSON_TOKENS)
                logger.info("llm json truncated at %d tokens, retrying with %d",
                            max_tokens, bigger)
                return chat_json(system, user, max_tokens=bigger,
                                 timeout_seconds=timeout_seconds,
                                 _retried=True)
            _last_error = (
                f"response truncated at max_tokens={max_tokens} "
                f"(finish_reason={_last_finish_reason or 'unknown'}): "
                f"output hit the token limit")
            return None
        repaired = _try_repair_json(text)
        if repaired is not None:
            _last_error = None
            return repaired
        _last_error = f"invalid JSON response: {_short(exc)}"
        return None
    except Exception as exc:
        _last_error = f"invalid JSON response: {_short(exc)}"
        return None
    _last_error = None
    return data


def _looks_truncated(text: str, exc: json.JSONDecodeError,
                     finish_reason: Optional[str]) -> bool:
    """Heuristic: did the token budget cut the response mid-JSON?"""
    if (finish_reason or "").lower() in ("length", "max_tokens"):
        return True
    if "Unterminated string" in str(exc):
        return True
    # The parser ran out of input at the very end (e.g. "Expecting ','
    # delimiter" at EOF) means the model stopped mid-object. Errors with
    # plenty of text after them are malformed-but-complete responses.
    return exc.pos >= len(text.rstrip()) - 1


def _try_repair_json(text: str) -> Optional[list | dict]:
    """Parse complete-but-sloppy LLM JSON. Never completes truncation."""
    candidates = [text]
    start_candidates = [i for i in (text.find("{"), text.find("["))
                        if i >= 0]
    end_candidates = [i for i in (text.rfind("}"), text.rfind("]"))
                      if i >= 0]
    if start_candidates and end_candidates:
        snippet = text[min(start_candidates):max(end_candidates) + 1]
        if snippet != text:
            candidates.append(snippet)
    for candidate in candidates:
        for attempt in (candidate,
                        _re.sub(r",\s*([}\]])", r"\1", candidate)):
            for strict in (True, False):
                try:
                    return json.loads(attempt, strict=strict)
                except Exception:
                    continue
    return None
