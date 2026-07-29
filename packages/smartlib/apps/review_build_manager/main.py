from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _default_config_dir() -> Path:
    configured = os.environ.get("PROJECT_CONFIG_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[5] / "config" / "STKB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open Review Build Manager.")
    parser.add_argument("--config-dir", default=str(_default_config_dir()))
    args = parser.parse_args(argv)

    try:
        from PySide6 import QtWidgets
    except ImportError:
        from PySide2 import QtWidgets

    from smartlib.apps.review_build_manager.window import ReviewBuildManagerWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    window = ReviewBuildManagerWindow(config_dir=args.config_dir)
    window.show()
    window.raise_()
    return app.exec()
