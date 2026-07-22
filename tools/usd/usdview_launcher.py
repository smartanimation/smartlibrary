from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def _houdini_root() -> Path:
    return Path(
        os.environ.get("HFS")
        or r"C:\Program Files\Side Effects Software\Houdini 21.0.440"
    )


def _clean_path(root: Path) -> None:
    keep = []
    for item in os.environ.get("PATH", "").split(os.pathsep):
        if not item:
            continue
        lowered = item.replace("\\", "/").lower()
        if "windowsapps" in lowered:
            continue
        keep.append(item)
    prefixes = [str(root / "bin"), str(root / "dsolib")]
    os.environ["PATH"] = os.pathsep.join([*prefixes, *keep])


def main() -> None:
    root = _houdini_root()
    os.environ["HFS"] = str(root)
    _clean_path(root)
    usdview = root / "bin" / "usdview"
    sys.argv = [str(usdview), *sys.argv[1:]]
    runpy.run_path(str(usdview), run_name="__main__")


if __name__ == "__main__":
    main()
