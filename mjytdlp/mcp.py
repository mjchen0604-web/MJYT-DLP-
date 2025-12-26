from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional

from flask import Blueprint, Response, jsonify, request

from .asr_tools import AsrError, transcribe
from .mcp_settings import load_mcp_settings
from .mcp_translate import ProviderError, translate_text
from .yt_dlp_tools import YtDlpError, download_subs, formats, list_subs, probe, yt_dlp_version


mcp_bp = Blueprint("mcp", __name__, url_prefix="/mcp")

_SESSIONS: Dict[str, "_SseSession"] = {}
_SESSIONS_LOCK = threading.Lock()
_SESSION_TTL = 60 * 60


class _SseSession:
    def __init__(self, session_id: str) -> None:
        self.id = session_id
        self.queue: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        self.created = time.time()


def _cleanup_sessions() -> None:
    now = time.time()
    stale = []
    with _SESSIONS_LOCK:
        for sid, session in _SESSIONS.items():
            if now - session.created > _SESSION_TTL:
                stale.append(sid)
        for sid in stale:
            _SESSIONS.pop(sid, None)


def _register_session() -> _SseSession:
    session_id = uuid.uuid4().hex
    session = _SseSession(session_id)
    with _SESSIONS_LOCK:
        _SESSIONS[session_id] = session
    _cleanup_sessions()
    return session


def _get_session(session_id: str) -> Optional[_SseSession]:
    with _SESSIONS_LOCK:
        return _SESSIONS.get(session_id)


def _remove_session(session_id: str) -> None:
    with _SESSIONS_LOCK:
        _SESSIONS.pop(session_id, None)


def _jsonrpc_error(code: int, message: str, req_id: Any = None) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _tool_schemas() -> List[Dict[str, Any]]:
    options_schema = {
        "type": "object",
        "properties": {
            "cookies_path": {"type": "string", "description": "cookies.txt 路径（可选）"},
            "cookies_name": {"type": "string", "description": "命名 cookies（可选，如 youtube/douyin/bilibili）"},
            "proxy": {"type": "string", "description": "代理地址（可选）"},
            "user_agent": {"type": "string", "description": "自定义 User-Agent（可选）"},
            "referer": {"type": "string", "description": "自定义 Referer（可选）"},
            "timeout": {"type": "number", "description": "超时秒数（可选）"},
        },
    }

    return [
        {
            "name": "translate",
            "description": "文本翻译（使用已配置的 Provider）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要翻译的文本"},
                    "target": {"type": "string", "description": "目标语言，如 zh/en"},
                    "source": {"type": "string", "description": "源语言（可选）"},
                    "provider": {"type": "string", "description": "Provider id（可选）"},
                    "model": {"type": "string", "description": "覆盖模型名（可选）"},
                    "temperature": {"type": "number", "description": "采样温度（可选）"},
                },
                "required": ["text", "target"],
            },
        },
        {
            "name": "list_providers",
            "description": "列出已配置的翻译 Provider（不含密钥）。",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "probe",
            "description": "获取视频元数据（不下载文件）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "视频链接"},
                    "full": {"type": "boolean", "description": "返回更完整字段"},
                    "options": options_schema,
                },
                "required": ["url"],
            },
        },
        {
            "name": "formats",
            "description": "列出可用格式与下载直链（不下载文件）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "视频链接"},
                    "limit": {"type": "integer", "description": "最多返回的格式数量"},
                    "options": options_schema,
                },
                "required": ["url"],
            },
        },
        {
            "name": "list_subs",
            "description": "列出字幕轨道（含下载直链）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "视频链接"},
                    "langs": {"type": "array", "items": {"type": "string"}, "description": "仅返回这些语言"},
                    "include_auto": {"type": "boolean", "description": "包含自动字幕"},
                    "include_manual": {"type": "boolean", "description": "包含人工字幕"},
                    "options": options_schema,
                },
                "required": ["url"],
            },
        },
        {
            "name": "download_subs",
            "description": "返回字幕文本或字幕直链（不落盘）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "视频链接"},
                    "lang": {"type": "string", "description": "语言，如 zh/en"},
                    "format": {"type": "string", "description": "字幕格式，如 vtt/srt"},
                    "auto": {"type": "boolean", "description": "是否使用自动字幕"},
                    "link_only": {"type": "boolean", "description": "仅返回字幕下载链接"},
                    "options": options_schema,
                },
                "required": ["url", "lang"],
            },
        },
        {
            "name": "transcribe",
            "description": "转写音频为字幕/文本（通过外部 ASR 服务）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "视频链接"},
                    "output": {"type": "string", "description": "输出格式：srt/vtt/txt/json"},
                    "language": {"type": "string", "description": "语言代码（可选）"},
                    "task": {"type": "string", "description": "transcribe/translate"},
                    "initial_prompt": {"type": "string", "description": "提示词（可选）"},
                    "encode": {"type": "boolean", "description": "是否先转码（可选）"},
                    "timeout": {"type": "integer", "description": "超时秒数（可选）"},
                    "max_mb": {"type": "integer", "description": "最大下载体积（MB，可选）"},
                    "options": options_schema,
                },
                "required": ["url"],
            },
        },
        {
            "name": "version",
            "description": "获取 yt-dlp 版本。",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


def _safe_provider_list() -> Dict[str, Any]:
    settings = load_mcp_settings()
    providers = settings.get("providers") if isinstance(settings.get("providers"), list) else []
    default_provider = settings.get("default_provider")
    safe: List[Dict[str, Any]] = []
    for p in providers:
        if not isinstance(p, dict):
            continue
        safe.append(
            {
                "id": p.get("id"),
                "label": p.get("label"),
                "model": p.get("model"),
                "base_url": p.get("base_url"),
                "endpoint_url": p.get("endpoint_url"),
                "enabled": p.get("enabled", True),
                "is_default": p.get("id") == default_provider,
            }
        )
    return {"default_provider": default_provider, "providers": safe}


def _handle_initialize(params: Any) -> Dict[str, Any]:
    protocol = "2024-11-05"
    if isinstance(params, dict):
        req_proto = params.get("protocolVersion")
        if isinstance(req_proto, str) and req_proto.strip():
            protocol = req_proto.strip()
    return {
        "protocolVersion": protocol,
        "serverInfo": {"name": "MJYT-DLP", "version": "0.2.0"},
        "capabilities": {"tools": {"list": True}},
    }


def _get_options(args: Dict[str, Any]) -> Dict[str, Any]:
    raw = args.get("options")
    return raw if isinstance(raw, dict) else {}


def _json_content(payload: Any) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}


