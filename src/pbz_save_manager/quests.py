"""从存档里的 Narrative 读出任务完成状态和分支进度。

只读已经解开的 JSON，不改任务数据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_CATEGORY_LABELS = {
    "Main": "主线",
    "Branch": "支线",
    "Collect": "收集",
    "Simple": "简单",
    "Tutorial": "教学",
}

_CATEGORY_ORDER = {"主线": 0, "支线": 1, "收集": 2, "简单": 3, "教学": 4, "其他": 5}

_QUEST_STATE = {
    "QC_Succeded": "done",
    "QC_Succeeded": "done",
    "QC_Started": "active",
    "QC_Failed": "failed",
}

_BRANCH_STATE = {
    "BranchCompleted": "completed",
    "BranchActivating": "active",
    "BranchLocked": "locked",
    "None": "inactive",
    "": "inactive",
}

_STATE_LABEL = {
    "done": "已完成",
    "active": "进行中",
    "failed": "已失败",
    "other": "其他",
}

_BRANCH_LABEL = {
    "completed": "已完成",
    "active": "进行中",
    "locked": "锁定",
    "inactive": "未开始",
}


@dataclass(frozen=True)
class QuestBranch:
    branch_id: str
    state: str
    progress: tuple[int, ...]
    dirty_time: str
    raw_type: str


@dataclass(frozen=True)
class QuestRecord:
    quest_id: str
    category: str
    state: str
    branches: tuple[QuestBranch, ...]
    reached: tuple[str, ...]


def describe_narrative(narrative: dict[str, Any]) -> str:
    if not narrative:
        return "没有任务数据。"
    records = parse_saved_quests(narrative)
    tracked_id = quest_identity(_text(narrative.get("TrackedQuestPath")))[1]
    lines = _pointer_lines(narrative, records)
    lines.extend(_main_updates(narrative))
    if narrative.get("ManualTrackingOptOut") is True:
        lines.append("已取消自动追踪")
    if not records:
        lines.append("没有已保存的任务。")
        return "\n".join(lines)
    lines.append(_count_line(records))
    lines.append("")
    lines.extend(_section("进行中", records, "active", tracked_id, detailed=True))
    lines.extend(_section("已失败", records, "failed", tracked_id, detailed=True))
    lines.extend(_section("已完成", records, "done", tracked_id, detailed=False))
    lines.extend(_section("其他状态", records, "other", tracked_id, detailed=True))
    return "\n".join(lines).rstrip()


def parse_saved_quests(narrative: dict[str, Any]) -> list[QuestRecord]:
    raw = narrative.get("NarrativeSavedQuests")
    if not isinstance(raw, list):
        return []
    records: list[QuestRecord] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        record = _parse_quest(item)
        if record is not None:
            records.append(record)
    return records


def quest_identity(path: str) -> tuple[str, str]:
    if not path:
        return "其他", ""
    leaf = path.split("/")[-1]
    quest_id = leaf.split(".")[0] or leaf
    category = "其他"
    parts = path.split("/")
    if "PBZQuest" in parts:
        index = parts.index("PBZQuest") + 1
        if index < len(parts):
            category = _CATEGORY_LABELS.get(parts[index], "其他")
    return category, quest_id


def _parse_quest(item: dict[str, Any]) -> QuestRecord | None:
    path = item.get("QuestClass")
    if not isinstance(path, str) or not path:
        return None
    category, quest_id = quest_identity(path)
    completed = {
        str(name)
        for name in item.get("CompletedBranches") or []
        if isinstance(name, str) and name
    }
    branches: list[QuestBranch] = []
    raw_branches = item.get("QuestBranches")
    if isinstance(raw_branches, list):
        for branch in raw_branches:
            if isinstance(branch, dict):
                parsed = _parse_branch(branch, completed)
                if parsed is not None:
                    branches.append(parsed)
    reached_raw = item.get("ReachedStateNames") or []
    reached = tuple(name for name in reached_raw if isinstance(name, str) and name)
    return QuestRecord(quest_id, category, _quest_state(item.get("QuestCompletion")), tuple(branches), reached)


def _parse_branch(item: dict[str, Any], completed: set[str]) -> QuestBranch | None:
    branch_id = item.get("BranchID")
    if not isinstance(branch_id, str) or not branch_id:
        return None
    progress: list[int] = []
    raw_progress = item.get("TasksProgress")
    if isinstance(raw_progress, list):
        for value in raw_progress:
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            progress.append(value)
    raw_type = _enum_tail(item.get("BranchType"))
    state = _branch_state(raw_type, branch_id in completed)
    dirty = item.get("DirtyTime")
    return QuestBranch(branch_id, state, tuple(progress), dirty if isinstance(dirty, str) else "", raw_type)


def _pointer_lines(narrative: dict[str, Any], records: list[QuestRecord]) -> list[str]:
    return [
        f"追踪任务：{_pointer(narrative.get('TrackedQuestPath'), records)}",
        *_optional_pointer("追踪分支", narrative.get("TrackedBranchID")),
        *_optional_pointer("追踪任务点", narrative.get("TrackedTaskPointID")),
        f"当前主线：{_pointer(narrative.get('CurrentMainQuestPath'), records)}",
        f"当前教学：{_pointer(narrative.get('CurrentTutorialQuestPath'), records)}",
        f"最近更新的主线：{_pointer(narrative.get('LastUpdatedMainQuestPath'), records)}",
    ]


def _optional_pointer(label: str, value: Any) -> list[str]:
    if not isinstance(value, str) or value in ("", "None"):
        return []
    return [f"{label}：{value}"]


def _pointer(path: Any, records: list[QuestRecord]) -> str:
    if not isinstance(path, str) or not path:
        return "无"
    category, quest_id = quest_identity(path)
    label = f"{category} {quest_id}".strip()
    record = next((item for item in records if item.quest_id == quest_id), None)
    if record is None:
        return label
    return f"{label}（{_STATE_LABEL.get(record.state, record.state)}）"


def _main_updates(narrative: dict[str, Any]) -> list[str]:
    rows = narrative.get("MainQuestUpdateRecords")
    if not isinstance(rows, list) or not rows:
        return []
    lines = ["主线推进"]
    for row in rows:
        if not isinstance(row, dict):
            continue
        category, quest_id = quest_identity(_text(row.get("QuestClass")))
        name = f"{category} {quest_id}".strip() or "未知任务"
        branch = row.get("BranchID")
        branch_text = "" if branch in (None, "", "None") else f"  分支 {branch}"
        lines.append(f"  {name}{branch_text}  游戏内 {_game_time(row.get('GameTime'))}")
    return lines


def _count_line(records: list[QuestRecord]) -> str:
    counts = {state: 0 for state in ("done", "active", "failed", "other")}
    for record in records:
        counts[record.state if record.state in counts else "other"] += 1
    text = f"任务 {len(records)} 个：已完成 {counts['done']}，进行中 {counts['active']}，已失败 {counts['failed']}"
    if counts["other"]:
        text += f"，其他 {counts['other']}"
    return text


def _section(title: str, records: list[QuestRecord], state: str, tracked_id: str, detailed: bool) -> list[str]:
    chosen = [record for record in records if record.state == state]
    if not chosen:
        return []
    lines = [f"【{title}】"]
    for record in _ordered(chosen, tracked_id, pin_tracked=detailed):
        lines.extend(_quest_lines(record, tracked_id, detailed))
    lines.append("")
    return lines


def _ordered(records: list[QuestRecord], tracked_id: str, pin_tracked: bool) -> list[QuestRecord]:
    ordered = sorted(records, key=lambda item: item.quest_id)
    ordered.sort(key=_latest_time, reverse=True)
    ordered.sort(key=lambda item: _CATEGORY_ORDER.get(item.category, 9))
    if pin_tracked:
        ordered.sort(key=lambda item: item.quest_id != tracked_id)
    return ordered


def _quest_lines(record: QuestRecord, tracked_id: str, detailed: bool) -> list[str]:
    title = f"{record.category} {record.quest_id}".strip()
    mark = "  正在追踪" if record.quest_id == tracked_id else ""
    reached = f"  到达状态：{'、'.join(record.reached)}" if record.reached else ""
    if not detailed:
        done_ids = [branch.branch_id for branch in record.branches if branch.state == "completed"]
        branch_text = f"  分支 {'、'.join(done_ids)}" if done_ids else ""
        when = _latest_time(record, states={"completed"})
        when_text = f"  {when}" if when else ""
        return [f"{title}{mark}{branch_text}{when_text}{reached}"]
    lines = [f"{title}{mark}{reached}"]
    idle: list[str] = []
    for branch in record.branches:
        if branch.state == "inactive" and not _has_open_progress(branch):
            idle.append(branch.branch_id)
            continue
        lines.append("  " + _branch_line(branch))
    if idle:
        lines.append("  未开始分支：" + "、".join(idle))
    lines.append("")
    return lines


def _branch_line(branch: QuestBranch) -> str:
    label = branch.raw_type if branch.state == "other" and branch.raw_type else _BRANCH_LABEL.get(branch.state, branch.state)
    parts = [f"分支 {branch.branch_id}", label]
    progress = _progress_text(branch)
    if progress:
        parts.append(progress)
    if _is_real_time(branch.dirty_time):
        parts.append(branch.dirty_time)
    return "  ".join(parts)


def _progress_text(branch: QuestBranch) -> str:
    values = branch.progress
    if not values:
        return ""
    if branch.state == "completed" and all(value == 0 for value in values):
        return ""
    if branch.state == "inactive" and all(value == 0 for value in values):
        return ""
    if all(value in (0, 1) for value in values):
        done = sum(values)
        text = f"子任务 {done}/{len(values)}"
        if 0 < done < len(values):
            pending = [str(index + 1) for index, value in enumerate(values) if value == 0]
            text += "，未完成第 " + "、".join(pending) + " 项"
        return text
    return "子任务计数 " + "、".join(str(value) for value in values)


def _has_open_progress(branch: QuestBranch) -> bool:
    return any(value != 0 for value in branch.progress)


def _latest_time(record: QuestRecord, states: set[str] | None = None) -> str:
    times = [
        branch.dirty_time
        for branch in record.branches
        if _is_real_time(branch.dirty_time) and (states is None or branch.state in states)
    ]
    return max(times) if times else ""


def _quest_state(value: Any) -> str:
    return _QUEST_STATE.get(_enum_tail(value), "other")


def _branch_state(raw_type: str, listed_complete: bool) -> str:
    if listed_complete or raw_type == "BranchCompleted":
        return "completed"
    return _BRANCH_STATE.get(raw_type, "other")


def _enum_tail(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    if "::" in value:
        return value.rsplit("::", 1)[1]
    return value


def _is_real_time(value: str) -> bool:
    return bool(value) and not value.startswith("0001.01.01")


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _game_time(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        return "未知"
    hours, remain = divmod(max(0, value), 3600)
    minutes, seconds = divmod(remain, 60)
    return f"{hours}小时{minutes:02d}分{seconds:02d}秒"
