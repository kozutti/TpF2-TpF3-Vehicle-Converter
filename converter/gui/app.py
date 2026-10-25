"""Responsive GUI. All file processing is kept off Tk's event thread."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import traceback

from converter import __version__
from converter.cli import default_output
from converter.batch import DEFAULT_MOD_WORKERS, MAX_MOD_WORKERS
from converter.transforms.texture_usage import DEFAULT_TEXTURE_WORKERS, MAX_TEXTURE_WORKERS


STATUS_LABELS = {
    "success": "出力生成・静的検証合格", "passed": "静的検証合格",
    "ok": "静的検証合格", "converted": "出力生成・要確認",
    "warning": "出力生成・要確認", "warnings": "出力生成・要確認",
    "needs_review": "出力生成・要確認", "partial": "要手動対応（部分出力）",
    "manual": "要手動対応", "manual_required": "要手動対応",
    "needs_manual": "要手動対応", "blocked": "要手動対応",
    "failed": "失敗", "invalid": "入力エラー", "error": "失敗",
    "cancelled": "取消し完了", "canceled": "取消し完了",
    "valid": "静的検証合格", "analyzed": "解析完了", "planned": "変換計画の生成完了",
}


SINGLES_LABELS = {"hide": "編成専用車を隠す（TPF2と同じ・既定）", "group": "MODごとにまとめる（実験機能）", "show": "そのまま並べる"}


def settings_path() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "TPF3VehicleConverter" / "settings.json"


ROW_STATUS = {"success": "成功", "partial": "要手動対応", "failed": "失敗", "skipped": "対象外", "cancelled": "取消し"}


class ResultsWindow(tk.Toplevel):
    """Per-MOD result list of a batch run."""

    COLUMNS = (("result", "結果", 90), ("folder", "フォルダー", 110), ("title", "MOD名", 260),
               ("source", "取り込み元", 150), ("errors", "エラー", 60), ("note", "備考", 420))

    def __init__(self, parent, summary: dict, open_path):
        super().__init__(parent)
        self.summary, self.open_path = summary, open_path
        self.hide_skipped = tk.StringVar(value="0")
        self.title(f"一括変換の結果  {len(summary['mods'])}件")
        self.geometry("1100x520")
        self.minsize(720, 320)
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        counts = "、".join(f"{ROW_STATUS.get(key, key)} {value}" for key, value in summary["counts"].items())
        ttk.Label(body, text=f"{counts}    （行をダブルクリックで出力フォルダーを開きます）").pack(anchor="w", pady=(0, 8))
        holder = ttk.Frame(body)
        holder.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(holder, columns=[c[0] for c in self.COLUMNS], show="headings", selectmode="browse")
        for key, label, width in self.COLUMNS:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w", stretch=key in {"title", "note"})
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.tag_configure("success", foreground="#1b7a3a")
        self.tree.tag_configure("partial", foreground="#b25a00")
        self.tree.tag_configure("failed", foreground="#b00020")
        self.tree.tag_configure("skipped", foreground="#777777")
        self.tree.bind("<Double-1>", lambda event: self.open_selected())
        bottom = ttk.Frame(body)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text="出力フォルダーを開く", command=self.open_selected).pack(side="left")
        ttk.Button(bottom, text="一覧（Markdown）を開く", command=self.open_summary).pack(side="left", padx=8)
        ttk.Checkbutton(bottom, text="対象外を隠す", variable=self.hide_skipped, onvalue="1", offvalue="0",
                        command=self.fill).pack(side="left", padx=8)
        ttk.Button(bottom, text="閉じる", command=self.destroy).pack(side="right")
        self.paths: dict[str, str] = {}
        self.fill()

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        self.paths.clear()
        for index, row in enumerate(self.summary["mods"]):
            if row["status"] == "skipped" and self.hide_skipped.get() == "1":
                continue
            refs = "、".join(f"{name}（{count}件）" for name, count in row.get("reference_mods", {}).items())
            note = row.get("reason") or ""
            if row["status"] != "skipped" and row.get("contents"):
                note = (note + " 変換対象外の同梱物: " + "、".join(row["contents"])).strip()
            errors = row.get("diagnostics", {}).get("error", "") if row["status"] in {"success", "partial"} else ""
            item = self.tree.insert("", "end", iid=str(index), tags=(row["status"],), values=(
                ROW_STATUS.get(row["status"], row["status"]), row["folder"], row["title"], refs, errors, note))
            target = row.get("package_dir") or row.get("run_dir") or row.get("source")
            if target:
                self.paths[item] = target

    def open_selected(self):
        selection = self.tree.selection()
        if selection and selection[0] in self.paths:
            self.open_path(Path(self.paths[selection[0]]))

    def open_summary(self):
        self.open_path(Path(self.summary["output"]) / "batch_summary.md")


class ConverterWindow:
    def __init__(self, root: tk.Tk, dnd_enabled: bool, sources: list[str]):
        self.root = root
        self.source = tk.StringVar()
        self.output = tk.StringVar(value=str(default_output()))
        self.game_root = tk.StringVar()
        self.singles = tk.StringVar(value=SINGLES_LABELS["hide"])
        self.search_siblings = tk.StringVar(value="1")
        self.texture_workers = tk.StringVar(value=str(DEFAULT_TEXTURE_WORKERS))
        self.mod_workers = tk.StringVar(value=str(DEFAULT_MOD_WORKERS))
        self.state = tk.StringVar(value="MODフォルダまたはZIPをドロップしてください")
        self.events: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.result: dict | None = None
        self.closing = False
        self.action_buttons: list[ttk.Button] = []
        try:
            config = json.loads(settings_path().read_text(encoding="utf-8"))
            if isinstance(config, dict) and isinstance(config.get("output"), str):
                self.output.set(config["output"])
            if isinstance(config, dict) and isinstance(config.get("game_root"), str):
                self.game_root.set(config["game_root"])
            if isinstance(config, dict) and config.get("singles") in SINGLES_LABELS:
                self.singles.set(SINGLES_LABELS[config["singles"]])
            if isinstance(config, dict) and config.get("search_siblings") is False:
                self.search_siblings.set("0")
            if isinstance(config, dict) and type(config.get("texture_workers")) is int and 1 <= config["texture_workers"] <= MAX_TEXTURE_WORKERS:
                self.texture_workers.set(str(config["texture_workers"]))
            if isinstance(config, dict) and type(config.get("mod_workers")) is int and 1 <= config["mod_workers"] <= MAX_MOD_WORKERS:
                self.mod_workers.set(str(config["mod_workers"]))
        except (OSError, ValueError, TypeError):
            pass
        root.title(f"TPF2 → TPF3 車両MOD変換  {__version__}")
        root.geometry("940x760")
        root.minsize(700, 520)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        frame = ttk.Frame(root, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="TPF2 → TPF3 車両MOD変換", font=("Yu Gothic UI", 20, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="車両・ドア・台車・カントなどを解析し、変換候補と確認レポートを作成します。").grid(row=1, column=0, sticky="w", pady=(3, 12))
        self.drop = tk.Label(frame, text="MODフォルダ / ZIP をここにドロップ\nドロップ後、変換を開始します", relief="groove", borderwidth=2,
                             background="#eef4fc", foreground="#18457b", font=("Yu Gothic UI", 13), height=4)
        self.drop.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        if dnd_enabled:
            from tkinterdnd2 import DND_FILES
            self.drop.drop_target_register(DND_FILES)
            self.drop.dnd_bind("<<Drop>>", self.on_drop)
        else:
            self.drop.configure(text="フォルダ/ZIP選択から入力できます\nEXEアイコンへのドロップも利用できます")
        input_row = ttk.Frame(frame)
        input_row.grid(row=3, column=0, sticky="ew")
        input_row.columnconfigure(1, weight=1)
        ttk.Label(input_row, text="入力").grid(row=0, column=0, padx=(0, 10))
        self.source_entry = ttk.Entry(input_row, textvariable=self.source)
        self.source_entry.grid(row=0, column=1, sticky="ew")
        self.add_button(input_row, "フォルダ選択", self.choose_folder, 0, 2)
        self.add_button(input_row, "ZIP選択", self.choose_zip, 0, 3)
        ttk.Label(input_row, text="出力先").grid(row=1, column=0, padx=(0, 10), pady=10)
        self.output_entry = ttk.Entry(input_row, textvariable=self.output)
        self.output_entry.grid(row=1, column=1, sticky="ew")
        self.add_button(input_row, "出力先選択", self.choose_output, 1, 2)
        ttk.Label(input_row, text="TPF2本体").grid(row=2, column=0, padx=(0, 10), pady=6)
        self.game_root_entry = ttk.Entry(input_row, textvariable=self.game_root)
        self.game_root_entry.grid(row=2, column=1, sticky="ew")
        self.game_root_entry.bind("<FocusOut>", lambda event: self.save_settings())
        self.add_button(input_row, "本体フォルダ選択", self.choose_game_root, 2, 2)
        self.add_button(input_row, "設定を解除", self.clear_game_root, 2, 3)
        ttk.Label(input_row, text="任意：不足する標準テクスチャ・音源を本体から読み込みます。元ファイルは変更しません。",
                  wraplength=740).grid(row=3, column=1, columnspan=3, sticky="w", pady=(2, 10))
        ttk.Label(input_row, text="単体車両").grid(row=4, column=0, padx=(0, 10), pady=6)
        singles_box = ttk.Combobox(input_row, textvariable=self.singles, state="readonly",
                                   values=list(SINGLES_LABELS.values()))
        singles_box.grid(row=4, column=1, sticky="ew")
        singles_box.bind("<<ComboboxSelected>>", lambda event: self.save_settings())
        ttk.Label(input_row, text="編成専用の車両の扱い。「隠す」は、TPF2と同じく、編成の中だけに置きます。「MODごとにまとめる」は実験機能で、編成の部品をMODごとに1つの行へ畳み、展開して選べます。",
                  wraplength=740).grid(row=5, column=1, columnspan=3, sticky="w", pady=(2, 4))
        sibling_box = ttk.Checkbutton(input_row, text="同じフォルダーにある他のMODから、不足する資産（メッシュ・音など）を探して取り込む",
                                      variable=self.search_siblings, onvalue="1", offvalue="0", command=self.save_settings)
        sibling_box.grid(row=6, column=1, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Label(input_row, text="並列数").grid(row=7, column=0, padx=(0, 10), pady=6)
        workers_row = ttk.Frame(input_row)
        workers_row.grid(row=7, column=1, columnspan=3, sticky="w")
        ttk.Label(workers_row, text="画像・最遠景LOD").grid(row=0, column=0, padx=(0, 6))
        self.texture_workers_box = ttk.Combobox(workers_row, textvariable=self.texture_workers, state="readonly",
                                                values=[str(n) for n in range(1, MAX_TEXTURE_WORKERS + 1)], width=5)
        self.texture_workers_box.grid(row=0, column=1)
        self.texture_workers_box.bind("<<ComboboxSelected>>", lambda event: self.save_settings())
        ttk.Label(workers_row, text="一括変換のMOD").grid(row=0, column=2, padx=(24, 6))
        self.mod_workers_box = ttk.Combobox(workers_row, textvariable=self.mod_workers, state="readonly",
                                            values=[str(n) for n in range(1, MAX_MOD_WORKERS + 1)], width=5)
        self.mod_workers_box.grid(row=0, column=3)
        self.mod_workers_box.bind("<<ComboboxSelected>>", lambda event: self.save_settings())
        ttk.Label(input_row, text="画像・最遠景LOD：DDS画像を同時変換します。既定は8、1は逐次処理。\n"
                                  "一括変換のMOD：MODを同時に変換します。既定は1（1件ずつ）。各MODが画像の並列数を使うため、プロセス数は両方を掛けた値になります。"
                                  "増やすほどCPUとメモリを使うので、環境に合わせて調整してください。",
                  wraplength=740, justify="left").grid(row=8, column=1, columnspan=3, sticky="w", pady=(2, 10))
        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, sticky="w", pady=(2, 12))
        for i, (text, command) in enumerate((("解析", "analyze"), ("変換計画", "plan"), ("変換する", "convert"))):
            self.add_button(actions, text, lambda cmd=command: self.start(cmd), 0, i)
        self.add_button(actions, "一括変換…", self.choose_batch, 0, 3)
        self.add_button(actions, "出力を検証", self.choose_validate, 0, 4)
        self.cancel_button = ttk.Button(actions, text="取消し", command=self.request_cancel, state="disabled")
        self.cancel_button.grid(row=0, column=5, padx=4)
        ttk.Label(frame, textvariable=self.state, wraplength=840).grid(row=5, column=0, sticky="w", pady=(0, 5))
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.grid(row=6, column=0, sticky="ew", pady=(0, 10))
        frame.rowconfigure(7, weight=1)
        detail = ttk.Frame(frame)
        detail.grid(row=7, column=0, sticky="nsew")
        detail.rowconfigure(0, weight=1)
        detail.columnconfigure(0, weight=1)
        self.log = tk.Text(detail, wrap="word", font=("Yu Gothic UI", 10), state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(detail, orient="vertical", command=self.log.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scrollbar.set)
        bottom = ttk.Frame(frame)
        bottom.grid(row=8, column=0, sticky="ew", pady=(10, 0))
        self.open_button = ttk.Button(bottom, text="結果フォルダを開く", command=self.open_result, state="disabled")
        self.open_button.pack(side="left")
        self.report_button = ttk.Button(bottom, text="レポートを開く", command=self.open_report, state="disabled")
        self.report_button.pack(side="left", padx=8)
        self.list_button = ttk.Button(bottom, text="結果一覧を開く", command=self.show_results, state="disabled")
        self.list_button.pack(side="left")
        ttk.Label(frame, text="ゲーム内動作は未検証です。生成後、モデルエディタとゲームで確認してください。", foreground="#9b4f16").grid(row=9, column=0, sticky="w", pady=(10, 0))
        root.after(100, self.poll)
        if sources:
            root.after(250, lambda: self.accept_sources(sources))

    def add_button(self, parent, text, command, row, column):
        button = ttk.Button(parent, text=text, command=command)
        button.grid(row=row, column=column, padx=4)
        self.action_buttons.append(button)
        return button

    def append(self, message: str):
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        # Keep the text widget bounded on large MODs and repeated runs.
        if int(self.log.index("end-1c").split(".")[0]) > 1800:
            self.log.delete("1.0", "400.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def accept_sources(self, paths):
        if self.worker and self.worker.is_alive():
            self.state.set("処理中です。完了または取消し後に次のMODをドロップしてください。")
            return
        if len(paths) != 1:
            messagebox.showinfo("入力", "MODフォルダまたはZIPを1つずつドロップしてください。", parent=self.root)
            return
        dropped = Path(paths[0])
        if dropped.is_dir() and not (dropped / "mod.lua").is_file():
            from converter.batch import find_mods
            count = len(find_mods(dropped))
            if count > 1 and messagebox.askyesno("一括変換", f"このフォルダーには{count}個のMODがあります。まとめて変換しますか？\n"
                                                    "（車両以外のMODは自動で対象外にします）", parent=self.root):
                self.source.set(str(dropped))
                self.start("batch")
                return
        self.source.set(str(dropped))
        self.start("convert")

    def on_drop(self, event):
        self.accept_sources(list(self.root.tk.splitlist(event.data)))
        return event.action

    def choose_folder(self):
        path = filedialog.askdirectory(title="TPF2 MODフォルダを選択", parent=self.root)
        if path:
            self.source.set(path)

    def choose_zip(self):
        path = filedialog.askopenfilename(title="TPF2 MOD ZIPを選択", filetypes=[("ZIP", "*.zip")], parent=self.root)
        if path:
            self.source.set(path)

    def choose_output(self):
        path = filedialog.askdirectory(title="変換結果の出力先", parent=self.root)
        if path:
            self.output.set(path)

    def choose_game_root(self):
        path = filedialog.askdirectory(title="Transport Fever 2 本体のインストールフォルダを選択", parent=self.root,
                                       initialdir=self.game_root.get() or None)
        if path:
            self.game_root.set(path)
            self.save_settings()

    def clear_game_root(self):
        self.game_root.set("")
        self.save_settings()

    def singles_mode(self) -> str:
        return next((key for key, label in SINGLES_LABELS.items() if label == self.singles.get()), "hide")

    def save_settings(self):
        try:
            config_path = settings_path()
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps({"output": self.output.get().strip(),
                                               "game_root": self.game_root.get().strip(),
                                               "singles": self.singles_mode(),
                                               "search_siblings": self.search_siblings.get() == "1",
                                               "texture_workers": int(self.texture_workers.get()),
                                               "mod_workers": int(self.mod_workers.get())}, ensure_ascii=False),
                                   encoding="utf-8")
        except OSError as error:
            self.append(f"設定の保存を省略しました: {error}")

    def choose_batch(self):
        path = filedialog.askdirectory(title="MODを含むフォルダーを選択（例：Steam Workshopのcontent/1066780、TPF2のmods）", parent=self.root)
        if path:
            self.source.set(path)
            self.start("batch", path)

    def choose_validate(self):
        path = filedialog.askdirectory(title="TPF3出力MODのフォルダを選択", parent=self.root)
        if path:
            self.start("validate", path)

    def start(self, command: str, source_override=None):
        if self.worker and self.worker.is_alive():
            return
        raw = source_override or self.source.get().strip()
        if not raw:
            messagebox.showinfo("入力", "MODフォルダまたはZIPを選択してください。", parent=self.root)
            return
        source = Path(raw)
        if not source.exists():
            messagebox.showerror("入力", f"入力が見つかりません。\n{source}", parent=self.root)
            return
        if not self.output.get().strip():
            messagebox.showerror("出力先", "出力先を指定してください。", parent=self.root)
            return
        output = Path(self.output.get().strip())
        game_raw = self.game_root.get().strip()
        game_root = Path(game_raw) if game_raw and command != "validate" else None
        if game_root is not None and not game_root.is_dir():
            messagebox.showerror("TPF2本体", f"本体フォルダが見つかりません。\n{game_root}", parent=self.root)
            return
        self.cancel = threading.Event()
        self.result = None
        self.open_button.configure(state="disabled")
        self.report_button.configure(state="disabled")
        for button in self.action_buttons:
            button.configure(state="disabled")
        self.source_entry.configure(state="disabled")
        self.output_entry.configure(state="disabled")
        self.game_root_entry.configure(state="disabled")
        self.texture_workers_box.configure(state="disabled")
        self.mod_workers_box.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        labels = {"analyze": "解析", "plan": "計画生成", "convert": "変換", "batch": "一括変換", "validate": "検証"}
        self.state.set(f"{labels[command]}中…")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.append(f"\n{labels[command]}: {source}\n出力先: {output}")
        if game_root is not None:
            self.append(f"TPF2本体フォルダ: {game_root}")
        single_mode = self.singles_mode()
        search = self.search_siblings.get() == "1"
        texture_workers = int(self.texture_workers.get())
        mod_workers = int(self.mod_workers.get())
        if command in {"convert", "batch"}:
            self.append(f"単体車両の扱い: {self.singles.get()}")
            self.append(f"画像の並列数: {texture_workers}")
        if command == "batch":
            self.append(f"MODの並列数: {mod_workers}")
        self.save_settings()

        def progress_callback(*args, **kwargs):
            self.events.put(("progress", (args, kwargs)))

        def work():
            try:
                from converter import pipeline
                operation = getattr(pipeline, command)
                if command == "convert":
                    result = operation(source, output, progress=progress_callback, cancel=self.cancel, game_root=game_root,
                                       single_vehicles=single_mode, siblings="auto" if search else None,
                                       texture_workers=texture_workers, mods_dir=Path(output) / "mods_ready")
                elif command == "batch":
                    result = operation(source, output, progress=progress_callback, cancel=self.cancel, game_root=game_root,
                                       single_vehicles=single_mode, search_siblings=search, texture_workers=texture_workers,
                                       mod_workers=mod_workers)
                elif command == "validate":
                    result = operation(source)
                else:
                    result = operation(source, progress=progress_callback, cancel=self.cancel, game_root=game_root)
                self.events.put(("result", result))
            except Exception as error:
                self.events.put(("failure", (str(error), traceback.format_exc())))
        self.worker = threading.Thread(target=work, name="conversion-worker", daemon=True)
        self.worker.start()

    def request_cancel(self):
        self.cancel.set()
        self.cancel_button.configure(state="disabled")
        self.state.set("取消しを要求しました。現在の処理が停止するまでお待ちください。")

    def finish(self):
        self.progress.stop()
        self.cancel_button.configure(state="disabled")
        for button in self.action_buttons:
            button.configure(state="normal")
        self.source_entry.configure(state="normal")
        self.output_entry.configure(state="normal")
        self.game_root_entry.configure(state="normal")
        self.texture_workers_box.configure(state="readonly")
        self.mod_workers_box.configure(state="readonly")

    def poll(self):
        try:
            for _ in range(150):
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    args, kwargs = value
                    event = args[0] if len(args) == 1 and isinstance(args[0], dict) else kwargs
                    message = event.get("message") or event.get("stage") or " / ".join(str(item) for item in args)
                    if message:
                        if not self.cancel.is_set():
                            self.state.set(str(message))
                        self.append(str(message))
                elif kind == "result":
                    self.finish()
                    self.result = value
                    status = str(value.get("status", "完了"))
                    self.state.set(STATUS_LABELS.get(status.lower(), f"処理完了: {status}"))
                    summary = {key: item for key, item in value.items()
                               if key not in {"parsed", "files", "mapping", "items", "resources"}}
                    if "files" in value:
                        summary["解析ファイル数"] = len(value["files"])
                    if "items" in value:
                        summary["計画項目数"] = len(value["items"])
                    self.append(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
                    if value.get("run_dir"):
                        self.open_button.configure(state="normal")
                        self.report_button.configure(state="normal")
                    if isinstance(value.get("batch"), dict):
                        from converter.batch import format_table
                        self.append("\n" + format_table(value["batch"]))
                        self.list_button.configure(state="normal")
                        self.show_results()
                    self.append("ゲーム内動作は未検証です。ドア、曲線・カント、台車、車輪、LODなどを確認してください。")
                elif kind == "failure":
                    self.finish()
                    self.state.set("処理に失敗しました。詳細を確認してください。")
                    self.append(value[1])
        except queue.Empty:
            pass
        if self.closing and not (self.worker and self.worker.is_alive()):
            self.root.destroy()
        else:
            self.root.after(100, self.poll)

    def open_path(self, path: Path):
        try:
            os.startfile(str(path))
        except OSError as error:
            messagebox.showerror("開けません", str(error), parent=self.root)

    def open_result(self):
        if self.result and self.result.get("run_dir"):
            self.open_path(Path(self.result["run_dir"]))

    def open_report(self):
        if self.result and self.result.get("run_dir"):
            name = "batch_summary.md" if self.result.get("batch") else "reports/conversion_report.md"
            report = Path(self.result["run_dir"]) / name
            self.open_path(report if report.exists() else report.parent)

    def show_results(self):
        """Result list of the last batch: one row per MOD, double-click opens its output."""
        if not (self.result and isinstance(self.result.get("batch"), dict)):
            return
        existing = getattr(self, "results_window", None)
        try:
            if existing is not None and existing.winfo_exists():
                existing.destroy()
        except tk.TclError:
            pass
        self.results_window = ResultsWindow(self.root, self.result["batch"], self.open_path)

    def on_close(self):
        if self.worker and self.worker.is_alive():
            self.closing = True
            self.request_cancel()
            self.state.set("取消し後に終了します。処理停止までお待ちください。")
        else:
            self.save_settings()
            self.root.destroy()


def launch(sources: list[str], smoke_test=False):
    dnd_enabled = False
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
        dnd_enabled = True
    except (ImportError, RuntimeError, tk.TclError):
        root = tk.Tk()
    if smoke_test:
        root.withdraw()
    ttk.Style(root).theme_use("vista" if "vista" in ttk.Style(root).theme_names() else "clam")
    app = ConverterWindow(root, dnd_enabled, [] if smoke_test else sources)
    if smoke_test:
        try:
            from converter import pipeline
            if not dnd_enabled:
                raise RuntimeError("D&D library not available")
            assert len(root.tk.splitlist(r'{C:\日本語 MOD\車両_1}')) == 1
            app.events.put(("progress", (({"message": "起動確認"},), {})))
            app.events.put(("result", {"status": "cancelled", "diagnostics": []}))
            app.poll()
            root.update_idletasks()
            assert app.state.get() == "取消し完了"
            return {"status": "smoke_passed", "dnd": dnd_enabled,
                    "tkdnd": root.tk.call("package", "provide", "tkdnd"),
                    "tk": root.tk.call("package", "provide", "Tk"), "pipeline_import": bool(pipeline)}
        finally:
            root.destroy()
    root.mainloop()
