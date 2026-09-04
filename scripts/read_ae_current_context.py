from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    for path in (root, root / "packages"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from smartlib.apps.shot_manager.context import read_shared_selection

    try:
        payload = {"ok": True, "context": read_shared_selection()}
    except FileNotFoundError:
        payload = {"ok": False, "error": "No shared shot context. Select a shot in Shot Manager first."}
    except (ValueError, KeyError, TypeError, OSError) as error:
        payload = {"ok": False, "error": str(error)}
    print(json.dumps(payload))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
