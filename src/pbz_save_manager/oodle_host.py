"""用游戏本体内的 Oodle 解压和压缩槽位正文。

只在内存里加载已经安装的 PBZ.exe，不修改游戏文件。
"""

from __future__ import annotations

import struct
import threading
from pathlib import Path

from unicorn import (
    UC_ARCH_X86,
    UC_HOOK_CODE,
    UC_HOOK_MEM_UNMAPPED,
    UC_MODE_64,
    UC_PROT_ALL,
    Uc,
    UcError,
)
from unicorn.x86_const import (
    UC_X86_REG_RAX,
    UC_X86_REG_RBX,
    UC_X86_REG_RCX,
    UC_X86_REG_RDX,
    UC_X86_REG_RDI,
    UC_X86_REG_RIP,
    UC_X86_REG_RSI,
    UC_X86_REG_RSP,
    UC_X86_REG_R8,
    UC_X86_REG_R9,
    UC_X86_REG_R10,
    UC_X86_REG_R11,
    UC_X86_REG_R12,
    UC_X86_REG_R13,
    UC_X86_REG_R14,
    UC_X86_REG_R15,
    UC_X86_REG_RBP,
    UC_X86_REG_GS_BASE,
)

from pbz_save_manager.errors import FormatError

DECOMPRESS_VA = 0x148D7FD20
COMPRESS_VA = 0x148D7F440
OPTIONS_VA = 0x148D7FBA0
TIMING_VA = 0x140D3ABA0
KRAKEN = 8
LEVEL_FAST = 3

DECOMPRESS_PROLOGUE = bytes.fromhex("405553565741544155415641574881ecf8000000")
COMPRESS_PROLOGUE = bytes.fromhex("40555356574154415541564157488d6c")

IMAGE_HINT = 0x140000000
STUB = 0x200000000
HEAP = 0x210000000
HEAP_SIZE = 0x4000000
RAW_ADDR = HEAP + 0x1000
RAW_CAP = 0x800000
COMP_ADDR = HEAP + 0x801000
COMP_CAP = 0x800000
MALLOC_BASE = HEAP + 0x1800000
SCRATCH_ADDR = HEAP + 0x1100000
STACK = 0x230000000
STACK_SIZE = 0x200000
STOP = 0x250000000
TEB = 0x260000000

_MEMCMP = bytes.fromhex(
    "31C0"          # xor eax, eax
    "FC"            # cld
    "4D85C0"        # test r8, r8
    "7421"          # jz done
    "57"            # push rdi
    "56"            # push rsi
    "4889CF"        # mov rdi, rcx
    "4889D6"        # mov rsi, rdx
    "4C89C1"        # mov rcx, r8
    "F3A6"          # repe cmpsb
    "740D"          # je equal
    "0FB647FF"      # movzx eax, byte ptr [rdi-1]
    "0FB656FF"      # movzx edx, byte ptr [rsi-1]
    "29D0"          # sub eax, edx
    "5E"            # pop rsi
    "5F"            # pop rdi
    "C3"            # ret
    "31C0"          # xor eax, eax
    "5E"
    "5F"
    "C3"
    "C3"            # done
)
_MEMCPY = bytes.fromhex(
    "4889C857564889CF4889D64C89C1F3A45E5FC3"
)
_MEMSET = bytes.fromhex(
    "4989C9574889CF8AC24C89C1F3AA4C89C85FC3"
)
_MEMMOVE = bytes.fromhex(
    "4889C84D85C0742A4839D1761657564A8D7C01FF4A8D7402FFF"
    "D4C89C1F3A4FC5E5FC357564889CF4889D64C89C1F3A45E5FC3"
)
_RET0 = b"\x31\xC0\xC3"


class EmuError(FormatError):
    """游戏内的压缩函数没有跑完。整份回档不依赖它。"""


