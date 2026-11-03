import tempfile
import unittest
from pathlib import Path

from pbz_save_manager.app import TAB_IDLE_BG, TAB_SELECTED_BG, SaveManagerApp, _set_text, choose_window_size
from pbz_save_manager.locate import Installation


class UiTests(unittest.TestCase):
    def test_window_lists_backups_without_writing(self) -> None:
        app = SaveManagerApp()
        try:
            app.root.withdraw()
            app.root.update()
            self.assertGreater(len(app.entries), 0)
            self.assertIn("PBZ", str(app.installation.game_dir))
            self.assertTrue(any(name.startswith("AutoSave") or name.startswith("ManualSave") for name in app.live_names))
            app.backup_tree.selection_set("0")
            app.root.update()
            chosen = app._selected_entry()
            self.assertIsNotNone(chosen)
            _set_text(app.preview, "解码标记")
            kept_source = app._selected_source()
            self.assertIsNotNone(kept_source)
            app._fill_source_slots()
            slot_index = 0
            if app.source_slot["values"]:
                slot_index = min(1, len(app.source_slot["values"]) - 1)
                app.source_slot.current(slot_index)
            app._show_entries(preserve=True)
            app._load_sources(preserve=True)
            self.assertEqual(app._selected_entry().path, chosen.path)
            self.assertEqual(app.preview.get("1.0", "end-1c"), "解码标记")
            self.assertEqual(app._selected_source().path, kept_source.path)
            self.assertEqual(app.source_slot.current(), slot_index)
            app._watch_listings(reschedule=False)
            self.assertEqual(app._selected_entry().path, chosen.path)
            self.assertEqual(app.preview.get("1.0", "end-1c"), "解码标记")
            self._assert_new_backup_refreshes_without_clearing(app)
        finally:
            app.root.destroy()

    def _assert_new_backup_refreshes_without_clearing(self, app: SaveManagerApp) -> None:
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder)
            realtime = profile / "1RealTime"
            realtime.mkdir()
            first = realtime / "20261102-120000-1.sav"
            first.write_bytes(b"not-a-save")
            app.installation = Installation(None, None, None, profile, (), "测试")
            app.kind.set("realtime")
            app._remember_listing()
            app._show_entries()
            app.backup_tree.selection_set("0")
            app.root.update()
            _set_text(app.preview, "仍在查看")
            second = realtime / "20261102-120100-1.sav"
            second.write_bytes(b"newer")
            app._refresh_if_listings_changed()
            self.assertEqual(app._selected_entry().path, first)
            self.assertEqual(app.preview.get("1.0", "end-1c"), "仍在查看")
            self.assertEqual([item.path for item in app.entries], [second, first])
            self.assertIn("新备份", app.status.get())

    def test_window_size_stays_on_screen_when_scale_changes(self) -> None:
        normal = choose_window_size(2560, 1440, 1)
        tight = choose_window_size(1280, 720, 1)
        scaled = choose_window_size(2560, 1440, 1.5)
        for screen_w, screen_h, scale in ((2560, 1440, 1), (1280, 720, 1), (1920, 1080, 1.5), (1366, 768, 2)):
            width, height, min_w, min_h = choose_window_size(screen_w, screen_h, scale)
            self.assertLessEqual(width, screen_w)
            self.assertLessEqual(height, screen_h)
            self.assertLessEqual(min_w, width)
            self.assertLessEqual(min_h, height)
        self.assertLess(tight[0], normal[0])
        self.assertGreaterEqual(scaled[0], normal[0])

    def test_controls_stay_inside_when_the_window_is_resized(self) -> None:
        app = SaveManagerApp()
        try:
            app.root.deiconify()
            min_w, min_h = app.root.minsize()
            for geometry in (f"{min_w}x{min_h}+20+20", f"{min_w + 480}x{min_h + 280}+20+20"):
                app.root.geometry(geometry)
                app.root.update()
                self._assert_page(
                    app,
                    0,
                    app.restore_button,
                    app.decode_backup_button,
                    app.backup_tree,
                    app.preview,
                    app.restore_hint,
                    app.path_label,
                    app.game_badge,
                    *app.tab_buttons,
                )
                self._assert_page(app, 1, app.read_live_button, app.write_numbers_button, app.overwrite_button, app.slot_text)
                self.assertGreaterEqual(app.slot_text.winfo_height(), 80)
                self.assertGreaterEqual(app.preview.winfo_height(), 80)
                self.assertGreater(app.tab_buttons[0].winfo_height(), 28)
                self.assertEqual(app.tab_buttons[1].cget("bg"), TAB_SELECTED_BG)
                self.assertEqual(app.tab_buttons[0].cget("bg"), TAB_IDLE_BG)
                app.notebook.select(0)
                app.root.update()
                self.assertEqual(app.tab_buttons[0].cget("bg"), TAB_SELECTED_BG)
                self.assertEqual(app.tab_buttons[1].cget("bg"), TAB_IDLE_BG)
                self.assertLessEqual(app.path_label.winfo_reqwidth(), app.root.winfo_width())
                used = sum(int(app.backup_tree.column(key, "width")) for key in ("when", "size", "slots", "note"))
                self.assertLessEqual(used, app.backup_tree.winfo_width() + 24)
                if app.backup_tree.winfo_width() > 700:
                    import tkinter.font as tkfont

                    needed = tkfont.nametofont("TkDefaultFont").measure("真实时间  2026-10-30 22:11:08")
                    self.assertGreaterEqual(int(app.backup_tree.column("when", "width")), needed)
                self.assertLess(int(app.intro_label.cget("wraplength")), app.root.winfo_width())
                self.assertGreater(int(app.path_label.cget("wraplength")), 80)
                self.assertLessEqual(int(app.overwrite_hint.cget("wraplength")), app.root.winfo_width())
        finally:
            app.root.destroy()

    def _assert_page(self, app: SaveManagerApp, tab: int, *widgets) -> None:
        app.notebook.select(tab)
        app.root.update()
        for widget in widgets:
            self.assertTrue(widget.winfo_viewable(), widget)
            x1, y1, x2, y2 = _box(widget, app.root)
            self.assertGreaterEqual(x1, -2, widget)
            self.assertGreaterEqual(y1, -2, widget)
            self.assertLessEqual(x2, app.root.winfo_width() + 2, widget)
            self.assertLessEqual(y2, app.root.winfo_height() + 2, widget)
            self.assertGreater(widget.winfo_width(), 8, widget)
            self.assertGreater(widget.winfo_height(), 8, widget)


def _box(widget, ancestor) -> tuple[int, int, int, int]:
    x = 0
    y = 0
    current = widget
    while current is not ancestor:
        x += current.winfo_x()
        y += current.winfo_y()
        current = current.master
    return x, y, x + widget.winfo_width(), y + widget.winfo_height()


if __name__ == "__main__":
    unittest.main()
