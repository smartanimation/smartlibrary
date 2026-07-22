from __future__ import annotations

import importlib
import os
import runpy
import sys
from pathlib import Path


SHELF_NAME = "SmartMenu"


def _root() -> Path:
    return Path(
        os.environ.get("SMARTPIPELINE_ROOT")
        or os.environ.get("SMARTLIBRARY_ROOT")
        or Path(__file__).resolve().parents[4]
    )


def ensure_runtime_paths() -> None:
    root = _root()
    for path in (root / "packages", root / "scripts", root):
        text = str(path).replace("\\", "/")
        if text not in sys.path:
            sys.path.insert(0, text)


def _reload(*names: str) -> None:
    for name in names:
        if name in sys.modules:
            importlib.reload(sys.modules[name])


def show_asset_manager() -> None:
    ensure_runtime_paths()
    _reload(
        "scripts.asset_manager",
        "scripts.asset_manager_ui",
        "asset_manager",
        "asset_manager_ui",
    )
    try:
        from scripts import asset_manager_ui
    except ImportError:
        import asset_manager_ui

    asset_manager_ui.show()


def show_shot_manager() -> None:
    ensure_runtime_paths()
    _reload(
        "smartlib.apps.shot_manager",
        "smartlib.apps.shot_manager.service",
        "scripts.shot_manager_ui",
    )
    from scripts import shot_manager_ui

    config_dir = os.environ.get("PROJECT_CONFIG_DIR") or str(_root() / "config" / "STKB")
    shot_manager_ui.show(config_dir)


def show_viewer() -> None:
    ensure_runtime_paths()
    _reload(
        "smartlib.apps.viewer",
        "smartlib.apps.viewer.service",
        "scripts.viewer_ui",
    )
    from scripts import viewer_ui

    config_dir = os.environ.get("PROJECT_CONFIG_DIR") or str(_root() / "config" / "STKB")
    viewer_ui.show(config_dir=config_dir)


def _run_car_system_script(name: str, init_globals: dict | None = None) -> None:
    ensure_runtime_paths()
    script_path = Path(__file__).resolve().parent / "scripts" / name
    if not script_path.exists():
        raise RuntimeError(f"Smart CarSystem script was not found: {script_path}")
    runpy.run_path(str(script_path), run_name="__main__", init_globals=init_globals)


def create_car_path_locators_hda() -> None:
    _run_car_system_script("create_car_path_locators_hda.py")


def create_abc_vehicle_spec_hda() -> None:
    _run_car_system_script("create_abc_vehicle_spec_hda.py")


def create_hair_groom_hda() -> None:
    _run_car_system_script("create_hair_groom_hda.py")


def export_car_locator_anim_json(node=None) -> None:
    init_globals = {"SMART_CAR_EXPORT_NODE": node} if node is not None else None
    _run_car_system_script("export_car_locator_anim_json.py", init_globals=init_globals)


def import_vehicle_spec_json(node=None) -> None:
    init_globals = {"SMART_CAR_IMPORT_TARGET_NODE": node} if node is not None else None
    _run_car_system_script("import_vehicle_spec_json.py", init_globals=init_globals)


def import_abc_vehicle_spec() -> None:
    _run_car_system_script("import_abc_vehicle_spec.py")


def create_smart_crowd_seat_prototype(crowd_dir: str | None = None) -> None:
    init_globals = {"SMART_CROWD_DIR": crowd_dir} if crowd_dir else None
    _run_car_system_script("create_smart_crowd_seat_prototype.py", init_globals=init_globals)