class OodleHost:
    def __init__(self, exe_path: Path) -> None:
        self.exe_path = Path(exe_path)
        self._lock = threading.Lock()
        self._uc: Uc | None = None
        self._decompress_va = DECOMPRESS_VA
        self._compress_va = COMPRESS_VA
        self._options_va = 0
        self._unknown: list[str] = []
        self._fault: str | None = None

    def decompress(self, compressed: bytes, raw_len: int) -> bytes:
        if raw_len <= 0 or raw_len > RAW_CAP or len(compressed) > COMP_CAP:
            raise EmuError("槽位长度超出可以解压的范围")
        with self._lock:
            self._ensure()
            return self._decompress(compressed, raw_len)

    def compress(self, raw: bytes) -> bytes:
        if not raw or len(raw) > RAW_CAP:
            raise EmuError("槽位长度超出可以压缩的范围")
        with self._lock:
            self._ensure()
            blob = self._compress(raw)
            restored = self._decompress(blob, len(raw))
            if restored != raw:
                raise EmuError("压缩结果无法还原，已放弃这次修改")
            return blob

    def _ensure(self) -> None:
        if self._uc is not None:
            return
        if not self.exe_path.is_file():
            raise EmuError("没有找到游戏程序，不能解读槽位内容")
        exe = self.exe_path.read_bytes()
        image_base, size_of_image, sections = _pe_layout(exe)
        uc = Uc(UC_ARCH_X86, UC_MODE_64)
        uc.mem_map(image_base, _align(size_of_image), UC_PROT_ALL)
        header_size = _optional_header_field(exe, 60)
        uc.mem_write(image_base, exe[:header_size])
        for _name, virtual_address, virtual_size, raw_ptr, raw_size in sections:
            if raw_size:
                uc.mem_write(image_base + virtual_address, exe[raw_ptr : raw_ptr + raw_size])
        for base, size in (
            (STUB, 0x10000),
            (HEAP, HEAP_SIZE),
            (STACK, STACK_SIZE),
            (STOP, 0x1000),
            (TEB, 0x10000),
        ):
            uc.mem_map(base, size, UC_PROT_ALL)
        uc.mem_write(STOP, b"\x90")
        uc.reg_write(UC_X86_REG_GS_BASE, TEB)
        uc.mem_write(TEB + 0x08, struct.pack("<Q", STACK + STACK_SIZE))
        uc.mem_write(TEB + 0x10, struct.pack("<Q", STACK))
        uc.mem_write(TEB + 0x30, struct.pack("<Q", TEB))
        uc.mem_write(TEB + 0x58, struct.pack("<Q", TEB + 0x1000))
        self._uc = uc
        self._install_imports(exe, image_base, sections)
        self._decompress_va = self._resolve(DECOMPRESS_VA, DECOMPRESS_PROLOGUE, image_base, sections, exe)
        self._compress_va = self._resolve(COMPRESS_VA, COMPRESS_PROLOGUE, image_base, sections, exe)
        self._options_va = self._options_pointer()
        timing = bytes(uc.mem_read(TIMING_VA, 1))
        if timing == b"\x53":
            uc.mem_write(TIMING_VA, b"\x31\xC0\xC3")
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._on_unmapped)
        uc.hook_add(UC_HOOK_CODE, self._on_code, begin=STUB, end=STUB + 0x10000)

    def _decompress(self, compressed: bytes, raw_len: int) -> bytes:
        uc = self._uc
        assert uc is not None
        self._prepare_buffers(compressed, raw_len)
        self._call(
            self._decompress_va,
            (COMP_ADDR, len(compressed), RAW_ADDR, raw_len),
            {
                0x28: 1,
                0x30: 0,
                0x38: 0,
                0x40: 0,
                0x48: 0,
                0x50: 0,
                0x58: 0,
                0x60: SCRATCH_ADDR,
                0x68: 0x400000,
                0x70: 3,
            },
        )
        produced = uc.reg_read(UC_X86_REG_RAX)
        if produced != raw_len:
            self._fail(f"解压长度是 {produced}，目录记录是 {raw_len}")
        return bytes(uc.mem_read(RAW_ADDR, raw_len))

    def _compress(self, raw: bytes) -> bytes:
        uc = self._uc
        assert uc is not None
        self._prepare_buffers(b"", len(raw))
        uc.mem_write(RAW_ADDR, raw)
        uc.mem_write(COMP_ADDR, b"\x00" * min(COMP_CAP, len(raw) + 0x10000))
        self._call(
            self._compress_va,
            (KRAKEN, RAW_ADDR, len(raw), COMP_ADDR),
            {0x28: LEVEL_FAST, 0x30: self._options_va, 0x38: 0, 0x40: 0, 0x48: 0, 0x50: 0},
        )
        produced = uc.reg_read(UC_X86_REG_RAX)
        if produced <= 0 or produced > COMP_CAP:
            self._fail(f"压缩没有得到有效结果（{produced}）")
        return bytes(uc.mem_read(COMP_ADDR, produced))

    def _prepare_buffers(self, compressed: bytes, raw_len: int) -> None:
        uc = self._uc
        assert uc is not None
        uc.mem_write(HEAP, struct.pack("<Q", MALLOC_BASE))
        uc.mem_write(RAW_ADDR, b"\x00" * (raw_len + 16))
        if compressed:
            uc.mem_write(COMP_ADDR, compressed)

    def _call(self, address: int, registers: tuple[int, int, int, int], stack_qwords: dict[int, int]) -> None:
        uc = self._uc
        assert uc is not None
        self._fault = None
        rsp = STACK + STACK_SIZE - 0x2000
        rsp -= rsp % 16
        rsp += 8
        frame = bytearray(0x200)
        struct.pack_into("<Q", frame, 0, STOP)
        for offset, value in stack_qwords.items():
            struct.pack_into("<Q", frame, offset, value)
        uc.mem_write(rsp, bytes(frame))
        for reg in (
            UC_X86_REG_RAX,
            UC_X86_REG_RBX,
            UC_X86_REG_RSI,
            UC_X86_REG_RDI,
            UC_X86_REG_R10,
            UC_X86_REG_R11,
            UC_X86_REG_R12,
            UC_X86_REG_R13,
            UC_X86_REG_R14,
            UC_X86_REG_R15,
            UC_X86_REG_RBP,
        ):
            uc.reg_write(reg, 0)
        uc.reg_write(UC_X86_REG_RCX, registers[0])
        uc.reg_write(UC_X86_REG_RDX, registers[1])
        uc.reg_write(UC_X86_REG_R8, registers[2])
        uc.reg_write(UC_X86_REG_R9, registers[3])
        uc.reg_write(UC_X86_REG_RSP, rsp)
        try:
            uc.emu_start(address, STOP, timeout=180 * 1_000_000)
        except UcError as exc:
            self._fail(f"游戏内的压缩函数中断了：{exc}")
        if self._fault:
            self._fail(self._fault)

    def _install_imports(self, exe: bytes, image_base: int, sections: list[tuple]) -> None:
        uc = self._uc
        assert uc is not None
        cursor = STUB
        hooked: dict[int, str] = {}

        def place(blob: bytes) -> int:
            nonlocal cursor
            address = cursor
            uc.mem_write(address, blob)
            cursor = _align(address + len(blob), 16)
            return address

        malloc = bytearray(bytes.fromhex("48B800000021000000004C8B104D89D34901CA4983C2204C89104989D8C3"))
        struct.pack_into("<Q", malloc, 2, HEAP)
        special = {
            "memcpy": place(_MEMCPY),
            "memmove": place(_MEMMOVE),
            "memset": place(_MEMSET),
            "memcmp": place(_MEMCMP),
            "malloc": place(bytes(malloc)),
            "_malloc_base": place(bytes(malloc)),
            "free": place(_RET0),
            "_free_base": place(_RET0),
            "GetEnvironmentVariableA": place(_RET0),
            "GetEnvironmentVariableW": place(_RET0),
        }
        import_rva = _data_directory(exe, 1)[0]
        for slot, name in _iter_imports(exe, image_base, sections, import_rva):
            if name in special:
                destination = special[name]
            else:
                destination = place(b"\xCC\xC3")
                hooked[destination] = name
            uc.mem_write(slot, struct.pack("<Q", destination))
        self._hooked = hooked

    def _resolve(self, preferred: int, prologue: bytes, image_base: int, sections: list[tuple], exe: bytes) -> int:
        uc = self._uc
        assert uc is not None
        if bytes(uc.mem_read(preferred, len(prologue))) == prologue:
            return preferred
        found: list[int] = []
        start = 0
        while len(found) < 4:
            index = exe.find(prologue, start)
            if index < 0:
                break
            virtual = _offset_to_va(index, image_base, sections)
            if virtual is not None:
                found.append(virtual)
            start = index + 1
        if len(found) == 1:
            return found[0]
        raise EmuError("游戏程序版本和槽位解压对不上。整份回档仍然可用。")

    def _options_pointer(self) -> int:
        uc = self._uc
        assert uc is not None
        blob = bytes(uc.mem_read(OPTIONS_VA, 8))
        if blob[:3] != bytes.fromhex("488d05") or blob[7] != 0xC3:
            return 0
        relative = struct.unpack_from("<i", blob, 3)[0]
        return OPTIONS_VA + 7 + relative

    def _on_unmapped(self, uc: Uc, access: int, address: int, size: int, value: int, user_data: object) -> bool:
        rip = uc.reg_read(UC_X86_REG_RIP)
        self._fault = f"访问了未映射内存 {address:#x}，位置 {rip:#x}"
        uc.emu_stop()
        return False

    def _on_code(self, uc: Uc, address: int, size: int, user_data: object) -> None:
        name = self._hooked.get(address)
        if name is None:
            return
        if name == "GetProcessHeap":
            self._return_value(uc, 1)
            return
        if name in {"HeapAlloc", "RtlAllocateHeap"}:
            flags = uc.reg_read(UC_X86_REG_RDX)
            requested = uc.reg_read(UC_X86_REG_R8)
            pointer = self._bump(uc, requested)
            if self._fault:
                return
            if pointer and flags & 0x8 and requested:
                uc.mem_write(pointer, b"\x00" * requested)
            self._return_value(uc, pointer)
            return
        if name in {"HeapFree", "RtlFreeHeap"}:
            self._return_value(uc, 1)
            return
        if name in {"OutputDebugStringA", "OutputDebugStringW"}:
            self._return_value(uc, 0)
            return
        if name not in self._unknown:
            self._unknown.append(name)
        self._return_value(uc, 0)

    def _bump(self, uc: Uc, size: int) -> int:
        if size > 0x2000000:
            self._fault = f"单次分配过大：{size}"
            uc.reg_write(UC_X86_REG_RIP, STOP)
            return 0
        cursor = struct.unpack("<Q", uc.mem_read(HEAP, 8))[0]
        pointer = (cursor + 15) & ~15
        nxt = pointer + max(size, 1) + 0x20
        if nxt > HEAP + HEAP_SIZE:
            self._fault = "模拟堆用尽"
            uc.reg_write(UC_X86_REG_RIP, STOP)
            return 0
        uc.mem_write(HEAP, struct.pack("<Q", nxt))
        return pointer

    def _return_value(self, uc: Uc, value: int) -> None:
        rsp = uc.reg_read(UC_X86_REG_RSP)
        ret = struct.unpack("<Q", uc.mem_read(rsp, 8))[0]
        uc.reg_write(UC_X86_REG_RSP, rsp + 8)
        uc.reg_write(UC_X86_REG_RAX, value)
        uc.reg_write(UC_X86_REG_RIP, ret)

    def _fail(self, message: str) -> None:
        extra = ""
        if self._unknown:
            extra = "。未提供的系统函数：" + "、".join(self._unknown[:8])
        self._uc = None
        raise EmuError(str(message) + extra)


