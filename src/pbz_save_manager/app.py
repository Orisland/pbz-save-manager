"""影之刃零存档管理器窗口。"""

from __future__ import annotations

import ctypes
import logging
import os
import queue
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from pbz_save_manager.archive import (
    GAME_FOLDERS,
    KIND_LABELS,
    BackupEntry,
    folder_signature,
    list_game_backups,
    list_mirror_backups,
    new_backup_names,
    read_backup,
    sync_mirror,
)
from pbz_save_manager.codec import load_slot_json
from pbz_save_manager.container import parse_save
from pbz_save_manager.errors import SaveToolError
from pbz_save_manager.guard import query_game_running
from pbz_save_manager.locate import Installation, locate
from pbz_save_manager.mutate import overwrite_slot, restore_bytes, update_continue_slot, update_player_numbers
from pbz_save_manager.oodle_host import get_host
from pbz_save_manager.paths import mirror_dir, safety_dir
from pbz_save_manager.settings import store_save_override
from pbz_save_manager.summary import describe_directory, slot_kind, slot_title, view_slot

logger = logging.getLogger(__name__)

TAB_SELECTED_BG = "#0f4c81"
TAB_IDLE_BG = "#e7eef8"
TAB_SELECTED_FG = "#ffffff"
TAB_IDLE_FG = "#1c2838"
TAB_BAR_BG = "#d5e3f5"
TAB_ACCENT = "#f2b84b"
REFRESH_MS = 2000


class _TabStrip(tk.Frame):
    """系统选项卡在 Windows 上几乎不能改颜色和字号，这里用自绘按钮代替。"""

    def __init__(self, parent: tk.Misc, notebook: ttk.Notebook) -> None:
        super().__init__(parent, bg=TAB_BAR_BG, padx=4, pady=4, highlightthickness=0, bd=0)
        self.notebook = notebook
        self.buttons: list[tk.Label] = []
        self._cells: list[tk.Frame] = []
        self._bars: list[tk.Frame] = []
        notebook.bind("<<NotebookTabChanged>>", lambda _event: self.paint(), add="+")

    def add(self, title: str) -> None:
        index = len(self.buttons)
        cell = tk.Frame(self, bg=TAB_IDLE_BG, bd=0, highlightthickness=0)
        cell.pack(side="left", padx=(0, 10), pady=2)
        label = tk.Label(
            cell,
            text=title,
            bg=TAB_IDLE_BG,
            fg=TAB_IDLE_FG,
            font=("Microsoft YaHei UI", 14, "bold"),
            padx=26,
            pady=10,
            cursor="hand2",
            bd=0,
            highlightthickness=0,
        )
        label.pack(side="top", fill="x")
        bar = tk.Frame(cell, bg=TAB_IDLE_BG, height=5, bd=0, highlightthickness=0)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        for widget in (cell, label, bar):
            widget.bind("<Button-1>", lambda _event, item=index: self._select(item))
        self._cells.append(cell)
        self.buttons.append(label)
        self._bars.append(bar)
        self.paint()

    def _select(self, index: int) -> None:
        self.notebook.select(index)
        self.paint()

    def paint(self) -> None:
        selected = 0
        try:
            current = self.notebook.select()
            if current:
                selected = self.notebook.index(current)
        except tk.TclError:
            selected = 0
        for index, (cell, label, bar) in enumerate(zip(self._cells, self.buttons, self._bars)):
            active = index == selected
            background = TAB_SELECTED_BG if active else TAB_IDLE_BG
            foreground = TAB_SELECTED_FG if active else TAB_IDLE_FG
            label.configure(bg=background, fg=foreground)
            cell.configure(bg=background)
            bar.configure(bg=TAB_ACCENT if active else background)


