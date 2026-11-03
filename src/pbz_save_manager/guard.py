"""确认游戏进程没有占用存档。"""

from __future__ import annotations

import subprocess

from pbz_save_manager.errors import GuardError

_IMAGE = "PBZ.exe"


def query_game_running() -> bool | None:
    """返回 True/False。无法确认时返回 None。"""
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {_IMAGE}", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creation,
        )
    except OSError:
        return None
    if completed.returncode != 0 and not completed.stdout:
        return None
    return _IMAGE in completed.stdout


def assert_game_stopped() -> None:
    state = query_game_running()
    if state is None:
        raise GuardError("无法确认游戏是否在运行，已停止写入。")
    if state:
        raise GuardError("游戏正在运行。请先退出到桌面，再回档或修改。")
