from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
for import_root in (REPOSITORY_ROOT / "packages", REPOSITORY_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish an inspected After Effects project as a shot PreComp."
    )
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--episode", required=True)
    parser.add_argument("--sequence", required=True)
    parser.add_argument("--shot", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--metadata-json", required=True)
    parser.add_argument("--author", default="")
    parser.add_argument("--comment", default="Published from Smart AE Browser")
    args = parser.parse_args(argv)

    from smartlib.apps.review_build_manager.service import ReviewBuildManagerService
    from smartlib.apps.shot_manager import ShotIdentity
    from smartlib.core.config_loader import ProjectConfig

    metadata_path = Path(args.metadata_json)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    service = ReviewBuildManagerService(ProjectConfig(args.config_dir))
    identity = ShotIdentity(args.episode, args.sequence, args.shot)
    destination = service.review_workflow(identity).publish_precomp(
        args.source,
        input_schema=metadata.get("input_schema") or {},
        composition=metadata.get("composition") or {},
        validation=metadata.get("validation") or {},
        dependency_snapshot=metadata.get("dependency_snapshot") or {},
        author=args.author,
        comment=args.comment,
    )
    publish = json.loads(
        (destination / "metadata" / "publish.json").read_text(encoding="utf-8-sig")
    )
    print(json.dumps({
        "ok": True,
        "version": publish.get("version", destination.name),
        "directory": str(destination),
        "project": str(destination / "aftereffects" / "precomp.aep"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
