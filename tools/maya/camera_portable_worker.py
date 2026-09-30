"""Background mayapy worker for Camera Package FBX/USD representations."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages"))


def main(snapshot, config_dir=None):
    import maya.standalone

    maya.standalone.initialize(name="python")
    import maya.cmds as cmds
    from smartlib.dcc.maya import camera_portable

    snapshot = Path(snapshot).resolve()
    try:
        if config_dir:
            from smartlib.core.config_loader import ProjectConfig
            from smartlib.core.maya_runtime import software_config_name
            from smartlib.apps.review_build_manager.worker import _load_build_plugins
            config = ProjectConfig(config_dir)
            _load_build_plugins(cmds, config, 'WORK STAGE', software_config_name(config))
        payload = json.loads(snapshot.read_text(encoding="utf-8-sig"))
        native = snapshot.parent / (payload.get("files") or {}).get("ma", "")
        if not native.is_file():
            raise FileNotFoundError("Published Primary Maya file was not found: " + str(native))
        cmds.file(str(native), open=True, force=True, executeScriptNodes=False)
        files = camera_portable.export_portable(payload, snapshot.parent, cmds)
        camera_portable.validate_portable(files, snapshot.parent, cmds)
        camera_portable.update_publish(snapshot, status="complete", files=files)
        return 0
    except Exception as exc:
        camera_portable.update_publish(snapshot, status="failed", error=str(exc))
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit("Usage: mayapy camera_portable_worker.py CAMERA_JSON [CONFIG_DIR]")
    raise SystemExit(main(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else None))
