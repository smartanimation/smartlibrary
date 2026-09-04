from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from smartlib.apps.editorial_intake.service import SmartEditorialIntakeService
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.icons import tool_ico_path


class EditorialIntakeWindow(tk.Tk):
    def __init__(self, service: SmartEditorialIntakeService):
        super().__init__()
        self.service = service
        self.title("Smart Editorial Intake")
        icon_path = tool_ico_path("smart_editorial")
        if icon_path:
            self.iconbitmap(default=str(icon_path))
        self.geometry("310x500")
        self.minsize(290, 460)

        self.episode_var = tk.StringVar()
        self.sequence_var = tk.StringVar()
        self.version_var = tk.StringVar(value="Latest")
        self.csv_var = tk.StringVar()
        self.mov_var = tk.StringVar()
        self.comment_var = tk.StringVar(value="editorial intake")
        self.create_folders_var = tk.BooleanVar(value=True)
        self.storyreel_var = tk.BooleanVar(value=True)
        self.dry_run_var = tk.BooleanVar(value=False)
        self._last_output_dir: Path | None = None

        self._build_ui()
        self._load_initial_values()
        self.refresh_report()

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 3}
        self.columnconfigure(1, weight=1)

        ttk.Label(self, text="Episode").grid(row=0, column=0, sticky="w", **pad)
        self.episode_combo = ttk.Combobox(self, textvariable=self.episode_var, state="readonly")
        self.episode_combo.grid(row=0, column=1, sticky="ew", **pad)

        ttk.Label(self, text="Sequence").grid(row=1, column=0, sticky="w", **pad)
        self.sequence_combo = ttk.Combobox(self, textvariable=self.sequence_var, state="readonly")
        self.sequence_combo.grid(row=1, column=1, sticky="ew", **pad)

        ttk.Label(self, text="Version").grid(row=2, column=0, sticky="w", **pad)
        self.version_combo = ttk.Combobox(self, textvariable=self.version_var, state="readonly")
        self.version_combo.grid(row=2, column=1, sticky="ew", **pad)

        file_row = ttk.Frame(self)
        file_row.grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 2))
        file_row.columnconfigure(0, weight=1)
        ttk.Button(file_row, text="CSV...", command=self.pick_csv).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(file_row, text="MOV...", command=self.pick_mov).grid(row=0, column=1, sticky="ew")

        ttk.Label(self, text="Inspect report").grid(row=4, column=0, columnspan=2, sticky="w", **pad)
        self.report = tk.Text(self, height=13, wrap="word")
        self.report.grid(row=5, column=0, columnspan=2, sticky="nsew", padx=8, pady=3)
        self.rowconfigure(5, weight=1)

        ttk.Label(self, text="Comment").grid(row=6, column=0, sticky="w", **pad)
        ttk.Entry(self, textvariable=self.comment_var).grid(row=6, column=1, sticky="ew", **pad)

        ttk.Checkbutton(self, text="Create Folder Structure", variable=self.create_folders_var).grid(row=7, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2))
        ttk.Checkbutton(self, text="Generate Storyreel / Thumbnail", variable=self.storyreel_var).grid(row=8, column=0, columnspan=2, sticky="w", padx=8, pady=2)

        action = ttk.Frame(self)
        action.grid(row=9, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 4))
        action.columnconfigure(0, weight=1)
        ttk.Button(action, text="editorial intake", command=self.run_intake).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Checkbutton(action, text="dry-run", variable=self.dry_run_var, command=self.refresh_report).grid(row=0, column=1, sticky="e")

        bottom = ttk.Frame(self)
        bottom.grid(row=10, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))
        bottom.columnconfigure(0, weight=1)
        ttk.Button(bottom, text="Refresh", command=self.refresh_report).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(bottom, text="Open Folder", command=self.open_output_folder).grid(row=0, column=1, sticky="ew")

        self.episode_combo.bind("<<ComboboxSelected>>", self._episode_changed)
        self.sequence_combo.bind("<<ComboboxSelected>>", self._sequence_changed)
        self.version_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_report())

    def _load_initial_values(self) -> None:
        episodes = self.service.list_episodes()
        self.episode_combo["values"] = episodes
        self.episode_var.set(episodes[0])
        self._update_sequences()

    def _episode_changed(self, _event: object | None = None) -> None:
        self._update_sequences()
        self.refresh_report()

    def _sequence_changed(self, _event: object | None = None) -> None:
        self._update_versions()
        self.refresh_report()

    def _update_sequences(self) -> None:
        sequences = self.service.list_sequences(self.episode_var.get())
        self.sequence_combo["values"] = sequences
        self.sequence_var.set(sequences[0])
        self._update_versions()

    def _update_versions(self) -> None:
        versions = self.service.list_versions(self.episode_var.get(), self.sequence_var.get())
        self.version_combo["values"] = versions
        self.version_var.set(versions[0])

    def pick_csv(self) -> None:
        path = filedialog.askopenfilename(
            title="Select events CSV",
            initialdir=str(self.service.incoming_editorial_dir),
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
        )
        if path:
            self.csv_var.set(path)
            self.refresh_report()

    def pick_mov(self) -> None:
        path = filedialog.askopenfilename(
            title="Select offline movie",
            initialdir=str(self.service.incoming_editorial_dir),
            filetypes=[("Movie", "*.mov *.mp4"), ("All files", "*.*")],
        )
        if path:
            self.mov_var.set(path)
            self.refresh_report()

    def refresh_report(self) -> None:
        preview = self.service.inspect(
            self.episode_var.get(),
            self.sequence_var.get(),
            self.version_var.get(),
            csv_path=self.csv_var.get() or None,
            mov_path=self.mov_var.get() or None,
        )
        self._set_report(preview.report)
        if preview.source:
            self._last_output_dir = preview.source.work_dir or preview.source.csv_path.parent

    def run_intake(self) -> None:
        try:
            result = self.service.run(
                self.episode_var.get(),
                self.sequence_var.get(),
                self.version_var.get(),
                csv_path=self.csv_var.get() or None,
                mov_path=self.mov_var.get() or None,
                comment=self.comment_var.get(),
                create_folder_structure=self.create_folders_var.get(),
                generate_storyreel=self.storyreel_var.get(),
                dry_run=self.dry_run_var.get(),
            )
        except Exception as exc:
            messagebox.showerror("Smart Editorial Intake", str(exc))
            return
        self._set_report(result.report)
        if result.intake and result.intake.publish_dir:
            self._last_output_dir = result.intake.publish_dir
        self._update_versions()
        messagebox.showinfo("Smart Editorial Intake", "Editorial intake finished.")

    def open_output_folder(self) -> None:
        if not self._last_output_dir:
            return
        path = self._last_output_dir
        if not path.exists():
            path = path.parent
        try:
            os.startfile(str(path))
        except Exception as exc:
            messagebox.showerror("Open Folder", str(exc))

    def _set_report(self, lines: list[str]) -> None:
        self.report.delete("1.0", tk.END)
        self.report.insert("1.0", "\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    _ = argv
    config_dir = os.environ.get("PROJECT_CONFIG_DIR")
    if not config_dir:
        root = Path(os.environ.get("SMARTPIPELINE_ROOT") or Path(__file__).resolve().parents[4])
        config_dir = str(root / "config" / "STKB")
    service = SmartEditorialIntakeService(ProjectConfig(config_dir))
    window = EditorialIntakeWindow(service)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
