from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .utils import get_data_dir, read_json, write_json_atomic


MCP_SETTINGS_FILENAME = "mcp_settings.json"

DEFAULT_MCP_SETTINGS: Dict[str, Any] = {
    "default_provider": None,
    "providers": [],
}


def settings_path(home_dir: Optional[str] = None) -> str:
    base = home_dir or get_data_dir()
    return os.path.join(base, MCP_SETTINGS_FILENAME)


def _coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "y", "on"):
            return True
        if v in ("0", "false", "no", "n", "off"):
            return False
    return None


def _as_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _sanitize_headers(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, str):
            kk = k.strip()
            vv = v.strip()
            if kk and vv:
                out[kk] = vv
    return out


def _sanitize_provider(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    pid = _as_str(raw.get("id") or raw.get("name"))
    if not pid:
        return None

    enabled = _coerce_bool(raw.get("enabled"))
    auth_header_raw = raw.get("auth_header")
    auth_prefix_raw = raw.get("auth_prefix")

    timeout_val = raw.get("timeout")
    timeout: Optional[float] = None
    if isinstance(timeout_val, (int, float)) and timeout_val > 0:
        timeout = float(timeout_val)
    elif isinstance(timeout_val, str):
        try:
            parsed = float(timeout_val.strip())
            if parsed > 0:
                timeout = parsed
        except Exception:
            timeout = None

    provider: Dict[str, Any] = {
        "id": pid,
        "label": _as_str(raw.get("label")) or pid,
        "base_url": _as_str(raw.get("base_url")),
        "endpoint_url": _as_str(raw.get("endpoint_url")),
        "model": _as_str(raw.get("model")),
        "api_key": _as_str(raw.get("api_key")),
        "api_key_env": _as_str(raw.get("api_key_env")),
        "auth_header": _as_str(auth_header_raw) if isinstance(auth_header_raw, str) else "Authorization",
        "auth_prefix": _as_str(auth_prefix_raw) if isinstance(auth_prefix_raw, str) else "Bearer ",
        "extra_headers": _sanitize_headers(raw.get("extra_headers")),
        "timeout": timeout,
        "enabled": True if enabled is None else enabled,
    }
    return provider


def _sanitize_settings(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return dict(DEFAULT_MCP_SETTINGS)

    providers_raw = raw.get("providers")
    providers: List[Dict[str, Any]] = []
    seen = set()
    if isinstance(providers_raw, list):
        for entry in providers_raw:
            provider = _sanitize_provider(entry)
            if not provider:
                continue
            pid = provider["id"]
            if pid in seen:
                continue
            seen.add(pid)
            providers.append(provider)

    default_provider = _as_str(raw.get("default_provider"))
    if default_provider and default_provider not in seen:
        default_provider = ""

    return {
        "default_provider": default_provider or None,
        "providers": providers,
    }


def load_mcp_settings(home_dir: Optional[str] = None) -> Dict[str, Any]:
    path = settings_path(home_dir)
    raw = read_json(path)
    if raw is None:
        return dict(DEFAULT_MCP_SETTINGS)
    return _sanitize_settings(raw)


def save_mcp_settings(settings: Dict[str, Any], home_dir: Optional[str] = None) -> bool:
    path = settings_path(home_dir)
    sanitized = _sanitize_settings(settings)
    payload = {**DEFAULT_MCP_SETTINGS, **sanitized}
    return write_json_atomic(path, payload)
