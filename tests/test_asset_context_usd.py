from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from smartlib.dcc.maya.asset_context import _write_asset_entry_layer


class AssetContextUsdTests(unittest.TestCase):
    def test_asset_entry_has_default_prim_and_remaps_payload_root(self):
        try:
            from pxr import Usd
        except ImportError:
            self.skipTest("USD Python bindings are not installed")

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload.usda"
            payload.write_text(
                "#usda 1.0\n\n"
                'def Xform "__SMART_DLI_ROOT__"\n'
                "{\n"
                '    def Xform "geo" {}\n'
                '}\n',
                encoding="utf-8",
            )
            entry = _write_asset_entry_layer(
                root / "asset.usda",
                asset_name="DLI",
                payload_name=payload.name,
                payload_root="/__SMART_DLI_ROOT__",
            )

            stage = Usd.Stage.Open(entry.as_posix())

            self.assertTrue(stage)
            self.assertEqual(stage.GetDefaultPrim().GetPath().pathString, "/DLI")
            self.assertTrue(stage.GetPrimAtPath("/DLI/geo"))

    def test_asset_name_is_normalized_to_a_valid_usd_identifier(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = _write_asset_entry_layer(
                root / "asset.usda",
                asset_name="01 hero-A",
                payload_name="payload.usd",
                payload_root="/__SMART_ROOT__",
            )

            text = entry.read_text(encoding="utf-8")
            self.assertIn('defaultPrim = "_01_hero_A"', text)
            self.assertIn('def SkelRoot "_01_hero_A"', text)

    def test_static_asset_entry_uses_xform_root(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = _write_asset_entry_layer(
                root / "asset.usda",
                asset_name="RoomA",
                payload_name="payload.usd",
                payload_root="/RoomA",
                root_type="Xform",
            )

            text = entry.read_text(encoding="utf-8")
            self.assertIn('def Xform "RoomA"', text)
            self.assertNotIn("SkelRoot", text)


if __name__ == "__main__":
    unittest.main()
