from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _ensure_smartlib_on_path() -> None:
    root = (
        os.environ.get("SMARTPIPELINE_ROOT")
        or os.environ.get("SMARTLIBRARY_ROOT")
        or str(Path(__file__).resolve().parents[1])
    )
    package_dir = str(Path(root) / "packages")
    if package_dir not in sys.path:
        sys.path.insert(0, package_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SmartPipeline editorial intake.")
    parser.add_argument("--config", default=os.environ.get("PROJECT_CONFIG_DIR", ""))
    subparsers = parser.add_subparsers(dest="command", required=True)

    template_parser = subparsers.add_parser("template", help="Write an editorial CSV template.")
    template_parser.add_argument("path")

    intake_parser = subparsers.add_parser("intake", help="Import MOV+CSV editorial data.")
    intake_parser.add_argument("--csv", required=True)
    intake_parser.add_argument("--mov", default="")
    intake_parser.add_argument("--comment", default="")
    intake_parser.add_argument("--no-publish", action="store_true")
    intake_parser.add_argument("--no-register-shots", action="store_true")

    storyreel_parser = subparsers.add_parser("storyreel", help="Build storyreel image sequences from a cut publish.")
    storyreel_parser.add_argument("--publish-dir", default="")
    storyreel_parser.add_argument("--latest", action="store_true")
    storyreel_parser.add_argument("--dry-run", action="store_true")
    storyreel_parser.add_argument("--width", type=int, default=960)

    args = parser.parse_args(argv)
    if not args.config:
        parser.error("--config or PROJECT_CONFIG_DIR is required")

    _ensure_smartlib_on_path()
    from smartlib.core.config_loader import ProjectConfig
    from smartlib.editorial import EditorialIntakeRequest, EditorialIntakeService, StoryreelBuilder

    service = EditorialIntakeService(ProjectConfig(args.config))
    if args.command == "template":
        path = service.write_csv_template(args.path)
        print(f"CSV template: {path}")
        return 0

    if args.command == "storyreel":
        builder = StoryreelBuilder(ProjectConfig(args.config))
        if args.latest:
            result = builder.build_latest_cut(execute=not args.dry_run, width=args.width)
        elif args.publish_dir:
            result = builder.build_from_publish(args.publish_dir, execute=not args.dry_run, width=args.width)
        else:
            parser.error("storyreel requires --latest or --publish-dir")
        print(f"Storyreel JSON: {result.storyreel_json}")
        print(f"Shots: {len(result.results)}")
        for item in result.results:
            print(f"- {item.shot}: {item.output_dir} ({item.frame_count} frames)")
        return 0

    result = service.intake(
        EditorialIntakeRequest(
            csv_path=Path(args.csv),
            offline_mov=Path(args.mov) if args.mov else None,
            comment=args.comment,
            publish=not args.no_publish,
            register_shots=not args.no_register_shots,
        )
    )
    print(f"Work: {result.work_dir}")
    if result.publish_dir:
        print(f"Publish: {result.publish_dir}")
    if result.editorial_json:
        print(f"Editorial JSON: {result.editorial_json}")
    if result.cut_otio:
        print(f"Cut OTIO: {result.cut_otio}")
    if result.offline_mov:
        print(f"Offline MOV: {result.offline_mov}")
    print(f"Events: {len(result.events)}")
    print(f"Registered shots: {len(result.registered_shots)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
