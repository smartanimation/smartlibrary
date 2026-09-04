import json

import pytest

from smartlib.dcc.maya import shot_builder as builder
from smartlib.review.workflow import ReviewWorkflowService


def test_old_construct_without_snapshot_placements_is_not_reused():
    inputs = dict(construct_snapshot={}, assembly_definition={}, layer_definition={})
    assert ReviewWorkflowService.canonical_construct_fingerprint(**inputs) != (
        ReviewWorkflowService.canonical_construct_fingerprint(**inputs, builder_version="review_builder_v2")
    )


def component(path, name="main", enabled=True):
    return {"component_type": "placement", "name": name, "path": str(path),
            "enabled": enabled, "required": True, "source": {"kind": "scene_data"}}


def test_snapshot_paths_win_over_empty_legacy_input_and_disabled_placeholder(tmp_path, monkeypatch):
    paths = [tmp_path / "main.json", tmp_path / "chair.json"]
    for path in paths:
        path.write_text("{}")
    calls = []
    monkeypatch.setattr(builder, "_apply_anim_placements", lambda cmds, root, data: calls.append(data["placements"]) or ["locator"])
    monkeypatch.setattr(builder, "_offset_animation_keys", lambda *args: pytest.fail("No legacy frame offset for Scene Data"))
    snapshot = {"components": [
        {"component_type": "placement", "name": "placements", "enabled": False,
         "source": {"kind": "anim_input", "field": "placements"}},
        component(paths[0]), component(paths[1], "chair"),
        component(tmp_path / "off.json", "off", False),
    ]}
    assert builder._apply_construct_placements(None, tmp_path, snapshot, anim_input={"placements": ""}, frame_offset=1000) == ["locator"]
    assert calls == [str(path) for path in paths]


def test_all_disabled_data_does_not_fall_back_to_legacy(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "_apply_anim_placements", lambda *args: pytest.fail("Disabled data must not apply"))
    assert builder._apply_construct_placements(None, tmp_path, {"components": [component("absent", enabled=False)]}, anim_input={"placements": "old.json"}) == []


def test_missing_required_snapshot_placement_fails(tmp_path):
    with pytest.raises(RuntimeError, match="Snapshot Placement was not found"):
        builder._apply_construct_placements(None, tmp_path, {"components": [component(tmp_path / "missing.json")]})


def test_legacy_input_still_applies_offset(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(builder, "_apply_anim_placements", lambda *args: ["old_locator"])
    monkeypatch.setattr(builder, "_offset_animation_keys", lambda cmds, nodes, offset: calls.append((nodes, offset)))
    builder._apply_construct_placements(None, tmp_path, {}, anim_input={"placements": "old.json"}, frame_offset=20)
    assert calls == [(["old_locator"], 20)]


class FakeMaya:
    def __init__(self):
        self.nodes = {"DeleinChair:A_C_DeleinChair"}
        self.constraints = []
        self.transforms = []

    def objExists(self, node):
        return node in self.nodes

    def group(self, **kwargs):
        self.nodes.add(kwargs["name"])
        return kwargs["name"]

    def spaceLocator(self, name):
        self.nodes.add(name)
        return [name]

    def parent(self, *args, **kwargs):
        pass

    def setAttr(self, *args):
        pass

    def xform(self, node, **kwargs):
        self.transforms.append((node, kwargs))

    def ls(self, pattern, **kwargs):
        return [pattern] if pattern in self.nodes else []

    def parentConstraint(self, source, target, **kwargs):
        self.constraints.append((source, target, kwargs))
        return [kwargs["name"]]

    def delete(self, nodes):
        pass


def test_snapshot_applies_transform_and_member_from_same_version(tmp_path):
    path = tmp_path / "placements.json"
    path.write_text(json.dumps({"placements": [{"locator": "chair_place_loc",
        "translate": [-56.9, 0, -48.9], "rotate": [136, 0, 90]}]}))
    (tmp_path / "placement_members.json").write_text(json.dumps({"placements": [{
        "locator": "chair_place_loc", "member": "DeleinChair_main",
        "attach_root": "DeleinChair:A_C_DeleinChair"}]}))
    cmds = FakeMaya()
    nodes = builder._apply_construct_placements(cmds, tmp_path, {"components": [component(path)]})
    assert nodes == ["chair_place_loc"]
    assert any(kwargs.get("translation") == [-56.9, 0, -48.9] for node, kwargs in cmds.transforms)
    assert cmds.constraints[0][:2] == ("chair_place_loc", "DeleinChair:A_C_DeleinChair")
