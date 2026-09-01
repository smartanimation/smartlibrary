from __future__ import annotations

import json
from pathlib import Path

from smartlib.core.texture_reconnect import (
    TextureReference,
    TextureReconnectItem,
    collect_texture_items,
    inspect_texture_references,
    plan_texture_reconnect,
    reconnect_manifest,
    texture_root_from_package,
)
from smartlib.dcc.maya.texture_reconnect import ingested_package_candidates, reconnect_file_nodes
from smartlib.core.config_loader import ProjectConfig


def _package(tmp_path: Path) -> Path:
    root = tmp_path / "production" / "assets" / "CH" / "main" / "DLI" / "default" / "data" / "assembly" / "vendor" / "v001"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "files": [{"role": "texture_root", "path": "sourceimages", "required": False}]
    }), encoding="utf-8")
    return root


def test_manifest_declares_texture_root(tmp_path: Path):
    package = _package(tmp_path)
    (package / "sourceimages").mkdir()
    assert texture_root_from_package(package) == package / "sourceimages"


def test_reconnect_prefers_preserved_relative_path_and_reports_ambiguity(tmp_path: Path):
    package = _package(tmp_path)
    root = package / "sourceimages"
    for relative in ("body/diffuse.1001.exr", "face/diffuse.1001.exr"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"texture")

    plan = plan_texture_reconnect([
        TextureReference("body_file", "Z:/client/sourceimages/body/diffuse.<UDIM>.exr"),
        TextureReference("unknown_file", "Z:/client/diffuse.<UDIM>.exr"),
    ], root)

    assert plan[0].status == "ready"
    assert plan[0].match_method == "relative_path"
    assert plan[0].resolved_path == root / "body" / "diffuse.<UDIM>.exr"
    assert plan[1].status == "ambiguous"
    assert plan[1].resolved_path is None
    assert len(reconnect_manifest(plan)["textures"][1]["candidates"]) == 2


class _FakeCmds:
    def __init__(self):
        self.paths = {"body_file": "X:/vendor/sourceimages/body/diffuse.png"}

    def ls(self, **kwargs):
        return list(self.paths) if kwargs.get("type") == "file" else []

    def getAttr(self, attribute):
        return self.paths[attribute.split(".", 1)[0]]

    def setAttr(self, attribute, value, **kwargs):
        assert kwargs == {"type": "string"}
        self.paths[attribute.split(".", 1)[0]] = value


def test_maya_adapter_reconnects_only_resolved_nodes(tmp_path: Path):
    package = _package(tmp_path)
    texture = package / "sourceimages" / "body" / "diffuse.png"
    texture.parent.mkdir(parents=True)
    texture.write_bytes(b"png")
    cmds = _FakeCmds()

    result = reconnect_file_nodes(package, cmds_module=cmds)

    assert result[0].match_method == "relative_path"
    assert cmds.paths["body_file"] == texture.as_posix()


def test_package_candidates_use_common_asset_resolver(tmp_path: Path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "templates_base.yml").write_text(
        f"anchors:\n  project_name: ELCD\n  project_root: '{tmp_path.as_posix()}'\n",
        encoding="utf-8",
    )
    package = _package(tmp_path)
    (package / "sourceimages").mkdir()
    scene = tmp_path / "production" / "assets" / "CH" / "main" / "DLI" / "default" / "work" / "look" / "DLI.ma"
    assert ingested_package_candidates(ProjectConfig(config), scene) == [package]


def test_collects_only_selected_textures_and_preserves_udim_tiles(tmp_path: Path):
    source_root = tmp_path / "package" / "sourceimages"
    for tile in ("1001", "1002"):
        path = source_root / "body" / f"diffuse.{tile}.exr"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(tile.encode("ascii"))
    ignored = source_root / "background" / "huge.exr"
    ignored.parent.mkdir(parents=True)
    ignored.write_bytes(b"not selected")
    item = TextureReconnectItem("body_file", "X:/diffuse.<UDIM>.exr", source_root / "body" / "diffuse.<UDIM>.exr", "ready", "relative_path")
    destination = tmp_path / "workspace" / "delivery" / "TEX-test" / "sourceimages"
    copied, manifest = collect_texture_items([item], destination, texture_root=source_root)
    assert [path.relative_to(destination).as_posix() for path in copied] == ["body/diffuse.1001.exr", "body/diffuse.1002.exr"]
    assert not (destination / "background" / "huge.exr").exists()
    assert manifest["schema"] == "smartpipeline.texture_collection.v1"


def test_vendor_mode_inspects_current_paths_without_ingested_package(tmp_path: Path):
    texture = tmp_path / "sourceimages" / "body" / "diffuse.png"
    texture.parent.mkdir(parents=True)
    texture.write_bytes(b"png")
    plan = inspect_texture_references([
        TextureReference("body_file", texture.as_posix()),
        TextureReference("missing_file", (texture.parent / "missing.png").as_posix()),
    ])
    assert [item.status for item in plan] == ["ready", "missing"]
    destination = tmp_path / "delivery" / "sourceimages"
    copied, _ = collect_texture_items([plan[0]], destination)
    assert [path.relative_to(destination).as_posix() for path in copied] == ["body/diffuse.png"]