class SaveManagerApp:
    def __init__(self) -> None:
        register_window_identity()
        enable_process_dpi()
        self.root = tk.Tk()
        self.root.title("影之刃零存档管理器")
        self._window_icon = apply_window_icon(self.root)
        self.root.resizable(True, True)
        sync_tk_scaling(self.root)
        configure_ui_fonts()
        width, height, min_w, min_h = choose_window_size(
            self.root.winfo_screenwidth(),
            self.root.winfo_screenheight(),
            ui_scale(self.root),
        )
        self.root.minsize(min_w, min_h)
        self.root.geometry(f"{width}x{height}")
        self._content_width = width
        install_dpi_watch(self.root, self._on_dpi_change)
        self.queue: queue.Queue = queue.Queue()
        self.installation: Installation = locate()
        self.entries: list[BackupEntry] = []
        self.sources: list[BackupEntry] = []
        self.live_names: tuple[str, ...] = ()
        self.loaded_slot: str | None = None
        self._suppress_preview = False
        self._listing_signature: tuple | None = None
        self._pending_after: dict[str, str] = {}
        self.kind = tk.StringVar(value="realtime")
        self.status = tk.StringVar(value="正在定位游戏。")
        self.game_state = tk.StringVar(value="正在确认游戏是否在运行。")
        self.path_text = tk.StringVar(value="")
        self._build()
        self._reload()
        self.root.bind("<FocusIn>", self._on_window_focus, add="+")
        self.root.bind("<Destroy>", self._cancel_scheduled, add="+")
        self._schedule("drain", 200, self._drain)
        self._schedule("sync", 600, self._sync_later)
        self._schedule("poll", 400, self._poll_game)
        self._schedule("watch", REFRESH_MS, self._watch_listings)

    def mainloop(self) -> None:
        self.root.mainloop()

    def _on_dpi_change(self) -> None:
        _width, _height, min_w, min_h = choose_window_size(
            self.root.winfo_screenwidth(),
            self.root.winfo_screenheight(),
            ui_scale(self.root),
        )
        self.root.minsize(min_w, min_h)

    def _build(self) -> None:
        style = ttk.Style(self.root)
        _configure_styles(style)
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="影之刃零存档管理器", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.intro_label = ttk.Label(
            header,
            text="进度异常时先回档。数值修改会先把当前存档完整复制到安全备份，再写入。",
            style="Hint.TLabel",
            justify="left",
        )
        self.intro_label.grid(row=1, column=0, sticky="ew", pady=(2, 8))
        track_wrap(self.intro_label, self._content_width, reserve=40)
        self.path_label = ttk.Label(header, textvariable=self.path_text, justify="left", anchor="w")
        self.path_label.grid(row=2, column=0, sticky="ew")
        track_wrap(self.path_label, self._content_width, reserve=40)
        actions = ttk.Frame(header)
        actions.grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Button(actions, text="重新检测", command=self._detect).pack(side="left")
        ttk.Button(actions, text="手动选择存档", command=self._choose_save).pack(side="left", padx=(8, 0))
        self._badge_host = tk.Frame(header, bg="#eceff3", bd=0, highlightthickness=0)
        self._badge_host.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.game_badge = tk.Label(
            self._badge_host,
            textvariable=self.game_state,
            justify="left",
            anchor="w",
            bg="#eceff3",
            fg="#1c2838",
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=12,
            pady=7,
            bd=0,
            highlightthickness=0,
        )
        self.game_badge.pack(fill="x")
        track_wrap(self.game_badge, self._content_width)

        self.notebook = ttk.Notebook(outer)
        self.tab_strip = _TabStrip(outer, self.notebook)
        self.tab_strip.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.notebook.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        self._build_restore(self.notebook)
        self._build_edit(self.notebook)
        self.tab_strip.add("回档")
        self.tab_strip.add("查看和修改")
        self.tab_buttons = self.tab_strip.buttons
        self.status_label = ttk.Label(outer, textvariable=self.status, justify="left")
        self.status_label.grid(row=3, column=0, sticky="ew")
        track_wrap(self.status_label, self._content_width)

    def _build_restore(self, book: ttk.Notebook) -> None:
        page = ttk.Frame(book, padding=8)
        book.add(page, text="回档")
        page.rowconfigure(1, weight=1)
        page.columnconfigure(0, weight=1)

        modes = ttk.Frame(page)
        modes.grid(row=0, column=0, sticky="ew")
        radios = ttk.Frame(modes)
        radios.pack(fill="x")
        for value, label in (
            ("realtime", "真实时间"),
            ("hourly", "按小时"),
            ("daily", "按天"),
            ("mirror", "管理器留存"),
        ):
            ttk.Radiobutton(
                radios,
                text=label,
                value=value,
                variable=self.kind,
                command=self._show_entries,
                style="Mode.TRadiobutton",
            ).pack(side="left", padx=(0, 16), pady=4)
        folders = ttk.Frame(modes)
        folders.pack(fill="x", pady=(4, 0))
        ttk.Button(folders, text="打开存档目录", command=self._open_profile).pack(side="left")
        ttk.Button(folders, text="打开留存目录", command=lambda: os.startfile(mirror_dir())).pack(side="left", padx=8)
        ttk.Button(folders, text="打开安全备份", command=self._open_safety).pack(side="left")

        panes = ttk.Panedwindow(page, orient="horizontal")
        panes.grid(row=1, column=0, sticky="nsew", pady=8)
        left = ttk.Frame(panes)
        right = ttk.Frame(panes)
        panes.add(left, weight=3)
        panes.add(right, weight=2)
        _place_sash_once(panes, 0.58)
        columns = ("when", "size", "slots", "note")
        self.backup_tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        for key, title in (
            ("when", "时间"),
            ("size", "大小"),
            ("slots", "槽位数"),
            ("note", "说明"),
        ):
            self.backup_tree.heading(key, text=title)
            self.backup_tree.column(key, width=80, anchor="w", stretch=key in {"when", "note"})
        scroll = ttk.Scrollbar(left, command=self.backup_tree.yview)
        self.backup_tree.configure(yscrollcommand=scroll.set)
        self.backup_tree.tag_configure("even", background="#f4f8fc")
        self.backup_tree.tag_configure("odd", background="#ffffff")
        self.backup_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.backup_tree.bind("<<TreeviewSelect>>", lambda _event: self._preview_backup())
        self.backup_tree.bind("<Configure>", self._fit_tree_columns)
        self.preview = _text_panel(right)

        buttons = ttk.Frame(page)
        buttons.grid(row=2, column=0, sticky="ew")
        buttons.columnconfigure(2, weight=1)
        self.decode_backup_button = ttk.Button(buttons, text="读取所选内容", command=self._decode_backup)
        self.decode_backup_button.grid(row=0, column=0, sticky="w")
        self.restore_button = ttk.Button(buttons, text="回档到所选", command=self._restore)
        self.restore_button.grid(row=0, column=1, sticky="w", padx=8)
        self.restore_hint = ttk.Label(buttons, text="回档会替换整个当前存档，并先留下一份安全备份。", justify="left")
        self.restore_hint.grid(row=0, column=2, sticky="ew")
        track_wrap(self.restore_hint, 240, reserve=460)

    def _build_edit(self, book: ttk.Notebook) -> None:
        page = ttk.Frame(book, padding=8)
        book.add(page, text="查看和修改")
        page.rowconfigure(1, weight=1)
        page.columnconfigure(0, weight=1)

        top = ttk.LabelFrame(page, text="用一份备份覆盖当前存档里的同名槽", padding=8)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        self.overwrite_hint = ttk.Label(
            top,
            text="只覆盖选中的那一个槽，其余槽保持原样。自动存档和手动存档可以互拷。成就、全局数据不会覆盖角色进度。",
            justify="left",
        )
        self.overwrite_hint.grid(row=0, column=0, sticky="ew")
        track_wrap(self.overwrite_hint, self._content_width, reserve=96)
        row = ttk.Frame(top)
        row.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        row.columnconfigure(1, weight=3)
        row.columnconfigure(3, weight=2)
        ttk.Label(row, text="来源备份").grid(row=0, column=0, sticky="w")
        self.source_box = ttk.Combobox(row, state="readonly", width=12)
        self.source_box.grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(row, text="槽位").grid(row=0, column=2, sticky="w")
        self.source_slot = ttk.Combobox(row, state="readonly", width=10)
        self.source_slot.grid(row=0, column=3, sticky="ew", padx=6)
        self.overwrite_button = ttk.Button(row, text="覆盖当前同名槽", command=self._overwrite)
        self.overwrite_button.grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))
        self.source_box.bind("<<ComboboxSelected>>", lambda _event: self._fill_source_slots())

        bottom = ttk.LabelFrame(page, text="当前存档", padding=8)
        bottom.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        bottom.rowconfigure(0, weight=1)
        bottom.columnconfigure(0, weight=1)
        panes = ttk.Panedwindow(bottom, orient="horizontal")
        panes.grid(row=0, column=0, sticky="nsew")
        left = ttk.Frame(panes)
        right = ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=3)
        _place_sash_once(panes, 0.28)
        self.slot_list = tk.Listbox(
            left,
            width=18,
            height=6,
            exportselection=False,
            font="TkDefaultFont",
            bg="#ffffff",
            fg="#1c2838",
            selectbackground="#0f4c81",
            selectforeground="#ffffff",
            activestyle="none",
            highlightthickness=1,
            highlightbackground="#c5d4ea",
            relief="flat",
            borderwidth=0,
        )
        slot_scroll = ttk.Scrollbar(left, command=self.slot_list.yview)
        self.slot_list.configure(yscrollcommand=slot_scroll.set)
        self.slot_list.pack(side="left", fill="both", expand=True)
        slot_scroll.pack(side="right", fill="y")
        self.slot_list.bind("<<ListboxSelect>>", lambda _event: self._preview_live_slot())

        self.read_live_button = ttk.Button(right, text="读取所选槽的内容", command=self._decode_live)
        self.read_live_button.pack(side="bottom", anchor="w", pady=(6, 0))
        form = ttk.Frame(right)
        form.pack(side="bottom", fill="x", pady=(6, 0))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)
        ttk.Label(form, text="尘埃").grid(row=0, column=0, sticky="w", pady=2)
        self.dust_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.dust_var, width=8).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(form, text="剩余天数").grid(row=0, column=2, sticky="w")
        self.days_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.days_var, width=8).grid(row=0, column=3, sticky="ew")
        ttk.Label(form, text="继续槽").grid(row=1, column=0, sticky="w", pady=(6, 2))
        self.continue_box = ttk.Combobox(form, state="readonly", width=12)
        self.continue_box.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(6, 0))
        self.write_numbers_button = ttk.Button(form, text="写入这两个数值", command=self._write_numbers)
        self.write_numbers_button.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.write_continue_button = ttk.Button(form, text="写入继续槽", command=self._write_continue)
        self.write_continue_button.grid(row=2, column=2, columnspan=2, sticky="w", pady=(6, 0))
        self.slot_text = _text_panel(right, height=4)

    def _reload(self) -> None:
        found = self.installation
        lines = [f"定位方式：{found.source}"]
        lines.append(f"游戏目录：{found.game_dir or '未找到'}")
        lines.append(f"当前存档：{found.save_file or '未找到'}")
        self.path_text.set("\n".join(lines))
        self._show_entries()
        self._load_sources()
        self._show_live_slots()
        self._remember_listing()

    def _detect(self) -> None:
        store_save_override(None)
        self.installation = locate(override=None)
        self._reload()
        self.status.set("已重新检测。")

    def _choose_save(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择 PBZSaveGame.sav",
            filetypes=[("影之刃零存档", "*.sav")],
        )
        if not selected:
            return
        path = Path(selected)
        store_save_override(path)
        self.installation = locate(override=path)
        self._reload()
        self.status.set("已改用手动选择的存档。")

    def _show_entries(self, preserve: bool = False) -> None:
        previous_path: Path | None = None
        preview_text: str | None = None
        if preserve:
            selected = self._selected_entry()
            previous_path = selected.path if selected is not None else None
            preview_text = self.preview.get("1.0", "end-1c")
        self.entries = self._entries_for_current_kind()
        self.backup_tree.delete(*self.backup_tree.get_children())
        chosen: str | None = None
        for index, entry in enumerate(self.entries):
            count = len(entry.save.slots) if entry.save else 0
            iid = str(index)
            self.backup_tree.insert(
                "",
                "end",
                iid=iid,
                values=(entry.label, _size(entry.size), count, entry.note),
                tags=("odd" if index % 2 else "even",),
            )
            if previous_path is not None and entry.path == previous_path:
                chosen = iid
        if chosen is not None:
            self._suppress_preview = True
            try:
                self.backup_tree.selection_set(chosen)
                self.backup_tree.see(chosen)
            finally:
                self._suppress_preview = False
            if preview_text is not None:
                _set_text(self.preview, preview_text)
        elif not preserve or previous_path is not None:
            _set_text(self.preview, "选择左侧一份备份后，这里显示槽位目录。")
        if not preserve:
            kind = self.kind.get()
            self.status.set(f"{KIND_LABELS.get(kind, '留存')} 共 {len(self.entries)} 份。")

    def _entries_for_current_kind(self) -> list[BackupEntry]:
        profile = self.installation.profile_dir
        kind = self.kind.get()
        if kind == "mirror":
            return list_mirror_backups()
        if profile is None:
            return []
        return list_game_backups(profile, kind)

    def _load_sources(self, preserve: bool = False) -> None:
        previous_path: Path | None = None
        previous_slot = -1
        if preserve:
            current = self._selected_source()
            if current is not None:
                previous_path = current.path
                previous_slot = self.source_slot.current()
        items = self._collect_sources()
        self.sources = items
        self.source_box["values"] = [item.label for item in items]
        if not items:
            self.source_slot["values"] = ()
            return
        chosen = 0
        if previous_path is not None:
            for index, item in enumerate(items):
                if item.path == previous_path:
                    chosen = index
                    break
            else:
                previous_slot = -1
        self.source_box.current(chosen)
        names = items[chosen].save.names() if items[chosen].save else ()
        if previous_slot >= 0 and previous_slot < len(names):
            self.source_slot["values"] = [slot_title(name) for name in names]
            self.source_slot.current(previous_slot)
            return
        self._fill_source_slots()

    def _collect_sources(self) -> list[BackupEntry]:
        profile = self.installation.profile_dir
        items: list[BackupEntry] = []
        if profile is not None:
            for kind in ("realtime", "hourly", "daily"):
                items.extend(list_game_backups(profile, kind))
        items.extend(list_mirror_backups())
        items.sort(key=lambda item: item.when, reverse=True)
        return items

    def _fill_source_slots(self) -> None:
        entry = self._selected_source()
        names = entry.save.names() if entry and entry.save else ()
        self.source_slot["values"] = [slot_title(name) for name in names]
        if names:
            self.source_slot.current(0)

    def _show_live_slots(self) -> None:
        self.slot_list.delete(0, "end")
        save_file = self.installation.save_file
        self.live_names: tuple[str, ...] = ()
        if save_file is None or not save_file.is_file():
            _set_text(self.slot_text, "还没有定位到当前存档。")
            return
        try:
            save = parse_save(save_file.read_bytes())
        except (OSError, SaveToolError) as exc:
            _set_text(self.slot_text, f"当前存档暂时读不了：{exc}")
            return
        self.live_names = save.names()
        for name in self.live_names:
            self.slot_list.insert("end", slot_title(name))
        gameplay = [name for name in self.live_names if slot_kind(name) == "game"]
        self.continue_box["values"] = [slot_title(name) for name in gameplay]
        if gameplay:
            self.continue_box.current(0)
        if self.live_names:
            self.slot_list.selection_set(0)
            self._preview_live_slot()

    def _preview_backup(self) -> None:
        if self._suppress_preview:
            return
        entry = self._selected_entry()
        if entry is None:
            return
        if entry.save is None:
            _set_text(self.preview, entry.note or "这份备份读不了。")
            return
        _set_text(self.preview, describe_directory(entry.save))

    def _preview_live_slot(self) -> None:
        name = self._selected_live_name()
        save_file = self.installation.save_file
        if name is None or save_file is None:
            return
        self.loaded_slot = None
        self.dust_var.set("")
        self.days_var.set("")
        try:
            save = parse_save(save_file.read_bytes())
        except (OSError, SaveToolError) as exc:
            _set_text(self.slot_text, str(exc))
            return
        _set_text(self.slot_text, describe_directory(save) + f"\n\n当前选中：{slot_title(name)}\n点“读取所选槽的内容”后显示尘埃、天数和任务。")

    def _decode_backup(self) -> None:
        entry = self._selected_entry()
        if entry is None or entry.save is None:
            messagebox.showinfo("回档", "先选一份能够读取的备份。")
            return
        name = next((item.name for item in entry.save.slots if slot_kind(item.name) == "game"), None)
        if name is None:
            messagebox.showinfo("回档", "这份备份没有角色进度槽。")
            return
        self._decode_path(entry.path, name, self.preview)

    def _decode_live(self) -> None:
        name = self._selected_live_name()
        save_file = self.installation.save_file
        if name is None or save_file is None:
            return
        self._decode_path(save_file, name, self.slot_text, fill_form=True)

    def _decode_path(self, path: Path, name: str, widget: tk.Text, fill_form: bool = False) -> None:
        exe = self.installation.exe_path
        if exe is None:
            messagebox.showinfo("查看", "找到了存档，但没有找到游戏程序，所以还不能解压槽位内容。整份回档仍然可用。")
            return

        def work():
            host = get_host(exe)
            save = parse_save(read_backup(path) if path.suffix.lower() == ".zip" else path.read_bytes())
            _raw, payload = load_slot_json(save, name, host)
            summary = view_slot(name, payload)
            continue_name = None
            if "__Advanced__" in save.names() and name != "__Advanced__":
                advanced = load_slot_json(save, "__Advanced__", host)[1]
                continue_name = advanced.get("LastContinueSlot")
            return summary, continue_name

        def done(result) -> None:
            summary, continue_name = result
            text = summary.detail
            if continue_name:
                text += f"\n全局继续槽：{slot_title(str(continue_name))}"
            _set_text(widget, text)
            if fill_form:
                self.loaded_slot = summary.name
                self.dust_var.set("" if summary.dust is None else str(summary.dust))
                self.days_var.set("" if summary.remaining_days is None else str(summary.remaining_days))
                if continue_name in self.live_names:
                    labels = list(self.continue_box["values"])
                    title = slot_title(str(continue_name))
                    if title in labels:
                        self.continue_box.current(labels.index(title))
            self.status.set(f"已读取 {slot_title(name)}。")

        self._run_worker(work, done)

    def _restore(self) -> None:
        entry = self._selected_entry()
        live = self.installation.save_file
        if entry is None or live is None:
            messagebox.showinfo("回档", "还没有可以回档的目标和当前存档。")
            return
        if not entry.usable:
            messagebox.showwarning("回档", "这份备份没有完整的角色进度，管理器不会用它覆盖当前存档。")
            return
        if not self._writes_allowed():
            return
        agreed = messagebox.askyesno(
            "确认回档",
            f"将用 {entry.label} 替换当前存档。\n\n写入前会先把现在的存档复制到安全备份目录。\n如果开了 Steam 云存档，进游戏后请确认读到的是这份进度。",
            default=messagebox.NO,
        )
        if not agreed:
            return
        try:
            result = restore_bytes(live, read_backup(entry.path))
        except SaveToolError as exc:
            logger.info("回档未写入：%s", exc)
            messagebox.showerror("没有写入", str(exc))
            return
        logger.info("回档完成：%s", result.message)
        messagebox.showinfo("回档", result.message)
        self._show_live_slots()
        self.status.set(result.message)

    def _overwrite(self) -> None:
        entry = self._selected_source()
        live = self.installation.save_file
        if entry is None or entry.save is None or live is None:
            messagebox.showinfo("覆盖槽位", "先选择一份来源备份。")
            return
        names = entry.save.names()
        index = self.source_slot.current()
        if index < 0 or index >= len(names):
            messagebox.showinfo("覆盖槽位", "先选择要覆盖的槽。")
            return
        name = names[index]
        if not self._writes_allowed():
            return
        agreed = messagebox.askyesno(
            "确认覆盖槽位",
            f"将用 {entry.label} 里的 {slot_title(name)} 覆盖当前存档的同名槽。\n其他槽保持不动。写入前会先备份。",
            default=messagebox.NO,
        )
        if not agreed:
            return
        try:
            result = overwrite_slot(live, read_backup(entry.path), name)
        except SaveToolError as exc:
            logger.info("改槽未写入：%s", exc)
            messagebox.showerror("没有写入", str(exc))
            return
        logger.info("改槽完成：%s", result.message)
        messagebox.showinfo("覆盖槽位", result.message)
        self._show_live_slots()
        self.status.set(result.message)

    def _write_numbers(self) -> None:
        name = self._selected_live_name()
        live = self.installation.save_file
        exe = self.installation.exe_path
        if name is None or live is None or exe is None:
            messagebox.showinfo("修改", "先定位存档和游戏程序，再选择一个自动或手动存档。")
            return
        if slot_kind(name) != "game":
            messagebox.showinfo("修改", "尘埃和剩余天数只写在自动存档或手动存档里。")
            return
        if self.loaded_slot != name:
            messagebox.showinfo("修改", "请先读取这个槽的内容，确认数值后再写入。")
            return
        try:
            dust = _optional_int(self.dust_var.get(), "尘埃")
            days = _optional_int(self.days_var.get(), "剩余天数")
        except SaveToolError as exc:
            messagebox.showerror("没有写入", str(exc))
            return
        if not self._writes_allowed():
            return
        agreed = messagebox.askyesno(
            "确认修改数值",
            f"将按输入修改 {slot_title(name)} 的尘埃和剩余天数。\n空着的一项保持原值。写入前会先备份。进度异常时，请改用回档。",
            default=messagebox.NO,
        )
        if not agreed:
            return

        def work():
            return update_player_numbers(live, name, get_host(exe), dust=dust, remaining_days=days)

        def done(result) -> None:
            messagebox.showinfo("修改", result.message)
            self.status.set(result.message)
            self._show_live_slots()

        self._run_worker(work, done)

    def _write_continue(self) -> None:
        live = self.installation.save_file
        exe = self.installation.exe_path
        labels = list(self.continue_box["values"])
        index = self.continue_box.current()
        gameplay = [name for name in self.live_names if slot_kind(name) == "game"]
        if live is None or exe is None or index < 0 or index >= len(gameplay):
            messagebox.showinfo("继续槽", "先选择一个自动或手动存档作为继续槽。")
            return
        name = gameplay[index]
        if not self._writes_allowed():
            return
        agreed = messagebox.askyesno(
            "确认继续槽",
            f"将把继续游戏指向 {labels[index]}。写入前会先备份。",
            default=messagebox.NO,
        )
        if not agreed:
            return

        def work():
            return update_continue_slot(live, name, get_host(exe))

        def done(result) -> None:
            messagebox.showinfo("继续槽", result.message)
            self.status.set(result.message)

        self._run_worker(work, done)

    def _writes_allowed(self) -> bool:
        state = query_game_running()
        if state is True:
            messagebox.showwarning("游戏正在运行", "请先退出到桌面，再回档或修改。")
            return False
        if state is None:
            messagebox.showwarning("无法确认", "无法确认游戏是否在运行，已停止写入。")
            return False
        return True

    def _run_worker(self, work, done) -> None:
        self.status.set("正在处理，第一次会加载游戏程序。")

        def runner() -> None:
            try:
                result = work()
            except Exception as exc:
                logger.exception("后台任务失败")
                self.queue.put(("error", exc))
                return
            self.queue.put(("done", done, result))

        threading.Thread(target=runner, daemon=True).start()

    def _sync_later(self) -> None:
        profile = self.installation.profile_dir
        if profile is not None:
            threading.Thread(target=self._sync_worker, args=(profile,), daemon=True).start()
        if self.root.winfo_exists():
            self._schedule("sync", 20000, self._sync_later)

    def _sync_worker(self, profile: Path) -> None:
        try:
            copied = sync_mirror(profile)
        except Exception as exc:
            logger.exception("留存备份失败")
            self.queue.put(("status", f"留存备份时出现问题：{exc}"))
            return
        if copied:
            self.queue.put(("synced", copied))

    def _poll_game(self) -> None:
        state = query_game_running()
        if state is True:
            self.game_state.set("游戏正在运行。退出到桌面后才能回档或修改。")
            self._paint_game_badge("#8a3b00", "#fde8d0")
        elif state is False:
            self.game_state.set("游戏未运行。可以回档或修改。")
            self._paint_game_badge("#146c2e", "#e5f6ea")
        else:
            self.game_state.set("无法确认游戏是否在运行。写入会暂停。")
            self._paint_game_badge("#3d4450", "#eceff3")
        if self.root.winfo_exists():
            self._schedule("poll", 2000, self._poll_game)

    def _paint_game_badge(self, foreground: str, background: str) -> None:
        self.game_badge.configure(fg=foreground, bg=background)
        self._badge_host.configure(bg=background)

    def _remember_listing(self) -> None:
        try:
            self._listing_signature = self._listing_signature_now()
        except OSError:
            self._listing_signature = None

    def _listing_signature_now(self) -> tuple:
        parts: list[tuple] = []
        profile = self.installation.profile_dir
        if profile is not None:
            for kind, name in GAME_FOLDERS.items():
                parts.append(("game", kind, folder_signature(profile / name)))
        base = mirror_dir()
        for kind in GAME_FOLDERS:
            parts.append(("mirror", kind, folder_signature(base / kind)))
        return tuple(parts)

    def _on_window_focus(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        self._watch_listings(reschedule=False)

    def _watch_listings(self, reschedule: bool = True) -> None:
        try:
            if self.root.winfo_exists():
                self._refresh_if_listings_changed()
        except tk.TclError:
            return
        if not reschedule:
            return
        try:
            if self.root.winfo_exists():
                self._schedule("watch", REFRESH_MS, self._watch_listings)
        except tk.TclError:
            return

    def _refresh_if_listings_changed(self) -> None:
        try:
            current = self._listing_signature_now()
        except OSError:
            return
        previous = self._listing_signature
        if current == previous:
            return
        self._show_entries(preserve=True)
        self._load_sources(preserve=True)
        self._listing_signature = current
        arrived = new_backup_names(previous, current)
        if previous is not None and arrived:
            self.status.set(f"发现 {len(arrived)} 份新备份，列表已更新。")

    def _drain(self) -> None:
        try:
            while True:
                kind, *rest = self.queue.get_nowait()
                if kind == "error":
                    messagebox.showerror("没有完成", str(rest[0]))
                    self.status.set(str(rest[0]))
                elif kind == "done":
                    done, result = rest
                    done(result)
                elif kind == "synced":
                    self._show_entries(preserve=True)
                    self._load_sources(preserve=True)
                    self._remember_listing()
                    self.status.set(f"已把 {rest[0]} 份新备份留存在管理器目录，没有写入游戏目录。")
                elif kind == "status":
                    self.status.set(str(rest[0]))
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self._schedule("drain", 200, self._drain)

    def _schedule(self, name: str, delay: int, callback) -> None:
        self._pending_after[name] = self.root.after(delay, callback)

    def _cancel_scheduled(self, event: tk.Event | None = None) -> None:
        if event is not None and event.widget is not self.root:
            return
        for token in self._pending_after.values():
            try:
                self.root.after_cancel(token)
            except tk.TclError:
                pass
        self._pending_after.clear()

    def _selected_entry(self) -> BackupEntry | None:
        selected = self.backup_tree.selection()
        if not selected:
            return None
        return self.entries[int(selected[0])]

    def _selected_source(self) -> BackupEntry | None:
        index = self.source_box.current()
        if index < 0 or index >= len(self.sources):
            return None
        return self.sources[index]

    def _selected_live_name(self) -> str | None:
        selected = self.slot_list.curselection()
        if not selected:
            return None
        return self.live_names[selected[0]]

    def _open_profile(self) -> None:
        profile = self.installation.profile_dir
        if profile and profile.is_dir():
            os.startfile(profile)

    def _open_safety(self) -> None:
        os.startfile(safety_dir())

    def _fit_tree_columns(self, event: tk.Event) -> None:
        if event.widget is not self.backup_tree or event.width <= 1:
            return
        if abs(event.width - getattr(self, "_tree_width", 0)) < 4:
            return
        self._tree_width = event.width
        font = tkfont.nametofont("TkDefaultFont")
        available = max(event.width - 8, 80)
        size_w = font.measure("1024 KB") + 24
        slots_w = font.measure("槽位数") + 24
        when_need = font.measure("留存·真实时间  2026-10-30 22:11:08") + 28
        note_floor = font.measure("说明") + 16
        leftover = available - size_w - slots_w - when_need
        if leftover < note_floor:
            when_need = max(font.measure("2026-10-30") + 16, available - size_w - slots_w - note_floor)
            leftover = max(note_floor, available - size_w - slots_w - when_need)
        self.backup_tree.column("when", width=when_need)
        self.backup_tree.column("size", width=size_w)
        self.backup_tree.column("slots", width=slots_w)
        self.backup_tree.column("note", width=leftover)


def register_window_identity() -> None:
    """在创建窗口前声明独立应用。否则任务栏会继续显示 Tk 的默认图标。"""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PBZ.SaveManager")
    except (AttributeError, OSError):
        return


def apply_window_icon(root: tk.Tk) -> tk.PhotoImage | None:
    """标题栏和任务栏都用这张图。文件图标由打包时的 ico 提供。"""
    ico = _bundled_icon("pbz-save.ico")
    if ico is not None:
        try:
            root.iconbitmap(default=str(ico))
        except tk.TclError:
            pass
        try:
            root.iconbitmap(str(ico))
        except tk.TclError:
            pass
    image = None
    png = _bundled_icon("pbz-save.png")
    if png is not None:
        try:
            image = tk.PhotoImage(file=str(png))
            root.iconphoto(True, image)
        except tk.TclError:
            image = None
    return image


def _bundled_icon(name: str) -> Path | None:
    if getattr(sys, "frozen", False):
        candidate = Path(getattr(sys, "_MEIPASS", "")) / name
    else:
        candidate = Path(__file__).resolve().parents[2] / "assets" / name
    return candidate if candidate.is_file() else None


def choose_window_size(screen_w: int, screen_h: int, scale: float = 1.0) -> tuple[int, int, int, int]:
    """按屏幕和界面缩放给出窗口宽、高，以及最小宽、高。"""
    factor = max(1.0, scale)
    avail_w = max(320, screen_w - 32)
    avail_h = max(240, screen_h - 72)
    min_w = min(int(880 * factor), avail_w)
    min_h = min(int(680 * factor), avail_h)
    width = min(max(int(screen_w * 0.62), int(1000 * factor)), int(1440 * factor), avail_w)
    height = min(max(int(screen_h * 0.75), int(780 * factor)), int(1040 * factor), avail_h)
    return max(width, min_w), max(height, min_h), min_w, min_h


def ui_scale(root: tk.Tk) -> float:
    return max(1.0, float(root.tk.call("tk", "scaling")) * 72 / 96)


def enable_process_dpi() -> None:
    """让窗口使用真实像素。重复调用时系统会拒绝，忽略即可。"""
    import ctypes

    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            return


def sync_tk_scaling(root: tk.Tk) -> None:
    root.update_idletasks()
    dpi = _window_dpi(root)
    if dpi <= 0:
        return
    target = dpi / 72.0
    current = float(root.tk.call("tk", "scaling"))
    if abs(current - target) > 0.08:
        root.tk.call("tk", "scaling", target)


def install_dpi_watch(root: tk.Tk, on_change) -> None:
    state = {"dpi": _window_dpi(root)}

    def on_configure(event: tk.Event) -> None:
        if event.widget is not root:
            return
        dpi = _window_dpi(root)
        if dpi <= 0 or dpi == state["dpi"]:
            return
        state["dpi"] = dpi
        target = dpi / 72.0
        current = float(root.tk.call("tk", "scaling"))
        if abs(current - target) > 0.08:
            root.tk.call("tk", "scaling", target)
        on_change()

    root.bind("<Configure>", on_configure, add="+")


def configure_ui_fonts() -> None:
    for name, size in (("TkDefaultFont", 10), ("TkTextFont", 10), ("TkHeadingFont", 10)):
        font = tkfont.nametofont(name)
        font.configure(family="Microsoft YaHei UI", size=size)


def _configure_styles(style: ttk.Style) -> None:
    style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
    style.configure("Hint.TLabel", font=("Microsoft YaHei UI", 10))
    style.configure("Mode.TRadiobutton", font=("Microsoft YaHei UI", 11, "bold"))
    line = tkfont.nametofont("TkDefaultFont").metrics("linespace")
    style.configure("Treeview", rowheight=max(30, line + 12), font=("Microsoft YaHei UI", 10))
    style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))
    style.map(
        "Treeview",
        foreground=_tree_map(style, "foreground") + [("selected", "#ffffff")],
        background=_tree_map(style, "background") + [("selected", TAB_SELECTED_BG)],
    )
    style.layout("TNotebook.Tab", [])
    style.configure("TNotebook", tabmargins=(0, 0, 0, 0), borderwidth=0)


