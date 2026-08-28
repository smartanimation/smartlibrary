import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from smartlib.apps.review_build_manager.service import ReviewBuildManagerService


class BuildContextProfileTests(unittest.TestCase):
    def test_stage_profiles_map_by_asset_class(self):
        service = object.__new__(ReviewBuildManagerService)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            character = root / "hero"
            background = root / "room"
            character.mkdir()
            background.mkdir()
            (character / "asset.json").write_text(
                json.dumps({"asset_type": "CH"}), encoding="utf-8"
            )
            (background / "asset.json").write_text(
                json.dumps({"asset_type": "BG"}), encoding="utf-8"
            )

            self.assertEqual(service.default_asset_context("WORK", character), "ANIM")
            self.assertEqual(service.default_asset_context("FAST", character), "LO")
            self.assertEqual(service.default_asset_context("WORK", background), "PROXY")
            self.assertEqual(service.default_asset_context("REND", background), "REND")
            self.assertEqual(service.default_asset_context("FINAL", background), "REND")


if __name__ == "__main__":
    unittest.main()
