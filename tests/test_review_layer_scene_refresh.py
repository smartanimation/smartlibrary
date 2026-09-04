"""Exercise scene refresh methods without requiring Maya or Qt."""
import ast
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from smartlib.apps.shot_manager.context import ShotContext


class Combo:
    def __init__(self):
        self.items = []
        self.index = -1

    def blockSignals(self, value):
        pass

    def clear(self):
        self.items.clear()
        self.index = -1

    def addItem(self, label, identity):
        self.items.append(identity)
        if self.index < 0:
            self.index = 0

    def count(self):
        return len(self.items)

    def setCurrentIndex(self, index):
        self.index = index

    def currentData(self):
        return self.items[self.index] if self.index >= 0 else None

    def setVisible(self, value):
        self.visible = value


class Base:
    def showEvent(self, event):
        pass

    def closeEvent(self, event):
        pass


def window_class():
    source = Path(__file__).parents[1] / "scripts" / "review_layer_ui.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "ReviewLayerWindow")
    names = {"showEvent", "closeEvent", "_reset_shot_contents", "_scene_changed",
             "_populate_shots", "_set_context_label", "refresh",
             "_working_shot_identity", "_selection_changed", "_scene_saved", "_refresh_context"}
    cls.body = [node for node in cls.body
                if isinstance(node, ast.FunctionDef) and node.name in names]
    cls.bases = [ast.Name(id="Base", ctx=ast.Load())]
    scope = {"Base": Base, "deepcopy": deepcopy}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
                 str(source), "exec"), scope)
    return scope["ReviewLayerWindow"]


