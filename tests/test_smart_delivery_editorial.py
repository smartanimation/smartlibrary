from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from smartlib.apps.smart_delivery.service import SmartDeliveryService
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
