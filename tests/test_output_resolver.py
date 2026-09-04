import ast
import importlib.util
import sys
from pathlib import Path

import pytest

from smartlib.core.output_resolver import OutputPathResolver
from smartlib.core.path_resolver import AssemblyIdentity, AssetIdentity, ProjectPaths
from smartlib.review.package import build_review_package_plan


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


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


def test_project_paths_resolve_editorial_data_under_production(tmp_path):
    default_paths = ProjectPaths(
        tmp_path,
        templates={"production_root": "{project_root}/production"},
    )
    configured_paths = ProjectPaths(
        tmp_path,
        templates={"editorial_data_root": "{project_root}/custom/editorial_data"},
    )

    assert default_paths.editorial_data_root() == tmp_path / "production/editorial/data"
    assert configured_paths.editorial_data_root() == tmp_path / "custom/editorial_data"


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


def test_project_paths_lists_shot_construct_builds_across_tasks(tmp_path):
    paths = ProjectPaths(
        tmp_path,
        templates={
            "workspace_root": "{project_root}/workspace",
            "shot_build_root": (
                "{workspace_root}/{workspace_partition}/"
                "{episode}/{sequence}/{shot}/build"
            ),
            "shot_build": (
                "{shot_build_root}/{department}/{dcc}/{task}/{version}"
            ),
            "shot_output_root": (
                "{project_root}/production/shots/"
                "{episode}/{sequence}/{shot}/output"
            ),
        },
        shot_dept_partitions={"default": "cg", "drawing": "drawing"},
    )
    expected = []
    for department, task, version in (
        ("anim", "main", "v001"),
        ("anim", "precomp", "v002"),
        ("drawing", "main", "v003"),
    ):
        manifest = paths.shot_build_dir(
            "ep02", "s027", "c001", department, "maya", task, version
        ) / "build_manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("{}", encoding="utf-8")
        expected.append(manifest)

    assert set(
        paths.shot_construct_build_manifests("ep02", "s027", "c001")
    ) == set(expected)


def test_project_paths_resolve_review_artifacts_under_workspace(tmp_path):
    paths = ProjectPaths(
        tmp_path,
        templates={
            "workspace_root": "{project_root}/workspace",
            "shot_workspace_root": "{workspace_root}/{workspace_partition}/shots/{episode}/{sequence}/{shot}",
            "shot_render_root": "{shot_workspace_root}/render",
            "shot_render_layers_root": "{shot_render_root}/{department}/layers",
            "shot_render_layer_version": "{shot_render_layers_root}/{layer}/{version}",
            "shot_review_root": "{shot_workspace_root}/review",
            "shot_review_movie": "{shot_review_root}/{department}/mov",
            "shot_review_build": "{shot_review_root}/review_build/{version}/{take}",
        },
        shot_dept_partitions={"default": "cg"},
    )

    assert paths.shot_render_layer_version_dir(
        "ep02", "s027", "c001", "anim", "CHA", "v002"
    ) == tmp_path / "workspace/cg/shots/ep02/s027/c001/render/anim/layers/CHA/v002"
    assert paths.shot_review_build_dir(
        "ep02", "s027", "c001", "anim", "v003", "t004"
    ) == tmp_path / "workspace/cg/shots/ep02/s027/c001/review/review_build/v003/t004"
    assert paths.shot_review_movie_dir(
        "ep02", "s027", "c001", "anim"
    ) == tmp_path / "workspace/cg/shots/ep02/s027/c001/review/anim/mov"
    assert paths.shot_review_output_root(
        "ep02", "s027", "c001", "anim", "internal"
    ) == tmp_path / "workspace/cg/shots/ep02/s027/c001/output/review/internal"
    assert paths.shot_animation_review_output_root(
        "ep02", "s027", "c001", "anim"
    ) == tmp_path / "workspace/cg/shots/ep02/s027/c001/output/review/animation"
    assert paths.shot_precomp_publish_root(
        "ep02", "s027", "c001"
    ) == tmp_path / "production/shots/ep02/s027/c001/publish/precomp"


def test_project_paths_resolve_shot_context_from_workspace_scene(tmp_path):
    paths = ProjectPaths(
        tmp_path,
        templates={
            "workspace_root": "{project_root}/workspace",
            "production_root": "{project_root}/production",
            "shots_root": "{production_root}/shots",
            "sequences_root": "{production_root}/sequences",
            "shot_root": "{shots_root}/{episode}/{sequence}/{shot}",
            "shot_workspace_root": "{workspace_root}/{workspace_partition}/shots/{episode}/{sequence}/{shot}",
        },
        shot_dept_partitions={"default": "cg"},
    )
    scene = tmp_path / "workspace/cg/shots/ep02/s027/c002/work/anim/maya/preComp/main/scene.ma"

    assert paths.context_root_from_scene_path(scene) == (
        "shot",
        tmp_path / "production/shots/ep02/s027/c002",
    )


def test_project_paths_resolve_sequence_context_from_workspace_scene(tmp_path):
    paths = ProjectPaths(
        tmp_path,
        templates={
            "workspace_root": "{project_root}/workspace",
            "production_root": "{project_root}/production",
            "sequences_root": "{production_root}/sequences",
            "sequence_work_root": "{workspace_root}/{workspace_partition}/sequences/{episode}/{sequence}/work",
        },
        shot_dept_partitions={"default": "cg"},
    )
    scene = tmp_path / "workspace/cg/sequences/ep02/s027/work/layout/maya/scene.ma"

    assert paths.context_root_from_scene_path(scene) == (
        "sequence",
        tmp_path / "production/sequences/ep02/s027",
    )

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


