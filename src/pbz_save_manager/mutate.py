"""回档和少量字段修改。任何写入都会先备份，校验失败则不碰原文件。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pbz_save_manager.codec import load_slot_json, reseal_slot
from pbz_save_manager.container import SaveFile, parse_save
from pbz_save_manager.errors import EditError
from pbz_save_manager.guard import assert_game_stopped
from pbz_save_manager.json_edit import assert_only_changed, replace_unique_int, replace_unique_string
from pbz_save_manager.oodle_host import OodleHost
from pbz_save_manager.safe_io import atomic_write, backup_file
from pbz_save_manager.summary import is_usable_save, slot_kind, slot_title


@dataclass(frozen=True)
class WriteResult:
    backup_path: Path | None
    message: str


def restore_bytes(
    live: Path,
    source: bytes,
    guard: Callable[[], None] = assert_game_stopped,
) -> WriteResult:
    save = parse_save(source)
    if not is_usable_save(save):
        raise EditError("这份备份没有角色进度，不能回档。请改选真实时间或按小时的备份。")
    if live.is_file() and live.read_bytes() == source:
        return WriteResult(None, "当前存档已经是这一份，没有覆盖。")
    return _commit(live, source, "回档前", "已回档到所选备份。", guard=guard)


def overwrite_slot(
    live: Path,
    source: bytes,
    slot_name: str,
    guard: Callable[[], None] = assert_game_stopped,
) -> WriteResult:
    current_bytes = _read_live(live)
    current = _mutable(current_bytes)
    incoming = parse_save(source)
    source_slot = incoming.slot(slot_name)
    target = current.slot(slot_name)
    if source_slot is None or target is None:
        raise EditError(f"当前存档和所选备份里都要有 {slot_title(slot_name)}。")
    if slot_kind(source_slot.name) != slot_kind(target.name):
        raise EditError("只会用同一类槽覆盖。成就和全局数据不会覆盖角色进度。")
    if source_slot.payload == target.payload:
        return WriteResult(None, "这个槽的内容和当前一样，没有写入。")
    replacement = source_slot.__class__(
        target.name,
        source_slot.algo,
        source_slot.flags,
        source_slot.uncompressed_size,
        0,
        source_slot.payload,
    )
    rebuilt = current.with_slot_payload(slot_name, replacement).to_bytes()
    _ensure_other_slots_unchanged(current, parse_save(rebuilt), slot_name)
    if parse_save(rebuilt).slot(slot_name).payload != source_slot.payload:
        raise EditError("槽位封装校验失败，没有写入。")
    return _commit(live, rebuilt, "改槽前", f"已用备份覆盖 {slot_title(slot_name)}。", current_bytes, guard)


def update_player_numbers(
    live: Path,
    slot_name: str,
    host: OodleHost,
    dust: int | None = None,
    remaining_days: int | None = None,
    guard: Callable[[], None] = assert_game_stopped,
) -> WriteResult:
    if slot_kind(slot_name) != "game":
        raise EditError("尘埃和剩余天数只存在于自动存档或手动存档。")
    current_bytes = _read_live(live)
    current = _mutable(current_bytes)
    raw, payload = load_slot_json(current, slot_name, host)
    text = raw.decode("utf-8")
    updated = text
    changes: dict[tuple[str, ...], int] = {}
    player = payload.get("Player")
    if not isinstance(player, dict):
        raise EditError("这个槽里没有角色数值，没有写入。")
    if dust is not None and dust != player.get("Dust"):
        updated = replace_unique_int(updated, "Dust", dust)
        changes[("Player", "Dust")] = dust
    if remaining_days is not None and remaining_days != player.get("RemainingDays"):
        updated = replace_unique_int(updated, "RemainingDays", remaining_days)
        changes[("Player", "RemainingDays")] = remaining_days
    if not changes:
        return WriteResult(None, "数值没有变化，没有写入。")
    assert_only_changed(payload, json.loads(updated), changes)
    return _replace_raw_slot(
        live,
        current,
        current_bytes,
        slot_name,
        updated.encode("utf-8"),
        host,
        "改数值前",
        "已修改角色数值。",
        guard,
    )


def update_continue_slot(
    live: Path,
    slot_name: str,
    host: OodleHost,
    guard: Callable[[], None] = assert_game_stopped,
) -> WriteResult:
    if slot_kind(slot_name) != "game":
        raise EditError("继续槽只能指向自动存档或手动存档。")
    current_bytes = _read_live(live)
    current = _mutable(current_bytes)
    if current.slot(slot_name) is None or current.slot("__Advanced__") is None:
        raise EditError("当前存档里没有这个槽，或没有全局数据。")
    raw, payload = load_slot_json(current, "__Advanced__", host)
    if payload.get("LastContinueSlot") == slot_name:
        return WriteResult(None, "继续槽已经是这个槽，没有写入。")
    updated = replace_unique_string(raw.decode("utf-8"), "LastContinueSlot", slot_name)
    assert_only_changed(payload, json.loads(updated), {("LastContinueSlot",): slot_name})
    return _replace_raw_slot(
        live,
        current,
        current_bytes,
        "__Advanced__",
        updated.encode("utf-8"),
        host,
        "改继续槽前",
        f"继续槽已改为 {slot_title(slot_name)}。",
        guard,
    )


def _replace_raw_slot(
    live: Path,
    current: SaveFile,
    current_bytes: bytes,
    slot_name: str,
    raw: bytes,
    host: OodleHost,
    reason: str,
    message: str,
    guard: Callable[[], None],
) -> WriteResult:
    slot = current.slot(slot_name)
    if slot is None:
        raise EditError(f"没有槽位 {slot_name}")
    replacement = reseal_slot(slot, raw, host)
    rebuilt = current.with_slot_payload(slot_name, replacement).to_bytes()
    parsed = parse_save(rebuilt)
    _ensure_other_slots_unchanged(current, parsed, slot_name)
    restored, _payload = load_slot_json(parsed, slot_name, host)
    if restored != raw:
        raise EditError("重新封装后的内容和修改不一致，没有写入。")
    return _commit(live, rebuilt, reason, message, current_bytes, guard)


def _commit(
    live: Path,
    data: bytes,
    reason: str,
    message: str,
    expected: bytes | None = None,
    guard: Callable[[], None] = assert_game_stopped,
) -> WriteResult:
    guard()
    if expected is not None and live.read_bytes() != expected:
        raise EditError("存档刚刚变过，没有写入。请重新读取后再试。")
    backup = backup_file(live, reason) if live.is_file() else None
    guard()
    if expected is not None and live.read_bytes() != expected:
        where = f" 已留下备份 {backup}。" if backup else ""
        raise EditError("准备写入时存档又变化了，原文件没有被覆盖。" + where)
    atomic_write(live, data)
    if backup is not None:
        message += f" 原存档备份在 {backup}"
    return WriteResult(backup, message)


def _read_live(live: Path) -> bytes:
    if not live.is_file():
        raise EditError("还没有找到当前存档。")
    return live.read_bytes()


def _mutable(data: bytes) -> SaveFile:
    save = parse_save(data)
    if not save.is_tightly_packed():
        raise EditError("这份存档的槽位排列不能安全改写。请改用整份回档。")
    return save


def _ensure_other_slots_unchanged(before: SaveFile, after: SaveFile, changed_name: str) -> None:
    if before.names() != after.names():
        raise EditError("槽位名单发生了变化，没有写入。")
    for name in before.names():
        if name == changed_name:
            continue
        left = before.slot(name)
        right = after.slot(name)
        if left is None or right is None or left.payload != right.payload:
            raise EditError(f"{slot_title(name)} 被意外改动，没有写入。")
