from pathlib import Path
from types import SimpleNamespace

from smartlib.dcc.maya.review_playblast import (
    ALL_DISPLAY_LAYERS,
    display_layer_members,
    display_layers,
    export_display_layer_sequences,
    is_display_layer_excluded,
    load_display_layer_row_settings,
    load_scene_playblast_settings,
    publish_sequence_metadata,
    save_scene_playblast_settings,
    save_display_layer_row_settings,
    set_display_layer_excluded,
)


class FakeCmds:
    def __init__(self, output):
        self.output = Path(output)
        self.visibility = {"CHA": True, "BGA": False}
        self.playblast_calls = []
        self.panel_camera = "cam"
        self.nodes = {"CHA": {}, "BGA": {}}
        self.file_info = {}

    def ls(self, type=None):
        return ["defaultLayer", "CHA", "BGA"] if type == "displayLayer" else []

    def editDisplayLayerMembers(self, layer, query=False, fullNames=False):
        return {"CHA": ["|hero"], "BGA": ["|set"]}[layer]

    def objExists(self, name):
        if name.endswith(".visibility"):
            return name.split(".", 1)[0] in self.visibility
        if "." in name:
            node, attr = name.split(".", 1)
            return attr in self.nodes.get(node, {})
        if name in self.nodes:
            return True
        return name == "cam"

    def getAttr(self, name):
        if name == "smartPlayblastInfo.settingsJson":
            return self.nodes["smartPlayblastInfo"]["settingsJson"]
        if "." in name:
            node, attr = name.split(".", 1)
            if attr in self.nodes.get(node, {}):
                return self.nodes[node][attr]
        return self.visibility[name.split(".", 1)[0]]

    def setAttr(self, name, value, **kwargs):
        if name == "smartPlayblastInfo.settingsJson":
            self.nodes["smartPlayblastInfo"]["settingsJson"] = value
            return
        if "." in name:
            node, attr = name.split(".", 1)
            if attr in self.nodes.get(node, {}):
                self.nodes[node][attr] = value
                return
        self.visibility[name.split(".", 1)[0]] = bool(value)

    def createNode(self, node_type, name):
        self.nodes[name] = {}
        return name

    def addAttr(self, node, longName, dataType=None, attributeType=None):
        self.nodes[node][longName] = ""

    def fileInfo(self, key, value=None, query=False, remove=False):
        if query:
            return [self.file_info[key]] if key in self.file_info else []
        if remove:
            self.file_info.pop(key, None)
            return
        self.file_info[key] = value

    def getPanel(self, type=None):
        return ["modelPanel1"] if type == "modelPanel" else []

    def modelPanel(self, panel, query=False, edit=False, camera=None):
        if query:
            return self.panel_camera
        if edit:
            self.panel_camera = camera

    def playblast(self, **kwargs):
        self.playblast_calls.append(
            {**kwargs, "visibility": dict(self.visibility), "camera": self.panel_camera}
        )
        prefix = Path(kwargs["filename"])
        prefix.parent.mkdir(parents=True, exist_ok=True)
        extension = "." + str(kwargs.get("compression") or "png")
        for frame in range(kwargs["startTime"], kwargs["endTime"] + 1):
            (prefix.parent / f"{prefix.name}.{frame:04d}{extension}").write_text("frame", encoding="utf-8")


def test_display_layer_helpers_exclude_default_layer(tmp_path):
    cmds = FakeCmds(tmp_path)

    assert display_layers(cmds) == ["CHA", "BGA"]
    assert display_layer_members("CHA", cmds) == ["|hero"]


