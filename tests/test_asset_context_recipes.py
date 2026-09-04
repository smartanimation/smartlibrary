import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from smartlib.apps.asset_manager.context import (
    AssetContextAssembly,
    AssetContextEntry,
    AssetContextService,
)
from smartlib.core.config_loader import load_config
from smartlib.core.path_resolver import AssetIdentity


class AssetContextRecipeTests(unittest.TestCase):
    def test_config_creator_collapses_stage_aliases_and_renames_divergent_profiles(self):
        from scripts.config_creator import ConfigCreatorApp

        data = {
            "quality_profiles": {
                "LO": {"model": "low", "rig": "layout"},
                "ANIM": {"model": "low", "rig": "anim"},
                "REND": {"model": "high", "look": "high"},
                "FAST": {"model": "low", "rig": "layout"},
                "WORK": {"model": "low", "rig": "anim"},
                "FINAL": {"model": "high", "look": "high"},
            },
            "asset_context_recipes": {
                "character": {
                    "profiles": {
                        "LO": {"model": "low", "rig": "layout"},
                    },
                },
                "prop": {
                    "profiles": {
                        "REND": {"model": "render", "look": "high"},
                    },
                },
            },
            "stage_profiles": {
                "FAST": {"character": "FAST", "prop": "FAST"},
                "WORK": {"character": "WORK", "prop": "WORK"},
                "REND": {"character": "FINAL", "prop": "REND"},
            },
        }

        ConfigCreatorApp._normalize_asset_profile_scopes(data)

        self.assertNotIn("FAST", data["quality_profiles"])
        self.assertNotIn("WORK", data["quality_profiles"])
        self.assertNotIn("FINAL", data["quality_profiles"])
        self.assertNotIn("profiles", data["asset_context_recipes"]["character"])
        self.assertEqual(
            data["asset_context_recipes"]["character"]["profile_names"],
            ["CHAR_LO", "CHAR_ANIM", "CHAR_REND"],
        )
        self.assertIn("PROP_REND", data["quality_profiles"])
        self.assertIn("PROP_REND", data["asset_context_recipes"]["prop"]["profile_names"])
        self.assertEqual(data["stage_profiles"]["FAST"]["character"], "CHAR_LO")
        self.assertEqual(data["stage_profiles"]["REND"]["character"], "CHAR_REND")
        self.assertEqual(data["stage_profiles"]["REND"]["prop"], "PROP_REND")

    def test_default_character_and_prop_inherit_common_anim_profile(self):
        context = load_config(
            Path(__file__).parents[1] / "config" / "default" / "contexts" / "asset" / "v001.yml"
        )

        self.assertEqual(
            set(context["quality_profiles"]),
            {
                "CHAR_LO", "CHAR_ANIM", "CHAR_REND", "CHAR_MCP",
                "BG_PROXY", "BG_REND",
                "PROP_LO", "PROP_ANIM", "PROP_REND",
            },
        )
        for asset_class, anim_id in (
            ("character", "CHAR_ANIM"),
            ("prop", "PROP_ANIM"),
        ):
            recipe = context["asset_context_recipes"][asset_class]
            self.assertIn(anim_id, recipe["profile_names"])
            profiles = {
                name: context["quality_profiles"][name]
                for name in recipe["profile_names"]
            }
            self.assertEqual(profiles[anim_id]["rig"], "anim")
        self.assertEqual(context["profile_labels"]["CHAR_REND"], "REND")
        self.assertEqual(context["profile_labels"]["BG_REND"], "REND")
        self.assertEqual(context["profile_labels"]["PROP_REND"], "REND")
        self.assertEqual(context["quality_profiles"]["CHAR_REND"]["groom"], "render")
        self.assertNotIn("groom", context["quality_profiles"]["BG_REND"])
        self.assertNotIn("groom", context["quality_profiles"]["PROP_REND"])

    def test_character_recipe_keeps_project_level_profiles(self):
        with TemporaryDirectory() as temporary:
            asset_root = Path(temporary) / "assets" / "CH" / "main" / "character"
            asset_root.mkdir(parents=True)
            (asset_root / "asset.json").write_text(
                json.dumps({"asset": "character", "asset_type": "CH"}),
                encoding="utf-8",
            )
            service = object.__new__(AssetContextService)
            service.paths = SimpleNamespace(asset_root=lambda _identity: asset_root)
            identity = AssetIdentity("CH", "main", "character", "default")
            context = {
                "quality_profiles": {
                    "WORK": {"model": "proxy", "rig": "anim"},
                    "MCP": {"model": "proxy", "rig": "mocap"},
                },
                "asset_context_recipes": {
                    "character": {
                        "match": {"asset_type": ["CH", "character"]},
                        "profiles": {"WORK": {"model": "low", "rig": "anim"}},
                    }
                },
            }

            asset_class, profiles = service._profiles_for_identity(identity, context)

            self.assertEqual(asset_class, "character")
            self.assertEqual(profiles["WORK"]["model"], "low")
            self.assertEqual(profiles["MCP"]["rig"], "mocap")

    def test_background_uses_assembly_recipe_without_rig_or_groom(self):
        with TemporaryDirectory() as temporary:
            asset_root = Path(temporary) / "assets" / "environment" / "main" / "room"
            asset_root.mkdir(parents=True)
            (asset_root / "asset.json").write_text(
                json.dumps({"asset": "room", "asset_type": "environment"}),
                encoding="utf-8",
            )

            service = object.__new__(AssetContextService)
            service.paths = SimpleNamespace(asset_root=lambda _identity: asset_root)
            context = load_config(
                Path(__file__).parents[1] / "config" / "default" / "contexts" / "asset" / "v001.yml"
            )
            identity = AssetIdentity("environment", "main", "room", "default")

            asset_class, profiles = service._profiles_for_identity(identity, context)

            self.assertEqual(asset_class, "environment")
            self.assertEqual(set(profiles), {"BG_PROXY", "BG_REND"})
            self.assertEqual(profiles["BG_PROXY"]["assembly"], "proxy")
            self.assertIn(profiles["BG_PROXY"]["look"], (None, "none"))
            self.assertNotIn("rig", profiles["BG_PROXY"])
            self.assertNotIn("groom", profiles["BG_PROXY"])
            service.load_context = lambda *_args, **_kwargs: context
            self.assertEqual(
                service.quality_profiles_for_asset(identity),
                ["PROXY", "REND"],
            )

    def test_background_usd_only_assembly_can_be_verified_and_packed(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "assembly.usda"
            source.write_text('#usda 1.0\n\ndef Xform "Room" {}\n', encoding="utf-8")
            identity = AssetIdentity("BG", "main", "room", "default")
            manifest = {
                "context": {"name": "asset", "version": "v001", "quality_profile": "WORK"},
                "resolved_representations": [
                    {
                        "publish_type": "assembly",
                        "requested_subset": "render",
                        "resolved_subset": "render",
                        "version": "v007",
                        "path": str(source.parent),
                        "files": {"usd": str(source)},
                    }
                ],
            }
            assembly = AssetContextAssembly(
                identity=identity,
                context_name="asset",
                context_version="v001",
                quality_profile="WORK",
                entries=[
                    AssetContextEntry(
                        publish_type="assembly",
                        requested_subset="render",
                        resolved_subset="render",
                        version="v007",
                        status="RESOLVED",
                        path=str(source.parent),
                        files={"usd": str(source)},
                        latest_version="v007",
                    )
                ],
                errors=[],
                manifest=manifest,
            )

            service = object.__new__(AssetContextService)
            service.paths = SimpleNamespace(
                asset_publish_dir=lambda _identity, publish_type, subset: (
                    root / "publish" / publish_type / subset
                )
            )
            service.has_pack_changes = lambda _assembly: True

            verification = service.write_assembly(assembly)
            packed = service.pack(assembly, assembled=verification)

            self.assertEqual(verification.scene_path.name, "asset.usda")
            self.assertTrue(service.is_current_assembly(assembly, verification))
            self.assertTrue(packed.usd_path.is_file())
            self.assertTrue((packed.version_dir / "room_default.ma").exists())
            self.assertIn(
                "asset.usda",
                (packed.version_dir / "room_default.ma").read_text(encoding="utf-8"),
            )
            latest = json.loads((packed.version_dir.parent / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["path"], "v001/room_default.ma")
            self.assertEqual(latest["scene"], "v001/room_default.ma")

    def test_current_maya_scene_can_supply_character_context_without_assembly_nodes(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "work" / "character.mb"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"maya-binary-placeholder")
            identity = AssetIdentity("CH", "main", "character", "default")
            missing = AssetContextEntry(
                publish_type="rig",
                requested_subset="anim",
                resolved_subset="",
                version="",
                status="MISSING",
                path="",
                files={},
                message="Missing rig/anim",
            )
            assembly = AssetContextAssembly(
                identity=identity,
                context_name="asset",
                context_version="v001",
                quality_profile="ANIM",
                entries=[missing],
                errors=["Missing rig/anim"],
                manifest={
                    "context": {"name": "asset", "version": "v001", "quality_profile": "ANIM"},
                    "resolved_representations": [],
                    "validation": {"status": "ERROR", "errors": ["Missing rig/anim"]},
                },
            )
            service = object.__new__(AssetContextService)
            service.paths = SimpleNamespace(
                asset_publish_dir=lambda _identity, publish_type, subset: root / "publish" / publish_type / subset
            )

            supplied, verification = service.write_current_scene_assembly(
                assembly,
                source,
                comment="client scene",
            )

            self.assertEqual(supplied.errors, [])
            self.assertEqual(supplied.manifest["source_policy"], "current_scene")
            self.assertEqual(verification.scene_path.name, "asset.mb")
            self.assertEqual(verification.scene_path.read_bytes(), source.read_bytes())
            record = json.loads(verification.assembly_json.read_text(encoding="utf-8"))
            self.assertEqual(record["composition"]["mode"], "current_scene_snapshot")
            self.assertTrue(service.is_current_assembly(supplied, verification))

            service.has_pack_changes = lambda _assembly: True

            def write_usd(_source, target, _asset_name):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("#usda 1.0\n", encoding="utf-8")
                return target

            packed = service.pack(
                supplied,
                assembled=verification,
                maya_usd_builder=write_usd,
            )
            self.assertEqual(packed.scene_path.name, "character_default.mb")
            self.assertEqual(packed.scene_path.read_bytes(), source.read_bytes())
            publish = json.loads(packed.publish_json.read_text(encoding="utf-8"))
            self.assertEqual(publish["files"]["mb"], "character_default.mb")

    def test_pack_does_not_automatically_approve_previous_latest(self):
        with TemporaryDirectory() as temporary:
            versions_path = Path(temporary) / "versions.json"
            versions_path.write_text(
                json.dumps([{"version": "v001", "status": "latest"}]),
                encoding="utf-8",
            )

            AssetContextService._update_versions(versions_path, "v002")

            rows = json.loads(versions_path.read_text(encoding="utf-8"))
            self.assertEqual(rows[0], {"version": "v001", "status": "published"})
            self.assertEqual(rows[1], {"version": "v002", "status": "latest"})

    def test_approve_pack_is_explicit_and_replaces_previous_approval(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            base_dir = root / "publish" / "asset" / "anim"
            for version in ("v001", "v002"):
                version_dir = base_dir / version
                version_dir.mkdir(parents=True)
                (version_dir / "publish.json").write_text("{}", encoding="utf-8")
            (base_dir / "versions.json").write_text(
                json.dumps(
                    [
                        {"version": "v001", "status": "approved"},
                        {"version": "v002", "status": "latest"},
                    ]
                ),
                encoding="utf-8",
            )
            service = object.__new__(AssetContextService)
            service.paths = SimpleNamespace(
                asset_publish_dir=lambda _identity, _publish_type, _subset: base_dir
            )

            service.approve_pack(
                AssetIdentity("CH", "main", "character", "default"),
                quality_profile="ANIM",
                version="v002",
            )

            rows = json.loads((base_dir / "versions.json").read_text(encoding="utf-8"))
            self.assertEqual(rows[0]["status"], "published")
            self.assertEqual(rows[1]["status"], "approved")


if __name__ == "__main__":
    unittest.main()
