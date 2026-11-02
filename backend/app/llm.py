"""LLM provider. Default: Meta model API (OpenAI-compatible chat completions).

Configure with::

    META_API_BASE_URL=https://api.llama.com/v1   # or an internal endpoint
    META_API_KEY=<key>
    META_MODEL=<model id>

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
    logger.info("llm chat model=%s base=%s messages=%d",
                cfg["model"], cfg["base_url"], len(messages))
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{cfg['base_url']}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            _last_error = None
            return resp.json()["choices"][0]["message"]["content"]
    except Exception as first:
        if not json_mode:
            _last_error = _short(first)
            logger.warning("llm chat failed model=%s: %s", cfg["model"], exc_str(first))
            return None
        # Some endpoints reject response_format: retry once as plain chat.
        try:
            payload = {k: v for k, v in payload.items() if k != "response_format"}
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    f"{cfg['base_url']}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                _last_error = None
                return resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            _last_error = _short(exc)
            logger.warning("llm chat failed model=%s: %s", cfg["model"], exc_str(exc))
            return None


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