def _tree_map(style: ttk.Style, option: str) -> list:
    """去掉主题里写死的未选中底色，行间隔色才不会被盖住。"""
    return [item for item in style.map("Treeview", query_opt=option) if item[:2] != ("!disabled", "!selected")]


def track_wrap(label: tk.Widget, initial: int, reserve: int = 88) -> None:
    """按窗口实际宽度换行。绑在标签自己身上时，长路径会把换行宽度撑成全文。"""
    host = label.winfo_toplevel()
    label.configure(wraplength=max(120, min(initial, 480)))
    state = {"width": 0}

    def fit(event: tk.Event) -> None:
        if event.widget is not host:
            return
        width = max(120, int(event.width) - reserve)
        if abs(width - state["width"]) < 4:
            return
        state["width"] = width
        label.configure(wraplength=width)

    host.bind("<Configure>", fit, add="+")


def _window_dpi(root: tk.Tk) -> int:
    import ctypes

    try:
        dpi = int(ctypes.windll.user32.GetDpiForWindow(root.winfo_id()))
    except (AttributeError, OSError, ValueError):
        dpi = 0
    if dpi > 0:
        return dpi
    return int(round(root.winfo_fpixels("1i")))


def _place_sash_once(panes: ttk.Panedwindow, ratio: float) -> None:
    state = {"done": False}

    def place(event: tk.Event) -> None:
        if state["done"] or event.widget is not panes or event.width < 100:
            return
        panes.sashpos(0, max(40, int(event.width * ratio)))
        state["done"] = True

    panes.bind("<Configure>", place)


def _text_panel(parent: ttk.Frame, height: int = 8) -> tk.Text:
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    widget = tk.Text(frame, height=height, width=20, wrap="char", font="TkDefaultFont")
    scroll = ttk.Scrollbar(frame, command=widget.yview)
    widget.configure(yscrollcommand=scroll.set)
    widget.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    widget.configure(state="disabled")
    return widget


def _set_text(widget: tk.Text, value: str) -> None:
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", value)
    widget.configure(state="disabled")


def _size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    return f"{size / 1024:.0f} KB"


def _optional_int(value: str, label: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    if not text.isdigit():
        raise SaveToolError(f"{label} 需要填写非负整数。")
    number = int(text)
    if number > 99_999_999:
        raise SaveToolError(f"{label} 太大，已停止。")
    return number
