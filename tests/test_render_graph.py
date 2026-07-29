from pathlib import Path

import pytest

import smartlib.dcc.maya.render_graph as render_graph
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json
from smartlib.dcc.maya.render_graph import (
    RenderGraph,
    _ApplyState,
    _apply_camera_overrides,
    _apply_geom_attrs,
    _apply_transform_override,
    _capture_object_state,
    _capture_viewport_state,
    _isolate_final_objects,
    _playblast,
    _read_scene_state_data,
    _restore_object_state,
    _restore_viewport_state,
    _write_scene_state_data,
    finish_transform_recording,
    default_attrs,
    build_ae_slots,
    export_ae_slots_build_data,
    normalized_attrs,
    publish_ae_slots,
    record_transform_override,
    start_transform_recording,
)
from smartlib.review.playblast_package import latest_take_for_package, resolve_playblast_package
from smartlib.review import playblast_package


def test_output_ports_accept_only_matching_node_types():
    graph = RenderGraph()
    output = graph.add_node("output")
    objects = graph.add_node("object")
    camera = graph.add_node("camera")
    settings = graph.add_node("render_settings")

    assert graph.can_connect(objects.id, "out", output.id, "objects")
    assert graph.can_connect(camera.id, "out", output.id, "camera")
    assert graph.can_connect(settings.id, "out", output.id, "render_settings")
    assert not graph.can_connect(objects.id, "out", output.id, "camera")
    assert not graph.can_connect(camera.id, "out", output.id, "objects")
    assert not graph.can_connect(settings.id, "out", output.id, "objects")
    assert not graph.can_connect(output.id, "out", output.id, "objects")
    assert graph.can_connect(objects.id, "out", objects.id, "in") is False


def test_output_nodes_connect_to_ae_slots():
    graph = RenderGraph()
    output = graph.add_node("output")
    output.name = "CHA"
    ae_slots = graph.add_node("ae_slots")

    assert graph.can_connect(output.id, "out", ae_slots.id, "slot01")
    assert graph.can_connect(output.id, "out", ae_slots.id, "slot03")
    assert not graph.can_connect(output.id, "out", ae_slots.id, "slot04")

    ae_slots.attrs["slot_count"] = 5

    assert graph.can_connect(output.id, "out", ae_slots.id, "slot05")


def test_ae_slots_order_follows_slot_ports():
    graph = RenderGraph()
    first = graph.add_node("output", name="Beauty")
    second = graph.add_node("output", name="Mask")
    ae_slots = graph.add_node("ae_slots")

    graph.connect(second.id, "out", ae_slots.id, "slot02")
    graph.connect(first.id, "out", ae_slots.id, "slot01")
    data = graph.to_data()
    ae_data = next(node for node in data["nodes"] if node["type"] == "ae_slots")

    assert graph.ae_slot_order(ae_slots.id) == [first.id, second.id]
    assert ae_data["attrs"]["order"] == [first.id, second.id]


def test_ae_slot_inputs_are_single_connection():
    graph = RenderGraph()
    first = graph.add_node("output")
    second = graph.add_node("output")
    ae_slots = graph.add_node("ae_slots")

    graph.connect(first.id, "out", ae_slots.id, "slot01")
    graph.connect(second.id, "out", ae_slots.id, "slot01")

    assert graph.ae_slot_order(ae_slots.id) == [second.id]
    assert len(graph.input_edges(ae_slots.id, "slot01")) == 1


def test_ae_slot_output_moves_between_slots():
    graph = RenderGraph()
    output = graph.add_node("output")
    ae_slots = graph.add_node("ae_slots")

    graph.connect(output.id, "out", ae_slots.id, "slot01")
    graph.connect(output.id, "out", ae_slots.id, "slot02")

    assert graph.input_edges(ae_slots.id, "slot01") == []
    edges = graph.input_edges(ae_slots.id, "slot02")
    assert len(edges) == 1
    assert edges[0].source == output.id
    assert graph.ae_slot_order(ae_slots.id) == [output.id]
    assert graph.node(ae_slots.id).attrs["slots"] == {"slot02": output.id}


def test_ae_slot_restore_prefers_slots_map_over_order():
    graph = RenderGraph()
    output = graph.add_node("output")
    ae_slots = graph.add_node("ae_slots")
    graph.connect(output.id, "out", ae_slots.id, "slot02")
    data = graph.to_data()
    data["edges"] = []

    restored = RenderGraph.from_data(data)

    assert restored.input_edges(ae_slots.id, "slot01") == []
    edges = restored.input_edges(ae_slots.id, "slot02")
    assert len(edges) == 1
    assert edges[0].source == output.id


def test_ae_slot_edges_restore_from_saved_order_when_edge_is_missing():
    graph = RenderGraph()
    output = graph.add_node("output")
    ae_slots = graph.add_node("ae_slots")
    graph.connect(output.id, "out", ae_slots.id, "slot01")
    data = graph.to_data()
    data["edges"] = []

    restored = RenderGraph.from_data(data)

    edges = restored.input_edges(ae_slots.id, "slot01")
    assert len(edges) == 1
    assert edges[0].source == output.id
    assert restored.ae_slot_order(ae_slots.id) == [output.id]
    assert restored.node(ae_slots.id).attrs["slots"]["slot01"] == output.id


def test_ae_slot_edges_restore_from_saved_slots_map_when_order_is_missing():
    graph = RenderGraph()
    output = graph.add_node("output")
    ae_slots = graph.add_node("ae_slots")
    graph.connect(output.id, "out", ae_slots.id, "slot01")
    data = graph.to_data()
    data["edges"] = []
    ae_data = next(node for node in data["nodes"] if node["type"] == "ae_slots")
    ae_data["attrs"]["order"] = []

    restored = RenderGraph.from_data(data)

    edges = restored.input_edges(ae_slots.id, "slot01")
    assert len(edges) == 1
    assert edges[0].source == output.id


def test_output_node_owns_output_path():
    graph = RenderGraph()
    output = graph.add_node("output")
    settings = graph.add_node("render_settings")

    assert "output_path" in output.attrs
    assert "output_path" not in settings.attrs


def test_output_path_is_evaluated_from_output_node():
    graph = RenderGraph()
    output = graph.add_node("output")
    settings = graph.add_node("render_settings")
    output.attrs["output_path"] = "D:/show/images/shot010_beauty"
    graph.connect(settings.id, "out", output.id, "render_settings")

    evaluated = _ApplyState(cmds=_FakeCmds(), graph=graph).evaluate_render_settings(output.id)

    assert evaluated["output_path"] == "D:/show/images/shot010_beauty"


def test_output_take_is_normalized_to_t_prefixed_three_digit_number():
    attrs = normalized_attrs("output", {"take": "take003"})

    assert attrs["take"] == "t003"


