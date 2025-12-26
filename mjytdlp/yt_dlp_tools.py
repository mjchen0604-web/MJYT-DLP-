from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests
import yt_dlp

from .utils import get_data_dir

DEFAULT_TIMEOUT = 30


class YtDlpError(Exception):
    pass


def _default_cookies_path() -> str:
    return os.path.join(get_data_dir(), "cookies.txt")


def _named_cookies_path(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", name).strip()
    if not safe:
        return ""
    if not safe.endswith(".txt"):
        safe = f"{safe}.txt"
    return os.path.join(get_data_dir(), "cookies", safe)


def _build_ydl_opts(options: Dict[str, Any]) -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "noplaylist": True,
        "cachedir": False,
    }

    cookies_path = (options.get("cookies_path") or "").strip() if isinstance(options.get("cookies_path"), str) else ""
    cookies_name = (options.get("cookies_name") or "").strip() if isinstance(options.get("cookies_name"), str) else ""
    if not cookies_path:
        if cookies_name:
            named_path = _named_cookies_path(cookies_name)
            if named_path and os.path.isfile(named_path):
                cookies_path = named_path
        if not cookies_path:
            default_path = _default_cookies_path()
            if os.path.isfile(default_path):
                cookies_path = default_path
    if cookies_path:
        opts["cookiefile"] = cookies_path

    proxy = (options.get("proxy") or "").strip() if isinstance(options.get("proxy"), str) else ""
    if proxy:
        opts["proxy"] = proxy

    http_headers: Dict[str, str] = {}
    user_agent = (options.get("user_agent") or "").strip() if isinstance(options.get("user_agent"), str) else ""
    referer = (options.get("referer") or "").strip() if isinstance(options.get("referer"), str) else ""
    if user_agent:
        http_headers["User-Agent"] = user_agent
    if referer:
        http_headers["Referer"] = referer
    if http_headers:
        opts["http_headers"] = http_headers

    timeout = options.get("timeout")
    if isinstance(timeout, (int, float)) and timeout > 0:
        opts["socket_timeout"] = int(timeout)

    return opts


def _extract_info(url: str, options: Dict[str, Any]) -> Dict[str, Any]:
    opts = _build_ydl_opts(options)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise YtDlpError(str(exc)) from exc

    if isinstance(info, dict) and info.get("_type") == "playlist":
        entries = info.get("entries")
        if isinstance(entries, list):
            first = next((e for e in entries if isinstance(e, dict)), None)
            if isinstance(first, dict):
                info = first
    if not isinstance(info, dict):
        raise YtDlpError("Failed to extract info")
    return info


def _safe_headers(info: Dict[str, Any], fmt: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    if isinstance(fmt, dict) and isinstance(fmt.get("http_headers"), dict):
        return {str(k): str(v) for k, v in fmt.get("http_headers", {}).items()}
    if isinstance(info.get("http_headers"), dict):
        return {str(k): str(v) for k, v in info.get("http_headers", {}).items()}
    return {}


def _summarize_info(info: Dict[str, Any], full: bool = False) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "id": info.get("id"),
        "title": info.get("title"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "uploader_id": info.get("uploader_id"),
        "channel": info.get("channel"),
        "channel_id": info.get("channel_id"),
        "upload_date": info.get("upload_date"),
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "comment_count": info.get("comment_count"),
        "webpage_url": info.get("webpage_url"),
        "extractor": info.get("extractor"),
        "thumbnail": info.get("thumbnail"),
        "is_live": info.get("is_live"),
        "live_status": info.get("live_status"),
    }
    if full:
        data["description"] = info.get("description")
        data["tags"] = info.get("tags")
        data["categories"] = info.get("categories")
        data["thumbnails"] = info.get("thumbnails")
    return data


def probe(url: str, options: Dict[str, Any], full: bool = False) -> Dict[str, Any]:
    info = _extract_info(url, options)
    return _summarize_info(info, full=full)


def formats(url: str, options: Dict[str, Any], limit: Optional[int] = None) -> Dict[str, Any]:
    info = _extract_info(url, options)
    raw_formats = info.get("formats") if isinstance(info.get("formats"), list) else []
    items: List[Dict[str, Any]] = []
    for fmt in raw_formats:
        if not isinstance(fmt, dict):
            continue
        item = {
            "format_id": fmt.get("format_id"),
            "ext": fmt.get("ext"),
            "format_note": fmt.get("format_note"),
            "resolution": fmt.get("resolution") or (
                f"{fmt.get('width')}x{fmt.get('height')}" if fmt.get("width") and fmt.get("height") else None
            ),
            "width": fmt.get("width"),
            "height": fmt.get("height"),
            "fps": fmt.get("fps"),
            "vcodec": fmt.get("vcodec"),
            "acodec": fmt.get("acodec"),
            "tbr": fmt.get("tbr"),
            "abr": fmt.get("abr"),
            "filesize": fmt.get("filesize"),
            "filesize_approx": fmt.get("filesize_approx"),
            "protocol": fmt.get("protocol"),
            "download_url": fmt.get("url"),
            "manifest_url": fmt.get("manifest_url"),
            "http_headers": _safe_headers(info, fmt),
        }
        items.append(item)
    if isinstance(limit, int) and limit > 0:
        items = items[:limit]
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "webpage_url": info.get("webpage_url"),
        "http_headers": _safe_headers(info),
        "formats": items,
    }


