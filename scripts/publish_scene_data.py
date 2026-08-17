from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish root-based Camera/Light scene data.")
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--scene", required=True)
    parser.add_argument("--episode", required=True)
    parser.add_argument("--sequence", required=True)
    parser.add_argument("--shot", required=True)
    parser.add_argument(
        "--component", action="append", required=True,
        help="Component in data_type:root form, for example camera:cam_CHA_grp.",
    )
    parser.add_argument("--comment", default="Background scene data publish")
    parser.add_argument(
        "--replace-existing", action="store_true",
        help="Replace native files in the latest matching data version without creating a version.",
    )
    args = parser.parse_args(argv)

    import maya.standalone
    maya.standalone.initialize(name="python")
    try:
        import maya.cmds as cmds
        from smartlib.apps.shot_manager import ShotIdentity, ShotManagerService
        from smartlib.core.config_loader import ProjectConfig
        from smartlib.dcc.maya.shot_scene_data import (
            collect_scene_component_data,
            export_scene_component_selection,
        )

        scene = Path(args.scene)
        if not scene.is_file():
            raise FileNotFoundError(scene)
        cmds.file(str(scene), open=True, force=True, ignoreVersion=True)
        service = ShotManagerService(ProjectConfig(args.config_dir))
        identity = ShotIdentity(args.episode, args.sequence, args.shot)
        results = []
        for value in args.component:
            data_type, separator, root = value.partition(":")
            if not separator or not root:
                raise ValueError(f"Invalid --component value: {value}")
            data_type = data_type.strip().lower()
            if data_type == "playblast_settings":
                from smartlib.dcc.maya.review_playblast import load_scene_playblast_settings
                payload = load_scene_playblast_settings(cmds)
                if not payload:
                    raise RuntimeError("Smart Playblast settings were not found in the source scene.")
            else:
                payload = collect_scene_component_data(root.strip(), data_type)
            target = root.strip()
            if args.replace_existing:
                base = service.shot_root(identity) / "data" / data_type / target / "main"
                latest = json.loads((base / "latest.json").read_text(encoding="utf-8-sig"))
                path = base / str(latest["path"])
                if not path.is_file():
                    raise FileNotFoundError(path)
            else:
                path = service.export_shot_scene_data(
                    identity, data_type, payload,
                    target=target, subset="main", filename=f"{data_type}.json",
                    source_workfile=scene, comment=args.comment,
                )
            exported = {"files": {}, "errors": {}}
            if data_type in {"camera", "light"}:
                exported = export_scene_component_selection(root.strip(), data_type, path.parent)
                service.register_scene_data_files(
                    path, exported.get("files") or {}, errors=exported.get("errors") or {}
                )
            results.append({"component": value, "manifest": str(path), **exported})
        print(json.dumps(results, indent=2))
        return 0
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
