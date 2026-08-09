from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from smartlib.apps.asset_manager.construct import AssetConstructService


class AssetConstructServiceTests(unittest.TestCase):
    def test_discovers_versions_and_restores_recipe_selection(self):
        with TemporaryDirectory() as temporary:
            variant_root = Path(temporary) / "default"
            v001 = variant_root / "data" / "geo" / "body" / "high" / "v001" / "geo.fbx"
            v002 = variant_root / "data" / "geo" / "body" / "high" / "v002" / "geo.fbx"
            model = variant_root / "data" / "model" / "body" / "high" / "v001" / "model.mb"
            for path in (v001, v002, model):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            service = AssetConstructService(variant_root)
            discovered = service.discover()

            self.assertEqual(len(discovered), 2)
            geo = next(item for item in discovered if item.data_type == "geo")
            self.assertEqual(geo.target, "body")
            self.assertEqual(geo.representation, "high")
            self.assertEqual(geo.latest_version, "v002")
            self.assertEqual(geo.selected_version, "v002")

            geo.selected_version = "v001"
            geo.use = False
            service.save_recipe(discovered, asset="hero", variant="default")
            restored = next(item for item in service.discover() if item.data_type == "geo")

            self.assertEqual(restored.selected_version, "v001")
            self.assertFalse(restored.use)
            self.assertEqual(restored.state, "UPDATE AVAILABLE")


if __name__ == "__main__":
    unittest.main()
