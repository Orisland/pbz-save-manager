"""读写管理器配置。配置只记录用户手动指定的存档路径。"""

from __future__ import annotations

import json
from pathlib import Path

from pbz_save_manager.paths import config_path


def load_save_override() -> Path | None:
    path = config_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = data.get("save_file") if isinstance(data, dict) else None
    if not isinstance(raw, str) or not raw:
        return None
    return Path(raw)


def store_save_override(save_file: Path | None) -> None:
    path = config_path()
    if save_file is None:
        if path.exists():
            path.unlink()
        return
    path.write_text(
        json.dumps({"save_file": str(save_file)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
