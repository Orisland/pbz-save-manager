import tempfile
import unittest
import zipfile
from pathlib import Path

from pbz_save_manager.archive import folder_signature, list_game_backups, new_backup_names, read_backup, sync_mirror
from pbz_save_manager.container import Slot, SaveFile, parse_save, seal
from pbz_save_manager.errors import EditError
from pbz_save_manager.json_edit import assert_only_changed, replace_unique_int, replace_unique_string
from pbz_save_manager.locate import locate
from pbz_save_manager.mutate import overwrite_slot, restore_bytes

SAMPLE = Path(r"G:\Project\新建文件夹 (2)\PBZSaveGame.sav")


def build_save(parts: list[tuple[str, bytes]]) -> bytes:
    slots: list[Slot] = []
    offset = 0
    for name, body in parts:
        payload = seal(body)
        slots.append(Slot(name, 2, 1, len(body), offset, payload))
        offset += len(payload)
    return SaveFile(3, 1, slots, b"").to_bytes()


class ContainerTests(unittest.TestCase):
    def test_roundtrip_keeps_slot_bytes(self) -> None:
        original = build_save([("AutoSave0", b"alpha"), ("__Advanced__", b"beta")])
        parsed = parse_save(original)
        self.assertTrue(parsed.is_tightly_packed())
        again = parse_save(parsed.to_bytes())
        self.assertEqual(again.names(), ("AutoSave0", "__Advanced__"))
        self.assertEqual(again.slot("AutoSave0").payload, parsed.slot("AutoSave0").payload)
        self.assertEqual(again.slot("__Advanced__").payload, parsed.slot("__Advanced__").payload)

    @unittest.skipUnless(SAMPLE.is_file(), "没有样例存档")
    def test_sample_directory(self) -> None:
        save = parse_save(SAMPLE.read_bytes())
        self.assertEqual(
            save.names(),
            (
                "__Advanced__",
                "AutoSave0",
                "__Achievements__",
                "AutoSave1",
                "ManualSave1",
                "AutoSave2",
                "ManualSave2",
            ),
        )
        self.assertTrue(save.is_tightly_packed())


class JsonEditTests(unittest.TestCase):
    def test_replaces_only_the_named_integer(self) -> None:
        text = '{"Player":{"RemainingDays":59,"Dust":5351,"UltimateCharge":1.5}}'
        updated = replace_unique_int(text, "Dust", 5352)
        before = __import__("json").loads(text)
        after = __import__("json").loads(updated)
        assert_only_changed(before, after, {("Player", "Dust"): 5352})
        self.assertIn('"UltimateCharge":1.5', updated)

    def test_rejects_duplicate_and_float(self) -> None:
        with self.assertRaises(EditError):
            replace_unique_int('{"Dust":1,"Dust":2}', "Dust", 3)
        with self.assertRaises(EditError):
            replace_unique_int('{"Dust":1.5}', "Dust", 2)

    def test_replaces_continue_slot(self) -> None:
        text = '{"LastContinueSlot":"AutoSave0","Other":"AutoSave0"}'
        updated = replace_unique_string(text, "LastContinueSlot", "ManualSave1")
        self.assertIn('"LastContinueSlot":"ManualSave1"', updated)
        self.assertIn('"Other":"AutoSave0"', updated)


