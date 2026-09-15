from dataclasses import replace
import pytest
from smartlib.apps.asset_manager.studio_delivery import StudioDeliveryService, read, write_new, digest
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.path_resolver import AssetIdentity


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    monkeypatch.delenv("SMARTPIPELINE_STUDIO_CONFIG", raising=False)
    monkeypatch.delenv("SMARTPIPELINE_STUDIO_CONFIG_DIR", raising=False)
    config = tmp_path / "config"
    config.mkdir()
    (config / "templates_base.yml").write_text(f'anchors:\n  project_root: "{tmp_path.as_posix()}"\n  project_name: test\n', encoding="utf-8")
    context = config / "contexts" / "studio_delivery"
    context.mkdir(parents=True)
    (context / "v001.yml").write_text("enabled: true\n", encoding="utf-8")
    service = StudioDeliveryService(ProjectConfig(config))
    identity = AssetIdentity("character", "hero", "Alice")
    add_source(service, identity)
    return service, identity


def add_source(service, identity):
    root = service.paths.asset_publish_version_dir(identity, "asset", "mcp", "v012")
    root.mkdir(parents=True, exist_ok=True)
    source = service.paths.artifact_file(root, "Alice.ma")
    source.write_bytes(b"source scene")
    return source


def passed_job(service, identity):
    job = read(service.prepare(identity, "v012"))
    from pathlib import Path
    Path(job["output"]).write_bytes(b"validated fbx")
    write_new(job["report"], {"run_id": job["run_id"], "status": "passed", "sha256": digest(job["output"]), "source_sha256": job["source"]["sha256"]})
    return job


def test_asset_wide_numbering_and_studio_history(delivery):
    service, identity = delivery
    job = passed_job(service, identity)
    first = service.finalize(identity, job["run_id"])
    assert first["version"] == "v001" and first["source"]["version"] == "v012"
    assert service.finalize(identity, job["run_id"])["version"] == "v001"
    for studio in ("Studio A", "Studio B"):
        service.record_sent(identity, "v001", studio)
    assert len(service.releases(identity)) == 1 and len(service.events(identity)) == 2
    other = replace(identity, variant="costume")
    add_source(service, other)
    second = service.finalize(other, passed_job(service, other)["run_id"])
    assert second["version"] == "v002"
    assert len(service.releases(identity)) == 2


def test_failed_export_does_not_allocate_version(delivery):
    service, identity = delivery
    failed = read(service.prepare(identity, "v012"))
    with pytest.raises(FileNotFoundError):
        service.finalize(identity, failed["run_id"])
    result = service.finalize(identity, passed_job(service, identity)["run_id"])
    assert result["version"] == "v001"


@pytest.mark.parametrize("changed", ["source", "output", "settings"])
def test_changed_inputs_block_finalization(delivery, changed):
    from pathlib import Path
    service, identity = delivery
    job = passed_job(service, identity)
    if changed == "settings":
        config = service.config.config_dir / "contexts/studio_delivery/v001.yml"
        config.write_text("enabled: true\nfbx:\n  units: m\n", encoding="utf-8")
        service = StudioDeliveryService(ProjectConfig(service.config.config_dir))
    else:
        Path(job["source"]["path"] if changed == "source" else job["output"]).write_bytes(b"changed")
    with pytest.raises(ValueError):
        service.finalize(identity, job["run_id"])
    assert service.releases(identity) == []


def test_disabled_project_and_explicit_source(delivery):
    service, identity = delivery
    for version in ("latest", "approved", "v999", "../v012"):
        with pytest.raises(ValueError):
            service.prepare(identity, version)
    config = service.config.config_dir / "contexts/studio_delivery/v001.yml"
    config.write_text("enabled: false\n", encoding="utf-8")
    service = StudioDeliveryService(ProjectConfig(service.config.config_dir))
    with pytest.raises(ValueError, match="disabled"):
        service.prepare(identity, "v012")


def test_modified_release_cannot_be_recorded_sent(delivery):
    from pathlib import Path
    service, identity = delivery
    release = service.finalize(identity, passed_job(service, identity)["run_id"])
    Path(release["fbx"]).write_bytes(b"modified")
    with pytest.raises(ValueError):
        service.record_sent(identity, release["version"], "Studio A")
    assert not service.events(identity)


def test_resolver_templates_and_traversal(delivery):
    from smartlib.core.path_resolver import ProjectPaths
    service, identity = delivery
    paths = ProjectPaths(service.paths.project_root, templates={"asset_publish_root": "{project_root}/custom/{asset_name}/{variant}/publish"})
    assert "custom" in paths.studio_delivery_path(identity, "publish", "v001").parts
    for area, entry, filename in (("bad", "", ""), ("jobs", "../escape", ""), ("events", "", "../bad.json")):
        with pytest.raises(ValueError):
            paths.studio_delivery_path(identity, area, entry, filename)


def test_release_lock_prevents_concurrent_allocation(delivery):
    service, identity = delivery
    job = passed_job(service, identity)
    lock = service.paths.studio_delivery_path(identity, "publish", filename="release.lock")
    lock.parent.mkdir(parents=True)
    lock.write_text("other process")
    with pytest.raises(RuntimeError, match="Another release"):
        service.finalize(identity, job["run_id"])
    assert lock.exists()


def test_received_motion_keeps_delivery_link_and_own_copy(delivery, tmp_path):
    from pathlib import Path
    service, identity = delivery
    release = service.finalize(identity, passed_job(service, identity)["run_id"])
    received = tmp_path / "take.fbx"
    received.write_bytes(b"motion")
    receipt = service.record_received(identity, "v001", "Studio B", received, "walk_01")
    received.unlink()
    assert Path(receipt["path"]).read_bytes() == b"motion"
    assert receipt["delivery_sha256"] == release["sha256"]
    assert receipt["take"] == "walk_01"
    assert len(service.releases(identity)) == 1
    assert service.events(identity)[0]["kind"] == "received"


def test_source_publish_uses_configured_resolver_root(delivery):
    service, identity = delivery
    config = service.config.config_dir / "templates_assets.yml"
    config.write_text("templates:\n  asset_publish_root: '{project_root}/custom/{category}/{group}/{asset_name}/{variant}/publish'\n", encoding="utf-8")
    service = StudioDeliveryService(ProjectConfig(service.config.config_dir))
    source = add_source(service, identity)
    assert service.sources(identity)[0]["path"] == str(source)
    assert read(service.prepare(identity, "v012"))["source"]["path"] == str(source)
