from __future__ import annotations

import os
import shutil
from pathlib import Path

from smartlib.core.config_loader import ProjectConfig, expand_config_tokens, pipeline_root


FOLDER_STRUCTURE_KINDS = {"shot", "asset"}


def folder_structure_source(project_config: ProjectConfig, kind: str) -> Path | None:
    """Return the first existing physical folder-structure template for *kind*.

    Project templates take precedence over the bundled fallback.  A configured
    path may be absolute or use ``{project_root}`` / ``{pipeline_root}``.
    """

    normalized_kind = str(kind).strip().lower()
    if normalized_kind not in FOLDER_STRUCTURE_KINDS:
        raise ValueError(f"Unsupported folder structure kind: {kind}")

    configured = (
        (project_config.base.get("template_files") or {})
        .get("folder_structure", {})
        .get(normalized_kind, "")
    )
    candidates: list[Path] = []
    if configured:
        expanded = expand_config_tokens(str(configured), project_config)
        expanded = expanded.replace("{pipeline_root}", pipeline_root().as_posix())
        configured_path = Path(os.path.expandvars(expanded))
        if not configured_path.is_absolute() and project_config.project_root is not None:
            configured_path = project_config.project_root / configured_path
        candidates.append(configured_path)

    project_root = project_config.project_root
    if project_root is not None:
        candidates.append(
            project_root / "settings" / "templates" / "folder_structure" / normalized_kind
        )
    candidates.append(pipeline_root() / "templates" / "folder_structure" / normalized_kind)

    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def copy_folder_structure(source: Path, destination: Path) -> list[Path]:
    """Merge a physical directory tree into *destination* without overwrites."""

    source = source.resolve()
    destination = destination.resolve()
    if source == destination or source in destination.parents:
        raise ValueError(
            f"Folder structure source cannot contain its destination: {source} -> {destination}"
        )

    created: list[Path] = []
    destination_existed = destination.exists()
    destination.mkdir(parents=True, exist_ok=True)
    if not destination_existed:
        created.append(destination)

    for source_root, dir_names, file_names in os.walk(source):
        relative_root = Path(source_root).relative_to(source)
        target_root = destination / relative_root
        if not target_root.exists():
            target_root.mkdir(parents=True)
            created.append(target_root)

        for dir_name in dir_names:
            target_dir = target_root / dir_name
            if not target_dir.exists():
                target_dir.mkdir()
                created.append(target_dir)

        for file_name in file_names:
            source_file = Path(source_root) / file_name
            target_file = target_root / file_name
            if target_file.exists():
                continue
            shutil.copy2(source_file, target_file)
            created.append(target_file)

    return created


def copy_entity_folder_structure(
    source: Path,
    entity_root: Path,
    work_root: Path | None = None,
    flat_destination: Path | None = None,
) -> list[Path]:
    """Copy a physical entity template, optionally routing ``root``/``work``."""

    root_source = source / "root"
    work_source = source / "work"
    if not root_source.is_dir() and not work_source.is_dir():
        return copy_folder_structure(source, flat_destination or entity_root)

    created: list[Path] = []
    if root_source.is_dir():
        created.extend(copy_folder_structure(root_source, entity_root))
    if work_source.is_dir() and work_root is not None:
        created.extend(copy_folder_structure(work_source, work_root))
    return created