def test_render_settings_frame_modes_resolve_scene_ranges():
    graph = RenderGraph()
    output = graph.add_node("output")
    settings = graph.add_node("render_settings")
    graph.connect(settings.id, "out", output.id, "render_settings")
    state = _ApplyState(cmds=_FrameRangeCmds(), graph=graph)

    settings.attrs["frame_mode"] = "Single"
    settings.attrs["start_frame"] = 1050
    evaluated = state.evaluate_render_settings(output.id)
    assert (evaluated["start_frame"], evaluated["end_frame"]) == (1050, 1050)

    settings.attrs["frame_mode"] = "TimeRange"
    evaluated = state.evaluate_render_settings(output.id)
    assert (evaluated["start_frame"], evaluated["end_frame"]) == (1001, 1100)

    settings.attrs["frame_mode"] = "RenderGlobal"
    evaluated = state.evaluate_render_settings(output.id)
    assert (evaluated["start_frame"], evaluated["end_frame"]) == (1010, 1090)

    settings.attrs["frame_mode"] = "Custom"
    settings.attrs["start_frame"] = 12
    settings.attrs["end_frame"] = 24
    evaluated = state.evaluate_render_settings(output.id)
    assert (evaluated["start_frame"], evaluated["end_frame"]) == (12, 24)


def test_render_settings_editorial_frame_mode_uses_shot_json(tmp_path):
    project_config = _project_config(tmp_path)
    shot_root = tmp_path / "project" / "shots" / "ep001" / "sq010" / "shot010"
    scene = shot_root / "work" / "layout" / "maya" / "scene.ma"
    scene.parent.mkdir(parents=True)
    scene.write_text("scene", encoding="utf-8")
    (shot_root / "shot.json").write_text('{"editorial": {"cut_in": 1008, "cut_out": 1072}}', encoding="utf-8")
    graph = RenderGraph()
    output = graph.add_node("output")
    settings = graph.add_node("render_settings")
    settings.attrs["frame_mode"] = "Editorial"
    graph.connect(settings.id, "out", output.id, "render_settings")

    evaluated = _ApplyState(cmds=_FrameRangeCmds(scene), graph=graph, project_config=project_config).evaluate_render_settings(output.id)

    assert (evaluated["start_frame"], evaluated["end_frame"]) == (1008, 1072)


def test_render_settings_defaults_use_editorial_order():
    attrs = default_attrs("render_settings")

    assert attrs["frame_mode"] == "Editorial Frame Range"
    assert list(attrs) == ["width", "height", "frame_mode", "start_frame", "end_frame", "format", "compression", "percent"]


def test_output_path_resolves_to_output_review_package(tmp_path):
    project_config = _project_config(tmp_path)
    scene = tmp_path / "project" / "shots" / "ep001" / "sq010" / "shot010" / "work" / "layout" / "maya" / "scene.ma"
    scene.parent.mkdir(parents=True)
    scene.write_text("scene", encoding="utf-8")
    graph = RenderGraph()
    output = graph.add_node("output")
    settings = graph.add_node("render_settings")
    output.attrs["dept"] = "layout"
    output.name = "CHA"
    output.attrs["version"] = "v002"
    output.attrs["take"] = "03"
    graph.connect(settings.id, "out", output.id, "render_settings")

    evaluated = _ApplyState(cmds=_ScenePathCmds(scene), graph=graph, project_config=project_config).evaluate_render_settings(output.id)

    assert evaluated["compression"] == "png"
    assert evaluated["package_root"].endswith("shots/ep001/sq010/shot010/output/review/layout/CHA/v002/t003")
    assert evaluated["output_path"].endswith("images/shot010_layout_CHA_v002_t003")


def test_playblast_package_paths_are_yaml_driven(tmp_path):
    project_config = _project_config(tmp_path)
    paths = resolve_playblast_package(
        project_config,
        area="output",
        shot_root=tmp_path / "project" / "shots" / "ep001" / "sq010" / "shot010",
        shot="shot010",
        dept="layout",
        version="2",
        take="3",
        layer="CHA",
    )

    assert paths.root.as_posix().endswith("output/review/layout/CHA/v002/t003")
    assert paths.image_sequence.as_posix().endswith("images/shot010_layout_CHA_v002_t003_####.png")
    assert paths.image_prefix.as_posix().endswith("images/shot010_layout_CHA_v002_t003")
    assert paths.mov.as_posix().endswith("mov/shot010_layout_v002_t003.mov")


def test_latest_take_is_layer_scoped(tmp_path):
    root = tmp_path / "project" / "shots" / "pv01" / "CG" / "c002" / "output" / "review" / "anim"
    (root / "CHA" / "v001" / "06").mkdir(parents=True)
    (root / "BGA" / "v001" / "01").mkdir(parents=True)

    assert latest_take_for_package(root / "CHA" / "v001" / "01", "01") == "t006"
    assert latest_take_for_package(root / "BGA" / "v001" / "01", "01") == "t001"


def test_publish_ae_slots_snapshots_output_to_publish(tmp_path, monkeypatch):
    project_config = _project_config(tmp_path)
    scene = tmp_path / "project" / "shots" / "ep001" / "sq010" / "shot010" / "work" / "layout" / "maya" / "scene.ma"
    scene.parent.mkdir(parents=True)
    scene.write_text("scene", encoding="utf-8")
    monkeypatch.setattr(render_graph, "_maya_cmds_or_none", lambda: _ScenePathCmds(scene))
    graph = RenderGraph()
    output = graph.add_node("output")
    output.name = "CHA"
    ae_slots = graph.add_node("ae_slots")
    graph.connect(output.id, "out", ae_slots.id, "slot01")
    settings = _ApplyState(cmds=_ScenePathCmds(scene), graph=graph, project_config=project_config).evaluate_render_settings(output.id)
    source_root = Path(settings["package_root"])
    (source_root / "metadata").mkdir(parents=True)
    (source_root / "metadata" / "playblast.json").write_text("{}", encoding="utf-8")

    published = publish_ae_slots(graph, ae_slots.id, project_config)

    assert len(published) == 1
    publish_root = Path(published[0])
    assert publish_root.as_posix().endswith("publish/review/layout/CHA/v001/t001")
    assert (publish_root / "metadata" / "playblast.json").exists()
    build_root = publish_root.parent.parent.parent / "review_build" / "v001" / "t001"
    assert build_root.as_posix().endswith("publish/review/layout/review_build/v001/t001")
    assert (build_root / "slots.json").exists()
    log = build_root / "ae" / "data" / "shot010_layout_build_v001_t001.log"
    assert log.exists()
    assert "Waiting for After Effects launch." in log.read_text(encoding="utf-8")
    slots = read_json(build_root / "slots.json", {})
    assert "CHA/v001/t001/images/" in slots["slots"][0]["image_sequence"]
    assert "CHA/v001/t001/slate/" in slots["slots"][0]["slate_sequence"]
    manifest = read_json(build_root / "shot010_layout_build_v001_t001.json", {})
    assert manifest["template_comp"] == "review_base.comp"
    assert manifest["stage"]["comp_name"] == "stage"
    assert manifest["layers"][0]["precomp"] == "CHA"
    assert "CHA/v001/t001/images/" in manifest["layers"][0]["image_sequence"]
    assert manifest["slate"]["layer"] == "Slate"
    assert "CHA/v001/t001/slate/" in manifest["slate"]["image_sequence"]
    script = build_root / "ae" / "scripts" / "shot010_layout_build_v001_t001.jsx"
    assert script.exists()
    script_text = script.read_text(encoding="utf-8")
    assert "shot010_layout_build_v001_t001.json" in script_text
    assert "addSlateToStage(stage, data.slate);" in script_text
    assert "options.sequence = shouldImportAsSequence(row);" in script_text
    assert 'options.sequence ? "sequence" : "still"' in script_text
    assert "Number(row.duration_frames || 0) <= 1" in script_text
    assert "data.auto_save === true" in script_text
    assert "app.project.save(projectFile);" in script_text
    assert manifest["auto_save"] is False


