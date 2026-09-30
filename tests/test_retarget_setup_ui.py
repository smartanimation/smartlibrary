from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtWidgets

from smartlib.apps.retarget_setup import window as ui
from smartlib.apps.retarget_setup.service import RetargetService
from smartlib.core.path_resolver import AssetIdentity, ProjectPaths


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    paths = ProjectPaths(tmp_path)
    assets = []
    for name in ("DLI", "SECOND"):
        identity = AssetIdentity("CH", "main", name)
        asset = SimpleNamespace(category="CH", group="main", name=name, root=paths.asset_root(identity))
        assets.append(asset)
        service = RetargetService(paths, identity)
        service.save_draft({"asset": name, "time_unit": "film", "source_skeleton": {"namespace_agnostic_prefix": "MC_"}, "transfer_nodes": {"controls": ["CTL_allLocal"]}, "pole_vectors": {"left_arm": {"enabled": True, "distance_ratio": 1.0}}})
    manager = SimpleNamespace(paths=paths, config_dir=tmp_path, list_assets=lambda: assets)
    monkeypatch.setattr(ui, "ProjectConfig", lambda path: None)
    widget = ui.RetargetWindow(manager)
    widget.show()
    app.processEvents()
    yield widget, app
    widget.dirty = False
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_basic_setting_changes_profile_and_invalidates_test(window):
    widget, app = window
    widget.data_path = "saved.json"
    widget.job = {"status": "passed", "reviewed": True}
    widget.update_publish()
    assert widget.publish_button.isEnabled()
    widget.poles["left_arm"][1].setValue(1.25)
    assert widget.current_profile()["pole_vectors"]["left_arm"]["distance_ratio"] == 1.25
    assert widget.dirty and widget.data_path is None and widget.job is None
    assert not widget.publish_button.isEnabled()


def test_character_switch_clears_test_and_data(window):
    widget, app = window
    widget.job = {"status": "passed", "reviewed": True}
    widget.data_path = "previous.json"
    widget.characters.setCurrentIndex(1)
    assert widget.current_profile()["asset"] == "SECOND"
    assert widget.data_path is None and widget.job is None
    assert not widget.publish_button.isEnabled()


def test_running_disables_all_test_input_changes(window):
    widget, app = window
    widget.set_running(True)
    assert not widget.characters.isEnabled()
    assert not widget.motion.isEnabled()
    assert not widget.motion_browse_button.isEnabled()
    assert not widget.setup_page.isEnabled()
    assert widget.cancel_button.isEnabled()
    widget.set_running(False)


def test_draft_save_does_not_publish_and_survives_reload(window):
    widget, app = window
    widget.poles["left_arm"][1].setValue(1.5)
    widget.save_draft()
    assert not widget.dirty and not widget.publish_button.isEnabled()
    widget.characters.setCurrentIndex(1)
    widget.characters.setCurrentIndex(0)
    assert widget.current_profile()["pole_vectors"]["left_arm"]["distance_ratio"] == 1.5


def test_advanced_settings_retain_basic_controls(window):
    widget, app = window
    widget.editor.setPlainText('{"pole_vectors":{"left_arm":{"enabled":false,"distance_ratio":2.0}}}')
    assert not widget.poles["left_arm"][0].isChecked()
    assert widget.poles["left_arm"][1].value() == 2.0


def test_maya_failed_start_restores_controls(window, monkeypatch, tmp_path):
    from PySide6 import QtTest
    widget, app = window
    for key, edit in widget.rigs.items():
        path = tmp_path / (key + ".mb")
        path.write_bytes(b"rig")
        edit.setText(str(path))
    motion = tmp_path / "motion.fbx"
    motion.write_bytes(b"motion")
    widget.motion.setText(str(motion))
    monkeypatch.setattr(ui, "resolve_mayapy", lambda cfg: tmp_path / "missing-mayapy.exe")
    monkeypatch.setattr(ui, "process_environment", lambda cfg: ({}, {}))
    widget.run_test()
    for _ in range(100):
        app.processEvents()
        if widget.process is None:
            break
        QtTest.QTest.qWait(10)
    assert widget.process is None
    assert widget.job["status"] == "failed"
    assert widget.run_button.isEnabled() and widget.characters.isEnabled()
    assert not widget.review.isEnabled() and not widget.publish_button.isEnabled()


def test_transfer_node_exclusion_updates_profile(window):
    widget, app = window
    from PySide6 import QtCore
    group = widget.nodes.topLevelItem(0)
    node = group.child(0)
    node.setCheckState(1, QtCore.Qt.Unchecked)
    assert widget.current_profile()["excluded_transfer_nodes"] == ["CTL_allLocal"]
    assert widget.dirty and not widget.publish_button.isEnabled()


def test_direct_mcr_mapping_edit_roundtrip(window):
    widget, app = window
    widget.new_direct_mapping()
    widget.add_mapping()
    row = widget.nodes.topLevelItem(0)
    row.setText(0, "MC_LeftHand")
    row.setText(2, "A_L_wrist")
    row.setText(3, "orient")
    widget.reference_frame.setValue(1)
    profile = widget.current_profile()
    assert profile["input_mode"] == "mcr_to_anim"
    assert profile["mappings"][0]["source"] == "MC_LeftHand"
    assert profile["mappings"][0]["target"] == "A_L_wrist"
    assert profile["mappings"][0]["maintain_offset"] is False
    assert profile["reference_frame"] == 1
    assert not widget.publish_button.isEnabled()


