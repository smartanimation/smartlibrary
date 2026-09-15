"""Tk review dialog for explicit cut-to-work-shot assignments."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from smartlib.editorial.cut_assignment import default_assignments, compile_assignments


class CutAssignmentDialog:
    def __init__(self, parent, events, shots, *, saved=None, offline_origin=0, dry_run=False, editorial_unit=None):
        self.events = events
        self.editorial_unit = editorial_unit
        self.rows = default_assignments(events, shots)
        self.result = None
        self.window = tk.Toplevel(parent)
        self.window.title("Editorial Cut Assignment")
        self.window.geometry("1260x660")
        self.window.transient(parent)
        self.origin = tk.StringVar(value=str(offline_origin))
        self.confirmed = tk.BooleanVar(value=False)
        if saved and [r.get("signature") for r in saved.get("rows", [])] == [r["signature"] for r in self.rows]:
            self.rows = [{**default, **r} for default, r in zip(self.rows, saved["rows"])]
            self.origin.set(str(saved.get("offline_origin", offline_origin)))
        restored = saved and [r.get("signature") for r in saved.get("rows", [])] == [r["signature"] for r in self.rows]
        for row in self.rows:
            row["production_sequence"] = row.pop("production_sequence", row.get("sequence", "") if restored or editorial_unit is None else "")
            row.pop("sequence", None)
        frame = ttk.Frame(self.window, padding=12)
        frame.pack(fill="both", expand=True)
        if editorial_unit is not None:
            ttk.Label(frame, text=f"Editorial Unit: {editorial_unit} → Production Sequenceを各行に指定してください").pack(anchor="w")
        ttk.Label(frame, text="編集位置は現在のResolveマーカーを使用します。範囲は両端を含みます。").pack(anchor="w")
        ttk.Label(frame, text="シーケンス・作業ショット・Maya欄はダブルクリックで編集できます。編集位置は受領動画内の位置を維持します。").pack(anchor="w", pady=(0, 8))
        origin_row = ttk.Frame(frame)
        origin_row.pack(fill="x")
        ttk.Label(origin_row, text="Offlineの先頭フレームに対応するタイムラインフレーム").pack(side="left")
        ttk.Entry(origin_row, textvariable=self.origin, width=12).pack(side="left", padx=8)
        ttk.Label(origin_row, text="例: 動画がtimeline 0からなら 0。編集開始120でも動画先頭が0なら 0。").pack(side="left")
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=8)
        for label, action in (("全て出力", lambda: self.enable(True)), ("出力解除", lambda: self.enable(False)),
                              ("選択行を同じ作業ショットへ", self.assign_selected),
                              ("選択行を同じシーケンスへ", self.assign_sequence),
                              ("選択行をMaya上で連続配置", self.place_selected)):
            ttk.Button(bar, text=label, command=action).pack(side="left", padx=(0, 8))
        table = ttk.Frame(frame)
        table.pack(fill="both", expand=True)
        self.columns = ("enabled", "cut", "record_in", "record_out", "offline_in", "offline_out", "production_sequence", "work_shot", "maya_in", "maya_out")
        self.tree = ttk.Treeview(table, columns=self.columns, show="headings", selectmode="extended")
        for key, title, width in zip(self.columns,
            ("出力", "編集カット", "編集 In", "編集 Out", "Offline In", "Offline Out", "Production Sequence", "作業ショット", "Maya In", "Maya Out"),
            (50, 95, 95, 95, 95, 95, 110, 145, 95, 95)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=45)
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self.toggle)
        self.tree.bind("<Double-1>", self.edit)
        self.context_menu = tk.Menu(self.window, tearoff=False)
        self.context_menu.add_command(label="選択行のProduction Sequenceを一括設定…", command=self.assign_sequence)
        self.tree.bind("<Button-3>", self.show_context_menu)
        self.origin.trace_add("write", lambda *_: self.origin_changed())
        ttk.Checkbutton(frame, variable=self.confirmed,
            text="選択カットの編集ポイントとOffline動画の対応を確認済み（全体尺の一致は不要）").pack(anchor="w", pady=12)
        self.status = tk.StringVar()
        ttk.Label(frame, textvariable=self.status).pack(anchor="w")
        bottom = ttk.Frame(frame)
        bottom.pack(fill="x", pady=(8, 0))
        ttk.Button(bottom, text="Cancel", command=self.window.destroy).pack(side="right")
        ttk.Button(bottom, text="Preflight Selected" if dry_run else "Export & Intake Selected",
                   command=self.accept).pack(side="right", padx=8)
        self.refresh()

    def show(self):
        self.window.grab_set()
        self.window.wait_window()
        return self.result

    def refresh(self):
        try:
            origin = int(self.origin.get())
        except ValueError:
            origin = 0
        for i, row in enumerate(self.rows):
            values = {**row, "enabled": "✓" if row["enabled"] else "",
                      "offline_in": row["record_in"] - origin,
                      "offline_out": row["record_out"] - origin}
            values = [values[k] for k in self.columns]
            if self.tree.exists(str(i)):
                self.tree.item(str(i), values=values)
            else:
                self.tree.insert("", "end", iid=str(i), values=values)
        chosen = [r for r in self.rows if r["enabled"]]
        self.status.set(f"出力: {len(chosen)} cuts / {len({r['production_sequence'] for r in chosen if r['production_sequence']})} sequences / 未割当 {sum(not r['production_sequence'] for r in chosen)} cuts")

    def enable(self, value):
        for row in self.rows:
            row["enabled"] = value
        self.refresh()

    def origin_changed(self):
        self.confirmed.set(False)
        self.refresh()

    def toggle(self, event):
        item = self.tree.identify_row(event.y)
        if item and self.tree.identify_column(event.x) == "#1":
            self.rows[int(item)]["enabled"] = not self.rows[int(item)]["enabled"]
            self.refresh()

    def edit(self, event):
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not item or column not in ("#7", "#8", "#9", "#10"):
            return
        key = self.columns[int(column[1:]) - 1]
        row = self.rows[int(item)]
        value = simpledialog.askstring("Edit " + key, key, initialvalue=str(row[key]), parent=self.window)
        if value is None:
            return
        try:
            row[key] = value.strip() if key in ("production_sequence", "work_shot") else int(value)
            if key == "maya_in":
                row["maya_out"] = row["maya_in"] + row["record_out"] - row["record_in"]
        except ValueError:
            messagebox.showerror("Invalid frame", "整数のフレーム番号を入力してください。", parent=self.window)
        self.refresh()

    def assign_selected(self):
        selected = self.tree.selection()
        if not selected:
            return
        name = simpledialog.askstring("Work Shot", "選択行の作業ショット名", parent=self.window)
        if name:
            for item in selected:
                self.rows[int(item)]["work_shot"] = name.strip()
            self.refresh()

    def place_selected(self):
        selected = sorted(map(int, self.tree.selection()))
        if not selected:
            return
        start = simpledialog.askinteger("Maya Range", "連続配置の開始フレーム", initialvalue=self.rows[selected[0]]["maya_in"], parent=self.window)
        if start is None:
            return
        for i in selected:
            row = self.rows[i]
            row["maya_in"] = start
            row["maya_out"] = start + row["record_out"] - row["record_in"]
            start = row["maya_out"] + 1
        self.refresh()

    def show_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if not item:
            return "break"
        # Keep the multi-selection when opening the menu on any selected row.
        if item not in self.tree.selection():
            self.tree.selection_set(item)
        self.tree.focus(item)
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()
        return "break"

    def assign_sequence(self):
        selected = self.tree.selection()
        if not selected:
            return
        names = {self.rows[int(item)]["production_sequence"] for item in selected}
        name = simpledialog.askstring(
            "Production Sequence 一括設定",
            f"選択した {len(selected)} カットの出力先シーケンス名",
            initialvalue=next(iter(names)) if len(names) == 1 else "",
            parent=self.window,
        )
        if name is not None:
            for item in selected:
                self.rows[int(item)]["production_sequence"] = name.strip()
            self.refresh()

    def accept(self):
        try:
            plan = {"schema": "smartpipeline.cut_assignment.v1", "rows": self.rows,
                    "offline_origin": int(self.origin.get()), "alignment_confirmed": self.confirmed.get()}
            if self.editorial_unit is not None:
                plan["editorial_unit"] = self.editorial_unit
            compile_assignments(self.events, plan)
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Cut Assignment", str(exc), parent=self.window)
            return
        self.result = plan
        self.window.destroy()
