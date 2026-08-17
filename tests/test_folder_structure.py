from pathlib import Path

from smartlib.apps.asset_manager.service import AssetCreateRequest, AssetManagerService
from smartlib.apps.shot_manager.service import ShotCreateRequest, ShotManagerService
from smartlib.core.folder_structure import (
    copy_entity_folder_structure,
    copy_folder_structure,
    folder_structure_source,
)


class _ProjectConfig:
    def __init__(self, root: Path, configured: str = ""):
        self.project_root = root
        self.project_name = "TEST"
        self.templates = {}
        self.config_dir = root / "settings"
        self.base = {
            "anchors": {"project_name": "TEST", "fps": 24},
            "template_files": {
                "folder_structure": {
                    "shot": configured,
                }
            }
        }

    def load(self, _name: str):
        return {}


def test_project_physical_folder_structure_is_discovered(tmp_path):
    source = tmp_path / "settings" / "templates" / "folder_structure" / "shot"
    source.mkdir(parents=True)

    assert folder_structure_source(_ProjectConfig(tmp_path), "shot") == source


def test_configured_folder_structure_supports_project_root_token(tmp_path):
    source = tmp_path / "custom" / "asset_tree"
    source.mkdir(parents=True)

    config = _ProjectConfig(tmp_path, "{project_root}/custom/asset_tree")
    config.base["template_files"]["folder_structure"]["asset"] = config.base[
        "template_files"
    ]["folder_structure"].pop("shot")

    assert folder_structure_source(config, "asset") == source


def test_copy_preserves_empty_directories_and_existing_files(tmp_path):
    source = tmp_path / "source"
    (source / "data" / "camera").mkdir(parents=True)
    (source / "publish").mkdir()
    (source / "README.txt").write_text("template", encoding="utf-8")

    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "README.txt").write_text("artist edit", encoding="utf-8")

    created = copy_folder_structure(source, destination)

    assert (destination / "data" / "camera").is_dir()
    assert (destination / "publish").is_dir()
    assert (destination / "README.txt").read_text(encoding="utf-8") == "artist edit"
    assert destination / "data" / "camera" in created
    assert destination / "README.txt" not in created


def test_entity_template_routes_root_and_work_to_separate_destinations(tmp_path):
    source = tmp_path / "structure"
    (source / "root" / "data" / "camera").mkdir(parents=True)
    (source / "work" / "draw" / "cuts").mkdir(parents=True)
    entity_root = tmp_path / "shots" / "c001"
    work_root = tmp_path / "workspace" / "c001" / "work"

    copy_entity_folder_structure(source, entity_root, work_root)

    assert (entity_root / "data" / "camera").is_dir()
    assert (work_root / "draw" / "cuts").is_dir()
    assert not (entity_root / "work").exists()


def test_flat_entity_template_can_merge_into_workspace_entity_root(tmp_path):
    source = tmp_path / "structure"
    (source / "archive").mkdir(parents=True)
    (source / "output" / "review").mkdir(parents=True)
    official_root = tmp_path / "shots" / "c001"
    workspace_entity = tmp_path / "workspace" / "c001"

    copy_entity_folder_structure(
        source,
        official_root,
        workspace_entity / "work",
        workspace_entity,
    )

    assert (workspace_entity / "archive").is_dir()
    assert (workspace_entity / "output" / "review").is_dir()
    assert not (official_root / "archive").exists()


def test_asset_creation_applies_physical_structure_to_each_variant(tmp_path):
    source = tmp_path / "settings" / "templates" / "folder_structure" / "asset"
    (source / "reference" / "drawing").mkdir(parents=True)
    service = AssetManagerService(_ProjectConfig(tmp_path))

    created = service.create_asset(
        AssetCreateRequest("characters", "hero", "alice", variant="winter")
    )

    assert (created.asset_root / "default" / "reference" / "drawing").is_dir()
    assert (created.asset_root / "winter" / "reference" / "drawing").is_dir()


def test_shot_creation_applies_physical_structure(tmp_path):
    source = tmp_path / "settings" / "templates" / "folder_structure" / "shot"
    (source / "output" / "review" / "layers").mkdir(parents=True)
    service = ShotManagerService(_ProjectConfig(tmp_path))

    shot_root = service.create_shot(ShotCreateRequest("ep01", "s010", "c001"))

    assert (shot_root / "output" / "review" / "layers").is_dir()
