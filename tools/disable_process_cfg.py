"""关掉 PyInstaller 启动器带上的进程级控制流保护。

影之刃零的槽位解压要在进程里调用游戏自带的 Oodle。这个调用使用
setjmp/longjmp。启动器若标记了控制流保护，Windows 会在 longjmp 时
以 0xC0000409 结束进程。这里只清除主程序头部的标记，不改任何代码字节。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

IMAGE_DLLCHARACTERISTICS_GUARD_CF = 0x4000


def disable_process_cfg(path: Path) -> None:
    data = bytearray(path.read_bytes())
    characteristics_at, checksum_at = _header_fields(data)
    before = struct.unpack_from("<H", data, characteristics_at)[0]
    cleared = before & ~IMAGE_DLLCHARACTERISTICS_GUARD_CF
    if cleared != before:
        struct.pack_into("<H", data, characteristics_at, cleared)
        struct.pack_into("<I", data, checksum_at, 0)
        struct.pack_into("<I", data, checksum_at, _pe_checksum(data, checksum_at))
        path.write_bytes(data)
    after = struct.unpack_from("<H", data, characteristics_at)[0]
    if after & IMAGE_DLLCHARACTERISTICS_GUARD_CF:
        raise SystemExit(f"没能关掉控制流保护：{path}")


def _header_fields(data: bytearray) -> tuple[int, int]:
    if data[:2] != b"MZ":
        raise SystemExit("不是 Windows 可执行文件")
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew : e_lfanew + 4] != b"PE\x00\x00":
        raise SystemExit("不是 PE 文件")
    optional = e_lfanew + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    if magic != 0x20B:
        raise SystemExit("不是 64 位可执行文件")
    return optional + 70, optional + 64


def _pe_checksum(data: bytearray, checksum_at: int) -> int:
    total = 0
    length = len(data)
    for offset in range(0, length, 2):
        if checksum_at <= offset < checksum_at + 4:
            value = 0
        elif offset + 1 < length:
            value = data[offset] + (data[offset + 1] << 8)
        else:
            value = data[offset]
        total += value
        total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    return (total + length) & 0xFFFFFFFF


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("用法: disable_process_cfg.py <exe>")
    disable_process_cfg(Path(sys.argv[1]))


if __name__ == "__main__":
    main()
