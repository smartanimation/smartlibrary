import pytest
from test_usd_handoff import service

from smartlib.apps.shot_manager.setdress_mapping import resolve, prim_record


@pytest.fixture
def stage():
    from pxr import Usd, UsdGeom
    stage = Usd.Stage.CreateInMemory()
    for target in ('chairA', 'chairB'):
        root = UsdGeom.Xform.Define(stage, '/Shot/Assets/' + target).GetPrim()
        root.SetCustomDataByKey('smartpipeline', dict(cast_key=target, name='Chair', usd_path='asset/v001/asset.usd'))
        prim = UsdGeom.Xform.Define(stage, str(root.GetPath()) + '/seat').GetPrim()
        prim.SetCustomDataByKey('smartpipeline:setdress:sourceId', 'asset-node-id')
        prim.SetCustomDataByKey('smartpipeline:setdress:sourcePath', '|Root|seat')
    return stage


def test_asset_mapping_scopes_repeated_asset_instances(stage):
    nodes = {'ref-uuid': '|layout|A:Root|A:seat'}
    cast = {'chairA': {'namespace': 'A'}, 'chairB': {'namespace': 'B'}}
    result = resolve(stage, nodes, cast)['ref-uuid']
    assert result['selected'] == '/Shot/Assets/chairA/seat'
    assert result['state'] == 'Asset mapping'
    # UUID alone cannot disambiguate two copies without their Cast identity.
    assert not resolve(stage, {'asset-node-id': '|Root|seat'}, cast)['asset-node-id']['selected']


def test_legacy_names_suggest_without_automatic_assignment(stage):
    for prim in stage.Traverse():
        prim.ClearCustomDataByKey('smartpipeline:setdress')
    result = resolve(stage, {'id': '|A:Root|A:seat'}, {'chairA': {'namespace': 'A'}})['id']
    assert not result['selected']
    assert result['candidates'][0] == '/Shot/Assets/chairA/seat'
    assert result['state'] == 'Name candidates — confirm'
    assert 'without mapping metadata' in result['reason']


def test_saved_mapping_validated_against_asset_version_and_prim(stage):
    path = '/Shot/Assets/chairA/seat'
    saved = {'id': prim_record(stage, path)}
    assert resolve(stage, {'id': '|oldName'}, {}, saved)['id']['selected'] == path
    stage.GetPrimAtPath('/Shot/Assets/chairA').SetCustomDataByKey('smartpipeline:usd_path', 'asset/v002/asset.usd')
    result = resolve(stage, {'id': '|oldName'}, {}, saved)['id']
    assert not result['selected'] and result['state'] == 'Asset changed — confirm mapping'
    stage.RemovePrim(path)
    assert path not in resolve(stage, {'id': '|oldName'}, {}, saved)['id']['candidates']


def test_explicit_stable_id_supports_renamed_source(stage):
    result = resolve(stage, {'uuid': '|A:Root|A:renamed'}, {'chairA': {'namespace': 'A'}},
                     source_ids={'uuid': 'asset-node-id'})['uuid']
    assert result['selected'] == '/Shot/Assets/chairA/seat'
    with pytest.raises(ValueError):
        prim_record(stage, '/Shot/Camera/primary')


def test_export_stamps_only_exact_existing_paths():
    from pxr import Usd, UsdGeom
    from smartlib.dcc.maya.setdress_mapping import stamp_asset
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, '/Root/seat')
    class Cmds:
        def objExists(self, plug):
            return False
        def ls(self, node, uuid=False):
            return [node + '-id']
    assert stamp_asset(stage, ['|Root', '|Root|seat', '|Root|missing'], Cmds()) == 2
    prim = stage.GetPrimAtPath('/Root/seat')
    assert prim.GetCustomDataByKey('smartpipeline:setdress:sourcePath') == '|Root|seat'
    assert prim.GetCustomDataByKey('smartpipeline:setdress:sourceId') == '|Root|seat-id'


def test_panel_suggests_saves_and_restores_mapping(stage, service, tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6 import QtWidgets
    from smartlib.apps.shot_manager.layout_publish_panel import LayoutPublishPanel
    from smartlib.dcc.maya import set_dress, setdress_mapping
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    svc, identity = service
    monkeypatch.setattr(svc.shots, 'shot_frame_range', lambda _: (1, 2))
    monkeypatch.setattr(svc.shots, 'load_cast', lambda _: {'cast': {'chairA': {'namespace': 'A'}}})
    package = set_dress.SetDressPackage(layers=[set_dress.SetDressLayer(name='seat', changes=[
        set_dress.Change('id', '|A:Root|A:seat', 'translateY', 0, 1)])])
    monkeypatch.setattr(set_dress, 'load_package_from_scene', lambda: (package, ''))
    monkeypatch.setattr(setdress_mapping, 'scene_nodes', lambda nodes: (nodes, {}))
    stored = {}
    monkeypatch.setattr(setdress_mapping, 'load', lambda _: dict(stored))
    def save(_, records, **kwargs):
        for key in kwargs.get('replace_keys', ()):
            stored.pop(key, None)
        stored.update(records)
    monkeypatch.setattr(setdress_mapping, 'save', save)
    path = tmp_path / 'shot.usda'
    stage.GetRootLayer().Export(str(path))
    panel = LayoutPublishPanel(svc.shots, is_maya_session=True)
    monkeypatch.setattr(panel.service, 'load_handoff', lambda _: {'entrypoint': {'path': str(path)}})
    panel.set_sources(identity, 'set_dress', ['seat'])
    panel.base_combo.addItem('v001', 'receipt')
    panel.base_combo.setCurrentIndex(panel.base_combo.count() - 1)
    assert panel.mapping.cellWidget(0, 1).currentText() == '/Shot/Assets/chairA/seat'
    assert panel.mapping.item(0, 2).text() == 'Asset mapping'
    panel.mapping.cellWidget(0, 1).setCurrentText('/Shot/Assets/chairB/seat')
    panel.save_mapping_btn.click()
    assert stored['id']['path'] == '/Shot/Assets/chairB/seat'
    panel._populate_mapping()
    assert panel.mapping.cellWidget(0, 1).currentText() == '/Shot/Assets/chairB/seat'
    assert panel.mapping.item(0, 2).text() == 'Saved mapping'
    monkeypatch.setattr(panel.service, 'composition_versions', lambda _: [
        dict(version='v002', path='new-receipt'), dict(version='v001', path='receipt')])
    panel.suggest_btn.click()
    assert panel.base_combo.currentData() == 'receipt'
    assert panel.base_combo.findData('new-receipt') >= 0
    assert 'Mapping refreshed' in panel.status.toPlainText()
    assert 'Newer Composition available: v002' in panel.status.toPlainText()
    assert panel.mapping.horizontalScrollBar().value() == 0
    panel.mapping.cellWidget(0, 1).setCurrentText('')
    panel.save_mapping_btn.click()
    assert not stored
    assert '0 / 1 mappings stored' in panel.status.toPlainText()
    panel.close()