def test_export_uses_existing_display_layers_and_restores_visibility(tmp_path):
    plan = SimpleNamespace(
        version_dir=tmp_path,
        review_json=tmp_path / "metadata" / "review.json",
        review_data={
            "frame_range": [1001, 1002],
            "layers": {
                "CHA": {
                    "members": ["|hero"],
                    "camera": "cam",
                    "resolution": [1280, 720],
                    "frame_range": [1010, 1011],
                    "outputs": {"beauty": "image_sequence/CHA/shot_CHA_####.png"},
                }
            },
        },
    )
    plan.review_json.parent.mkdir(parents=True)
    cmds = FakeCmds(tmp_path)

    result = export_display_layer_sequences(plan, {"CHA": "CHA"}, cmds=cmds)

    assert result["CHA"]["file_count"] == 2
    assert cmds.playblast_calls[0]["visibility"] == {"CHA": True, "BGA": False}
    assert cmds.playblast_calls[0]["widthHeight"] == [1280, 720]
    assert cmds.playblast_calls[0]["startTime"] == 1010
    assert cmds.playblast_calls[0]["compression"] == "png"
    assert cmds.visibility == {"CHA": True, "BGA": False}


def test_export_all_makes_all_display_layers_visible(tmp_path):
    plan = SimpleNamespace(
        version_dir=tmp_path,
        review_json=tmp_path / "metadata" / "review.json",
        review_data={
            "frame_range": [1, 1],
            "layers": {
                "ALL": {
                    "members": ["|hero", "|set"],
                    "camera": "cam",
                    "resolution": [640, 360],
                    "outputs": {"beauty": "image_sequence/ALL/shot_ALL_####.png"},
                }
            },
        },
    )
    cmds = FakeCmds(tmp_path)

    export_display_layer_sequences(
        plan,
        {"ALL": ALL_DISPLAY_LAYERS},
        cmds=cmds,
        write_metadata=False,
    )

    assert cmds.playblast_calls[0]["visibility"] == {"CHA": True, "BGA": True}
    assert cmds.visibility == {"CHA": True, "BGA": False}


def test_scene_settings_round_trip_through_network_node(tmp_path):
    cmds = FakeCmds(tmp_path)
    settings = {
        "department": "anim",
        "rows": [{"layer": "CHA", "camera": "|cam", "start": 1001, "end": 1010}],
    }

    node = save_scene_playblast_settings(settings, cmds)

    assert node == "smartPlayblastInfo"
    assert load_scene_playblast_settings(cmds) == settings
    assert "smartPlayblastSettings" in cmds.file_info


def test_scene_settings_fall_back_to_maya_file_info(tmp_path):
    cmds = FakeCmds(tmp_path)
    settings = {"department": "layout", "rows": []}
    save_scene_playblast_settings(settings, cmds)
    cmds.nodes["smartPlayblastInfo"]["settingsJson"] = "{broken"

    assert load_scene_playblast_settings(cmds) == settings


def test_removed_display_layer_keeps_exclusion_on_the_layer(tmp_path):
    cmds = FakeCmds(tmp_path)

    set_display_layer_excluded("CHA", True, cmds)

    assert is_display_layer_excluded("CHA", cmds) is True
    assert is_display_layer_excluded("BGA", cmds) is False


def test_display_layer_row_settings_preserve_render_size(tmp_path):
    cmds = FakeCmds(tmp_path)
    row = {
        "layer": "BGA",
        "width": 1920,
        "height": 1080,
        "version": 2,
        "take": 3,
    }

    save_display_layer_row_settings("BGA", row, cmds)

    assert load_display_layer_row_settings("BGA", cmds) == row


def test_publish_sequence_metadata_creates_discoverable_review(tmp_path):
    version_dir = tmp_path / "review" / "anim" / "v003" / "t002"
    plan = SimpleNamespace(
        version_dir=version_dir,
        review_json=version_dir / "metadata" / "review.json",
        publish_json=version_dir / "metadata" / "publish.json",
        subset="anim",
        version=3,
        files={"review_json": "metadata/review.json"},
        review_data={
            "version": "v003",
            "take": "t002",
            "layers": {"CHA": {"outputs": {"beauty": "image_sequence/CHA/test_####.jpg"}}},
            "publish": {"source_workfile": "shot.ma"},
        },
    )

    review_json = publish_sequence_metadata(
        plan,
        {"CHA": {"pattern": "image_sequence/CHA/test_####.jpg", "file_count": 10}},
    )

    assert review_json.exists()
    assert (version_dir / "metadata" / "publish.json").exists()
    latest = (tmp_path / "review" / "anim" / "latest.json").read_text(encoding="utf-8")
    assert "v003/t002/metadata/review.json" in latest
