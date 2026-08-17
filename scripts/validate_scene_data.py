from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate Camera/Light data packages in a clean Maya scene.")
    parser.add_argument("manifest", nargs="+")
    parser.add_argument(
        "--light-container", action="store_true",
        help="Apply light manifests through WORK Construct into a template lights_grp.",
    )
    args = parser.parse_args(argv)
    import maya.standalone
    maya.standalone.initialize(name="python")
    try:
        import maya.cmds as cmds
        from smartlib.core.metadata import read_json
        from smartlib.dcc.maya.shot_scene_data import import_scene_component_package
        cmds.file(new=True, force=True)
        results = []
        if args.light_container:
            from smartlib.dcc.maya.shot_builder import _apply_construct_lights

            cmds.group(empty=True, name="lights_grp")
            manifests = [Path(raw_path) for raw_path in args.manifest]
            project_root = Path(manifests[0].anchor)
            components = [
                {
                    "type": "light", "name": str((read_json(path, {}) or {}).get("root") or path.stem),
                    "path": str(path), "enabled": True,
                }
                for path in manifests
            ]
            created = _apply_construct_lights(
                cmds, project_root, {"components": components}
            )
            children = cmds.listRelatives("lights_grp", children=True, fullPath=True) or []
            expected = {str((read_json(path, {}) or {}).get("root") or "") for path in manifests}
            actual = {node.rsplit("|", 1)[-1] for node in children}
            if not expected.issubset(actual):
                raise RuntimeError(
                    f"Template lights_grp did not receive all lights: expected={sorted(expected)}, "
                    f"actual={sorted(actual)}"
                )
            print(json.dumps({"created": created, "children": children}, indent=2))
            return 0
        for raw_path in args.manifest:
            data = read_json(raw_path, {}) or {}
            created = import_scene_component_package(raw_path)
            root = str(data.get("root") or "")
            exists = bool(root and cmds.objExists(root))
            if not exists:
                raise RuntimeError(f"Imported root was not restored: {root} ({raw_path})")
            shape_types = [str(row.get("type") or "") for row in data.get("shapes") or []]
            results.append({"manifest": raw_path, "root": root, "created": created, "shape_types": shape_types})
        print(json.dumps(results, indent=2))
        return 0
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
