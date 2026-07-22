from __future__ import annotations

import code
import os
import runpy
import sys
from pathlib import Path


def _root() -> Path:
    if os.environ.get("NVIDIA_USD_ROOT"):
        return Path(os.environ["NVIDIA_USD_ROOT"])
    if os.environ.get("SMARTPIPELINE_TOOLS"):
        return Path(os.environ["SMARTPIPELINE_TOOLS"]) / "usd" / "nvidia-25.08"
    return Path(__file__).resolve().parents[3] / "smarttools" / "usd" / "nvidia-25.08"


def _add_dll_directories(paths: list[Path]) -> None:
    add_dll_directory = getattr(os, "add_dll_directory", None)
    if not add_dll_directory:
        return
    for path in paths:
        if path.exists():
            add_dll_directory(str(path))


def _clear_dcc_env() -> None:
    for key in (
        "HFS",
        "HB",
        "H",
        "HH",
        "HOUDINI_PATH",
        "HOUDINI_OTLSCAN_PATH",
        "HOUDINI_SCRIPT_PATH",
        "HOUDINI_DSO_PATH",
        "MAYA_LOCATION",
        "MAYA_APP_DIR",
        "MAYA_SCRIPT_PATH",
        "MAYA_PLUG_IN_PATH",
        "MAYA_MODULE_PATH",
        "MAYA_SHELF_PATH",
        "MAYA_PRESET_PATH",
        "MAYA_CUSTOM_TEMPLATE_PATH",
        "XBMLANGPATH",
        "QT_PLUGIN_PATH",
        "QML2_IMPORT_PATH",
        "PYSIDE_DISABLE_INTERNAL_QT_CONF",
        "PYTHONHOME",
        "PYTHONNOUSERSITE",
        "PXR_PLUGINPATH_NAME",
    ):
        os.environ.pop(key, None)


def _is_dcc_runtime_path(path: str) -> bool:
    text = path.replace("\\", "/").lower()
    return any(
        token in text
        for token in (
            "/autodesk/maya",
            "/side effects software/houdini",
            "/houdini",
            "/maya20",
        )
    )


def _setup(root: Path) -> None:
    _clear_dcc_env()
    _add_dll_directories(
        [
            root / "lib",
            root / "bin",
            root / "plugin" / "usd",
            root / "python" / "Library" / "bin",
            root / "pip-packages" / "PySide6",
            root / "pip-packages" / "shiboken6",
        ]
    )
    os.environ["USD_INSTALL_DIR"] = str(root)
    os.environ["PXR_MTLX_STDLIB_SEARCH_PATHS"] = str(root / "libraries")
    sys.path.insert(0, str(root / "lib" / "python"))
    sys.path.insert(0, str(root / "pip-packages"))
    keep = []
    for item in os.environ.get("PATH", "").split(os.pathsep):
        if (
            item
            and "windowsapps" not in item.replace("\\", "/").lower()
            and not _is_dcc_runtime_path(item)
        ):
            keep.append(item)
    prefixes = [
        root / "lib",
        root / "plugin" / "usd",
        root / "bin",
        root / "pip-packages",
        root / "pip-packages" / "PySide6",
        root / "pip-packages" / "shiboken6",
        root / "python",
        root / "python" / "Library" / "bin",
    ]
    os.environ["PATH"] = os.pathsep.join([*(str(path) for path in prefixes), *keep])


def main() -> None:
    root = _root()
    _setup(root)
    args = sys.argv[1:]
    if not args:
        code.interact(local={"__name__": "__main__"})
        return
    if args[0] == "-c":
        if len(args) < 2:
            raise SystemExit("-c requires code")
        sys.argv = ["-c", *args[2:]]
        exec(compile(args[1], "<string>", "exec"), {"__name__": "__main__", "__file__": "<string>"})
        return
    if args[0] == "-m":
        if len(args) < 2:
            raise SystemExit("-m requires module")
        sys.argv = [args[1], *args[2:]]
        runpy.run_module(args[1], run_name="__main__", alter_sys=True)
        return
    sys.argv = args
    runpy.run_path(args[0], run_name="__main__")


if __name__ == "__main__":
    main()
