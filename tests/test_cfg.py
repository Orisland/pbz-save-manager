import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from disable_process_cfg import disable_process_cfg


class CfgFlagTests(unittest.TestCase):
    def test_clears_guard_bit_and_can_run_twice(self) -> None:
        blob = bytearray(0x200)
        blob[:2] = b"MZ"
        struct.pack_into("<I", blob, 0x3C, 0x80)
        blob[0x80:0x84] = b"PE\x00\x00"
        struct.pack_into("<H", blob, 0x98, 0x20B)
        struct.pack_into("<H", blob, 0x98 + 70, 0xC160)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.exe"
            path.write_bytes(blob)
            disable_process_cfg(path)
            flags = struct.unpack_from("<H", path.read_bytes(), 0x98 + 70)[0]
            self.assertEqual(flags & 0x4000, 0)
            self.assertEqual(flags & 0x8160, 0x8160)
            disable_process_cfg(path)
            again = struct.unpack_from("<H", path.read_bytes(), 0x98 + 70)[0]
            self.assertEqual(again, flags)
