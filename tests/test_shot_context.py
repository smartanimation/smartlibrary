from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from smartlib.apps.shot_manager.service import ShotIdentity, ShotManagerService


class _ProjectConfig:
    def __init__(self, root: Path):
        self.project_root = root
        self.project_name = "TEST"
        self.config_dir = root / "settings"
        self.base = {"anchors": {"project_name": "TEST"}}
        self.templates = {}


class ShotContextTests(unittest.TestCase):
    def test_builds_immutable_context_versions_and_latest_alias(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = ShotManagerService(_ProjectConfig(root))
            identity = ShotIdentity("ep01", "sq01", "sh0010")
            source = root / "assets" / "env" / "set" / "room" / "asset.usda"
            source.parent.mkdir(parents=True)
            source.write_text("#usda 1.0\n", encoding="utf-8")
            components = [
                {
                    "use": True,
                    "type": "background",
                    "name": "room_main",
                    "subset": "work",
                    "version": "v003",
                    "load_policy": "payload",
                    "source": str(source),
                    "state": "READY",
                }
            ]

            first = service.build_shot_context(
                identity, department="anim", profile="WORK", components=components, comment="first"
            )
            second = service.build_shot_context(
                identity, department="anim", profile="WORK", components=components, comment="second"
            )

            self.assertEqual(first.parent.name, "v001")
            self.assertEqual(second.parent.name, "v002")
            self.assertTrue(first.is_file())
            text = second.read_text(encoding="utf-8")
            self.assertIn('def Scope "Background"', text)
            self.assertIn("payload = @", text)
            latest = json.loads((second.parent.parent / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest, {"version": "v002", "path": "v002/context.usda"})
            resolved, version = service.latest_shot_context(
                identity, department="anim", profile="WORK"
            )
            self.assertEqual(resolved, second)
            self.assertEqual(version, "v002")
            self.assertTrue((second.parent / "context.json").is_file())
            self.assertTrue((second.parent / "build_manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
