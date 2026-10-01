"""Texture ingestion and actual standalone Qt publishing against disposable projects."""
import os
from pathlib import Path
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

from smartlib.apps.asset_manager.preview_publish import PreviewPublishService
from smartlib.apps.asset_manager.texture_intake import TextureInput, collect_textures
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json
from smartlib.core.path_resolver import AssetIdentity


@pytest.fixture
def publisher(tmp_path, monkeypatch):
    monkeypatch.delenv('SMARTPIPELINE_STUDIO_CONFIG', raising=False)
    monkeypatch.delenv('SMARTPIPELINE_STUDIO_CONFIG_DIR', raising=False)
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'templates_base.yml').write_text(
        f"anchors:\n  project_name: TEST\n  project_root: '{tmp_path.as_posix()}/project'\n", encoding='utf8')
    return PreviewPublishService(ProjectConfig(config))


IDENTITY = AssetIdentity('character', 'main', 'DLI', 'default')


def test_copy_texture_settings_to_multiple_selected_rows(publisher, tmp_path):
    from smartlib.apps.asset_manager.texture_publish_ui import TexturePublishDialog, QtCore, QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = TexturePublishDialog(publisher, IDENTITY, ingest=True)
    paths = [tmp_path / f'color.{1001+i}.png' for i in range(4)]
    for path in paths:
        path.write_bytes(b'texture')
    dialog.add_paths(paths)
    original = [(entry.path, entry.udim) for entry in dialog.entries()]
    dialog.table.cellWidget(0, 3).setCurrentText('base_color')
    dialog.table.cellWidget(0, 4).setCurrentText('sRGB')
    dialog.table.selectRow(0)
    dialog.copy_settings()
    dialog.table.clearSelection()
    for row in (1, 2):
        dialog.table.selectionModel().select(dialog.table.model().index(row, 0),
            QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
    dialog.paste_settings()
    entries = dialog.entries()
    assert [(e.usage, e.color_space) for e in entries] == [('base_color', 'sRGB')] * 3 + [('unspecified', 'unspecified')]
    assert [(e.path, e.udim) for e in entries] == original
    QtWidgets.QApplication.clipboard().setText('unrelated clipboard text')
    dialog.paste_settings()
    assert dialog.entries() == entries
    dialog.close()


def test_nested_intake_metadata_and_immutable_versions(publisher, tmp_path):
    source = tmp_path / 'received'
    source.mkdir()
    nested = source / 'body'
    nested.mkdir()
    texture = nested / 'base.1001.png'
    texture.write_bytes(b'received texture')
    (source / 'notes.txt').write_text('vendor notes')
    entries, ignored = collect_textures([source, texture])
    assert len(entries) == 1 and entries[0].udim == 1001
    assert [p.name for p in ignored] == ['notes.txt']
    first = publisher.publish_textures(IDENTITY, entries=[TextureInput(texture, 'base_color', 'sRGB')])
    record = read_json(first, {})
    original = first.read_bytes()
    assert record['textures']['texture_000'] == dict(usage='base_color', color_space='sRGB', udim=1001)
    assert Path(record['artifacts']['texture_000']['path']).read_bytes() == b'received texture'
    texture.write_bytes(b'updated texture')
    second = publisher.publish_textures(IDENTITY, entries=entries)
    assert read_json(second, {})['version'] == 'v002'
    assert first.read_bytes() == original
    assert Path(record['artifacts']['texture_000']['path']).read_bytes() == b'received texture'


def test_collision_and_invalid_selection_do_not_reserve_version(publisher, tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    a.mkdir()
    b.mkdir()
    (a / 'Body.png').write_bytes(b'a')
    (b / 'body.PNG').write_bytes(b'b')
    with pytest.raises(ValueError, match='Duplicate texture filename'):
        publisher.publish_textures(IDENTITY, entries=[TextureInput(a/'Body.png'), TextureInput(b/'body.PNG')])
    with pytest.raises(ValueError, match='No textures'):
        publisher.publish_textures(IDENTITY, entries=[])
    assert not publisher.paths.asset_publish_dir(IDENTITY, 'texture', 'low').exists()


def test_copy_failure_keeps_latest(publisher, tmp_path, monkeypatch):
    texture = tmp_path / 'color.png'
    texture.write_bytes(b'original')
    first = publisher.publish_textures(IDENTITY, entries=[TextureInput(texture)])
    root = publisher.paths.asset_publish_dir(IDENTITY, 'texture', 'low')
    latest = publisher.paths.artifact_file(root, 'latest.json').read_bytes()
    def broken_copy(source, output):
        Path(output).write_bytes(b'broken')
    monkeypatch.setattr('smartlib.apps.asset_manager.preview_publish.shutil.copy2', broken_copy)
    with pytest.raises(ValueError, match='changed during publication'):
        publisher.publish_textures(IDENTITY, entries=[TextureInput(texture)])
    assert publisher.paths.artifact_file(root, 'latest.json').read_bytes() == latest
    assert not publisher.paths.artifact_file(root / 'v002', 'publish.json').exists()
    assert first.exists()


def test_qt_drop_and_publish_without_dcc(publisher, tmp_path):
    from smartlib.apps.asset_manager.texture_publish_ui import TexturePublishDialog, QtCore, QtGui, QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    source = tmp_path / 'color.png'
    image = QtGui.QImage(8, 8, QtGui.QImage.Format_RGB32)
    image.fill(QtGui.QColor('red'))
    assert image.save(str(source))
    dialog = TexturePublishDialog(publisher, IDENTITY)
    mime = QtCore.QMimeData()
    mime.setUrls([QtCore.QUrl.fromLocalFile(str(source))])
    event = QtGui.QDropEvent(QtCore.QPointF(10, 10), QtCore.Qt.CopyAction, mime,
                            QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
    dialog.table.dropEvent(event)
    assert dialog.table.rowCount() == 1
    dialog.table.cellWidget(0, 3).setCurrentText('base_color')
    dialog.table.cellWidget(0, 4).setCurrentText('sRGB')
    dialog.publish_button.click()
    deadline = time.monotonic() + 10
    while dialog.manifest is None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert dialog.manifest, dialog.status.text()
    record = read_json(dialog.manifest, {})
    assert record['textures']['texture_000']['color_space'] == 'sRGB'
    assert record['variant'] == 'default'
    dialog.close()


def test_asset_manager_texture_tab_lists_complete_publishes(publisher, tmp_path, monkeypatch):
    from scripts.asset_manager import Asset, AssetManager
    from scripts.asset_manager_ui import AssetManagerWindow, QtWidgets, QtCore
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    # Avoid external catalog refresh and user window preferences in this UI test.
    for method in ('refresh_assets', '_restore_window_geometry', '_restore_window_state'):
        monkeypatch.setattr(AssetManagerWindow, method, lambda self: None)
    window = AssetManagerWindow(AssetManager(tmp_path / 'config'))
    asset = Asset('character', 'main', 'DLI', publisher.paths.asset_root(IDENTITY))
    monkeypatch.setattr(window, '_current_asset', lambda: asset)
    monkeypatch.setattr(window, '_current_asset_variant', lambda: 'default')
    def no_dcc():
        raise AssertionError('Texture tab must not query a DCC scene')
    monkeypatch.setattr(window, '_publish_source_path', no_dcc)
    texture = tmp_path / 'base.png'
    texture.write_bytes(b'image')
    publisher.publish_textures(IDENTITY, entries=[TextureInput(texture)])
    publisher.reserve(IDENTITY, 'texture', 'low')  # incomplete publish is not displayed
    for row in range(window.asset_publish_type_list.count()):
        if window.asset_publish_type_list.item(row).data(QtCore.Qt.UserRole) == 'texture':
            window.asset_publish_type_list.setCurrentRow(row)
            break
    assert not window.publish_texture_btn.isHidden()
    assert window.publish_texture_btn.isEnabled()
    assert window.publish_selected_btn.isHidden()
    assert window.asset_publish_tree.topLevelItemCount() == 1
    branch = window.asset_publish_tree.topLevelItem(0)
    assert branch.childCount() == 2  # active file and publish history
    assert branch.child(0).text(0) == 'base.png'
    assert branch.child(0).text(1) == 'v001'
    assert branch.child(1).childCount() == 1
    window._populate_data_tree(asset)
    window._publish_textures()
    assert window.detail_tabs.currentWidget() == window.data_tab
    assert not window.texture_data_panel.isHidden()
    assert window.data_list.isHidden()
    assert window.texture_data_panel.table.rowCount() == 1
    window.deleteLater()
    app.processEvents()


def test_data_intake_selected_publish_and_inherited_composition(publisher, tmp_path):
    from smartlib.apps.asset_manager.texture_data import TextureDataService
    service = TextureDataService(publisher.project_config)
    source = tmp_path / 'received'
    source.mkdir()
    for name in ('body.png', 'face.png', 'hair.png'):
        (source / name).write_bytes(name.encode())
    entries, _ = collect_textures([source])
    service.ingest(IDENTITY, entries)
    assert len(service.catalog(IDENTITY)) == 3
    assert service.texture_snapshot(IDENTITY)[0] == {}
    first = service.publish_data(IDENTITY, ['body.png', 'face.png'])
    first_bytes = first.read_bytes()
    old, _ = service.texture_snapshot(IDENTITY)
    assert set(old) == {'body.png', 'face.png'}
    (source / 'body.png').write_bytes(b'new body')
    service.ingest(IDENTITY, [TextureInput(source / 'body.png')])
    second = service.publish_data(IDENTITY, ['body.png'])
    current, _ = service.texture_snapshot(IDENTITY)
    assert current['body.png']['version'] == 'v002'
    assert current['face.png'] == old['face.png']
    assert 'hair.png' not in current
    record = read_json(second, {})
    assert len(record['artifacts']) == 2 and len(record['files']) == 1
    assert record['previous'] == service.pin(first)
    assert first.read_bytes() == first_bytes
    service.publish_textures(IDENTITY, entries=[], removed=['face.png'])
    assert set(service.texture_snapshot(IDENTITY)[0]) == {'body.png'}
    assert len(service.catalog(IDENTITY)) == 3  # removal never deletes Data or old published images
    assert Path(old['face.png']['artifact']['path']).exists()


def test_changed_inherited_texture_blocks_new_publish(publisher, tmp_path):
    a, b = tmp_path/'a.png', tmp_path/'b.png'
    a.write_bytes(b'a')
    b.write_bytes(b'b')
    publisher.publish_textures(IDENTITY, entries=[TextureInput(a)])
    snapshot, _ = publisher.texture_snapshot(IDENTITY)
    Path(snapshot['a.png']['artifact']['path']).write_bytes(b'changed')
    with pytest.raises(ValueError, match='Published texture changed'):
        publisher.publish_textures(IDENTITY, entries=[TextureInput(b)])


def test_data_panel_publishes_only_selected_rows(publisher, tmp_path):
    from smartlib.apps.asset_manager.texture_data_ui import TextureDataPanel, QtWidgets, QtCore
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = TextureDataPanel(publisher.project_config)
    a, b = tmp_path/'a.png', tmp_path/'b.png'
    a.write_bytes(b'a')
    b.write_bytes(b'b')
    panel.service.ingest(IDENTITY, [TextureInput(a), TextureInput(b)])
    panel.set_identity(IDENTITY)
    assert panel.table.rowCount() == 2
    panel.table.selectRow(0)
    panel.publish_button.click()
    deadline = time.monotonic() + 10
    while not panel.isEnabled() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert panel.isEnabled(), panel.status.text()
    snapshot, _ = panel.service.texture_snapshot(IDENTITY)
    assert set(snapshot) == {'a.png'}
    assert panel.table.item(0, 3).text() == 'Published'
    assert panel.table.item(1, 3).text() == 'New'
    panel.deleteLater()


def test_legacy_manifest_is_displayed_and_inherited(publisher, tmp_path):
    from smartlib.core.metadata import write_json
    path = tmp_path/'old.png'
    path.write_bytes(b'old')
    manifest = publisher.publish_textures(IDENTITY, entries=[TextureInput(path)])
    record = read_json(manifest, {})
    for key in ('origins', 'changed', 'removed', 'previous', 'textures'):
        record.pop(key)
    write_json(manifest, record)
    other = tmp_path/'new.png'
    other.write_bytes(b'new')
    publisher.publish_textures(IDENTITY, entries=[TextureInput(other)])
    snapshot, _ = publisher.texture_snapshot(IDENTITY)
    assert snapshot['old.png']['version'] == 'v001'
    assert snapshot['new.png']['version'] == 'v002'