def test_result_history_restores_review_and_scene_path(window, tmp_path, monkeypatch):
    from smartlib.apps.retarget_setup.service import write
    widget, app = window
    profile=widget.current_profile()
    for key in ('mcr_scene','animation_rig_scene'):
        path=tmp_path/(key+'.mb');path.write_bytes(b'rig');profile[key]=str(path)
    data=widget.service.save_data(profile)
    motion=tmp_path/'test.fbx';motion.write_bytes(b'fbx')
    job=widget.service.prepare_test(profile,motion,1,10,data_path=data)
    scene=widget.service.path('test',job['run_id'],job['result_file']);scene.write_bytes(b'maya')
    write(widget.service.path('test',job['run_id'],'report.json'),{'keyed_plugs':6})
    job=widget.service.review_test(widget.service.complete_test(job,0),True)
    widget.refresh_tests()
    widget.test_results.setCurrentItem(widget.test_results.topLevelItem(0))
    widget.load_test_result()
    assert widget.job['run_id']=='v001' and widget.data_path==data
    assert widget.review.isChecked() and widget.publish_button.isEnabled()
    calls=[]
    monkeypatch.setattr(ui,'open_result_in_current_maya',lambda path:calls.append(path))
    widget.open_result()
    assert calls==[scene]


def test_asset_tab_keeps_character_editors_separate(window):
    from smartlib.apps.retarget_setup.asset_tab import RetargetTab
    widget, app=window
    tab=RetargetTab(widget.manager)
    assets=widget.manager.list_assets()
    tab.set_asset(assets[0]);tab.activate()
    first=tab.views[str(assets[0].root)]
    first.comment.setText('DLI comment')
    tab.set_asset(assets[1]);tab.activate()
    assert tab.stack.currentWidget().current_profile()['asset']=='SECOND'
    tab.set_asset(assets[0]);tab.activate()
    assert tab.stack.currentWidget() is first and first.comment.text()=='DLI comment'
    first.process=object()
    assert not tab.can_close()
    first.process=None
    for view in tab.views.values():view.dirty=False
    assert tab.can_close()
    tab.deleteLater()


def test_common_motion_button_sets_fbx_and_manifest_range(window):
    from smartlib.apps.retarget_setup.service import write
    widget,app=window
    root=widget.service.paths.retarget_library('test_motion');root.mkdir(parents=True,exist_ok=True)
    (root/'common.fbx').write_bytes(b'fbx')
    write(root/'manifest.json',{'file':'common.fbx','frame_range':[1,254],'time_unit':'film'})
    widget.use_common_motion()
    assert widget.motion.text()==str(root/'common.fbx')
    assert widget.start.value()==1 and widget.end.value()==254
    widget.new_direct_mapping()
    assert widget.nodes.topLevelItemCount()==66
    assert all(not row['maintain_offset'] for row in widget.current_profile()['mappings'])
    assert all(w.isHidden() for w in widget.rig_rows['mcr_scene'])



def test_open_result_reuses_asset_manager_scene_open(monkeypatch,tmp_path):
    import sys
    from types import ModuleType
    from scripts import asset_manager_ui
    maya=ModuleType('maya');cmds=ModuleType('maya.cmds')
    cmds.about=lambda **kwargs:False
    maya.cmds=cmds
    monkeypatch.setitem(sys.modules,'maya',maya)
    monkeypatch.setitem(sys.modules,'maya.cmds',cmds)
    calls=[]
    monkeypatch.setattr(asset_manager_ui,'open_scene_in_current_dcc',lambda path:calls.append(path))
    path=tmp_path/'result.ma'
    ui.open_result_in_current_maya(path)
    assert calls==[path]
    cmds.about=lambda **kwargs:True
    with pytest.raises(RuntimeError,match='interactive Maya'):
        ui.open_result_in_current_maya(path)
    assert calls==[path]


def test_open_result_outside_maya_requires_host_session(monkeypatch,tmp_path):
    import sys
    monkeypatch.setitem(sys.modules,'maya',None)
    monkeypatch.setitem(sys.modules,'maya.cmds',None)
    with pytest.raises(RuntimeError,match='inside the running Maya'):
        ui.open_result_in_current_maya(tmp_path/'result.ma')


def test_opening_history_requires_explicit_visual_ok(window,tmp_path,monkeypatch):
    from smartlib.apps.retarget_setup.service import write
    widget,_=window
    profile=widget.current_profile()
    for key in ('mcr_scene','animation_rig_scene'):
        path=tmp_path/(key+'.mb');path.write_bytes(b'rig');profile[key]=str(path)
    data=widget.service.save_data(profile)
    motion=tmp_path/'motion.fbx';motion.write_bytes(b'fbx')
    job=widget.service.prepare_test(profile,motion,1,10,data_path=data)
    widget.service.path('test',job['run_id'],job['result_file']).write_bytes(b'maya')
    write(widget.service.path('test',job['run_id'],'report.json'),{'keyed_plugs':6})
    widget.service.complete_test(job,0)
    widget.refresh_tests();widget.test_results.setCurrentItem(widget.test_results.topLevelItem(0))
    opened=[]
    monkeypatch.setattr(ui,'open_result_in_current_maya',lambda path:opened.append(path))
    widget.open_selected_result()
    assert opened and widget.job['run_id']==job['run_id'] and widget.data_path==data
    assert widget.review.isEnabled() and not widget.review.isChecked()
    assert not widget.publish_button.isEnabled()
    assert 'Visual review OK' in widget.publish_hint.text()
    widget.review.setChecked(True)
    assert widget.publish_button.isEnabled()
    assert widget.service.restore_test(job['run_id'])[1]['reviewed']
