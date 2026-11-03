"""从 Steam 库和游戏目录定位影之刃零与它的存档。"""

from __future__ import annotations

import re
import winreg
from dataclasses import dataclass
from pathlib import Path

from pbz_save_manager.settings import load_save_override

_APP_ID = "4115450"
_SAVE_NAME = "PBZSaveGame.sav"


@dataclass(frozen=True)
class Installation:
    game_dir: Path | None
    exe_path: Path | None
    save_file: Path | None
    profile_dir: Path | None
    profiles: tuple[Path, ...]
    source: str


def locate(override: Path | None = None) -> Installation:
    chosen = override if override is not None else load_save_override()
    if chosen is not None and chosen.is_file():
        game_dir = infer_game_dir(chosen)
        exe_path = find_exe(game_dir) if game_dir else None
        profile = chosen.parent if chosen.name == _SAVE_NAME else None
        return Installation(game_dir, exe_path, chosen, profile, _profiles_of(game_dir), "手动指定")

    game_dir = find_game_dir()
    exe_path = find_exe(game_dir) if game_dir else None
    profiles = _profiles_of(game_dir)
    save_file = _newest_save(profiles)
    profile = save_file.parent if save_file else None
    return Installation(
        game_dir,
        exe_path,
        save_file,
        profile,
        profiles,
        "Steam 库" if game_dir else "未找到",
    )


def infer_game_dir(save_file: Path) -> Path | None:
    for parent in save_file.parents:
        if parent.name == "ProjectPBZ":
            return parent.parent
        if find_exe(parent) is not None:
            return parent
    return None


def find_exe(game_dir: Path | None) -> Path | None:
    if game_dir is None:
        return None
    for relative in (
        Path("platform") / "steam" / "PBZ.exe",
        Path("steam") / "PBZ.exe",
        Path("PBZ.exe"),
    ):
        candidate = game_dir / relative
        if candidate.is_file():
            return candidate
    return None


def find_game_dir() -> Path | None:
    found: list[Path] = []
    for library in steam_libraries():
        steamapps = library / "steamapps"
        if not steamapps.is_dir():
            continue
        for manifest in steamapps.glob("appmanifest_*.acf"):
            install_dir = _matching_install_dir(manifest)
            if install_dir is None:
                continue
            candidate = steamapps / "common" / install_dir
            if _looks_like_pbz(candidate):
                found.append(candidate)
    if not found:
        return None
    found.sort(key=lambda path: (_newest_save(_profiles_of(path)) is None, str(path)))
    return found[0]


def steam_libraries() -> list[Path]:
    libraries: list[Path] = []
    for steam in _steam_roots():
        libraries.append(steam)
        manifest = steam / "steamapps" / "libraryfolders.vdf"
        if not manifest.is_file():
            continue
        text = manifest.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r'"path"\s+"([^"]+)"', text):
            libraries.append(Path(match.group(1).replace("\\\\", "\\")))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in libraries:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _steam_roots() -> list[Path]:
    roots: list[Path] = []
    keys = (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    )
    for hive, name, value_name in keys:
        try:
            with winreg.OpenKey(hive, name) as key:
                raw, _ = winreg.QueryValueEx(key, value_name)
        except OSError:
            continue
        if isinstance(raw, str) and raw:
            roots.append(Path(raw))
    return roots


def _matching_install_dir(manifest: Path) -> str | None:
    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fields = dict(re.findall(r'"([^"]+)"\s+"([^"]*)"', text))
    install_dir = fields.get("installdir", "")
    name = fields.get("name", "")
    app_id = fields.get("appid", "")
    if install_dir == "PBZ" or app_id == _APP_ID or "Phantom Blade Zero" in name or "影之刃零" in name:
        return install_dir or "PBZ"
    return None


def _looks_like_pbz(folder: Path) -> bool:
    return (folder / "ProjectPBZ").is_dir() and find_exe(folder) is not None


def _profiles_of(game_dir: Path | None) -> tuple[Path, ...]:
    if game_dir is None:
        return ()
    root = game_dir / "ProjectPBZ" / "Saved" / "SaveGames" / "Steam"
    if not root.is_dir():
        return ()
    profiles = [path for path in root.iterdir() if path.is_dir() and (path / _SAVE_NAME).is_file()]
    profiles.sort(key=lambda path: (path / _SAVE_NAME).stat().st_mtime, reverse=True)
    return tuple(profiles)


def _newest_save(profiles: tuple[Path, ...]) -> Path | None:
    if not profiles:
        return None
    return profiles[0] / _SAVE_NAME
