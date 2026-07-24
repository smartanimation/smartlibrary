from __future__ import annotations

import json
import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from smartlib.core.config_loader import ProjectConfig
from smartlib.dcc.resolve.export_timeline_csv import (
    create_cutting_markers_from_timeline,
    current_timeline_media_info,
    editorial_work_sequence_dir,
    editorial_work_versions,
    export_marker_events_to_work,
    ingested_editorial_files,
    latest_editorial_work_version_dir,
    latest_ingested_offline_movie,
    next_editorial_work_version_dir,
    resolve_project_manifest_data,
    shot_naming_profile_names,
    shot_naming_rule,
    stage_editorial_source,
)


_WINDOW = None


class ResolveTimelineExportWindow:
    def __init__(self, config_dir: str | os.PathLike[str] | None = None, resolve_app=None):
        self.resolve_app = resolve_app
        self.project_config = ProjectConfig(config_dir or os.environ.get("PROJECT_CONFIG_DIR", ""))
        self.current_work_dir: Path | None = None
        self.selected_movie_path: Path | None = None
        self._updating_versions = False
        self._updating_episode_sequence = False

        self.root = tk.Tk()
        self.root.title("Smart Editorial Export")
        self.root.geometry("340x560")
        self.root.resizable(True, True)
        self._build_menu()

        self.episode_var = tk.StringVar(value="ep001")
        self.sequence_var = tk.StringVar(value="sq010")
        self.version_var = tk.StringVar(value="Latest")
        self.handle_head_var = tk.IntVar(value=12)
        self.handle_tail_var = tk.IntVar(value=12)
        self.track_var = tk.IntVar(value=1)
        self.cut_start_var = tk.IntVar(value=1001)
        self.shot_naming_profile_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="")
        self.media_vars = {
            "Video Codec": tk.StringVar(value="-"),
            "Audio Codec": tk.StringVar(value="-"),
            "Resolution": tk.StringVar(value="-"),
            "Frame Rate": tk.StringVar(value="-"),
            "Duration": tk.StringVar(value="-"),
            "Color Space": tk.StringVar(value="-"),
        }

        self._build()
        self.refresh()

    def show(self):
        self.root.mainloop()
        return self

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        form = ttk.Frame(frame)
        form.pack(fill=tk.X)
        self._labeled_combo(form, "episode", self.episode_var, 0, "episode_combo", self._episode_changed)
        self._labeled_combo(form, "sequence", self.sequence_var, 1, "sequence_combo", self._sequence_changed)
        self._labeled_version(form, 2)

        movie_row = ttk.Frame(frame)
        movie_row.pack(fill=tk.X, pady=(10, 4))
        ttk.Button(movie_row, text="MOV", command=self.pick_manifest_movie).pack(side=tk.LEFT)
        self.movie_label_var = tk.StringVar(value="No MOV selected")
        ttk.Label(movie_row, textvariable=self.movie_label_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        media = ttk.LabelFrame(frame, text="media info", padding=8)
        media.pack(fill=tk.X, pady=(4, 8))
        for row, (label, value_var) in enumerate(self.media_vars.items()):
            ttk.Label(media, text=label).grid(row=row, column=0, sticky=tk.W, padx=(0, 10), pady=2)
            ttk.Label(media, textvariable=value_var).grid(row=row, column=1, sticky=tk.W, pady=2)

        options = ttk.LabelFrame(frame, text="OPTION", padding=8)
        options.pack(fill=tk.X, pady=(4, 8))
        self._labeled_spin(options, "handle_head", self.handle_head_var, 0, 0, 999)
        self._labeled_spin(options, "handle_tail", self.handle_tail_var, 1, 0, 999)
        self._labeled_spin(options, "track_index", self.track_var, 2, 1, 99)
        self._labeled_spin(options, "cut_start_frame", self.cut_start_var, 3, 0, 999999)
        self._labeled_combo(options, "shot_naming", self.shot_naming_profile_var, 4, "shot_naming_combo", self._shot_naming_changed)

        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X, pady=(2, 8))
        ttk.Button(buttons, text="Refresh", command=self.refresh).pack(side=tk.LEFT)
        ttk.Button(buttons, text="New Version", command=self.new_version).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(buttons, text="Save", command=self.save).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        ttk.Button(buttons, text="Open Folder", command=self.open_output_folder).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(frame, textvariable=self.status_var, wraplength=300).pack(fill=tk.X)
        self._refresh_episode_sequence()
        self._refresh_versions()

    def _build_menu(self) -> None:
        menu_bar = tk.Menu(self.root)
        file_menu = tk.Menu(menu_bar, tearoff=False)
        file_menu.add_command(label="New Episode", command=self.new_episode)
        file_menu.add_command(label="New Sequence", command=self.new_sequence)
        file_menu.add_separator()
        file_menu.add_command(label="Stage MOV", command=self.stage_mov)
        file_menu.add_command(label="Stage AAF", command=lambda: self.stage_reference("aaf"))
        file_menu.add_command(label="Stage XML", command=lambda: self.stage_reference("xml"))
        file_menu.add_command(label="Stage EDL", command=lambda: self.stage_reference("edl"))
        menu_bar.add_cascade(label="File", menu=file_menu)

        marker_menu = tk.Menu(menu_bar, tearoff=False)
        marker_menu.add_command(label="new cutting marker", command=self.new_cutting_marker)
        menu_bar.add_cascade(label="Makers", menu=marker_menu)
        self.root.config(menu=menu_bar)

    @staticmethod
    def _labeled_entry(parent, label: str, variable: tk.StringVar, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, padx=(0, 10), pady=3)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky=tk.EW, pady=3)
        parent.columnconfigure(1, weight=1)

    def _labeled_combo(self, parent, label: str, variable: tk.StringVar, row: int, attr_name: str, callback) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, padx=(0, 10), pady=3)
        combo = ttk.Combobox(parent, textvariable=variable, state="readonly")
        combo.grid(row=row, column=1, sticky=tk.EW, pady=3)
        combo.bind("<<ComboboxSelected>>", callback)
        setattr(self, attr_name, combo)
        parent.columnconfigure(1, weight=1)

    @staticmethod
    def _labeled_spin(parent, label: str, variable: tk.IntVar, row: int, minimum: int, maximum: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, padx=(0, 10), pady=3)
        ttk.Spinbox(parent, from_=minimum, to=maximum, textvariable=variable, width=12).grid(
            row=row,
            column=1,
            sticky=tk.EW,
            pady=3,
        )
        parent.columnconfigure(1, weight=1)

    def _labeled_version(self, parent, row: int) -> None:
        ttk.Label(parent, text="version").grid(row=row, column=0, sticky=tk.W, padx=(0, 10), pady=3)
        version_row = ttk.Frame(parent)
        version_row.grid(row=row, column=1, sticky=tk.EW, pady=3)
        version_row.columnconfigure(0, weight=1)
        self.version_combo = ttk.Combobox(version_row, textvariable=self.version_var, state="readonly", width=12)
        self.version_combo.grid(row=0, column=0, sticky=tk.EW)
        self.version_combo.bind("<<ComboboxSelected>>", lambda _event: self._version_changed())

    def refresh(self) -> None:
        try:
            info = current_timeline_media_info(self.resolve_app, self.track_var.get())
            for key, variable in self.media_vars.items():
                variable.set(info.get(key) or "-")
            self._refresh_episode_sequence(keep_current=True)
            self._refresh_versions(keep_current=True)
            self.status_var.set("Timeline info refreshed")
        except Exception as exc:
            self.status_var.set(str(exc))

    def save(self) -> None:
        episode = self.episode_var.get().strip()
        sequence = self.sequence_var.get().strip()
        if not episode or not sequence:
            messagebox.showwarning("Export Timeline CSV", "episode and sequence are required.")
            return
        try:
            manifest_data = self._manifest_data()
            path = export_marker_events_to_work(
                project_config=self.project_config,
                episode=episode,
                sequence=sequence,
                resolve_app=self.resolve_app,
                work_dir=self._selected_work_dir(),
                handle_head=self.handle_head_var.get(),
                handle_tail=self.handle_tail_var.get(),
                cut_start_frame=self.cut_start_var.get(),
                shot_naming_profile=self.shot_naming_profile_var.get().strip() or None,
                manifest_data=manifest_data,
            )
            self.status_var.set(f"Saved: {path}")
            self.current_work_dir = path.parent
            self.version_var.set(path.parent.name)
            self._refresh_versions(keep_current=True)
        except Exception as exc:
            messagebox.showerror("Export Timeline CSV Failed", str(exc))

    def open_output_folder(self) -> None:
        folder = self.current_work_dir
        if not folder:
            episode = self.episode_var.get().strip() or "episode"
            sequence = self.sequence_var.get().strip() or "sequence"
            try:
                folder = self._selected_work_dir()
            except Exception as exc:
                messagebox.showerror("Open Folder Failed", str(exc))
                return
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))
        except Exception as exc:
            messagebox.showerror("Open Folder Failed", str(exc))

    def stage_mov(self) -> None:
        movie = self._ask_movie()
        if not movie:
            return
        self._set_manifest_movie(movie)
        self._stage(movie_path=movie)

    def stage_reference(self, reference_type: str) -> None:
        episode = self.episode_var.get().strip()
        sequence = self.sequence_var.get().strip()
        reference = self._ingested_reference(episode, sequence, reference_type)
        if not reference:
            return
        movie = latest_ingested_offline_movie(self.project_config, episode, sequence)
        if movie is None:
            messagebox.showinfo(
                "Offline Movie",
                f"No ingested offline movie was found for {episode}/{sequence}.\n"
                "Select a movie manually.",
            )
            movie = self._ask_movie()
        if not movie:
            return
        self._set_manifest_movie(movie)
        answer = messagebox.askyesno(
            f"Stage {reference_type.upper()}",
            f"Construct a Resolve timeline from the ingested editorial source?\n\n"
            f"Reference:\n{reference}\n\n"
            f"Offline:\n{movie}",
        )
        if not answer:
            return
        self._stage(movie_path=movie, reference_path=reference, reference_type=reference_type)

    def _ingested_reference(self, episode: str, sequence: str, reference_type: str) -> Path | None:
        files = ingested_editorial_files(
            self.project_config,
            episode,
            sequence,
            "edit_source",
            extension=reference_type,
            version="latest",
        )
        if not files:
            messagebox.showwarning(
                f"Stage {reference_type.upper()}",
                "No ingested editorial source was found.\n\n"
                f"Expected:\neditorial/data/{episode}/{sequence}/edit_source/v###/*.{reference_type}",
            )
            return None
        preferred_stem = f"{episode}_{sequence}".lower()
        preferred = [path for path in files if path.stem.lower() == preferred_stem]
        if len(preferred) == 1:
            return preferred[0]
        if len(files) == 1:
            return files[0]
        selected = filedialog.askopenfilename(
            title=f"Select Ingested {reference_type.upper()}",
            initialdir=str(files[0].parent),
            filetypes=[(reference_type.upper(), f"*.{reference_type}")],
        )
        return Path(selected) if selected else None

    def new_cutting_marker(self) -> None:
        sequence_name = simpledialog.askstring(
            "New Cutting Marker",
            "Marker Name / sequence name",
            initialvalue=self.sequence_var.get().strip(),
            parent=self.root,
        )
        if sequence_name is None:
            return
        try:
            naming = shot_naming_rule(self.project_config, profile_name=self.shot_naming_profile_var.get().strip() or None)
            count = create_cutting_markers_from_timeline(
                resolve_app=self.resolve_app,
                track_index=self.track_var.get(),
                sequence_note=sequence_name,
                shot_prefix=naming["prefix"],
                shot_start=naming["start"],
                shot_step=naming["step"],
                shot_padding=naming["padding"],
            )
            self.status_var.set(f"Created cutting markers: {count}")
        except Exception as exc:
            messagebox.showerror("New Cutting Marker Failed", str(exc))

    def _stage(
        self,
        *,
        movie_path: Path,
        reference_path: Path | None = None,
        reference_type: str = "",
    ) -> None:
        episode = self.episode_var.get().strip()
        sequence = self.sequence_var.get().strip()
        if not episode or not sequence:
            messagebox.showwarning("Stage Editorial", "episode and sequence are required.")
            return
        try:
            self.current_work_dir = stage_editorial_source(
                project_config=self.project_config,
                episode=episode,
                sequence=sequence,
                movie_path=movie_path,
                resolve_app=self.resolve_app,
                reference_path=reference_path,
                reference_type=reference_type,
                work_dir=self._selected_work_dir(),
                shot_naming_profile=self.shot_naming_profile_var.get().strip() or None,
            )
            manifest_path = self.current_work_dir / "manifest.json"
            manifest = {}
            if manifest_path.is_file():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    manifest = {}
            imported_type = str(manifest.get("timeline_import_type") or "").upper()
            requested_type = reference_type.upper()
            suffix = ""
            if imported_type:
                suffix = f" | Timeline: {imported_type}"
                if requested_type and imported_type != requested_type:
                    suffix += f" (fallback from {requested_type})"
            shot_media_links = manifest.get("shot_media_links")
            if isinstance(shot_media_links, list) and shot_media_links:
                suffix += f" | shot_media: {len(shot_media_links)} linked"
            self.status_var.set(f"Staged: {self.current_work_dir}{suffix}")
            self.version_var.set(self.current_work_dir.name)
            self._refresh_versions(keep_current=True)
            self.refresh()
        except Exception as exc:
            messagebox.showerror("Stage Editorial Failed", str(exc))

    @staticmethod
    def _ask_movie() -> Path | None:
        path = filedialog.askopenfilename(
            title="Select MOV",
            filetypes=[("Movie", "*.mov *.mp4 *.mxf"), ("All Files", "*.*")],
        )
        return Path(path) if path else None

    def pick_manifest_movie(self) -> None:
        movie = self._ask_movie()
        if not movie:
            return
        self._set_manifest_movie(movie)
        self.status_var.set(f"MOV selected: {movie.name}")

    def _set_manifest_movie(self, movie: Path) -> None:
        self.selected_movie_path = movie
        self.movie_label_var.set(movie.name)

    def _manifest_data(self) -> dict[str, object]:
        data = resolve_project_manifest_data(self.resolve_app)
        if self.selected_movie_path:
            data.update(
                {
                    "movie": self.selected_movie_path.name,
                    "movie_path": self.selected_movie_path.as_posix(),
                }
            )
        return data

    def new_version(self) -> None:
        episode = self.episode_var.get().strip()
        sequence = self.sequence_var.get().strip()
        if not episode or not sequence:
            messagebox.showwarning("New Version", "episode and sequence are required.")
            return
        self.current_work_dir = next_editorial_work_version_dir(self.project_config, episode, sequence)
        self.current_work_dir.mkdir(parents=True, exist_ok=True)
        self.version_var.set(self.current_work_dir.name)
        self._refresh_versions(keep_current=True)
        self.status_var.set(f"New version selected: {self.current_work_dir}")

    def _refresh_episode_sequence(self, keep_current: bool = False) -> None:
        if not hasattr(self, "episode_combo") or self._updating_episode_sequence:
            return
        self._updating_episode_sequence = True
        current_episode = self.episode_var.get().strip()
        current_sequence = self.sequence_var.get().strip()
        try:
            episodes = self._episode_values()
            self.episode_combo["values"] = episodes
            if keep_current and current_episode in episodes:
                self.episode_var.set(current_episode)
            elif current_episode in episodes:
                self.episode_var.set(current_episode)
            else:
                self.episode_var.set(episodes[0])

            sequences = self._sequence_values(self.episode_var.get().strip())
            self.sequence_combo["values"] = sequences
            if keep_current and current_sequence in sequences:
                self.sequence_var.set(current_sequence)
            elif current_sequence in sequences:
                self.sequence_var.set(current_sequence)
            else:
                self.sequence_var.set(sequences[0])
        finally:
            self._updating_episode_sequence = False

    def _episode_changed(self, _event=None) -> None:
        if self._updating_episode_sequence:
            return
        self._refresh_episode_sequence()
        self._refresh_versions()

    def _sequence_changed(self, _event=None) -> None:
        self._refresh_versions()

    def _episode_values(self) -> list[str]:
        root = editorial_work_sequence_dir(self.project_config, "episode", "sequence").parents[1]
        if root.exists():
            values = sorted(path.name for path in root.iterdir() if path.is_dir())
            if values:
                return values
        return ["ep001"]

    def _sequence_values(self, episode: str) -> list[str]:
        root = editorial_work_sequence_dir(self.project_config, episode or "episode", "sequence").parent
        if root.exists():
            values = sorted(path.name for path in root.iterdir() if path.is_dir())
            if values:
                return values
        return ["sq010"]

    def _refresh_versions(self, keep_current: bool = False) -> None:
        if not hasattr(self, "version_combo"):
            return
        if self._updating_versions:
            return
        self._updating_versions = True
        episode = self.episode_var.get().strip()
        sequence = self.sequence_var.get().strip()
        current = self.version_var.get().strip()
        try:
            try:
                versions = editorial_work_versions(self.project_config, episode, sequence)
            except Exception:
                versions = []
            values = ["Latest", *versions]
            self.version_combo["values"] = values
            if keep_current and current in values:
                self.version_var.set(current)
            elif current in values:
                self.version_var.set(current)
            else:
                self.version_var.set("Latest")
        finally:
            self._updating_versions = False
        self._version_changed()
        self._refresh_shot_naming_profiles()

    def _refresh_shot_naming_profiles(self) -> None:
        if not hasattr(self, "shot_naming_combo"):
            return
        values = shot_naming_profile_names(self.project_config)
        current = self.shot_naming_profile_var.get().strip()
        self.shot_naming_combo["values"] = values
        self.shot_naming_profile_var.set(current if current in values else values[0])

    def _shot_naming_changed(self, _event=None) -> None:
        profile = self.shot_naming_profile_var.get().strip()
        rule = shot_naming_rule(self.project_config, profile_name=profile or None)
        self.status_var.set(
            f"Shot naming: {rule.get('profile') or profile} "
            f"{rule['prefix']}{rule['start']:0{rule['padding']}d} step {rule['step']}"
        )

    def _version_changed(self) -> None:
        self.current_work_dir = self._selected_work_dir()
        self._load_manifest_movie()

    def _selected_work_dir(self) -> Path:
        episode = self.episode_var.get().strip() or "episode"
        sequence = self.sequence_var.get().strip() or "sequence"
        version = self.version_var.get().strip()
        if version and version != "Latest":
            return editorial_work_sequence_dir(self.project_config, episode, sequence) / version
        latest = latest_editorial_work_version_dir(self.project_config, episode, sequence)
        return latest or next_editorial_work_version_dir(self.project_config, episode, sequence)

    def _load_manifest_movie(self) -> None:
        manifest_path = self.current_work_dir / "manifest.json" if self.current_work_dir else None
        if not manifest_path or not manifest_path.exists():
            self.selected_movie_path = None
            self.movie_label_var.set("No MOV selected")
            return
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return
        movie_path = str(data.get("movie_path") or "").strip()
        movie = str(data.get("movie") or "").strip()
        if movie_path:
            self.selected_movie_path = Path(movie_path)
            self.movie_label_var.set(Path(movie_path).name)
        elif movie:
            self.selected_movie_path = None
            self.movie_label_var.set(movie)

    def new_episode(self) -> None:
        value = simpledialog.askstring(
            "New Episode",
            "Episode",
            initialvalue=self.episode_var.get().strip() or "ep001",
            parent=self.root,
        )
        if not value:
            return
        episode = value.strip()
        sequence = self.sequence_var.get().strip() or "sq010"
        editorial_work_sequence_dir(self.project_config, episode, sequence).mkdir(parents=True, exist_ok=True)
        self.episode_var.set(episode)
        self.sequence_var.set(sequence)
        self._refresh_episode_sequence(keep_current=True)
        self._refresh_versions()
        self.status_var.set(f"Episode added: {episode}")

    def new_sequence(self) -> None:
        episode = self.episode_var.get().strip() or "ep001"
        value = simpledialog.askstring(
            "New Sequence",
            "Sequence",
            initialvalue=self.sequence_var.get().strip() or "sq010",
            parent=self.root,
        )
        if not value:
            return
        sequence = value.strip()
        editorial_work_sequence_dir(self.project_config, episode, sequence).mkdir(parents=True, exist_ok=True)
        self.sequence_var.set(sequence)
        self._refresh_episode_sequence(keep_current=True)
        self._refresh_versions()
        self.status_var.set(f"Sequence added: {episode}/{sequence}")

def show(config_dir: str | os.PathLike[str] | None = None, resolve_app=None):
    global _WINDOW
    _WINDOW = ResolveTimelineExportWindow(config_dir=config_dir, resolve_app=resolve_app)
    return _WINDOW.show()


if __name__ == "__main__":
    show(sys.argv[1] if len(sys.argv) > 1 else None)
