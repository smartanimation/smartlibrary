from pathlib import Path
from types import SimpleNamespace

from smartlib.dcc.maya.review_playblast import (
    display_layer_members,
    display_layers,
    export_display_layer_sequences,
)


class FakeCmds:
    def __init__(self, output):
        self.output = Path(output)
        self.visibility = {"CHA": True, "BGA": False}
        self.playblast_calls = []
        self.panel_camera = "cam"

    def ls(self, type=None):
        return ["defaultLayer", "CHA", "BGA"] if type == "displayLayer" else []

    def editDisplayLayerMembers(self, layer, query=False, fullNames=False):
        return {"CHA": ["|hero"], "BGA": ["|set"]}[layer]

    def objExists(self, name):
        if name.endswith(".visibility"):
            return name.split(".", 1)[0] in self.visibility
        return name == "cam"

    def getAttr(self, name):
        return self.visibility[name.split(".", 1)[0]]

    def setAttr(self, name, value):
        self.visibility[name.split(".", 1)[0]] = bool(value)

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
        for frame in range(kwargs["startTime"], kwargs["endTime"] + 1):
            (prefix.parent / f"{prefix.name}.{frame:04d}.jpg").write_text("frame", encoding="utf-8")


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
                    "outputs": {"beauty": "image_sequence/CHA/shot_CHA_####.jpg"},
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
    assert cmds.visibility == {"CHA": True, "BGA": False}