def test_export_ae_slots_build_data_uses_common_review_build_root(tmp_path, monkeypatch):
    project_config = _project_config(tmp_path)
    scene = tmp_path / "project" / "shots" / "ep001" / "sq010" / "shot010" / "work" / "layout" / "maya" / "scene.ma"
    scene.parent.mkdir(parents=True)
    scene.write_text("scene", encoding="utf-8")
    monkeypatch.setattr(render_graph, "_maya_cmds_or_none", lambda: _ScenePathCmds(scene))
    graph = RenderGraph()
    cha = graph.add_node("output")
    cha.name = "CHA"
    bga = graph.add_node("output")
    bga.name = "BGA"
    bga.attrs["take"] = "06"
    ae_slots = graph.add_node("ae_slots")
    graph.connect(cha.id, "out", ae_slots.id, "slot01")
    graph.connect(bga.id, "out", ae_slots.id, "slot02")

    manifests = export_ae_slots_build_data(graph, ae_slots.id, project_config, area="output")

    assert len(manifests) == 1
    manifest_path = Path(manifests[0])
    assert manifest_path.as_posix().endswith("output/review/layout/review_build/v001/t001/shot010_layout_build_v001_t001.json")
    manifest = read_json(manifest_path, {})
    assert [row["layer"] for row in manifest["layers"]] == ["CHA", "BGA"]
    assert "CHA/v001/t001/images/" in manifest["layers"][0]["image_sequence"]
    assert "BGA/v001/t006/images/" in manifest["layers"][1]["image_sequence"]
    assert ".." not in manifest["layers"][0]["first_frame_file"]
    assert manifest["layers"][0]["first_frame_file"].endswith("CHA/v001/t001/images/shot010_layout_CHA_v001_t001_0100.png")


def test_publish_ae_slots_uses_next_take_when_publish_exists(tmp_path, monkeypatch):
    project_config = _project_config(tmp_path)
    scene = tmp_path / "project" / "shots" / "ep001" / "sq010" / "shot010" / "work" / "layout" / "maya" / "scene.ma"
    scene.parent.mkdir(parents=True)
    scene.write_text("scene", encoding="utf-8")
    monkeypatch.setattr(render_graph, "_maya_cmds_or_none", lambda: _ScenePathCmds(scene))
    graph = RenderGraph()
    output = graph.add_node("output")
    output.name = "CHA"
    output.attrs["take"] = "06"
    ae_slots = graph.add_node("ae_slots")
    graph.connect(output.id, "out", ae_slots.id, "slot01")
    settings = _ApplyState(cmds=_ScenePathCmds(scene), graph=graph, project_config=project_config).evaluate_render_settings(output.id)
    source_root = Path(settings["package_root"])
    (source_root / "metadata").mkdir(parents=True)
    (source_root / "metadata" / "playblast.json").write_text("{}", encoding="utf-8")
    existing_publish_root = Path(settings["publish_package_root"])
    existing_log = existing_publish_root / "ae" / "data" / "build_review.log"
    existing_log.parent.mkdir(parents=True)
    existing_log.write_text("locked by another process", encoding="utf-8")

    published = publish_ae_slots(graph, ae_slots.id, project_config)

    assert len(published) == 1
    publish_root = Path(published[0])
    assert publish_root.as_posix().endswith("publish/review/layout/CHA/v001/t007")
    assert existing_log.read_text(encoding="utf-8") == "locked by another process"
    assert (publish_root / "metadata" / "playblast.json").exists()
    build_root = publish_root.parents[2] / "review_build" / "v001" / "t001"
    assert (build_root / "shot010_layout_build_v001_t001.json").exists()


def test_build_ae_slots_launches_configured_after_effects(tmp_path, monkeypatch):
    from smartlib.review import ae as review_ae

    project_config = _project_config(tmp_path)
    fake_afterfx = tmp_path / "AfterFX.exe"
    fake_afterfx.write_text("", encoding="utf-8")
    (project_config.config_dir / "software_AfterEffects2025.yml").write_text(f"path: {fake_afterfx.as_posix()}\n", encoding="utf-8")
    monkeypatch.setattr(review_ae, "_AE_SCRIPT_RUN_DELAY_SECONDS", 0.0)
    scene = tmp_path / "project" / "shots" / "ep001" / "sq010" / "shot010" / "work" / "layout" / "maya" / "scene.ma"
    scene.parent.mkdir(parents=True)
    scene.write_text("scene", encoding="utf-8")
    monkeypatch.setattr(render_graph, "_maya_cmds_or_none", lambda: _ScenePathCmds(scene))
    calls = []
    monkeypatch.setattr(review_ae.subprocess, "Popen", lambda command, cwd=None, env=None: calls.append((command, cwd, env)) or object())
    graph = RenderGraph()
    output = graph.add_node("output")
    ae_slots = graph.add_node("ae_slots")
    graph.connect(output.id, "out", ae_slots.id, "slot01")
    settings = _ApplyState(cmds=_ScenePathCmds(scene), graph=graph, project_config=project_config).evaluate_render_settings(output.id)
    source_root = Path(settings["package_root"])
    (source_root / "metadata").mkdir(parents=True)
    (source_root / "metadata" / "playblast.json").write_text("{}", encoding="utf-8")

    results = build_ae_slots(graph, ae_slots.id, project_config)

    assert results[0]["launched"] is True
    assert len(calls) == 2
    assert calls[0][0][0] == str(fake_afterfx)
    assert calls[1][0][0] == str(fake_afterfx)
    assert calls[1][0][1] == "-r"
    assert calls[1][0][2].endswith("ae\\scripts\\shot010_layout_build_v001_t001.jsx") or calls[1][0][2].endswith("ae/scripts/shot010_layout_build_v001_t001.jsx")
    assert calls[0][2]["SMART_PROJECT"] == project_config.project_name
    assert calls[0][2]["SMART_EPISODE"] == "ep001"
    assert calls[0][2]["SMART_SEQUENCE"] == "sq010"
    assert calls[0][2]["SMART_SHOT"] == "shot010"
    log = Path(results[0]["log"])
    assert Path(results[0]["manifest"]).as_posix().endswith("output/review/layout/review_build/v001/t001/shot010_layout_build_v001_t001.json")
    assert log.exists()
    log_text = log.read_text(encoding="utf-8")
    assert "Opening After Effects" in log_text
    assert "Running AE build script" in log_text


def test_legacy_render_settings_output_path_migrates_to_output():
    graph = RenderGraph()
    output = graph.add_node("output")
    settings = graph.add_node("render_settings")
    graph.connect(settings.id, "out", output.id, "render_settings")
    data = graph.to_data()
    output_data = next(node for node in data["nodes"] if node["type"] == "output")
    settings_data = next(node for node in data["nodes"] if node["type"] == "render_settings")
    output_data["attrs"]["output_path"] = ""
    settings_data["attrs"]["output_path"] = "D:/legacy/shot010_beauty"

    imported = RenderGraph.from_data(data)
    imported_output = next(node for node in imported.nodes if node.type == "output")
    imported_settings = next(node for node in imported.nodes if node.type == "render_settings")

    assert imported_output.attrs["output_path"] == "D:/legacy/shot010_beauty"
    assert "output_path" not in imported_settings.attrs


