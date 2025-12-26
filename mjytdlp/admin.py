from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .mcp_settings import DEFAULT_MCP_SETTINGS, load_mcp_settings, save_mcp_settings
from .utils import get_data_dir


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _admin_password() -> str | None:
    pw = os.getenv("MJYTDLP_ADMIN_PASSWORD")
    if isinstance(pw, str) and pw.strip():
        return pw.strip()
    return None


def _admin_disabled() -> bool:
    flag = os.getenv("MJYTDLP_DISABLE_ADMIN")
    return isinstance(flag, str) and flag.strip().lower() in ("1", "true", "yes", "on")


def _require_admin_enabled() -> None:
    if _admin_disabled() or not _admin_password():
        abort(404)


def _require_ui_login() -> None:
    if not session.get("mjytdlp_admin"):
        abort(401)


def _require_api_auth() -> None:
    pw = _admin_password()
    if not pw:
        abort(404)

    header = request.headers.get("Authorization", "")
    token = ""
    if isinstance(header, str) and header.lower().startswith("bearer "):
        token = header[7:].strip()
    if not token:
        token = (request.headers.get("X-MJYTDLP-Admin-Password") or "").strip()
    if token != pw:
        abort(401)


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "***"
    return f"{value[:2]}***{value[-2:]}"


def _cookies_path() -> str:
    return os.path.join(get_data_dir(), "cookies.txt")


def _cookies_status() -> Dict[str, Any]:
    path = _cookies_path()
    exists = os.path.isfile(path)
    size = None
    mtime = None
    if exists:
        try:
            size = os.path.getsize(path)
            mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(path)))
        except Exception:
            pass
    return {"path": path, "exists": exists, "size": size, "mtime": mtime}


def _cookies_notice(code: str | None) -> Dict[str, str] | None:
    mapping = {
        "uploaded": {"kind": "ok", "text": "cookies.txt 上传成功。"},
        "upload_failed": {"kind": "bad", "text": "cookies.txt 上传失败，请重试。"},
        "delete_failed": {"kind": "bad", "text": "cookies.txt 删除失败。"},
        "deleted": {"kind": "ok", "text": "cookies.txt 已删除。"},
        "missing": {"kind": "bad", "text": "未找到 cookies.txt。"},
        "empty": {"kind": "bad", "text": "请选择 cookies.txt 文件。"},
    }
    return mapping.get(code or "")


