from __future__ import annotations

import os
import sys
from pathlib import Path


def _ensure_smartlib_on_path() -> None:
    root = Path(__file__).resolve().parents[1]
    package_dir = root / "packages"
    for path in (str(package_dir), str(root)):
        if path not in sys.path:
            sys.path.insert(0, path)
    os.environ.setdefault("SMARTPIPELINE_ROOT", str(root))
    os.environ.setdefault("SMARTLIBRARY_ROOT", str(root))


def main() -> int:
    _ensure_smartlib_on_path()
    from smartlib.apps.smart_ingest.main import main as app_main

    return app_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