def test_geom_attr_node_defaults_and_legacy_visible_migration():
    graph = RenderGraph()
    node = graph.add_node("visibility")

    assert node.name.startswith("geomAttr")
    assert node.attrs["visibility"] is True
    assert node.attrs["template"] is False
    assert node.attrs["castsShadows"] is True
    assert node.attrs["receiveShadows"] is True
    assert node.attrs["motionBlur"] is True

    attrs = normalized_attrs("visibility", {"visible": False})

    assert attrs["visibility"] is False
    assert "visible" not in attrs


def test_apply_geom_attrs_sets_transform_and_shape_attrs():
    cmds = _GeomAttrCmds()

    _apply_geom_attrs(
        cmds,
        ["|hero"],
        {
            "visibility": False,
            "template": True,
            "castsShadows": False,
            "receiveShadows": False,
            "motionBlur": False,
        },
    )

    assert cmds.values["|hero.visibility"] is False
    assert cmds.values["|hero.template"] is True
    assert cmds.values["|hero|heroShape.visibility"] is False
    assert cmds.values["|hero|heroShape.template"] is True
    assert cmds.values["|hero|heroShape.castsShadows"] is False
    assert cmds.values["|hero|heroShape.receiveShadows"] is False
    assert cmds.values["|hero|heroShape.motionBlur"] is False


def test_object_nodes_accept_in_objects():
    graph = RenderGraph()
    first = graph.add_node("object")
    second = graph.add_node("object")

    assert graph.can_connect(first.id, "out", second.id, "in")


def test_reference_node_outputs_objects():
    graph = RenderGraph()
    cast = graph.add_node("cast")
    output = graph.add_node("output")

    assert graph.can_connect(cast.id, "out", output.id, "objects")


def test_import_drops_invalid_edges():
    graph = RenderGraph()
    output = graph.add_node("output")
    objects = graph.add_node("object")
    data = graph.to_data()
    data["edges"] = [
        {"source": objects.id, "source_port": "out", "target": output.id, "target_port": "camera"},
        {"source": objects.id, "source_port": "out", "target": output.id, "target_port": "objects"},
    ]

    imported = RenderGraph.from_data(data)

    assert len(imported.edges) == 1
    assert imported.edges[0].target_port == "objects"


def test_object_node_merges_in_objects_with_local_objects():
    graph = RenderGraph()
    first = graph.add_node("object")
    first.attrs["mode"] = "objects"
    first.attrs["objects"] = ["pSphere1"]
    second = graph.add_node("object")
    second.attrs["mode"] = "objects"
    second.attrs["objects"] = ["desk_GRP"]
    graph.connect(first.id, "out", second.id, "in")

    state = _ApplyState(cmds=_FakeCmds(), graph=graph)

    assert state.evaluate_node_objects(second.id) == ["pSphere1", "desk_GRP"]


def test_apply_graph_stops_on_ambiguous_short_object_name(monkeypatch):
    cmds = _DuplicateNameCmds()
    monkeypatch.setattr(render_graph, "_maya_cmds", lambda: cmds)
    graph = RenderGraph()
    output = graph.add_node("output")
    objects = graph.add_node("object")
    objects.attrs["mode"] = "objects"
    objects.attrs["objects"] = ["geo"]
    graph.connect(objects.id, "out", output.id, "objects")

    with pytest.raises(RuntimeError, match="Ambiguous object name"):
        render_graph.apply_graph(graph, output.id)

    assert cmds.values == {}


def test_apply_graph_warns_when_output_objects_share_short_name(monkeypatch):
    cmds = _DuplicateNameCmds()
    monkeypatch.setattr(render_graph, "_maya_cmds", lambda: cmds)
    graph = RenderGraph()
    output = graph.add_node("output")
    objects = graph.add_node("object")
    objects.attrs["mode"] = "objects"
    objects.attrs["objects"] = ["|A|geo", "|B|geo"]
    graph.connect(objects.id, "out", output.id, "objects")

    result = render_graph.apply_graph(graph, output.id)

    assert result["objects"] == ["|A|geo", "|B|geo"]
    assert any("Duplicate DAG short name" in warning for warning in result["warnings"])


def test_reference_node_uses_scene_reference_and_returns_geometry_only():
    graph = RenderGraph()
    cast = graph.add_node("cast")
    cast.attrs["asset"] = "Hero"
    cast.attrs["reference_node"] = "HeroRN"

    objects = _ApplyState(cmds=_CastCmds(), graph=graph).evaluate_node_objects(cast.id)

    assert objects == ["|Hero:geo_GRP"]
    assert cast.attrs["namespace"] == "Hero"


def test_legacy_object_attrs_are_normalized():
    attrs = normalized_attrs("object", {"objects": "pSphere1, desk_GRP", "use_selection": False})

    assert attrs["mode"] == "objects"
    assert attrs["objects"] == ["pSphere1", "desk_GRP"]


def test_camera_attrs_include_overrides():
    graph = RenderGraph()
    camera = graph.add_node("camera")

    assert camera.attrs["override_overscan"] is True
    assert camera.attrs["overscan"] == 1.0
    assert camera.attrs["override_depthOfField"] is False
    assert camera.attrs["depthOfField"] is False
    assert "focusDistance" in camera.attrs
    assert "fStop" in camera.attrs
    assert "nearClipPlane" in camera.attrs
    assert "farClipPlane" in camera.attrs


def test_apply_camera_overrides_sets_enabled_attrs_only():
    cmds = _CameraCmds()

    _apply_camera_overrides(
        cmds,
        "cam1",
        {
            "override_overscan": True,
            "overscan": 1.2,
            "override_depthOfField": False,
            "depthOfField": True,
            "override_focusDistance": True,
            "focusDistance": 12.5,
        },
    )

    assert cmds.values["camShape.overscan"] == 1.2
    assert cmds.values["camShape.focusDistance"] == 12.5
    assert "camShape.depthOfField" not in cmds.values


def test_playblast_uses_camera_panel_from_apply_state(tmp_path, monkeypatch):
    cmds = _PlayblastCameraCmds()
    monkeypatch.setattr(render_graph, "_maya_cmds", lambda: cmds)
    graph = RenderGraph()
    output = graph.add_node("output")
    camera = graph.add_node("camera")
    settings = graph.add_node("render_settings")
    graph.connect(camera.id, "out", output.id, "camera")
    graph.connect(settings.id, "out", output.id, "render_settings")
    camera.attrs["camera"] = "shotCam"
    output.attrs["output_path"] = str(tmp_path / "review" / "shot_layout")
    progress_events = []

    result = render_graph.apply_graph(
        graph,
        output.id,
        playblast=True,
        restore_after_playblast=False,
        progress_callback=lambda message, value: progress_events.append((message, value)),
    )

    assert result["camera"] == "shotCam"
    assert cmds.panel_cameras["modelPanel1"] == "shotCam"
    assert cmds.playblast_calls[0]["editorPanelName"] == "modelPanel1"
    assert cmds.playblast_calls[0]["camera"] == "shotCam"
    assert cmds.select_calls == [{"clear": True}]
    assert ("Running playblast image sequence...", 45) in progress_events
    assert ("Encoding review movie...", 90) in progress_events


