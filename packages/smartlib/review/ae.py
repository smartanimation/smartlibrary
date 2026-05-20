from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from smartlib.core.metadata import write_json


@dataclass(frozen=True)
class AETemplateResult:
    source_template: Path | None
    copied_to: Path
    template_used_json: Path
    candidates: list[Path]


def ae_template_candidates(
    shot_root: str | Path,
    project_root: str | Path,
    pipeline_root: str | Path,
    department: str,
) -> list[Path]:
    shot_root = Path(shot_root)
    project_root = Path(project_root)
    pipeline_root = Path(pipeline_root)
    names = [f"review_{department}.aep", "review_custom.aep", "review_base.aep"]
    candidates = []

    shot_template_root = shot_root / "review" / "templates"
    for name in (f"review_{department}.aep", "review_custom.aep"):
        candidates.append(shot_template_root / name)

    project_template_root = project_root / "settings" / "templates" / "ae" / "review"
    for name in (f"review_{department}.aep", "review_base.aep"):
        candidates.append(project_template_root / name)

    pipeline_template_root = pipeline_root / "templates" / "ae" / "review"
    for name in names:
        candidates.append(pipeline_template_root / name)

    return candidates


def resolve_ae_template(
    shot_root: str | Path,
    project_root: str | Path,
    pipeline_root: str | Path,
    department: str,
) -> tuple[Path | None, list[Path]]:
    candidates = ae_template_candidates(shot_root, project_root, pipeline_root, department)
    for path in candidates:
        if path.exists():
            return path, candidates
    return None, candidates


def copy_ae_template_to_publish(
    version_dir: str | Path,
    shot_root: str | Path,
    project_root: str | Path,
    pipeline_root: str | Path,
    department: str,
) -> AETemplateResult:
    version_dir = Path(version_dir)
    target = version_dir / "ae" / "review_project.aep"
    template_used_json = version_dir / "ae" / "template_used.json"
    source, candidates = resolve_ae_template(shot_root, project_root, pipeline_root, department)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source:
        shutil.copy2(source, target)
    write_json(
        template_used_json,
        {
            "department": department,
            "source_template": str(source) if source else "",
            "copied_to": "ae/review_project.aep",
            "status": "copied" if source else "missing_template",
            "candidates": [str(path) for path in candidates],
        },
    )
    return AETemplateResult(
        source_template=source,
        copied_to=target,
        template_used_json=template_used_json,
        candidates=candidates,
    )
