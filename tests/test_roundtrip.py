import unittest
from pathlib import Path

from pbz_save_manager.codec import load_slot_json
from pbz_save_manager.container import parse_save
from pbz_save_manager.locate import locate
from pbz_save_manager.mutate import update_continue_slot, update_player_numbers
from pbz_save_manager.oodle_host import get_host

SAMPLE = Path(r"G:\Project\新建文件夹 (2)\PBZSaveGame.sav")


class RoundtripTests(unittest.TestCase):
    def test_decode_and_edit_sample_without_touching_it(self) -> None:
        if not SAMPLE.is_file():
            self.skipTest("没有样例存档")
        found = locate()
        if found.exe_path is None:
            self.skipTest("没有游戏程序")
        original = SAMPLE.read_bytes()
        host = get_host(found.exe_path)
        save = parse_save(original)
        raw, achievements = load_slot_json(save, "__Achievements__", host)
        self.assertIn("UnlockedIds", achievements)
        self.assertEqual(host.decompress(host.compress(raw), len(raw)), raw)

        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            live = Path(folder) / "PBZSaveGame.sav"
            live.write_bytes(original)
            _raw, gameplay = load_slot_json(parse_save(original), "AutoSave0", host)
            dust = gameplay["Player"]["Dust"]
            result = update_player_numbers(live, "AutoSave0", host, dust=dust + 1, guard=lambda: None)
            self.assertIsNotNone(result.backup_path)
            edited, payload = load_slot_json(parse_save(live.read_bytes()), "AutoSave0", host)
            self.assertEqual(payload["Player"]["Dust"], dust + 1)
            self.assertEqual(payload["Player"]["RemainingDays"], gameplay["Player"]["RemainingDays"])
            self.assertNotEqual(edited, _raw)
            advanced = load_slot_json(parse_save(original), "__Advanced__", host)[1]
            target = "AutoSave1" if advanced.get("LastContinueSlot") != "AutoSave1" else "AutoSave0"
            update_continue_slot(live, target, host, guard=lambda: None)
            changed = load_slot_json(parse_save(live.read_bytes()), "__Advanced__", host)[1]
            self.assertEqual(changed["LastContinueSlot"], target)
        self.assertEqual(SAMPLE.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
