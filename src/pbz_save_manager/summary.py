"""把槽位 JSON 整理成界面上的简短说明。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pbz_save_manager.container import SaveFile
from pbz_save_manager.quests import describe_narrative


@dataclass(frozen=True)
class SlotView:
    name: str
    title: str
    detail: str
    dust: int | None
    remaining_days: int | None
    continue_slot: str | None


def slot_title(name: str) -> str:
    if name.startswith("AutoSave"):
        return f"自动存档 {name}"
    if name.startswith("ManualSave"):
        return f"手动存档 {name}"
    if name == "__Advanced__":
        return "全局数据"
    if name == "__Achievements__":
        return "成就"
    return name


def slot_kind(name: str) -> str:
    if name.startswith("AutoSave") or name.startswith("ManualSave"):
        return "game"
    return name


def is_usable_save(save: SaveFile) -> bool:
    if any(slot_kind(item.name) == "game" for item in save.slots):
        return True
    return sum(item.uncompressed_size for item in save.slots) >= 100_000


def describe_directory(save: SaveFile) -> str:
    lines = [f"容器版本 {save.version}，共 {len(save.slots)} 个槽"]
    for item in save.slots:
        lines.append(
            f"{slot_title(item.name)}    解压前 {item.uncompressed_size} 字节"
        )
    if save.trailing:
        lines.append("文件末尾还有额外数据。这种文件只适合整份回档，不能改槽。")
    if not save.is_tightly_packed():
        lines.append("槽位排列不连续。这种文件只适合整份回档，不能改槽。")
    if not is_usable_save(save):
        lines.append("这里没有角色进度槽，不能用来回档。")
    return "\n".join(lines)


def view_slot(name: str, payload: dict[str, Any]) -> SlotView:
    if slot_kind(name) == "game":
        return _view_gameplay(name, payload)
    if name == "__Advanced__":
        return _view_advanced(payload)
    if name == "__Achievements__":
        unlocked = payload.get("UnlockedIds") or []
        count = len(unlocked) if isinstance(unlocked, list) else 0
        return SlotView(name, slot_title(name), f"已解锁成就 {count} 个", None, None, None)
    return SlotView(name, slot_title(name), "这个槽没有做进一步解读。", None, None, None)


def _view_gameplay(name: str, payload: dict[str, Any]) -> SlotView:
    system = _dict(payload.get("PBZGameSystem"))
    player = _dict(payload.get("Player"))
    inventory = _dict(payload.get("Inventory"))
    narrative = _dict(payload.get("Narrative"))
    death = _dict(payload.get("PBZDeathMeta"))
    bells = _dict(payload.get("BellManager"))
    dust = _int_or_none(player.get("Dust"))
    days = _int_or_none(player.get("RemainingDays"))
    lines = [
        f"关卡：{system.get('CurrentLevelName') or '未知'}",
        f"存档内时间：{system.get('DateTimeString') or '未知'}",
        f"游戏时长：{_format_seconds(system.get('HasGameTime'))}",
        f"存档点：{payload.get('BellSaveID') or bells.get('SaveBellID') or '未知'}",
        f"尘埃：{dust if dust is not None else '未知'}",
        f"剩余天数：{days if days is not None else '未知'}",
        f"死亡次数：{death.get('DiedTimes') if death.get('DiedTimes') is not None else '未知'}",
        _weapon_line(inventory),
        f"药品：上 {_text(inventory.get('TopMedicine'))}，下 {_text(inventory.get('BottomMedicine'))}",
        "",
        describe_narrative(narrative),
    ]
    return SlotView(name, slot_title(name), "\n".join(lines), dust, days, None)


def _view_advanced(payload: dict[str, Any]) -> SlotView:
    continue_slot = payload.get("LastContinueSlot")
    continue_text = continue_slot if isinstance(continue_slot, str) else ""
    codex = _dict(payload.get("Codex"))
    bosses = _dict(payload.get("BossRecord"))
    discovered = codex.get("DiscoveredEnemies")
    enemy_count = len(discovered) if isinstance(discovered, list) else 0
    lines = [
        f"继续槽：{continue_text or '无'}",
        f"图鉴中的敌人：{enemy_count}",
        f"首领记录：{len(bosses)}",
    ]
    return SlotView("__Advanced__", slot_title("__Advanced__"), "\n".join(lines), None, None, continue_text or None)


def _weapon_line(inventory: dict[str, Any]) -> str:
    mains = inventory.get("MainWeapons") or []
    shaqi = inventory.get("ShaqiWeapons") or []
    current = inventory.get("CurrentMainWeaponID")
    main_text = "、".join(str(item) for item in mains) if isinstance(mains, list) else "未知"
    shaqi_text = "、".join(str(item) for item in shaqi) if isinstance(shaqi, list) and shaqi else "无"
    return f"主武器：{main_text or '无'}（当前 {_text(current)}）\n煞器：{shaqi_text}"


def _format_seconds(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        return "未知"
    hours, remain = divmod(max(0, value), 3600)
    minutes, seconds = divmod(remain, 60)
    return f"{hours}小时{minutes:02d}分{seconds:02d}秒"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _text(value: Any) -> str:
    if value is None or value == "":
        return "无"
    return str(value)
