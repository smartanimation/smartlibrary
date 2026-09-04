"""Use the real path resolver and shot metadata, including Workspace scenes."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from smartlib.apps.shot_manager.service import ShotIdentity, ShotManagerService
from smartlib.core.path_resolver import ProjectPaths


class ShotSceneIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.service = ShotManagerService.__new__(ShotManagerService)
        self.service.project_config = SimpleNamespace(base={"shot_depts": ["anim", "comp"]})
        self.service.paths = ProjectPaths(
            self.root, shot_dept_partitions={"default": "cg", "comp": "2d"}
        )
        self.shot = ShotIdentity("ep02", "s027", "c002")
        for identity in [ShotIdentity("ep02", "s027", "c001"), self.shot]:
            root = self.service.shot_root(identity)
            root.mkdir(parents=True)
            (root / "shot.json").write_text(json.dumps({
                "episode": identity.episode, "sequence": identity.sequence, "shot": identity.shot,
            }), encoding="utf-8")

    def test_workspace_scene_resolves_to_registered_production_shot(self):
        scene = self.root / "workspace/cg/shots/ep02/s027/c002/work/anim/maya/preComp/main/ELCD_ep02_s027_c002_preComp_v001_t04.ma"
        self.assertEqual(self.service.shot_identity_from_path(scene), self.shot)

    def test_all_configured_departments_are_resolved(self):
        scene = self.root / "workspace/2d/shots/ep02/s027/c002/work/comp/maya/scene.ma"
        self.assertEqual(self.service.shot_identity_from_path(scene), self.shot)

    def test_production_path_still_resolves(self):
        scene = self.service.shot_root(self.shot) / "work/maya/scene.ma"
        self.assertEqual(self.service.shot_identity_from_path(scene), self.shot)

    def test_unknown_shot_and_other_project_do_not_match(self):
        for scene in [
            self.root / "workspace/cg/shots/ep02/s027/c999/work/scene.ma",
            self.root / "other/workspace/cg/shots/ep02/s027/c002/work/scene.ma",
            self.root / "workspace/cg/shots/ep02/s027/c002_extra/work/scene.ma",
            self.root / "workspace/cg/sequences/ep02/s027/work/scene.ma",
            "",
        ]:
            with self.subTest(scene=scene):
                self.assertIsNone(self.service.shot_identity_from_path(scene))

    def test_configured_workspace_template_is_used(self):
        self.service.paths = ProjectPaths(self.root, templates={
            "shot_workspace_root": "{project_root}/artist_work/{department}/{episode}/{sequence}/{shot}",
        })
        scene = self.root / "artist_work/anim/ep02/s027/c002/scene.ma"
        self.assertEqual(self.service.shot_identity_from_path(scene), self.shot)


if __name__ == "__main__":
    unittest.main()