_hosts: dict[str, OodleHost] = {}


def get_host(exe_path: Path) -> OodleHost:
    key = str(Path(exe_path).resolve()).lower()
    host = _hosts.get(key)
    if host is None:
        host = OodleHost(Path(exe_path))
        _hosts[key] = host
    return host


def _align(value: int, page: int = 0x1000) -> int:
    return (value + page - 1) & ~(page - 1)


def _pe_layout(exe: bytes) -> tuple[int, int, list[tuple]]:
    e_lfanew = struct.unpack_from("<I", exe, 0x3C)[0]
    coff = e_lfanew + 4
    number = struct.unpack_from("<H", exe, coff + 2)[0]
    optional_size = struct.unpack_from("<H", exe, coff + 16)[0]
    optional = coff + 20
    magic = struct.unpack_from("<H", exe, optional)[0]
    if magic != 0x20B:
        raise EmuError("游戏程序不是 64 位程序")
    image_base = struct.unpack_from("<Q", exe, optional + 24)[0]
    size_of_image = struct.unpack_from("<I", exe, optional + 56)[0]
    section_off = optional + optional_size
    sections = []
    for index in range(number):
        offset = section_off + index * 40
        name = exe[offset : offset + 8].split(b"\x00", 1)[0].decode("latin1", "replace")
        virtual_size, virtual_address, raw_size, raw_ptr = struct.unpack_from("<IIII", exe, offset + 8)
        sections.append((name, virtual_address, virtual_size, raw_ptr, raw_size))
    return image_base, size_of_image, sections