def _handle_tools_call(params: Any) -> Dict[str, Any]:
    if not isinstance(params, dict):
        raise ProviderError("无效的 tools/call 参数。")
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise ProviderError("缺少工具名称。")
    args = params.get("arguments")
    if not isinstance(args, dict):
        args = {}

    if name == "translate":
        result = translate_text(
            text=args.get("text") or "",
            target=args.get("target") or "",
            source=args.get("source") if isinstance(args.get("source"), str) else None,
            provider_id=args.get("provider") if isinstance(args.get("provider"), str) else None,
            model_override=args.get("model") if isinstance(args.get("model"), str) else None,
            temperature=args.get("temperature") if isinstance(args.get("temperature"), (int, float)) else None,
        )
        return {"content": [{"type": "text", "text": result.get("text") or ""}]}

    if name == "list_providers":
        return _json_content(_safe_provider_list())

    if name == "probe":
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ProviderError("缺少 url。")
        full = bool(args.get("full"))
        result = probe(url.strip(), _get_options(args), full=full)
        return _json_content(result)

    if name == "formats":
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ProviderError("缺少 url。")
        limit = args.get("limit")
        limit_val = int(limit) if isinstance(limit, int) and limit > 0 else None
        result = formats(url.strip(), _get_options(args), limit=limit_val)
        return _json_content(result)

    if name == "list_subs":
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ProviderError("缺少 url。")
        langs = args.get("langs") if isinstance(args.get("langs"), list) else None
        include_auto = True if args.get("include_auto") is None else bool(args.get("include_auto"))
        include_manual = True if args.get("include_manual") is None else bool(args.get("include_manual"))
        result = list_subs(
            url.strip(),
            _get_options(args),
            include_auto=include_auto,
            include_manual=include_manual,
            langs=langs,
        )
        return _json_content(result)

    if name == "download_subs":
        url = args.get("url")
        lang = args.get("lang")
        if not isinstance(url, str) or not url.strip():
            raise ProviderError("缺少 url。")
        if not isinstance(lang, str) or not lang.strip():
            raise ProviderError("缺少 lang。")
        fmt = args.get("format") if isinstance(args.get("format"), str) else None
        auto = args.get("auto") if isinstance(args.get("auto"), bool) else None
        link_only = bool(args.get("link_only"))
        result = download_subs(
            url.strip(),
            lang.strip(),
            _get_options(args),
            fmt=fmt,
            auto=auto,
            link_only=link_only,
        )
        return _json_content(result)

    if name == "transcribe":
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ProviderError("缺少 url。")
        output = args.get("output") if isinstance(args.get("output"), str) else "srt"
        language = args.get("language") if isinstance(args.get("language"), str) else None
        task = args.get("task") if isinstance(args.get("task"), str) else "transcribe"
        initial_prompt = args.get("initial_prompt") if isinstance(args.get("initial_prompt"), str) else None
        encode = True if args.get("encode") is None else bool(args.get("encode"))
        timeout = args.get("timeout") if isinstance(args.get("timeout"), int) else None
        max_mb = args.get("max_mb") if isinstance(args.get("max_mb"), int) else None
        result = transcribe(
            url.strip(),
            _get_options(args),
            output=output,
            language=language,
            task=task,
            initial_prompt=initial_prompt,
            encode=encode,
            timeout=timeout,
            max_mb=max_mb,
        )
        return _json_content(result)

    if name == "version":
        return _json_content(yt_dlp_version())

    raise ProviderError(f"未知工具: {name}")


