from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def smartpipeline_root() -> Path:
    root = os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT")
    if root:
        return Path(root).resolve()
    return Path(__file__).resolve().parents[4]


def main() -> None:
    root = smartpipeline_root()
    packages = root / "packages"
    for path in (str(packages), str(root)):
        if path not in sys.path:
            sys.path.insert(0, path)
    os.environ.setdefault("SMARTPIPELINE_ROOT", str(root))
    os.environ.setdefault("SMARTLIBRARY_ROOT", str(root))
    runpy.run_path(str(root / "launcher.py"), run_name="__main__")
