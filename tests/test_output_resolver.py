from pathlib import Path

import pytest

from smartlib.core.output_resolver import OutputPathResolver
from smartlib.core.path_resolver import AssetIdentity, ProjectPaths
from smartlib.review.package import build_review_package_plan


class _Config:
    def __init__(self, root: Path):
        self.project_root = root
        self.project_name = "ELCD"
        self.templates = {
            "workspace_root": "{project_root}/workspace",
            "shot_root": "{project_root}/shots/{episode}/{sequence}/{shot}",
        }

    def load(self, name):
        if name != "naming.yml":
            return {}
        return {
            "outputs": {
                "review": {
                    "directory": "{shot_root}/output/review/{department}/{version}",
                    "filename": "{project_name}_{shot}_{department}_{version}.{ext}",
                }
            }
        }


def test_resolves_nested_directory_and_filename_tokens(tmp_path):
    output = OutputPathResolver(_Config(tmp_path)).resolve(
        "review",
        {
            "episode": "ep02",
            "sequence": "s027",
            "shot": "c001",
            "department": "animation",
            "version": "v008",
            "ext": "mov",
        },
    )

    assert output.path == (
        tmp_path / "shots" / "ep02" / "s027" / "c001" / "output"
        / "review" / "animation" / "v008" / "ELCD_c001_animation_v008.mov"
    )


def test_rejects_unresolved_tokens(tmp_path):
    with pytest.raises(KeyError):
        OutputPathResolver(_Config(tmp_path)).resolve("review", {"shot": "c001"})


def test_output_resolver_uses_configured_default_workspace_partition(tmp_path):
    class Config(_Config):
        def __init__(self, root):
            super().__init__(root)
            self.templates.update({
                "asset_work_root": "{workspace_root}/{workspace_partition}/assets/{category}/{group}/{asset_name}/{variant}/work",
                "asset_work": "{asset_work_root}/{department}",
            })

        def load(self, name):
            if name == "templates_base.yml":
                return {"shot_dept_partitions": {"default": "cg"}}
            if name == "naming.yml":
                return {
                    "outputs": {
                        "asset_work_scene": {
                            "directory": "{asset_work}/{dcc}/{task}",
                            "filename": "{project_name}_{asset}_{task}_v{version}_t{take}.{ext}",
                        }
                    }
                }
            return {}

    output = OutputPathResolver(Config(tmp_path)).resolve(
        "asset_work_scene",
        {
            "asset": "OBN",
            "asset_name": "OBN",
            "category": "characters",
            "group": "main",
            "variant": "default",
            "department": "rig",
            "dcc": "maya",
            "task": "layout",
            "version": "001",
            "take": "01",
            "ext": "mb",
        },
    )

    assert output.path == (
        tmp_path
        / "workspace/cg/assets/characters/main/OBN/default/work/rig/maya/layout"
        / "ELCD_OBN_layout_v001_t01.mb"
    )


def test_project_paths_support_separate_work_roots(tmp_path):
    paths = ProjectPaths(
        tmp_path,
        templates={
            "workspace_root": "{project_root}/workspace",
            "shot_work": "{workspace_root}/{episode}/{sequence}/{shot}/work/{department}/{dcc}",
            "asset_root": "{project_root}/assets/{category}/{group}/{asset_name}",
            "asset_work": "{workspace_root}/assets/{category}/{group}/{asset_name}/{variant}/work/{department}",
        },
    )

    assert paths.shot_work_dir("ep02", "s027", "c001", "anim", "maya") == (
        tmp_path / "workspace" / "ep02" / "s027" / "c001" / "work" / "anim" / "maya"
    )
    assert paths.asset_work_dir(
        AssetIdentity("characters", "hero", "alice", "winter"), "model"
    ) == (
        tmp_path / "workspace" / "assets" / "characters" / "hero" / "alice"
        / "winter" / "work" / "model"
    )


