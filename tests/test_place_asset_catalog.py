import json
from types import SimpleNamespace

import pytest

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.path_resolver import AssetIdentity, configured_project_paths
from smartlib.apps.asset_manager.published_catalog import published_prop_cards


@pytest.fixture
def config(tmp_path):
    config_dir = tmp_path / 'config'
    config_dir.mkdir()
    (config_dir / 'templates_base.yml').write_text(
        "anchors:\n  project_name: TEST\n  project_root: '" + (tmp_path / 'project').as_posix() + "'\n",
        encoding='utf-8')
    return ProjectConfig(config_dir)


def publish(config, group, name, variant, context, version, *, building=False, extension='mb'):
    paths = configured_project_paths(config.project_root, config)
    identity = AssetIdentity('prop', group, name, variant)
    directory = paths.asset_publish_version_dir(identity, 'asset', context, version)
    directory.mkdir(parents=True, exist_ok=True)
    paths.artifact_file(paths.asset_variant_root(identity), 'variant.json').write_text('{}')
    paths.artifact_file(paths.asset_root(identity), 'asset.json').write_text(json.dumps({'status': 'Approved'}))
    paths.artifact_file(directory, name + '.' + extension).write_text('fixture')
    if building:
        paths.artifact_file(directory, '_building').touch()


def test_catalog_uses_asset_manager_all_groups_and_completed_maya_publishes(config):
    publish(config, 'main', 'DeleinChair', 'default', 'anim', 'v005')
    publish(config, 'main', 'DeleinChair', 'default', 'anim', 'v006', building=True)
    publish(config, 'main', 'DeleinChair', 'red', 'render', 'v001')
    publish(config, 'bp', 'Desk', 'default', 'proxy', 'v002', extension='ma')
    publish(config, 'main', 'Unfinished', 'default', 'anim', 'v001', building=True)
    publish(config, 'main', 'UsdOnly', 'default', 'proxy', 'v001', extension='usd')
    rows = published_prop_cards(config)
    assert {(r['group'], r['asset']) for r in rows} == {('main', 'DeleinChair'), ('bp', 'Desk')}
    chair = next(r for r in rows if r['asset'] == 'DeleinChair')
    assert chair['status'] == 'Approved'
    assert set(chair['variants']) == {'default', 'red'}
    assert [r['version'] for r in chair['variants']['default']['anim']] == ['v005']


@pytest.fixture
def panel(monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from smartlib.apps.asset_assembly import place_panel
    app = place_panel.QtWidgets.QApplication.instance() or place_panel.QtWidgets.QApplication([])
    cards = [dict(category='prop', group='main', asset='DeleinChair', status='Approved',
                  description='', thumbnail='', variants={
                      'default': {'anim': [{'version': 'v005', 'path': 'chair.mb'}]},
                      'red': {'render': [{'version': 'v002', 'path': 'red.mb'}]}}),
             dict(category='prop', group='bp', asset='Desk', status='', description='', thumbnail='',
                  variants={'default': {'proxy': [{'version': 'v001', 'path': 'desk.mb'}]}})]
    monkeypatch.setattr(place_panel, 'published_prop_cards', lambda _: cards)
    panel = place_panel.PlaceAssetPanel(SimpleNamespace())
    panel.reload_assets()
    yield panel
    panel.close()


def test_card_selection_populates_only_published_choices(panel):
    assert not panel.replace_button.isEnabled()
    panel.asset_list.setCurrentRow(0)
    assert panel.identity() == AssetIdentity('prop', 'main', 'DeleinChair', 'default')
    assert panel.context.currentText() == 'anim'
    assert panel.version.currentText() == 'v005'
    assert panel.source.text() == 'prop/main/DeleinChair/default/anim/v005'
    panel.variant.setCurrentText('red')
    assert panel.context.currentText() == 'render'
    assert panel.version.currentData() == 'red.mb'
    panel.asset_list.setCurrentRow(1)
    assert panel.identity().name == 'Desk'
    assert panel.context.currentText() == 'proxy'


def test_search_and_missing_saved_version_cannot_apply_stale_selection(panel):
    panel.asset_list.setCurrentRow(0)
    panel.search.setText('prop/main/DeleinChair/default/anim')
    assert not panel.asset_list.item(0).isHidden()
    assert panel.asset_list.item(1).isHidden()
    panel.search.setText('Desk')
    assert not panel.replace_button.isEnabled()
    assert panel.asset_list.item(0).isHidden()
    panel.search.clear()
    assert panel.select_publish('main', 'DeleinChair', 'default', 'anim', 'v005')
    panel.reload_assets()
    assert panel.version.currentText() == 'v005'
    assert not panel.select_publish('main', 'DeleinChair', 'default', 'anim', 'v999')
    assert panel.version.currentIndex() == -1
    assert not panel.new_button.isEnabled()


def test_place_asset_is_single_tab_and_extract_link_works(config, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from smartlib.apps.asset_assembly import ui, place_panel
    app = ui.QtWidgets.QApplication.instance() or ui.QtWidgets.QApplication([])
    monkeypatch.setattr(place_panel, 'published_prop_cards', lambda _: [])
    window = ui.AssetAssemblyWindow(config.config_dir)
    window.selection_timer.stop()
    assert [window.assembly_tabs.tabText(i) for i in range(window.assembly_tabs.count())] == ['Extract / Publish', 'Place Asset']
    window.assembly_tabs.setCurrentWidget(window.place_panel)
    window.place_panel.extract_requested.emit()
    assert window.assembly_tabs.currentWidget() is window.extract_panel
    window.close()
