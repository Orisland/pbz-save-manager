"""影之刃零 PBZS 存档容器。

文件由 12 字节外层头、一份 AES-256-GCM 封存的槽位目录，以及按目录排列的槽位块组成。
槽位块本身通常也是同样方式封存的。本模块只处理封存和目录，不解压槽位正文。
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from pbz_save_manager.errors import FormatError

MAGIC = b"PBZS"
SEAL_MARK = b"\x01\x01\x00\x00"
# 游戏把存档正文封存在固定密钥的 AES-256-GCM 里。
SAVE_KEY = bytes.fromhex(
    "15a1fe0f55e0eb298e7c9461548351696a2a0700ed2d17ba1f871457e8f93e2d"
)
_ENTRY_TAIL = 14


@dataclass(frozen=True)
class Slot:
    name: str
    algo: int
    flags: int
    uncompressed_size: int
    offset: int
    payload: bytes

    @property
    def sealed(self) -> bool:
        return bool(self.flags & 1)


@dataclass(frozen=True)
class SaveFile:
    version: int
    flags: int
    slots: list[Slot]
    trailing: bytes

    def slot(self, name: str) -> Slot | None:
        for item in self.slots:
            if item.name == name:
                return item
        return None

    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.slots)

    def is_tightly_packed(self) -> bool:
        if self.trailing:
            return False
        offset = 0
        for item in self.slots:
            if item.offset != offset:
                return False
            offset += len(item.payload)
        return True

    def with_slot_payload(self, name: str, replacement: Slot) -> SaveFile:
        found = False
        slots: list[Slot] = []
        for item in self.slots:
            if item.name == name:
                slots.append(replacement)
                found = True
            else:
                slots.append(item)
        if not found:
            raise FormatError(f"存档里没有槽位 {name}")
        return SaveFile(self.version, self.flags, slots, self.trailing)

    def to_bytes(self) -> bytes:
        entries = bytearray()
        parts: list[bytes] = []
        offset = 0
        for item in self.slots:
            name = item.name.encode("utf-8")
            if not name or len(name) > 0xFFFF:
                raise FormatError(f"槽位名无法写回：{item.name}")
            entries += struct.pack("<H", len(name))
            entries += name
            entries += struct.pack(
                "<BBIII",
                item.algo,
                item.flags,
                item.uncompressed_size,
                offset,
                len(item.payload),
            )
            parts.append(item.payload)
            offset += len(item.payload)
        directory_length = 48 + len(entries)
        outer = struct.pack(
            "<4sBBHI",
            MAGIC,
            self.version,
            self.flags,
            len(self.slots),
            directory_length,
        )
        sealed = seal(outer + bytes(entries))
        if len(sealed) != directory_length:
            raise FormatError("封存后的目录长度不符合预期")
        return outer + sealed + b"".join(parts) + self.trailing


def seal(plaintext: bytes) -> bytes:
    nonce = os.urandom(12)
    encrypted = AESGCM(SAVE_KEY).encrypt(nonce, plaintext, None)
    ciphertext, tag = encrypted[:-16], encrypted[-16:]
    return MAGIC + SEAL_MARK + nonce + tag + ciphertext


def unseal(blob: bytes) -> bytes:
    if len(blob) < 36 or blob[:4] != MAGIC:
        raise FormatError("封存块损坏")
    if blob[4:8] != SEAL_MARK:
        raise FormatError(f"不认识的封存标记 {blob[4:8].hex()}")
    nonce = blob[8:20]
    tag = blob[20:36]
    ciphertext = blob[36:]
    try:
        return AESGCM(SAVE_KEY).decrypt(nonce, ciphertext + tag, None)
    except Exception as exc:
        raise FormatError("存档校验失败，内容可能已损坏") from exc


def unseal_payload(slot: Slot) -> bytes:
    if slot.sealed:
        return unseal(slot.payload)
    return slot.payload


def parse_save(data: bytes) -> SaveFile:
    if len(data) < 12 or data[:4] != MAGIC:
        raise FormatError("这不是影之刃零存档")
    version = data[4]
    flags = data[5]
    count = struct.unpack_from("<H", data, 6)[0]
    directory_length = struct.unpack_from("<I", data, 8)[0]
    if directory_length < 36 or 12 + directory_length > len(data):
        raise FormatError("存档目录长度无效")
    directory = unseal(data[12 : 12 + directory_length])
    if directory[:12] != data[:12]:
        raise FormatError("存档目录和文件头不一致")
    body = directory[12:]
    slots: list[Slot] = []
    position = 0
    base = 12 + directory_length
    for _ in range(count):
        if position + 2 > len(body):
            raise FormatError("存档目录提前结束")
        name_length = struct.unpack_from("<H", body, position)[0]
        position += 2
        tail = position + name_length + _ENTRY_TAIL
        if name_length <= 0 or tail > len(body):
            raise FormatError("存档目录中的槽位记录损坏")
        try:
            name = body[position : position + name_length].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FormatError("槽位名不是文本") from exc
        position += name_length
        algo = body[position]
        slot_flags = body[position + 1]
        uncompressed_size, offset, block_length = struct.unpack_from(
            "<III", body, position + 2
        )
        position += _ENTRY_TAIL
        start = base + offset
        end = start + block_length
        if block_length <= 0 or end > len(data):
            raise FormatError(f"槽位 {name} 超出文件范围")
        slots.append(
            Slot(
                name=name,
                algo=algo,
                flags=slot_flags,
                uncompressed_size=uncompressed_size,
                offset=offset,
                payload=data[start:end],
            )
        )
    if position != len(body):
        raise FormatError("存档目录还有无法识别的内容")
    if slots:
        occupied = base + max(item.offset + len(item.payload) for item in slots)
    else:
        occupied = base
    return SaveFile(version, flags, slots, data[occupied:])