def test_project_paths_partition_workspace_by_shot_department(tmp_path):
    paths = ProjectPaths(
        tmp_path,
        templates={
            "workspace_root": "{project_root}/workspace",
            "shot_work_root": "{workspace_root}/{workspace_partition}/{episode}/{sequence}/{shot}/work",
            "shot_work": "{shot_work_root}/{department}/{dcc}",
            "shot_build_root": "{workspace_root}/{workspace_partition}/{episode}/{sequence}/{shot}/build",
            "shot_build": "{shot_build_root}/{department}/{dcc}/{task}/{version}",
            "sequence_build_root": "{workspace_root}/{workspace_partition}/{episode}/{sequence}/build",
            "sequence_build": "{sequence_build_root}/{department}/{dcc}/{task}/{version}",
        },
        shot_dept_partitions={"default": "cg", "drawing": "drawing"},
    )

    assert paths.shot_build_dir(
        "ep02", "s027", "c001", "layout", "maya", "main", "v001"
    ) == tmp_path / "workspace/cg/ep02/s027/c001/build/layout/maya/main/v001"
    assert paths.shot_work_dir(
        "ep02", "s027", "c001", "drawing", "clipstudio"
    ) == tmp_path / "workspace/drawing/ep02/s027/c001/work/drawing/clipstudio"
    assert paths.sequence_build_dir(
        "ep02", "s027", "drawing", "clipstudio", "main", "v002"
    ) == tmp_path / "workspace/drawing/ep02/s027/build/drawing/clipstudio/main/v002"


def test_project_paths_use_default_workspace_partition_for_asset_root(tmp_path):
    paths = ProjectPaths(
        tmp_path,
        templates={
            "workspace_root": "{project_root}/workspace",
            "asset_root": "{project_root}/production/assets/{category}/{group}/{asset_name}",
            "asset_work_root": "{workspace_root}/{workspace_partition}/assets/{category}/{group}/{asset_name}/{variant}/work",
            "asset_work": "{asset_work_root}/{department}",
        },
        shot_dept_partitions={"default": "cg", "drawing": "drawing"},
    )
    identity = AssetIdentity("characters", "main", "OBN")

    assert paths.asset_work_root(identity) == (
        tmp_path / "workspace/cg/assets/characters/main/OBN/default/work"
    )
    assert paths.asset_work_dir(identity, "model") == (
        tmp_path / "workspace/cg/assets/characters/main/OBN/default/work/model"
    )


def test_project_paths_reject_literal_unresolved_folder_tokens(tmp_path):
    paths = ProjectPaths(
        tmp_path,
        templates={"shot_root": "{project_root}/{unknown}/{shot}"},
    )
    with pytest.raises(ValueError, match="Unresolved path template token"):
        paths.shot_root("ep02", "s027", "c001")


def test_project_paths_support_separate_data_and_publish_roots(tmp_path):
    paths = ProjectPaths(
        tmp_path,
        templates={
            "shot_data_root": "{project_root}/production_data/{episode}/{sequence}/{shot}",
            "shot_publish_root": "{project_root}/production_publish/{episode}/{sequence}/{shot}",
            "asset_root": "{project_root}/assets/{category}/{group}/{asset_name}",
            "asset_data_root": "{project_root}/asset_data/{asset_name}/{variant}",
            "asset_publish_root": "{project_root}/asset_publish/{asset_name}/{variant}",
        },
    )
    identity = AssetIdentity("characters", "hero", "alice", "winter")

    assert paths.shot_data_root("ep02", "s027", "c001") == (
        tmp_path / "production_data" / "ep02" / "s027" / "c001"
    )
    assert paths.shot_publish_root("ep02", "s027", "c001") == (
        tmp_path / "production_publish" / "ep02" / "s027" / "c001"
    )
    assert paths.asset_data_root(identity) == tmp_path / "asset_data" / "alice" / "winter"
    assert paths.asset_publish_root(identity) == tmp_path / "asset_publish" / "alice" / "winter"


def test_review_package_accepts_resolved_publish_root(tmp_path):
    publish_root = tmp_path / "client_publish" / "ep02" / "s027" / "c001"
    plan = build_review_package_plan(
        tmp_path / "shots" / "ep02" / "s027" / "c001",
        {"episode": "ep02", "sequence": "s027", "shot": "c001"},
        {"review_layers": {}},
        "anim",
        publish_root=publish_root,
    )

    assert plan.version_dir == publish_root / "review" / "anim" / "v001" / "t001"
