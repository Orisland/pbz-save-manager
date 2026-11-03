"""写入前先留下一份完整副本，再用临时文件替换目标。"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from pbz_save_manager.paths import safety_dir


def backup_file(source: Path, reason: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_reason = "".join(char if char.isalnum() or char in "-_" else "_" for char in reason)
    destination = safety_dir() / f"{stamp}-{safe_reason}.sav"
    data = source.read_bytes()
    _write_bytes(destination, data)
    return destination


def atomic_write(destination: Path, data: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".writing")
    _write_bytes(temporary, data)
    os.replace(temporary, destination)


def _write_bytes(path: Path, data: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
