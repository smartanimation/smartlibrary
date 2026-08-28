from pathlib import Path

import pytest

from smartlib.apps.assembly_manager import AssemblyManagerService, AssemblyMember
from smartlib.apps.sequence_manager import SequenceManagerService, SequenceSummary
from smartlib.apps.shot_manager import ShotCreateRequest, ShotManagerService
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.path_resolver import AssemblyIdentity, AssetIdentity


def _config(tmp_path: Path) -> ProjectConfig:
    root = tmp_path / "project"
    config = tmp_path / "config"
    config.mkdir()
    config.joinpath("templates_base.yml").write_text(
        f"anchors:\n  project_name: TEST\n  project_root: '{root.as_posix()}'\n"
        "shot_dept_partitions:\n  default: cg\n  layout: previs\n"
        "templates:\n  production_root: '{project_root}/production'\n"
        "  assets_root: '{production_root}/assets'\n  assemblies_root: '{production_root}/assemblies'\n"
        "  sequences_root: '{production_root}/sequences'\n  shots_root: '{production_root}/shots'\n"
        "  workspace_root: '{project_root}/workspace'\n",
        encoding="utf-8",
    )
    config.joinpath("templates_assets.yml").write_text(
        "templates:\n  asset_root: '{assets_root}/{category}/{group}/{asset_name}'\n"
        "  asset_work_root: '{workspace_root}/{workspace_partition}/assets/{category}/{group}/{asset_name}/{variant}/work'\n"
        "  asset_work: '{asset_work_root}/{department}'\n",
        encoding="utf-8",
    )
    config.joinpath("templates_assemblies.yml").write_text(
        "templates:\n  assembly_root: '{assemblies_root}/{category}/{group}/{assembly_name}'\n"
        "  assembly_work_root: '{workspace_root}/{workspace_partition}/assemblies/{category}/{group}/{assembly_name}/{variant}/work'\n",
        encoding="utf-8",
    )
    config.joinpath("templates_shots.yml").write_text(
        "templates:\n  shot_root: '{shots_root}/{episode}/{seq}/{shot}'\n",
        encoding="utf-8",
    )
    return ProjectConfig(config)


def test_entity_paths_separate_production_and_workspace(tmp_path: Path):
    service = AssemblyManagerService(_config(tmp_path))
    identity = AssemblyIdentity("environment", "tokyo", "cityA")
    assert service.paths.assembly_root(identity).as_posix().endswith("production/assemblies/environment/tokyo/cityA")
    assert service.paths.assembly_work_root(identity).as_posix().endswith("workspace/previs/assemblies/environment/tokyo/cityA/default/work")
    asset = AssetIdentity("character", "main", "YOU")
    assert service.paths.asset_work_dir(asset, "model").as_posix().endswith("workspace/cg/assets/character/main/YOU/default/work/model")


def test_assembly_publish_requires_pinned_existing_dependency(tmp_path: Path):
    service = AssemblyManagerService(_config(tmp_path))
    asset = AssetIdentity("environment", "architecture", "buildingA")
    version = service.paths.asset_publish_root(asset) / "model" / "proxy" / "v002"
    version.mkdir(parents=True)
    version.joinpath("buildingA.ma").write_text("//Maya ASCII\n", encoding="utf-8")
    identity = AssemblyIdentity("environment", "tokyo", "cityA")
    service.create_assembly(identity)
    service.save_composition(identity, [AssemblyMember("building01", "asset", "environment/architecture/buildingA", version="v002")])
    construct = service.construct_maya(identity)
    assert "namespace \"building01\"" in construct.read_text(encoding="utf-8")
    published = service.publish(identity, comment="blockout")
    assert published.name == "v001"
    assert published.joinpath("manifest.json").is_file()


def test_assembly_rejects_self_reference(tmp_path: Path):
    service = AssemblyManagerService(_config(tmp_path)); identity = AssemblyIdentity("environment", "tokyo", "cityA")
    service.create_assembly(identity)
    with pytest.raises(ValueError, match="cannot reference itself"):
        service.save_composition(identity, [AssemblyMember("self", "assembly", "environment/tokyo/cityA")])


def test_sequence_and_shot_composition(tmp_path: Path):
    config = _config(tmp_path); shots = ShotManagerService(config)
    identity = ShotCreateRequest("ep001", "seq010", "sh0010").identity
    shots.create_shot(ShotCreateRequest("ep001", "seq010", "sh0010"))
    composition = shots.load_shot_composition(identity)
    assert composition["schema"] == "smartpipeline.shot_composition.v1"
    composition["members"] = [{"uid": "city", "entity_type": "assembly", "entity_id": "environment/tokyo/cityA"}]
    shots.write_shot_composition(identity, composition)
    sequence_service = SequenceManagerService(config)
    summary = sequence_service.load(sequence_service.list_sequences()[0])
    sequence_service.save(SequenceSummary(summary.identity, summary.shots, [{"entity_id": "environment/tokyo/cityA", "variant": "default", "version": "v001"}]))
    assert sequence_service.load(summary.identity).default_assemblies[0]["version"] == "v001"