def _mcp_ui_providers(settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    providers = settings.get("providers") if isinstance(settings.get("providers"), list) else []
    out: List[Dict[str, Any]] = []
    for p in providers:
        if not isinstance(p, dict):
            continue
        api_key = p.get("api_key") if isinstance(p.get("api_key"), str) else ""
        out.append(
            {
                "id": p.get("id") or "",
                "label": p.get("label") or "",
                "base_url": p.get("base_url") or "",
                "endpoint_url": p.get("endpoint_url") or "",
                "model": p.get("model") or "",
                "api_key_env": p.get("api_key_env") or "",
                "api_key_masked": _mask_secret(api_key),
                "auth_header": p.get("auth_header") or "",
                "auth_prefix": p.get("auth_prefix") or "",
                "extra_headers": json.dumps(p.get("extra_headers") or {}, ensure_ascii=False, indent=2),
                "timeout": p.get("timeout") or "",
                "enabled": bool(p.get("enabled", True)),
            }
        )
    return out


@admin_bp.before_request
def _guard():
    _require_admin_enabled()


@admin_bp.get("/login")
def login_page() -> Response:
    return render_template("admin_login.html", error=None)


@admin_bp.post("/login")
def login_post() -> Response:
    pw = _admin_password() or ""
    submitted = (request.form.get("password") or "").strip()
    if submitted and submitted == pw:
        session["mjytdlp_admin"] = True
        return redirect(url_for("admin.panel"))
    return render_template("admin_login.html", error="密码不正确")


@admin_bp.post("/logout")
def logout_post() -> Response:
    session.pop("mjytdlp_admin", None)
    return redirect(url_for("admin.login_page"))


@admin_bp.get("/")
def panel() -> Response:
    if not session.get("mjytdlp_admin"):
        return redirect(url_for("admin.login_page"))

    return render_template(
        "admin_panel.html",
        home_dir=get_data_dir(),
        cookies=_cookies_status(),
        cookies_notice=_cookies_notice(request.args.get("cookies")),
    )


@admin_bp.get("/mcp")
def mcp_panel() -> Response:
    if not session.get("mjytdlp_admin"):
        return redirect(url_for("admin.login_page"))

    settings = load_mcp_settings()
    providers = _mcp_ui_providers(settings)
    return render_template(
        "admin_mcp.html",
        providers=providers,
        default_provider=settings.get("default_provider"),
        home_dir=get_data_dir(),
        error=None,
    )


@admin_bp.post("/mcp/provider")
def mcp_provider_upsert() -> Response:
    if not session.get("mjytdlp_admin"):
        return redirect(url_for("admin.login_page"))

    def _get_bool(name: str) -> bool:
        return (request.form.get(name) or "").strip().lower() in ("1", "true", "yes", "on")

    settings = load_mcp_settings()
    providers = settings.get("providers") if isinstance(settings.get("providers"), list) else []

    pid = (request.form.get("id") or "").strip()
    if not pid:
        providers_ui = _mcp_ui_providers(settings)
        return render_template(
            "admin_mcp.html",
            providers=providers_ui,
            default_provider=settings.get("default_provider"),
            home_dir=get_data_dir(),
            error="Provider id 不能为空。",
        )

    existing = next((p for p in providers if isinstance(p, dict) and p.get("id") == pid), None)

    api_key_input = (request.form.get("api_key") or "").strip()
    clear_api_key = _get_bool("clear_api_key")
    api_key = ""
    if api_key_input:
        api_key = api_key_input
    elif clear_api_key:
        api_key = ""
    elif isinstance(existing, dict):
        api_key = existing.get("api_key") or ""

    extra_headers_raw = (request.form.get("extra_headers") or "").strip()
    if extra_headers_raw:
        try:
            parsed = json.loads(extra_headers_raw)
            if not isinstance(parsed, dict):
                raise ValueError("extra_headers must be an object")
            extra_headers = parsed
        except Exception:
            providers_ui = _mcp_ui_providers(settings)
            return render_template(
                "admin_mcp.html",
                providers=providers_ui,
                default_provider=settings.get("default_provider"),
                home_dir=get_data_dir(),
                error="extra_headers 必须是合法的 JSON 对象。",
            )
    elif isinstance(existing, dict):
        extra_headers = existing.get("extra_headers") or {}
    else:
        extra_headers = {}

    timeout_raw = (request.form.get("timeout") or "").strip()
    timeout_val: Any = None
    if timeout_raw:
        try:
            timeout_val = float(timeout_raw)
        except Exception:
            timeout_val = None
    elif isinstance(existing, dict):
        timeout_val = existing.get("timeout")

    def _inherit(name: str, default: str = "") -> str:
        value = (request.form.get(name) or "").strip()
        if value:
            return value
        if isinstance(existing, dict):
            existing_val = existing.get(name)
            if isinstance(existing_val, str) and existing_val.strip():
                return existing_val.strip()
        return default

    provider_payload: Dict[str, Any] = {
        "id": pid,
        "label": _inherit("label", pid) or pid,
        "base_url": _inherit("base_url", ""),
        "endpoint_url": _inherit("endpoint_url", ""),
        "model": _inherit("model", ""),
        "api_key": api_key,
        "api_key_env": _inherit("api_key_env", ""),
        "auth_header": _inherit("auth_header", "Authorization") or "Authorization",
        "auth_prefix": _inherit("auth_prefix", "Bearer ") or "Bearer ",
        "extra_headers": extra_headers,
        "timeout": timeout_val,
        "enabled": _get_bool("enabled"),
    }

    if existing is not None:
        providers = [provider_payload if p.get("id") == pid else p for p in providers]
    else:
        providers.append(provider_payload)

    settings["providers"] = providers

    if _get_bool("set_default"):
        settings["default_provider"] = pid

    save_mcp_settings(settings)
    return redirect(url_for("admin.mcp_panel"))


@admin_bp.post("/mcp/provider/delete")
def mcp_provider_delete() -> Response:
    if not session.get("mjytdlp_admin"):
        return redirect(url_for("admin.login_page"))

    pid = (request.form.get("id") or "").strip()
    settings = load_mcp_settings()
    providers = settings.get("providers") if isinstance(settings.get("providers"), list) else []
    providers = [p for p in providers if not (isinstance(p, dict) and p.get("id") == pid)]
    settings["providers"] = providers
    if settings.get("default_provider") == pid:
        settings["default_provider"] = None
    save_mcp_settings(settings)
    return redirect(url_for("admin.mcp_panel"))


@admin_bp.post("/mcp/default")
def mcp_default_set() -> Response:
    if not session.get("mjytdlp_admin"):
        return redirect(url_for("admin.login_page"))

    pid = (request.form.get("default_provider") or "").strip()
    settings = load_mcp_settings()
    providers = settings.get("providers") if isinstance(settings.get("providers"), list) else []
    if pid and any(isinstance(p, dict) and p.get("id") == pid for p in providers):
        settings["default_provider"] = pid
    else:
        settings["default_provider"] = None
    save_mcp_settings(settings)
    return redirect(url_for("admin.mcp_panel"))


@admin_bp.get("/api/mcp/settings")
def api_get_mcp_settings() -> Response:
    _require_api_auth()
    return jsonify(load_mcp_settings())


@admin_bp.post("/api/mcp/settings")
def api_set_mcp_settings() -> Response:
    _require_api_auth()
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        abort(400)
    merged = {**DEFAULT_MCP_SETTINGS, **payload}
    if not save_mcp_settings(merged):
        abort(500)
    return jsonify({"ok": True, "settings": load_mcp_settings()})


@admin_bp.post("/yt-dlp/cookies")
def cookies_upload() -> Response:
    if not session.get("mjytdlp_admin"):
        return redirect(url_for("admin.login_page"))

    file = request.files.get("cookies_file")
    if file is None or not file.filename:
        return redirect(url_for("admin.panel", cookies="empty"))

    target = _cookies_path()
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        file.save(target)
        try:
            os.chmod(target, 0o600)
        except Exception:
            pass
        return redirect(url_for("admin.panel", cookies="uploaded"))
    except Exception:
        return redirect(url_for("admin.panel", cookies="upload_failed"))


@admin_bp.post("/yt-dlp/cookies/delete")
def cookies_delete() -> Response:
    if not session.get("mjytdlp_admin"):
        return redirect(url_for("admin.login_page"))

    target = _cookies_path()
    try:
        os.remove(target)
        return redirect(url_for("admin.panel", cookies="deleted"))
    except FileNotFoundError:
        return redirect(url_for("admin.panel", cookies="missing"))
    except Exception:
        return redirect(url_for("admin.panel", cookies="delete_failed"))
