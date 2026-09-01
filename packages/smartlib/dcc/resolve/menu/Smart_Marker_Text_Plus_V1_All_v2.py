from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def main() -> None:
    root = Path(os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or "P:/dev/smartlibrary")
    packages = root / "packages"
    if str(packages) not in sys.path:
        sys.path.insert(0, str(packages))
    resolve_app = app.GetResolve()  # type: ignore[name-defined]
    from smartlib.dcc.resolve import marker_text_plus_batch_v2
    importlib.reload(marker_text_plus_batch_v2)
    count = marker_text_plus_batch_v2.apply_to_video_track(resolve_app=resolve_app, track_index=1)
    print(f"Created black, left-aligned marker Text+ for {count} clips on video track 1.")


main()