def test_project_paths_resolve_common_work_contract(tmp_path):
    paths = ProjectPaths(
        tmp_path,
        templates={
            "workspace_root": "{project_root}/workspace",
            "assembly_work_root": "{workspace_root}/{workspace_partition}/assemblies/{category}/{group}/{asset_name}/{variant}/work",
            "assembly_work": "{assembly_work_root}/{department}/{dcc}",
            "asset_work_root": "{workspace_root}/{workspace_partition}/assets/{category}/{group}/{asset_name}/{variant}/work",
            "asset_work": "{asset_work_root}/{department}",
            "shot_work_root": "{workspace_root}/{workspace_partition}/shots/{episode}/{sequence}/{shot}/work",
            "shot_work": "{shot_work_root}/{department}",
            "sequence_work_root": "{workspace_root}/{workspace_partition}/sequences/{episode}/{sequence}/work",
            "sequence_work": "{sequence_work_root}/{department}",
        },
        shot_dept_partitions={"default": "cg"},
    )

    assert paths.assembly_work_dir(
        AssemblyIdentity("BG", "main", "Room", "default"), "layout", "maya"
    ) == tmp_path / "workspace/cg/assemblies/BG/main/Room/default/work/layout/maya"
    assert paths.asset_work_dir(
        AssetIdentity("CH", "main", "DLI", "default"), "rig", "maya"
    ) == tmp_path / "workspace/cg/assets/CH/main/DLI/default/work/rig"
    assert paths.shot_work_dir(
        "ep02", "s027", "c001", "anim", "maya"
    ) == tmp_path / "workspace/cg/shots/ep02/s027/c001/work/anim/maya"
    assert paths.sequence_work_dir(
        "ep02", "s027", "layout", "maya"
    ) == tmp_path / "workspace/cg/sequences/ep02/s027/work/layout"


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


def _runtime_source(relative: str) -> str:
    return (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")


def _runtime_function_source(relative: str, name: str) -> str:
    source = _runtime_source(relative)
    tree = ast.parse(source)
    node = next(
        item for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


def test_review_runtime_boundaries_use_semantic_path_resolver_api():
    service = _runtime_source(
        "packages/smartlib/apps/review_build_manager/service.py"
    )
    worker = _runtime_source(
        "packages/smartlib/apps/review_build_manager/worker.py"
    )

    assert "paths=self.shots.paths" in service
    assert "shot_precomp_publish_root(" in worker
    assert "shot_animation_review_output_root(" in worker
    assert 'shot_publish_root(identity) / "precomp"' not in worker
    assert 'shot_output_root(identity) / "review"' not in worker


def test_rv_uses_path_resolver_for_project_and_review_roots():
    rv = "tools/OpenRV/smart-review/smart_review.py"
    production = _runtime_function_source(rv, "_production_root")
    review = _runtime_function_source(rv, "_shot_review_base")

    assert "_project_paths(project_root).production_root()" in production
    assert "replace(" not in production
    assert "shot_review_publish_root(" in review
    assert "shot_review_output_root(" in review
    assert ' / "publish" / "review"' not in review


def test_ae_and_render_graph_only_forward_resolved_review_build_paths():
    ae = _runtime_source("packages/smartlib/review/ae.py")
    render_graph = _runtime_source("packages/smartlib/dcc/maya/render_graph.py")

    assert "SMART_AE_MANIFEST" in ae
    assert "manifest_path" in ae
    assert "resolve_review_build_package(" in render_graph
    assert 'shot_root / "review" / "review_build"' not in render_graph


def test_runtime_has_no_production_hierarchy_outside_resolvers():
    audit_path = REPOSITORY_ROOT / "tools" / "audit_unresolved_paths.py"
    spec = importlib.util.spec_from_file_location("path_resolver_audit", audit_path)
    assert spec is not None and spec.loader is not None
    audit = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = audit
    spec.loader.exec_module(audit)

    findings = audit.dedupe(
        finding
        for path in audit.runtime_files()
        for finding in audit.scan_file(path)
    )
    violations = [
        f"{finding.path}:{finding.line}: {finding.snippet}"
        for finding in findings
        if finding.severity == "P1"
    ]
    assert violations == []


def test_config_creator_exposes_and_preserves_all_path_template_domains(tmp_path):
    from scripts import config_creator

    custom_shot = tmp_path / "templates_shots.yml"
    custom_shot.write_text(
        "templates:\n  custom_shot_cache: '{shot_workspace_root}/cache'\n",
        encoding="utf-8",
    )
    keys = config_creator.domain_path_template_keys(tmp_path)

    assert "shot_workspace_root" in keys["templates_shots.yml"]
    assert "shot_render_layers_root" in keys["templates_shots.yml"]
    assert "shot_review_build" in keys["templates_shots.yml"]
    assert "work_scene_dir" in keys["templates_assets.yml"]
    assert "assembly_work_root" in keys["templates_assemblies.yml"]
    assert "custom_shot_cache" in keys["templates_shots.yml"]

    split = config_creator.split_path_templates(
        {
            "production_root": "{project_root}/prod",
            "shot_review_build": "{shot_review_build_root}/{version}/{take}",
            "custom_shot_cache": "{shot_workspace_root}/cache",
            "asset_publish_root": "{asset_root}/{variant}/publish",
            "assembly_work_root": "{workspace_root}/assemblies",
        },
        tmp_path,
    )

    assert split["templates_base.yml"] == {
        "production_root": "{project_root}/prod"
    }
    assert set(split["templates_shots.yml"]) == {
        "shot_review_build",
        "custom_shot_cache",
    }
    assert set(split["templates_assets.yml"]) == {"asset_publish_root"}
    assert set(split["templates_assemblies.yml"]) == {"assembly_work_root"}
