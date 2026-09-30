from types import SimpleNamespace

from smartlib.core.metadata import write_json
from smartlib.core.path_resolver import ProjectPaths, AssetIdentity
from smartlib.apps.smart_reference_editor.service import Reference
from smartlib.dcc.maya import placement, reference_editor


def test_nested_props_keep_full_namespace_and_variant(tmp_path, monkeypatch):
    paths = ProjectPaths(tmp_path)
    root = paths.asset_root(AssetIdentity("prop", "main", "Chair"))
    write_json(root / "asset.json", {"asset": "Chair", "category": "prop", "group": "main"})
    source = str(root / "red" / "publish" / "Chair.mb")
    refs = [Reference("a", "Room:Chair", source, nested=True),
            Reference("b", "Room2:Chair", source, nested=True),
            Reference("c", "Room:Hidden", source, loaded=False, nested=True)]
    monkeypatch.setattr(reference_editor, "scan", lambda cmds: refs)
    monkeypatch.setattr(placement, "_maya_cmds", lambda: object())
    monkeypatch.setattr(placement, "_project_root", lambda config: tmp_path)
    monkeypatch.setattr(placement, "configured_project_paths", lambda *args: paths)
    monkeypatch.setattr(placement, "_context_cast_data", lambda config: {"cast": {}})
    rows = placement.list_cast_members(SimpleNamespace())
    assert [row.name for row in rows] == ["Room:Chair", "Room2:Chair"]
    assert all(row.asset == "Chair" and row.variant == "red" for row in rows)
    assert placement._cast_member_by_name(None, "Room2:Chair").namespace == "Room2:Chair"
    assert placement._nested_reference_members(None, rows) == []


def test_missing_nested_target_does_not_use_unqualified_node():
    cmds = SimpleNamespace(ls=lambda pattern, **kwargs: ["Chair"] if pattern == "Chair" else [])
    assert placement._find_namespaced_node(cmds, "Room:Chair", "Chair") == ""


def test_nested_prop_resolves_root_below_background():
    target = "Room:Chair:Root"
    cmds = SimpleNamespace(ls=lambda pattern, **kwargs: [target] if pattern == target else [])
    member = placement.CastMember("Room:Chair", "Chair", category="prop", namespace="Room:Chair")
    assert placement._resolve_fallback_target(cmds, member) == target


def test_export_preserves_nested_member_and_attach_target(monkeypatch):
    member = "Room:Chair"
    target = member + ":Root"
    attrs = {placement.MEMBER_ATTR: member, placement.ATTACH_ROOT_ATTR: target}
    monkeypatch.setattr(placement, "_maya_cmds", lambda: SimpleNamespace(xform=lambda *args, **kwargs: [0, 0, 0]))
    monkeypatch.setattr(placement, "list_placement_locators", lambda: [placement.PlacementLocator("chair_place_loc", "chair_place_loc")])
    monkeypatch.setattr(placement, "_get_string_attr", lambda cmds, node, attr: attrs.get(attr, ""))
    _, metadata = placement._collect_placement_metadata()
    assert metadata["placements"][0]["member"] == member
    assert metadata["placements"][0]["attach_root"] == target