def test_slate_playblast_isolates_smart_gate_guide(tmp_path):
    cmds = _SlateIsolationCmds()

    _playblast(
        cmds,
        {
            "output_path": str(tmp_path / "image_sequence" / "CHA" / "shot_CHA"),
            "slate_prefix": str(tmp_path / "image_sequence" / "slate" / "shot_slate"),
            "start_frame": 1001,
            "end_frame": 1001,
            "format": "image",
            "compression": "png",
        },
        "",
    )

    assert len(cmds.playblast_calls) == 2
    assert cmds.playblast_calls[0]["visibility"]["|hero.visibility"] is True
    assert cmds.playblast_calls[0]["visibility"]["|SmartGateGuide.visibility"] is False
    assert cmds.playblast_calls[1]["visibility"]["|hero.visibility"] is False
    assert cmds.playblast_calls[1]["visibility"]["|SmartGateGuide.visibility"] is True
    assert cmds.select_calls == [{"clear": True}, {"clear": True}]
    assert cmds.values["|hero.visibility"] is True
    assert cmds.values["|SmartGateGuide.visibility"] is True


def test_playblast_removes_existing_sequence_before_overwrite(tmp_path):
    prefix = tmp_path / "review" / "shot_CHA"
    prefix.parent.mkdir(parents=True)
    existing = prefix.parent / "shot_CHA_1001.png"
    existing.write_text("old", encoding="utf-8")
    cmds = _OverwritePlayblastCmds(existing)

    _playblast(
        cmds,
        {
            "output_path": str(prefix),
            "start_frame": 1001,
            "end_frame": 1001,
            "format": "image",
            "compression": "png",
        },
        "",
    )

    assert cmds.target_exists_during_playblast is False
    assert existing.read_text(encoding="utf-8") == "new"
    assert cmds.playblast_calls[0]["forceOverwrite"] is True


