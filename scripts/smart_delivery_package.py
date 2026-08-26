from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _bootstrap() -> Path:
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root / "packages"), str(root)]
    os.environ.setdefault("SMARTPIPELINE_ROOT", str(root))
    return root


def main(argv: list[str] | None = None) -> int:
    root = _bootstrap()
    from smartlib.delivery.vendor_exporter import PackageProfile, VendorPackageBuilder

    parser = argparse.ArgumentParser(description="Create a manifest-driven Smart Delivery ZIP.")
    parser.add_argument("--profile", default=str(root / "config/delivery/package_profiles/vendor.json"))
    sub = parser.add_subparsers(dest="package_type", required=True)
    asset = sub.add_parser("asset")
    asset.add_argument("--scene", required=True); asset.add_argument("--textures", default="")
    asset.add_argument("--output", required=True); asset.add_argument("--project", required=True)
    asset.add_argument("--category", required=True); asset.add_argument("--group", default="main")
    asset.add_argument("--asset", required=True); asset.add_argument("--variant", default="default")
    asset.add_argument("--subset", default=""); asset.add_argument("--comment", default="")
    shot = sub.add_parser("shot")
    shot.add_argument("--source", action="append", required=True); shot.add_argument("--output", required=True)
    shot.add_argument("--project", required=True); shot.add_argument("--episode", required=True)
    shot.add_argument("--sequence", required=True); shot.add_argument("--shot", required=True)
    shot.add_argument("--department", required=True); shot.add_argument("--subset", default="vendor")
    shot.add_argument("--comment", default="")
    args = parser.parse_args(argv)
    builder = VendorPackageBuilder(PackageProfile.load(args.profile))
    if args.package_type == "asset":
        result = builder.build_asset(scene=args.scene, texture_root=args.textures or None, output=args.output,
            project=args.project, category=args.category, group=args.group, asset=args.asset,
            variant=args.variant, subset=args.subset or None, comment=args.comment)
    else:
        target = {key: getattr(args, key) for key in ("project", "episode", "sequence", "shot", "department", "subset")}
        target["target_type"] = "Shot"
        result = builder.build_shot(sources=args.source, output=args.output, target=target, comment=args.comment)
    print(result.archive)
    print(f"members: {len(result.files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
