from smartlib.preflight import PreflightContext, PreflightEngine, Severity
from smartlib.preflight.profiles import create_asset_profile, create_shot_profile
from smartlib.dcc.maya.preflight import MayaPreflightAdapter, resolve_context


class FakeAdapter:
    def __init__(self):
        self.missing = []
        self.non_manifold = []

    def scene_path(self):
        return "/project/shots/sq010/sh020/animation/sh020_anim_v018.ma"

    def scene_modified(self):
        return False

    def missing_references(self):
        return self.missing

    def unknown_nodes(self):
        return []

    def non_manifold_meshes(self):
        return self.non_manifold

    def asset_roots(self):
        return ["|hero_asset"]

    def renderable_cameras(self):
        return ["|shotCam"]

    def frame_range(self):
        return (1001, 1120)

    def animation_curve_count(self):
        return 42

    def missing_cast(self, _context):
        return []

    def cast_version_issues(self, _context):
        return []

    def duplicate_namespaces(self, _context):
        return []

    def resolution(self):
        return (1920, 1080)

    def non_horizontal_cameras(self):
        return []

    def object_set_exists(self, name):
        return name in {"allRigSet", "cache_geo_set"}

    def object_set_members(self, name):
        return [f"|hero|{name}_member"]


def test_asset_and_shot_are_profiles_of_same_engine():
    adapter = FakeAdapter()
    asset = PreflightEngine(adapter, create_asset_profile()).run(
        PreflightContext(kind="asset", entity="hero")
    )
    shot = PreflightEngine(adapter, create_shot_profile()).run(
        PreflightContext(kind="shot", entity="sh020", metadata={"frame_range": [1001, 1120]})
    )
    assert asset.profile == "asset"
    assert shot.profile == "shot"
    assert not asset.blocked
    assert not shot.blocked
    assert {row.key for row in asset.results} != {row.key for row in shot.results}


def test_output_selection_controls_related_checks():
    report = PreflightEngine(FakeAdapter(), create_shot_profile()).run(
        PreflightContext(kind="shot", entity="sh020"),
        selected_outputs=("maya_scene", "metadata"),
    )
    keys = {row.key for row in report.results}
    assert "renderable_camera" not in keys
    assert "animation_curves" not in keys
    assert "frame_range" in keys


def test_errors_block_publish_report():
    adapter = FakeAdapter()
    adapter.non_manifold = ["|hero|body_geoShape"]
    report = PreflightEngine(adapter, create_asset_profile()).run(
        PreflightContext(kind="asset", entity="hero")
    )
    result = next(row for row in report.results if row.key == "non_manifold_geometry")
    assert result.severity == Severity.ERROR
    assert report.blocked
    assert result.nodes == ("|hero|body_geoShape",)


def test_report_is_serializable(tmp_path):
    report = PreflightEngine(FakeAdapter(), create_shot_profile()).run(
        PreflightContext(kind="shot", entity="sh020")
    )
    target = PreflightEngine.write_report(report, tmp_path / "preflight.json")
    text = target.read_text(encoding="utf-8")
    assert '"schema": "smart_preflight/v1"' in text
    assert report.attempt_id in text


class ContextCmds:
    def __init__(self, path):
        self.path = path

    def file(self, **kwargs):
        if kwargs.get("sceneName"):
            return self.path
        return False


def test_maya_context_resolves_asset_and_shot_profiles():
    asset = resolve_context(ContextCmds("P:/show/assets/char/Hero/model/work/Hero_model_v003.ma"))
    shot = resolve_context(ContextCmds("P:/show/shots/ep01/sq010/sh020/anim/sh020_anim_v018.ma"))
    assert (asset.kind, asset.entity, asset.task, asset.version) == (
        "asset", "Hero", "Model", "v003"
    )
    assert (shot.kind, shot.entity, shot.task, shot.version) == (
        "shot", "sh020", "Animation", "v018"
    )


def test_asset_required_sets_and_shot_camera_policy_are_profile_checks():
    asset_keys = {row.key for row in create_asset_profile().checks}
    shot_keys = {row.key for row in create_shot_profile().checks}
    assert {"all_rig_set", "cache_geo_set"} <= asset_keys
    assert {
        "cast_assets_exist",
        "cast_versions",
        "namespace_duplicates",
        "renderable_camera",
        "resolution",
        "camera_film_fit",
    } <= shot_keys


def test_unloaded_cast_is_warning_for_partial_shot_work():
    adapter = FakeAdapter()
    adapter.missing_cast = lambda _context: ["Crowd_main: reference not found"]
    report = PreflightEngine(adapter, create_shot_profile()).run(
        PreflightContext(kind="shot", entity="sh020", metadata={"cast": {"Crowd_main": {}}})
    )
    result = next(row for row in report.results if row.key == "cast_assets_exist")
    assert result.severity == Severity.WARNING
    assert not report.blocked


class RootCmds:
    def ls(self, **kwargs):
        return ["|Root", "|rig_controllers_grp", "|persp"]


def test_named_root_is_preferred_over_other_top_level_groups():
    assert MayaPreflightAdapter(RootCmds()).asset_roots() == ["|Root"]