def _pick_audio_format(info: Dict[str, Any]) -> Dict[str, Any]:
    formats_list = info.get("formats") if isinstance(info.get("formats"), list) else []
    audio_only: List[Dict[str, Any]] = []
    for fmt in formats_list:
        if not isinstance(fmt, dict):
            continue
        if fmt.get("vcodec") == "none" and fmt.get("acodec") not in (None, "none"):
            audio_only.append(fmt)

    def _score(fmt: Dict[str, Any]) -> Tuple[float, float]:
        bitrate = fmt.get("abr") or fmt.get("tbr") or 0
        size = fmt.get("filesize") or fmt.get("filesize_approx") or 0
        return float(bitrate), float(size)

    if audio_only:
        return max(audio_only, key=_score)

    candidates = [f for f in formats_list if isinstance(f, dict) and f.get("url")]
    if not candidates:
        raise YtDlpError("No audio stream found")
    return max(candidates, key=_score)


def audio_stream(url: str, options: Dict[str, Any]) -> Dict[str, Any]:
    info = _extract_info(url, options)
    fmt = _pick_audio_format(info)
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "webpage_url": info.get("webpage_url"),
        "format_id": fmt.get("format_id"),
        "ext": fmt.get("ext"),
        "acodec": fmt.get("acodec"),
        "abr": fmt.get("abr"),
        "filesize": fmt.get("filesize") or fmt.get("filesize_approx"),
        "download_url": fmt.get("url"),
        "http_headers": _safe_headers(info, fmt),
    }


def _collect_subs(source: Any, is_auto: bool, langs: Optional[List[str]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(source, dict):
        return out
    for lang, tracks in source.items():
        if langs and lang not in langs:
            continue
        formats_list: List[Dict[str, Any]] = []
        if isinstance(tracks, list):
            for track in tracks:
                if not isinstance(track, dict):
                    continue
                formats_list.append(
                    {
                        "ext": track.get("ext"),
                        "name": track.get("name"),
                        "download_url": track.get("url"),
                    }
                )
        out.append({"lang": lang, "is_auto": is_auto, "formats": formats_list})
    return out


def list_subs(
    url: str,
    options: Dict[str, Any],
    include_auto: bool = True,
    include_manual: bool = True,
    langs: Optional[List[str]] = None,
) -> Dict[str, Any]:
    info = _extract_info(url, options)
    subtitles: List[Dict[str, Any]] = []
    if include_manual:
        subtitles.extend(_collect_subs(info.get("subtitles"), False, langs))
    if include_auto:
        subtitles.extend(_collect_subs(info.get("automatic_captions"), True, langs))
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "webpage_url": info.get("webpage_url"),
        "http_headers": _safe_headers(info),
        "subtitles": subtitles,
    }


def _pick_subtitle_track(
    info: Dict[str, Any],
    lang: str,
    auto: Optional[bool],
    fmt: Optional[str],
) -> Tuple[str, bool]:
    def _find(subs: Any) -> Optional[List[Dict[str, Any]]]:
        if not isinstance(subs, dict):
            return None
        tracks = subs.get(lang)
        return tracks if isinstance(tracks, list) else None

    prefer_formats = [fmt] if fmt else ["vtt", "srt", "srv3", "srv2", "srv1", "ttml"]

    search_order = []
    if auto is True:
        search_order = [(True, info.get("automatic_captions"))]
    elif auto is False:
        search_order = [(False, info.get("subtitles"))]
    else:
        search_order = [(False, info.get("subtitles")), (True, info.get("automatic_captions"))]

    for is_auto, source in search_order:
        tracks = _find(source)
        if not tracks:
            continue
        for pf in prefer_formats:
            for track in tracks:
                if isinstance(track, dict) and track.get("ext") == pf and track.get("url"):
                    return track.get("url"), is_auto
        first = next((t for t in tracks if isinstance(t, dict) and t.get("url")), None)
        if first:
            return first.get("url"), is_auto

    raise YtDlpError("Subtitle not found")


def download_subs(
    url: str,
    lang: str,
    options: Dict[str, Any],
    fmt: Optional[str] = None,
    auto: Optional[bool] = None,
    link_only: bool = False,
) -> Dict[str, Any]:
    info = _extract_info(url, options)
    subtitle_url, is_auto = _pick_subtitle_track(info, lang, auto, fmt)
    headers = _safe_headers(info)

    if link_only:
        return {
            "lang": lang,
            "format": fmt,
            "is_auto": is_auto,
            "download_url": subtitle_url,
            "http_headers": headers,
        }

    timeout = options.get("timeout")
    timeout_val = int(timeout) if isinstance(timeout, (int, float)) and timeout > 0 else DEFAULT_TIMEOUT
    try:
        resp = requests.get(subtitle_url, headers=headers, timeout=timeout_val)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise YtDlpError(f"Subtitle fetch failed: {exc}") from exc

    return {
        "lang": lang,
        "format": fmt,
        "is_auto": is_auto,
        "content": resp.text,
    }


def yt_dlp_version() -> Dict[str, Any]:
    try:
        version = yt_dlp.version.__version__
    except Exception:
        version = getattr(yt_dlp, "__version__", None)
    return {"version": version}
