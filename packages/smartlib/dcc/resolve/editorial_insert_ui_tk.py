from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from smartlib.core.config_loader import ProjectConfig
from smartlib.editorial.policy import editorial_handle_policy
from smartlib.dcc.resolve.editorial_insert import (
    InsertRequest,
    ShotExportChoice,
    export_editorial_insert,
    preview_editorial_insert,
)


def show(*, config_dir: str, resolve_app: Any) -> Any:
    project = resolve_app.GetProjectManager().GetCurrentProject()
    timeline = project.GetCurrentTimeline() if project else None
    default_episode = _episode_from_timeline(timeline.GetName() if timeline else "")
    config = ProjectConfig(config_dir)
    handle_policy = editorial_handle_policy(config)
    root = tk.Tk()
    root.title("Editorial Insert Export")
    root.geometry("920x620")
    root.minsize(760, 420)
    body = ttk.Frame(root, padding=12)
    body.pack(fill=tk.BOTH, expand=True)
    values = {
        "episode": tk.StringVar(value=default_episode),
        "sequence": tk.StringVar(value=f"{default_episode}01" if default_episode else "op01"),
        "head": tk.IntVar(value=handle_policy.head),
        "tail": tk.IntVar(value=handle_policy.tail),
    }
    fields = ttk.Frame(body)
    fields.pack(fill=tk.X)
    for column, (label, key) in enumerate((("Episode / Unit", "episode"), ("Production Sequence", "sequence"))):
        ttk.Label(fields, text=label).grid(row=0, column=column * 2, sticky="w", padx=(0, 6))
        ttk.Entry(fields, textvariable=values[key], width=18).grid(row=0, column=column * 2 + 1, padx=(0, 16))
    for index, (label, key) in enumerate((("Head Handle", "head"), ("Tail Handle", "tail"))):
        column = 4 + index * 2
        ttk.Label(fields, text=label).grid(row=0, column=column, sticky="w", padx=(0, 6))
        ttk.Spinbox(
            fields, from_=0, to=9999, textvariable=values[key], width=10, state="readonly"
        ).grid(row=0, column=column + 1, padx=(0, 14))

    toolbar = ttk.Frame(body)
    toolbar.pack(fill=tk.X, pady=(10, 6))
    ttk.Label(toolbar, text="Output checked shots as a Clean + HUD pair").pack(side=tk.LEFT)
    rows: list[dict[str, Any]] = []

    table_host = ttk.Frame(body)
    table_host.pack(fill=tk.BOTH, expand=True)
    canvas = tk.Canvas(table_host, highlightthickness=0)
    scrollbar = ttk.Scrollbar(table_host, orient=tk.VERTICAL, command=canvas.yview)
    table = ttk.Frame(canvas)
    table.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas_window = canvas.create_window((0, 0), window=table, anchor="nw")
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(canvas_window, width=event.width))
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    headers = ("Output", "Shot", "Event", "Frames", "Action", "Fixed Version")
    widths = (8, 18, 8, 10, 16, 15)

    def request_from_fields(choices=()) -> InsertRequest:
        episode = values["episode"].get().strip().lower()
        sequence = values["sequence"].get().strip().lower()
        if not episode or not sequence:
            raise ValueError("Episode and Production Sequence are required.")
        return InsertRequest(
            episode=episode, production_sequence=sequence,
            head_handle=int(values["head"].get()), tail_handle=int(values["tail"].get()),
            choices=tuple(choices),
        )

    def load_shots() -> None:
        try:
            request = request_from_fields()
            shots, versions = preview_editorial_insert(
                resolve_app=resolve_app, project_config=config, request=request
            )
        except Exception as exc:
            messagebox.showerror("Editorial Insert Export", str(exc), parent=root)
            return
        for child in table.winfo_children():
            child.destroy()
        rows.clear()
        for column, (header, width) in enumerate(zip(headers, widths)):
            ttk.Label(table, text=header, width=width).grid(row=0, column=column, sticky="w", padx=3, pady=3)
        for row_index, shot in enumerate(shots, start=1):
            available = list(versions.get(shot.occurrence) or ())
            action_values = ["New Version"]
            if available:
                action_values.append("Fix Existing")
            action = tk.StringVar(value="New Version")
            fixed = tk.StringVar(value=available[0] if available else "")
            selected = tk.BooleanVar(value=True)
            ttk.Checkbutton(table, variable=selected).grid(row=row_index, column=0)
            ttk.Label(table, text=shot.shot, width=widths[1]).grid(row=row_index, column=1, sticky="w", padx=3)
            ttk.Label(table, text=f"E{shot.occurrence:03d}", width=widths[2]).grid(row=row_index, column=2, sticky="w", padx=3)
            ttk.Label(table, text=str(shot.cut_duration), width=widths[3]).grid(row=row_index, column=3, sticky="w", padx=3)
            ttk.Combobox(table, textvariable=action, values=action_values, state="readonly", width=14).grid(row=row_index, column=4, sticky="w", padx=3, pady=2)
            ttk.Combobox(table, textvariable=fixed, values=available, state="readonly", width=13).grid(row=row_index, column=5, sticky="w", padx=3)
            rows.append({"shot": shot, "selected": selected, "action": action, "fixed": fixed})

    ttk.Button(toolbar, text="Invert", command=lambda: [row["selected"].set(not row["selected"].get()) for row in rows]).pack(side=tk.RIGHT, padx=(4, 0))
    ttk.Button(toolbar, text="Clear All", command=lambda: [row["selected"].set(False) for row in rows]).pack(side=tk.RIGHT, padx=(4, 0))
    ttk.Button(toolbar, text="Select All", command=lambda: [row["selected"].set(True) for row in rows]).pack(side=tk.RIGHT, padx=(12, 0))
    ttk.Button(toolbar, text="Load / Refresh Markers", command=load_shots).pack(side=tk.RIGHT)
    result = {"request": None}

    def accept() -> None:
        if not rows:
            messagebox.showerror("Editorial Insert Export", "Load marker shots first.", parent=root)
            return
        choices = []
        action_map = {"New Version": "new", "Fix Existing": "fixed"}
        for row in rows:
            shot = row["shot"]
            action = action_map[row["action"].get()] if row["selected"].get() else "omit"
            fixed = row["fixed"].get() if action == "fixed" else ""
            if action == "fixed" and not fixed:
                messagebox.showerror("Editorial Insert Export", f"Select a fixed version for {shot.shot}.", parent=root)
                return
            choices.append(ShotExportChoice(
                occurrence=shot.occurrence, action=action, fixed_revision=fixed,
                output_clean=True, output_edit=True,
            ))
        try:
            result["request"] = request_from_fields(choices)
        except Exception as exc:
            messagebox.showerror("Editorial Insert Export", str(exc), parent=root)
            return
        root.destroy()

    buttons = ttk.Frame(body)
    buttons.pack(fill=tk.X, pady=(10, 0))
    ttk.Button(buttons, text="Cancel", command=root.destroy).pack(side=tk.RIGHT, padx=(8, 0))
    ttk.Button(buttons, text="Export Selected", command=accept).pack(side=tk.RIGHT)
    load_shots()
    root.mainloop()
    request = result["request"]
    if request is None:
        return None
    try:
        output = export_editorial_insert(
            resolve_app=resolve_app, project_config=config, request=request,
        )
    except Exception as exc:
        messagebox.showerror("Editorial Insert Export", str(exc))
        raise
    messagebox.showinfo("Editorial Insert Export", f"Completed:\n{output}")
    return output


def _episode_from_timeline(name: str) -> str:
    value = str(name or "").strip().lower()
    return value.rsplit("_", 1)[-1] if "_" in value else value
