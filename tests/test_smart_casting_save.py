from __future__ import annotations

import os
from unittest.mock import Mock

import pytest

from smartlib.apps.smart_casting.service import CastingAsset, SmartCastingService
from smartlib.apps.shot_manager import ShotIdentity
from smartlib.core.config_loader import ProjectConfig
from test_smart_casting_paths import write_config, write_json


@pytest.fixture
def casting(tmp_path):
    config_dir = tmp_path / "config"
    write_config(config_dir, tmp_path / "project")
    service = SmartCastingService(ProjectConfig(config_dir))
    shots = [ShotIdentity("ep001", "sq010", f"sh{index:03}") for index in range(3)]
    for shot in shots:
        write_json(
            service.shot_service.shot_root(shot) / "shot.json",
            {"episode": shot.episode, "sequence": shot.sequence, "shot": shot.shot},
        )
    assets = [CastingAsset("character", "main", name, "default") for name in ("JIN", "DLI")]
    rows = [
        {
            "cast_key": asset.asset,
            "asset": asset.asset,
            "variant": asset.variant,
            "category": asset.category,
            "namespace": asset.asset,
            "asset_publish": "approved",
            "required": True,
            "note": "sequence",
        }
        for asset in assets
    ]
    return config_dir, service, shots, assets, rows


@pytest.mark.parametrize("supplied", [False, True])
def test_save_shares_catalog_and_preserves_shot_overrides(casting, monkeypatch, supplied):
    _, service, shots, assets, rows = casting
    override = dict(rows[0], namespace="custom", asset_publish="v003", required=False, note="shot")
    service.save_shot_cast(shots[0], [override])
    before = service.load_shot_cast(shots[0])["cast"]["JIN"]
    catalog = Mock(return_value=assets)
    monkeypatch.setattr(service, "list_assets", catalog)
    options = {"asset_rows": assets} if supplied else {}
    service.save_sequence_cast("ep001", "sq010", rows, **options)
    assert catalog.call_count == (0 if supplied else 1)
    assert service.load_shot_cast(shots[0])["cast"]["JIN"] == before
    for shot in shots:
        assert set(service.load_shot_cast(shot)["cast"]) == {"JIN", "DLI"}
        assert service.shot_service.shot_composition_path(shot).exists()

    catalog.reset_mock()
    writes = Mock(wraps=service.shot_service.write_cast)
    monkeypatch.setattr(service.shot_service, "write_cast", writes)
    service.save_sequence_cast("ep001", "sq010", rows, **options)
    service.save_sequence_cast("ep001", "sq010", rows[:1], **options)
    catalog.assert_not_called()
    writes.assert_not_called()
    assert "DLI" in service.load_shot_cast(shots[0])["cast"]


def test_empty_catalog_is_not_refetched(casting, monkeypatch):
    _, service, shots, _, rows = casting
    catalog = Mock(side_effect=AssertionError("unexpected catalog refresh"))
    monkeypatch.setattr(service, "list_assets", catalog)
    service.save_sequence_cast("ep001", "sq010", rows, asset_rows=[])
    catalog.assert_not_called()
    assert all(not service.load_shot_cast(shot)["cast"] for shot in shots)


def test_namespace_collision_does_not_require_catalog(casting, monkeypatch):
    _, service, shots, _, rows = casting
    for shot in shots:
        service.save_shot_cast(shot, [dict(rows[0], cast_key="existing")])
    catalog = Mock(side_effect=AssertionError("unexpected catalog refresh"))
    monkeypatch.setattr(service, "list_assets", catalog)
    service.save_sequence_cast("ep001", "sq010", rows[:1])
    catalog.assert_not_called()
    assert all(set(service.load_shot_cast(shot)["cast"]) == {"existing"} for shot in shots)