class SceneRefreshTests(unittest.TestCase):
    def setUp(self):
        self.old = SimpleNamespace(code="ep02_s027_c001", episode="ep02", sequence="s027", shot="c001")
        self.new = SimpleNamespace(code="ep02_s027_c002", episode="ep02", sequence="s027", shot="c002")
        self.ui = window_class()()
        ui = self.ui
        ui.shot_context = ShotContext()
        ui.is_maya_session = True
        ui.fixed_identity = True
        ui.identity = self.old
        ui.department = "anim"
        ui._scene_callbacks = []
        ui._layers = {"OLD": {"members": ["old_cast"]}}
        ui._cast = {"old_cast": {}}
        ui.shot_combo = Combo()
        for name in ("layer_list", "cast_search", "member_table", "cast_table",
                     "display_layer_combo", "info_label", "status_label", "context_label"):
            setattr(ui, name, Mock())
        ui._populate_layers = Mock()
        ui._refresh_member_views = Mock()
        ui._normalize_cast_members = Mock(return_value=False)
        ui.service = Mock()
        ui.service.list_shots.return_value = [self.old, self.new]
        ui.service.shot_identity_from_path.return_value = self.new
        ui.service.load_cast.return_value = {"cast": {"new_cast": {"asset": "New"}}}
        ui.service.review_layers.return_value = {"CHA": {"members": ["new_cast"]}}
        cmds = ModuleType("maya.cmds")
        cmds.file = Mock(return_value="new_scene.ma")
        self.cmds = cmds
        self.om = SimpleNamespace(
            MSceneMessage=SimpleNamespace(kAfterOpen=1, kAfterNew=2, kAfterSave=3,
                                         addCallback=Mock(side_effect=[101, 102, 103, 104, 105, 106])),
            MMessage=SimpleNamespace(removeCallback=Mock()),
        )
        maya = ModuleType("maya")
        maya.cmds = cmds
        api = ModuleType("maya.api")
        api.OpenMaya = self.om
        self.modules = patch.dict("sys.modules", {"maya": maya, "maya.cmds": cmds, "maya.api": api})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_scene_open_replaces_fixed_shot_and_discards_drafts(self):
        self.ui._scene_changed(None)
        self.assertEqual(self.ui.identity, self.new)
        self.assertEqual(self.ui.shot_combo.currentData(), self.new)
        self.assertEqual(self.ui._cast, {"new_cast": {"asset": "New"}})
        self.assertEqual(self.ui._layers, {"CHA": {"members": ["new_cast"]}})
        self.ui.cast_search.clear.assert_called_once()
        self.ui.layer_list.clear.assert_called_once()
        self.ui.service.load_cast.assert_called_once_with(self.new)
        self.ui.service.review_layers.assert_called_once_with(self.new, "anim")
        self.ui.service.publish_review_definitions.assert_not_called()

    def test_same_shot_reopen_still_reloads(self):
        self.ui.service.shot_identity_from_path.return_value = self.old
        self.ui._scene_changed()
        self.ui.service.load_cast.assert_called_once_with(self.old)
        self.assertNotIn("OLD", self.ui._layers)

    def assert_empty(self):
        self.assertIsNone(self.ui.identity)
        self.assertEqual(self.ui._layers, {})
        self.assertEqual(self.ui._cast, {})
        self.assertIsNone(self.ui.shot_combo.currentData())
        self.ui.member_table.setRowCount.assert_called_with(0)
        self.ui.cast_table.setRowCount.assert_called_with(0)
        self.ui.context_label.setText.assert_called_with("No shot selected")

    def test_unknown_scene_does_not_fall_back_to_first_shot(self):
        self.ui.service.shot_identity_from_path.return_value = None
        self.ui._scene_changed()
        self.assert_empty()
        self.ui.service.load_cast.assert_not_called()

    def test_new_scene_clears_without_resolving_empty_path(self):
        self.cmds.file.return_value = ""
        self.ui._scene_changed()
        self.assert_empty()
        self.ui.service.shot_identity_from_path.assert_not_called()

    def test_load_failure_discards_partial_data(self):
        self.ui.service.review_layers.side_effect = RuntimeError("read failed")
        self.ui._scene_changed()
        self.assert_empty()
        self.ui.status_label.setText.assert_called_with("Failed to load scene shot: read failed")

    def test_callback_registration_is_unique_and_removed_on_close(self):
        self.ui.showEvent(None)
        self.ui.showEvent(None)
        self.assertEqual(self.om.MSceneMessage.addCallback.call_count, 3)
        self.assertEqual([call.args[0] for call in self.om.MSceneMessage.addCallback.call_args_list], [1, 2, 3])
        self.ui.closeEvent(None)
        self.assertEqual([call.args[0] for call in self.om.MMessage.removeCallback.call_args_list], [101, 102, 103])
        self.assertEqual(self.ui._scene_callbacks, [])
        self.ui.showEvent(None)
        self.assertEqual(self.ui._scene_callbacks, [104, 105, 106])

    def test_maya_selection_never_retargets_open_scene(self):
        self.ui._selection_changed(self.new)
        self.assertEqual(self.ui.identity, self.old)
        self.ui.service.load_cast.assert_not_called()

    def test_initial_unknown_scene_does_not_use_selected_shot(self):
        self.ui.shot_context.select_shot(self.old)
        self.ui.service.shot_identity_from_path.return_value = None
        self.assertIsNone(self.ui._working_shot_identity())

    def test_save_as_changes_shot_but_same_shot_save_preserves_drafts(self):
        self.ui.service.shot_identity_from_path.return_value = self.old
        self.ui._scene_saved()
        self.ui.service.load_cast.assert_not_called()
        self.assertIn("OLD", self.ui._layers)
        self.ui.service.shot_identity_from_path.return_value = self.new
        self.ui._scene_saved()
        self.assertEqual(self.ui.identity, self.new)
        self.assertNotIn("OLD", self.ui._layers)

    def test_standalone_follows_shared_selection_and_clear(self):
        self.ui.is_maya_session = False
        self.ui.fixed_identity = False
        self.ui.showEvent(None)
        self.ui.shot_context.select_shot(self.new)
        self.assertEqual(self.ui.identity, self.new)
        self.ui.shot_context.select_shot(None)
        self.assert_empty()
        self.ui.closeEvent(None)
        self.ui.shot_context.select_shot(self.old)
        self.assertIsNone(self.ui.identity)

    def test_selection_load_failure_clears_partial_contents(self):
        self.ui.is_maya_session = False
        self.ui.fixed_identity = False
        self.ui.service.review_layers.side_effect = RuntimeError("read failed")
        self.ui._selection_changed(self.new)
        self.assert_empty()
        self.ui.status_label.setText.assert_called_with("Failed to load selected shot: read failed")

    def test_refresh_retries_scene_identification_after_initial_miss(self):
        self.ui.identity = None
        self.ui._refresh_context()
        self.assertEqual(self.ui.identity, self.new)
        self.ui.service.load_cast.assert_called_once_with(self.new)

    def test_standalone_window_does_not_register_maya_callbacks(self):
        self.ui.is_maya_session = False
        self.ui.showEvent(None)
        self.om.MSceneMessage.addCallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
