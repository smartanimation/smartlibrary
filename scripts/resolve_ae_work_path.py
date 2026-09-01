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
    from smartlib.core.output_resolver import OutputPathResolver
    from smartlib.core.path_resolver import ProjectPaths

    parser = argparse.ArgumentParser(description="Resolve Smart AE Browser work paths.")
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--episode", required=True)
    parser.add_argument("--sequence", required=True)
    parser.add_argument("--shot", required=True)
    parser.add_argument("--department", default="anim")
    parser.add_argument("--dcc", default="ae")
    parser.add_argument("--task", default="preComp")
    parser.add_argument("--option", default="main")
    args = parser.parse_args(argv)

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
    shot_root = paths.shot_root(args.episode, args.sequence, args.shot)
    shot_work = paths.shot_work_dir(
        args.episode,
        args.sequence,
        args.shot,
        args.department,
        args.dcc,
    )
    work_output = OutputPathResolver(config).resolve(
        "shot_work_scene",
        {
            "shot_root": shot_root.as_posix(),
            "shot_work": shot_work.as_posix(),
            "episode": args.episode,
            "sequence": args.sequence,
            "shot": args.shot,
            "department": args.department,
            "dcc": args.dcc,
            "task": args.task,
            "option": args.option,
            "version": "001",
            "take": "01",
            "ext": "aep",
        },
        default_directory="{shot_work}/{task}/{option}",
        default_filename="{shot}_{task}_v{version}_t{take}.{ext}",
    )
    work_root = work_output.directory
    print(
        json.dumps(
            {
                "ok": True,
                "shot_root": shot_root.as_posix(),
                "shot_work": shot_work.as_posix(),
                "work_root": work_root.as_posix(),
                "dcc": args.dcc,
                "task": args.task,
                "option": args.option,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
