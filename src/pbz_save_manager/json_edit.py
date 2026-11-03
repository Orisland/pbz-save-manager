"""只改原 JSON 文本中唯一出现的字段，避免整份重新序列化。"""

from __future__ import annotations

import json
import re
from typing import Any

from pbz_save_manager.errors import EditError


def replace_unique_int(text: str, key: str, new_value: int) -> str:
    if new_value < 0 or new_value > 99_999_999:
        raise EditError(f"{key} 超出可写范围")
    pattern = re.compile(rf'("{re.escape(key)}"\s*:\s*)(-?\d+)(?![\d.])')
    found = list(pattern.finditer(text))
    if len(found) != 1:
        raise EditError(f"字段 {key} 在这个槽里出现了 {len(found)} 次，已放弃修改")
    match = found[0]
    updated = text[: match.start(2)] + str(int(new_value)) + text[match.end(2) :]
    _require_json(updated)
    return updated


def replace_unique_string(text: str, key: str, new_value: str) -> str:
    if not new_value or any(char in new_value for char in '"\\'):
        raise EditError("新的文本里不能包含引号或反斜杠")
    pattern = re.compile(rf'("{re.escape(key)}"\s*:\s*")([^"]*)(")')
    found = list(pattern.finditer(text))
    if len(found) != 1:
        raise EditError(f"字段 {key} 在这个槽里出现了 {len(found)} 次，已放弃修改")
    match = found[0]
    updated = text[: match.start(2)] + new_value + text[match.end(2) :]
    _require_json(updated)
    return updated


def assert_only_changed(before: Any, after: Any, changes: dict[tuple[str, ...], Any]) -> None:
    _walk(before, after, (), changes)


def _walk(before: Any, after: Any, path: tuple[str, ...], changes: dict[tuple[str, ...], Any]) -> None:
    if path in changes:
        if after != changes[path]:
            raise EditError("写回后的字段值和预期不一致")
        return
    if type(before) is not type(after):
        raise EditError("修改让存档结构发生了变化，已放弃")
    if isinstance(before, dict):
        if set(before) != set(after):
            raise EditError("修改让存档字段发生了变化，已放弃")
        for key in before:
            _walk(before[key], after[key], path + (str(key),), changes)
        return
    if isinstance(before, list):
        if len(before) != len(after):
            raise EditError("修改让存档列表长度发生了变化，已放弃")
        for index, (left, right) in enumerate(zip(before, after)):
            _walk(left, right, path + (str(index),), changes)
        return
    if before != after:
        location = ".".join(path) or "根"
        raise EditError(f"修改意外碰到了 {location}，已放弃")


def _require_json(text: str) -> None:
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise EditError("修改后的槽位不再是完整的存档文本，已放弃") from exc
