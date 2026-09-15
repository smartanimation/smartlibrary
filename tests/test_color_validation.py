import json
from pathlib import Path

import pytest

from smartlib.core.color_settings import color_settings, apply_color_environment, require_compatible, config_fingerprint
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.path_resolver import ProjectPaths


def test_settings_require_acescg_and_boolean():
    assert color_settings({})["enabled"] is False
    with pytest.raises(ValueError):
        color_settings({"color": {"enabled": "false"}})
    with pytest.raises(ValueError):
        color_settings({"color": {"enabled": True}})
    with pytest.raises(ValueError):
        color_settings({"color": {"enabled": True, "config": "config.ocio", "working_space": "sRGB"}})


def test_profile_checks_minor_version(tmp_path):
    config = tmp_path / "config.ocio"
    config.write_text("ocio_profile_version: 2.4\n")
    with pytest.raises(ValueError, match="newer SDK"):
        require_compatible(config, "2.2.1")
    require_compatible(config, "2.4.0")
    require_compatible(config, "2.5.2")


def test_resolver_rejects_run_traversal_and_nested_files(tmp_path):
    paths = ProjectPaths(tmp_path)
    for run in ("../bad", "a/b", "a\\b", "", ".."):
        with pytest.raises(ValueError):
            paths.color_validation_dir(run)
    for filename in ("../bad", "a/b", "a\\b", ""):
        with pytest.raises(ValueError):
            paths.color_validation_file("r1", filename)
    assert paths.color_config_file(tmp_path, "color/config.ocio") == tmp_path / "color/config.ocio"
    assert "output" not in paths.color_validation_dir("r1").parts


def test_fingerprint_changes_and_rejects_external_luts(tmp_path):
    path = tmp_path / "config.ocio"
    path.write_text("ocio_profile_version: 2.2\n")
    first = config_fingerprint(path)
    (tmp_path / "unrelated.txt").write_text("not an OCIO dependency")
    assert config_fingerprint(path) == first
    path.write_text("ocio_profile_version: 2.2\n# edited")
    assert config_fingerprint(path) != first
    path.write_text("ocio_profile_version: 2.2\n!<FileTransform> {}")
    with pytest.raises(ValueError, match="FileTransform"):
        config_fingerprint(path)


def test_launcher_color_wins_over_software_environment(tmp_path, monkeypatch):
    from smartlib.apps.launcher.main import apply_project_color_env
    monkeypatch.setenv("SMARTPIPELINE_STUDIO_CONFIG", str(tmp_path / "missing.yml"))
    monkeypatch.setenv("SMARTPIPELINE_STUDIO_CONFIG_DIR", str(tmp_path))
    config = tmp_path / "config.ocio"
    config.write_text("ocio_profile_version: 2.2\n")
    (tmp_path / "project_settings.yml").write_text("color:\n  enabled: true\n  config: config.ocio\n")
    env = {"OCIO": "stale", "MAYA_COLOR_MANAGEMENT_SYNCOLOR": "1"}
    apply_project_color_env(env, str(tmp_path), "maya2024")
    assert env["OCIO"] == str(config)
    assert "MAYA_COLOR_MANAGEMENT_SYNCOLOR" not in env
    assert json.loads(env["SMARTPIPELINE_COLOR_CONTRACT"])["working_space"] == "ACEScg"


def test_compare_finite_shape_tolerance_and_exr(tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("OpenImageIO")
    from smartlib.color.images import compare, write_exr, read_exr, chart
    reference = chart()
    path = tmp_path / "test.exr"
    write_exr(path, reference, "ACEScg")
    assert compare(reference, read_exr(path))["passed"]
    changed = reference.copy()
    changed[0, 0, 0] = 0.2
    assert not compare(reference, changed)["passed"]
    changed[0, 0, 0] = np.nan
    assert not compare(reference, changed)["passed"]
    assert not compare(reference, reference[:1])["passed"]
    with pytest.raises(ValueError):
        compare(reference, reference, atol=float("inf"))


def test_missing_evidence_fails_and_cannot_be_approved(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    pytest.importorskip("OpenImageIO")
    ocio = pytest.importorskip("PyOpenColorIO")
    from smartlib.color.validation import prepare, compare_run, approve
    monkeypatch.setenv("SMARTPIPELINE_STUDIO_CONFIG", str(tmp_path / "missing.yml"))
    monkeypatch.setenv("SMARTPIPELINE_STUDIO_CONFIG_DIR", str(tmp_path))
    config = ocio.Config.CreateFromBuiltinConfig("ocio://studio-config-v2.2.0_aces-v1.3_ocio-v2.4")
    (tmp_path / "config.ocio").write_text(config.serialize())
    (tmp_path / "project_settings.yml").write_text("color:\n  enabled: true\n  config: config.ocio\n")
    project = ProjectConfig(tmp_path)
    manifest = prepare(project, "test")
    report = compare_run(manifest)
    assert not report["passed"]
    assert not report["checks"]["Maya:evidence"]["passed"]
    with pytest.raises(ValueError, match="incomplete"):
        approve(project, manifest, "baseline", "Test reviewer")
    with pytest.raises(FileExistsError):
        prepare(project, "test")


@pytest.mark.parametrize("hosts", [[], ["Maya", "Maya"], ["unknown"], [None], [["Maya"]], "Maya"])
def test_invalid_host_selection(hosts):
    with pytest.raises(ValueError):
        color_settings({"color": {"validation_hosts": hosts}})


def test_maya_rv_scope_and_launcher_hook():
    from smartlib.apps.launcher.main import rv_color_launch_args
    assert color_settings({"color": {"validation_hosts": ["Maya", "RV"]}})["validation_hosts"] == ["Maya", "RV"]
    assert rv_color_launch_args({}) == []
    assert "smartlib.dcc.rv.color_validation" in rv_color_launch_args({"SMARTPIPELINE_COLOR_CONTRACT": "{}"})[1]
