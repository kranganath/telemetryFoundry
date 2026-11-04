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
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_last_error: Optional[str] = None


def last_error() -> Optional[str]:
    """Reason the most recent LLM call failed (None after a success)."""
    return _last_error

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
) -> Optional[str]:
    """Raw chat call. Returns content string, or None when unavailable."""
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
    global _last_error
    timeout = _timeout_seconds()
    logger.info("llm chat model=%s base=%s messages=%d timeout=%ss",
                cfg["model"], cfg["base_url"], len(messages), timeout)
    try:
        content = _post(cfg, headers, payload, timeout)
    except Exception as first:
        # Retry once as plain chat only when the endpoint rejected the
        # JSON format (400-class). Timeouts and connection errors fail
        # fast instead of doubling the hang.
        if json_mode and _is_format_rejection(first):
            try:
                payload = {k: v for k, v in payload.items()
                           if k != "response_format"}
                content = _post(cfg, headers, payload, timeout)
            except Exception as exc:
                _last_error = _describe(exc, timeout)
                logger.warning("llm chat failed model=%s: %s",
                               cfg["model"], _last_error)
                return None
        else:
            _last_error = _describe(first, timeout)
            logger.warning("llm chat failed model=%s: %s",
                           cfg["model"], _last_error)
            return None
    _last_error = None
    return content


def _timeout_seconds() -> float:
    try:
        return max(5.0, float(os.environ.get("META_TIMEOUT_SECONDS", "60")))
    except ValueError:
        return 60.0


def _post(cfg: dict, headers: dict, payload: dict, timeout: float) -> str:
    limits = httpx.Timeout(connect=10.0, read=timeout, write=30.0,
                           pool=10.0)
    with httpx.Client(timeout=limits) as client:
        resp = client.post(
            f"{cfg['base_url']}/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def _is_format_rejection(exc: Exception) -> bool:
    resp = getattr(exc, "response", None)
    return (resp is not None
            and getattr(resp, "status_code", None) in (400, 415, 422))


def _describe(exc: Exception, timeout: float) -> str:
    if isinstance(exc, httpx.ConnectTimeout):
        return ("connection timed out after 10s — host unreachable "
                "(check META_API_BASE_URL, VPN, or proxy)")
    if isinstance(exc, httpx.TimeoutException):
        return (f"no response after {timeout:g}s — endpoint accepted the "
                f"connection but never replied (check META_MODEL / server "
                f"load, or raise META_TIMEOUT_SECONDS)")
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


def chat_json(system: str, user: str, max_tokens: int = 4000) -> Optional[list | dict]:
    """JSON chat call with offline fallback (returns None)."""
    global _last_error
    content = chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        json_mode=True,
    )
    if not content:
        return None
    try:
        return json.loads(_strip_fences(content))
    except Exception as exc:
        _last_error = f"invalid JSON response: {_short(exc)}"
        return None
