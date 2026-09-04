from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from smartlib.apps.smart_delivery.service import SmartDeliveryService
from smartlib.core.metadata import read_json, write_json
from smartlib.core.path_resolver import ProjectPaths
from smartlib.delivery import PackageProfile


def test_editorial_delivery_path_uses_recipient_batch_and_process(tmp_path):
    paths = ProjectPaths(tmp_path)
    assert paths.delivery_editorial_package(
        "external_editor", "20260901_02", "edit_in", "op_v001"
    ) == (
        tmp_path / "delivery" / "editorial" / "external_editor"
        / "20260901_02" / "edit_in" / "op_v001.zip"
    )


def test_editorial_profile_routes_suggested_output(tmp_path, monkeypatch):
    paths = ProjectPaths(tmp_path / "project")
    today = datetime.now().strftime("%Y%m%d")
    (paths.delivery_editorial_recipient_root("editor_a") / f"{today}_01").mkdir(parents=True)
    service = object.__new__(SmartDeliveryService)
    service.config = SimpleNamespace(project_root=tmp_path / "project")
    service.shots = SimpleNamespace(paths=paths)
    profile = PackageProfile(
        id="editorial", source="smartpipeline", received_from="editor_a",
        asset_subset="editorial", asset_root="production/editorial",
        shot_root="production/editorial", layouts={}, include={},
        delivery_recipient="editor_a", delivery_process="edit_in",
    )
    monkeypatch.setattr(service, "package_profile", lambda _name: profile)

    assert service.suggested_package_output("op_v001", profile="editorial") == (
        tmp_path / "project" / "delivery" / "editorial" / "editor_a"
        / f"{today}_02" / "edit_in" / "op_v001.zip"
    )

def test_editorial_delivery_revision_starts_per_mapping_and_increments_after_build(tmp_path, monkeypatch):
    paths = ProjectPaths(tmp_path / "project")
    mapping = paths.editorial_revision_mapping_path("op", "v002")
    publish = paths.editorial_episode_publish_root("op")
    hud = publish / "revisions/media/CGID-aaaaaaaa_EVID-bbbbbbbb/v004/edit/op_c001_edit_v004.mov"
    hud.parent.mkdir(parents=True)
    hud.write_bytes(b"editorial-hud")
    write_json(mapping, {
        "schema": "smartpipeline.editorial_insert.v2",
        "episode": "op", "timeline_revision": "v002",
        "shots": [{
            "shot": "c001", "export_action": "new", "media_version": "v004",
            "editorial_primary": hud.relative_to(publish).as_posix(),
        }],
    })
    service = object.__new__(SmartDeliveryService)
    service.config = SimpleNamespace(project_root=tmp_path / "project")
    service.shots = SimpleNamespace(paths=paths)
    profile = PackageProfile(
        id="editorial", source="smartpipeline", received_from="editor_a",
        asset_subset="editorial", asset_root="production/editorial",
        shot_root="production/editorial", layouts={}, include={},
        delivery_recipient="editor_a", delivery_process="edit_in",
    )
    monkeypatch.setattr(service, "package_profile", lambda _name: profile)

    first = service.editorial_delivery_context(mapping)
    assert [row.key for row in first["shots"]] == ["c001"]
    assert first["delivery_shots"][0]["status"] == "NEVER DELIVERED"
    assert first["delivery_shots"][0]["needs_delivery"] is True
    assert first["delivery_revision"] == "d001"
    assert first["output"].name == "op_v002_d001.zip"
    service.build_editorial_package(mapping_path=mapping, output=first["output"])

    history = read_json(first["index_path"], {})
    assert history["deliveries"][0]["delivery_revision"] == "d001"
    assert len(history["deliveries"][0]["archive_sha256"]) == 64
    assert history["deliveries"][0]["shots"][0]["media_version"] == "v004"
    second = service.editorial_delivery_context(mapping)
    assert second["delivery_revision"] == "d002"
    assert second["delivery_shots"][0]["status"] == "DELIVERED  v004"
    assert second["delivery_shots"][0]["needs_delivery"] is False
    assert second["delivery_shots"][0]["last_delivery_revision"] == "d001"
    assert second["output"].name == "op_v002_d002.zip"

    next_mapping = paths.editorial_revision_mapping_path("op", "v003")
    write_json(next_mapping, {
        "schema": "smartpipeline.editorial_insert.v2",
        "episode": "op", "timeline_revision": "v003",
        "shots": [{
            "shot": "c001", "export_action": "fixed", "media_version": "v004",
            "editorial_primary": hud.relative_to(publish).as_posix(),
        }],
    })
    across_revision = service.editorial_delivery_context(next_mapping)
    assert across_revision["delivery_shots"][0]["status"] == "DELIVERED  v004"
    assert across_revision["delivery_shots"][0]["needs_delivery"] is False