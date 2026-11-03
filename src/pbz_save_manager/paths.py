"""管理器自己的数据目录，与游戏存档目录分开。"""

from __future__ import annotations

import os
from pathlib import Path


def manager_root() -> Path:
    root = Path(os.environ["LOCALAPPDATA"]) / "PBZSaveManager"
    root.mkdir(parents=True, exist_ok=True)
    return root


def safety_dir() -> Path:
    path = manager_root() / "safety"
    path.mkdir(parents=True, exist_ok=True)
    return path


def mirror_dir() -> Path:
    path = manager_root() / "mirror"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_path() -> Path:
    return manager_root() / "manager.log"


def config_path() -> Path:
    return manager_root() / "config.json"