def _handle_rpc_message(message: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(message, dict):
        return _jsonrpc_error(-32600, "Invalid Request", None)
    if message.get("jsonrpc") != "2.0":
        return _jsonrpc_error(-32600, "Invalid Request", message.get("id"))

    req_id = message.get("id")
    method = message.get("method")
    params = message.get("params")

    if not isinstance(method, str):
        return _jsonrpc_error(-32600, "Invalid Request", req_id)

    if method.startswith("notifications/"):
        return None

    try:
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": req_id, "result": _handle_initialize(params)}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": _tool_schemas()}}
        if method == "tools/call":
            result = _handle_tools_call(params)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        if method == "resources/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": []}}
        if method == "prompts/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"prompts": []}}
        if method == "prompts/get":
            return _jsonrpc_error(-32601, "Prompt not found", req_id)
        return _jsonrpc_error(-32601, "Method not found", req_id)
    except (ProviderError, YtDlpError, AsrError) as exc:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True},
        }
    except Exception:
        return _jsonrpc_error(-32000, "Server error", req_id)


def _handle_rpc_payload(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        responses: List[Dict[str, Any]] = []
        for item in payload:
            resp = _handle_rpc_message(item)
            if resp is not None:
                responses.append(resp)
        return responses
    resp = _handle_rpc_message(payload)
    return [resp] if resp is not None else []


@mcp_bp.route("", methods=["GET"], strict_slashes=False)
@mcp_bp.route("/", methods=["GET"], strict_slashes=False)
def info() -> Response:
    return jsonify(
        {
            "name": "MJYT-DLP",
            "sse": "/mcp/sse",
            "streamable_http": "/mcp",
        }
    )


@mcp_bp.route("/sse", methods=["GET"], strict_slashes=False)
def sse() -> Response:
    session = _register_session()

    def _stream() -> Iterable[str]:
        try:
            endpoint = f"/mcp/messages/{session.id}"
            yield f"event: endpoint\ndata: {endpoint}\n\n"
            while True:
                try:
                    item = session.queue.get(timeout=15)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    break
                payload = json.dumps(item, ensure_ascii=False)
                yield f"event: message\ndata: {payload}\n\n"
        finally:
            _remove_session(session.id)

    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
    return Response(_stream(), mimetype="text/event-stream", headers=headers)


@mcp_bp.route("/messages/<session_id>", methods=["POST"], strict_slashes=False)
def sse_messages(session_id: str) -> Response:
    session = _get_session(session_id)
    if session is None:
        return jsonify({"error": "session not found"}), 404

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify(_jsonrpc_error(-32700, "Parse error", None)), 400

    responses = _handle_rpc_payload(payload)
    for resp in responses:
        session.queue.put(resp)

    return jsonify({"ok": True})


@mcp_bp.route("", methods=["POST"], strict_slashes=False)
@mcp_bp.route("/", methods=["POST"], strict_slashes=False)
def streamable_http() -> Response:
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify(_jsonrpc_error(-32700, "Parse error", None)), 400

    responses = _handle_rpc_payload(payload)
    wants_stream = "text/event-stream" in (request.headers.get("Accept") or "")

    if wants_stream:
        def _stream() -> Iterable[str]:
            for resp in responses:
                payload_text = json.dumps(resp, ensure_ascii=False)
                yield f"data: {payload_text}\n\n"
        headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
        return Response(_stream(), mimetype="text/event-stream", headers=headers)

    if not responses:
        return Response(status=204)
    if isinstance(payload, list):
        return jsonify(responses)
    return jsonify(responses[0])
