"""启动存档管理器。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from pbz_save_manager.codec import load_slot_json
from pbz_save_manager.container import parse_save
from pbz_save_manager.locate import locate
from pbz_save_manager.oodle_host import get_host
from pbz_save_manager.paths import log_path


def main() -> None:
    try:
        _dispatch()
    except Exception as exc:
        if "--check" in sys.argv or "--check-decode" in sys.argv:
            _write_check(f"{type(exc).__name__}: {exc}")
            raise SystemExit(1) from exc
        raise


def _dispatch() -> None:
    if "--check" in sys.argv:
        _write_check(locate_text())
        return
    if "--check-decode" in sys.argv:
        index = sys.argv.index("--check-decode")
        if index + 1 >= len(sys.argv):
            _write_check("缺少存档路径")
            return
        _write_check(decode_text(Path(sys.argv[index + 1])))
        return
    _configure_logging()
    _enable_dpi()
    from pbz_save_manager.app import SaveManagerApp

    SaveManagerApp().mainloop()


def locate_text() -> str:
    found = locate()
    return "\n".join(
        [
            f"source={found.source}",
            f"game={found.game_dir or ''}",
            f"exe={found.exe_path or ''}",
            f"save={found.save_file or ''}",
        ]
    )


def decode_text(path: Path) -> str:
    found = locate()
    if found.exe_path is None:
        return "没有找到游戏程序"
    save = parse_save(path.read_bytes())
    _raw, payload = load_slot_json(save, "__Achievements__", get_host(found.exe_path))
    unlocked = payload.get("UnlockedIds")
    count = len(unlocked) if isinstance(unlocked, list) else 0
    return f"ok slots={len(save.slots)} achievements={count}"


def _write_check(text: str) -> None:
    from pbz_save_manager.paths import manager_root

    destination = manager_root() / "last-check.txt"
    destination.write_text(text, encoding="utf-8")
    print(text, flush=True)


def _configure_logging() -> None:
    logging.basicConfig(
        filename=log_path(),
        level=logging.INFO,
        encoding="utf-8",
        format="%(asctime)s %(levelname)s %(message)s",
    )


def _enable_dpi() -> None:
    from pbz_save_manager.app import enable_process_dpi

    enable_process_dpi()


if __name__ == "__main__":
    main()
