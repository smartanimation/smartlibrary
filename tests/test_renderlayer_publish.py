import json
from types import SimpleNamespace

from smartlib.apps.shot_manager.service import ShotManagerService


def test_publish_renderlayer_settings_writes_versioned_playblast_json(tmp_path):
    service = object.__new__(ShotManagerService)
    service.project_config = SimpleNamespace(project_name="ELCD")
    service.paths = SimpleNamespace(project_root=tmp_path)
    service.shot_root = lambda _identity: tmp_path / "shots" / "ep02" / "s027" / "c001"
    identity = SimpleNamespace(episode="ep02", sequence="s027", shot="c001")
    settings = {
        "department": "anim",
        "rows": [
            {
                "layer": "BGA",
                "enabled": True,
                "camera": "shotCam",
                "start": 278,
                "end": 411,
                "width": 1280,
                "height": 720,
                "preset": "layout_material",
            },
            {
                "layer": "CHA",
                "enabled": True,
                "camera": "shotCam",
                "start": 278,
                "end": 411,
                "width": 1920,
                "height": 1080,
                "preset": "layout_geo",
            },
        ],
    }

    path = service.publish_renderlayer_settings(
        identity,
        settings,
        source_scene=tmp_path / "shots" / "ep02" / "s027" / "c001" / "work" / "anim.ma",
    )

    assert path == tmp_path / "shots" / "ep02" / "s027" / "c001" / "publish" / "renderlayer" / "v001" / "playblast.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["layer_order"] == ["BGA", "CHA"]
    assert data["layers"][1]["resolution"] == [1920, 1080]
    assert (path.parents[1] / "latest.json").exists()