def test_thumbnail_is_extracted_from_movie(tmp_path, monkeypatch):
    prefix = tmp_path / "review" / "shot_CHA"
    movie = tmp_path / "review" / "mov" / "shot.mov"
    thumbnail = tmp_path / "review" / "thumbnail" / "shot.jpg"
    calls = {}

    def fake_encode(**kwargs):
        Path(kwargs["mov_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["mov_path"]).write_text("movie", encoding="utf-8")
        return True, str(kwargs["mov_path"])

    def fake_extract(*, mov_path, thumbnail_path, ffmpeg=""):
        calls["mov_path"] = str(mov_path)
        calls["thumbnail_path"] = str(thumbnail_path)
        Path(thumbnail_path).parent.mkdir(parents=True, exist_ok=True)
        Path(thumbnail_path).write_text("thumbnail", encoding="utf-8")
        return True, str(thumbnail_path)

    monkeypatch.setattr(playblast_package, "encode_prores_proxy_mov", fake_encode)
    monkeypatch.setattr(playblast_package, "extract_thumbnail_from_mov", fake_extract)
    monkeypatch.setattr(playblast_package, "find_ffmpeg", lambda project_config=None: "ffmpeg")

    _playblast(
        _OverwritePlayblastCmds(prefix.parent / "shot_CHA_1001.png"),
        {
            "output_path": str(prefix),
            "movie_path": str(movie),
            "image_pattern": (prefix.parent / "shot_CHA_%04d.png").as_posix(),
            "thumbnail": str(thumbnail),
            "start_frame": 1001,
            "end_frame": 1001,
            "format": "image",
            "compression": "png",
        },
        "",
    )

    assert calls == {"mov_path": str(movie), "thumbnail_path": str(thumbnail)}
    assert thumbnail.read_text(encoding="utf-8") == "thumbnail"


def test_record_transform_override_captures_world_transform():
    attrs = normalized_attrs("transform_override", {})

    recorded, target = record_transform_override(_TransformRecordCmds(), ["hero"], attrs)

    assert recorded is True
    assert target == "hero"
    assert attrs["translate_enabled"] is True
    assert attrs["rotate_enabled"] is True
    assert attrs["scale_enabled"] is True
    assert attrs["translate"] == [1.0, 2.0, 3.0]
    assert attrs["rotate"] == [10.0, 20.0, 30.0]
    assert attrs["scale"] == [1.5, 1.5, 1.5]


def test_finish_transform_recording_records_current_and_restores_start():
    attrs = normalized_attrs("transform_override", {})
    cmds = _TransformSessionCmds()

    started, target = start_transform_recording(cmds, ["hero"], attrs)
    cmds.values["translate"] = [8.0, 9.0, 10.0]
    cmds.values["rotate"] = [40.0, 50.0, 60.0]
    cmds.values["scale"] = [2.0, 2.0, 2.0]
    recorded, recorded_target = finish_transform_recording(cmds, ["hero"], attrs)

    assert started is True
    assert target == "hero"
    assert recorded is True
    assert recorded_target == "hero"
    assert attrs["recording"] is False
    assert attrs["record_start_transform"] == {}
    assert attrs["translate"] == [8.0, 9.0, 10.0]
    assert attrs["rotate"] == [40.0, 50.0, 60.0]
    assert attrs["scale"] == [2.0, 2.0, 2.0]
    assert cmds.values["translate"] == [1.0, 2.0, 3.0]
    assert cmds.values["rotate"] == [10.0, 20.0, 30.0]
    assert cmds.values["scale"] == [1.0, 1.0, 1.0]


def test_transform_override_matches_source_world_matrix():
    cmds = _MatchTransformCmds()

    _apply_transform_override(
        cmds,
        ["|Chair"],
        {
            "match_source": "Chair_place_LOC",
            "translate_enabled": True,
            "translate": [99.0, 99.0, 99.0],
        },
    )

    assert cmds.matrices["|Chair"] == cmds.source_matrix
    assert cmds.translation_sets == []


def test_object_state_restores_shape_material_assignments():
    cmds = _MaterialStateCmds()

    state = _capture_object_state(cmds, "|hero")
    cmds.assign("|hero|heroShape.f[0:3]", "redSG")
    _restore_object_state(cmds, "|hero", state)

    assert "|hero|heroShape.f[0:3]" in cmds.members["initialSG"]
    assert "|hero|heroShape.f[0:3]" not in cmds.members["redSG"]


def test_object_state_restores_geom_attrs():
    cmds = _GeomAttrCmds()

    state = _capture_object_state(cmds, "|hero")
    cmds.values["|hero.template"] = True
    cmds.values["|hero|heroShape.castsShadows"] = False
    cmds.values["|hero|heroShape.receiveShadows"] = False
    cmds.values["|hero|heroShape.motionBlur"] = False
    _restore_object_state(cmds, "|hero", state)

    assert cmds.values["|hero.template"] is False
    assert cmds.values["|hero|heroShape.castsShadows"] is True
    assert cmds.values["|hero|heroShape.receiveShadows"] is True
    assert cmds.values["|hero|heroShape.motionBlur"] is True


def test_isolate_final_objects_hides_non_final_geometry():
    cmds = _IsolationCmds()

    _isolate_final_objects(cmds, ["|hero"])

    assert cmds.values["|hero.visibility"] is True
    assert cmds.values["|desk.visibility"] is False


def test_apply_graph_without_output_objects_keeps_viewport_visibility(monkeypatch):
    cmds = _NoObjectInputCmds()
    monkeypatch.setattr(render_graph, "_maya_cmds", lambda: cmds)
    graph = RenderGraph()
    output = graph.add_node("output")

    result = render_graph.apply_graph(graph, output.id, playblast=False)

    assert result["objects"] == []
    assert not any(name.endswith(".visibility") for name in cmds.values)


def test_apply_graph_applies_quality_preset_on_apply(tmp_path, monkeypatch):
    from smartlib.dcc.maya import playblast_preset as maya_playblast_preset

    cmds = _NoObjectInputCmds()
    monkeypatch.setattr(render_graph, "_maya_cmds", lambda: cmds)
    calls = []
    monkeypatch.setattr(maya_playblast_preset, "apply_playblast_preset", lambda _config, preset: calls.append(preset))
    graph = RenderGraph()
    output = graph.add_node("output")
    output.attrs["quality_preset"] = "layout_geometry"

    render_graph.apply_graph(graph, output.id, playblast=False, project_config=_project_config(tmp_path))

    assert calls == ["layout_geometry"]


def test_viewport_state_restores_camera_view_values():
    cmds = _ViewportStateCmds()

    state = _capture_viewport_state(cmds)
    cmds.panel_cameras["modelPanel1"] = "shotCam"
    cmds.transforms["persp"]["translate"] = [20.0, 30.0, 40.0]
    cmds.attrs["perspShape.focalLength"] = 100.0
    cmds.attrs["perspShape.zoom"] = 2.5
    _restore_viewport_state(cmds, state)

    assert cmds.panel_cameras["modelPanel1"] == "persp"
    assert cmds.transforms["persp"]["translate"] == [1.0, 2.0, 3.0]
    assert cmds.transforms["persp"]["rotate"] == [10.0, 20.0, 30.0]
    assert cmds.attrs["perspShape.focalLength"] == 35.0
    assert cmds.attrs["perspShape.zoom"] == 1.0


def test_scene_state_data_round_trips_through_network_attr():
    cmds = _SceneStateCmds()
    data = {
        "schema": "smart_render_scene_state",
        "version": 1,
        "master_states": {"output1": {"objects": {"hero": {"visibility": True}}}},
    }

    _write_scene_state_data(cmds, data)

    assert _read_scene_state_data(cmds) == data


def test_render_graph_round_trips_through_scene_state(monkeypatch):
    cmds = _SceneStateCmds()
    monkeypatch.setattr(render_graph, "_maya_cmds", lambda: cmds)
    graph = RenderGraph()
    output = graph.add_node("output")
    objects = graph.add_node("object")
    camera = graph.add_node("camera")
    settings = graph.add_node("render_settings")
    graph.connect(objects.id, "out", output.id, "objects")
    graph.connect(camera.id, "out", output.id, "camera")
    graph.connect(settings.id, "out", output.id, "render_settings")
    output.attrs["output_path"] = "D:/show/images/shot010_beauty"

    render_graph.save_graph_to_scene(graph)
    restored = render_graph.load_graph_from_scene()

    assert restored is not None
    restored_output = next(node for node in restored.nodes if node.type == "output")
    restored_settings = next(node for node in restored.nodes if node.type == "render_settings")
    assert restored_output.attrs["output_path"] == "D:/show/images/shot010_beauty"
    assert "output_path" not in restored_settings.attrs
    assert len(restored.edges) == 3


class _FakeCmds:
    def objExists(self, name):
        return True


class _FrameRangeCmds:
    def __init__(self, scene=""):
        self.scene = str(scene)

    def file(self, query=False, sceneName=False):
        return self.scene if query and sceneName else ""

    def objExists(self, name):
        return True

    def currentTime(self, query=False):
        return 42 if query else 0

    def playbackOptions(self, query=False, minTime=False, maxTime=False, **kwargs):
        if query and minTime:
            return 1001
        if query and maxTime:
            return 1100
        return None

    def getAttr(self, name):
        values = {
            "defaultRenderGlobals.startFrame": 1010,
            "defaultRenderGlobals.endFrame": 1090,
        }
        return values[name]


class _ScenePathCmds:
    def __init__(self, scene):
        self.scene = str(scene)

    def file(self, query=False, sceneName=False):
        return self.scene if query and sceneName else ""


def _project_config(tmp_path):
    config_dir = tmp_path / "config" / "STKB"
    default_dir = tmp_path / "config" / "default"
    config_dir.mkdir(parents=True)
    default_dir.mkdir(parents=True)
    (config_dir / "templates_base.yml").write_text(
        "anchors:\n  project_name: STKB\n  project_root: \"{0}\"\n  fps: 24\n".format((tmp_path / "project").as_posix()),
        encoding="utf-8",
    )
    (config_dir / "review_package.yml").write_text(
        """
playblast_package:
  roots:
    output: "{shot_root}/output/review/{dept}/{layer}/{version}/{take}"
    publish: "{shot_root}/publish/review/{dept}/{layer}/{version}/{take}"
    output_review_build: "{shot_root}/output/review/{dept}/review_build/{version}/{take}"
    publish_review_build: "{shot_root}/publish/review/{dept}/review_build/{version}/{take}"
  paths:
    mov: "mov/{shot}_{dept}_{version}_{take}.mov"
    image_sequence: "images/{shot}_{dept}_{layer}_{version}_{take}_{frame}.png"
    slate_sequence: "slate/{shot}_{dept}_slate_{version}_{take}_{frame}.png"
    metadata_review: "metadata/review.json"
    metadata_playblast: "metadata/playblast.json"
    metadata_source_scene: "metadata/source_scene.json"
    thumbnail: "thumbnail/{shot}_{dept}_{version}_{take}.jpg"
    ae_dir: "ae"
    review_build_manifest: "{shot}_{dept}_build_{version}_{take}.json"
    review_build_script: "ae/scripts/{shot}_{dept}_build_{version}_{take}.jsx"
    review_build_log: "ae/data/{shot}_{dept}_build_{version}_{take}.log"
    review_build_template_project: "ae/review_project.aep"
    review_build_template_used: "ae/template_used.json"
    review_build_slots: "slots.json"
""",
        encoding="utf-8",
    )
    return ProjectConfig(config_dir)


class _CastCmds:
    def __init__(self):
        self.nodes = {
            "Hero:geo_GRP": "transform",
            "Hero:geoShape": "mesh",
            "Hero:camShape": "camera",
        }

    def objExists(self, name):
        return name in self.nodes

    def referenceQuery(self, reference_node, namespace=False):
        return ":Hero" if namespace and reference_node == "HeroRN" else ""

    def ls(self, pattern=None, type=None, long=False):
        if pattern == "Hero:*" and type == "mesh":
            return ["Hero:geoShape"]
        return []

    def nodeType(self, name):
        return self.nodes.get(name, "")

    def listRelatives(self, name, parent=False, fullPath=False, allDescendents=False, **kwargs):
        if name == "Hero:geo_GRP" and allDescendents:
            return ["Hero:geoShape", "Hero:camShape"]
        if name == "Hero:geoShape" and parent:
            return ["|Hero:geo_GRP"]
        return []


class _DuplicateNameCmds:
    def __init__(self):
        self.values = {}
        self.nodes = {
            "|A|geo": "transform",
            "|A|geoShape": "mesh",
            "|B|geo": "transform",
            "|B|geoShape": "mesh",
        }

    def objExists(self, name):
        return name in self.nodes or name.startswith("defaultResolution.") or name in {"|A|geo.visibility", "|B|geo.visibility"}

    def ls(self, pattern=None, type=None, long=False, **kwargs):
        if pattern == "geo" and long:
            return ["|A|geo", "|B|geo"]
        if pattern in self.nodes and long:
            return [pattern]
        if type == "mesh":
            return ["|A|geoShape", "|B|geoShape"]
        return []

    def nodeType(self, name):
        return self.nodes.get(name, "")

    def listRelatives(self, name, parent=False, fullPath=False, **kwargs):
        if parent and name == "|A|geoShape":
            return ["|A|geo"]
        if parent and name == "|B|geoShape":
            return ["|B|geo"]
        return []

    def setAttr(self, name, value):
        self.values[name] = value

    def playbackOptions(self, *args, **kwargs):
        return None


class _CameraCmds:
    def __init__(self):
        self.values = {}

    def objExists(self, name):
        return name in {"cam1", "camShape"} or name.startswith("camShape.")

    def nodeType(self, name):
        return "transform"

    def listRelatives(self, name, shapes=False, type=None, fullPath=False, **kwargs):
        return ["camShape"] if name == "cam1" and shapes and type == "camera" else []

    def setAttr(self, name, value):
        self.values[name] = value


class _PlayblastCameraCmds:
    def __init__(self):
        self.panel_cameras = {"modelPanel1": "persp"}
        self.playblast_calls = []
        self.select_calls = []
        self.values = {}

    def objExists(self, name):
        return name in {"shotCam", "shotCamShape"} or name.startswith(("defaultResolution.", "defaultRenderGlobals."))

    def getPanel(self, **kwargs):
        if kwargs.get("withFocus"):
            return "modelPanel1"
        if kwargs.get("typeOf"):
            return "modelPanel" if kwargs["typeOf"] == "modelPanel1" else ""
        if kwargs.get("visiblePanels"):
            return ["modelPanel1"]
        if kwargs.get("type") == "modelPanel":
            return ["modelPanel1"]
        return ""

    def modelPanel(self, panel, edit=False, query=False, camera=None):
        if edit and camera:
            self.panel_cameras[panel] = camera
        if query and camera:
            return self.panel_cameras.get(panel, "")
        return ""

    def nodeType(self, name):
        return "transform"

    def listRelatives(self, name, shapes=False, type=None, fullPath=False, **kwargs):
        if name == "shotCam" and shapes and type == "camera":
            return ["shotCamShape"]
        return []

    def setAttr(self, name, value):
        self.values[name] = value

    def getAttr(self, name):
        values = {
            "defaultRenderGlobals.startFrame": 1001,
            "defaultRenderGlobals.endFrame": 1001,
        }
        return values.get(name, self.values.get(name, 1))

    def playbackOptions(self, query=False, minTime=False, maxTime=False, **kwargs):
        if query and minTime:
            return 1001
        if query and maxTime:
            return 1001
        self.values.update(kwargs)
        return None

    def ls(self, type=None, long=False, **kwargs):
        return []

    def playblast(self, **kwargs):
        panel = kwargs.get("editorPanelName", "")
        self.playblast_calls.append({**kwargs, "camera": self.panel_cameras.get(panel, "")})
        return kwargs.get("filename", "")

    def select(self, **kwargs):
        self.select_calls.append(dict(kwargs))


class _SlateIsolationCmds:
    def __init__(self):
        self.values = {
            "|hero.visibility": True,
            "|SmartGateGuide.visibility": True,
        }
        self.nodes = {
            "|hero": "transform",
            "|hero|heroShape": "mesh",
            "|SmartGateGuide": "transform",
            "|SmartGateGuide|guideShape": "mesh",
        }
        self.playblast_calls = []
        self.select_calls = []

    def objExists(self, name):
        return name in self.nodes or name in self.values

    def nodeType(self, name):
        return self.nodes.get(name, "")

    def ls(self, pattern=None, type=None, long=False, **kwargs):
        if pattern == "SmartGateGuide*":
            return ["|SmartGateGuide"]
        if type == "SmartViewportGateGuide":
            return []
        if type == "mesh":
            return ["|hero|heroShape", "|SmartGateGuide|guideShape"]
        return []

    def listRelatives(self, name, parent=False, fullPath=False, **kwargs):
        if parent and name == "|hero|heroShape":
            return ["|hero"]
        if parent and name == "|SmartGateGuide|guideShape":
            return ["|SmartGateGuide"]
        return []

    def getAttr(self, name):
        return self.values[name]

    def setAttr(self, name, value):
        self.values[name] = value

    def playblast(self, **kwargs):
        self.playblast_calls.append({**kwargs, "visibility": dict(self.values)})
        return kwargs.get("filename", "")

    def select(self, **kwargs):
        self.select_calls.append(dict(kwargs))


class _OverwritePlayblastCmds:
    def __init__(self, target):
        self.target = Path(target)
        self.target_exists_during_playblast = None
        self.playblast_calls = []

    def playblast(self, **kwargs):
        self.target_exists_during_playblast = self.target.exists()
        self.playblast_calls.append(dict(kwargs))
        filename = Path(str(kwargs.get("filename") or ""))
        candidate = filename.parent / f"{filename.name}.1001.png"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("new", encoding="utf-8")
        return str(filename)


class _TransformRecordCmds:
    def objExists(self, name):
        return name == "hero"

    def xform(self, name, query=False, worldSpace=False, translation=False, rotation=False, scale=False):
        if translation:
            return [1.0, 2.0, 3.0]
        if rotation:
            return [10.0, 20.0, 30.0]
        if scale:
            return [1.5, 1.5, 1.5]
        return []


class _TransformSessionCmds:
    def __init__(self):
        self.values = {
            "translate": [1.0, 2.0, 3.0],
            "rotate": [10.0, 20.0, 30.0],
            "scale": [1.0, 1.0, 1.0],
        }

    def objExists(self, name):
        return name == "hero"

    def xform(self, name, query=False, worldSpace=False, translation=None, rotation=None, scale=None):
        if query and translation:
            return list(self.values["translate"])
        if query and rotation:
            return list(self.values["rotate"])
        if query and scale:
            return list(self.values["scale"])
        if isinstance(translation, list):
            self.values["translate"] = list(translation)
        if isinstance(rotation, list):
            self.values["rotate"] = list(rotation)
        if isinstance(scale, list):
            self.values["scale"] = list(scale)
        return []


class _MatchTransformCmds:
    def __init__(self):
        self.nodes = {
            "|Chair_place_LOC": "transform",
            "|Chair": "transform",
        }
        self.source_matrix = [
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            10.0,
            20.0,
            30.0,
            1.0,
        ]
        self.matrices = {}
        self.translation_sets = []

    def objExists(self, name):
        return name in self.nodes or name == "Chair_place_LOC"

    def nodeType(self, name):
        return self.nodes.get(name, "transform" if name == "Chair_place_LOC" else "")

    def ls(self, pattern=None, long=False, **kwargs):
        if pattern == "Chair_place_LOC" and long:
            return ["|Chair_place_LOC"]
        return []

    def xform(self, name, query=False, worldSpace=False, matrix=None, translation=None, **kwargs):
        if query and matrix:
            return list(self.source_matrix) if name in {"|Chair_place_LOC", "Chair_place_LOC"} else []
        if isinstance(matrix, list):
            self.matrices[name] = list(matrix)
        if isinstance(translation, list):
            self.translation_sets.append((name, list(translation)))
        return []


class _GeomAttrCmds:
    def __init__(self):
        self.values = {
            "|hero.visibility": True,
            "|hero.template": False,
            "|hero|heroShape.visibility": True,
            "|hero|heroShape.template": False,
            "|hero|heroShape.castsShadows": True,
            "|hero|heroShape.receiveShadows": True,
            "|hero|heroShape.motionBlur": True,
        }
        self.nodes = {
            "|hero": "transform",
            "|hero|heroShape": "mesh",
        }

    def objExists(self, name):
        return name in self.nodes or name in self.values

    def nodeType(self, name):
        return self.nodes.get(name, "")

    def getAttr(self, name):
        return self.values[name]

    def setAttr(self, name, value):
        self.values[name] = value

    def xform(self, name, query=False, worldSpace=False, translation=False, rotation=False, scale=False, **kwargs):
        if query and translation:
            return [0.0, 0.0, 0.0]
        if query and rotation:
            return [0.0, 0.0, 0.0]
        if query and scale:
            return [1.0, 1.0, 1.0]
        return []

    def listRelatives(self, name, shapes=False, fullPath=False, allDescendents=False, **kwargs):
        if name == "|hero" and (shapes or allDescendents):
            return ["|hero|heroShape"]
        return []

    def listConnections(self, name, type=None):
        return []


class _MaterialStateCmds:
    def __init__(self):
        self.members = {
            "initialSG": ["|hero|heroShape.f[0:3]"],
            "redSG": [],
        }
        self.values = {}

    def objExists(self, name):
        return name in {
            "|hero",
            "|hero.visibility",
            "|hero|heroShape",
            "|hero|heroShape.f[0:3]",
            "initialSG",
            "redSG",
        }

    def getAttr(self, name):
        return True

    def xform(self, name, query=False, worldSpace=False, translation=False, rotation=False, scale=False, **kwargs):
        if query and translation:
            return [0.0, 0.0, 0.0]
        if query and rotation:
            return [0.0, 0.0, 0.0]
        if query and scale:
            return [1.0, 1.0, 1.0]
        return []

    def listRelatives(self, name, shapes=False, fullPath=False, **kwargs):
        if name == "|hero" and shapes:
            return ["|hero|heroShape"]
        return []

    def listConnections(self, name, type=None):
        if type != "shadingEngine":
            return []
        return [engine for engine, members in self.members.items() if name in {item.split(".", 1)[0] for item in members} or name in members]

    def sets(self, *args, **kwargs):
        if kwargs.get("query"):
            return list(self.members.get(args[0], []))
        member = args[0]
        shading_engine = kwargs.get("forceElement")
        if kwargs.get("edit") and shading_engine:
            self.assign(member, shading_engine)
        return []

    def setAttr(self, name, value):
        self.values[name] = value

    def assign(self, member, shading_engine):
        for members in self.members.values():
            while member in members:
                members.remove(member)
        self.members.setdefault(shading_engine, []).append(member)


class _IsolationCmds:
    def __init__(self):
        self.values = {}

    def objExists(self, name):
        return name in {"|hero", "|desk", "|heroShape", "|deskShape", "|hero.visibility", "|desk.visibility"}

    def nodeType(self, name):
        return "transform"

    def ls(self, type=None, long=False):
        if type == "mesh":
            return ["|hero|heroShape", "|desk|deskShape"]
        return []

    def listRelatives(self, name, parent=False, fullPath=False, **kwargs):
        if name == "|hero|heroShape":
            return ["|hero"]
        if name == "|desk|deskShape":
            return ["|desk"]
        return []

    def setAttr(self, name, value):
        self.values[name] = value


class _NoObjectInputCmds:
    def __init__(self):
        self.values = {}

    def setAttr(self, name, value):
        self.values[name] = value

    def playbackOptions(self, *args, **kwargs):
        return None


class _ViewportStateCmds:
    def __init__(self):
        self.panel_cameras = {"modelPanel1": "persp"}
        self.nodes = {
            "persp": "transform",
            "perspShape": "camera",
            "shotCam": "transform",
            "shotCamShape": "camera",
        }
        self.transforms = {
            "persp": {
                "translate": [1.0, 2.0, 3.0],
                "rotate": [10.0, 20.0, 30.0],
                "scale": [1.0, 1.0, 1.0],
            }
        }
        self.attrs = {
            "perspShape.focalLength": 35.0,
            "perspShape.overscan": 1.0,
            "perspShape.zoom": 1.0,
        }

    def getPanel(self, **kwargs):
        if kwargs.get("withFocus"):
            return "modelPanel1"
        if kwargs.get("typeOf"):
            return "modelPanel" if kwargs["typeOf"] == "modelPanel1" else ""
        if kwargs.get("visiblePanels"):
            return ["modelPanel1"]
        if kwargs.get("type") == "modelPanel":
            return ["modelPanel1"]
        return ""

    def modelPanel(self, panel, edit=False, query=False, camera=None):
        if edit and camera:
            self.panel_cameras[panel] = camera
        if query and camera:
            return self.panel_cameras.get(panel, "")
        return ""

    def objExists(self, name):
        return name in self.nodes or name in self.attrs

    def nodeType(self, name):
        return self.nodes.get(name, "")

    def listRelatives(self, name, shapes=False, parent=False, type=None, fullPath=False, **kwargs):
        if name == "persp" and shapes and type == "camera":
            return ["perspShape"]
        if name == "shotCam" and shapes and type == "camera":
            return ["shotCamShape"]
        if name == "perspShape" and parent:
            return ["persp"]
        if name == "shotCamShape" and parent:
            return ["shotCam"]
        return []

    def xform(self, name, query=False, worldSpace=False, translation=None, rotation=None, scale=None):
        values = self.transforms.setdefault(name, {"translate": [0.0, 0.0, 0.0], "rotate": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]})
        if query and translation:
            return list(values["translate"])
        if query and rotation:
            return list(values["rotate"])
        if query and scale:
            return list(values["scale"])
        if isinstance(translation, list):
            values["translate"] = list(translation)
        if isinstance(rotation, list):
            values["rotate"] = list(rotation)
        if isinstance(scale, list):
            values["scale"] = list(scale)
        return []

    def getAttr(self, name):
        return self.attrs[name]

    def setAttr(self, name, value):
        self.attrs[name] = value


class _SceneStateCmds:
    def __init__(self):
        self.nodes = set()
        self.attrs = {}

    def objExists(self, name):
        if "." in name:
            return name in self.attrs
        return name in self.nodes

    def createNode(self, node_type, name):
        self.nodes.add(name)
        return name

    def addAttr(self, node, longName, dataType):
        self.attrs[f"{node}.{longName}"] = ""

    def setAttr(self, name, value, type=None):
        self.attrs[name] = value

    def getAttr(self, name):
        return self.attrs.get(name, "")
