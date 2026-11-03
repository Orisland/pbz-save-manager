"""读取游戏自带的实时、小时、每日备份，并复制一份到管理器目录。"""

from __future__ import annotations

import hashlib
import json
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pbz_save_manager.container import SaveFile, parse_save
from pbz_save_manager.errors import FormatError
from pbz_save_manager.paths import mirror_dir
from pbz_save_manager.summary import is_usable_save

GAME_FOLDERS = {
    "realtime": "1RealTime",
    "hourly": "2Hourly",
    "daily": "3Daily",
}
KIND_LABELS = {
    "realtime": "真实时间",
    "hourly": "按小时",
    "daily": "按天",
}
_STAMP = re.compile(r"^(\d{8})-(\d{6})-(\d+)")


@dataclass(frozen=True)
class BackupEntry:
    kind: str
    origin: str
    when: datetime
    path: Path
    size: int
    save: SaveFile | None
    note: str

    @property
    def usable(self) -> bool:
        return self.save is not None and is_usable_save(self.save) and self.save.is_tightly_packed()

    @property
    def label(self) -> str:
        prefix = KIND_LABELS.get(self.kind, self.kind)
        if self.origin == "mirror":
            prefix = f"留存·{prefix}"
        return f"{prefix}  {self.when.strftime('%Y-%m-%d %H:%M:%S')}"


def list_game_backups(profile_dir: Path, kind: str) -> list[BackupEntry]:
    folder_name = GAME_FOLDERS[kind]
    return _list_folder(profile_dir / folder_name, kind, "game")


def list_mirror_backups(root: Path | None = None) -> list[BackupEntry]:
    base = _mirror_root(root)
    entries: list[BackupEntry] = []
    for kind in GAME_FOLDERS:
        entries.extend(_list_folder(base / kind, kind, "mirror"))
    entries.sort(key=lambda item: item.when, reverse=True)
    return entries


def read_backup(path: Path) -> bytes:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".sav")]
            if len(names) != 1:
                raise FormatError("压缩备份里没有唯一的存档文件")
            return archive.read(names[0])
    return path.read_bytes()


def sync_mirror(profile_dir: Path, root: Path | None = None) -> int:
    """把游戏目录里新出现的备份复制到管理器目录。不写回游戏目录。"""
    base = _mirror_root(root)
    copied = 0
    index = _load_index(base)
    known = {item["sha256"] for item in index if isinstance(item, dict) and "sha256" in item}
    for kind, folder_name in GAME_FOLDERS.items():
        source_dir = profile_dir / folder_name
        if not source_dir.is_dir():
            continue
        for source in source_dir.iterdir():
            if not _is_backup_file(source):
                continue
            if source.suffix.lower() == ".zip" and source.with_suffix(".sav").is_file():
                continue
            try:
                data = read_backup(source)
            except (OSError, zipfile.BadZipFile, FormatError):
                continue
            digest = hashlib.sha256(data).hexdigest()
            if digest in known:
                continue
            target_dir = base / kind
            target_dir.mkdir(parents=True, exist_ok=True)
            target = _unique_target(target_dir, _stored_name(source), digest)
            if not _stable_snapshot(source, data):
                continue
            target.write_bytes(data)
            index.append(
                {
                    "sha256": digest,
                    "kind": kind,
                    "source": str(source),
                    "stored": str(target),
                    "size": len(data),
                }
            )
            known.add(digest)
            copied += 1
    _store_index(base, index)
    return copied


def _list_folder(folder: Path, kind: str, origin: str) -> list[BackupEntry]:
    if not folder.is_dir():
        return []
    entries: list[BackupEntry] = []
    seen_sav = {path.stem for path in folder.glob("*.sav")}
    for path in folder.iterdir():
        if not _is_backup_file(path):
            continue
        if path.suffix.lower() == ".zip" and path.stem in seen_sav:
            continue
        entries.append(_make_entry(path, kind, origin))
    entries.sort(key=lambda item: item.when, reverse=True)
    return entries


def _make_entry(path: Path, kind: str, origin: str) -> BackupEntry:
    when = _parse_when(path) or datetime.fromtimestamp(path.stat().st_mtime)
    try:
        data = read_backup(path)
        save = parse_save(data)
        if not is_usable_save(save):
            note = "没有角色进度槽"
        elif not save.is_tightly_packed():
            note = "可以整份回档，不能改槽"
        else:
            note = ""
    except (OSError, zipfile.BadZipFile, FormatError) as exc:
        save = None
        note = str(exc)
        data = b""
    return BackupEntry(kind, origin, when, path, path.stat().st_size if path.suffix.lower() != ".zip" else len(data), save, note)


def _parse_when(path: Path) -> datetime | None:
    match = _STAMP.match(path.stem)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _is_backup_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in {".sav", ".zip"}


def folder_signature(folder: Path) -> tuple[tuple[str, int, int], ...]:
    """备份文件的名字、大小和修改时间。只看目录，不解析存档内容。"""
    if not folder.is_dir():
        return ()
    try:
        children = list(folder.iterdir())
    except OSError:
        return ()
    sav_stems = {path.stem for path in children if path.is_file() and path.suffix.lower() == ".sav"}
    found: list[tuple[str, int, int]] = []
    for path in children:
        if not _is_backup_file(path):
            continue
        if path.suffix.lower() == ".zip" and path.stem in sav_stems:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        found.append((path.name, int(stat.st_size), int(stat.st_mtime_ns)))
    found.sort()
    return tuple(found)


def new_backup_names(previous: tuple | None, current: tuple) -> tuple[str, ...]:
    """比较两次目录快照里的文件名。同一份文件仍在写入时，大小变化不算新备份。"""
    arrived = _signature_names(current) - _signature_names(previous)
    return tuple(sorted(arrived))


def _signature_names(signature: tuple | None) -> set[str]:
    if not signature:
        return set()
    found: set[str] = set()
    for item in signature:
        origin, kind, files = item
        for record in files:
            found.add(f"{origin}/{kind}/{record[0]}")
    return found


def _stable_snapshot(source: Path, first: bytes) -> bool:
    try:
        age = time.time() - source.stat().st_mtime
    except OSError:
        return False
    if age > 2:
        return True
    time.sleep(0.3)
    try:
        second = read_backup(source)
    except (OSError, zipfile.BadZipFile, FormatError):
        return False
    return first == second


def _stored_name(source: Path) -> str:
    if source.suffix.lower() == ".zip":
        return source.stem + ".sav"
    return source.name


def _unique_target(folder: Path, name: str, digest: str) -> Path:
    candidate = folder / name
    if not candidate.exists():
        return candidate
    return folder / f"{digest[:8]}-{name}"


def _mirror_root(root: Path | None) -> Path:
    if root is None:
        return mirror_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _index_path(root: Path) -> Path:
    return root / "index.json"


def _load_index(root: Path) -> list[dict]:
    path = _index_path(root)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _store_index(root: Path, index: list[dict]) -> None:
    _index_path(root).write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

