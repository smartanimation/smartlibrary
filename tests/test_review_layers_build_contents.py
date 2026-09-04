from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from smartlib.apps.review_build_manager.service import ReviewBuildManagerService


def _identity():
    return SimpleNamespace(episode="ep01", sequence="s001", shot="c001")


def test_build_contents_exposes_review_layers_version(tmp_path: Path) -> None:
    layer_path = tmp_path / "review_layers" / "v004" / "review_layers.json"
    layer_path.parent.mkdir(parents=True)
    layer_path.write_text(json.dumps({"layers": [{"slug": "CHA"}]}), encoding="utf-8")
    manager = object.__new__(ReviewBuildManagerService)
    manager.shots = SimpleNamespace(
        paths=object(),
        load_construct=lambda _identity: {"components": []},
        load_cast=lambda _identity: {"cast": {}},
        resolved_construct=lambda *_args, **_kwargs: {"components": []},
        find_asset_root=lambda _asset: None,
    )
    workflow = SimpleNamespace(
        latest_layer_definition=lambda: ({"layers": [{"slug": "CHA"}]}, layer_path)
    )
    manager.review_workflow = lambda _identity: workflow

    row = manager.build_contents(_identity())[0]

    assert row["type"] == "review_layers"
    assert row["cast_key"] == "main"
    assert row["latest"] == "v004"
    assert row["official"] == "v004"
    assert row["state"] == "READY"
    assert row["allow_disable"] is False
    assert row["component"]["path"] == str(layer_path)


def test_planned_layer_definition_loads_locked_version(tmp_path: Path) -> None:
    root = tmp_path / "review_layers"
    latest_path = root / "v004" / "review_layers.json"
    locked_path = root / "v003" / "review_layers.json"
    for path, slug in ((latest_path, "LATEST"), (locked_path, "LOCKED")):
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"layers": [{"slug": slug}]}), encoding="utf-8")
    manager = object.__new__(ReviewBuildManagerService)
    manager.review_workflow = lambda _identity: SimpleNamespace(
        layer_definition_root=root,
        latest_layer_definition=lambda: ({"layers": [{"slug": "LATEST"}]}, latest_path),
    )
    snapshot = {
        "inputs": [{
            "enabled": True,
            "type": "review_layers",
            "name": "main",
            "version": "v003",
            "path": str(locked_path),
        }]
    }

    payload, path = manager.planned_layer_definition(_identity(), planned_snapshot=snapshot)

    assert path == locked_path
    assert payload["layers"][0]["slug"] == "LOCKED"


def test_save_build_contents_does_not_write_managed_review_layers() -> None:
    captured = {}
    manager = object.__new__(ReviewBuildManagerService)
    manager.shots = SimpleNamespace(
        write_construct=lambda identity, payload: captured.setdefault("payload", payload)
    )
    rows = [
        {"component": {"component_type": "rig", "name": "hero"}},
        {
            "persist_construct": False,
            "component": {"component_type": "review_layers", "name": "main"},
        },
    ]

    manager.save_build_contents(_identity(), rows)

    assert captured["payload"]["components"] == [
        {"component_type": "rig", "name": "hero"}
    ]


def test_latest_review_snapshot_reads_submitted_source_manifest(tmp_path: Path) -> None:
    submitted = tmp_path / "review" / "anim" / "internal" / "v008"
    submitted.mkdir(parents=True)
    snapshot = {"inputs": [{"type": "rig", "name": "hero", "version": "v003"}]}
    (submitted / "source_manifest.json").write_text(
        json.dumps({"planned_snapshot": snapshot}), encoding="utf-8"
    )
    manager = object.__new__(ReviewBuildManagerService)
    manager.list_outputs = lambda _identity: [
        SimpleNamespace(version="internal/v008", directory=str(submitted))
    ]

    assert manager.latest_review_snapshot(_identity(), "anim", "internal") == snapshot
