from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open Smart Delivery.")
    parser.add_argument("--config-dir", default=os.environ.get("PROJECT_CONFIG_DIR", ""))
    args = parser.parse_args(argv)
    if not args.config_dir:
        parser.error("--config-dir or PROJECT_CONFIG_DIR is required")
    try:
        from PySide6 import QtWidgets
    except ImportError:
        from PySide2 import QtWidgets
    from .window import SmartDeliveryWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    window = SmartDeliveryWindow(Path(args.config_dir))
    window.show()
    window.raise_()
    return app.exec()
