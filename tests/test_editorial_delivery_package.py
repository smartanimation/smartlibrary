from __future__ import annotations

import json
import zipfile

from smartlib.core.metadata import write_json
from smartlib.core.path_resolver import ProjectPaths
from smartlib.delivery.editorial_exporter import (
    EditorialPackageBuilder,
    resolve_editorial_package_source,
)


def test_editorial_package_selection_is_independent_of_mapping_omit(tmp_path):
    publish = tmp_path / "production/editorial/publish/op"
    mapping_path = publish / "revisions/metadata/v001/editorial_mapping.json"
    hud = publish / "revisions/media/CGID-aaaaaaaa_EVID-11111111/v005/edit/op_c001_edit_v005.mov"
    omitted = publish / "revisions/media/CGID-bbbbbbbb_EVID-22222222/v002/edit/op_c002_edit_v002.mov"
    hud.parent.mkdir(parents=True)
    omitted.parent.mkdir(parents=True)
    hud.write_bytes(b"hud")
    omitted.write_bytes(b"omit")
    registry = publish / "identity/shot_registry.json"
    write_json(registry, {"schema": "smartpipeline.cg_shot_registry.v2"})
    write_json(mapping_path, {
        "schema": "smartpipeline.editorial_insert.v2",
        "episode": "op", "timeline_revision": "v001",
        "shots": [
            {
                "shot": "c001", "export_action": "new", "media_version": "v005",
                "editorial_primary": hud.relative_to(publish).as_posix(),
            },
            {
                "shot": "c002", "export_action": "omit", "media_version": "v002",
                "event_storage_id": "CGID-bbbbbbbb_EVID-22222222",
            },
        ],
    })

    paths = ProjectPaths(tmp_path)
    source = resolve_editorial_package_source(mapping_path, paths=paths)
    assert source.episode == "op"
    assert source.timeline_revision == "v001"
    assert [path for path, _member in source.media] == [hud, omitted]

    output = tmp_path / "delivery/editorial/external_editor/20260901_01/edit_in/op_v001.zip"
    result = EditorialPackageBuilder().build(
        mapping_path=mapping_path, output=output,
        recipient="external_editor", process="edit_in",
        delivery_revision="d001", delivery_batch="20260901_01",
        selected_shot_keys={"c001"}, paths=paths,
    )
    assert result.archive == output
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            "manifest.json", "metadata/editorial_mapping.json",
            "metadata/shot_registry.json", f"media/edit/{hud.name}",
        }
        packaged_mapping = json.loads(archive.read("metadata/editorial_mapping.json"))
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["delivery"]["delivery_revision"] == "d001"
        assert manifest["delivery"]["delivery_batch"] == "20260901_01"
        assert manifest["files"][0]["shot_key"] == "c001"
        assert manifest["files"][0]["media_version"] == "v005"
        assert packaged_mapping["shots"][0]["package_editorial_primary"] == f"media/edit/{hud.name}"
        assert omitted.name not in archive.namelist()
        assert manifest["selection"] == {
            "selected_shot_keys": ["c001"],
            "excluded_shot_keys": ["c002"],
        }
    original_mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    assert "package_editorial_primary" not in original_mapping["shots"][0]


def test_selected_legacy_omit_uses_resolved_existing_hud(tmp_path):
    paths = ProjectPaths(tmp_path)
    mapping = paths.editorial_revision_mapping_path("op", "v004")
    hud = paths.editorial_event_media_edit_dir(
        "op", "CGID-aaaaaaaa_EVID-bbbbbbbb", "v002"
    ) / "op_c001_edit_v002.mov"
    hud.parent.mkdir(parents=True)
    hud.write_bytes(b"hud")
    write_json(mapping, {
        "schema": "smartpipeline.editorial_insert.v2",
        "episode": "op", "timeline_revision": "v004",
        "shots": [{
            "shot": "c001", "editorial_event_uid": "event-c001",
            "event_storage_id": "CGID-aaaaaaaa_EVID-bbbbbbbb",
            "export_action": "omit",
        }],
    })

    output = tmp_path / "delivery.zip"
    result = EditorialPackageBuilder().build(
        mapping_path=mapping, output=output,
        recipient="external_editor", process="edit_in",
        delivery_revision="d001", delivery_batch="20260901_01",
        selected_shot_keys={"event-c001"}, paths=paths,
    )

    assert result.manifest["files"][0]["media_version"] == "v002"
    with zipfile.ZipFile(output) as archive:
        assert f"media/edit/{hud.name}" in archive.namelist()
        packaged = json.loads(archive.read("metadata/editorial_mapping.json"))
        assert packaged["shots"][0]["package_editorial_primary"] == f"media/edit/{hud.name}"