from __future__ import annotations

import json
import os
import tempfile
import sys
from typing import Any, Dict, Optional


def eprint(*args, **kwargs) -> None:
    print(*args, file=sys.stderr, **kwargs)


def get_data_dir() -> str:
    home = os.getenv("MJYTDLP_HOME") or os.getenv("MJYTDLP_DATA_DIR")
    if not home:
        home = os.path.expanduser("~/.mjyt-dlp")
    return home


def read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def write_json_atomic(path: str, data: Dict[str, Any]) -> bool:
    folder = os.path.dirname(path) or "."
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception as exc:
        eprint(f"ERROR: unable to create data directory {folder}: {exc}")
        return False

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=folder,
            prefix=os.path.basename(path) + ".",
            suffix=".tmp",
            delete=False,
        ) as fp:
            if hasattr(os, "fchmod"):
                os.fchmod(fp.fileno(), 0o600)
            json.dump(data, fp, indent=2, ensure_ascii=False)
            fp.write("\n")
            tmp_path = fp.name
        os.replace(tmp_path, path)
        return True
    except Exception as exc:
        eprint(f"ERROR: unable to write {path}: {exc}")
        try:
            if "tmp_path" in locals() and os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass
        return False
