from __future__ import annotations
import argparse
import json
from smartlib.core.config_loader import ProjectConfig
from smartlib.color.validation import prepare, load_manifest, compare_run, approve


def main():
    parser = argparse.ArgumentParser(description="SmartPipeline color validation")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--config-dir", required=True)
    p.add_argument("--run", required=True)
    p = commands.add_parser("compare")
    p.add_argument("manifest")
    p.add_argument("--baseline")
    p.add_argument("--atol", type=float, default=1e-5)
    p.add_argument("--rtol", type=float, default=1e-5)
    p = commands.add_parser("approve")
    p.add_argument("manifest")
    p.add_argument("--config-dir", required=True)
    p.add_argument("--baseline", required=True)
    p.add_argument("--reviewer", required=True)
    p = commands.add_parser("maya")
    p.add_argument("manifest")
    p.add_argument("--render", action="store_true")
    p.add_argument("--images", action="store_true")
    p = commands.add_parser("rv")
    p.add_argument("manifest")
    p.add_argument("--config-dir", required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        print(prepare(ProjectConfig(args.config_dir), args.run)["paths"]["manifest.json"])
    elif args.command == "compare":
        report = compare_run(load_manifest(args.manifest), args.baseline, atol=args.atol, rtol=args.rtol)
        print(json.dumps(report, indent=2))
        return 0 if report["passed"] else 1
    elif args.command == "approve":
        print(approve(ProjectConfig(args.config_dir), load_manifest(args.manifest), args.baseline, args.reviewer))
    elif args.command == "rv":
        from smartlib.color.rv_runner import run
        result = run(ProjectConfig(args.config_dir), load_manifest(args.manifest))
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "complete" else 1
    elif args.command == "maya":
        import os
        manifest = load_manifest(args.manifest)
        os.environ["OCIO"] = manifest["contract"]["config"]
        os.environ.pop("MAYA_COLOR_MANAGEMENT_SYNCOLOR", None)
        import maya.standalone
        maya.standalone.initialize(name="python")
        try:
            from smartlib.dcc.maya.color_validation import probe, render_smoke, export_images
            manifest = load_manifest(args.manifest)
            result = render_smoke(manifest) if args.render else export_images(manifest) if args.images else probe(manifest)
            print(json.dumps(result, indent=2))
            return 1 if result["status"] == "failed" else 0
        finally:
            maya.standalone.uninitialize()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
