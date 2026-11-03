"""把一个槽位从封存块还原成 JSON 对象。"""

from __future__ import annotations

import json

from pbz_save_manager.container import SaveFile, Slot, seal, unseal, unseal_payload
from pbz_save_manager.errors import FormatError
from pbz_save_manager.oodle_host import OodleHost


def load_slot_json(save: SaveFile, name: str, host: OodleHost) -> tuple[bytes, dict]:
    slot = save.slot(name)
    if slot is None:
        raise FormatError(f"没有槽位 {name}")
    raw = decode_slot_bytes(slot, host)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FormatError(f"{name} 解压后不是存档文本") from exc
    if not isinstance(payload, dict):
        raise FormatError(f"{name} 的内容不是一组字段")
    return raw, payload


def decode_slot_bytes(slot: Slot, host: OodleHost) -> bytes:
    if slot.algo != 2:
        raise FormatError(f"{slot.name} 使用了还不能解读的压缩方式")
    raw = host.decompress(unseal_payload(slot), slot.uncompressed_size)
    if len(raw) != slot.uncompressed_size:
        raise FormatError(f"{slot.name} 解压后的长度和目录不一致")
    return raw


def reseal_slot(slot: Slot, raw: bytes, host: OodleHost) -> Slot:
    if slot.algo != 2 or not slot.sealed:
        raise FormatError(f"{slot.name} 不能安全地重新封装")
    compressed = host.compress(raw)
    payload = seal(compressed)
    if unseal(payload) != compressed:
        raise FormatError("重新封存后的校验失败")
    return Slot(slot.name, slot.algo, slot.flags, len(raw), 0, payload)
