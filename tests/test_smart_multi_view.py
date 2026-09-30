from types import SimpleNamespace

from smartlib.dcc.maya import smart_menu, smart_multi_view as multi


def test_camera_discovery_preserves_paths_and_namespaces():
    cameras = ["|persp", "|top", "|front", "|side", "|persp1",
               "|ref:persp", "|a|cam", "|b|cam", "|renamedDefault", "|intermediate"]
    cmds = SimpleNamespace(
        ls=lambda **kw: [name + "|shape" for name in cameras],
        listRelatives=lambda shape, **kw: [shape.rsplit("|", 1)[0]],
        getAttr=lambda plug: "intermediate|" in plug,
        camera=lambda shape, **kw: "renamedDefault|" in shape,
    )
    assert multi.scene_cameras(cmds) == ["|a|cam", "|b|cam", "|persp1", "|ref:persp"]


def test_display_controls_only_touch_owned_live_panels():
    edits = []
    cmds = SimpleNamespace(
        modelPanel=lambda panel, **kw: panel != "deleted",
        modelEditor=lambda panel, **kw: edits.append((panel, kw)),
    )
    view = multi.MultiView(cmds)
    view.panels = ["owned", "deleted"]
    view.set_display("displayLights", True)
    view.set_display("wireframeOnShaded", True)
    view.set_display("displayLights", False)
    assert edits == [("owned", {"edit": True, "displayLights": "all"}),
                     ("owned", {"edit": True, "wireframeOnShaded": True}),
                     ("owned", {"edit": True, "displayLights": "default"})]
    assert view.settings["wireframeOnShaded"] is True


def test_geometry_only_restores_per_panel_filters_without_changing_shading():
    states = {
        name: {**dict.fromkeys(multi.GEOMETRY_ONLY_FLAGS, True),
               "nurbsCurves": name == "first", "polymeshes": False,
               "displayTextures": True, "pluginShapes": True}
        for name in ("first", "second", "outside")
    }
    original = {name: dict(state) for name, state in states.items()}

    def editor(panel, query=False, edit=False, **flags):
        if query:
            return states[panel][next(iter(flags))]
        states[panel].update(flags)

    view = multi.MultiView(SimpleNamespace(
        modelPanel=lambda name, **kw: name in states, modelEditor=editor))
    view.panels = ["first", "second", "deleted"]
    view.set_geometry_only(True)
    view.set_geometry_only(True)  # A repeated ON must not replace saved settings.
    for name in ("first", "second"):
        assert states[name]["polymeshes"]
        assert not states[name]["nurbsCurves"]
        assert not states[name]["locators"]
        assert states[name]["displayTextures"] and states[name]["pluginShapes"]
    assert states["outside"] == original["outside"]
    # Newly built panels also inherit the active mode and save their defaults.
    states["new"] = dict(original["first"])
    view.panels.append("new")
    view.apply_geometry_only("new")
    assert not states["new"]["locators"]
    view.set_geometry_only(False)
    assert states["new"] == original["first"]
    assert all(states[name] == state for name, state in original.items())
    assert not view.object_display_states


def test_dispose_deletes_only_owned_panels_and_is_repeatable():
    deleted = []
    view = multi.MultiView(SimpleNamespace(
        modelPanel=lambda panel, **kw: panel != "alreadyGone",
        deleteUI=lambda name, **kw: deleted.append((name, kw)),
    ))
    view.panels = ["owned", "alreadyGone"]
    view.dispose()
    view.dispose()
    assert deleted == [("owned", {"panel": True})]
    assert view.panels == []


def test_guide_off_does_not_create_and_on_restores_hidden_parent(monkeypatch):
    values = {"|guide|shape.visibility": True, "|guide.visibility": False}
    monkeypatch.setattr(multi, "guide_nodes", lambda cmds: ["|guide|shape"])
    cmds = SimpleNamespace(getAttr=values.__getitem__,
                           setAttr=values.__setitem__,
                           listRelatives=lambda *a, **kw: ["|guide"])
    multi.set_guides(cmds, False)
    assert not values["|guide|shape.visibility"]
    multi.set_guides(cmds, True)
    assert all(values.values())
    monkeypatch.setattr(multi, "guide_nodes", lambda cmds: [])
    multi.set_guides(cmds, False)


def test_guide_creation_preserves_selection(monkeypatch):
    shapes, selection = [], []
    monkeypatch.setattr(multi, "guide_nodes", lambda cmds: shapes)
    monkeypatch.setattr(smart_menu, "ensure_smart_gate_guide_plugin", lambda cmds: True)
    cmds = SimpleNamespace(
        ls=lambda **kw: ["|selected"],
        SmartGateGuide=lambda: shapes.append("|guide|shape"),
        select=lambda items, **kw: selection.extend(items),
        listRelatives=lambda *a, **kw: ["|guide"],
        getAttr=lambda plug: True,
    )
    multi.set_guides(cmds, True)
    assert selection == ["|selected"]


def test_legacy_menu_entry_is_idempotent_and_keeps_disabled_override():
    command = "smartlib.dcc.maya.smart_menu.show_smart_multi_view"
    data = {"maya_menu": {"categories": {"Camera": {
        "Custom label": {"command": command, "enabled": False}}}}}
    smart_menu._ensure_multi_view_entry(data)
    smart_menu._ensure_multi_view_entry(data)
    items = data["maya_menu"]["categories"]["Camera"]
    assert len(items) == 1
    assert items[0]["enabled"] is False
    fresh = {"maya_menu": {"categories": {}}}
    smart_menu._ensure_multi_view_entry(fresh)
    assert fresh["maya_menu"]["categories"]["Camera"][0]["command"] == command
