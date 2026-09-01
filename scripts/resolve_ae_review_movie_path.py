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

    from smartlib.core.config_loader import ProjectConfig
    from smartlib.core.path_resolver import ProjectPaths

    parser = argparse.ArgumentParser(
        description="Resolve the Smart AE Browser working review movie path."
    )
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--episode", required=True)
    parser.add_argument("--sequence", required=True)
    parser.add_argument("--shot", required=True)
    parser.add_argument("--department", default="anim")
    parser.add_argument("--filename", required=True)
    args = parser.parse_args(argv)

    filename = Path(args.filename)
    if filename.name != args.filename or filename.suffix.lower() != ".mov":
        raise ValueError("filename must be a .mov basename")

    config = ProjectConfig(args.config_dir)
    project_root = config.project_root
    if project_root is None:
        raise RuntimeError("project_root is not set in templates_base.yml")

    paths = ProjectPaths(
        project_root,
        templates=config.templates,
        project_name=config.project_name,
        shot_dept_partitions=(config.base.get("shot_dept_partitions") or {}),
    )
    directory = paths.shot_review_movie_dir(
        args.episode,
        args.sequence,
        args.shot,
        args.department,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "directory": directory.as_posix(),
                "path": (directory / filename.name).as_posix(),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
