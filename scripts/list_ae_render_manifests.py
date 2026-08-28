from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _bootstrap() -> None:
    root = Path(__file__).resolve().parents[1]
    for path in (root / "packages", root):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)


def main(argv: list[str] | None = None) -> int:
    _bootstrap()

    from smartlib.apps.shot_manager.service import ShotIdentity, ShotManagerService
    from smartlib.core.config_loader import ProjectConfig
    from smartlib.core.metadata import read_json

    parser = argparse.ArgumentParser(description="List AE Render Manifest Data publishes through SmartPipeline resolvers.")
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--episode", required=True)
    parser.add_argument("--sequence", required=True)
    parser.add_argument("--shot", required=True)
    parser.add_argument("--department", default="")
    parser.add_argument("--task", default="")
    args = parser.parse_args(argv)

    service = ShotManagerService(ProjectConfig(args.config_dir))
    identity = ShotIdentity(args.episode, args.sequence, args.shot)
    department = args.department.strip().lower()
    task = args.task.strip().lower()
    manifests: list[dict[str, object]] = []

    for row in service.list_shot_data_versions(identity):
        parts = str(row.name or "").replace("\\", "/").split("/")
        if len(parts) < 3 or parts[0] != "render_manifest":
            continue
        row_department = parts[1]
        row_task = parts[2]
        if department and row_department.lower() != department:
            continue
        if task and row_task.lower() != task:
            continue
        manifest_path = Path(row.path) / "render_manifest.json"
        if not manifest_path.is_file():
            continue
        data = read_json(manifest_path, {}) or {}
        if data.get("schema") != "smartpipeline.render_manifest.v1":
            continue
        project_root = service.project_config.project_root
        manifests.append(
            {
                "path": manifest_path.as_posix(),
                "data": data,
                "context": {
                    "project": service.project_config.project_name,
                    "projectRoot": project_root.as_posix() if project_root else "",
                    "configDir": Path(args.config_dir).as_posix(),
                    "episode": identity.episode,
                    "sequence": identity.sequence,
                    "shot": identity.shot,
                    "department": row_department,
                    "task": row_task,
                    "latest": row.latest,
                    "updated": row.updated,
                },
            }
        )

    manifests.sort(
        key=lambda item: (
            str((item.get("data") or {}).get("version") or ""),
            str((item.get("data") or {}).get("exported_at") or ""),
            str(item.get("path") or ""),
        ),
        reverse=True,
    )
    print(json.dumps({"ok": True, "manifests": manifests}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
