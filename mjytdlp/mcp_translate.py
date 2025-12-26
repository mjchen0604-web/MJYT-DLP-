from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import requests

from .mcp_settings import load_mcp_settings


DEFAULT_TIMEOUT = 30


class ProviderError(Exception):
    pass


def _resolve_provider(provider_id: Optional[str]) -> Dict[str, Any]:
    settings = load_mcp_settings()
    providers = settings.get("providers") if isinstance(settings.get("providers"), list) else []
    default_provider = settings.get("default_provider")

    chosen = provider_id or default_provider
    if not chosen:
        raise ProviderError("No provider configured. Set default_provider in MCP settings.")

    provider = next((p for p in providers if isinstance(p, dict) and p.get("id") == chosen), None)
    if not provider:
        raise ProviderError(f"Provider not found: {chosen}")
    if not provider.get("enabled", True):
        raise ProviderError(f"Provider disabled: {chosen}")
    return provider


def _provider_endpoint(provider: Dict[str, Any]) -> str:
    endpoint = (provider.get("endpoint_url") or "").strip()
    if endpoint:
        return endpoint
    base = (provider.get("base_url") or "").strip()
    if base.endswith("/"):
        base = base[:-1]
    if base:
        return f"{base}/v1/chat/completions"
    return ""


def _provider_headers(provider: Dict[str, Any]) -> Dict[str, str]:
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    extra = provider.get("extra_headers")
    if isinstance(extra, dict):
        for k, v in extra.items():
            if isinstance(k, str) and isinstance(v, str):
                kk = k.strip()
                vv = v.strip()
                if kk and vv:
                    headers[kk] = vv

    api_key_env = (provider.get("api_key_env") or "").strip()
    api_key = os.getenv(api_key_env) if api_key_env else (provider.get("api_key") or "")
    api_key = api_key.strip() if isinstance(api_key, str) else ""
    auth_header = provider.get("auth_header")
    auth_prefix = provider.get("auth_prefix")
    if api_key and isinstance(auth_header, str) and auth_header:
        prefix = auth_prefix if isinstance(auth_prefix, str) else "Bearer "
        headers[auth_header] = f"{prefix}{api_key}"
    return headers


def _extract_text(data: Dict[str, Any]) -> str:
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message.get("content") or ""
            if isinstance(first.get("text"), str):
                return first.get("text") or ""
    if isinstance(data.get("output_text"), str):
        return data.get("output_text") or ""
    output = data.get("output")
    if isinstance(output, list):
        parts = []
        for item in output:
            if isinstance(item, dict) and item.get("type") == "message":
                content = item.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            parts.append(part.get("text"))
        if parts:
            return "".join(parts)
    return ""


def translate_text(
    text: str,
    target: str,
    source: Optional[str] = None,
    provider_id: Optional[str] = None,
    model_override: Optional[str] = None,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise ProviderError("Missing text to translate.")
    if not isinstance(target, str) or not target.strip():
        raise ProviderError("Missing target language.")

    provider = _resolve_provider(provider_id)
    model = (model_override or provider.get("model") or "").strip()
    if not model:
        raise ProviderError("Provider missing model.")

    endpoint = _provider_endpoint(provider)
    if not endpoint:
        raise ProviderError("Provider missing endpoint_url or base_url.")

    if source:
        system_prompt = (
            f"Translate the following text from {source} to {target}. "
            "Return only the translated text."
        )
    else:
        system_prompt = (
            f"Translate the following text to {target}. "
            "Return only the translated text."
        )

    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        "temperature": 0.2 if temperature is None else float(temperature),
        "stream": False,
    }

    timeout = provider.get("timeout")
    timeout = float(timeout) if isinstance(timeout, (int, float)) else DEFAULT_TIMEOUT
    headers = _provider_headers(provider)

    try:
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise ProviderError(f"Provider request failed: {exc}") from exc

    if resp.status_code >= 400:
        message = f"Provider error ({resp.status_code})"
        try:
            data = resp.json()
            if isinstance(data, dict) and isinstance(data.get("error"), dict):
                msg = data["error"].get("message")
                if isinstance(msg, str) and msg:
                    message = msg
        except Exception:
            pass
        raise ProviderError(message)

    try:
        data = resp.json()
    except ValueError as exc:
        raise ProviderError("Provider returned non-JSON response.") from exc

    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        msg = data["error"].get("message")
        raise ProviderError(msg if isinstance(msg, str) and msg else "Provider returned error.")

    translated = _extract_text(data)
    if not translated:
        translated = json.dumps(data, ensure_ascii=False)

    return {
        "text": translated,
        "provider": provider.get("id"),
        "model": model,
    }