def test_window_publish_keeps_draft_without_refreshing_catalog_or_statuses(casting, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from smartlib.apps.smart_casting import ui

    config_dir, service, shots, assets, rows = casting
    monkeypatch.setattr(service, "list_assets", lambda: assets)
    monkeypatch.setattr(ui, "SmartCastingService", lambda config: service)
    app = ui.QtWidgets.QApplication.instance() or ui.QtWidgets.QApplication([])
    window = ui.SmartCastingWindow(config_dir)
    try:
        key = ("ep001", "sq010")
        window._sequence_cast_drafts[key] = rows
        window._mark_sequence_cast_dirty(key)
        window.populate_asset_table()
        for name in ("list_assets", "cast_context_statuses", "sequence_cast_rows"):
            monkeypatch.setattr(service, name, Mock(side_effect=AssertionError(name)))
        message = Mock()
        errors = Mock()
        monkeypatch.setattr(ui.QtWidgets.QMessageBox, "information", message)
        monkeypatch.setattr(ui.QtWidgets.QMessageBox, "critical", errors)
        assert not hasattr(window, "save_asset_sequence_cast_btn")
        assert window.publish_asset_sequence_cast_btn.text() == "Publish Sequence Cast *"
        assert not service.load_sequence_cast(*key)["cast"]
        assert all(not service.load_shot_cast(shot)["cast"] for shot in shots)
        monkeypatch.setattr(ui.QtWidgets.QInputDialog, "getText", lambda *args: ("", False))
        window.publish_sequence_cast()
        assert key in window._sequence_cast_dirty
        assert not service.load_sequence_cast(*key)["cast"]
        monkeypatch.setattr(ui.QtWidgets.QInputDialog, "getText", lambda *args: ("test", True))
        window.publish_sequence_cast()
        errors.assert_not_called()
        message.assert_called_once()
        assert key not in window._sequence_cast_dirty
        assert window._sequence_cast_drafts[key] == rows
        assert window.asset_table.rowCount() == 2
        assert all(set(service.load_shot_cast(shot)["cast"]) == {"JIN", "DLI"} for shot in shots)

        rows[0]["namespace"] = "JIN_edited"
        window._mark_sequence_cast_dirty(key)
        window.publish_sequence_cast()
        errors.assert_not_called()
        assert service.load_sequence_cast(*key)["cast"]["JIN"]["namespace"] == "JIN_edited"
        assert service.load_shot_cast(shots[0])["cast"]["JIN"]["namespace"] == "JIN"
        for name in ("list_assets", "cast_context_statuses", "sequence_cast_rows"):
            getattr(service, name).assert_not_called()
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_publish_versions_and_shot_preservation(casting):
    _, service, shots, assets, rows = casting
    first = service.publish_sequence_cast("ep001", "sq010", rows=rows, asset_rows=assets)
    assert first.parent.name == "v001"
    original = first.read_bytes()
    second = service.publish_sequence_cast(
        "ep001", "sq010", rows=[dict(rows[0], namespace="new")], asset_rows=assets
    )
    assert second.parent.name == "v002"
    assert first.read_bytes() == original
    assert service.load_sequence_cast("ep001", "sq010")["cast"]["JIN"]["namespace"] == "new"
    for shot in shots:
        cast = service.load_shot_cast(shot)["cast"]
        assert cast["JIN"]["namespace"] == "JIN"
        assert "DLI" in cast


def test_invalid_publish_does_not_change_current_or_shots(casting):
    _, service, shots, assets, rows = casting
    service.publish_sequence_cast("ep001", "sq010", rows=rows, asset_rows=assets)
    before = service.load_sequence_cast("ep001", "sq010")
    shot_before = [service.load_shot_cast(shot) for shot in shots]
    invalid = [dict(row, namespace="duplicate") for row in rows]
    with pytest.raises(ValueError):
        service.publish_sequence_cast("ep001", "sq010", rows=invalid, asset_rows=assets)
    assert service.load_sequence_cast("ep001", "sq010") == before
    assert [service.load_shot_cast(shot) for shot in shots] == shot_before


def test_shared_sequence_tree_drives_both_tabs(casting, monkeypatch, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from smartlib.apps.smart_casting import ui

    config_dir, service, shots, assets, rows = casting
    other = ShotIdentity("ep002", "sq020", "sh100")
    write_json(service.shot_service.shot_root(other) / "shot.json", {
        "episode": other.episode, "sequence": other.sequence, "shot": other.shot,
    })
    service.publish_sequence_cast("ep001", "sq010", rows=rows, asset_rows=assets)
    service.publish_sequence_cast("ep002", "sq020", rows=rows[:1], asset_rows=assets)
    monkeypatch.setattr(service, "list_assets", lambda: assets)
    monkeypatch.setattr(ui, "SmartCastingService", lambda config: service)
    app = ui.QtWidgets.QApplication.instance() or ui.QtWidgets.QApplication([])
    font_id = ui.QtGui.QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    families = ui.QtGui.QFontDatabase.applicationFontFamilies(font_id)
    if families:
        app.setFont(ui.QtGui.QFont(families[0], 9))
    window = ui.SmartCastingWindow(config_dir)
    try:
        window.show()
        app.processEvents()
        assert not hasattr(window, "episode_combo")
        assert not hasattr(window, "sequence_combo")
        assert window.selected_sequence() == ("ep001", "sq010")
        assert window.shot_list.count() == 3
        draft = window._sequence_cast_draft(("ep001", "sq010"))
        draft[0]["note"] = "pending"
        window._mark_sequence_cast_dirty(("ep001", "sq010"))
        window.tabs.setCurrentWidget(window.shots_tab)
        assert window.sequence_tree.isVisible()
        window.shot_list.setCurrentRow(1)
        item = window.shot_cast_list.item(0)
        data = dict(item.data(ui.QtCore.Qt.UserRole))
        data["note"] = "shot pending"
        item.setData(ui.QtCore.Qt.UserRole, data)
        window.tabs.setCurrentWidget(window.assets_tab)
        window.tabs.setCurrentWidget(window.shots_tab)
        assert window.current_shot_identity() == shots[1]
        assert window.shot_cast_list.item(0).data(ui.QtCore.Qt.UserRole)["note"] == "shot pending"

        window.focus_context(episode="ep002", sequence="sq020", shot="sh100")
        assert window.selected_sequence() == ("ep002", "sq020")
        assert window.current_shot_identity() == other
        assert window.sequence_cast_list.count() == 1
        assert window.asset_table.rowCount() == 1
        window.tabs.setCurrentWidget(window.assets_tab)
        assert window.selected_sequence() == ("ep002", "sq020")
        assert window._sequence_cast_draft(("ep001", "sq010"))[0]["note"] == "pending"
        window.tabs.setCurrentWidget(window.shots_tab)
        app.processEvents()
        window.grab().save(str(tmp_path / "shared-sequence-shots.png"))

        window.sequence_tree.setCurrentItem(window.sequence_tree.topLevelItem(0))
        assert window.selected_sequence() is None
        assert window.shot_list.count() == 0
        assert window.sequence_cast_list.count() == 0
        assert window.shot_cast_list.count() == 0
        window.focus_context(episode="ep001", sequence="sq010", shot=shots[0].shot)
        assert window.current_shot_identity() == shots[0]
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