class ArchiveTests(unittest.TestCase):
    def test_lists_sav_and_skips_zip_when_sav_exists(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder)
            hourly = profile / "2Hourly"
            hourly.mkdir()
            body = build_save([("AutoSave0", b"progress")])
            sav = hourly / "20261030-131024-680.sav"
            sav.write_bytes(body)
            with zipfile.ZipFile(hourly / "20261030-131024-680.zip", "w") as archive:
                archive.writestr("20261030-131024-680.sav", body)
            entries = list_game_backups(profile, "hourly")
            self.assertEqual(len(entries), 1)
            self.assertTrue(entries[0].usable)
            self.assertEqual(entries[0].when.hour, 13)
            self.assertEqual(read_backup(entries[0].path), body)

    def test_marks_advanced_only_file_unusable(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            daily = Path(folder) / "3Daily"
            daily.mkdir()
            (daily / "20261029-115953-988.sav").write_bytes(build_save([("__Advanced__", b"{}")]))
            entry = list_game_backups(Path(folder), "daily")[0]
            self.assertFalse(entry.usable)
            self.assertIn("没有角色进度槽", entry.note)

    def test_folder_signature_changes_only_when_a_backup_appears(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            self.assertEqual(folder_signature(directory / "missing"), ())
            first = directory / "20261030-120000-1.sav"
            first.write_bytes(b"abc")
            (directory / "notes.txt").write_bytes(b"ignore")
            signed = folder_signature(directory)
            self.assertEqual([item[0] for item in signed], [first.name])
            self.assertEqual(signed[0][1], 3)
            self.assertEqual(folder_signature(directory), signed)
            extra = directory / "20261030-130000-1.sav"
            extra.write_bytes(b"abcd")
            self.assertNotEqual(folder_signature(directory), signed)

    def test_new_backup_names_ignore_size_changes(self) -> None:
        previous = (("game", "realtime", (("a.sav", 1, 10),)),)
        grown = (("game", "realtime", (("a.sav", 9, 11),)),)
        added = (("game", "realtime", (("a.sav", 9, 11), ("b.sav", 4, 12))),)
        self.assertEqual(new_backup_names(previous, grown), ())
        self.assertEqual(new_backup_names(previous, added), ("game/realtime/b.sav",))


class MutateTests(unittest.TestCase):
    def test_restore_and_slot_overwrite_keep_a_backup(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            live = root / "PBZSaveGame.sav"
            current = build_save([("AutoSave0", b"now"), ("ManualSave1", b"keep")])
            source = build_save([("AutoSave0", b"then"), ("ManualSave1", b"keep")])
            live.write_bytes(current)
            restored = restore_bytes(live, source, guard=lambda: None)
            self.assertIsNotNone(restored.backup_path)
            self.assertEqual(live.read_bytes(), source)
            self.assertEqual(restored.backup_path.read_bytes(), current)

            newer = build_save([("AutoSave0", b"later"), ("ManualSave1", b"keep")])
            overwritten = overwrite_slot(live, newer, "AutoSave0", guard=lambda: None)
            parsed = parse_save(live.read_bytes())
            self.assertEqual(parsed.slot("AutoSave0").payload, parse_save(newer).slot("AutoSave0").payload)
            self.assertEqual(parsed.slot("ManualSave1").payload, parse_save(source).slot("ManualSave1").payload)
            self.assertTrue(overwritten.backup_path.is_file())

    def test_refuses_backup_without_gameplay(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            live = Path(folder) / "PBZSaveGame.sav"
            live.write_bytes(build_save([("AutoSave0", b"safe")]))
            before = live.read_bytes()
            with self.assertRaises(EditError):
                restore_bytes(live, build_save([("__Advanced__", b"tiny")]), guard=lambda: None)
            self.assertEqual(live.read_bytes(), before)


class LocateTests(unittest.TestCase):
    def test_finds_installed_game_and_save(self) -> None:
        found = locate(override=None)
        if found.game_dir is None:
            self.skipTest("这台机器上没有定位到影之刃零")
        self.assertIsNotNone(found.exe_path)
        self.assertTrue(found.exe_path.is_file())
        self.assertIsNotNone(found.save_file)
        self.assertEqual(found.save_file.name, "PBZSaveGame.sav")
        self.assertTrue((found.profile_dir / "1RealTime").is_dir())
        self.assertTrue((found.profile_dir / "2Hourly").is_dir())
        self.assertTrue((found.profile_dir / "3Daily").is_dir())


class MirrorTests(unittest.TestCase):
    def test_sync_does_not_write_into_the_game_folder(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder)
            realtime = profile / "1RealTime"
            realtime.mkdir()
            body = build_save([("AutoSave0", b"mirror-me")])
            source = realtime / "20261030-195058-281.sav"
            source.write_bytes(body)
            before = {path.name for path in realtime.iterdir()}
            mirror = profile / "manager-mirror"
            copied = sync_mirror(profile, mirror)
            self.assertGreaterEqual(copied, 1)
            self.assertEqual({path.name for path in realtime.iterdir()}, before)
            stored = list((mirror / "realtime").glob("*.sav"))
            self.assertTrue(any(item.read_bytes() == body for item in stored))


if __name__ == "__main__":
    unittest.main()
