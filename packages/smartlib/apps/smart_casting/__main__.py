from __future__ import annotations

import sys

from smartlib.apps.smart_casting.ui import show


if __name__ == "__main__":
    config_dir = sys.argv[1] if len(sys.argv) > 1 else None
    show(config_dir=config_dir)