def _optional_header_field(exe: bytes, field_offset: int) -> int:
    e_lfanew = struct.unpack_from("<I", exe, 0x3C)[0]
    optional = e_lfanew + 24
    return struct.unpack_from("<I", exe, optional + field_offset)[0]


def _data_directory(exe: bytes, index: int) -> tuple[int, int]:
    e_lfanew = struct.unpack_from("<I", exe, 0x3C)[0]
    optional = e_lfanew + 24
    return struct.unpack_from("<II", exe, optional + 112 + index * 8)


def _rva_to_offset(rva: int, sections: list[tuple]) -> int | None:
    for _name, virtual_address, virtual_size, raw_ptr, raw_size in sections:
        if virtual_address <= rva < virtual_address + max(virtual_size, raw_size):
            delta = rva - virtual_address
            if delta < raw_size:
                return raw_ptr + delta
    return None


def _offset_to_va(file_offset: int, image_base: int, sections: list[tuple]) -> int | None:
    for _name, virtual_address, _virtual_size, raw_ptr, raw_size in sections:
        if raw_ptr <= file_offset < raw_ptr + raw_size:
            return image_base + virtual_address + (file_offset - raw_ptr)
    return None


def _iter_imports(exe: bytes, image_base: int, sections: list[tuple], import_rva: int) -> list[tuple[int, str]]:
    if import_rva == 0:
        return []
    table = _rva_to_offset(import_rva, sections)
    if table is None:
        return []
    found: list[tuple[int, str]] = []
    cursor = 0
    while True:
        original, _time, _forward, name_rva, first_thunk = struct.unpack_from("<IIIII", exe, table + cursor * 20)
        if name_rva == 0:
            break
        thunk = first_thunk or original
        index = 0
        while True:
            entry = _rva_to_offset(thunk + index * 8, sections)
            if entry is None:
                break
            value = struct.unpack_from("<Q", exe, entry)[0]
            if value == 0:
                break
            if value & (1 << 63):
                name = f"ord{value & 0xFFFF}"
            else:
                name_off = _rva_to_offset(value & 0x7FFFFFFF, sections)
                if name_off is None:
                    name = "unknown"
                else:
                    end = exe.find(b"\x00", name_off + 2)
                    name = exe[name_off + 2 : end].decode("latin1", "replace")
            found.append((image_base + thunk + index * 8, name))
            index += 1
        cursor += 1
    return found
