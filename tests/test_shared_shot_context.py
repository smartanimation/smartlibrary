"""Live selection must never become a fallback for an open DCC scene."""
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from smartlib.apps.shot_manager.context import ShotContext, get_shot_context, read_shared_selection


class SharedShotContextTests(unittest.TestCase):
    def test_same_selection_republishes_after_another_process_and_initial_clear(self):
        config = SimpleNamespace(config_dir=Path("P:/config/ELCD"), project_name="ELCD", project_root="")
        identity = SimpleNamespace(episode="ep02", sequence="s027", shot="c001")
        other = SimpleNamespace(episode="ep02", sequence="s027", shot="c002")
        with TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"DCC_CONTEXT_PATH": str(Path(directory) / "context.json")}):
                first, second = ShotContext(config), ShotContext(config)
                first.select_shot(identity)
                second.select_shot(other)
                first.select_shot(identity)
                self.assertEqual(read_shared_selection()["shot"], "c001")
                ShotContext(config).select_shot(None)
                with self.assertRaisesRegex(ValueError, "No shot is selected"):
                    read_shared_selection()

    def test_cli_returns_json_error_for_missing_or_unrelated_context(self):
        from scripts.dcc_context import WorkContext, save_context
        from scripts.read_ae_current_context import main

        with TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"DCC_CONTEXT_PATH": str(Path(directory) / "context.json")}):
                with patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(main(), 1)
                    self.assertFalse(json.loads(output.getvalue())["ok"])
                save_context(WorkContext("ep02", "s027", "c001", extra=None))
                with patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(main(), 1)
                    self.assertIn("No shared Shot Manager selection", json.loads(output.getvalue())["error"])

    def test_selection_snapshot_can_be_read_and_cleared(self):
        config = SimpleNamespace(
            config_dir=Path("P:/config/ELCD"),
            project_name="ELCD",
            project_root=Path("D:/Projects/ELCD"),
        )
        identity = SimpleNamespace(episode="ep02", sequence="s027", shot="c001")
        with TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"DCC_CONTEXT_PATH": str(Path(directory) / "context.json")}):
                context = ShotContext(config)
                context.select_shot(identity)
                payload = read_shared_selection()
                self.assertEqual(payload["project"], "ELCD")
                self.assertEqual(payload["source"], "shot_manager")
                self.assertEqual(
                    (payload["episode"], payload["sequence"], payload["shot"]),
                    ("ep02", "s027", "c001"),
                )
                self.assertIsNone(ShotContext(config).selected_shot)
                context.select_shot(None)
                with self.assertRaisesRegex(ValueError, "No shot is selected"):
                    read_shared_selection()

    def test_project_scoping(self):
        first = SimpleNamespace(config_dir="project_one/settings")
        same = SimpleNamespace(config_dir="project_one/./settings")
        other = SimpleNamespace(config_dir="project_two/settings")
        self.assertIs(get_shot_context(first), get_shot_context(same))
        self.assertIsNot(get_shot_context(first), get_shot_context(other))

    def test_selection_and_scene_are_independent_and_scene_is_not_cached(self):
        context = ShotContext()
        context.select_shot("selected")
        cmds = ModuleType("maya.cmds")
        cmds.file = Mock(return_value="scene_a.ma")
        maya = ModuleType("maya")
        maya.cmds = cmds
        service = SimpleNamespace(shot_identity_from_path=Mock(return_value="scene_a"))
        with patch.dict("sys.modules", {"maya": maya, "maya.cmds": cmds}):
            self.assertEqual(context.scene_shot(service), "scene_a")
            context.select_shot("another_selection")
            self.assertEqual(context.scene_shot(service), "scene_a")
            service.shot_identity_from_path.return_value = None
            self.assertIsNone(context.scene_shot(service))
            cmds.file.return_value = ""
            service.shot_identity_from_path.reset_mock()
            self.assertIsNone(context.scene_shot(service))
            service.shot_identity_from_path.assert_not_called()
        self.assertEqual(context.selected_shot, "another_selection")

    def test_selection_notifications_are_unique_and_can_unsubscribe(self):
        class Listener:
            def __init__(self):
                self.values = []

            def changed(self, identity):
                self.values.append(identity)

        context = ShotContext()
        listener = Listener()
        context.subscribe(listener.changed)
        context.subscribe(listener.changed)
        context.select_shot("shot")
        context.select_shot("shot")
        context.select_shot(None)
        self.assertEqual(listener.values, ["shot", None])
        context.unsubscribe(listener.changed)
        context.select_shot("other")
        self.assertEqual(listener.values, ["shot", None])


if __name__ == "__main__":
    unittest.main()
