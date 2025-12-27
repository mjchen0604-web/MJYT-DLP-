from __future__ import annotations

import os
import tempfile
import uuid
from typing import Any, Dict, Optional

import requests
import yt_dlp

from .utils import get_data_dir
from .yt_dlp_tools import YtDlpError, _build_ydl_opts, audio_stream


DEFAULT_ASR_TIMEOUT = 600


class AsrError(Exception):
    pass


def _asr_config() -> tuple[str, Dict[str, str]]:
    base = (os.getenv("MJYTDLP_ASR_URL") or "").strip()
    if not base:
        raise AsrError("ASR 未配置，请设置 MJYTDLP_ASR_URL。")
    base = base.rstrip("/")

    headers: Dict[str, str] = {}
    api_key = (os.getenv("MJYTDLP_ASR_API_KEY") or "").strip()
    if api_key:
        header = (os.getenv("MJYTDLP_ASR_AUTH_HEADER") or "Authorization").strip() or "Authorization"
        prefix = os.getenv("MJYTDLP_ASR_AUTH_PREFIX") or "Bearer "
        headers[header] = f"{prefix}{api_key}" if prefix else api_key

    return base, headers


def _download_audio(
    url: str,
    headers: Dict[str, str],
    timeout: int,
    max_mb: Optional[int],
    suffix: str,
) -> str:
    tmp_dir = os.path.join(get_data_dir(), "tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    resp = requests.get(url, headers=headers, stream=True, timeout=timeout)
    try:
        resp.raise_for_status()
        if max_mb is not None:
            max_bytes = max_mb * 1024 * 1024
            content_len = resp.headers.get("Content-Length")
            if content_len and content_len.isdigit() and int(content_len) > max_bytes:
                raise AsrError(f"音频大小超过限制（>{max_mb}MB）。")

        with tempfile.NamedTemporaryFile(delete=False, dir=tmp_dir, suffix=suffix) as fp:
            total = 0
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                fp.write(chunk)
                total += len(chunk)
                if max_mb is not None and total > max_bytes:
                    raise AsrError(f"音频大小超过限制（>{max_mb}MB）。")
            return fp.name
    finally:
        resp.close()


def _is_hls_audio(download_url: str, protocol: Optional[str]) -> bool:
    proto = str(protocol or "").lower()
    url = download_url.lower()
    if "m3u8" in proto:
        return True
    if ".m3u8" in url:
        return True
    return False


def _download_audio_via_ytdlp(
    video_url: str,
    options: Dict[str, Any],
    format_id: Optional[str],
    timeout: int,
    max_mb: Optional[int],
) -> str:
    tmp_dir = os.path.join(get_data_dir(), "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    base = os.path.join(tmp_dir, f"asr_{uuid.uuid4().hex}")
    outtmpl = f"{base}.%(ext)s"

    ydl_opts = _build_ydl_opts(options)
    ydl_opts.update(
        {
            "skip_download": False,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "outtmpl": outtmpl,
            "format": format_id or "bestaudio/best",
            "socket_timeout": int(timeout),
        }
    )

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
    except Exception as exc:
        raise AsrError(f"下载音频失败：{exc}") from exc

    base_name = os.path.basename(base)
    candidates = [
        os.path.join(tmp_dir, name)
        for name in os.listdir(tmp_dir)
        if name.startswith(base_name)
    ]
    if not candidates:
        raise AsrError("未找到下载的音频文件。")

    tmp_path = max(candidates, key=lambda path: os.path.getmtime(path))
    if not os.path.isfile(tmp_path):
        raise AsrError("未找到下载的音频文件。")

    if max_mb is not None:
        size = os.path.getsize(tmp_path)
        if size > max_mb * 1024 * 1024:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            raise AsrError(f"音频大小超过限制（>{max_mb}MB）。")

    return tmp_path


def transcribe(
    url: str,
    options: Dict[str, Any],
    output: str = "srt",
    language: Optional[str] = None,
    task: str = "transcribe",
    initial_prompt: Optional[str] = None,
    encode: bool = True,
    timeout: Optional[int] = None,
    max_mb: Optional[int] = None,
) -> Dict[str, Any]:
    try:
        audio = audio_stream(url, options)
    except YtDlpError as exc:
        raise AsrError(f"获取音频失败：{exc}") from exc

    download_url = audio.get("download_url")
    if not isinstance(download_url, str) or not download_url:
        raise AsrError("未获取到音频直链。")

    ext = audio.get("ext") if isinstance(audio.get("ext"), str) else ""
    suffix = f".{ext}" if ext else ".audio"
    timeout_val = int(timeout) if isinstance(timeout, int) and timeout > 0 else DEFAULT_ASR_TIMEOUT

    protocol = audio.get("protocol") if isinstance(audio.get("protocol"), str) else None
    if _is_hls_audio(download_url, protocol):
        tmp_path = _download_audio_via_ytdlp(
            url,
            options,
            audio.get("format_id") if isinstance(audio.get("format_id"), str) else None,
            timeout_val,
            max_mb,
        )
    else:
        tmp_path = _download_audio(
            download_url,
            audio.get("http_headers") if isinstance(audio.get("http_headers"), dict) else {},
            timeout_val,
            max_mb,
            suffix,
        )

    asr_base, asr_headers = _asr_config()
    params: Dict[str, Any] = {
        "output": output,
        "task": task,
        "encode": bool(encode),
    }
    if language:
        params["language"] = language
    if initial_prompt:
        params["initial_prompt"] = initial_prompt

    try:
        with open(tmp_path, "rb") as fp:
            files = {"audio_file": (os.path.basename(tmp_path), fp, "application/octet-stream")}
            resp = requests.post(
                f"{asr_base}/asr",
                params=params,
                files=files,
                headers=asr_headers,
                timeout=timeout_val,
            )
        resp.raise_for_status()
        return {
            "output": output,
            "content": resp.text,
        }
    except requests.RequestException as exc:
        raise AsrError(f"ASR 请求失败：{exc}") from exc
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
