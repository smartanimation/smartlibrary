from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from smartlib.apps.smart_delivery.service import SmartDeliveryService
from smartlib.core.path_resolver import ProjectPaths


def test_vendor_delivery_paths_use_the_common_resolver(tmp_path):
    paths = ProjectPaths(
        tmp_path,
        {
            "delivery_root": "{project_root}/delivery",
            "deliveries_vendors_dir": "{delivery_root}/vendors",
            "delivery_vendor_root": "{deliveries_vendors_dir}/{studio_id}",
            "delivery_vendor_batch": "{delivery_vendor_root}/{delivery_batch}",
            "delivery_vendor_package": "{delivery_vendor_batch}/{entity}.zip",
        },
        "TEST",
    )

    assert paths.delivery_vendor_package(
        "vendor_a", "20260829_01", "asset_a"
    ) == tmp_path / "delivery" / "vendors" / "vendor_a" / "20260829_01" / "asset_a.zip"


def test_smart_delivery_uses_studio_id_and_next_batch(tmp_path, monkeypatch):
    studio_config = tmp_path / "smartprojects" / "studio.yml"
    studio_config.parent.mkdir()
    studio_config.write_text(
        "studio:\n  id: vendor_a\n  name: Vendor A\n  role: vendor\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMARTPIPELINE_STUDIO_CONFIG", str(studio_config))

    paths = ProjectPaths(tmp_path / "project")
    today = datetime.now().strftime("%Y%m%d")
    (paths.delivery_vendor_root("vendor_a") / f"{today}_01").mkdir(parents=True)

    service = object.__new__(SmartDeliveryService)
    service.config = SimpleNamespace(project_root=tmp_path / "project")
    service.shots = SimpleNamespace(paths=paths)

    assert service.delivery_preferences()["studio_id"] == "vendor_a"
    assert service.suggested_package_output("asset_a") == (
        tmp_path
        / "project"
        / "delivery"
        / "vendors"
        / "vendor_a"
        / f"{today}_02"
        / "asset_a.zip"
    )


def test_internal_delivery_preferences_include_client_identity(tmp_path, monkeypatch):
    studio_config = tmp_path / "studio.yml"
    studio_config.write_text(
        "studio:\n  id: internal\n  name: Internal\n  role: internal\n"
        "client:\n  id: client_a\n  name: Client A\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMARTPIPELINE_STUDIO_CONFIG", str(studio_config))

    service = object.__new__(SmartDeliveryService)

    assert service.delivery_preferences()["client_id"] == "client_a"
    assert service.delivery_preferences()["client_name"] == "Client A"


def test_smart_delivery_rejects_unsafe_studio_id(tmp_path, monkeypatch):
    studio_config = tmp_path / "studio.yml"
    studio_config.write_text("studio:\n  id: ../outside\n", encoding="utf-8")
    monkeypatch.setenv("SMARTPIPELINE_STUDIO_CONFIG", str(studio_config))

    service = object.__new__(SmartDeliveryService)
    service.config = SimpleNamespace(project_root=tmp_path / "project")
    service.shots = SimpleNamespace(paths=ProjectPaths(tmp_path / "project"))

    try:
        service.suggested_package_output("asset_a")
    except ValueError as exc:
        assert "Invalid Studio ID" in str(exc)
    else:
        raise AssertionError("Unsafe Studio ID was accepted")
