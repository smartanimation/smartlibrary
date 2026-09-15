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

    from smartlib.apps.review_build_manager.service import ReviewBuildManagerService
    from smartlib.apps.shot_manager import ShotIdentity
    from smartlib.core.config_loader import ProjectConfig

    parser = argparse.ArgumentParser(
        description="List shot PreComp publishes through SmartPipeline resolvers."
    )
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--episode", required=True)
    parser.add_argument("--sequence", required=True)
    parser.add_argument("--shot", required=True)
    args = parser.parse_args(argv)

    service = ReviewBuildManagerService(ProjectConfig(args.config_dir))
    identity = ShotIdentity(args.episode, args.sequence, args.shot)
    publishes = service.review_workflow(identity).list_precomp_publishes()
    print(
        json.dumps(
            {
                "ok": True,
                "publishes": publishes,
                "context": {
                    "project": service.project_config.project_name,
                    "episode": identity.episode,
                    "sequence": identity.sequence,
                    "shot": identity.shot,
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
