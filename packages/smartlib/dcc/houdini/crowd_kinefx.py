from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Callable

from smartlib.crowd.behavior import Agent, BehaviorGoal, BehaviorSystem
from smartlib.crowd.interaction import InteractionSystem
from smartlib.crowd.schema import load_behavior_schema
from smartlib.crowd.yamlio import load_yaml
from smartlib.dcc.houdini.crowd_loader import interaction_points_from_data


REQUIRED_PROTOTYPE_FILES = (
    "character.fbx",
    "walk.fbx",
    "sit_down.fbx",
    "sit_idle.fbx",
    "interaction.yaml",
    "animation.yaml",
)

DEFAULT_FPS = 24.0
DEFAULT_WALK_SPEED = 1.2
DEFAULT_START_DISTANCE = 3.0
DEFAULT_ALIGN_FRAMES = 16
DEFAULT_SIT_DOWN_FRAMES = 40
DEFAULT_WALK_CLIP_FRAMES = 24
DEFAULT_SIT_IDLE_CLIP_FRAMES = 24


@dataclass(frozen=True)
class CrowdPrototypeFiles:
    character_fbx: Path
    walk_fbx: Path
    sit_down_fbx: Path
    sit_idle_fbx: Path
    interaction_yaml: Path
    animation_yaml: Path

    def as_mapping(self) -> dict[str, str]:
        return {
            "character_fbx": _as_posix(self.character_fbx),
            "walk_fbx": _as_posix(self.walk_fbx),
            "sit_down_fbx": _as_posix(self.sit_down_fbx),
            "sit_idle_fbx": _as_posix(self.sit_idle_fbx),
            "interaction_yaml": _as_posix(self.interaction_yaml),
            "animation_yaml": _as_posix(self.animation_yaml),
        }


def resolve_prototype_files(
    crowd_dir: str | Path,
    *,
    exists: Callable[[Path], bool] | None = None,
) -> CrowdPrototypeFiles:
    root = Path(crowd_dir)
    exists = exists or Path.exists
    paths = {name: root / name for name in REQUIRED_PROTOTYPE_FILES}
    missing = [str(path) for path in paths.values() if not exists(path)]
    if missing:
        raise FileNotFoundError("Missing Smart Crowd prototype files: {}".format(", ".join(missing)))
    return CrowdPrototypeFiles(
        character_fbx=paths["character.fbx"],
        walk_fbx=paths["walk.fbx"],
        sit_down_fbx=paths["sit_down.fbx"],
        sit_idle_fbx=paths["sit_idle.fbx"],
        interaction_yaml=paths["interaction.yaml"],
        animation_yaml=paths["animation.yaml"],
    )


def build_single_agent_plan_from_data(
    interaction_data: dict[str, Any],
    animation_data: dict[str, Any] | None = None,
    *,
    files: CrowdPrototypeFiles | None = None,
    interaction_type: str = "seat",
    agent_id: str = "agent_001",
    schema_path: str | Path | None = None,
) -> dict[str, Any]:
    schema = load_behavior_schema(schema_path)
    interactions = InteractionSystem.from_yaml_data(schema, interaction_data)
    behavior = BehaviorSystem(interactions)
    agent = Agent(id=agent_id)
    trace = behavior.execute_single_agent_goal(agent, BehaviorGoal(name=f"find_{interaction_type}", interaction_type=interaction_type))
    selected = _selected_point(interaction_data, trace.interaction_point_id)
    animation = dict((animation_data or {}).get("animation") or {})
    locomotion = _build_locomotion_plan(selected, animation_data or {})
    interaction_points = interaction_points_from_data(interaction_data, schema_path=schema.path)
    runtime = _build_runtime_settings(locomotion, animation_data or {})
    return {
        "schema": "smart_crowd_houdini_plan.v1",
        "agent": {
            "id": agent.id,
            "state": agent.state,
            "position": _vector_mapping(agent.position),
            "rotation": _vector_mapping(agent.rotation),
        },
        "goal": {
            "interaction_type": interaction_type,
            "interaction_point_id": trace.interaction_point_id,
            "steps": list(trace.steps),
        },
        "target": selected,
        "animation": animation,
        "locomotion": locomotion,
        "runtime": runtime,
        "interaction_points": interaction_points,
        "clips": {
            "Walk": "walk",
            "Sit Down": "sit_down",
            "Sit Idle": "sit_idle",
        },
        "files": files.as_mapping() if files else {},
    }


def build_single_agent_plan(
    crowd_dir: str | Path,
    *,
    interaction_type: str = "seat",
    agent_id: str = "agent_001",
    schema_path: str | Path | None = None,
) -> dict[str, Any]:
    files = resolve_prototype_files(crowd_dir)
    interaction_data = load_yaml(files.interaction_yaml)
    animation_data = load_yaml(files.animation_yaml)
    return build_single_agent_plan_from_data(
        interaction_data,
        animation_data,
        files=files,
        interaction_type=interaction_type,
        agent_id=agent_id,
        schema_path=schema_path,
    )


def _build_locomotion_plan(target: dict[str, Any], animation_data: dict[str, Any]) -> dict[str, Any]:
    settings = dict(animation_data.get("behavior") or animation_data.get("behavior_settings") or {})
    animation = dict(animation_data.get("animation") or {})
    root_motion = dict(animation.get("rootMotion") or animation.get("root_motion") or {})
    fps = _positive_float(settings.get("fps"), DEFAULT_FPS)
    walk_speed = _positive_float(settings.get("walk_speed"), _animation_speed_for_houdini(animation, root_motion))
    start_distance = _positive_float(settings.get("start_distance"), DEFAULT_START_DISTANCE)
    sit_down_frames = max(1, int(_positive_float(settings.get("sit_down_frames"), DEFAULT_SIT_DOWN_FRAMES)))
    walk_clip_start_frame = max(1, int(_positive_float(settings.get("walk_clip_start_frame"), _animation_start_frame(animation, root_motion))))
    walk_clip_frames = max(1, int(_positive_float(settings.get("walk_clip_frames"), _animation_duration_frames(animation, root_motion, DEFAULT_WALK_CLIP_FRAMES))))
    sit_down_clip_start_frame = max(1, int(_positive_float(settings.get("sit_down_clip_start_frame"), 1.0)))
    sit_down_clip_frames = max(1, int(_positive_float(settings.get("sit_down_clip_frames"), sit_down_frames)))
    sit_idle_clip_start_frame = max(1, int(_positive_float(settings.get("sit_idle_clip_start_frame"), 1.0)))
    sit_idle_clip_frames = max(1, int(_positive_float(settings.get("sit_idle_clip_frames"), DEFAULT_SIT_IDLE_CLIP_FRAMES)))

    seat = _mapping_vector(target.get("position"), (0.0, 0.0, 0.0))
    approach = _mapping_vector(target.get("approach_position"), (seat[0] - 1.0, seat[1], seat[2]))
    away = _normalized_vector(
        (approach[0] - seat[0], approach[1] - seat[1], approach[2] - seat[2]),
        fallback=(-1.0, 0.0, 0.0),
    )
    start = (
        approach[0] + away[0] * start_distance,
        approach[1] + away[1] * start_distance,
        approach[2] + away[2] * start_distance,
    )
    walk_distance = _distance(start, approach)
    align_distance = _distance(approach, seat)
    align_speed = _positive_float(settings.get("align_speed"), walk_speed)
    walk_frames = max(1, int(math.ceil((walk_distance / walk_speed) * fps - 1e-6)))
    if settings.get("align_frames") is not None:
        align_frames = max(1, int(_positive_float(settings.get("align_frames"), DEFAULT_ALIGN_FRAMES)))
    elif align_distance > 1e-6:
        align_frames = max(1, int(math.ceil((align_distance / align_speed) * fps - 1e-6)))
    else:
        align_frames = DEFAULT_ALIGN_FRAMES
    walk_start_frame = int(settings.get("walk_start_frame") or 1)
    walk_end_frame = walk_start_frame + walk_frames - 1
    align_end_frame = walk_end_frame + align_frames
    sit_down_end_frame = align_end_frame + sit_down_frames
    return {
        "fps": fps,
        "walk_speed": walk_speed,
        "align_speed": align_speed,
        "start_distance": start_distance,
        "walk_distance": walk_distance,
        "align_distance": align_distance,
        "travel_distance": walk_distance + align_distance,
        "walk_frames": walk_frames,
        "align_frames": align_frames,
        "sit_down_frames": sit_down_frames,
        "walk_clip_start_frame": walk_clip_start_frame,
        "walk_clip_frames": walk_clip_frames,
        "sit_down_clip_start_frame": sit_down_clip_start_frame,
        "sit_down_clip_frames": sit_down_clip_frames,
        "sit_idle_clip_start_frame": sit_idle_clip_start_frame,
        "sit_idle_clip_frames": sit_idle_clip_frames,
        "walk_start_frame": walk_start_frame,
        "walk_end_frame": walk_end_frame,
        "align_end_frame": align_end_frame,
        "sit_down_end_frame": sit_down_end_frame,
        "sit_idle_start_frame": sit_down_end_frame + 1,
        "start_position": _vector_mapping(start),
        "approach_position": _vector_mapping(approach),
        "seat_position": _vector_mapping(seat),
    }


def _build_runtime_settings(locomotion: dict[str, Any], animation_data: dict[str, Any]) -> dict[str, Any]:
    settings = dict(animation_data.get("behavior") or animation_data.get("behavior_settings") or {})
    fps = _positive_float(settings.get("fps"), float(locomotion.get("fps", DEFAULT_FPS)))
    sit_down_frames = max(1, int(_positive_float(settings.get("sit_down_frames"), float(locomotion.get("sit_down_frames", DEFAULT_SIT_DOWN_FRAMES)))))
    return {
        "agent_count": max(1, int(_positive_float(settings.get("runtime_agent_count"), 4.0))),
        "seed": int(_positive_float(settings.get("runtime_seed"), 7.0)),
        "fps": fps,
        "walk_speed": _positive_float(settings.get("walk_speed"), float(locomotion.get("walk_speed", DEFAULT_WALK_SPEED))),
        "align_speed": _positive_float(settings.get("align_speed"), float(locomotion.get("align_speed", locomotion.get("walk_speed", DEFAULT_WALK_SPEED)))),
        "query_radius": _positive_float(settings.get("seat_query_radius"), 8.0),
        "spawn_radius": _positive_float(settings.get("runtime_spawn_radius"), max(2.0, float(locomotion.get("travel_distance", 3.0)) * 1.5)),
        "arrive_distance": _positive_float(settings.get("arrive_distance"), 0.05),
        "sit_distance": _positive_float(settings.get("sit_distance"), 0.05),
        "align_duration": _positive_float(settings.get("align_duration"), float(locomotion.get("align_frames", DEFAULT_ALIGN_FRAMES)) / fps if fps else DEFAULT_ALIGN_FRAMES / DEFAULT_FPS),
        "sit_down_duration": sit_down_frames / fps if fps else 0.0,
    }


def create_single_agent_seat_prototype(
    crowd_dir: str | Path,
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    interaction_type: str = "seat",
    schema_path: str | Path | None = None,
    replace_existing: bool = False,
    update_existing: bool = True,
    rebuild_kinefx: bool = False,
) -> dict[str, Any]:
    """Create a Houdini network shell for the first single-agent seat prototype.

    By default, re-running updates the existing subnet without rebuilding FBX or
    KineFX import nodes. Destroying or recreating cooked FBX/KineFX import trees
    can crash Houdini in some builds.
    """

    import hou

    files = resolve_prototype_files(crowd_dir)
    interaction_data = load_yaml(files.interaction_yaml)
    animation_data = load_yaml(files.animation_yaml)
    plan = build_single_agent_plan_from_data(
        interaction_data,
        animation_data,
        files=files,
        interaction_type=interaction_type,
        schema_path=schema_path,
    )

    parent = hou.node(parent_path)
    if parent is None:
        raise RuntimeError(f"Houdini parent node was not found: {parent_path}")

    root, was_existing = _create_or_update_root(
        parent,
        "subnet",
        node_name,
        replace_existing=replace_existing,
        update_existing=update_existing,
    )
    root.setComment("Smart Crowd single-agent seat prototype")
    root.setUserData("smart_crowd_plan", repr(plan))

    seat_geo = _child_or_create(root, "geo", "interaction_points")
    _clear_children(seat_geo)
    seat_python = seat_geo.createNode("python", "load_interaction_yaml")
    seat_python.parm("python").set(_interaction_python_sop(files.interaction_yaml, schema_path))
    seat_null = seat_geo.createNode("null", "OUT_SEAT_POINTS")
    seat_null.setInput(0, seat_python)
    seat_null.setDisplayFlag(True)
    seat_null.setRenderFlag(True)

    import_geo = root.node("kinefx_imports")
    if import_geo is None:
        import_geo = root.createNode("geo", "kinefx_imports")
        _clear_children(import_geo)
        _create_kinefx_imports(hou, import_geo, files, plan)
    elif rebuild_kinefx and not was_existing:
        _clear_children(import_geo)
        _create_kinefx_imports(hou, import_geo, files, plan)
    else:
        _update_existing_kinefx_preview(hou, import_geo, files, plan)

    plan_geo = _child_or_create(root, "geo", "behavior_plan")
    _clear_children(plan_geo)
    plan_python = plan_geo.createNode("python", "build_behavior_plan")
    plan_python.parm("python").set(_plan_python_sop(plan))
    plan_null = plan_geo.createNode("null", "OUT_BEHAVIOR_PLAN")
    plan_null.setInput(0, plan_python)
    plan_null.setDisplayFlag(True)
    plan_null.setRenderFlag(True)

    controller_geo = _child_or_create(root, "geo", "single_agent_controller")
    _clear_children(controller_geo)
    controller_python = controller_geo.createNode("python", "preview_single_agent_behavior")
    controller_python.parm("python").set(_single_agent_preview_python_sop(plan))
    controller_null = controller_geo.createNode("null", "OUT_SINGLE_AGENT_PREVIEW")
    controller_null.setInput(0, controller_python)
    controller_null.setDisplayFlag(True)
    controller_null.setRenderFlag(True)

    if root.node("agent_crowd_pipeline") is None and not was_existing:
        _create_agent_crowd_scaffold(hou, root, files, plan)
    _ensure_agent_character_preview(hou, root, files)
    _ensure_agent_crowd_scaffold_nodes(hou, root, files, plan)
    _ensure_runtime_agent_source(hou, root, plan)

    runtime_geo = _child_or_create(root, "geo", "runtime_behavior_preview")
    _clear_children(runtime_geo)
    _create_runtime_behavior_preview(hou, root, runtime_geo, plan)
    _ensure_runtime_behavior_driver(hou, root, plan)
    _ensure_crowd_clip_state_driver(hou, root, plan)
    _ensure_agent_clip_bridge(hou, root, plan)
    _ensure_agent_crowd_behavior_output(hou, root, plan)
    experiment_geo = _child_or_create(root, "geo", "agent_clip_experiment")
    _clear_children(experiment_geo)
    _create_agent_clip_experiment(hou, root, experiment_geo, plan)
    _ensure_runtime_kinefx_preview(hou, root, plan)
    _ensure_agent_crowd_visual_preview(hou, root, plan)
    _ensure_runtime_dop_result_preview_node(hou, root, plan)

    if not was_existing:
        _add_note(root, "README", _network_note(files, plan))
    root.layoutChildren()
    return {"node": root.path(), "plan": plan, "updated_existing": was_existing}


def update_single_agent_seat_prototype(
    crowd_dir: str | Path,
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    interaction_type: str = "seat",
    schema_path: str | Path | None = None,
) -> dict[str, Any]:
    """Update the lightweight behavior preview without rebuilding FBX imports."""
    return create_single_agent_seat_prototype(
        crowd_dir,
        parent_path=parent_path,
        node_name=node_name,
        interaction_type=interaction_type,
        schema_path=schema_path,
        replace_existing=False,
        update_existing=True,
        rebuild_kinefx=False,
    )


def activate_agent_clip_test(
    clip_name: str,
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
) -> dict[str, Any]:
    """Un-bypass exactly one TEST_AGENTCLIP_* node and refresh the guard."""

    import hou

    requested = str(clip_name or "").strip()
    specs = _agent_clip_test_specs()
    valid = {spec["clip"] for spec in specs}
    if requested not in valid:
        raise ValueError("clip_name must be one of: {}".format(", ".join(sorted(valid))))

    experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
    for spec in specs:
        node = experiment.node(spec["node"])
        if node is None:
            continue
        _safe_bypass(node, spec["clip"] != requested)
    return refresh_agent_clip_unbypass_guard(parent_path=parent_path, node_name=node_name)


def deactivate_agent_clip_tests(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
) -> dict[str, Any]:
    """Bypass all TEST_AGENTCLIP_* nodes and refresh the guard."""

    import hou

    experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
    for spec in _agent_clip_test_specs():
        node = experiment.node(spec["node"])
        if node is not None:
            _safe_bypass(node, True)
    return refresh_agent_clip_unbypass_guard(parent_path=parent_path, node_name=node_name)


def run_agent_clip_activation_sequence(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    reset_to_safe: bool = True,
) -> list[dict[str, Any]]:
    """Activate walk, sit_down, and sit_idle one at a time and record guard status.

    This only changes TEST_AGENTCLIP_* bypass states. It does not connect those
    nodes to the safe display output.
    """

    results: list[dict[str, Any]] = []
    try:
        for spec in _agent_clip_test_specs():
            guard = activate_agent_clip_test(
                spec["clip"],
                parent_path=parent_path,
                node_name=node_name,
            )
            results.append(
                {
                    "clip": spec["clip"],
                    "node": spec["node"],
                    "result_status": guard.get("result_status", ""),
                    "active_test_node_count": guard.get("active_test_node_count", 0),
                    "active_test_nodes": guard.get("active_test_nodes", ""),
                    "recommended_display_node": guard.get("recommended_display_node", ""),
                    "safe_to_cook_single_test": guard.get("safe_to_cook_single_test", 0),
                    "next_step": guard.get("next_step", ""),
                }
            )
    finally:
        if reset_to_safe:
            deactivate_agent_clip_tests(parent_path=parent_path, node_name=node_name)
    return results


def cook_agent_clip_test(
    clip_name: str,
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    reset_to_safe: bool = True,
) -> dict[str, Any]:
    """Activate and cook exactly one TEST_AGENTCLIP_* node."""

    import hou

    requested = str(clip_name or "").strip()
    spec = _agent_clip_test_spec(requested)
    guard = activate_agent_clip_test(requested, parent_path=parent_path, node_name=node_name)
    result: dict[str, Any] = {
        "clip": requested,
        "node": spec["node"],
        "guard_status": guard.get("result_status", ""),
        "safe_to_cook_single_test": guard.get("safe_to_cook_single_test", 0),
        "cook_status": "not_started",
        "input_point_count": 0,
        "point_count": 0,
        "error": "",
    }

    try:
        if not guard:
            result["cook_status"] = "blocked_by_missing_guard_geometry"
            result["error"] = "OUT_AGENT_CLIP_UNBYPASS_GUARD did not return detail attributes."
            return result
        experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
        active_state = _agent_clip_active_state(experiment)
        result["active_test_nodes"] = ", ".join(active_state["active_nodes"]) or "none"
        result["active_test_node_count"] = len(active_state["active_nodes"])
        if guard.get("result_status") != "single_agentclip_test_active" and active_state["active_nodes"] != [spec["node"]]:
            result["cook_status"] = "blocked_by_guard"
            result["error"] = "Expected single_agentclip_test_active before cooking. guard_status={!r}, active_test_nodes={!r}".format(
                guard.get("result_status", ""),
                result["active_test_nodes"],
            )
            return result
        node = experiment.node(spec["node"])
        if node is None:
            result["cook_status"] = "missing_test_node"
            result["error"] = "{} was not found.".format(spec["node"])
            return result
        try:
            input_node = node.input(0)
            if input_node is not None:
                input_geo = input_node.geometry()
                result["input_point_count"] = len(input_geo.points()) if input_geo is not None else 0
            node.cook(force=True)
            geo = node.geometry()
            result["point_count"] = len(geo.points()) if geo is not None else 0
            result["cook_status"] = "cook_ok"
        except Exception as exc:
            result["cook_status"] = "cook_error"
            result["error"] = str(exc)
        return result
    finally:
        if reset_to_safe:
            deactivate_agent_clip_tests(parent_path=parent_path, node_name=node_name)


def run_agent_clip_cook_sequence(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    stop_on_error: bool = True,
    reset_to_safe: bool = True,
) -> list[dict[str, Any]]:
    """Cook walk, sit_down, and sit_idle Agent Clip test nodes one at a time."""

    results: list[dict[str, Any]] = []
    try:
        for spec in _agent_clip_test_specs():
            result = cook_agent_clip_test(
                spec["clip"],
                parent_path=parent_path,
                node_name=node_name,
                reset_to_safe=False,
            )
            results.append(result)
            if stop_on_error and result.get("cook_status") != "cook_ok":
                break
    finally:
        if reset_to_safe:
            deactivate_agent_clip_tests(parent_path=parent_path, node_name=node_name)
    return results


def cook_clip_locomotion_test(
    clip_name: str,
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    reset_to_safe: bool = True,
) -> dict[str, Any]:
    """Cook TEST_CLIP_LOCOMOTION from exactly one TEST_AGENTCLIP_* node."""

    import hou

    requested = str(clip_name or "").strip()
    spec = _agent_clip_test_spec(requested)
    result: dict[str, Any] = {
        "clip": requested,
        "agent_clip_node": spec["node"],
        "clip_locomotion_node": "TEST_CLIP_LOCOMOTION",
        "cook_status": "not_started",
        "agent_clip_point_count": 0,
        "input_point_count": 0,
        "point_count": 0,
        "input_connected": 0,
        "error": "",
    }

    try:
        activate_agent_clip_test(requested, parent_path=parent_path, node_name=node_name)
        experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
        agent_node = experiment.node(spec["node"])
        locomotion = experiment.node("TEST_CLIP_LOCOMOTION")
        if agent_node is None:
            result["cook_status"] = "missing_agent_clip_node"
            result["error"] = "{} was not found.".format(spec["node"])
            return result
        if locomotion is None:
            result["cook_status"] = "missing_clip_locomotion_node"
            result["error"] = "TEST_CLIP_LOCOMOTION was not found in this Houdini build."
            return result

        _safe_bypass(agent_node, False)
        _safe_bypass(locomotion, True)
        try:
            locomotion.setInput(0, agent_node)
            result["input_connected"] = 1
        except Exception as exc:
            result["cook_status"] = "clip_locomotion_input_error"
            result["error"] = str(exc)
            return result

        try:
            agent_node.cook(force=True)
            agent_geo = agent_node.geometry()
            result["agent_clip_point_count"] = len(agent_geo.points()) if agent_geo is not None else 0
            input_node = locomotion.input(0)
            input_geo = input_node.geometry() if input_node is not None else None
            result["input_point_count"] = len(input_geo.points()) if input_geo is not None else 0
            _safe_bypass(locomotion, False)
            locomotion.cook(force=True)
            geo = locomotion.geometry()
            result["point_count"] = len(geo.points()) if geo is not None else 0
            result["cook_status"] = "cook_ok"
        except Exception as exc:
            result["cook_status"] = "cook_error"
            result["error"] = str(exc)
        return result
    finally:
        if reset_to_safe:
            try:
                experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
                locomotion = experiment.node("TEST_CLIP_LOCOMOTION")
                if locomotion is not None:
                    _safe_bypass(locomotion, True)
            except Exception:
                pass
            deactivate_agent_clip_tests(parent_path=parent_path, node_name=node_name)


def run_clip_locomotion_cook_sequence(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    stop_on_error: bool = True,
    reset_to_safe: bool = True,
) -> list[dict[str, Any]]:
    """Cook TEST_CLIP_LOCOMOTION for walk, sit_down, and sit_idle one at a time."""

    results: list[dict[str, Any]] = []
    try:
        for spec in _agent_clip_test_specs():
            result = cook_clip_locomotion_test(
                spec["clip"],
                parent_path=parent_path,
                node_name=node_name,
                reset_to_safe=False,
            )
            results.append(result)
            if stop_on_error and result.get("cook_status") != "cook_ok":
                break
    finally:
        if reset_to_safe:
            try:
                import hou

                experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
                locomotion = experiment.node("TEST_CLIP_LOCOMOTION")
                if locomotion is not None:
                    _safe_bypass(locomotion, True)
            except Exception:
                pass
            deactivate_agent_clip_tests(parent_path=parent_path, node_name=node_name)
    return results


def cook_crowd_source_test(
    clip_name: str,
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    reset_to_safe: bool = True,
) -> dict[str, Any]:
    """Cook TEST_CROWD_SOURCE from TEST_CLIP_LOCOMOTION for one clip."""

    import hou

    requested = str(clip_name or "").strip()
    spec = _agent_clip_test_spec(requested)
    result: dict[str, Any] = {
        "clip": requested,
        "agent_clip_node": spec["node"],
        "clip_locomotion_node": "TEST_CLIP_LOCOMOTION",
        "crowd_source_node": "TEST_CROWD_SOURCE",
        "clip_locomotion_status": "not_started",
        "clip_locomotion_point_count": 0,
        "input_point_count": 0,
        "point_count": 0,
        "input_connected": 0,
        "agent_count_parameters": "",
        "cook_status": "not_started",
        "error": "",
    }

    try:
        locomotion_result = cook_clip_locomotion_test(
            requested,
            parent_path=parent_path,
            node_name=node_name,
            reset_to_safe=False,
        )
        result["clip_locomotion_status"] = locomotion_result.get("cook_status", "")
        result["clip_locomotion_point_count"] = locomotion_result.get("point_count", 0)
        if locomotion_result.get("cook_status") != "cook_ok":
            result["cook_status"] = "blocked_by_clip_locomotion"
            result["error"] = locomotion_result.get("error", "")
            return result

        experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
        locomotion = experiment.node("TEST_CLIP_LOCOMOTION")
        source = experiment.node("TEST_CROWD_SOURCE")
        if locomotion is None:
            result["cook_status"] = "missing_clip_locomotion_node"
            result["error"] = "TEST_CLIP_LOCOMOTION was not found in this Houdini build."
            return result
        if source is None:
            result["cook_status"] = "missing_crowd_source_node"
            result["error"] = "TEST_CROWD_SOURCE was not found in this Houdini build."
            return result

        _safe_bypass(locomotion, False)
        _safe_bypass(source, True)
        result["agent_count_parameters"] = ", ".join(_set_crowd_source_agent_count(source, 1))
        try:
            source.setInput(0, locomotion)
            result["input_connected"] = 1
        except Exception as exc:
            result["cook_status"] = "crowd_source_input_error"
            result["error"] = str(exc)
            return result

        try:
            input_node = source.input(0)
            input_geo = input_node.geometry() if input_node is not None else None
            result["input_point_count"] = len(input_geo.points()) if input_geo is not None else 0
            _safe_bypass(source, False)
            source.cook(force=True)
            geo = source.geometry()
            result["point_count"] = len(geo.points()) if geo is not None else 0
            result["cook_status"] = "cook_ok"
        except Exception as exc:
            result["cook_status"] = "cook_error"
            result["error"] = str(exc)
        return result
    finally:
        if reset_to_safe:
            try:
                experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
                source = experiment.node("TEST_CROWD_SOURCE")
                locomotion = experiment.node("TEST_CLIP_LOCOMOTION")
                if source is not None:
                    _safe_bypass(source, True)
                if locomotion is not None:
                    _safe_bypass(locomotion, True)
            except Exception:
                pass
            deactivate_agent_clip_tests(parent_path=parent_path, node_name=node_name)


def run_crowd_source_cook_sequence(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    stop_on_error: bool = True,
    reset_to_safe: bool = True,
) -> list[dict[str, Any]]:
    """Cook TEST_CROWD_SOURCE for walk, sit_down, and sit_idle one at a time."""

    results: list[dict[str, Any]] = []
    try:
        for spec in _agent_clip_test_specs():
            result = cook_crowd_source_test(
                spec["clip"],
                parent_path=parent_path,
                node_name=node_name,
                reset_to_safe=False,
            )
            results.append(result)
            if stop_on_error and result.get("cook_status") != "cook_ok":
                break
    finally:
        if reset_to_safe:
            try:
                import hou

                experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
                source = experiment.node("TEST_CROWD_SOURCE")
                locomotion = experiment.node("TEST_CLIP_LOCOMOTION")
                if source is not None:
                    _safe_bypass(source, True)
                if locomotion is not None:
                    _safe_bypass(locomotion, True)
            except Exception:
                pass
            deactivate_agent_clip_tests(parent_path=parent_path, node_name=node_name)
    return results


def probe_crowd_solver_connection(
    clip_name: str,
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    reset_to_safe: bool = True,
) -> dict[str, Any]:
    """Wire TEST_CROWD_SOLVER from TEST_CROWD_SOURCE without cooking the solver."""

    import hou

    requested = str(clip_name or "").strip()
    spec = _agent_clip_test_spec(requested)
    result: dict[str, Any] = {
        "clip": requested,
        "agent_clip_node": spec["node"],
        "clip_locomotion_node": "TEST_CLIP_LOCOMOTION",
        "crowd_source_node": "TEST_CROWD_SOURCE",
        "crowd_solver_node": "TEST_CROWD_SOLVER",
        "crowd_source_status": "not_started",
        "crowd_source_point_count": 0,
        "solver_input_connected": 0,
        "solver_input_point_count": 0,
        "solver_bypassed": 0,
        "crowd_solver_status": "not_started",
        "crowd_solver_node_type": "",
        "sop_crowd_solver_types": "",
        "dop_crowd_solver_types": "",
        "probe_status": "not_started",
        "error": "",
    }

    try:
        source_result = cook_crowd_source_test(
            requested,
            parent_path=parent_path,
            node_name=node_name,
            reset_to_safe=False,
        )
        result["crowd_source_status"] = source_result.get("cook_status", "")
        result["crowd_source_point_count"] = source_result.get("point_count", 0)
        if source_result.get("cook_status") != "cook_ok":
            result["probe_status"] = "blocked_by_crowd_source"
            result["error"] = source_result.get("error", "")
            return result

        experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
        source = experiment.node("TEST_CROWD_SOURCE")
        ensure_solver = ensure_crowd_solver_test_node(parent_path=parent_path, node_name=node_name)
        result["crowd_solver_status"] = ensure_solver.get("status", "")
        result["crowd_solver_node_type"] = ensure_solver.get("node_type", "")
        result["sop_crowd_solver_types"] = ensure_solver.get("sop_crowd_solver_types", "")
        result["dop_crowd_solver_types"] = ensure_solver.get("dop_crowd_solver_types", "")
        solver = experiment.node("TEST_CROWD_SOLVER")
        if source is None:
            result["probe_status"] = "missing_crowd_source_node"
            result["error"] = "TEST_CROWD_SOURCE was not found in this Houdini build."
            return result
        if solver is None:
            result["probe_status"] = ensure_solver.get("status") or "missing_crowd_solver_node"
            result["error"] = ensure_solver.get("error") or "TEST_CROWD_SOLVER was not found in this Houdini build."
            return result

        _safe_bypass(solver, True)
        try:
            solver.setInput(0, source)
            result["solver_input_connected"] = 1
        except Exception as exc:
            result["probe_status"] = "crowd_solver_input_error"
            result["error"] = str(exc)
            return result

        try:
            source.cook(force=True)
            input_node = solver.input(0)
            input_geo = input_node.geometry() if input_node is not None else None
            result["solver_input_point_count"] = len(input_geo.points()) if input_geo is not None else 0
            result["solver_bypassed"] = int(_node_is_bypassed(solver))
            result["probe_status"] = "ready_for_solver_cook"
        except Exception as exc:
            result["probe_status"] = "crowd_solver_probe_error"
            result["error"] = str(exc)
        return result
    finally:
        if reset_to_safe:
            try:
                experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
                solver = experiment.node("TEST_CROWD_SOLVER")
                source = experiment.node("TEST_CROWD_SOURCE")
                locomotion = experiment.node("TEST_CLIP_LOCOMOTION")
                if solver is not None:
                    _safe_bypass(solver, True)
                if source is not None:
                    _safe_bypass(source, True)
                if locomotion is not None:
                    _safe_bypass(locomotion, True)
            except Exception:
                pass
            deactivate_agent_clip_tests(parent_path=parent_path, node_name=node_name)


def run_crowd_solver_connection_sequence(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    stop_on_error: bool = True,
    reset_to_safe: bool = True,
) -> list[dict[str, Any]]:
    """Probe TEST_CROWD_SOLVER input wiring for each clip without cooking it."""

    results: list[dict[str, Any]] = []
    try:
        for spec in _agent_clip_test_specs():
            result = probe_crowd_solver_connection(
                spec["clip"],
                parent_path=parent_path,
                node_name=node_name,
                reset_to_safe=False,
            )
            results.append(result)
            if stop_on_error and result.get("probe_status") != "ready_for_solver_cook":
                break
    finally:
        if reset_to_safe:
            try:
                import hou

                experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
                solver = experiment.node("TEST_CROWD_SOLVER")
                source = experiment.node("TEST_CROWD_SOURCE")
                locomotion = experiment.node("TEST_CLIP_LOCOMOTION")
                if solver is not None:
                    _safe_bypass(solver, True)
                if source is not None:
                    _safe_bypass(source, True)
                if locomotion is not None:
                    _safe_bypass(locomotion, True)
            except Exception:
                pass
            deactivate_agent_clip_tests(parent_path=parent_path, node_name=node_name)
    return results


def ensure_crowd_solver_dop_bridge(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
) -> dict[str, Any]:
    """Create a DOP Network scaffold when Crowd Solver exists only as a DOP."""

    import hou

    result: dict[str, Any] = {
        "status": "not_started",
        "dop_network": "crowd_solver_dop_bridge",
        "dop_network_path": "",
        "dop_network_node_type": "",
        "dop_network_parent": "",
        "dop_crowd_solver": "DOP_CROWD_SOLVER",
        "dop_crowd_solver_path": "",
        "dop_crowd_solver_node_type": "",
        "dop_crowd_solver_types": "",
        "error": "",
    }
    root = _prototype_root_node(hou, parent_path=parent_path, node_name=node_name)
    node_types = probe_crowd_node_types(parent_path=parent_path, node_name=node_name)
    result["dop_crowd_solver_types"] = node_types.get("dop_crowd_solver_types", "")

    parent = root
    dopnet_type = _available_type_name(hou, parent, ("dopnet", "dopnet::2.0"))
    if not dopnet_type:
        fallback_parent = hou.node(parent_path)
        if fallback_parent is not None:
            fallback_type = _available_type_name(hou, fallback_parent, ("dopnet", "dopnet::2.0"))
            if fallback_type:
                parent = fallback_parent
                dopnet_type = fallback_type
    if not dopnet_type:
        result["status"] = "missing_dop_network_node_type"
        result["error"] = "No DOP Network object node type was found."
        return result

    bridge = parent.node(result["dop_network"])
    if bridge is None:
        bridge = _create_named_node_safely(parent, dopnet_type, result["dop_network"])
        if bridge is None:
            result["status"] = "dop_network_create_failed"
            result["dop_network_node_type"] = dopnet_type
            result["error"] = f"Could not create DOP Network with node type: {dopnet_type}"
            return result
    result["dop_network_path"] = bridge.path()
    result["dop_network_node_type"] = bridge.type().name() if bridge.type() is not None else dopnet_type
    result["dop_network_parent"] = parent.path()

    solver = bridge.node(result["dop_crowd_solver"])
    solver_type = _available_dop_crowd_solver_type_name(hou, bridge)
    if solver is None:
        if not solver_type:
            result["status"] = "missing_dop_crowd_solver_node_type"
            result["error"] = "No DOP Crowd Solver node type was found inside the DOP Network."
            return result
        solver = _create_named_node_safely(bridge, solver_type, result["dop_crowd_solver"])
        if solver is None:
            result["status"] = "dop_crowd_solver_create_failed"
            result["dop_crowd_solver_node_type"] = solver_type
            result["error"] = f"Could not create DOP_CROWD_SOLVER with node type: {solver_type}"
            return result
    _set_dop_solver_safe_state(solver, True)
    result["dop_crowd_solver_path"] = solver.path()
    result["dop_crowd_solver_node_type"] = solver.type().name() if solver.type() is not None else solver_type
    result["status"] = "ready_dop_crowd_solver_bridge"

    _add_or_update_note(
        bridge,
        "dop_crowd_solver_bridge_note",
        "Smart Crowd DOP bridge scaffold.\n\n"
        "DOP_CROWD_SOLVER is created for the Houdini Crowd Solver DOP path.\n"
        "This bridge is not cooked by the Python validation helpers.\n"
        "Next step: bind SOP TEST_CROWD_SOURCE output into the DOP crowd object/source setup.",
    )
    try:
        bridge.layoutChildren()
        parent.layoutChildren()
    except Exception:
        pass
    return result


def probe_crowd_dop_bridge_connection(
    clip_name: str,
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    reset_to_safe: bool = True,
) -> dict[str, Any]:
    """Validate the Crowd Source -> DOP bridge handoff without cooking DOPs."""

    import hou

    requested = str(clip_name or "").strip()
    spec = _agent_clip_test_spec(requested)
    result: dict[str, Any] = {
        "clip": requested,
        "agent_clip_node": spec["node"],
        "crowd_source_status": "not_started",
        "crowd_source_point_count": 0,
        "source_sop_path": "",
        "dop_bridge_status": "not_started",
        "dop_network_path": "",
        "dop_crowd_solver_path": "",
        "dop_crowd_solver_node_type": "",
        "probe_status": "not_started",
        "error": "",
    }

    try:
        source_result = cook_crowd_source_test(
            requested,
            parent_path=parent_path,
            node_name=node_name,
            reset_to_safe=False,
        )
        result["crowd_source_status"] = source_result.get("cook_status", "")
        result["crowd_source_point_count"] = source_result.get("point_count", 0)
        if source_result.get("cook_status") != "cook_ok":
            result["probe_status"] = "blocked_by_crowd_source"
            result["error"] = source_result.get("error", "")
            return result

        experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
        source = experiment.node("TEST_CROWD_SOURCE")
        if source is None:
            result["probe_status"] = "missing_crowd_source_node"
            result["error"] = "TEST_CROWD_SOURCE was not found in this Houdini build."
            return result
        result["source_sop_path"] = source.path()

        bridge = ensure_crowd_solver_dop_bridge(parent_path=parent_path, node_name=node_name)
        result["dop_bridge_status"] = bridge.get("status", "")
        result["dop_network_path"] = bridge.get("dop_network_path", "")
        result["dop_crowd_solver_path"] = bridge.get("dop_crowd_solver_path", "")
        result["dop_crowd_solver_node_type"] = bridge.get("dop_crowd_solver_node_type", "")
        if bridge.get("status") != "ready_dop_crowd_solver_bridge":
            result["probe_status"] = bridge.get("status") or "dop_bridge_not_ready"
            result["error"] = bridge.get("error", "")
            return result

        result["probe_status"] = "ready_for_dop_crowd_solver_network"
        return result
    finally:
        if reset_to_safe:
            try:
                experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
                source = experiment.node("TEST_CROWD_SOURCE")
                locomotion = experiment.node("TEST_CLIP_LOCOMOTION")
                if source is not None:
                    _safe_bypass(source, True)
                if locomotion is not None:
                    _safe_bypass(locomotion, True)
            except Exception:
                pass
            deactivate_agent_clip_tests(parent_path=parent_path, node_name=node_name)


def run_crowd_dop_bridge_connection_sequence(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    stop_on_error: bool = True,
    reset_to_safe: bool = True,
) -> list[dict[str, Any]]:
    """Probe the Crowd Source -> DOP bridge handoff for each clip."""

    results: list[dict[str, Any]] = []
    try:
        for spec in _agent_clip_test_specs():
            result = probe_crowd_dop_bridge_connection(
                spec["clip"],
                parent_path=parent_path,
                node_name=node_name,
                reset_to_safe=False,
            )
            results.append(result)
            if stop_on_error and result.get("probe_status") != "ready_for_dop_crowd_solver_network":
                break
    finally:
        if reset_to_safe:
            try:
                import hou

                experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
                source = experiment.node("TEST_CROWD_SOURCE")
                locomotion = experiment.node("TEST_CLIP_LOCOMOTION")
                if source is not None:
                    _safe_bypass(source, True)
                if locomotion is not None:
                    _safe_bypass(locomotion, True)
            except Exception:
                pass
            deactivate_agent_clip_tests(parent_path=parent_path, node_name=node_name)
    return results


def probe_crowd_dop_node_types(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
) -> dict[str, Any]:
    """Report DOP node types needed for the Crowd Solver bridge scaffold."""

    import hou

    bridge = ensure_crowd_solver_dop_bridge(parent_path=parent_path, node_name=node_name)
    dop_network = hou.node(bridge.get("dop_network_path", ""))
    category = dop_network.childTypeCategory() if dop_network is not None else _hou_category(hou, "dopNodeTypeCategory")
    solver = _matching_node_type_names(category, ("crowd", "solver"))
    crowd_object = _matching_node_type_names(category, ("crowd", "object"))
    sop_geometry = _matching_node_type_names(category, ("sop", "geometry"))
    merge = _matching_node_type_names(category, ("merge",))
    all_crowd = _matching_node_type_names(category, ("crowd",))
    missing = []
    if not solver:
        missing.append("crowd_solver")
    if not crowd_object:
        missing.append("crowd_object")
    if not sop_geometry:
        missing.append("sop_geometry")
    return {
        "dop_network_path": bridge.get("dop_network_path", ""),
        "dop_crowd_solver_types": ", ".join(solver) or "none",
        "dop_crowd_object_types": ", ".join(crowd_object) or "none",
        "dop_sop_geometry_types": ", ".join(sop_geometry) or "none",
        "dop_merge_types": ", ".join(merge) or "none",
        "all_crowd_dop_types": ", ".join(all_crowd) or "none",
        "missing_required_dop_nodes": ", ".join(missing) or "none",
        "source_scaffold_hint": "ready_for_dop_source_scaffold" if not missing else "missing_dop_source_scaffold_nodes",
    }


def ensure_crowd_dop_source_scaffold(
    clip_name: str = "walk",
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    reset_to_safe: bool = True,
) -> dict[str, Any]:
    """Create a non-cooked DOP source scaffold from TEST_CROWD_SOURCE."""

    import hou

    requested = str(clip_name or "").strip() or "walk"
    spec = _agent_clip_test_spec(requested)
    result: dict[str, Any] = {
        "clip": requested,
        "agent_clip_node": spec["node"],
        "status": "not_started",
        "crowd_source_status": "not_started",
        "crowd_source_point_count": 0,
        "source_sop_path": "",
        "dop_network_path": "",
        "dop_crowd_object_path": "",
        "dop_crowd_object_node_type": "",
        "dop_source_geometry_path": "",
        "dop_source_geometry_node_type": "",
        "dop_merge_path": "",
        "dop_merge_node_type": "",
        "dop_safe_input_path": "",
        "dop_safe_input_node_type": "",
        "dop_crowd_solver_path": "",
        "dop_crowd_solver_node_type": "",
        "source_path_parameters": "",
        "wire_summary": "",
        "missing_required_dop_nodes": "",
        "error": "",
    }

    try:
        source_result = cook_crowd_source_test(
            requested,
            parent_path=parent_path,
            node_name=node_name,
            reset_to_safe=False,
        )
        result["crowd_source_status"] = source_result.get("cook_status", "")
        result["crowd_source_point_count"] = source_result.get("point_count", 0)
        if source_result.get("cook_status") != "cook_ok":
            result["status"] = "blocked_by_crowd_source"
            result["error"] = source_result.get("error", "")
            return result

        experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
        source = experiment.node("TEST_CROWD_SOURCE")
        if source is None:
            result["status"] = "missing_crowd_source_node"
            result["error"] = "TEST_CROWD_SOURCE was not found in this Houdini build."
            return result
        result["source_sop_path"] = source.path()
        empty_source = _ensure_empty_crowd_source_sop(hou, experiment)

        bridge = ensure_crowd_solver_dop_bridge(parent_path=parent_path, node_name=node_name)
        if bridge.get("status") != "ready_dop_crowd_solver_bridge":
            result["status"] = bridge.get("status") or "dop_bridge_not_ready"
            result["error"] = bridge.get("error", "")
            return result

        dop_network = hou.node(bridge.get("dop_network_path", ""))
        if dop_network is None:
            result["status"] = "missing_dop_network"
            result["error"] = "The DOP bridge network was not found after creation."
            return result
        result["dop_network_path"] = dop_network.path()

        crowd_object_type = _available_dop_crowd_object_type_name(hou, dop_network)
        sop_geometry_type = _available_dop_sop_geometry_type_name(hou, dop_network)
        merge_type = _available_dop_merge_type_name(hou, dop_network)
        missing = []
        if not crowd_object_type:
            missing.append("crowd_object")
        if not sop_geometry_type:
            missing.append("sop_geometry")
        result["missing_required_dop_nodes"] = ", ".join(missing) or "none"
        if missing:
            result["status"] = "missing_dop_source_scaffold_node_type"
            result["error"] = "Missing DOP node type(s): {}".format(", ".join(missing))
            return result

        crowd_object = _ensure_named_child(dop_network, crowd_object_type, "DOP_CROWD_OBJECT")
        source_geometry = _ensure_named_child(dop_network, sop_geometry_type, "DOP_SOURCE_GEOMETRY")
        merge = _ensure_named_child(dop_network, merge_type, "DOP_MERGE_INPUTS") if merge_type else None
        safe_input = _ensure_named_child(dop_network, merge_type, "DOP_SAFE_EMPTY") if merge_type else None
        solver = hou.node(bridge.get("dop_crowd_solver_path", ""))

        if crowd_object is not None:
            result["dop_crowd_object_path"] = crowd_object.path()
            result["dop_crowd_object_node_type"] = crowd_object.type().name() if crowd_object.type() is not None else crowd_object_type
            crowd_object.setUserData("smart_crowd_source_sop_path", source.path())
        if source_geometry is not None:
            result["dop_source_geometry_path"] = source_geometry.path()
            result["dop_source_geometry_node_type"] = source_geometry.type().name() if source_geometry.type() is not None else sop_geometry_type
            result["source_path_parameters"] = ", ".join(_set_sop_path_like_parms(source_geometry, source.path()))
            source_geometry.setUserData("smart_crowd_source_sop_path", source.path())
            if empty_source is not None:
                source_geometry.setUserData("smart_crowd_empty_source_sop_path", empty_source.path())
        if merge is not None:
            result["dop_merge_path"] = merge.path()
            result["dop_merge_node_type"] = merge.type().name() if merge.type() is not None else merge_type
        if safe_input is not None:
            result["dop_safe_input_path"] = safe_input.path()
            result["dop_safe_input_node_type"] = safe_input.type().name() if safe_input.type() is not None else merge_type
            for input_index in range(4):
                _safe_disconnect_input(safe_input, input_index)
        if solver is not None:
            result["dop_crowd_solver_path"] = solver.path()
            result["dop_crowd_solver_node_type"] = solver.type().name() if solver.type() is not None else ""

        wire_summary = []
        if merge is not None and crowd_object is not None:
            wire_summary.append("merge_input0_crowd_object={}".format(int(_safe_set_input(merge, 0, crowd_object))))
        if merge is not None and source_geometry is not None:
            wire_summary.append("merge_input1_source_geometry={}".format(int(_safe_set_input(merge, 1, source_geometry))))
        if solver is not None:
            solver_input = merge if merge is not None else crowd_object
            if solver_input is not None:
                wire_summary.append("solver_input0={}".format(int(_safe_set_input(solver, 0, solver_input))))
            _set_dop_solver_safe_state(solver, True)
        result["wire_summary"] = ", ".join(wire_summary) or "none"

        _add_or_update_note(
            dop_network,
            "dop_source_scaffold_note",
            "Smart Crowd DOP source scaffold.\n\n"
            f"Source SOP: {source.path()}\n"
            "DOP_SOURCE_GEOMETRY stores the SOP path for the cooked Crowd Source test output.\n"
            "DOP nodes are scaffolded only; validation helpers do not cook the DOP simulation.",
        )
        try:
            dop_network.layoutChildren()
        except Exception:
            pass
        result["status"] = "ready_dop_source_scaffold"
        return result
    finally:
        if reset_to_safe:
            try:
                experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
                source = experiment.node("TEST_CROWD_SOURCE")
                locomotion = experiment.node("TEST_CLIP_LOCOMOTION")
                if source is not None:
                    _safe_bypass(source, True)
                if locomotion is not None:
                    _safe_bypass(locomotion, True)
            except Exception:
                pass
            deactivate_agent_clip_tests(parent_path=parent_path, node_name=node_name)


def ensure_runtime_crowd_dop_source_scaffold(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    reset_to_safe: bool = True,
) -> dict[str, Any]:
    """Create a non-cooked DOP source scaffold from the runtime behavior driver."""

    import hou

    result: dict[str, Any] = {
        "status": "not_started",
        "source_mode": "runtime_behavior_driver",
        "runtime_source_node": "",
        "runtime_source_path": "",
        "runtime_source_point_count": 0,
        "runtime_ready_agent_count": 0,
        "runtime_agent_count": 0,
        "runtime_clip_summary": "",
        "runtime_state_summary": "",
        "dop_network_path": "",
        "dop_crowd_object_path": "",
        "dop_crowd_object_node_type": "",
        "dop_source_geometry_path": "",
        "dop_source_geometry_node_type": "",
        "dop_merge_path": "",
        "dop_merge_node_type": "",
        "dop_safe_input_path": "",
        "dop_safe_input_node_type": "",
        "dop_crowd_solver_path": "",
        "dop_crowd_solver_node_type": "",
        "source_path_parameters": "",
        "source_path_parameters_after_safe": "",
        "source_path_is_safe_after": 0,
        "empty_source_sop_path": "",
        "wire_summary": "",
        "missing_required_dop_nodes": "",
        "error": "",
    }

    root = _prototype_root_node(hou, parent_path=parent_path, node_name=node_name)
    source = _runtime_driver_source_node(root)
    if source is None:
        result["status"] = "missing_runtime_driver_source"
        result["error"] = "Runtime driver source was not found. Re-run update_single_agent_seat_prototype()."
        return result

    result["runtime_source_node"] = source.name()
    result["runtime_source_path"] = source.path()
    try:
        source.cook(force=True)
        source_geo = source.geometry()
        result["runtime_source_point_count"] = len(source_geo.points()) if source_geo is not None else 0
        globals_ = _global_attrib_values(source)
        result["runtime_ready_agent_count"] = int(globals_.get("ready_agent_count", 0) or 0)
        result["runtime_agent_count"] = int(globals_.get("agent_count", result["runtime_source_point_count"]) or 0)
        result["runtime_clip_summary"] = str(globals_.get("clip_summary", "") or "")
        result["runtime_state_summary"] = str(globals_.get("state_summary", "") or "")
    except Exception as exc:
        result["status"] = "runtime_driver_source_cook_error"
        result["error"] = str(exc)
        return result

    experiment = root.node("agent_clip_experiment")
    if experiment is None:
        result["status"] = "missing_agent_clip_experiment"
        result["error"] = "agent_clip_experiment was not found. Re-run update_single_agent_seat_prototype()."
        return result
    empty_source = _ensure_empty_crowd_source_sop(hou, experiment)

    bridge = ensure_crowd_solver_dop_bridge(parent_path=parent_path, node_name=node_name)
    if bridge.get("status") != "ready_dop_crowd_solver_bridge":
        result["status"] = bridge.get("status") or "dop_bridge_not_ready"
        result["error"] = bridge.get("error", "")
        return result

    dop_network = hou.node(bridge.get("dop_network_path", ""))
    if dop_network is None:
        result["status"] = "missing_dop_network"
        result["error"] = "The DOP bridge network was not found after creation."
        return result
    result["dop_network_path"] = dop_network.path()

    crowd_object_type = _available_dop_crowd_object_type_name(hou, dop_network)
    sop_geometry_type = _available_dop_sop_geometry_type_name(hou, dop_network)
    merge_type = _available_dop_merge_type_name(hou, dop_network)
    missing = []
    if not crowd_object_type:
        missing.append("crowd_object")
    if not sop_geometry_type:
        missing.append("sop_geometry")
    result["missing_required_dop_nodes"] = ", ".join(missing) or "none"
    if missing:
        result["status"] = "missing_dop_source_scaffold_node_type"
        result["error"] = "Missing DOP node type(s): {}".format(", ".join(missing))
        return result

    crowd_object = _ensure_named_child(dop_network, crowd_object_type, "DOP_CROWD_OBJECT")
    source_geometry = _ensure_named_child(dop_network, sop_geometry_type, "DOP_SOURCE_GEOMETRY")
    merge = _ensure_named_child(dop_network, merge_type, "DOP_MERGE_INPUTS") if merge_type else None
    safe_input = _ensure_named_child(dop_network, merge_type, "DOP_SAFE_EMPTY") if merge_type else None
    solver = hou.node(bridge.get("dop_crowd_solver_path", ""))

    if crowd_object is not None:
        result["dop_crowd_object_path"] = crowd_object.path()
        result["dop_crowd_object_node_type"] = _node_type_name(crowd_object)
        crowd_object.setUserData("smart_crowd_source_sop_path", source.path())
        crowd_object.setUserData("smart_crowd_source_mode", "runtime_behavior_driver")
    if source_geometry is not None:
        result["dop_source_geometry_path"] = source_geometry.path()
        result["dop_source_geometry_node_type"] = _node_type_name(source_geometry)
        result["source_path_parameters"] = ", ".join(_set_sop_path_like_parms(source_geometry, source.path()))
        source_geometry.setUserData("smart_crowd_source_sop_path", source.path())
        source_geometry.setUserData("smart_crowd_source_mode", "runtime_behavior_driver")
        if empty_source is not None:
            source_geometry.setUserData("smart_crowd_empty_source_sop_path", empty_source.path())
            result["empty_source_sop_path"] = empty_source.path()
    if merge is not None:
        result["dop_merge_path"] = merge.path()
        result["dop_merge_node_type"] = _node_type_name(merge)
    if safe_input is not None:
        result["dop_safe_input_path"] = safe_input.path()
        result["dop_safe_input_node_type"] = _node_type_name(safe_input)
        for input_index in range(4):
            _safe_disconnect_input(safe_input, input_index)
    if solver is not None:
        result["dop_crowd_solver_path"] = solver.path()
        result["dop_crowd_solver_node_type"] = _node_type_name(solver)

    wire_summary = []
    if merge is not None and crowd_object is not None:
        wire_summary.append("merge_input0_crowd_object={}".format(int(_safe_set_input(merge, 0, crowd_object))))
    if merge is not None and source_geometry is not None:
        wire_summary.append("merge_input1_runtime_source_geometry={}".format(int(_safe_set_input(merge, 1, source_geometry))))
    if solver is not None:
        solver_input = merge if merge is not None else crowd_object
        if solver_input is not None:
            wire_summary.append("solver_input0={}".format(int(_safe_set_input(solver, 0, solver_input))))
        if reset_to_safe:
            _set_dop_solver_safe_state(solver, True)
    result["wire_summary"] = ", ".join(wire_summary) or "none"

    if source_geometry is not None:
        values = _sop_path_like_parm_values(source_geometry)
        result["source_path_parameters_after_safe"] = ", ".join(f"{name}={value}" for name, value in values.items())
        result["source_path_is_safe_after"] = int(_source_geometry_uses_empty_source(source_geometry))

    _add_or_update_note(
        dop_network,
        "runtime_dop_source_scaffold_note",
        "Smart Crowd runtime DOP source scaffold.\n\n"
        f"Runtime Source SOP: {source.path()}\n"
        "This uses the Behavior System point driver as the DOP source candidate.\n"
        "The Solver remains gated by OUT_EMPTY_CROWD_SOURCE after preparation.",
    )
    try:
        dop_network.layoutChildren()
    except Exception:
        pass
    result["status"] = "ready_runtime_dop_source_scaffold"
    return result


def run_crowd_dop_source_scaffold_sequence(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    stop_on_error: bool = True,
    reset_to_safe: bool = True,
) -> list[dict[str, Any]]:
    """Create and validate the non-cooked DOP source scaffold for each clip."""

    results: list[dict[str, Any]] = []
    try:
        for spec in _agent_clip_test_specs():
            result = ensure_crowd_dop_source_scaffold(
                spec["clip"],
                parent_path=parent_path,
                node_name=node_name,
                reset_to_safe=False,
            )
            results.append(result)
            if stop_on_error and result.get("status") != "ready_dop_source_scaffold":
                break
    finally:
        if reset_to_safe:
            try:
                import hou

                experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
                source = experiment.node("TEST_CROWD_SOURCE")
                locomotion = experiment.node("TEST_CLIP_LOCOMOTION")
                if source is not None:
                    _safe_bypass(source, True)
                if locomotion is not None:
                    _safe_bypass(locomotion, True)
            except Exception:
                pass
            deactivate_agent_clip_tests(parent_path=parent_path, node_name=node_name)
    return results


def probe_crowd_dop_scaffold_state(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
) -> dict[str, Any]:
    """Inspect the DOP scaffold without cooking the DOP network."""

    import hou

    result: dict[str, Any] = {
        "status": "not_started",
        "dop_network_path": "",
        "dop_crowd_object_path": "",
        "dop_crowd_object_node_type": "",
        "dop_source_geometry_path": "",
        "dop_source_geometry_node_type": "",
        "dop_merge_path": "",
        "dop_merge_node_type": "",
        "dop_safe_input_path": "",
        "dop_safe_input_node_type": "",
        "dop_crowd_solver_path": "",
        "dop_crowd_solver_node_type": "",
        "source_sop_path": "",
        "source_path_parameters": "",
        "source_path_is_safe": 0,
        "empty_source_sop_path": "",
        "merge_input_summary": "",
        "solver_input_summary": "",
        "solver_input_is_safe": 0,
        "solver_bypassed": 0,
        "ready_for_solver_cook": 0,
        "error": "",
    }
    root = _prototype_root_node(hou, parent_path=parent_path, node_name=node_name)
    dop_network = root.node("crowd_solver_dop_bridge")
    if dop_network is None:
        parent = hou.node(parent_path)
        dop_network = parent.node("crowd_solver_dop_bridge") if parent is not None else None
    if dop_network is None:
        result["status"] = "missing_dop_network"
        result["error"] = "crowd_solver_dop_bridge was not found. Run ensure_crowd_solver_dop_bridge()."
        return result

    result["dop_network_path"] = dop_network.path()
    crowd_object = dop_network.node("DOP_CROWD_OBJECT")
    source_geometry = dop_network.node("DOP_SOURCE_GEOMETRY")
    merge = dop_network.node("DOP_MERGE_INPUTS")
    safe_input = dop_network.node("DOP_SAFE_EMPTY")
    solver = dop_network.node("DOP_CROWD_SOLVER")

    if crowd_object is not None:
        result["dop_crowd_object_path"] = crowd_object.path()
        result["dop_crowd_object_node_type"] = _node_type_name(crowd_object)
    if source_geometry is not None:
        result["dop_source_geometry_path"] = source_geometry.path()
        result["dop_source_geometry_node_type"] = _node_type_name(source_geometry)
        result["source_sop_path"] = source_geometry.userData("smart_crowd_source_sop_path") or ""
        result["empty_source_sop_path"] = source_geometry.userData("smart_crowd_empty_source_sop_path") or ""
        parm_values = _sop_path_like_parm_values(source_geometry)
        result["source_path_parameters"] = ", ".join(f"{name}={value}" for name, value in parm_values.items())
        result["source_path_is_safe"] = int(_source_geometry_uses_empty_source(source_geometry))
        if not result["source_sop_path"] and parm_values:
            result["source_sop_path"] = next(iter(parm_values.values()))
    if merge is not None:
        result["dop_merge_path"] = merge.path()
        result["dop_merge_node_type"] = _node_type_name(merge)
        result["merge_input_summary"] = _input_summary(merge)
    if safe_input is not None:
        result["dop_safe_input_path"] = safe_input.path()
        result["dop_safe_input_node_type"] = _node_type_name(safe_input)
    if solver is not None:
        result["dop_crowd_solver_path"] = solver.path()
        result["dop_crowd_solver_node_type"] = _node_type_name(solver)
        result["solver_input_summary"] = _input_summary(solver)
        result["solver_input_is_safe"] = int(_solver_input_is_safe(solver))
        result["solver_bypassed"] = int(_node_is_bypassed(solver))

    missing = []
    for label, node in (
        ("crowd_object", crowd_object),
        ("source_geometry", source_geometry),
        ("crowd_solver", solver),
    ):
        if node is None:
            missing.append(label)
    if merge is None:
        missing.append("merge")
    if safe_input is None:
        missing.append("safe_input")
    if not result["source_sop_path"]:
        missing.append("source_sop_path")
    if missing:
        result["status"] = "incomplete_dop_scaffold"
        result["error"] = "Missing DOP scaffold item(s): {}".format(", ".join(missing))
        return result

    result["ready_for_solver_cook"] = 1
    result["status"] = "ready_for_explicit_dop_solver_cook"
    return result


def cook_crowd_dop_solver_smoke_test(
    clip_name: str = "walk",
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    allow_solver_cook: bool = False,
    frame: int | None = None,
    reset_to_safe: bool = True,
) -> dict[str, Any]:
    """Run a guarded DOP Solver smoke test only when explicitly enabled."""

    import hou

    requested = str(clip_name or "").strip() or "walk"
    spec = _agent_clip_test_spec(requested)
    result: dict[str, Any] = {
        "clip": requested,
        "agent_clip_node": spec["node"],
        "prepare_status": "not_started",
        "scaffold_status": "not_started",
        "smoke_status": "not_started",
        "cook_attempted": 0,
        "allow_solver_cook": int(bool(allow_solver_cook)),
        "frame": frame if frame is not None else 0,
        "dop_network_path": "",
        "dop_crowd_solver_path": "",
        "solver_bypassed_before": 0,
        "solver_bypassed_after": 0,
        "solver_activation_before": -1,
        "solver_activation_after": -1,
        "solver_activation_parameters": "",
        "solver_input_summary_after": "",
        "solver_input_disconnected_after": 0,
        "solver_input_safe_after": 0,
        "solver_gate_summary_after": "",
        "solver_gate_safe_after": 0,
        "solver_source_sop_path_after": "",
        "solver_empty_source_sop_path": "",
        "solver_source_is_safe_after": 0,
        "solver_safe_input_path": "",
        "solver_safe_after": 0,
        "solver_reset_to_safe": 0,
        "solver_bypass_check": "",
        "error": "",
    }

    try:
        prepare = ensure_crowd_dop_source_scaffold(
            requested,
            parent_path=parent_path,
            node_name=node_name,
            reset_to_safe=False,
        )
        result["prepare_status"] = prepare.get("status", "")
        if prepare.get("status") != "ready_dop_source_scaffold":
            result["smoke_status"] = "blocked_by_dop_source_scaffold"
            result["error"] = prepare.get("error", "")
            return result

        state = probe_crowd_dop_scaffold_state(parent_path=parent_path, node_name=node_name)
        result["scaffold_status"] = state.get("status", "")
        result["dop_network_path"] = state.get("dop_network_path", "")
        result["dop_crowd_solver_path"] = state.get("dop_crowd_solver_path", "")
        result["solver_bypassed_before"] = state.get("solver_bypassed", 0)
        result["solver_activation_before"] = _first_dop_activation_value(hou.node(result["dop_crowd_solver_path"]))
        if state.get("status") != "ready_for_explicit_dop_solver_cook":
            result["smoke_status"] = "blocked_by_dop_scaffold_state"
            result["error"] = state.get("error", "")
            return result

        solver = hou.node(result["dop_crowd_solver_path"])
        if not allow_solver_cook:
            if solver is not None:
                result["solver_reset_to_safe"] = int(_set_dop_solver_safe_state(solver, True, disconnect_input=True))
                _update_solver_safety_result(result, solver)
                result["solver_bypass_check"] = _node_bypass_debug_summary(solver)
            else:
                result["solver_bypassed_after"] = result["solver_bypassed_before"]
            result["smoke_status"] = "ready_for_explicit_solver_cook"
            result["error"] = "Pass allow_solver_cook=True to cook the DOP Solver smoke test."
            return result

        dop_network = hou.node(result["dop_network_path"])
        if dop_network is None or solver is None:
            result["smoke_status"] = "missing_dop_smoke_test_node"
            result["error"] = "DOP network or DOP Crowd Solver was not found."
            return result

        if frame is not None:
            try:
                hou.setFrame(int(frame))
            except Exception:
                pass

        try:
            _set_dop_solver_safe_state(solver, False)
            result["cook_attempted"] = 1
            dop_network.cook(force=True)
            result["smoke_status"] = "cook_ok"
        except Exception as exc:
            result["smoke_status"] = "cook_error"
            result["error"] = str(exc)
        finally:
            if reset_to_safe:
                result["solver_reset_to_safe"] = int(_set_dop_solver_safe_state(solver, True, disconnect_input=True))
            _update_solver_safety_result(result, solver)
            result["solver_bypass_check"] = _node_bypass_debug_summary(solver)
        return result
    finally:
        if reset_to_safe:
            try:
                import hou

                root = _prototype_root_node(hou, parent_path=parent_path, node_name=node_name)
                dop_network = root.node("crowd_solver_dop_bridge")
                solver = dop_network.node("DOP_CROWD_SOLVER") if dop_network is not None else None
                if solver is not None:
                    result["solver_reset_to_safe"] = int(_set_dop_solver_safe_state(solver, True, disconnect_input=True))
                    _update_solver_safety_result(result, solver)
                    result["solver_bypass_check"] = _node_bypass_debug_summary(solver)
                experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
                source = experiment.node("TEST_CROWD_SOURCE")
                locomotion = experiment.node("TEST_CLIP_LOCOMOTION")
                if source is not None:
                    _safe_bypass(source, True)
                if locomotion is not None:
                    _safe_bypass(locomotion, True)
            except Exception:
                pass
            deactivate_agent_clip_tests(parent_path=parent_path, node_name=node_name)


def run_crowd_dop_solver_smoke_test_sequence(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    allow_solver_cook: bool = False,
    frame: int | None = None,
    stop_on_error: bool = True,
    reset_to_safe: bool = True,
) -> list[dict[str, Any]]:
    """Run guarded DOP Solver smoke tests for each clip."""

    results: list[dict[str, Any]] = []
    try:
        for spec in _agent_clip_test_specs():
            result = cook_crowd_dop_solver_smoke_test(
                spec["clip"],
                parent_path=parent_path,
                node_name=node_name,
                allow_solver_cook=allow_solver_cook,
                frame=frame,
                reset_to_safe=reset_to_safe,
            )
            results.append(result)
            expected = "cook_ok" if allow_solver_cook else "ready_for_explicit_solver_cook"
            if stop_on_error and result.get("smoke_status") != expected:
                break
    finally:
        if reset_to_safe:
            try:
                import hou

                root = _prototype_root_node(hou, parent_path=parent_path, node_name=node_name)
                dop_network = root.node("crowd_solver_dop_bridge")
                solver = dop_network.node("DOP_CROWD_SOLVER") if dop_network is not None else None
                if solver is not None:
                    _set_dop_solver_safe_state(solver, True, disconnect_input=True)
            except Exception:
                pass
            deactivate_agent_clip_tests(parent_path=parent_path, node_name=node_name)
    return results


def cook_runtime_crowd_dop_solver_smoke_test(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    allow_solver_cook: bool = False,
    frame: int | None = None,
    reset_to_safe: bool = True,
) -> dict[str, Any]:
    """Run a guarded DOP Solver smoke test from the runtime behavior driver."""

    import hou

    result: dict[str, Any] = {
        "source_mode": "runtime_behavior_driver",
        "prepare_status": "not_started",
        "scaffold_status": "not_started",
        "smoke_status": "not_started",
        "cook_attempted": 0,
        "allow_solver_cook": int(bool(allow_solver_cook)),
        "frame": frame if frame is not None else 0,
        "runtime_source_path": "",
        "runtime_source_point_count": 0,
        "runtime_ready_agent_count": 0,
        "runtime_agent_count": 0,
        "runtime_clip_summary": "",
        "runtime_state_summary": "",
        "dop_network_path": "",
        "dop_crowd_solver_path": "",
        "solver_bypassed_before": 0,
        "solver_bypassed_after": 0,
        "solver_activation_before": -1,
        "solver_activation_after": -1,
        "solver_activation_parameters": "",
        "solver_input_summary_after": "",
        "solver_input_disconnected_after": 0,
        "solver_input_safe_after": 0,
        "solver_gate_summary_after": "",
        "solver_gate_safe_after": 0,
        "solver_source_sop_path_after": "",
        "solver_empty_source_sop_path": "",
        "solver_source_is_safe_after": 0,
        "solver_safe_input_path": "",
        "solver_safe_after": 0,
        "solver_reset_to_safe": 0,
        "solver_bypass_check": "",
        "error": "",
    }

    try:
        prepare = ensure_runtime_crowd_dop_source_scaffold(
            parent_path=parent_path,
            node_name=node_name,
            reset_to_safe=True,
        )
        result["prepare_status"] = prepare.get("status", "")
        result["runtime_source_path"] = prepare.get("runtime_source_path", "")
        result["runtime_source_point_count"] = prepare.get("runtime_source_point_count", 0)
        result["runtime_ready_agent_count"] = prepare.get("runtime_ready_agent_count", 0)
        result["runtime_agent_count"] = prepare.get("runtime_agent_count", 0)
        result["runtime_clip_summary"] = prepare.get("runtime_clip_summary", "")
        result["runtime_state_summary"] = prepare.get("runtime_state_summary", "")
        if prepare.get("status") != "ready_runtime_dop_source_scaffold":
            result["smoke_status"] = "blocked_by_runtime_dop_source_scaffold"
            result["error"] = prepare.get("error", "")
            return result

        state = probe_crowd_dop_scaffold_state(parent_path=parent_path, node_name=node_name)
        result["scaffold_status"] = state.get("status", "")
        result["dop_network_path"] = state.get("dop_network_path", "")
        result["dop_crowd_solver_path"] = state.get("dop_crowd_solver_path", "")
        result["solver_bypassed_before"] = state.get("solver_bypassed", 0)
        result["solver_activation_before"] = _first_dop_activation_value(hou.node(result["dop_crowd_solver_path"]))
        if state.get("status") != "ready_for_explicit_dop_solver_cook":
            result["smoke_status"] = "blocked_by_dop_scaffold_state"
            result["error"] = state.get("error", "")
            return result

        solver = hou.node(result["dop_crowd_solver_path"])
        if not allow_solver_cook:
            if solver is not None:
                result["solver_reset_to_safe"] = int(_set_dop_solver_safe_state(solver, True, disconnect_input=True))
                _update_solver_safety_result(result, solver)
                result["solver_bypass_check"] = _node_bypass_debug_summary(solver)
            else:
                result["solver_bypassed_after"] = result["solver_bypassed_before"]
            result["smoke_status"] = "ready_for_explicit_runtime_solver_cook"
            result["error"] = "Pass allow_solver_cook=True to cook the runtime DOP Solver smoke test."
            return result

        dop_network = hou.node(result["dop_network_path"])
        if dop_network is None or solver is None:
            result["smoke_status"] = "missing_dop_smoke_test_node"
            result["error"] = "DOP network or DOP Crowd Solver was not found."
            return result

        if frame is not None:
            try:
                hou.setFrame(int(frame))
            except Exception:
                pass

        try:
            _set_dop_solver_safe_state(solver, False)
            result["cook_attempted"] = 1
            dop_network.cook(force=True)
            result["smoke_status"] = "cook_ok"
        except Exception as exc:
            result["smoke_status"] = "cook_error"
            result["error"] = str(exc)
        finally:
            if reset_to_safe:
                result["solver_reset_to_safe"] = int(_set_dop_solver_safe_state(solver, True, disconnect_input=True))
            _update_solver_safety_result(result, solver)
            result["solver_bypass_check"] = _node_bypass_debug_summary(solver)
        return result
    finally:
        if reset_to_safe:
            try:
                root = _prototype_root_node(hou, parent_path=parent_path, node_name=node_name)
                dop_network = root.node("crowd_solver_dop_bridge")
                solver = dop_network.node("DOP_CROWD_SOLVER") if dop_network is not None else None
                if solver is not None:
                    result["solver_reset_to_safe"] = int(_set_dop_solver_safe_state(solver, True, disconnect_input=True))
                    _update_solver_safety_result(result, solver)
                    result["solver_bypass_check"] = _node_bypass_debug_summary(solver)
            except Exception:
                pass
            deactivate_agent_clip_tests(parent_path=parent_path, node_name=node_name)


def ensure_runtime_dop_result_preview(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
) -> dict[str, Any]:
    """Create or refresh the displayable runtime DOP handoff preview node."""

    import hou

    result: dict[str, Any] = {
        "status": "not_started",
        "node_path": "",
        "point_count": 0,
        "driver_source": "",
        "source_mode": "",
        "runtime_source_sop_path": "",
        "current_source_path": "",
        "source_path_is_safe": 0,
        "empty_source_sop_path": "",
        "dop_network_path": "",
        "dop_solver_path": "",
        "solver_node_type": "",
        "agent_count": 0,
        "ready_agent_count": 0,
        "clip_summary": "",
        "state_summary": "",
        "error": "",
    }

    root = _prototype_root_node(hou, parent_path=parent_path, node_name=node_name)
    plan = _plan_from_root_user_data(root)
    _ensure_runtime_dop_result_preview_node(hou, root, plan)
    out = root.node("runtime_dop_result_preview/OUT_RUNTIME_DOP_RESULT")
    if out is None:
        result["status"] = "missing_runtime_dop_result_preview"
        result["error"] = "OUT_RUNTIME_DOP_RESULT was not created."
        return result
    result["node_path"] = out.path()
    try:
        out.cook(force=True)
        geo = out.geometry()
        result["point_count"] = len(geo.points()) if geo is not None else 0
        values = _global_attrib_values(out)
        for key in (
            "driver_source",
            "source_mode",
            "runtime_source_sop_path",
            "current_source_path",
            "empty_source_sop_path",
            "dop_network_path",
            "dop_solver_path",
            "solver_node_type",
            "clip_summary",
            "state_summary",
        ):
            result[key] = str(values.get(key, "") or "")
        for key in ("source_path_is_safe", "agent_count", "ready_agent_count"):
            result[key] = int(values.get(key, 0) or 0)
        result["status"] = "ready_runtime_dop_result_preview"
    except Exception as exc:
        result["status"] = "runtime_dop_result_preview_cook_error"
        result["error"] = str(exc)
    return result


def sample_runtime_dop_result_timeline(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    frames: list[int] | tuple[int, ...] | None = None,
    step: int = 24,
    end_frame: int = 480,
    allow_solver_cook: bool = False,
    reset_to_safe: bool = True,
) -> list[dict[str, Any]]:
    """Sample runtime behavior handoff state across frames.

    By default this only cooks the displayable runtime handoff preview. Passing
    allow_solver_cook=True performs the guarded one-frame DOP smoke cook at each
    sampled frame and returns the Solver to the safe empty source afterward.
    """

    import hou

    root = _prototype_root_node(hou, parent_path=parent_path, node_name=node_name)
    ensure_runtime_dop_result_preview(parent_path=parent_path, node_name=node_name)
    ensure_runtime_crowd_dop_source_scaffold(parent_path=parent_path, node_name=node_name, reset_to_safe=True)
    out = root.node("runtime_dop_result_preview/OUT_RUNTIME_DOP_RESULT")
    frame_values = _runtime_timeline_sample_frames(root, frames=frames, step=step, end_frame=end_frame)
    results: list[dict[str, Any]] = []
    for frame in frame_values:
        result: dict[str, Any] = {
            "frame": int(frame),
            "status": "not_started",
            "display_node": out.path() if out is not None else "",
            "point_count": 0,
            "agent_count": 0,
            "ready_agent_count": 0,
            "clip_summary": "",
            "state_summary": "",
            "agent_clip_summary": "",
            "agent_state_summary": "",
            "source_path_is_safe": 0,
            "current_source_path": "",
            "runtime_source_sop_path": "",
            "smoke_status": "not_requested",
            "cook_attempted": 0,
            "solver_source_is_safe_after": 0,
            "solver_safe_after": 0,
            "solver_reset_to_safe": 0,
            "error": "",
        }
        try:
            hou.setFrame(int(frame))
        except Exception:
            pass
        _cook_runtime_driver_chain(root)

        if allow_solver_cook:
            smoke = cook_runtime_crowd_dop_solver_smoke_test(
                parent_path=parent_path,
                node_name=node_name,
                allow_solver_cook=True,
                frame=int(frame),
                reset_to_safe=reset_to_safe,
            )
            result["smoke_status"] = smoke.get("smoke_status", "")
            result["cook_attempted"] = smoke.get("cook_attempted", 0)
            result["solver_source_is_safe_after"] = smoke.get("solver_source_is_safe_after", 0)
            result["solver_safe_after"] = smoke.get("solver_safe_after", 0)
            result["solver_reset_to_safe"] = smoke.get("solver_reset_to_safe", 0)
            if smoke.get("smoke_status") != "cook_ok":
                result["error"] = smoke.get("error", "")
        else:
            _set_runtime_dop_safe_state(root)

        if out is None:
            result["status"] = "missing_runtime_dop_result_preview"
            result["error"] = result["error"] or "OUT_RUNTIME_DOP_RESULT was not found."
            results.append(result)
            continue

        try:
            out.cook(force=True)
            geo = out.geometry()
            values = _global_attrib_values(out)
            result["point_count"] = len(geo.points()) if geo is not None else 0
            result["agent_count"] = int(values.get("agent_count", result["point_count"]) or 0)
            result["ready_agent_count"] = int(values.get("ready_agent_count", 0) or 0)
            result["clip_summary"] = str(values.get("clip_summary", "") or "")
            result["state_summary"] = str(values.get("state_summary", "") or "")
            result["source_path_is_safe"] = int(values.get("source_path_is_safe", 0) or 0)
            result["current_source_path"] = str(values.get("current_source_path", "") or "")
            result["runtime_source_sop_path"] = str(values.get("runtime_source_sop_path", "") or "")
            if geo is not None:
                agent_summary = _agent_point_timeline_summary(geo)
                result["agent_clip_summary"] = agent_summary["clips"]
                result["agent_state_summary"] = agent_summary["states"]
            result["status"] = "sample_ok"
        except Exception as exc:
            result["status"] = "sample_error"
            result["error"] = result["error"] or str(exc)
        results.append(result)
    return results


def ensure_runtime_timeline_sample_preview(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    frames: list[int] | tuple[int, ...] | None = None,
    step: int = 24,
    end_frame: int = 480,
    allow_solver_cook: bool = False,
) -> dict[str, Any]:
    """Create a displayable timeline sample node from runtime handoff rows."""

    import hou

    rows = sample_runtime_dop_result_timeline(
        parent_path=parent_path,
        node_name=node_name,
        frames=frames,
        step=step,
        end_frame=end_frame,
        allow_solver_cook=allow_solver_cook,
    )
    result: dict[str, Any] = {
        "status": "not_started",
        "node_path": "",
        "sample_count": len(rows),
        "agent_sample_count": 0,
        "allow_solver_cook": int(bool(allow_solver_cook)),
        "all_samples_ok": 0,
        "all_sources_safe": 0,
        "all_solver_samples_safe": 0,
        "observed_clips": "",
        "observed_states": "",
        "error": "",
    }

    root = _prototype_root_node(hou, parent_path=parent_path, node_name=node_name)
    parent = _child_or_create(root, "geo", "runtime_timeline_samples")
    _clear_children(parent)
    sampler = parent.createNode("python", "build_runtime_timeline_samples")
    sampler.parm("python").set(_runtime_timeline_sample_preview_python_sop(rows, allow_solver_cook=allow_solver_cook))
    out = parent.createNode("null", "OUT_RUNTIME_TIMELINE_SAMPLES")
    out.setInput(0, sampler)
    out.setDisplayFlag(True)
    out.setRenderFlag(True)
    _add_or_update_note(
        parent,
        "runtime_timeline_samples_note",
        "Runtime timeline sample preview.\n\n"
        "Display OUT_RUNTIME_TIMELINE_SAMPLES to inspect one point per sampled frame/agent.\n"
        "Refresh this node by re-running ensure_runtime_timeline_sample_preview().",
    )
    try:
        parent.layoutChildren()
        out.cook(force=True)
        values = _global_attrib_values(out)
        result["agent_sample_count"] = int(values.get("agent_sample_count", 0) or 0)
        result["all_samples_ok"] = int(values.get("all_samples_ok", 0) or 0)
        result["all_sources_safe"] = int(values.get("all_sources_safe", 0) or 0)
        result["all_solver_samples_safe"] = int(values.get("all_solver_samples_safe", 0) or 0)
        result["observed_clips"] = str(values.get("observed_clips", "") or "")
        result["observed_states"] = str(values.get("observed_states", "") or "")
        result["node_path"] = out.path()
        result["status"] = "ready_runtime_timeline_sample_preview"
    except Exception as exc:
        result["node_path"] = out.path() if out is not None else ""
        result["status"] = "runtime_timeline_sample_preview_cook_error"
        result["error"] = str(exc)
    return result


def apply_runtime_validation_short_distance(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    start_distance: float = 0.0,
    spawn_radius: float = 0.75,
    query_radius: float = 8.0,
) -> dict[str, Any]:
    """Apply a short-distance runtime preview override without changing YAML files."""

    import hou

    root = _prototype_root_node(hou, parent_path=parent_path, node_name=node_name)
    plan = _plan_from_root_user_data(root)
    if not plan:
        return {
            "status": "missing_smart_crowd_plan",
            "node": root.path(),
            "error": "smart_crowd_plan userData was not found. Re-run update_single_agent_seat_prototype().",
        }

    runtime = dict(plan.get("runtime") or {})
    runtime["validation_short_distance"] = 1
    runtime["validation_start_distance"] = max(0.0, float(start_distance))
    runtime["spawn_radius"] = max(0.0, float(spawn_radius))
    runtime["query_radius"] = max(float(query_radius), runtime["validation_start_distance"] + 2.0)
    plan["runtime"] = runtime
    root.setUserData("smart_crowd_plan", repr(plan))

    _ensure_runtime_agent_source(hou, root, plan)

    runtime_geo = _child_or_create(root, "geo", "runtime_behavior_preview")
    _clear_children(runtime_geo)
    _create_runtime_behavior_preview(hou, root, runtime_geo, plan)
    _ensure_runtime_behavior_driver(hou, root, plan)
    _ensure_crowd_clip_state_driver(hou, root, plan)
    _ensure_agent_clip_bridge(hou, root, plan)
    _ensure_runtime_kinefx_preview(hou, root, plan)
    _ensure_runtime_dop_result_preview_node(hou, root, plan)
    try:
        root.layoutChildren()
    except Exception:
        pass

    return {
        "status": "ready_runtime_validation_short_distance",
        "node": root.path(),
        "validation_short_distance": 1,
        "validation_start_distance": runtime["validation_start_distance"],
        "runtime_spawn_radius": runtime["spawn_radius"],
        "seat_query_radius": runtime["query_radius"],
        "runtime_agent_count": runtime.get("agent_count", 0),
        "next_step": "Run validate_runtime_seat_behavior_timeline(end_frame=480) again.",
        "error": "",
    }


def validate_runtime_seat_behavior_timeline(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
    frames: list[int] | tuple[int, ...] | None = None,
    step: int = 24,
    end_frame: int = 480,
    allow_solver_cook: bool = False,
) -> dict[str, Any]:
    """Validate that the runtime seat behavior reaches walk, sit_down, and sit_idle."""

    required_clips = ("walk", "sit_down", "sit_idle")
    rows = sample_runtime_dop_result_timeline(
        parent_path=parent_path,
        node_name=node_name,
        frames=frames,
        step=step,
        end_frame=end_frame,
        allow_solver_cook=allow_solver_cook,
    )
    observed_clips: list[str] = []
    observed_states: list[str] = []
    first_clip_frames: dict[str, int] = {}
    first_state_frames: dict[str, int] = {}
    sample_errors: list[str] = []
    all_samples_ok = bool(rows)
    all_sources_safe = bool(rows)
    all_solver_samples_safe = bool(rows)

    for row in rows:
        frame = int(row.get("frame", 0) or 0)
        status = str(row.get("status", "") or "")
        if status != "sample_ok":
            all_samples_ok = False
            error = str(row.get("error", "") or status or "sample_error")
            sample_errors.append(f"{frame}:{error}")
        if not int(row.get("source_path_is_safe", 0) or 0):
            all_sources_safe = False
        if allow_solver_cook:
            solver_ok = (
                int(row.get("solver_source_is_safe_after", 0) or 0)
                and int(row.get("solver_safe_after", 0) or 0)
                and int(row.get("solver_reset_to_safe", 0) or 0)
            )
            if not solver_ok:
                all_solver_samples_safe = False
        else:
            all_solver_samples_safe = True

        for clip in _summary_values(row.get("agent_clip_summary", "") or row.get("clip_summary", "")):
            if clip and clip != "none":
                if clip not in observed_clips:
                    observed_clips.append(clip)
                first_clip_frames.setdefault(clip, frame)
        for state in _summary_values(row.get("agent_state_summary", "") or row.get("state_summary", "")):
            if state and state != "unknown":
                if state not in observed_states:
                    observed_states.append(state)
                first_state_frames.setdefault(state, frame)

    missing_clips = [clip for clip in required_clips if clip not in observed_clips]
    if not rows:
        status = "no_runtime_timeline_samples"
    elif missing_clips:
        status = "missing_required_runtime_clips"
    elif not all_samples_ok:
        status = "runtime_timeline_sample_error"
    elif not all_sources_safe:
        status = "runtime_dop_source_not_safe"
    elif allow_solver_cook and not all_solver_samples_safe:
        status = "runtime_solver_not_safe_after_sample"
    else:
        status = "runtime_seat_behavior_validated"

    return {
        "status": status,
        "sample_count": len(rows),
        "allow_solver_cook": int(bool(allow_solver_cook)),
        "all_samples_ok": int(all_samples_ok),
        "all_sources_safe": int(all_sources_safe),
        "all_solver_samples_safe": int(all_solver_samples_safe),
        "required_clips": ", ".join(required_clips),
        "observed_clips": ", ".join(observed_clips) or "none",
        "missing_clips": ", ".join(missing_clips) or "none",
        "observed_states": ", ".join(observed_states) or "none",
        "first_clip_frames": ", ".join(f"{clip}:{first_clip_frames[clip]}" for clip in observed_clips) or "none",
        "first_state_frames": ", ".join(f"{state}:{first_state_frames[state]}" for state in observed_states) or "none",
        "sample_errors": ", ".join(sample_errors) or "",
    }


def ensure_crowd_solver_test_node(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
) -> dict[str, Any]:
    """Create TEST_CROWD_SOLVER if this Houdini has a SOP Crowd Solver type."""

    import hou

    result: dict[str, Any] = {
        "status": "not_started",
        "node": "TEST_CROWD_SOLVER",
        "node_type": "",
        "sop_crowd_solver_types": "",
        "dop_crowd_solver_types": "",
        "error": "",
    }
    experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
    node_types = probe_crowd_node_types(parent_path=parent_path, node_name=node_name)
    result["sop_crowd_solver_types"] = node_types.get("sop_crowd_solver_types", "")
    result["dop_crowd_solver_types"] = node_types.get("dop_crowd_solver_types", "")

    existing = experiment.node("TEST_CROWD_SOLVER")
    if existing is not None:
        _safe_bypass(existing, True)
        result["status"] = "existing_crowd_solver_node"
        result["node_type"] = existing.type().name() if existing.type() is not None else ""
        return result

    type_name = _available_crowd_solver_type_name(hou, experiment)
    if not type_name:
        dop_types = result["dop_crowd_solver_types"]
        if dop_types and dop_types != "none":
            result["status"] = "crowd_solver_requires_dop_network"
            result["error"] = "No SOP Crowd Solver node type was found; this Houdini exposes Crowd Solver as a DOP node."
        else:
            result["status"] = "missing_crowd_solver_node_type"
            result["error"] = "No SOP or DOP Crowd Solver node type was found in this Houdini session."
        return result

    solver = _create_named_node_safely(experiment, type_name, "TEST_CROWD_SOLVER")
    if solver is None:
        result["status"] = "crowd_solver_create_failed"
        result["node_type"] = type_name
        result["error"] = f"Could not create TEST_CROWD_SOLVER with node type: {type_name}"
        return result

    _disconnect_input(solver, 0)
    _safe_bypass(solver, True)
    experiment.layoutChildren()
    result["status"] = "created_crowd_solver_node"
    result["node_type"] = type_name
    return result


def probe_crowd_node_types(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
) -> dict[str, Any]:
    """Report version-specific Crowd/Agent node type names visible to Houdini."""

    import hou

    try:
        experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
        sop_category = experiment.childTypeCategory()
    except Exception:
        sop_category = _hou_category(hou, "sopNodeTypeCategory")
    dop_category = _hou_category(hou, "dopNodeTypeCategory")

    sop_solver = _matching_node_type_names(sop_category, ("crowd", "solver"))
    dop_solver = _matching_node_type_names(dop_category, ("crowd", "solver"))
    sop_source = _matching_node_type_names(sop_category, ("crowd", "source"))
    sop_agentclip = _matching_node_type_names(sop_category, ("agent", "clip"))
    sop_locomotion = _matching_node_type_names(sop_category, ("clip", "locomotion"))
    return {
        "sop_crowd_solver_types": ", ".join(sop_solver) or "none",
        "dop_crowd_solver_types": ", ".join(dop_solver) or "none",
        "sop_crowd_source_types": ", ".join(sop_source) or "none",
        "sop_agent_clip_types": ", ".join(sop_agentclip) or "none",
        "sop_clip_locomotion_types": ", ".join(sop_locomotion) or "none",
        "solver_location_hint": _crowd_solver_location_hint(sop_solver, dop_solver),
    }


def refresh_agent_clip_unbypass_guard(
    *,
    parent_path: str = "/obj",
    node_name: str = "smart_crowd_seat_proto",
) -> dict[str, Any]:
    """Force the un-bypass guard to recook and return its detail attributes."""

    import hou

    experiment = _agent_clip_experiment_node(hou, parent_path=parent_path, node_name=node_name)
    guard = experiment.node("OUT_AGENT_CLIP_UNBYPASS_GUARD")
    if guard is None:
        raise RuntimeError("OUT_AGENT_CLIP_UNBYPASS_GUARD was not found. Re-run update_single_agent_seat_prototype().")
    source = guard.input(0)
    for node in (source, guard):
        if node is None:
            continue
        try:
            node.cook(force=True)
        except Exception:
            pass
    values = _global_attrib_values(guard)
    if values:
        return values
    return _global_attrib_values(source) if source is not None else {}


def _create_kinefx_imports(hou: Any, parent: Any, files: CrowdPrototypeFiles, plan: dict[str, Any]) -> None:
    character = _create_first_available(
        hou,
        parent,
        ("kinefx::fbxcharacterimport", "fbxcharacterimport"),
        "character_fbx",
    )
    if character is not None:
        _set_first_existing_parm(character, ("fbxfile", "file", "filepath"), _as_posix(files.character_fbx))
    else:
        _add_note(parent, "character_fbx", f"Import character FBX with KineFX:\n{_as_posix(files.character_fbx)}")

    clip_nodes = []
    for label, path in (
        ("walk", files.walk_fbx),
        ("sit_down", files.sit_down_fbx),
        ("sit_idle", files.sit_idle_fbx),
    ):
        node = _create_first_available(
            hou,
            parent,
            (
                "kinefx::fbxanimationimport",
                "fbxanimationimport",
                "kinefx::fbxanimimport",
                "fbxanimimport",
                "fbx_animation_import",
            ),
            f"{label}_fbx",
        )
        if node is None:
            node = _create_first_available(hou, parent, ("file",), f"{label}_fbx_file")
            if node is not None:
                _set_first_existing_parm(node, ("file", "fileName", "filepath"), _as_posix(path))
                _add_note(
                    parent,
                    f"{label}_fbx_note",
                    "KineFX FBX Animation Import node was not found in this Houdini build.\n"
                    f"A File SOP was created so the FBX path is still visible:\n{_as_posix(path)}",
                )
            else:
                _add_note(parent, f"{label}_fbx", f"Import animation FBX with KineFX:\n{_as_posix(path)}")
            continue
        _set_first_existing_parm(node, ("fbxfile", "file", "filepath"), _as_posix(path))
        if character is not None and node.inputConnectors():
            try:
                node.setInput(0, character)
            except hou.OperationFailed:
                pass
        node = _create_clip_time_shift(hou, parent, node, label, plan) or node
        clip_out = parent.createNode("null", f"OUT_{label.upper()}")
        clip_out.setInput(0, node)
        clip_nodes.append(clip_out)

    if clip_nodes:
        switch = parent.createNode("switch", "SWITCH_CLIP_BY_FRAME")
        for index, clip_node in enumerate(clip_nodes):
            switch.setInput(index, clip_node)
        _set_switch_frame_expression(hou, switch, plan)
        raw_out = parent.createNode("null", "OUT_KINEFX_CLIPS")
        raw_out.setInput(0, switch)

        behavior_transform = parent.createNode("python", "APPLY_BEHAVIOR_TRANSFORM")
        behavior_transform.setInput(0, raw_out)
        behavior_transform.parm("python").set(_apply_behavior_transform_python_sop(plan))

        out = parent.createNode("null", "OUT_AGENT_BEHAVIOR")
        out.setInput(0, behavior_transform)
        out.setDisplayFlag(True)
        out.setRenderFlag(True)
        _add_note(
            parent,
            "clip_switch_note",
            "Display OUT_AGENT_BEHAVIOR and play the timeline.\n"
            f"Frames {plan['locomotion']['walk_start_frame']}-{plan['locomotion']['walk_end_frame']}: walk toward approach_position\n"
            f"Frames {plan['locomotion']['walk_end_frame'] + 1}-{plan['locomotion']['align_end_frame']}: align in place toward seat\n"
            f"Frames {plan['locomotion']['align_end_frame'] + 1}-{plan['locomotion']['sit_down_end_frame']}: sit_down moves from approach to seat\n"
            f"Frames {plan['locomotion']['sit_idle_start_frame']}+: sit_idle at seat\n\n"
            f"Walk speed: {plan['locomotion']['walk_speed']:.3f} units/sec\n"
            f"Walk distance: {plan['locomotion']['walk_distance']:.3f} units\n\n"
            f"Walk clip cycles every {plan['locomotion']['walk_clip_frames']} frames.\n"
            "OUT_KINEFX_CLIPS shows the raw clip switch before behavior placement.",
        )
    _refresh_kinefx_fbx_imports_if_supported(parent, files)
    _ensure_kinefx_clip_diagnostic(hou, parent, files, plan)
    parent.layoutChildren()


def _ensure_agent_character_preview(hou: Any, root: Any, files: CrowdPrototypeFiles) -> None:
    parent = root.node("agent_crowd_pipeline")
    if parent is None:
        parent = root.createNode("geo", "agent_crowd_pipeline")
        _clear_children(parent)

    agent = parent.node("agent_definition")
    if agent is None:
        agent = _create_first_available(hou, parent, ("agent", "crowd::agent"), "agent_definition")

    if agent is None:
        _add_or_update_note(
            parent,
            "agent_character_preview_note",
            "Agent preview could not be created because this Houdini session did not expose an Agent SOP type.\n\n"
            "The KineFX skeleton preview remains available in kinefx_imports/OUT_RUNTIME_AGENT_BEHAVIOR.",
        )
        return

    agent_file_ready = _set_agent_character_file_if_supported(agent, _as_posix(files.character_fbx))
    _set_first_existing_parm(agent, ("agentname", "agent", "name"), "smart_crowd_character")
    mesh_import = _enable_agent_mesh_import_options_if_supported(agent)
    if mesh_import.get("status") == "enabled_mesh_import_options":
        _reload_agent_definition_if_supported(agent)
    _auto_select_agent_visual_layer_if_supported(agent)

    out = parent.node("OUT_AGENT_CHARACTER")
    if out is None:
        out = parent.createNode("null", "OUT_AGENT_CHARACTER")
    if agent_file_ready:
        _safe_bypass(agent, False)
        out.setInput(0, agent)
        note_text = (
            "Display OUT_AGENT_CHARACTER to inspect the character.fbx Agent definition.\n\n"
            "If it appears as only a skeleton, the FBX may not be importing a displayable mesh/shape layer into the Agent SOP.\n"
            "Display OUT_AGENT_CHARACTER_UNPACKED to inspect the shape geometry, or OUT_AGENT_CHARACTER_DIAGNOSTIC for shape/layer intrinsics."
        )
    else:
        _safe_bypass(agent, True)
        _disconnect_agent_clip_sources_from_invalid_agent(parent, agent)
        status = parent.node("agent_character_status")
        if status is None:
            status = parent.createNode("python", "agent_character_status")
        parm = status.parm("python")
        if parm is not None:
            parm.set(
                _agent_character_status_python_sop(
                    files,
                    "agent_fbx_file_parameter_not_found",
                    "Agent SOP did not expose a safe FBX file parameter. The invalid source=.fbx setting was cleared and the Agent SOP was bypassed.",
                )
            )
        out.setInput(0, status)
        note_text = (
            "OUT_AGENT_CHARACTER is showing a safe diagnostic point because this Houdini Agent SOP did not expose a safe FBX file parameter.\n\n"
            "The previous error came from assigning character.fbx to an Agent SOP source/object parameter. That parameter expects a Houdini object path in this build.\n"
            "Use kinefx_imports/OUT_RUNTIME_AGENT_BEHAVIOR for the current character preview, or build a production Agent definition from a supported rig/shape layer setup.\n"
            "OUT_AGENT_CHARACTER_DIAGNOSTIC records the current Agent import status in Detail attributes. OUT_AGENT_CHARACTER_UNPACKED is available when a valid Agent SOP exists."
        )
    try:
        out.setRenderFlag(True)
    except Exception:
        pass

    diagnostic = parent.node("inspect_agent_character_shapes")
    if diagnostic is None:
        diagnostic = parent.createNode("python", "inspect_agent_character_shapes")
    if agent_file_ready:
        diagnostic.setInput(0, agent)
    else:
        _disconnect_input(diagnostic, 0)
    parm = diagnostic.parm("python")
    if parm is not None:
        parm.set(_agent_character_diagnostic_python_sop(files))
    diagnostic_out = parent.node("OUT_AGENT_CHARACTER_DIAGNOSTIC")
    if diagnostic_out is None:
        diagnostic_out = parent.createNode("null", "OUT_AGENT_CHARACTER_DIAGNOSTIC")
    diagnostic_out.setInput(0, diagnostic)

    _ensure_agent_character_unpacked_preview(hou, parent, agent if agent_file_ready else None)
    _ensure_agent_definition_parameter_diagnostic(hou, parent, agent)

    _add_or_update_note(
        parent,
        "agent_character_preview_note",
        note_text,
    )
    parent.layoutChildren()


def _ensure_agent_character_unpacked_preview(hou: Any, parent: Any, agent: Any | None) -> None:
    out = parent.node("OUT_AGENT_CHARACTER_UNPACKED")
    if out is None:
        out = parent.createNode("null", "OUT_AGENT_CHARACTER_UNPACKED")

    if agent is None:
        _disconnect_input(out, 0)
        return

    unpack = parent.node("unpack_agent_character_shapes")
    if unpack is None:
        unpack = _create_first_available(
            hou,
            parent,
            (
                "agentunpack",
                "agentunpack::2.0",
                "crowd::agentunpack",
                "crowd::agentunpack::2.0",
                "unpack",
            ),
            "unpack_agent_character_shapes",
        )

    if unpack is not None:
        try:
            unpack.setInput(0, agent)
            out.setInput(0, unpack)
        except Exception:
            out.setInput(0, agent)
    else:
        out.setInput(0, agent)


def _ensure_agent_definition_parameter_diagnostic(hou: Any, parent: Any, agent: Any | None) -> None:
    diagnostic = parent.node("inspect_agent_definition_parameters")
    if diagnostic is None:
        diagnostic = parent.createNode("python", "inspect_agent_definition_parameters")
    parm = diagnostic.parm("python")
    if parm is not None:
        parm.set(_agent_definition_parameter_diagnostic_python_sop(agent.path() if agent is not None else ""))

    out = parent.node("OUT_AGENT_DEFINITION_PARAMETER_DIAGNOSTIC")
    if out is None:
        out = parent.createNode("null", "OUT_AGENT_DEFINITION_PARAMETER_DIAGNOSTIC")
    out.setInput(0, diagnostic)


def _ensure_agent_crowd_scaffold_nodes(hou: Any, root: Any, files: CrowdPrototypeFiles, plan: dict[str, Any]) -> None:
    parent = root.node("agent_crowd_pipeline")
    if parent is None:
        parent = root.createNode("geo", "agent_crowd_pipeline")
        _clear_children(parent)

    agent = parent.node("agent_definition")
    if agent is None:
        agent = _create_first_available(hou, parent, ("agent", "crowd::agent"), "agent_definition")

    agent_file_ready = False
    if agent is not None:
        agent_file_ready = _set_agent_character_file_if_supported(agent, _as_posix(files.character_fbx))
        _set_first_existing_parm(agent, ("agentname", "agent", "name"), "smart_crowd_character")
        mesh_import = _enable_agent_mesh_import_options_if_supported(agent)
        if mesh_import.get("status") == "enabled_mesh_import_options":
            _reload_agent_definition_if_supported(agent)
        _auto_select_agent_visual_layer_if_supported(agent)
        _safe_bypass(agent, not agent_file_ready)
        if not agent_file_ready:
            _disconnect_agent_clip_sources_from_invalid_agent(parent, agent)

    previous = agent if agent_file_ready else None
    clip_nodes = []
    for label, path in (
        ("walk", files.walk_fbx),
        ("sit_down", files.sit_down_fbx),
        ("sit_idle", files.sit_idle_fbx),
    ):
        clip = parent.node(f"agentclip_{label}")
        if clip is None:
            clip = _create_first_available(
                hou,
                parent,
                ("agentclip", "agentclip::2.0", "crowd::agentclip", "crowd::agentclip::2.0"),
                f"agentclip_{label}",
            )
        if clip is None:
            continue
        _set_first_existing_parm(clip, ("clipname", "clip", "name"), label)
        _set_first_existing_parm(clip, ("fbxfile", "file", "filepath", "source"), _as_posix(path))
        if previous is not None and clip.inputConnectors():
            try:
                clip.setInput(0, previous)
            except hou.OperationFailed:
                pass
        previous = clip
        clip_nodes.append(clip)

    locomotion = parent.node("clip_locomotion")
    if locomotion is None:
        locomotion = _create_first_available(
            hou,
            parent,
            (
                "agentcliplocomotion",
                "agentcliplocomotion::2.0",
                "clip_locomotion",
                "agentclipproperties",
                "agentclipproperties::2.0",
            ),
            "clip_locomotion",
        )
    if locomotion is not None and clip_nodes:
        try:
            locomotion.setInput(0, clip_nodes[-1])
        except hou.OperationFailed:
            pass

    crowd_source = parent.node("crowd_source_one_agent")
    if crowd_source is None:
        crowd_source = _create_first_available(
            hou,
            parent,
            ("crowdsource", "crowdsource::2.0", "crowdsource::3.0", "crowd::crowdsource"),
            "crowd_source_one_agent",
        )
    if crowd_source is not None:
        input_node = locomotion or (clip_nodes[-1] if clip_nodes else agent)
        if input_node is not None and crowd_source.inputConnectors():
            try:
                crowd_source.setInput(0, input_node)
            except hou.OperationFailed:
                pass
        _set_crowd_source_agent_count(crowd_source, int((plan.get("runtime") or {}).get("agent_count") or 1))

    out_source = crowd_source or locomotion or (clip_nodes[-1] if clip_nodes else agent)
    out = parent.node("OUT_AGENT_CROWD_SCAFFOLD")
    if out is None:
        out = parent.createNode("null", "OUT_AGENT_CROWD_SCAFFOLD")
    if out_source is not None:
        out.setInput(0, out_source)
        out.setDisplayFlag(True)
        try:
            out.setRenderFlag(True)
        except Exception:
            pass

    _add_or_update_note(
        parent,
        "agent_crowd_pipeline_note",
        "Agent/Crowd migration path:\n"
        "1. agent_definition imports character.fbx as a Houdini Agent primitive\n"
        "2. agentclip_walk / agentclip_sit_down / agentclip_sit_idle attach the FBX clips\n"
        "3. crowd_source_one_agent creates the crowd-side agent source\n"
        "4. OUT_AGENT_CROWD_BEHAVIOR transfers behavior clip/state/orient/v onto that Agent source\n\n"
        "KineFX preview remains available as a fallback, but the main verification target is now OUT_AGENT_CROWD_BEHAVIOR.\n"
        f"Computed walk speed: {plan['locomotion']['walk_speed']:.3f} units/sec\n"
        f"Computed walk frames: {plan['locomotion']['walk_frames']}",
    )
    parent.layoutChildren()


def _create_agent_crowd_scaffold(hou: Any, root: Any, files: CrowdPrototypeFiles, plan: dict[str, Any]) -> None:
    parent = root.createNode("geo", "agent_crowd_pipeline")
    _clear_children(parent)
    _ensure_agent_crowd_scaffold_nodes(hou, root, files, plan)


def _update_existing_kinefx_preview(hou: Any, parent: Any, files: CrowdPrototypeFiles, plan: dict[str, Any]) -> None:
    _refresh_kinefx_fbx_imports_if_supported(parent, files)

    switch = parent.node("SWITCH_CLIP_BY_FRAME")
    if switch is None:
        existing_outputs = [parent.node(f"OUT_{label.upper()}") for label in ("walk", "sit_down", "sit_idle")]
        if any(existing_outputs):
            switch = parent.createNode("switch", "SWITCH_CLIP_BY_FRAME")
            for index, output in enumerate(existing_outputs):
                if output is not None:
                    try:
                        switch.setInput(index, output)
                    except hou.OperationFailed:
                        pass
    if switch is not None:
        _set_switch_frame_expression(hou, switch, plan)

    for index, label in enumerate(("walk", "sit_down", "sit_idle")):
        time_shift = parent.node(f"TIME_{label.upper()}_CLIP")
        source = parent.node(f"OUT_{label.upper()}")
        if time_shift is None and source is not None:
            time_shift = _create_clip_time_shift(hou, parent, source, label, plan)
        elif time_shift is not None:
            _set_clip_time_expression(hou, time_shift, label, plan)
        if switch is not None and time_shift is not None:
            try:
                switch.setInput(index, time_shift)
            except hou.OperationFailed:
                pass

    transform = parent.node("APPLY_BEHAVIOR_TRANSFORM")
    raw_out = parent.node("OUT_KINEFX_CLIPS")
    if raw_out is None and switch is not None:
        raw_out = parent.createNode("null", "OUT_KINEFX_CLIPS")
        raw_out.setInput(0, switch)
    if transform is None and raw_out is not None:
        transform = parent.createNode("python", "APPLY_BEHAVIOR_TRANSFORM")
        transform.setInput(0, raw_out)
    if transform is not None:
        parm = transform.parm("python")
        if parm is not None:
            parm.set(_apply_behavior_transform_python_sop(plan))

    out = parent.node("OUT_AGENT_BEHAVIOR")
    if out is None and transform is not None:
        out = parent.createNode("null", "OUT_AGENT_BEHAVIOR")
        out.setInput(0, transform)
    if out is not None:
        out.setDisplayFlag(True)
        out.setRenderFlag(True)
    _ensure_kinefx_clip_diagnostic(hou, parent, files, plan)
    parent.layoutChildren()


def _create_runtime_behavior_preview(hou: Any, root: Any, parent: Any, plan: dict[str, Any]) -> None:
    agent_merge = _create_object_merge(
        hou,
        parent,
        "IN_AGENTS",
        f"{root.path()}/agent_crowd_pipeline/OUT_AGENT_SOURCE_POINTS",
    )
    seat_merge = _create_object_merge(
        hou,
        parent,
        "IN_SEATS",
        f"{root.path()}/interaction_points/OUT_SEAT_POINTS",
    )
    runtime_python = parent.createNode("python", "simulate_condition_based_behavior")
    if agent_merge is not None:
        runtime_python.setInput(0, agent_merge)
    if seat_merge is not None:
        runtime_python.setInput(1, seat_merge)
    runtime_python.parm("python").set(_runtime_behavior_python_sop(plan))
    runtime_null = parent.createNode("null", "OUT_RUNTIME_BEHAVIOR")
    runtime_null.setInput(0, runtime_python)
    runtime_null.setDisplayFlag(True)
    runtime_null.setRenderFlag(True)
    _add_note(
        parent,
        "runtime_behavior_note",
        "Runtime behavior preview now reads input geometry when available.\n\n"
        "Input 0: agent points from Crowd Source / agent_crowd_pipeline\n"
        "Input 1: interaction seat points\n\n"
        "If input 0 is empty, fallback agents are generated for preview only.",
    )
    parent.layoutChildren()


def _ensure_runtime_agent_source(hou: Any, root: Any, plan: dict[str, Any]) -> None:
    parent = root.node("agent_crowd_pipeline")
    if parent is None:
        parent = root.createNode("geo", "agent_crowd_pipeline")
        _clear_children(parent)
    source = parent.node("runtime_agent_source_points")
    if source is None:
        source = parent.createNode("python", "runtime_agent_source_points")
    parm = source.parm("python")
    if parm is not None:
        parm.set(_runtime_agent_source_python_sop(plan))
    out = parent.node("OUT_AGENT_SOURCE_POINTS")
    if out is None:
        out = parent.createNode("null", "OUT_AGENT_SOURCE_POINTS")
    out.setInput(0, source)
    out.setDisplayFlag(True)
    out.setRenderFlag(True)
    parent.layoutChildren()


def _ensure_runtime_behavior_driver(hou: Any, root: Any, plan: dict[str, Any]) -> None:
    parent = root.node("agent_crowd_pipeline")
    if parent is None:
        parent = root.createNode("geo", "agent_crowd_pipeline")
        _clear_children(parent)

    runtime_path = f"{root.path()}/runtime_behavior_preview/OUT_RUNTIME_BEHAVIOR"
    runtime_merge = parent.node("IN_RUNTIME_BEHAVIOR")
    if runtime_merge is None:
        runtime_merge = _create_object_merge(hou, parent, "IN_RUNTIME_BEHAVIOR", runtime_path)
    elif runtime_merge is not None:
        _set_first_existing_parm(runtime_merge, ("objpath1", "objpath", "path"), runtime_path)
        _set_first_existing_parm(runtime_merge, ("xformtype", "transform"), 1)

    driver = parent.node("build_behavior_agent_driver")
    if driver is None:
        driver = parent.createNode("python", "build_behavior_agent_driver")
    if runtime_merge is not None:
        driver.setInput(0, runtime_merge)
    parm = driver.parm("python")
    if parm is not None:
        parm.set(_behavior_agent_driver_python_sop(plan))

    out = parent.node("OUT_BEHAVIOR_AGENT_POINTS")
    if out is None:
        out = parent.createNode("null", "OUT_BEHAVIOR_AGENT_POINTS")
    out.setInput(0, driver)
    out.setDisplayFlag(True)
    out.setRenderFlag(True)

    note = _find_sticky_note(parent, "runtime_behavior_driver_note")
    text = (
        "Behavior driver output for the future Agent Clip path.\n\n"
        "IN_RUNTIME_BEHAVIOR reads runtime_behavior_preview/OUT_RUNTIME_BEHAVIOR.\n"
        "OUT_BEHAVIOR_AGENT_POINTS contains only agent points with normalized attrs:\n"
        "current_clip, clipname, agent_state, state, current_step, target_seat,\n"
        "target_position, heading, orient, agentid.\n\n"
        "Use this output as the behavior-side driver before wiring real Agent Clip/Crowd Solver nodes."
    )
    if note is None:
        _add_note(parent, "runtime_behavior_driver_note", text)
    else:
        note.setText(text)
    parent.layoutChildren()


def _ensure_crowd_clip_state_driver(hou: Any, root: Any, plan: dict[str, Any]) -> None:
    parent = root.node("agent_crowd_pipeline")
    if parent is None:
        parent = root.createNode("geo", "agent_crowd_pipeline")
        _clear_children(parent)

    behavior_path = f"{root.path()}/agent_crowd_pipeline/OUT_BEHAVIOR_AGENT_POINTS"
    behavior_merge = parent.node("IN_BEHAVIOR_AGENT_POINTS")
    if behavior_merge is None:
        behavior_merge = _create_object_merge(hou, parent, "IN_BEHAVIOR_AGENT_POINTS", behavior_path)
    else:
        _set_first_existing_parm(behavior_merge, ("objpath1", "objpath", "path"), behavior_path)
        _set_first_existing_parm(behavior_merge, ("xformtype", "transform"), 1)

    driver = parent.node("build_crowd_clip_state_driver")
    if driver is None:
        driver = parent.createNode("python", "build_crowd_clip_state_driver")
    if behavior_merge is not None:
        driver.setInput(0, behavior_merge)
    parm = driver.parm("python")
    if parm is not None:
        parm.set(_crowd_clip_state_driver_python_sop(plan))

    out = parent.node("OUT_CROWD_CLIP_STATE_DRIVER")
    if out is None:
        out = parent.createNode("null", "OUT_CROWD_CLIP_STATE_DRIVER")
    out.setInput(0, driver)
    out.setDisplayFlag(True)
    out.setRenderFlag(True)

    note = _find_sticky_note(parent, "crowd_clip_state_driver_note")
    text = (
        "Crowd clip/state driver output.\n\n"
        "OUT_CROWD_CLIP_STATE_DRIVER is the handoff point for Agent Clip/Crowd Solver wiring.\n"
        "It preserves behavior attributes and adds crowd-friendly aliases:\n"
        "clip, agentclip, agent_clip, crowd_state, clip_index, state_index, speed, v.\n\n"
        "This node remains point-based so it can be verified before real Agent Clip nodes are connected."
    )
    if note is None:
        _add_note(parent, "crowd_clip_state_driver_note", text)
    else:
        note.setText(text)
    parent.layoutChildren()


def _ensure_agent_clip_bridge(hou: Any, root: Any, plan: dict[str, Any]) -> None:
    parent = root.node("agent_crowd_pipeline")
    if parent is None:
        parent = root.createNode("geo", "agent_crowd_pipeline")
        _clear_children(parent)

    driver_path = f"{root.path()}/agent_crowd_pipeline/OUT_CROWD_CLIP_STATE_DRIVER"
    driver_merge = parent.node("IN_CROWD_DRIVER_FOR_AGENT_CLIPS")
    if driver_merge is None:
        driver_merge = _create_object_merge(hou, parent, "IN_CROWD_DRIVER_FOR_AGENT_CLIPS", driver_path)
    else:
        _set_first_existing_parm(driver_merge, ("objpath1", "objpath", "path"), driver_path)
        _set_first_existing_parm(driver_merge, ("xformtype", "transform"), 1)

    bridge = parent.node("apply_agent_clip_driver_attrs")
    if bridge is None:
        bridge = parent.createNode("python", "apply_agent_clip_driver_attrs")
    if driver_merge is not None:
        bridge.setInput(0, driver_merge)
    for input_index in range(1, 4):
        _disconnect_input(bridge, input_index)
    parm = bridge.parm("python")
    if parm is not None:
        parm.set(_agent_clip_bridge_python_sop(plan))

    out = parent.node("OUT_AGENT_CLIP_BRIDGE")
    if out is None:
        out = parent.createNode("null", "OUT_AGENT_CLIP_BRIDGE")
    out.setInput(0, bridge)
    out.setDisplayFlag(True)
    out.setRenderFlag(True)

    note = _find_sticky_note(parent, "agent_clip_bridge_note")
    text = (
        "Agent Clip bridge output.\n\n"
        "OUT_AGENT_CLIP_BRIDGE currently reads only OUT_CROWD_CLIP_STATE_DRIVER.\n"
        "It intentionally avoids the experimental Agent/Crowd scaffold so invalid agent_definition nodes cannot break the bridge.\n\n"
        "Use this verified point driver as the first safe handoff before wiring version-specific Agent Clip/Crowd Solver nodes."
    )
    if note is None:
        _add_note(parent, "agent_clip_bridge_note", text)
    else:
        note.setText(text)
    parent.layoutChildren()


def _ensure_agent_crowd_behavior_output(hou: Any, root: Any, plan: dict[str, Any]) -> None:
    parent = root.node("agent_crowd_pipeline")
    if parent is None:
        parent = root.createNode("geo", "agent_crowd_pipeline")
        _clear_children(parent)

    scaffold = parent.node("OUT_AGENT_CROWD_SCAFFOLD")
    driver = parent.node("OUT_CROWD_CLIP_STATE_DRIVER")

    behavior = parent.node("apply_agent_crowd_runtime_behavior")
    if behavior is None:
        behavior = parent.createNode("python", "apply_agent_crowd_runtime_behavior")
    if scaffold is not None:
        behavior.setInput(0, scaffold)
    else:
        _disconnect_input(behavior, 0)
    if driver is not None:
        behavior.setInput(1, driver)
    else:
        _disconnect_input(behavior, 1)
    parm = behavior.parm("python")
    if parm is not None:
        parm.set(_agent_crowd_behavior_python_sop(plan))

    out = parent.node("OUT_AGENT_CROWD_BEHAVIOR")
    if out is None:
        out = parent.createNode("null", "OUT_AGENT_CROWD_BEHAVIOR")
    out.setInput(0, behavior)
    out.setDisplayFlag(True)
    try:
        out.setRenderFlag(True)
    except Exception:
        pass

    _ensure_agent_crowd_behavior_unpacked_preview(hou, parent, out)
    _ensure_agent_crowd_visual_diagnostic(hou, parent, out)

    note = _find_sticky_note(parent, "agent_crowd_behavior_note")
    text = (
        "Agent/Crowd behavior output.\n\n"
        "OUT_AGENT_CROWD_BEHAVIOR keeps the Houdini Agent primitive from OUT_AGENT_CROWD_SCAFFOLD and transfers runtime behavior attributes from OUT_CROWD_CLIP_STATE_DRIVER.\n"
        "This is now the main Agent/Crowd-side display target: clipname/agentclip/clip, state/crowd_state, P/orient/v, and target_seat are all present on the agent point.\n\n"
        "If the viewport still draws the Agent as a skeleton, display OUT_AGENT_CROWD_BEHAVIOR_UNPACKED to inspect the mesh/shape result."
    )
    if note is None:
        _add_note(parent, "agent_crowd_behavior_note", text)
    else:
        note.setText(text)
    parent.layoutChildren()


def _ensure_agent_crowd_behavior_unpacked_preview(hou: Any, parent: Any, source: Any | None) -> None:
    out = parent.node("OUT_AGENT_CROWD_BEHAVIOR_UNPACKED")
    if out is None:
        out = parent.createNode("null", "OUT_AGENT_CROWD_BEHAVIOR_UNPACKED")

    if source is None:
        _disconnect_input(out, 0)
        return

    unpack = parent.node("unpack_agent_crowd_behavior")
    if unpack is None:
        unpack = _create_first_available(
            hou,
            parent,
            (
                "agentunpack",
                "agentunpack::2.0",
                "crowd::agentunpack",
                "crowd::agentunpack::2.0",
                "unpack",
            ),
            "unpack_agent_crowd_behavior",
        )

    if unpack is not None:
        try:
            unpack.setInput(0, source)
            out.setInput(0, unpack)
        except Exception:
            out.setInput(0, source)
    else:
        out.setInput(0, source)


def _ensure_agent_crowd_visual_diagnostic(hou: Any, parent: Any, source: Any | None) -> None:
    diagnostic = parent.node("inspect_agent_crowd_visual_layers")
    if diagnostic is None:
        diagnostic = parent.createNode("python", "inspect_agent_crowd_visual_layers")
    if source is not None:
        diagnostic.setInput(0, source)
    else:
        _disconnect_input(diagnostic, 0)
    parm = diagnostic.parm("python")
    if parm is not None:
        parm.set(_agent_crowd_visual_diagnostic_python_sop())

    out = parent.node("OUT_AGENT_CROWD_VISUAL_DIAGNOSTIC")
    if out is None:
        out = parent.createNode("null", "OUT_AGENT_CROWD_VISUAL_DIAGNOSTIC")
    out.setInput(0, diagnostic)


def _create_agent_clip_experiment(hou: Any, root: Any, parent: Any, plan: dict[str, Any]) -> None:
    bridge_merge = _create_object_merge(
        hou,
        parent,
        "IN_AGENT_CLIP_BRIDGE",
        f"{root.path()}/agent_crowd_pipeline/OUT_AGENT_CLIP_BRIDGE",
    )
    probe = parent.createNode("python", "probe_agent_clip_connection")
    if bridge_merge is not None:
        probe.setInput(0, bridge_merge)
    probe.parm("python").set(_agent_clip_experiment_python_sop(plan))

    out = parent.createNode("null", "OUT_AGENT_CLIP_EXPERIMENT")
    out.setInput(0, probe)
    out.setDisplayFlag(True)
    out.setRenderFlag(True)

    test_input = parent.createNode("null", "OUT_AGENT_CLIP_NODE_TEST_INPUT")
    test_input.setInput(0, probe)
    _create_agent_clip_node_test_candidates(hou, parent, test_input, plan)
    result = parent.createNode("python", "summarize_agent_clip_node_test")
    result.setInput(0, test_input)
    result.parm("python").set(_agent_clip_node_test_result_python_sop(plan))
    result_out = parent.createNode("null", "OUT_AGENT_CLIP_NODE_TEST_RESULT")
    result_out.setInput(0, result)

    walk_test = parent.createNode("python", "filter_agentclip_walk_test")
    walk_test.setInput(0, test_input)
    walk_test.parm("python").set(_agent_clip_walk_test_input_python_sop(plan))
    walk_test_out = parent.createNode("null", "OUT_AGENT_CLIP_WALK_TEST_INPUT")
    walk_test_out.setInput(0, walk_test)
    walk_result = parent.createNode("python", "summarize_agentclip_walk_test")
    walk_result.setInput(0, walk_test_out)
    walk_result.parm("python").set(_agent_clip_walk_test_result_python_sop(plan))
    walk_result_out = parent.createNode("null", "OUT_AGENT_CLIP_WALK_TEST_RESULT")
    walk_result_out.setInput(0, walk_result)

    clip_test_inputs = {"walk": walk_test_out}
    for clip_name, output_name, result_name, target_node in (
        ("sit_down", "OUT_AGENT_CLIP_SIT_DOWN_TEST_INPUT", "OUT_AGENT_CLIP_SIT_DOWN_TEST_RESULT", "TEST_AGENTCLIP_SIT_DOWN"),
        ("sit_idle", "OUT_AGENT_CLIP_SIT_IDLE_TEST_INPUT", "OUT_AGENT_CLIP_SIT_IDLE_TEST_RESULT", "TEST_AGENTCLIP_SIT_IDLE"),
    ):
        clip_test = parent.createNode("python", f"filter_agentclip_{clip_name}_test")
        clip_test.setInput(0, test_input)
        clip_test.parm("python").set(
            _agent_clip_named_test_input_python_sop(
                plan,
                clip_name=clip_name,
                output_name=output_name,
                target_node=target_node,
            )
        )
        clip_test_out = parent.createNode("null", output_name)
        clip_test_out.setInput(0, clip_test)
        clip_result = parent.createNode("python", f"summarize_agentclip_{clip_name}_test")
        clip_result.setInput(0, clip_test_out)
        clip_result.parm("python").set(
            _agent_clip_named_test_result_python_sop(
                plan,
                clip_name=clip_name,
                output_name=output_name,
                target_node=target_node,
            )
        )
        clip_result_out = parent.createNode("null", result_name)
        clip_result_out.setInput(0, clip_result)
        clip_test_inputs[clip_name] = clip_test_out

    _prewire_agent_clip_test_candidates(parent, clip_test_inputs)

    clipset_result = parent.createNode("python", "summarize_agentclip_three_clip_test")
    for index, clip_name in enumerate(("walk", "sit_down", "sit_idle")):
        clip_output = clip_test_inputs.get(clip_name)
        if clip_output is not None:
            clipset_result.setInput(index, clip_output)
    clipset_result.parm("python").set(_agent_clip_clipset_test_result_python_sop(plan))
    clipset_result_out = parent.createNode("null", "OUT_AGENT_CLIP_THREE_CLIP_TEST_RESULT")
    clipset_result_out.setInput(0, clipset_result)

    unbypass_guard = parent.createNode("python", "guard_agentclip_unbypass_tests")
    unbypass_guard.setInput(0, clipset_result_out)
    unbypass_guard.parm("python").set(_agent_clip_unbypass_guard_python_sop(plan))
    unbypass_guard_out = parent.createNode("null", "OUT_AGENT_CLIP_UNBYPASS_GUARD")
    unbypass_guard_out.setInput(0, unbypass_guard)

    sequence_log = parent.createNode("python", "summarize_agentclip_activation_sequence")
    sequence_log.setInput(0, unbypass_guard_out)
    sequence_log.parm("python").set(_agent_clip_activation_sequence_log_python_sop(plan))
    sequence_log_out = parent.createNode("null", "OUT_AGENT_CLIP_SEQUENCE_LOG")
    sequence_log_out.setInput(0, sequence_log)

    _add_note(
        parent,
        "agent_clip_experiment_note",
        "Agent Clip experiment probe.\n\n"
        "OUT_AGENT_CLIP_EXPERIMENT is the safe output and does not cook real Agent Clip/Crowd Solver nodes.\n"
        "OUT_AGENT_CLIP_NODE_TEST_INPUT is the first input to use when testing version-specific Agent Clip nodes.\n"
        "OUT_AGENT_CLIP_NODE_TEST_RESULT summarizes the manual Agent Clip test contract.\n"
        "OUT_AGENT_CLIP_*_TEST_INPUT nodes contain one clip at a time for single-clip Agent Clip tests.\n"
        "TEST_AGENTCLIP_* nodes are prewired to those safe inputs but remain bypassed.\n"
        "OUT_AGENT_CLIP_THREE_CLIP_TEST_RESULT summarizes the walk -> sit_down -> sit_idle handoff.\n"
        "OUT_AGENT_CLIP_UNBYPASS_GUARD tells you whether zero or one Agent Clip test is active.\n"
        "OUT_AGENT_CLIP_SEQUENCE_LOG shows the expected one-at-a-time activation sequence.\n"
        "Any TEST_* Agent/Crowd nodes are created bypassed and are not connected to the safe output.",
    )
    parent.layoutChildren()


def _create_agent_clip_node_test_candidates(hou: Any, parent: Any, test_input: Any, plan: dict[str, Any]) -> None:
    available = {
        "agentclip": _available_type_name(hou, parent, ("agentclip", "crowd::agentclip")),
        "clip_locomotion": _available_type_name(hou, parent, ("agentcliplocomotion", "clip_locomotion", "agentclipproperties")),
        "crowdsource": _available_type_name(hou, parent, ("crowdsource", "crowd::crowdsource")),
        "crowdsolver": _available_crowd_solver_type_name(hou, parent),
    }
    created = []
    for label, clip_name in (("walk", "walk"), ("sit_down", "sit_down"), ("sit_idle", "sit_idle")):
        type_name = available["agentclip"]
        if not type_name:
            continue
        node = parent.node(f"TEST_AGENTCLIP_{label.upper()}")
        if node is None:
            node = _create_named_node_safely(parent, type_name, f"TEST_AGENTCLIP_{label.upper()}")
        if node is None:
            continue
        _set_first_existing_parm(node, ("clipname", "clip", "name"), clip_name)
        _set_first_existing_parm(node, ("group", "pointgroup", "sourcegroup"), f"@clipname={clip_name}")
        _disconnect_input(node, 0)
        _safe_bypass(node, True)
        created.append(node)

    type_name = available["clip_locomotion"]
    if type_name:
        node = parent.node("TEST_CLIP_LOCOMOTION")
        if node is None:
            node = _create_named_node_safely(parent, type_name, "TEST_CLIP_LOCOMOTION")
        if node is None:
            type_name = ""
    if type_name and node is not None:
        if test_input is not None:
            try:
                node.setInput(0, test_input)
            except Exception:
                pass
        _safe_bypass(node, True)
        created.append(node)

    type_name = available["crowdsource"]
    if type_name:
        node = parent.node("TEST_CROWD_SOURCE")
        if node is None:
            node = _create_named_node_safely(parent, type_name, "TEST_CROWD_SOURCE")
        if node is None:
            type_name = ""
    if type_name and node is not None:
        _set_crowd_source_agent_count(node, int(plan.get("runtime", {}).get("agent_count", 1) or 1))
        _disconnect_input(node, 0)
        _safe_bypass(node, True)
        created.append(node)

    type_name = available["crowdsolver"]
    if type_name:
        node = parent.node("TEST_CROWD_SOLVER")
        if node is None:
            node = _create_named_node_safely(parent, type_name, "TEST_CROWD_SOLVER")
        if node is None:
            type_name = ""
    if type_name and node is not None:
        _disconnect_input(node, 0)
        _safe_bypass(node, True)
        created.append(node)

    if created:
        _add_or_update_note(
            parent,
            "agent_clip_node_test_note",
            "Bypassed TEST_* nodes are present only for parameter inspection.\n\n"
            "Start from OUT_AGENT_CLIP_NODE_TEST_INPUT when manually wiring the first Agent Clip test.\n"
            "Recommended attrs: clipname for clip selection, state for behavior state, P/orient/v for placement/motion.\n"
            "Keep OUT_AGENT_CLIP_EXPERIMENT as the safe display node while testing.",
        )


def _prewire_agent_clip_test_candidates(parent: Any, clip_test_inputs: dict[str, Any]) -> None:
    for clip_name, target_name in (
        ("walk", "TEST_AGENTCLIP_WALK"),
        ("sit_down", "TEST_AGENTCLIP_SIT_DOWN"),
        ("sit_idle", "TEST_AGENTCLIP_SIT_IDLE"),
    ):
        node = parent.node(target_name)
        source = clip_test_inputs.get(clip_name)
        if node is None or source is None:
            continue
        _set_first_existing_parm(node, ("clipname", "clip", "name"), clip_name)
        _set_first_existing_parm(node, ("group", "pointgroup", "sourcegroup"), f"@clipname={clip_name}")
        try:
            node.setInput(0, source)
        except Exception:
            pass
        _safe_bypass(node, True)

    _add_or_update_note(
        parent,
        "agent_clip_prewire_note",
        "TEST_AGENTCLIP_* nodes are prewired to their matching OUT_AGENT_CLIP_*_TEST_INPUT nodes.\n\n"
        "They remain bypassed and are not connected to OUT_AGENT_CLIP_EXPERIMENT.\n"
        "Un-bypass one TEST_AGENTCLIP_* node at a time only after the corresponding result node reports ready.",
    )


def _ensure_runtime_kinefx_preview(hou: Any, root: Any, plan: dict[str, Any]) -> None:
    parent = root.node("kinefx_imports")
    if parent is None:
        return

    driver_path = f"{root.path()}/agent_crowd_pipeline/OUT_CROWD_CLIP_STATE_DRIVER"
    driver_merge = parent.node("IN_CROWD_CLIP_STATE_DRIVER")
    if driver_merge is None:
        driver_merge = _create_object_merge(hou, parent, "IN_CROWD_CLIP_STATE_DRIVER", driver_path)
    else:
        _set_first_existing_parm(driver_merge, ("objpath1", "objpath", "path"), driver_path)
        _set_first_existing_parm(driver_merge, ("xformtype", "transform"), 1)

    preview = parent.node("APPLY_RUNTIME_BEHAVIOR_TRANSFORM")
    if preview is None:
        preview = parent.createNode("python", "APPLY_RUNTIME_BEHAVIOR_TRANSFORM")
    for index, node_name in enumerate(("OUT_WALK", "OUT_SIT_DOWN", "OUT_SIT_IDLE")):
        source = parent.node(node_name)
        if source is not None:
            preview.setInput(index, source)
    if driver_merge is not None:
        preview.setInput(3, driver_merge)
    parm = preview.parm("python")
    if parm is not None:
        parm.set(_runtime_kinefx_preview_python_sop(plan))

    out = parent.node("OUT_RUNTIME_AGENT_BEHAVIOR")
    if out is None:
        out = parent.createNode("null", "OUT_RUNTIME_AGENT_BEHAVIOR")
    out.setInput(0, preview)
    out.setDisplayFlag(True)
    out.setRenderFlag(True)

    note = _find_sticky_note(parent, "runtime_kinefx_preview_note")
    text = (
        "Display OUT_RUNTIME_AGENT_BEHAVIOR to preview runtime behavior on the KineFX character.\n\n"
        "The node reads agent_crowd_pipeline/OUT_CROWD_CLIP_STATE_DRIVER and uses the first agent point:\n"
        "current_clip/clipname selects walk, sit_down, or sit_idle.\n"
        "P places the KineFX clip at the runtime behavior position.\n\n"
        "This is still a safe prototype bridge, not the final Crowd Solver graph."
    )
    if note is None:
        _add_note(parent, "runtime_kinefx_preview_note", text)
    else:
        note.setText(text)
    parent.layoutChildren()


def _ensure_kinefx_clip_diagnostic(hou: Any, parent: Any, files: CrowdPrototypeFiles, plan: dict[str, Any]) -> None:
    diagnostic = parent.node("inspect_kinefx_clip_runtime")
    if diagnostic is None:
        diagnostic = parent.createNode("python", "inspect_kinefx_clip_runtime")

    for index, node_name in enumerate(("OUT_WALK", "OUT_SIT_DOWN", "OUT_SIT_IDLE", "OUT_RUNTIME_AGENT_BEHAVIOR")):
        source = parent.node(node_name)
        if source is not None:
            diagnostic.setInput(index, source)
        else:
            _disconnect_input(diagnostic, index)

    parm = diagnostic.parm("python")
    if parm is not None:
        parm.set(_kinefx_clip_diagnostic_python_sop(files, plan))

    out = parent.node("OUT_KINEFX_CLIP_DIAGNOSTIC")
    if out is None:
        out = parent.createNode("null", "OUT_KINEFX_CLIP_DIAGNOSTIC")
    out.setInput(0, diagnostic)


def _ensure_agent_crowd_visual_preview(hou: Any, root: Any, plan: dict[str, Any]) -> None:
    parent = root.node("agent_crowd_pipeline")
    if parent is None:
        parent = root.createNode("geo", "agent_crowd_pipeline")
        _clear_children(parent)

    kinefx_path = f"{root.path()}/kinefx_imports/OUT_RUNTIME_AGENT_BEHAVIOR"
    behavior_path = f"{root.path()}/agent_crowd_pipeline/OUT_AGENT_CROWD_BEHAVIOR"

    kinefx_merge = parent.node("IN_KINEFX_RUNTIME_VISUAL")
    if kinefx_merge is None:
        kinefx_merge = _create_object_merge(hou, parent, "IN_KINEFX_RUNTIME_VISUAL", kinefx_path)
    else:
        _set_first_existing_parm(kinefx_merge, ("objpath1", "objpath", "path"), kinefx_path)
        _set_first_existing_parm(kinefx_merge, ("xformtype", "transform"), 1)

    behavior_merge = parent.node("IN_AGENT_CROWD_BEHAVIOR_FOR_VISUAL")
    if behavior_merge is None:
        behavior_merge = _create_object_merge(hou, parent, "IN_AGENT_CROWD_BEHAVIOR_FOR_VISUAL", behavior_path)
    else:
        _set_first_existing_parm(behavior_merge, ("objpath1", "objpath", "path"), behavior_path)
        _set_first_existing_parm(behavior_merge, ("xformtype", "transform"), 1)

    preview = parent.node("build_agent_crowd_visual_preview")
    if preview is None:
        preview = parent.createNode("python", "build_agent_crowd_visual_preview")
    if kinefx_merge is not None:
        preview.setInput(0, kinefx_merge)
    if behavior_merge is not None:
        preview.setInput(1, behavior_merge)
    parm = preview.parm("python")
    if parm is not None:
        parm.set(_agent_crowd_visual_preview_python_sop(plan))

    out = parent.node("OUT_AGENT_CROWD_VISUAL_PREVIEW")
    if out is None:
        out = parent.createNode("null", "OUT_AGENT_CROWD_VISUAL_PREVIEW")
    out.setInput(0, preview)
    out.setDisplayFlag(True)
    try:
        out.setRenderFlag(True)
    except Exception:
        pass

    _add_or_update_note(
        parent,
        "agent_crowd_visual_preview_note",
        "Display OUT_AGENT_CROWD_VISUAL_PREVIEW for viewport checking while the Agent Definition only has Default/Collision layers.\n\n"
        "Behavior authority remains OUT_AGENT_CROWD_BEHAVIOR.\n"
        "The visual preview reads kinefx_imports/OUT_RUNTIME_AGENT_BEHAVIOR and copies the Agent/Crowd behavior attributes for inspection.",
    )
    parent.layoutChildren()


def _ensure_runtime_dop_result_preview_node(hou: Any, root: Any, plan: dict[str, Any]) -> None:
    parent = _child_or_create(root, "geo", "runtime_dop_result_preview")
    _clear_children(parent)

    driver_merge = _create_object_merge(
        hou,
        parent,
        "IN_RUNTIME_DOP_DRIVER",
        f"{root.path()}/agent_crowd_pipeline/OUT_AGENT_CLIP_BRIDGE",
    )

    preview = parent.createNode("python", "summarize_runtime_dop_handoff")
    if driver_merge is not None:
        preview.setInput(0, driver_merge)
    preview.parm("python").set(_runtime_dop_result_preview_python_sop(plan))

    out = parent.createNode("null", "OUT_RUNTIME_DOP_RESULT")
    out.setInput(0, preview)
    out.setDisplayFlag(True)
    out.setRenderFlag(True)

    _add_or_update_note(
        parent,
        "runtime_dop_result_preview_note",
        "Runtime DOP handoff preview.\n\n"
        "Display OUT_RUNTIME_DOP_RESULT to inspect the behavior driver points that are handed to the DOP source scaffold.\n"
        "This is a displayable verification output; the Solver remains gated by OUT_EMPTY_CROWD_SOURCE after smoke tests.",
    )
    parent.layoutChildren()


def _create_object_merge(hou: Any, parent: Any, name: str, objpath: str):
    node = _create_first_available(hou, parent, ("object_merge", "objectmerge"), name)
    if node is None:
        return None
    _set_first_existing_parm(node, ("objpath1", "objpath", "path"), objpath)
    _set_first_existing_parm(node, ("xformtype", "transform"), 1)
    return node


def _interaction_python_sop(interaction_yaml: Path, schema_path: str | Path | None) -> str:
    schema_text = _as_posix(Path(schema_path)) if schema_path else ""
    return "\n".join(
        [
            "import hou",
            "from smartlib.dcc.houdini.crowd_loader import load_interaction_points",
            "geo = hou.pwd().geometry()",
            "geo.clear()",
            f"points = load_interaction_points(r'{_as_posix(interaction_yaml)}', schema_path=r'{schema_text}' or None)",
            "attrs = {",
            "    'interaction_type': geo.addAttrib(hou.attribType.Point, 'interaction_type', ''),",
            "    'enabled': geo.addAttrib(hou.attribType.Point, 'enabled', 1),",
            "    'priority': geo.addAttrib(hou.attribType.Point, 'priority', 0),",
            "    'seat_id': geo.addAttrib(hou.attribType.Point, 'seat_id', ''),",
            "    'interaction_id': geo.addAttrib(hou.attribType.Point, 'interaction_id', ''),",
            "    'occupied': geo.addAttrib(hou.attribType.Point, 'occupied', 0),",
            "    'reserved_by': geo.addAttrib(hou.attribType.Point, 'reserved_by', ''),",
            "    'approach_position': geo.addAttrib(hou.attribType.Point, 'approach_position', (0.0, 0.0, 0.0)),",
            "}",
            "for item in points:",
            "    p = geo.createPoint()",
            "    p.setPosition(item['P'])",
            "    for key in ('interaction_type', 'enabled', 'priority', 'seat_id', 'interaction_id', 'occupied', 'reserved_by'):",
            "        value = int(item[key]) if key in ('enabled', 'priority', 'occupied') else item[key]",
            "        p.setAttribValue(attrs[key], value)",
            "    p.setAttribValue(attrs['approach_position'], item['approach_position'])",
        ]
    )


def _plan_python_sop(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import math",
            "import hou",
            f"plan = {repr(plan)}",
            "geo = hou.pwd().geometry()",
            "geo.clear()",
            "loc = plan['locomotion']",
            "steps = ' -> '.join(plan['goal']['steps'])",
            "clip_sequence = 'walk -> sit_down -> sit_idle'",
            "target = plan.get('target') or {}",
            "position = target.get('position') or {}",
            "px = float(position.get('x', 0.0))",
            "py = float(position.get('y', 0.0))",
            "pz = float(position.get('z', 0.0))",
            "geo.addAttrib(hou.attribType.Global, 'behavior_steps', '')",
            "geo.addAttrib(hou.attribType.Global, 'interaction_point_id', '')",
            "geo.addAttrib(hou.attribType.Global, 'agent_state', '')",
            "geo.addAttrib(hou.attribType.Global, 'clip_sequence', '')",
            "geo.addAttrib(hou.attribType.Global, 'clip_walk', '')",
            "geo.addAttrib(hou.attribType.Global, 'clip_sit_down', '')",
            "geo.addAttrib(hou.attribType.Global, 'clip_sit_idle', '')",
            "geo.addAttrib(hou.attribType.Global, 'walk_speed', 0.0)",
            "geo.addAttrib(hou.attribType.Global, 'align_speed', 0.0)",
            "geo.addAttrib(hou.attribType.Global, 'walk_distance', 0.0)",
            "geo.addAttrib(hou.attribType.Global, 'align_distance', 0.0)",
            "geo.addAttrib(hou.attribType.Global, 'travel_distance', 0.0)",
            "geo.addAttrib(hou.attribType.Global, 'walk_frames', 0)",
            "geo.addAttrib(hou.attribType.Global, 'align_frames', 0)",
            "geo.addAttrib(hou.attribType.Global, 'walk_clip_frames', 0)",
            "geo.addAttrib(hou.attribType.Global, 'frame_ranges', '')",
            "geo.addAttrib(hou.attribType.Global, 'how_to_check', '')",
            "geo.setGlobalAttribValue('behavior_steps', steps)",
            "geo.setGlobalAttribValue('interaction_point_id', plan['goal']['interaction_point_id'])",
            "geo.setGlobalAttribValue('agent_state', plan['agent']['state'])",
            "geo.setGlobalAttribValue('clip_sequence', clip_sequence)",
            "geo.setGlobalAttribValue('clip_walk', plan['clips']['Walk'])",
            "geo.setGlobalAttribValue('clip_sit_down', plan['clips']['Sit Down'])",
            "geo.setGlobalAttribValue('clip_sit_idle', plan['clips']['Sit Idle'])",
            "geo.setGlobalAttribValue('walk_speed', float(loc['walk_speed']))",
            "geo.setGlobalAttribValue('align_speed', float(loc['align_speed']))",
            "geo.setGlobalAttribValue('walk_distance', float(loc['walk_distance']))",
            "geo.setGlobalAttribValue('align_distance', float(loc['align_distance']))",
            "geo.setGlobalAttribValue('travel_distance', float(loc['travel_distance']))",
            "geo.setGlobalAttribValue('walk_frames', int(loc['walk_frames']))",
            "geo.setGlobalAttribValue('align_frames', int(loc['align_frames']))",
            "geo.setGlobalAttribValue('walk_clip_frames', int(loc['walk_clip_frames']))",
            "geo.setGlobalAttribValue('frame_ranges', '{}-{} walk, {}-{} align_to_seat, {}-{} sit_down, {}+ sit_idle'.format(loc['walk_start_frame'], loc['walk_end_frame'], loc['walk_end_frame'] + 1, loc['align_end_frame'], loc['align_end_frame'] + 1, loc['sit_down_end_frame'], loc['sit_idle_start_frame']))",
            "geo.setGlobalAttribValue('how_to_check', 'Geometry Spreadsheet > Detail or Points')",
            "attrs = {",
            "    'behavior_steps': geo.addAttrib(hou.attribType.Point, 'behavior_steps', ''),",
            "    'interaction_point_id': geo.addAttrib(hou.attribType.Point, 'interaction_point_id', ''),",
            "    'agent_state': geo.addAttrib(hou.attribType.Point, 'agent_state', ''),",
            "    'interaction_type': geo.addAttrib(hou.attribType.Point, 'interaction_type', ''),",
            "    'clip_sequence': geo.addAttrib(hou.attribType.Point, 'clip_sequence', ''),",
            "}",
            "p = geo.createPoint()",
            "p.setPosition((px, py, pz))",
            "p.setAttribValue(attrs['behavior_steps'], steps)",
            "p.setAttribValue(attrs['interaction_point_id'], plan['goal']['interaction_point_id'])",
            "p.setAttribValue(attrs['agent_state'], plan['agent']['state'])",
            "p.setAttribValue(attrs['interaction_type'], plan['goal']['interaction_type'])",
            "p.setAttribValue(attrs['clip_sequence'], clip_sequence)",
        ]
    )


def _single_agent_preview_python_sop(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import math",
            "import hou",
            f"plan = {repr(plan)}",
            "geo = hou.pwd().geometry()",
            "geo.clear()",
            "loc = plan['locomotion']",
            "seat_v = loc['seat_position']",
            "app_v = loc['approach_position']",
            "start_v = loc['start_position']",
            "seat = (float(seat_v['x']), float(seat_v['y']), float(seat_v['z']))",
            "app = (float(app_v['x']), float(app_v['y']), float(app_v['z']))",
            "start = (float(start_v['x']), float(start_v['y']), float(start_v['z']))",
            "frame = float(hou.frame())",
            "walk_start = float(loc['walk_start_frame'])",
            "walk_end = float(loc['walk_end_frame'])",
            "align_end = float(loc['align_end_frame'])",
            "sit_down_end = float(loc['sit_down_end_frame'])",
            "def lerp(a, b, t):",
            "    return tuple(float(a[i]) + (float(b[i]) - float(a[i])) * t for i in range(3))",
            "def clamp01(value):",
            "    return max(0.0, min(1.0, float(value)))",
            "if frame <= walk_end:",
            "    current_step = 'Walk'",
            "    current_clip = 'walk'",
            "    t = clamp01((frame - walk_start) / max(walk_end - walk_start, 1.0))",
            "    agent_pos = lerp(start, app, t)",
            "elif frame <= align_end:",
            "    current_step = 'Align'",
            "    current_clip = 'walk'",
            "    t = clamp01((frame - walk_end) / max(align_end - walk_end, 1.0))",
            "    agent_pos = app",
            "elif frame <= sit_down_end:",
            "    current_step = 'Sit Down'",
            "    current_clip = 'sit_down'",
            "    t = clamp01((frame - align_end) / max(sit_down_end - align_end, 1.0))",
            "    agent_pos = lerp(app, seat, t)",
            "else:",
            "    current_step = 'Sit Idle'",
            "    current_clip = 'sit_idle'",
            "    t = 1.0",
            "    agent_pos = seat",
            "geo.addAttrib(hou.attribType.Global, 'current_step', '')",
            "geo.addAttrib(hou.attribType.Global, 'current_clip', '')",
            "geo.addAttrib(hou.attribType.Global, 'target_seat', '')",
            "geo.addAttrib(hou.attribType.Global, 'frame_ranges', '')",
            "geo.addAttrib(hou.attribType.Global, 'walk_speed', 0.0)",
            "geo.addAttrib(hou.attribType.Global, 'walk_distance', 0.0)",
            "geo.setGlobalAttribValue('current_step', current_step)",
            "geo.setGlobalAttribValue('current_clip', current_clip)",
            "geo.setGlobalAttribValue('target_seat', plan['goal']['interaction_point_id'])",
            "geo.setGlobalAttribValue('frame_ranges', '{}-{} walk, {}-{} align_to_seat, {}-{} sit_down, {}+ sit_idle'.format(loc['walk_start_frame'], loc['walk_end_frame'], loc['walk_end_frame'] + 1, loc['align_end_frame'], loc['align_end_frame'] + 1, loc['sit_down_end_frame'], loc['sit_idle_start_frame']))",
            "geo.setGlobalAttribValue('walk_speed', float(loc['walk_speed']))",
            "geo.setGlobalAttribValue('walk_distance', float(loc['walk_distance']))",
            "name_attr = geo.addAttrib(hou.attribType.Point, 'name', '')",
            "role_attr = geo.addAttrib(hou.attribType.Point, 'preview_role', '')",
            "step_attr = geo.addAttrib(hou.attribType.Point, 'current_step', '')",
            "clip_attr = geo.addAttrib(hou.attribType.Point, 'current_clip', '')",
            "pscale_attr = geo.addAttrib(hou.attribType.Point, 'pscale', 0.15)",
            "cd_attr = geo.addAttrib(hou.attribType.Point, 'Cd', (1.0, 1.0, 1.0))",
            "def point(name, role, position, color, scale):",
            "    p = geo.createPoint()",
            "    p.setPosition(position)",
            "    p.setAttribValue(name_attr, name)",
            "    p.setAttribValue(role_attr, role)",
            "    p.setAttribValue(step_attr, current_step)",
            "    p.setAttribValue(clip_attr, current_clip)",
            "    p.setAttribValue(pscale_attr, scale)",
            "    p.setAttribValue(cd_attr, color)",
            "    return p",
            "p_start = point('agent_start', 'start', start, (0.25, 0.35, 1.0), 0.12)",
            "p_app = point('approach_position', 'approach', app, (0.0, 0.85, 1.0), 0.16)",
            "p_seat = point(plan['goal']['interaction_point_id'], 'seat', seat, (1.0, 0.75, 0.1), 0.2)",
            "p_agent = point('agent_001', 'agent', agent_pos, (1.0, 0.1, 0.05), 0.28)",
            "poly = geo.createPolygon()",
            "poly.setIsClosed(False)",
            "poly.addVertex(p_start)",
            "poly.addVertex(p_app)",
            "poly.addVertex(p_seat)",
        ]
    )


def _runtime_agent_source_python_sop(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import math",
            "import random",
            "import hou",
            f"plan = {repr(plan)}",
            "geo = hou.pwd().geometry()",
            "geo.clear()",
            "runtime = plan.get('runtime') or {}",
            "points = plan.get('interaction_points') or []",
            "seat_positions = []",
            "seat_approaches = []",
            "for item in points:",
            "    p = item.get('P') or (0.0, 0.0, 0.0)",
            "    seat = (float(p[0]), float(p[1]), float(p[2]))",
            "    app = item.get('approach_position') or (seat[0] - 1.0, seat[1], seat[2])",
            "    approach = (float(app[0]), float(app[1]), float(app[2]))",
            "    seat_positions.append(seat)",
            "    seat_approaches.append(approach)",
            "if seat_positions:",
            "    center = (sum(p[0] for p in seat_positions) / len(seat_positions), sum(p[1] for p in seat_positions) / len(seat_positions), sum(p[2] for p in seat_positions) / len(seat_positions))",
            "else:",
            "    center = (0.0, 0.0, 0.0)",
            "agent_count = max(1, int(runtime.get('agent_count', 4)))",
            "spawn_radius = float(runtime.get('spawn_radius', 4.0) or 4.0)",
            "validation_short = bool(int(runtime.get('validation_short_distance', 0) or 0))",
            "validation_start_distance = max(0.0, float(runtime.get('validation_start_distance', 0.0) or 0.0))",
            "seed = int(runtime.get('seed', 7))",
            "rng = random.Random(seed)",
            "name_attr = geo.addAttrib(hou.attribType.Point, 'name', '')",
            "state_attr = geo.addAttrib(hou.attribType.Point, 'agent_state', '')",
            "step_attr = geo.addAttrib(hou.attribType.Point, 'current_step', '')",
            "clip_attr = geo.addAttrib(hou.attribType.Point, 'current_clip', '')",
            "target_attr = geo.addAttrib(hou.attribType.Point, 'target_seat', '')",
            "elapsed_attr = geo.addAttrib(hou.attribType.Point, 'state_elapsed', 0.0)",
            "pscale_attr = geo.addAttrib(hou.attribType.Point, 'pscale', 0.15)",
            "cd_attr = geo.addAttrib(hou.attribType.Point, 'Cd', (1.0, 0.15, 0.1))",
            "def short_validation_position():",
            "    if not seat_positions or not seat_approaches:",
            "        return center",
            "    seat = seat_positions[0]",
            "    approach = seat_approaches[0]",
            "    dx = approach[0] - seat[0]",
            "    dz = approach[2] - seat[2]",
            "    length = math.sqrt(dx * dx + dz * dz)",
            "    if length <= 1e-8:",
            "        away = (-1.0, 0.0, 0.0)",
            "    else:",
            "        away = (dx / length, 0.0, dz / length)",
            "    return (approach[0] + away[0] * validation_start_distance, approach[1], approach[2] + away[2] * validation_start_distance)",
            "for index in range(agent_count):",
            "    if validation_short and index == 0:",
            "        pos = short_validation_position()",
            "    else:",
            "        angle = rng.random() * math.tau",
            "        radius = spawn_radius * (0.35 + rng.random() * 0.65)",
            "        pos = (center[0] + math.cos(angle) * radius, center[1], center[2] + math.sin(angle) * radius)",
            "    p = geo.createPoint()",
            "    p.setPosition(pos)",
            "    p.setAttribValue(name_attr, 'agent_{:03d}'.format(index + 1))",
            "    p.setAttribValue(state_attr, 'idle')",
            "    p.setAttribValue(step_attr, 'Find Seat')",
            "    p.setAttribValue(clip_attr, '')",
            "    p.setAttribValue(target_attr, '')",
            "    p.setAttribValue(elapsed_attr, 0.0)",
            "    p.setAttribValue(pscale_attr, 0.15)",
            "    p.setAttribValue(cd_attr, (1.0, 0.15, 0.1))",
            "geo.addAttrib(hou.attribType.Global, 'agent_source_role', '')",
            "geo.addAttrib(hou.attribType.Global, 'agent_count', 0)",
            "geo.addAttrib(hou.attribType.Global, 'validation_short_distance', 0)",
            "geo.addAttrib(hou.attribType.Global, 'validation_start_distance', 0.0)",
            "geo.addAttrib(hou.attribType.Global, 'runtime_spawn_radius', 0.0)",
            "geo.setGlobalAttribValue('agent_source_role', 'replaceable_agent_points_for_runtime_behavior')",
            "geo.setGlobalAttribValue('agent_count', agent_count)",
            "geo.setGlobalAttribValue('validation_short_distance', int(validation_short))",
            "geo.setGlobalAttribValue('validation_start_distance', validation_start_distance)",
            "geo.setGlobalAttribValue('runtime_spawn_radius', spawn_radius)",
        ]
    )


def _runtime_behavior_python_sop(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import math",
            "import random",
            "import hou",
            f"plan = {repr(plan)}",
            "node = hou.pwd()",
            "geo = node.geometry()",
            "input_nodes = node.inputs()",
            "input_agent_geo = input_nodes[0].geometry() if len(input_nodes) > 0 and input_nodes[0] is not None else None",
            "input_seat_geo = input_nodes[1].geometry() if len(input_nodes) > 1 and input_nodes[1] is not None else None",
            "geo.clear()",
            "runtime = plan.get('runtime') or {}",
            "goal_type = plan['goal']['interaction_type']",
            "def attr_value(point, name, default=None):",
            "    attrib = point.geometry().findPointAttrib(name)",
            "    if attrib is None:",
            "        return default",
            "    try:",
            "        value = point.attribValue(attrib)",
            "    except Exception:",
            "        return default",
            "    return default if value is None else value",
            "def as_bool(value):",
            "    if isinstance(value, str):",
            "        return value.lower() in ('1', 'true', 'yes', 'on')",
            "    return bool(value)",
            "def as_position(value, default):",
            "    if value is None:",
            "        return default",
            "    try:",
            "        return (float(value[0]), float(value[1]), float(value[2]))",
            "    except Exception:",
            "        return default",
            "seats = []",
            "seat_source = 'input_seats' if input_seat_geo is not None and len(input_seat_geo.points()) else 'plan_yaml'",
            "if seat_source == 'input_seats':",
            "    for index, point in enumerate(input_seat_geo.points()):",
            "        interaction_type = str(attr_value(point, 'interaction_type', goal_type) or goal_type)",
            "        if interaction_type != goal_type:",
            "            continue",
            "        enabled = as_bool(attr_value(point, 'enabled', True))",
            "        if not enabled:",
            "            continue",
            "        p = point.position()",
            "        position = (float(p[0]), float(p[1]), float(p[2]))",
            "        approach = as_position(attr_value(point, 'approach_position', None), (position[0] - 1.0, position[1], position[2]))",
            "        seats.append({",
            "            'id': str(attr_value(point, 'seat_id', '') or attr_value(point, 'name', '') or 'seat_{:03d}'.format(index + 1)),",
            "            'position': position,",
            "            'approach': approach,",
            "            'priority': int(attr_value(point, 'priority', 0) or 0),",
            "            'occupied': as_bool(attr_value(point, 'occupied', False)),",
            "            'reserved_by': str(attr_value(point, 'reserved_by', '') or ''),",
            "        })",
            "else:",
            "    for item in plan.get('interaction_points') or []:",
            "        if item.get('interaction_type') != goal_type or not bool(item.get('enabled', True)):",
            "            continue",
            "        p = item.get('P') or (0.0, 0.0, 0.0)",
            "        app = item.get('approach_position') or (float(p[0]) - 1.0, float(p[1]), float(p[2]))",
            "        seats.append({",
            "            'id': str(item.get('seat_id') or item.get('id') or ''),",
            "            'position': (float(p[0]), float(p[1]), float(p[2])),",
            "            'approach': (float(app[0]), float(app[1]), float(app[2])),",
            "            'priority': int(item.get('priority', 0)),",
            "            'occupied': bool(item.get('occupied', False)),",
            "            'reserved_by': str(item.get('reserved_by') or ''),",
            "        })",
            "frame = max(1, int(hou.frame()))",
            "fps = float(runtime.get('fps', 24.0) or 24.0)",
            "dt = 1.0 / fps if fps else 1.0 / 24.0",
            "walk_speed = float(runtime.get('walk_speed', 1.2) or 1.2)",
            "align_speed = float(runtime.get('align_speed', walk_speed) or walk_speed)",
            "query_radius = float(runtime.get('query_radius', 8.0) or 8.0)",
            "spawn_radius = float(runtime.get('spawn_radius', 4.0) or 4.0)",
            "arrive_distance = float(runtime.get('arrive_distance', 0.05) or 0.05)",
            "sit_distance = float(runtime.get('sit_distance', 0.05) or 0.05)",
            "sit_down_duration = float(runtime.get('sit_down_duration', 40.0 / 24.0) or (40.0 / 24.0))",
            "align_duration = float(runtime.get('align_duration', 16.0 / 24.0) or (16.0 / 24.0))",
            "agent_count = max(1, int(runtime.get('agent_count', 4)))",
            "seed = int(runtime.get('seed', 7))",
            "def dist(a, b):",
            "    return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)))",
            "def lerp(a, b, t):",
            "    return tuple(float(a[i]) + (float(b[i]) - float(a[i])) * max(0.0, min(1.0, float(t))) for i in range(3))",
            "def move_towards(position, target, speed):",
            "    d = dist(position, target)",
            "    if d <= 1e-8:",
            "        return target, 0.0",
            "    step = max(0.0, speed) * dt",
            "    if step >= d:",
            "        return target, 0.0",
            "    r = step / d",
            "    return tuple(float(position[i]) + (float(target[i]) - float(position[i])) * r for i in range(3)), d - step",
            "def normalized_xz(a, b, fallback=(0.0, 0.0, 1.0)):",
            "    dx = float(b[0]) - float(a[0])",
            "    dz = float(b[2]) - float(a[2])",
            "    length = math.sqrt(dx * dx + dz * dz)",
            "    if length <= 1e-8:",
            "        return fallback",
            "    return (dx / length, 0.0, dz / length)",
            "def seat_forward(seat):",
            "    return normalized_xz(seat['approach'], seat['position'], (0.0, 0.0, 1.0))",
            "def agent_motion_and_facing(agent):",
            "    seat = next((item for item in seats if item['id'] == agent['target']), None)",
            "    if seat is None:",
            "        pos = agent['position']",
            "        return pos, pos, (0.0, 0.0, 1.0), (0.0, 0.0, 1.0)",
            "    pos = agent['position']",
            "    sit_heading = seat_forward(seat)",
            "    if agent['state'] == 'walking_to_interaction':",
            "        motion_target = seat['approach']",
            "        facing_target = seat['approach']",
            "        move_heading = normalized_xz(pos, motion_target, sit_heading)",
            "        facing_heading = move_heading",
            "    elif agent['state'] == 'aligning_to_interaction':",
            "        motion_target = pos",
            "        facing_target = seat['position']",
            "        move_heading = (0.0, 0.0, 0.0)",
            "        facing_heading = sit_heading",
            "    else:",
            "        motion_target = seat['position']",
            "        facing_target = seat['position']",
            "        move_heading = (0.0, 0.0, 0.0)",
            "        facing_heading = sit_heading",
            "    return motion_target, facing_target, move_heading, facing_heading",
            "if seats:",
            "    center = (sum(seat['position'][0] for seat in seats) / len(seats), sum(seat['position'][1] for seat in seats) / len(seats), sum(seat['position'][2] for seat in seats) / len(seats))",
            "else:",
            "    center = (0.0, 0.0, 0.0)",
            "agents = []",
            "agent_source = 'input_agents' if input_agent_geo is not None and len(input_agent_geo.points()) else 'generated_fallback'",
            "if agent_source == 'input_agents':",
            "    for index, point in enumerate(input_agent_geo.points()):",
            "        p = point.position()",
            "        pos = (float(p[0]), float(p[1]), float(p[2]))",
            "        state = str(attr_value(point, 'agent_state', '') or attr_value(point, 'state', '') or 'idle')",
            "        step = str(attr_value(point, 'current_step', '') or 'Find Seat')",
            "        clip = str(attr_value(point, 'current_clip', '') or '')",
            "        target = str(attr_value(point, 'target_seat', '') or '')",
            "        name = str(attr_value(point, 'name', '') or attr_value(point, 'agentname', '') or 'agent_{:03d}'.format(index + 1))",
            "        agents.append({'id': name, 'position': pos, 'state': state, 'step': step, 'clip': clip, 'target': target, 'distance': 0.0, 'elapsed': float(attr_value(point, 'state_elapsed', 0.0) or 0.0)})",
            "if not agents:",
            "    agent_source = 'generated_fallback'",
            "    rng = random.Random(seed)",
            "    for index in range(agent_count):",
            "        angle = rng.random() * math.tau",
            "        radius = spawn_radius * (0.35 + rng.random() * 0.65)",
            "        pos = (center[0] + math.cos(angle) * radius, center[1], center[2] + math.sin(angle) * radius)",
            "        agents.append({'id': 'agent_{:03d}'.format(index + 1), 'position': pos, 'state': 'idle', 'step': 'Find Seat', 'clip': '', 'target': '', 'distance': 0.0, 'elapsed': 0.0})",
            "def nearest_available(agent):",
            "    choices = []",
            "    for seat in seats:",
            "        if seat['occupied'] or seat['reserved_by']:",
            "            continue",
            "        d = dist(agent['position'], seat['position'])",
            "        if d <= query_radius:",
            "            choices.append((d, -seat['priority'], seat['id'], seat))",
            "    choices.sort()",
            "    return choices[0][3] if choices else None",
            "for _frame in range(1, frame + 1):",
            "    for agent in agents:",
            "        if agent['state'] in ('sit_idle', 'no_available_interaction'):",
            "            continue",
            "        if agent['state'] == 'idle':",
            "            seat = nearest_available(agent)",
            "            if seat is None:",
            "                agent['state'] = 'no_available_interaction'",
            "                agent['step'] = 'No Seat Available'",
            "                agent['clip'] = ''",
            "                continue",
            "            seat['reserved_by'] = agent['id']",
            "            agent['target'] = seat['id']",
            "            agent['state'] = 'walking_to_interaction'",
            "            agent['step'] = 'Walk'",
            "            agent['clip'] = 'walk'",
            "        seat = next((item for item in seats if item['id'] == agent['target']), None)",
            "        if seat is None:",
            "            agent['state'] = 'no_available_interaction'",
            "            agent['step'] = 'No Seat Available'",
            "            agent['clip'] = ''",
            "            continue",
            "        if agent['state'] == 'walking_to_interaction':",
            "            agent['position'], agent['distance'] = move_towards(agent['position'], seat['approach'], walk_speed)",
            "            agent['step'] = 'Walk'",
            "            agent['clip'] = 'walk'",
            "            if agent['distance'] <= arrive_distance:",
            "                agent['position'] = seat['approach']",
            "                agent['state'] = 'aligning_to_interaction'",
            "                agent['step'] = 'Align'",
            "                agent['elapsed'] = 0.0",
            "        elif agent['state'] == 'aligning_to_interaction':",
            "            agent['elapsed'] += dt",
            "            agent['position'] = seat['approach']",
            "            agent['distance'] = 0.0",
            "            agent['step'] = 'Align'",
            "            agent['clip'] = 'walk'",
            "            if agent['elapsed'] >= align_duration:",
            "                agent['position'] = seat['approach']",
            "                agent['state'] = 'sitting_down'",
            "                agent['step'] = 'Sit Down'",
            "                agent['clip'] = 'sit_down'",
            "                agent['elapsed'] = 0.0",
            "        elif agent['state'] == 'sitting_down':",
            "            agent['elapsed'] += dt",
            "            sit_t = max(0.0, min(1.0, agent['elapsed'] / max(sit_down_duration, 1e-6)))",
            "            agent['position'] = lerp(seat['approach'], seat['position'], sit_t)",
            "            agent['distance'] = dist(agent['position'], seat['position'])",
            "            agent['step'] = 'Sit Down'",
            "            agent['clip'] = 'sit_down'",
            "            if agent['elapsed'] >= sit_down_duration:",
            "                agent['state'] = 'sit_idle'",
            "                agent['step'] = 'Sit Idle'",
            "                agent['clip'] = 'sit_idle'",
            "                seat['occupied'] = True",
            "                seat['reserved_by'] = ''",
            "name_attr = geo.addAttrib(hou.attribType.Point, 'name', '')",
            "entity_attr = geo.addAttrib(hou.attribType.Point, 'entity_type', '')",
            "state_attr = geo.addAttrib(hou.attribType.Point, 'agent_state', '')",
            "step_attr = geo.addAttrib(hou.attribType.Point, 'current_step', '')",
            "clip_attr = geo.addAttrib(hou.attribType.Point, 'current_clip', '')",
            "target_attr = geo.addAttrib(hou.attribType.Point, 'target_seat', '')",
            "distance_attr = geo.addAttrib(hou.attribType.Point, 'distance_to_target', 0.0)",
            "approach_attr = geo.addAttrib(hou.attribType.Point, 'approach_position', (0.0, 0.0, 0.0))",
            "motion_target_attr = geo.addAttrib(hou.attribType.Point, 'motion_target_position', (0.0, 0.0, 0.0))",
            "facing_target_attr = geo.addAttrib(hou.attribType.Point, 'facing_target_position', (0.0, 0.0, 0.0))",
            "move_heading_attr = geo.addAttrib(hou.attribType.Point, 'move_heading', (0.0, 0.0, 1.0))",
            "heading_attr = geo.addAttrib(hou.attribType.Point, 'heading', (0.0, 0.0, 1.0))",
            "occupied_attr = geo.addAttrib(hou.attribType.Point, 'occupied', 0)",
            "reserved_attr = geo.addAttrib(hou.attribType.Point, 'reserved_by', '')",
            "pscale_attr = geo.addAttrib(hou.attribType.Point, 'pscale', 0.12)",
            "cd_attr = geo.addAttrib(hou.attribType.Point, 'Cd', (1.0, 1.0, 1.0))",
            "def add_point(name, entity, position, state, step, clip, target, distance_value, occupied, reserved_by, color, scale, approach=None, motion_target=None, facing_target=None, move_heading=None, heading=None):",
            "    p = geo.createPoint()",
            "    p.setPosition(position)",
            "    p.setAttribValue(name_attr, name)",
            "    p.setAttribValue(entity_attr, entity)",
            "    p.setAttribValue(state_attr, state)",
            "    p.setAttribValue(step_attr, step)",
            "    p.setAttribValue(clip_attr, clip)",
            "    p.setAttribValue(target_attr, target)",
            "    p.setAttribValue(distance_attr, float(distance_value))",
            "    p.setAttribValue(approach_attr, approach if approach is not None else position)",
            "    p.setAttribValue(motion_target_attr, motion_target if motion_target is not None else position)",
            "    p.setAttribValue(facing_target_attr, facing_target if facing_target is not None else position)",
            "    p.setAttribValue(move_heading_attr, move_heading if move_heading is not None else (0.0, 0.0, 1.0))",
            "    p.setAttribValue(heading_attr, heading if heading is not None else (0.0, 0.0, 1.0))",
            "    p.setAttribValue(occupied_attr, int(bool(occupied)))",
            "    p.setAttribValue(reserved_attr, reserved_by)",
            "    p.setAttribValue(cd_attr, color)",
            "    p.setAttribValue(pscale_attr, scale)",
            "for seat in seats:",
            "    state = 'occupied' if seat['occupied'] else ('reserved' if seat['reserved_by'] else 'available')",
            "    color = (1.0, 0.75, 0.1) if state == 'available' else ((0.2, 0.55, 1.0) if state == 'reserved' else (0.4, 0.4, 0.4))",
            "    forward = seat_forward(seat)",
            "    add_point(seat['id'], 'interaction', seat['position'], state, '', '', seat['id'], 0.0, seat['occupied'], seat['reserved_by'], color, 0.22, approach=seat['approach'], motion_target=seat['position'], facing_target=seat['position'], move_heading=forward, heading=forward)",
            "for agent in agents:",
            "    color = (1.0, 0.15, 0.1)",
            "    if agent['state'] == 'sit_idle':",
            "        color = (0.1, 0.8, 0.25)",
            "    elif agent['state'] == 'no_available_interaction':",
            "        color = (0.55, 0.55, 0.55)",
            "    elif agent['state'] == 'sitting_down':",
            "        color = (1.0, 0.45, 0.1)",
            "    motion_target, facing_target, move_heading, facing_heading = agent_motion_and_facing(agent)",
            "    add_point(agent['id'], 'agent', agent['position'], agent['state'], agent['step'], agent['clip'], agent['target'], agent['distance'], False, '', color, 0.18, approach=facing_target, motion_target=motion_target, facing_target=facing_target, move_heading=move_heading, heading=facing_heading)",
            "agent_state_summary = ', '.join('{}:{}'.format(agent['id'], agent['state']) for agent in agents)",
            "agent_step_summary = ', '.join('{}:{}'.format(agent['id'], agent['step']) for agent in agents)",
            "agent_clip_summary = ', '.join('{}:{}'.format(agent['id'], agent['clip'] or 'none') for agent in agents)",
            "agent_target_summary = ', '.join('{}:{}'.format(agent['id'], agent['target'] or 'none') for agent in agents)",
            "agent_distance_summary = ', '.join('{}:{:.3f}'.format(agent['id'], float(agent['distance'])) for agent in agents)",
            "seat_status_summary = ', '.join('{}:{}'.format(seat['id'], 'occupied' if seat['occupied'] else ('reserved_by=' + seat['reserved_by'] if seat['reserved_by'] else 'available')) for seat in seats)",
            "geo.addAttrib(hou.attribType.Global, 'runtime_mode', '')",
            "geo.addAttrib(hou.attribType.Global, 'agent_source', '')",
            "geo.addAttrib(hou.attribType.Global, 'seat_source', '')",
            "geo.addAttrib(hou.attribType.Global, 'rule_summary', '')",
            "geo.addAttrib(hou.attribType.Global, 'how_to_check', '')",
            "geo.addAttrib(hou.attribType.Global, 'point_attributes', '')",
            "geo.addAttrib(hou.attribType.Global, 'agent_states', '')",
            "geo.addAttrib(hou.attribType.Global, 'current_steps', '')",
            "geo.addAttrib(hou.attribType.Global, 'current_clips', '')",
            "geo.addAttrib(hou.attribType.Global, 'target_seats', '')",
            "geo.addAttrib(hou.attribType.Global, 'distance_to_targets', '')",
            "geo.addAttrib(hou.attribType.Global, 'seat_status', '')",
            "geo.addAttrib(hou.attribType.Global, 'agent_count', 0)",
            "geo.addAttrib(hou.attribType.Global, 'input_agent_count', 0)",
            "geo.addAttrib(hou.attribType.Global, 'input_seat_count', 0)",
            "geo.addAttrib(hou.attribType.Global, 'available_seats', 0)",
            "geo.addAttrib(hou.attribType.Global, 'occupied_seats', 0)",
            "geo.addAttrib(hou.attribType.Global, 'reserved_seats', 0)",
            "geo.addAttrib(hou.attribType.Global, 'current_frame', 0)",
            "geo.addAttrib(hou.attribType.Global, 'fps', 0.0)",
            "geo.addAttrib(hou.attribType.Global, 'query_radius', 0.0)",
            "geo.addAttrib(hou.attribType.Global, 'walk_speed', 0.0)",
            "geo.addAttrib(hou.attribType.Global, 'align_speed', 0.0)",
            "geo.addAttrib(hou.attribType.Global, 'sit_down_duration', 0.0)",
            "geo.setGlobalAttribValue('runtime_mode', 'condition_based_preview')",
            "geo.setGlobalAttribValue('agent_source', agent_source)",
            "geo.setGlobalAttribValue('seat_source', seat_source)",
            "geo.setGlobalAttribValue('rule_summary', 'Find available seat within query_radius -> reserve -> walk -> align -> sit_down -> sit_idle/occupied')",
            "geo.setGlobalAttribValue('how_to_check', 'Detail shows summaries and input sources. Switch Geometry Spreadsheet to Points for per-agent/per-seat attributes.')",
            "geo.setGlobalAttribValue('point_attributes', 'name, entity_type, agent_state, current_step, current_clip, target_seat, distance_to_target, approach_position, motion_target_position, facing_target_position, move_heading, heading, occupied, reserved_by')",
            "geo.setGlobalAttribValue('agent_states', agent_state_summary)",
            "geo.setGlobalAttribValue('current_steps', agent_step_summary)",
            "geo.setGlobalAttribValue('current_clips', agent_clip_summary)",
            "geo.setGlobalAttribValue('target_seats', agent_target_summary)",
            "geo.setGlobalAttribValue('distance_to_targets', agent_distance_summary)",
            "geo.setGlobalAttribValue('seat_status', seat_status_summary)",
            "geo.setGlobalAttribValue('agent_count', len(agents))",
            "geo.setGlobalAttribValue('input_agent_count', len(input_agent_geo.points()) if input_agent_geo is not None else 0)",
            "geo.setGlobalAttribValue('input_seat_count', len(input_seat_geo.points()) if input_seat_geo is not None else 0)",
            "geo.setGlobalAttribValue('available_seats', sum(1 for seat in seats if not seat['occupied'] and not seat['reserved_by']))",
            "geo.setGlobalAttribValue('occupied_seats', sum(1 for seat in seats if seat['occupied']))",
            "geo.setGlobalAttribValue('reserved_seats', sum(1 for seat in seats if seat['reserved_by']))",
            "geo.setGlobalAttribValue('current_frame', frame)",
            "geo.setGlobalAttribValue('fps', fps)",
            "geo.setGlobalAttribValue('query_radius', query_radius)",
            "geo.setGlobalAttribValue('walk_speed', walk_speed)",
            "geo.setGlobalAttribValue('align_speed', align_speed)",
            "geo.setGlobalAttribValue('sit_down_duration', sit_down_duration)",
        ]
    )


def _behavior_agent_driver_python_sop(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import math",
            "import hou",
            f"plan = {repr(plan)}",
            "node = hou.pwd()",
            "geo = node.geometry()",
            "input_nodes = node.inputs()",
            "runtime_geo = input_nodes[0].geometry() if input_nodes and input_nodes[0] is not None else None",
            "geo.clear()",
            "def attr_value(point, name, default=None):",
            "    attrib = point.geometry().findPointAttrib(name)",
            "    if attrib is None:",
            "        return default",
            "    try:",
            "        value = point.attribValue(attrib)",
            "    except Exception:",
            "        return default",
            "    return default if value is None else value",
            "def choose_clip(state, clip):",
            "    if clip:",
            "        return clip",
            "    if state in ('walking_to_interaction', 'aligning_to_interaction'):",
            "        return 'walk'",
            "    if state == 'sitting_down':",
            "        return 'sit_down'",
            "    if state == 'sit_idle':",
            "        return 'sit_idle'",
            "    return ''",
            "def vec3(value, default=(0.0, 0.0, 0.0)):",
            "    if value is None:",
            "        return default",
            "    try:",
            "        return (float(value[0]), float(value[1]), float(value[2]))",
            "    except Exception:",
            "        return default",
            "def heading_to_target(position, target_position, fallback=(0.0, 0.0, 1.0)):",
            "    dx = float(target_position[0]) - float(position[0])",
            "    dz = float(target_position[2]) - float(position[2])",
            "    length = math.sqrt(dx * dx + dz * dz)",
            "    if length <= 1e-8:",
            "        return fallback",
            "    return (dx / length, 0.0, dz / length)",
            "def yaw_to_orient(heading):",
            "    yaw = math.atan2(float(heading[0]), float(heading[2]))",
            "    return (0.0, math.sin(yaw * 0.5), 0.0, math.cos(yaw * 0.5))",
            "def seat_forward(seat):",
            "    return heading_to_target(seat['approach'], seat['position'], (0.0, 0.0, 1.0))",
            "def facing_heading_for_state(state, position, motion_target, facing_target, sit_heading):",
            "    if state == 'walking_to_interaction':",
            "        return heading_to_target(position, motion_target, sit_heading)",
            "    if state in ('aligning_to_interaction', 'sitting_down', 'sit_idle'):",
            "        return heading_to_target(position, facing_target, sit_heading)",
            "    return heading_to_target(position, motion_target, sit_heading)",
            "name_attr = geo.addAttrib(hou.attribType.Point, 'name', '')",
            "agentname_attr = geo.addAttrib(hou.attribType.Point, 'agentname', '')",
            "agentid_attr = geo.addAttrib(hou.attribType.Point, 'agentid', 0)",
            "entity_attr = geo.addAttrib(hou.attribType.Point, 'entity_type', '')",
            "state_attr = geo.addAttrib(hou.attribType.Point, 'agent_state', '')",
            "crowd_state_attr = geo.addAttrib(hou.attribType.Point, 'state', '')",
            "step_attr = geo.addAttrib(hou.attribType.Point, 'current_step', '')",
            "clip_attr = geo.addAttrib(hou.attribType.Point, 'current_clip', '')",
            "clipname_attr = geo.addAttrib(hou.attribType.Point, 'clipname', '')",
            "target_attr = geo.addAttrib(hou.attribType.Point, 'target_seat', '')",
            "target_pos_attr = geo.addAttrib(hou.attribType.Point, 'target_position', (0.0, 0.0, 0.0))",
            "motion_target_attr = geo.addAttrib(hou.attribType.Point, 'motion_target_position', (0.0, 0.0, 0.0))",
            "facing_target_attr = geo.addAttrib(hou.attribType.Point, 'facing_target_position', (0.0, 0.0, 0.0))",
            "move_heading_attr = geo.addAttrib(hou.attribType.Point, 'move_heading', (0.0, 0.0, 1.0))",
            "heading_attr = geo.addAttrib(hou.attribType.Point, 'heading', (0.0, 0.0, 1.0))",
            "orient_attr = geo.addAttrib(hou.attribType.Point, 'orient', (0.0, 0.0, 0.0, 1.0))",
            "distance_attr = geo.addAttrib(hou.attribType.Point, 'distance_to_target', 0.0)",
            "pscale_attr = geo.addAttrib(hou.attribType.Point, 'pscale', 0.14)",
            "cd_attr = geo.addAttrib(hou.attribType.Point, 'Cd', (1.0, 0.15, 0.1))",
            "seats = {}",
            "agents = []",
            "runtime_point_count = len(runtime_geo.points()) if runtime_geo is not None else 0",
            "if runtime_geo is not None:",
            "    for index, source_point in enumerate(runtime_geo.points()):",
            "        entity = str(attr_value(source_point, 'entity_type', '') or '')",
            "        if entity != 'interaction':",
            "            continue",
            "        name = str(attr_value(source_point, 'name', '') or attr_value(source_point, 'target_seat', '') or 'seat_{:03d}'.format(index + 1))",
            "        position = vec3(source_point.position())",
            "        approach = vec3(attr_value(source_point, 'approach_position', None), vec3(attr_value(source_point, 'facing_target_position', None), position))",
            "        seats[name] = {'position': position, 'approach': approach}",
            "    for index, source_point in enumerate(runtime_geo.points()):",
            "        entity = str(attr_value(source_point, 'entity_type', '') or '')",
            "        if entity and entity != 'agent':",
            "            continue",
            "        state = str(attr_value(source_point, 'agent_state', '') or attr_value(source_point, 'state', '') or 'idle')",
            "        step = str(attr_value(source_point, 'current_step', '') or '')",
            "        clip = choose_clip(state, str(attr_value(source_point, 'current_clip', '') or attr_value(source_point, 'clipname', '') or ''))",
            "        target = str(attr_value(source_point, 'target_seat', '') or '')",
            "        name = str(attr_value(source_point, 'name', '') or attr_value(source_point, 'agentname', '') or 'agent_{:03d}'.format(index + 1))",
            "        position = vec3(source_point.position())",
            "        seat = seats.get(target)",
            "        sit_heading = seat_forward(seat) if seat is not None else vec3(attr_value(source_point, 'heading', None), (0.0, 0.0, 1.0))",
            "        fallback_motion = seat['position'] if seat is not None else position",
            "        fallback_facing = seat['position'] if seat is not None else fallback_motion",
            "        motion_target = vec3(attr_value(source_point, 'motion_target_position', None), fallback_motion)",
            "        facing_target = vec3(attr_value(source_point, 'facing_target_position', None), fallback_facing)",
            "        target_position = motion_target",
            "        move_heading = vec3(attr_value(source_point, 'move_heading', None), heading_to_target(position, motion_target, sit_heading))",
            "        heading = facing_heading_for_state(state, position, motion_target, facing_target, sit_heading)",
            "        orient = yaw_to_orient(heading)",
            "        distance = float(attr_value(source_point, 'distance_to_target', 0.0) or 0.0)",
            "        agents.append({'name': name, 'position': position, 'state': state, 'step': step, 'clip': clip, 'target': target, 'target_position': target_position, 'distance': distance})",
            "        p = geo.createPoint()",
            "        p.setPosition(position)",
            "        p.setAttribValue(name_attr, name)",
            "        p.setAttribValue(agentname_attr, name)",
            "        p.setAttribValue(agentid_attr, len(agents) - 1)",
            "        p.setAttribValue(entity_attr, 'agent')",
            "        p.setAttribValue(state_attr, state)",
            "        p.setAttribValue(crowd_state_attr, state)",
            "        p.setAttribValue(step_attr, step)",
            "        p.setAttribValue(clip_attr, clip)",
            "        p.setAttribValue(clipname_attr, clip)",
            "        p.setAttribValue(target_attr, target)",
            "        p.setAttribValue(target_pos_attr, target_position)",
            "        p.setAttribValue(motion_target_attr, motion_target)",
            "        p.setAttribValue(facing_target_attr, facing_target)",
            "        p.setAttribValue(move_heading_attr, move_heading)",
            "        p.setAttribValue(heading_attr, heading)",
            "        p.setAttribValue(orient_attr, orient)",
            "        p.setAttribValue(distance_attr, distance)",
            "        p.setAttribValue(pscale_attr, 0.16)",
            "        color = (1.0, 0.15, 0.1)",
            "        if state == 'sit_idle':",
            "            color = (0.1, 0.8, 0.25)",
            "        elif state == 'sitting_down':",
            "            color = (1.0, 0.45, 0.1)",
            "        elif state == 'no_available_interaction':",
            "            color = (0.55, 0.55, 0.55)",
            "        p.setAttribValue(cd_attr, color)",
            "clip_summary = ', '.join('{}:{}'.format(agent['name'], agent['clip'] or 'none') for agent in agents)",
            "state_summary = ', '.join('{}:{}'.format(agent['name'], agent['state']) for agent in agents)",
            "target_summary = ', '.join('{}:{}'.format(agent['name'], agent['target'] or 'none') for agent in agents)",
            "geo.addAttrib(hou.attribType.Global, 'driver_mode', '')",
            "geo.addAttrib(hou.attribType.Global, 'driver_source', '')",
            "geo.addAttrib(hou.attribType.Global, 'how_to_connect', '')",
            "geo.addAttrib(hou.attribType.Global, 'required_point_attributes', '')",
            "geo.addAttrib(hou.attribType.Global, 'agent_count', 0)",
            "geo.addAttrib(hou.attribType.Global, 'runtime_point_count', 0)",
            "geo.addAttrib(hou.attribType.Global, 'clip_summary', '')",
            "geo.addAttrib(hou.attribType.Global, 'state_summary', '')",
            "geo.addAttrib(hou.attribType.Global, 'target_summary', '')",
            "geo.setGlobalAttribValue('driver_mode', 'runtime_behavior_to_agent_clip_driver')",
            "geo.setGlobalAttribValue('driver_source', 'input_runtime_behavior' if runtime_geo is not None else 'missing_runtime_behavior')",
            "geo.setGlobalAttribValue('how_to_connect', 'Use OUT_BEHAVIOR_AGENT_POINTS as the behavior-side point driver. current_clip/clipname selects walk, sit_down, or sit_idle.')",
            "geo.setGlobalAttribValue('required_point_attributes', 'P, name, agentname, agentid, agent_state, state, current_step, current_clip, clipname, target_seat, target_position, motion_target_position, facing_target_position, move_heading, heading, orient, distance_to_target')",
            "geo.setGlobalAttribValue('agent_count', len(agents))",
            "geo.setGlobalAttribValue('runtime_point_count', runtime_point_count)",
            "geo.setGlobalAttribValue('clip_summary', clip_summary)",
            "geo.setGlobalAttribValue('state_summary', state_summary)",
            "geo.setGlobalAttribValue('target_summary', target_summary)",
        ]
    )


def _crowd_clip_state_driver_python_sop(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import hou",
            f"plan = {repr(plan)}",
            "node = hou.pwd()",
            "geo = node.geometry()",
            "inputs = node.inputs()",
            "source_geo = inputs[0].geometry() if inputs and inputs[0] is not None else None",
            "geo.clear()",
            "runtime = plan.get('runtime') or {}",
            "clip_to_index = {'walk': 0, 'sit_down': 1, 'sit_idle': 2}",
            "state_to_index = {",
            "    'walking_to_interaction': 0,",
            "    'aligning_to_interaction': 0,",
            "    'sitting_down': 1,",
            "    'sit_idle': 2,",
            "    'idle': -1,",
            "    'no_available_interaction': -1,",
            "}",
            "def attr_value(point, name, default=None):",
            "    attrib = point.geometry().findPointAttrib(name)",
            "    if attrib is None:",
            "        return default",
            "    try:",
            "        value = point.attribValue(attrib)",
            "    except Exception:",
            "        return default",
            "    return default if value is None else value",
            "def vec3(value, default=(0.0, 0.0, 0.0)):",
            "    if value is None:",
            "        return default",
            "    try:",
            "        return (float(value[0]), float(value[1]), float(value[2]))",
            "    except Exception:",
            "        return default",
            "def vec4(value, default=(0.0, 0.0, 0.0, 1.0)):",
            "    if value is None:",
            "        return default",
            "    try:",
            "        return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))",
            "    except Exception:",
            "        return default",
            "def choose_clip(state, clip):",
            "    if clip:",
            "        return clip",
            "    if state in ('walking_to_interaction', 'aligning_to_interaction'):",
            "        return 'walk'",
            "    if state == 'sitting_down':",
            "        return 'sit_down'",
            "    if state == 'sit_idle':",
            "        return 'sit_idle'",
            "    return ''",
            "def speed_for_state(state):",
            "    if state == 'walking_to_interaction':",
            "        return float(runtime.get('walk_speed', 0.0) or 0.0)",
            "    if state == 'aligning_to_interaction':",
            "        return 0.0",
            "    return 0.0",
            "name_attr = geo.addAttrib(hou.attribType.Point, 'name', '')",
            "agentname_attr = geo.addAttrib(hou.attribType.Point, 'agentname', '')",
            "agentid_attr = geo.addAttrib(hou.attribType.Point, 'agentid', 0)",
            "entity_attr = geo.addAttrib(hou.attribType.Point, 'entity_type', '')",
            "agent_state_attr = geo.addAttrib(hou.attribType.Point, 'agent_state', '')",
            "state_attr = geo.addAttrib(hou.attribType.Point, 'state', '')",
            "crowd_state_attr = geo.addAttrib(hou.attribType.Point, 'crowd_state', '')",
            "step_attr = geo.addAttrib(hou.attribType.Point, 'current_step', '')",
            "current_clip_attr = geo.addAttrib(hou.attribType.Point, 'current_clip', '')",
            "clipname_attr = geo.addAttrib(hou.attribType.Point, 'clipname', '')",
            "clip_attr = geo.addAttrib(hou.attribType.Point, 'clip', '')",
            "agentclip_attr = geo.addAttrib(hou.attribType.Point, 'agentclip', '')",
            "agent_clip_attr = geo.addAttrib(hou.attribType.Point, 'agent_clip', '')",
            "clip_index_attr = geo.addAttrib(hou.attribType.Point, 'clip_index', -1)",
            "state_index_attr = geo.addAttrib(hou.attribType.Point, 'state_index', -1)",
            "target_attr = geo.addAttrib(hou.attribType.Point, 'target_seat', '')",
            "target_pos_attr = geo.addAttrib(hou.attribType.Point, 'target_position', (0.0, 0.0, 0.0))",
            "motion_target_attr = geo.addAttrib(hou.attribType.Point, 'motion_target_position', (0.0, 0.0, 0.0))",
            "facing_target_attr = geo.addAttrib(hou.attribType.Point, 'facing_target_position', (0.0, 0.0, 0.0))",
            "move_heading_attr = geo.addAttrib(hou.attribType.Point, 'move_heading', (0.0, 0.0, 1.0))",
            "heading_attr = geo.addAttrib(hou.attribType.Point, 'heading', (0.0, 0.0, 1.0))",
            "orient_attr = geo.addAttrib(hou.attribType.Point, 'orient', (0.0, 0.0, 0.0, 1.0))",
            "distance_attr = geo.addAttrib(hou.attribType.Point, 'distance_to_target', 0.0)",
            "speed_attr = geo.addAttrib(hou.attribType.Point, 'speed', 0.0)",
            "v_attr = geo.addAttrib(hou.attribType.Point, 'v', (0.0, 0.0, 0.0))",
            "clip_loop_attr = geo.addAttrib(hou.attribType.Point, 'clip_loop', 0)",
            "clip_transition_attr = geo.addAttrib(hou.attribType.Point, 'clip_transition', '')",
            "ready_attr = geo.addAttrib(hou.attribType.Point, 'crowd_clip_ready', 0)",
            "pscale_attr = geo.addAttrib(hou.attribType.Point, 'pscale', 0.14)",
            "cd_attr = geo.addAttrib(hou.attribType.Point, 'Cd', (0.2, 0.8, 1.0))",
            "agents = []",
            "source_count = len(source_geo.points()) if source_geo is not None else 0",
            "if source_geo is not None:",
            "    for index, source_point in enumerate(source_geo.points()):",
            "        name = str(attr_value(source_point, 'name', '') or attr_value(source_point, 'agentname', '') or 'agent_{:03d}'.format(index + 1))",
            "        state = str(attr_value(source_point, 'agent_state', '') or attr_value(source_point, 'state', '') or 'idle')",
            "        step = str(attr_value(source_point, 'current_step', '') or '')",
            "        clip = choose_clip(state, str(attr_value(source_point, 'current_clip', '') or attr_value(source_point, 'clipname', '') or ''))",
            "        clip_index = int(clip_to_index.get(clip, -1))",
            "        state_index = int(state_to_index.get(state, -1))",
            "        speed = speed_for_state(state)",
            "        heading = vec3(attr_value(source_point, 'heading', (0.0, 0.0, 1.0)), (0.0, 0.0, 1.0))",
            "        move_heading = vec3(attr_value(source_point, 'move_heading', None), heading)",
            "        velocity = (move_heading[0] * speed, move_heading[1] * speed, move_heading[2] * speed)",
            "        target = str(attr_value(source_point, 'target_seat', '') or '')",
            "        target_position = vec3(attr_value(source_point, 'target_position', None), vec3(source_point.position()))",
            "        motion_target = vec3(attr_value(source_point, 'motion_target_position', None), target_position)",
            "        facing_target = vec3(attr_value(source_point, 'facing_target_position', None), target_position)",
            "        orient = vec4(attr_value(source_point, 'orient', None))",
            "        distance = float(attr_value(source_point, 'distance_to_target', 0.0) or 0.0)",
            "        loop = 1 if clip in ('walk', 'sit_idle') else 0",
            "        transition = '{}->{}'.format(state, clip or 'none')",
            "        p = geo.createPoint()",
            "        p.setPosition(source_point.position())",
            "        p.setAttribValue(name_attr, name)",
            "        p.setAttribValue(agentname_attr, name)",
            "        p.setAttribValue(agentid_attr, int(attr_value(source_point, 'agentid', index) or index))",
            "        p.setAttribValue(entity_attr, 'agent')",
            "        p.setAttribValue(agent_state_attr, state)",
            "        p.setAttribValue(state_attr, state)",
            "        p.setAttribValue(crowd_state_attr, state)",
            "        p.setAttribValue(step_attr, step)",
            "        p.setAttribValue(current_clip_attr, clip)",
            "        p.setAttribValue(clipname_attr, clip)",
            "        p.setAttribValue(clip_attr, clip)",
            "        p.setAttribValue(agentclip_attr, clip)",
            "        p.setAttribValue(agent_clip_attr, clip)",
            "        p.setAttribValue(clip_index_attr, clip_index)",
            "        p.setAttribValue(state_index_attr, state_index)",
            "        p.setAttribValue(target_attr, target)",
            "        p.setAttribValue(target_pos_attr, target_position)",
            "        p.setAttribValue(motion_target_attr, motion_target)",
            "        p.setAttribValue(facing_target_attr, facing_target)",
            "        p.setAttribValue(move_heading_attr, move_heading)",
            "        p.setAttribValue(heading_attr, heading)",
            "        p.setAttribValue(orient_attr, orient)",
            "        p.setAttribValue(distance_attr, distance)",
            "        p.setAttribValue(speed_attr, speed)",
            "        p.setAttribValue(v_attr, velocity)",
            "        p.setAttribValue(clip_loop_attr, loop)",
            "        p.setAttribValue(clip_transition_attr, transition)",
            "        p.setAttribValue(ready_attr, int(clip_index >= 0))",
            "        p.setAttribValue(pscale_attr, float(attr_value(source_point, 'pscale', 0.16) or 0.16))",
            "        p.setAttribValue(cd_attr, (0.2, 0.8, 1.0) if clip_index >= 0 else (0.55, 0.55, 0.55))",
            "        agents.append({'name': name, 'state': state, 'clip': clip, 'target': target, 'ready': clip_index >= 0})",
            "clip_summary = ', '.join('{}:{}'.format(agent['name'], agent['clip'] or 'none') for agent in agents)",
            "state_summary = ', '.join('{}:{}'.format(agent['name'], agent['state']) for agent in agents)",
            "ready_summary = ', '.join('{}:{}'.format(agent['name'], 'ready' if agent['ready'] else 'not_ready') for agent in agents)",
            "geo.addAttrib(hou.attribType.Global, 'driver_mode', '')",
            "geo.addAttrib(hou.attribType.Global, 'driver_source', '')",
            "geo.addAttrib(hou.attribType.Global, 'how_to_connect', '')",
            "geo.addAttrib(hou.attribType.Global, 'state_clip_map', '')",
            "geo.addAttrib(hou.attribType.Global, 'point_attributes', '')",
            "geo.addAttrib(hou.attribType.Global, 'agent_count', 0)",
            "geo.addAttrib(hou.attribType.Global, 'source_agent_count', 0)",
            "geo.addAttrib(hou.attribType.Global, 'ready_agent_count', 0)",
            "geo.addAttrib(hou.attribType.Global, 'clip_summary', '')",
            "geo.addAttrib(hou.attribType.Global, 'state_summary', '')",
            "geo.addAttrib(hou.attribType.Global, 'ready_summary', '')",
            "geo.setGlobalAttribValue('driver_mode', 'crowd_clip_state_driver')",
            "geo.setGlobalAttribValue('driver_source', 'behavior_agent_points' if source_geo is not None else 'missing_behavior_agent_points')",
            "geo.setGlobalAttribValue('how_to_connect', 'Use this as the Agent Clip/Crowd Solver handoff. clipname/agentclip/clip select the clip; state/crowd_state select the behavior state; P/orient/v drive placement and motion.')",
            "geo.setGlobalAttribValue('state_clip_map', 'walking_to_interaction:walk, aligning_to_interaction:walk, sitting_down:sit_down, sit_idle:sit_idle')",
            "geo.setGlobalAttribValue('point_attributes', 'P, name, agentname, agentid, state, crowd_state, current_clip, clipname, clip, agentclip, agent_clip, clip_index, state_index, target_seat, target_position, motion_target_position, facing_target_position, move_heading, heading, orient, speed, v, clip_loop, clip_transition, crowd_clip_ready')",
            "geo.setGlobalAttribValue('agent_count', len(agents))",
            "geo.setGlobalAttribValue('source_agent_count', source_count)",
            "geo.setGlobalAttribValue('ready_agent_count', sum(1 for agent in agents if agent['ready']))",
            "geo.setGlobalAttribValue('clip_summary', clip_summary)",
            "geo.setGlobalAttribValue('state_summary', state_summary)",
            "geo.setGlobalAttribValue('ready_summary', ready_summary)",
        ]
    )


def _agent_clip_bridge_python_sop(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import hou",
            f"plan = {repr(plan)}",
            "node = hou.pwd()",
            "geo = node.geometry()",
            "inputs = node.inputs()",
            "input0_geo = inputs[0].geometry() if len(inputs) > 0 and inputs[0] is not None else None",
            "input1_geo = inputs[1].geometry() if len(inputs) > 1 and inputs[1] is not None else None",
            "scaffold_geo = input0_geo if input1_geo is not None else None",
            "driver_geo = input1_geo if input1_geo is not None else input0_geo",
            "geo.clear()",
            "def attr_value(point, name, default=None):",
            "    attrib = point.geometry().findPointAttrib(name)",
            "    if attrib is None:",
            "        return default",
            "    try:",
            "        value = point.attribValue(attrib)",
            "    except Exception:",
            "        return default",
            "    return default if value is None else value",
            "def ensure_point_attrib(name, default):",
            "    attrib = geo.findPointAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Point, name, default)",
            "def ensure_global_attrib(name, default):",
            "    attrib = geo.findGlobalAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Global, name, default)",
            "def as_int(value, default=0):",
            "    try:",
            "        return int(value)",
            "    except Exception:",
            "        return int(default)",
            "def as_float(value, default=0.0):",
            "    try:",
            "        return float(value)",
            "    except Exception:",
            "        return float(default)",
            "def as_str(value, default=''):",
            "    if value is None:",
            "        return default",
            "    return str(value)",
            "def vec3(value, default=(0.0, 0.0, 0.0)):",
            "    if value is None:",
            "        return default",
            "    try:",
            "        return (float(value[0]), float(value[1]), float(value[2]))",
            "    except Exception:",
            "        return default",
            "def vec4(value, default=(0.0, 0.0, 0.0, 1.0)):",
            "    if value is None:",
            "        return default",
            "    try:",
            "        return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))",
            "    except Exception:",
            "        return default",
            "scaffold_count = len(scaffold_geo.points()) if scaffold_geo is not None else 0",
            "driver_count = len(driver_geo.points()) if driver_geo is not None else 0",
            "if scaffold_count:",
            "    geo.merge(scaffold_geo)",
            "elif driver_geo is not None:",
            "    geo.merge(driver_geo)",
            "driver_points = list(driver_geo.points()) if driver_geo is not None else []",
            "driver_by_id = {}",
            "driver_by_name = {}",
            "for index, driver in enumerate(driver_points):",
            "    agent_id = as_int(attr_value(driver, 'agentid', index), index)",
            "    driver_by_id[agent_id] = driver",
            "    name = as_str(attr_value(driver, 'name', '') or attr_value(driver, 'agentname', ''), '')",
            "    if name:",
            "        driver_by_name[name] = driver",
            "def driver_for_point(point, index):",
            "    agent_id = as_int(attr_value(point, 'agentid', index), index)",
            "    if agent_id in driver_by_id:",
            "        return driver_by_id[agent_id]",
            "    name = as_str(attr_value(point, 'name', '') or attr_value(point, 'agentname', ''), '')",
            "    if name and name in driver_by_name:",
            "        return driver_by_name[name]",
            "    if len(driver_points) == 1:",
            "        return driver_points[0]",
            "    return driver_points[index] if index < len(driver_points) else None",
            "attrs = {",
            "    'name': ensure_point_attrib('name', ''),",
            "    'agentname': ensure_point_attrib('agentname', ''),",
            "    'agentid': ensure_point_attrib('agentid', 0),",
            "    'agent_state': ensure_point_attrib('agent_state', ''),",
            "    'state': ensure_point_attrib('state', ''),",
            "    'crowd_state': ensure_point_attrib('crowd_state', ''),",
            "    'current_step': ensure_point_attrib('current_step', ''),",
            "    'current_clip': ensure_point_attrib('current_clip', ''),",
            "    'clipname': ensure_point_attrib('clipname', ''),",
            "    'clip': ensure_point_attrib('clip', ''),",
            "    'agentclip': ensure_point_attrib('agentclip', ''),",
            "    'agent_clip': ensure_point_attrib('agent_clip', ''),",
            "    'clip_index': ensure_point_attrib('clip_index', -1),",
            "    'state_index': ensure_point_attrib('state_index', -1),",
            "    'target_seat': ensure_point_attrib('target_seat', ''),",
            "    'target_position': ensure_point_attrib('target_position', (0.0, 0.0, 0.0)),",
            "    'motion_target_position': ensure_point_attrib('motion_target_position', (0.0, 0.0, 0.0)),",
            "    'facing_target_position': ensure_point_attrib('facing_target_position', (0.0, 0.0, 0.0)),",
            "    'move_heading': ensure_point_attrib('move_heading', (0.0, 0.0, 1.0)),",
            "    'heading': ensure_point_attrib('heading', (0.0, 0.0, 1.0)),",
            "    'orient': ensure_point_attrib('orient', (0.0, 0.0, 0.0, 1.0)),",
            "    'speed': ensure_point_attrib('speed', 0.0),",
            "    'v': ensure_point_attrib('v', (0.0, 0.0, 0.0)),",
            "    'clip_loop': ensure_point_attrib('clip_loop', 0),",
            "    'clip_transition': ensure_point_attrib('clip_transition', ''),",
            "    'crowd_clip_ready': ensure_point_attrib('crowd_clip_ready', 0),",
            "    'agent_clip_ready': ensure_point_attrib('agent_clip_ready', 0),",
            "    'agent_clip_bridge_source': ensure_point_attrib('agent_clip_bridge_source', ''),",
            "    'behavior_driver_clip': ensure_point_attrib('behavior_driver_clip', ''),",
            "    'behavior_driver_state': ensure_point_attrib('behavior_driver_state', ''),",
            "}",
            "ready_count = 0",
            "points = list(geo.points())",
            "for index, point in enumerate(points):",
            "    driver = driver_for_point(point, index)",
            "    if driver is None:",
            "        continue",
            "    name = as_str(attr_value(driver, 'name', '') or attr_value(driver, 'agentname', '') or 'agent_{:03d}'.format(index + 1))",
            "    agent_id = as_int(attr_value(driver, 'agentid', index), index)",
            "    state = as_str(attr_value(driver, 'state', '') or attr_value(driver, 'agent_state', ''), '')",
            "    crowd_state = as_str(attr_value(driver, 'crowd_state', state), state)",
            "    step = as_str(attr_value(driver, 'current_step', ''), '')",
            "    clip = as_str(attr_value(driver, 'clipname', '') or attr_value(driver, 'current_clip', '') or attr_value(driver, 'clip', ''), '')",
            "    clip_index = as_int(attr_value(driver, 'clip_index', -1), -1)",
            "    state_index = as_int(attr_value(driver, 'state_index', -1), -1)",
            "    target = as_str(attr_value(driver, 'target_seat', ''), '')",
            "    target_position = vec3(attr_value(driver, 'target_position', None), vec3(point.position()))",
            "    motion_target = vec3(attr_value(driver, 'motion_target_position', None), target_position)",
            "    facing_target = vec3(attr_value(driver, 'facing_target_position', None), target_position)",
            "    move_heading = vec3(attr_value(driver, 'move_heading', None), vec3(attr_value(driver, 'heading', (0.0, 0.0, 1.0)), (0.0, 0.0, 1.0)))",
            "    heading = vec3(attr_value(driver, 'heading', (0.0, 0.0, 1.0)), (0.0, 0.0, 1.0))",
            "    orient = vec4(attr_value(driver, 'orient', None))",
            "    speed = as_float(attr_value(driver, 'speed', 0.0), 0.0)",
            "    velocity = vec3(attr_value(driver, 'v', (0.0, 0.0, 0.0)), (0.0, 0.0, 0.0))",
            "    loop = as_int(attr_value(driver, 'clip_loop', 0), 0)",
            "    transition = as_str(attr_value(driver, 'clip_transition', ''), '')",
            "    is_ready = as_int(attr_value(driver, 'crowd_clip_ready', 0), 0)",
            "    point.setAttribValue(attrs['name'], name)",
            "    point.setAttribValue(attrs['agentname'], name)",
            "    point.setAttribValue(attrs['agentid'], agent_id)",
            "    point.setAttribValue(attrs['agent_state'], state)",
            "    point.setAttribValue(attrs['state'], state)",
            "    point.setAttribValue(attrs['crowd_state'], crowd_state)",
            "    point.setAttribValue(attrs['current_step'], step)",
            "    point.setAttribValue(attrs['current_clip'], clip)",
            "    point.setAttribValue(attrs['clipname'], clip)",
            "    point.setAttribValue(attrs['clip'], clip)",
            "    point.setAttribValue(attrs['agentclip'], clip)",
            "    point.setAttribValue(attrs['agent_clip'], clip)",
            "    point.setAttribValue(attrs['clip_index'], clip_index)",
            "    point.setAttribValue(attrs['state_index'], state_index)",
            "    point.setAttribValue(attrs['target_seat'], target)",
            "    point.setAttribValue(attrs['target_position'], target_position)",
            "    point.setAttribValue(attrs['motion_target_position'], motion_target)",
            "    point.setAttribValue(attrs['facing_target_position'], facing_target)",
            "    point.setAttribValue(attrs['move_heading'], move_heading)",
            "    point.setAttribValue(attrs['heading'], heading)",
            "    point.setAttribValue(attrs['orient'], orient)",
            "    point.setAttribValue(attrs['speed'], speed)",
            "    point.setAttribValue(attrs['v'], velocity)",
            "    point.setAttribValue(attrs['clip_loop'], loop)",
            "    point.setAttribValue(attrs['clip_transition'], transition)",
            "    point.setAttribValue(attrs['crowd_clip_ready'], is_ready)",
            "    point.setAttribValue(attrs['agent_clip_ready'], is_ready)",
            "    point.setAttribValue(attrs['agent_clip_bridge_source'], 'agent_scaffold' if scaffold_count else 'driver_points_fallback')",
            "    point.setAttribValue(attrs['behavior_driver_clip'], clip)",
            "    point.setAttribValue(attrs['behavior_driver_state'], state)",
            "    ready_count += 1 if is_ready else 0",
            "ensure_global_attrib('bridge_mode', '')",
            "ensure_global_attrib('bridge_source', '')",
            "ensure_global_attrib('how_to_connect', '')",
            "ensure_global_attrib('transferred_attributes', '')",
            "ensure_global_attrib('agent_count', 0)",
            "ensure_global_attrib('scaffold_point_count', 0)",
            "ensure_global_attrib('driver_point_count', 0)",
            "ensure_global_attrib('ready_agent_count', 0)",
            "ensure_global_attrib('next_step', '')",
            "geo.setGlobalAttribValue('bridge_mode', 'agent_clip_attribute_bridge')",
            "geo.setGlobalAttribValue('bridge_source', 'agent_scaffold_with_driver' if scaffold_count else 'crowd_clip_state_driver_only')",
            "geo.setGlobalAttribValue('how_to_connect', 'Feed OUT_AGENT_CLIP_BRIDGE to the first version-specific Agent Clip/Crowd Solver test. The behavior clip is available as clipname, clip, agentclip, and agent_clip.')",
            "geo.setGlobalAttribValue('transferred_attributes', 'clipname, clip, agentclip, agent_clip, state, crowd_state, current_clip, clip_index, state_index, target_seat, target_position, motion_target_position, facing_target_position, move_heading, heading, orient, speed, v, agent_clip_ready')",
            "geo.setGlobalAttribValue('agent_count', len(points))",
            "geo.setGlobalAttribValue('scaffold_point_count', scaffold_count)",
            "geo.setGlobalAttribValue('driver_point_count', driver_count)",
            "geo.setGlobalAttribValue('ready_agent_count', ready_count)",
            "geo.setGlobalAttribValue('next_step', 'Connect this bridge to Houdini Agent Clip/Crowd Solver nodes and test which clip attribute is consumed reliably.')",
        ]
    )


def _agent_crowd_behavior_python_sop(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import hou",
            f"plan = {repr(plan)}",
            "node = hou.pwd()",
            "geo = node.geometry()",
            "inputs = node.inputs()",
            "def input_geometry(index):",
            "    if len(inputs) <= index or inputs[index] is None:",
            "        return None",
            "    try:",
            "        return inputs[index].geometry()",
            "    except Exception:",
            "        return None",
            "scaffold_geo = input_geometry(0)",
            "driver_geo = input_geometry(1)",
            "geo.clear()",
            "scaffold_count = len(scaffold_geo.points()) if scaffold_geo is not None else 0",
            "driver_count = len(driver_geo.points()) if driver_geo is not None else 0",
            "if scaffold_geo is not None and scaffold_count:",
            "    geo.merge(scaffold_geo)",
            "elif driver_geo is not None:",
            "    geo.merge(driver_geo)",
            "def attr_value(point, name, default=None):",
            "    attrib = point.geometry().findPointAttrib(name)",
            "    if attrib is None:",
            "        return default",
            "    try:",
            "        value = point.attribValue(attrib)",
            "    except Exception:",
            "        return default",
            "    return default if value is None else value",
            "def ensure_point_attrib(name, default):",
            "    attrib = geo.findPointAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Point, name, default)",
            "def ensure_global_attrib(name, default):",
            "    attrib = geo.findGlobalAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Global, name, default)",
            "def safe_set(point, attrib, value):",
            "    try:",
            "        point.setAttribValue(attrib, value)",
            "        return True",
            "    except Exception:",
            "        return False",
            "def as_int(value, default=0):",
            "    try:",
            "        return int(value)",
            "    except Exception:",
            "        return int(default)",
            "def as_float(value, default=0.0):",
            "    try:",
            "        return float(value)",
            "    except Exception:",
            "        return float(default)",
            "def as_str(value, default=''):",
            "    if value is None:",
            "        return default",
            "    return str(value)",
            "def vec3(value, default=(0.0, 0.0, 0.0)):",
            "    if value is None:",
            "        return default",
            "    try:",
            "        return (float(value[0]), float(value[1]), float(value[2]))",
            "    except Exception:",
            "        return default",
            "def vec4(value, default=(0.0, 0.0, 0.0, 1.0)):",
            "    if value is None:",
            "        return default",
            "    try:",
            "        return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))",
            "    except Exception:",
            "        return default",
            "def primitive_type_name(prim):",
            "    try:",
            "        return prim.type().name()",
            "    except Exception:",
            "        return 'unknown'",
            "driver_points = list(driver_geo.points()) if driver_geo is not None else []",
            "driver_by_id = {}",
            "driver_by_name = {}",
            "for index, driver in enumerate(driver_points):",
            "    agent_id = as_int(attr_value(driver, 'agentid', index), index)",
            "    driver_by_id[agent_id] = driver",
            "    name = as_str(attr_value(driver, 'name', '') or attr_value(driver, 'agentname', ''), '')",
            "    if name:",
            "        driver_by_name[name] = driver",
            "def driver_for_point(point, index):",
            "    if driver_geo is None:",
            "        return point",
            "    agent_id = as_int(attr_value(point, 'agentid', index), index)",
            "    if agent_id in driver_by_id:",
            "        return driver_by_id[agent_id]",
            "    name = as_str(attr_value(point, 'name', '') or attr_value(point, 'agentname', ''), '')",
            "    if name and name in driver_by_name:",
            "        return driver_by_name[name]",
            "    if len(driver_points) == 1:",
            "        return driver_points[0]",
            "    return driver_points[index] if index < len(driver_points) else None",
            "attrs = {",
            "    'name': ensure_point_attrib('name', ''),",
            "    'agentname': ensure_point_attrib('agentname', ''),",
            "    'agentid': ensure_point_attrib('agentid', 0),",
            "    'entity_type': ensure_point_attrib('entity_type', ''),",
            "    'agent_state': ensure_point_attrib('agent_state', ''),",
            "    'state': ensure_point_attrib('state', ''),",
            "    'crowd_state': ensure_point_attrib('crowd_state', ''),",
            "    'current_step': ensure_point_attrib('current_step', ''),",
            "    'current_clip': ensure_point_attrib('current_clip', ''),",
            "    'clipname': ensure_point_attrib('clipname', ''),",
            "    'clip': ensure_point_attrib('clip', ''),",
            "    'agentclip': ensure_point_attrib('agentclip', ''),",
            "    'agent_clip': ensure_point_attrib('agent_clip', ''),",
            "    'clip_index': ensure_point_attrib('clip_index', -1),",
            "    'state_index': ensure_point_attrib('state_index', -1),",
            "    'target_seat': ensure_point_attrib('target_seat', ''),",
            "    'target_position': ensure_point_attrib('target_position', (0.0, 0.0, 0.0)),",
            "    'motion_target_position': ensure_point_attrib('motion_target_position', (0.0, 0.0, 0.0)),",
            "    'facing_target_position': ensure_point_attrib('facing_target_position', (0.0, 0.0, 0.0)),",
            "    'move_heading': ensure_point_attrib('move_heading', (0.0, 0.0, 1.0)),",
            "    'heading': ensure_point_attrib('heading', (0.0, 0.0, 1.0)),",
            "    'orient': ensure_point_attrib('orient', (0.0, 0.0, 0.0, 1.0)),",
            "    'speed': ensure_point_attrib('speed', 0.0),",
            "    'v': ensure_point_attrib('v', (0.0, 0.0, 0.0)),",
            "    'clip_loop': ensure_point_attrib('clip_loop', 0),",
            "    'clip_transition': ensure_point_attrib('clip_transition', ''),",
            "    'crowd_clip_ready': ensure_point_attrib('crowd_clip_ready', 0),",
            "    'agent_clip_ready': ensure_point_attrib('agent_clip_ready', 0),",
            "    'agent_crowd_ready': ensure_point_attrib('agent_crowd_ready', 0),",
            "    'agent_crowd_source': ensure_point_attrib('agent_crowd_source', ''),",
            "}",
            "valid_clips = ('walk', 'sit_down', 'sit_idle')",
            "points = list(geo.points())",
            "ready_count = 0",
            "clip_parts = []",
            "state_parts = []",
            "for index, point in enumerate(points):",
            "    driver = driver_for_point(point, index)",
            "    if driver is None:",
            "        continue",
            "    name = as_str(attr_value(driver, 'name', '') or attr_value(driver, 'agentname', '') or 'agent_{:03d}'.format(index + 1))",
            "    agent_id = as_int(attr_value(driver, 'agentid', index), index)",
            "    state = as_str(attr_value(driver, 'state', '') or attr_value(driver, 'agent_state', ''), '')",
            "    crowd_state = as_str(attr_value(driver, 'crowd_state', state), state)",
            "    step = as_str(attr_value(driver, 'current_step', ''), '')",
            "    clip = as_str(attr_value(driver, 'clipname', '') or attr_value(driver, 'current_clip', '') or attr_value(driver, 'clip', '') or attr_value(driver, 'agentclip', ''), '')",
            "    clip_index = as_int(attr_value(driver, 'clip_index', -1), -1)",
            "    state_index = as_int(attr_value(driver, 'state_index', -1), -1)",
            "    target = as_str(attr_value(driver, 'target_seat', ''), '')",
            "    position = vec3(attr_value(driver, 'P', None), vec3(point.position()))",
            "    target_position = vec3(attr_value(driver, 'target_position', None), position)",
            "    motion_target = vec3(attr_value(driver, 'motion_target_position', None), target_position)",
            "    facing_target = vec3(attr_value(driver, 'facing_target_position', None), target_position)",
            "    move_heading = vec3(attr_value(driver, 'move_heading', None), vec3(attr_value(driver, 'heading', (0.0, 0.0, 1.0)), (0.0, 0.0, 1.0)))",
            "    heading = vec3(attr_value(driver, 'heading', (0.0, 0.0, 1.0)), (0.0, 0.0, 1.0))",
            "    orient = vec4(attr_value(driver, 'orient', None))",
            "    speed = as_float(attr_value(driver, 'speed', 0.0), 0.0)",
            "    velocity = vec3(attr_value(driver, 'v', (0.0, 0.0, 0.0)), (0.0, 0.0, 0.0))",
            "    loop = as_int(attr_value(driver, 'clip_loop', 0), 0)",
            "    transition = as_str(attr_value(driver, 'clip_transition', ''), '')",
            "    is_ready = 1 if clip in valid_clips else as_int(attr_value(driver, 'crowd_clip_ready', 0), 0)",
            "    point.setPosition(position)",
            "    safe_set(point, attrs['name'], name)",
            "    safe_set(point, attrs['agentname'], name)",
            "    safe_set(point, attrs['agentid'], agent_id)",
            "    safe_set(point, attrs['entity_type'], 'agent')",
            "    safe_set(point, attrs['agent_state'], state)",
            "    safe_set(point, attrs['state'], state)",
            "    safe_set(point, attrs['crowd_state'], crowd_state)",
            "    safe_set(point, attrs['current_step'], step)",
            "    safe_set(point, attrs['current_clip'], clip)",
            "    safe_set(point, attrs['clipname'], clip)",
            "    safe_set(point, attrs['clip'], clip)",
            "    safe_set(point, attrs['agentclip'], clip)",
            "    safe_set(point, attrs['agent_clip'], clip)",
            "    safe_set(point, attrs['clip_index'], clip_index)",
            "    safe_set(point, attrs['state_index'], state_index)",
            "    safe_set(point, attrs['target_seat'], target)",
            "    safe_set(point, attrs['target_position'], target_position)",
            "    safe_set(point, attrs['motion_target_position'], motion_target)",
            "    safe_set(point, attrs['facing_target_position'], facing_target)",
            "    safe_set(point, attrs['move_heading'], move_heading)",
            "    safe_set(point, attrs['heading'], heading)",
            "    safe_set(point, attrs['orient'], orient)",
            "    safe_set(point, attrs['speed'], speed)",
            "    safe_set(point, attrs['v'], velocity)",
            "    safe_set(point, attrs['clip_loop'], loop)",
            "    safe_set(point, attrs['clip_transition'], transition)",
            "    safe_set(point, attrs['crowd_clip_ready'], is_ready)",
            "    safe_set(point, attrs['agent_clip_ready'], is_ready)",
            "    safe_set(point, attrs['agent_crowd_ready'], is_ready)",
            "    safe_set(point, attrs['agent_crowd_source'], 'agent_primitive_scaffold' if scaffold_count else 'driver_points_fallback')",
            "    ready_count += 1 if is_ready else 0",
            "    clip_parts.append('{}:{}'.format(name, clip or 'none'))",
            "    state_parts.append('{}:{}'.format(name, state or 'unknown'))",
            "agent_primitive_count = sum(1 for prim in geo.prims() if 'agent' in primitive_type_name(prim).lower())",
            "primitive_types = []",
            "for prim in geo.prims():",
            "    name = primitive_type_name(prim)",
            "    if name not in primitive_types:",
            "        primitive_types.append(name)",
            "ensure_global_attrib('agent_crowd_mode', '')",
            "ensure_global_attrib('agent_crowd_source', '')",
            "ensure_global_attrib('display_node', '')",
            "ensure_global_attrib('how_to_check', '')",
            "ensure_global_attrib('point_attributes', '')",
            "ensure_global_attrib('agent_count', 0)",
            "ensure_global_attrib('ready_agent_count', 0)",
            "ensure_global_attrib('agent_primitive_count', 0)",
            "ensure_global_attrib('primitive_types', '')",
            "ensure_global_attrib('scaffold_point_count', 0)",
            "ensure_global_attrib('driver_point_count', 0)",
            "ensure_global_attrib('clip_summary', '')",
            "ensure_global_attrib('state_summary', '')",
            "ensure_global_attrib('next_step', '')",
            "source = 'agent_primitive_scaffold' if scaffold_count else 'driver_points_fallback'",
            "status = 'agent_crowd_behavior_runtime' if agent_primitive_count else 'agent_crowd_behavior_driver_fallback'",
            "geo.setGlobalAttribValue('agent_crowd_mode', status)",
            "geo.setGlobalAttribValue('agent_crowd_source', source)",
            "geo.setGlobalAttribValue('display_node', 'OUT_AGENT_CROWD_BEHAVIOR')",
            "geo.setGlobalAttribValue('how_to_check', 'Display OUT_AGENT_CROWD_BEHAVIOR for Agent primitive, or OUT_AGENT_CROWD_BEHAVIOR_UNPACKED for mesh inspection. Detail attributes show source, clips, and primitive counts.')",
            "geo.setGlobalAttribValue('point_attributes', 'P, name, agentname, agentid, state, crowd_state, current_clip, clipname, clip, agentclip, agent_clip, target_seat, motion_target_position, facing_target_position, move_heading, heading, orient, speed, v, agent_crowd_ready')",
            "geo.setGlobalAttribValue('agent_count', len(points))",
            "geo.setGlobalAttribValue('ready_agent_count', ready_count)",
            "geo.setGlobalAttribValue('agent_primitive_count', agent_primitive_count)",
            "geo.setGlobalAttribValue('primitive_types', ', '.join(primitive_types) or 'none')",
            "geo.setGlobalAttribValue('scaffold_point_count', scaffold_count)",
            "geo.setGlobalAttribValue('driver_point_count', driver_count)",
            "geo.setGlobalAttribValue('clip_summary', ', '.join(clip_parts) or 'none')",
            "geo.setGlobalAttribValue('state_summary', ', '.join(state_parts) or 'none')",
            "geo.setGlobalAttribValue('next_step', 'Use OUT_AGENT_CROWD_BEHAVIOR as the Agent/Crowd-side behavior output. Keep OUT_AGENT_CLIP_BRIDGE only as the point-driver fallback.')",
        ]
    )


def _agent_crowd_visual_diagnostic_python_sop() -> str:
    return "\n".join(
        [
            "import hou",
            "node = hou.pwd()",
            "geo = node.geometry()",
            "inputs = node.inputs()",
            "source_geo = inputs[0].geometry() if inputs and inputs[0] is not None else None",
            "geo.clear()",
            "if source_geo is not None:",
            "    geo.merge(source_geo)",
            "def ensure_global_attrib(name, default):",
            "    attrib = geo.findGlobalAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Global, name, default)",
            "def intrinsic_names(prim):",
            "    for method_name in ('intrinsicNames', 'intrinsicValueNames'):",
            "        method = getattr(prim, method_name, None)",
            "        if method is None:",
            "            continue",
            "        try:",
            "            return list(method())",
            "        except Exception:",
            "            continue",
            "    return []",
            "def intrinsic_value(prim, name):",
            "    try:",
            "        return prim.intrinsicValue(name)",
            "    except Exception as exc:",
            "        return '<{}>'.format(type(exc).__name__)",
            "def flatten_values(value, out):",
            "    if isinstance(value, (tuple, list)):",
            "        for item in value:",
            "            flatten_values(item, out)",
            "        return",
            "    text = str(value or '').strip()",
            "    if text and text not in out:",
            "        out.append(text)",
            "def primitive_type_name(prim):",
            "    try:",
            "        return prim.type().name()",
            "    except Exception:",
            "        return 'unknown'",
            "def is_collision_or_proxy(text):",
            "    lower = str(text or '').lower()",
            "    return any(token in lower for token in ('collision', 'proxy', 'subnet_proxy', 'geo_proxy', 'guide', 'standin', 'stand-in', 'capsule', 'sphere'))",
            "def is_numeric_text(text):",
            "    try:",
            "        float(str(text or '').strip())",
            "        return True",
            "    except Exception:",
            "        return False",
            "def is_visual_candidate(text):",
            "    lower = str(text or '').strip().lower()",
            "    if not lower or lower in ('none', 'default', '[]', '{}'):",
            "        return False",
            "    if lower in ('interpolate', 'constant', 'deform', 'rigid') or is_numeric_text(lower):",
            "        return False",
            "    if lower.startswith('<') or is_collision_or_proxy(lower):",
            "        return False",
            "    return True",
            "primitive_types = []",
            "agent_primitive_count = 0",
            "shape_names = []",
            "layer_names = []",
            "current_layer_values = []",
            "shape_intrinsic_names = []",
            "layer_intrinsic_names = []",
            "if source_geo is not None:",
            "    for prim in source_geo.prims():",
            "        prim_type = primitive_type_name(prim)",
            "        if prim_type not in primitive_types:",
            "            primitive_types.append(prim_type)",
            "        if 'agent' in prim_type.lower():",
            "            agent_primitive_count += 1",
            "        for intrinsic_name in intrinsic_names(prim):",
            "            lower = intrinsic_name.lower()",
            "            if 'bounds' in lower or 'clip' in lower:",
            "                continue",
            "            if not any(token in lower for token in ('agent', 'shape', 'layer', 'display', 'render')):",
            "                continue",
            "            values = []",
            "            flatten_values(intrinsic_value(prim, intrinsic_name), values)",
            "            if 'shape' in lower:",
            "                if intrinsic_name not in shape_intrinsic_names:",
            "                    shape_intrinsic_names.append(intrinsic_name)",
            "                for value in values:",
            "                    if value not in shape_names:",
            "                        shape_names.append(value)",
            "            if 'layer' in lower or 'display' in lower or 'render' in lower:",
            "                if intrinsic_name not in layer_intrinsic_names:",
            "                    layer_intrinsic_names.append(intrinsic_name)",
            "                for value in values:",
            "                    if 'current' in lower:",
            "                        if value not in current_layer_values:",
            "                            current_layer_values.append(value)",
            "                    elif value not in layer_names:",
            "                        layer_names.append(value)",
            "visual_candidates = [value for value in shape_names + layer_names if is_visual_candidate(value)]",
            "collision_or_proxy_count = sum(1 for value in shape_names + layer_names if is_collision_or_proxy(value))",
            "if source_geo is None:",
            "    visual_status = 'missing_agent_crowd_behavior_input'",
            "elif agent_primitive_count <= 0:",
            "    visual_status = 'missing_agent_primitive'",
            "elif visual_candidates:",
            "    visual_status = 'agent_visual_shape_candidates_found'",
            "elif collision_or_proxy_count > 0:",
            "    visual_status = 'agent_collision_or_proxy_only'",
            "else:",
            "    visual_status = 'agent_visual_layer_unknown'",
            "if visual_status == 'agent_visual_shape_candidates_found':",
            "    next_step = 'Set the Agent display/current layer to one of visual_shape_candidates, then display OUT_AGENT_CROWD_BEHAVIOR_UNPACKED again.'",
            "elif visual_status == 'agent_collision_or_proxy_only':",
            "    next_step = 'The Agent contains only collision/proxy shapes. Inspect OUT_AGENT_DEFINITION_PARAMETER_DIAGNOSTIC. If no mesh import option or visual layer appears, re-export/build the Agent definition with the skinned render mesh/shape layer from character.fbx.'",
            "else:",
            "    next_step = 'Inspect agent_definition parameters and character.fbx export. The Agent primitive exists, but a displayable mesh layer was not identified.'",
            "ensure_global_attrib('agent_crowd_visual_status', '')",
            "ensure_global_attrib('agent_primitive_count', 0)",
            "ensure_global_attrib('point_count', 0)",
            "ensure_global_attrib('primitive_types', '')",
            "ensure_global_attrib('shape_intrinsic_names', '')",
            "ensure_global_attrib('layer_intrinsic_names', '')",
            "ensure_global_attrib('agent_shape_names', '')",
            "ensure_global_attrib('agent_layer_names', '')",
            "ensure_global_attrib('current_layer_values', '')",
            "ensure_global_attrib('visual_shape_candidates', '')",
            "ensure_global_attrib('collision_or_proxy_shape_count', 0)",
            "ensure_global_attrib('how_to_check', '')",
            "ensure_global_attrib('next_step', '')",
            "geo.setGlobalAttribValue('agent_crowd_visual_status', visual_status)",
            "geo.setGlobalAttribValue('agent_primitive_count', agent_primitive_count)",
            "geo.setGlobalAttribValue('point_count', len(geo.points()))",
            "geo.setGlobalAttribValue('primitive_types', ', '.join(primitive_types) or 'none')",
            "geo.setGlobalAttribValue('shape_intrinsic_names', ', '.join(shape_intrinsic_names) or 'none')",
            "geo.setGlobalAttribValue('layer_intrinsic_names', ', '.join(layer_intrinsic_names) or 'none')",
            "geo.setGlobalAttribValue('agent_shape_names', ', '.join(shape_names[:60]) or 'none')",
            "geo.setGlobalAttribValue('agent_layer_names', ', '.join(layer_names[:60]) or 'none')",
            "geo.setGlobalAttribValue('current_layer_values', ', '.join(current_layer_values[:20]) or 'none')",
            "geo.setGlobalAttribValue('visual_shape_candidates', ', '.join(visual_candidates[:40]) or 'none')",
            "geo.setGlobalAttribValue('collision_or_proxy_shape_count', int(collision_or_proxy_count))",
            "geo.setGlobalAttribValue('how_to_check', 'Display OUT_AGENT_CROWD_VISUAL_DIAGNOSTIC and inspect Detail attributes: agent_crowd_visual_status, visual_shape_candidates, agent_shape_names, and current_layer_values.')",
            "geo.setGlobalAttribValue('next_step', next_step)",
        ]
    )


def _agent_definition_parameter_diagnostic_python_sop(agent_path: str) -> str:
    return "\n".join(
        [
            "import hou",
            f"agent_path = {repr(agent_path)}",
            "geo = hou.pwd().geometry()",
            "geo.clear()",
            "agent = hou.node(agent_path) if agent_path else None",
            "def ensure_global_attrib(name, default):",
            "    attrib = geo.findGlobalAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Global, name, default)",
            "def compact(text, limit=900):",
            "    text = str(text or '')",
            "    return text if len(text) <= limit else text[:limit - 3] + '...'",
            "def menu_items(parm):",
            "    try:",
            "        template = parm.parmTemplate()",
            "        items = list(template.menuItems() or [])",
            "        labels = list(template.menuLabels() or [])",
            "    except Exception:",
            "        return []",
            "    values = []",
            "    for index, item in enumerate(items):",
            "        label = labels[index] if index < len(labels) else ''",
            "        values.append('{}:{}'.format(item, label) if label else str(item))",
            "    return values",
            "def is_visual_parm(parm):",
            "    try:",
            "        template = parm.parmTemplate()",
            "        text = '{} {}'.format(parm.name(), template.label()).lower()",
            "    except Exception:",
            "        return False",
            "    if not any(token in text for token in ('layer', 'shape', 'display', 'render', 'visual', 'geometry', 'mesh')):",
            "        return False",
            "    if any(token in text for token in ('collisionlayer', 'collision layer')):",
            "        return False",
            "    return True",
            "status = 'missing_agent_definition_node' if agent is None else 'agent_definition_parameters_inspected'",
            "visual_parameters = []",
            "visual_values = []",
            "visual_items = []",
            "if agent is not None:",
            "    try:",
            "        parms = list(agent.parms())",
            "    except Exception:",
            "        parms = []",
            "    for parm in parms:",
            "        if not is_visual_parm(parm):",
            "            continue",
            "        try:",
            "            value = parm.eval()",
            "        except Exception:",
            "            value = '<unreadable>'",
            "        items = menu_items(parm)",
            "        visual_parameters.append(parm.name())",
            "        visual_values.append('{}={}'.format(parm.name(), value))",
            "        if items:",
            "            visual_items.append('{} [{}]'.format(parm.name(), ', '.join(items[:30])))",
            "auto_status = agent.userData('smart_crowd_visual_auto_select_status') if agent is not None else ''",
            "auto_selected = agent.userData('smart_crowd_visual_auto_selected_parameters') if agent is not None else ''",
            "auto_candidates = agent.userData('smart_crowd_visual_auto_candidate_items') if agent is not None else ''",
            "mesh_import_status = agent.userData('smart_crowd_mesh_import_option_status') if agent is not None else ''",
            "mesh_import_enabled = agent.userData('smart_crowd_mesh_import_enabled_parameters') if agent is not None else ''",
            "mesh_import_candidates = agent.userData('smart_crowd_mesh_import_candidate_parameters') if agent is not None else ''",
            "reload_status = agent.userData('smart_crowd_agent_reload_status') if agent is not None else ''",
            "reload_pressed = agent.userData('smart_crowd_agent_reload_pressed_parameters') if agent is not None else ''",
            "reload_candidates = agent.userData('smart_crowd_agent_reload_candidate_parameters') if agent is not None else ''",
            "if auto_status == 'selected_visual_layer':",
            "    next_step = 'Display OUT_AGENT_CROWD_BEHAVIOR_UNPACKED. The Agent visual/display layer was auto-selected where a render/mesh candidate was available.'",
            "elif reload_status == 'pressed_reload':",
            "    next_step = 'Agent Reload was pressed after mesh/deforming import options were enabled. Inspect OUT_AGENT_CROWD_VISUAL_DIAGNOSTIC again; if only Default/Collision remain, rebuild the Agent definition from an FBX that exports the skinned mesh.'",
            "elif mesh_import_status == 'enabled_mesh_import_options':",
            "    next_step = 'Mesh/deforming shape import options were enabled, but Reload was not confirmed. Press Reload on agent_definition, then inspect OUT_AGENT_CROWD_VISUAL_DIAGNOSTIC again.'",
            "elif visual_items:",
            "    next_step = 'No strong render/mesh layer was auto-selected. Inspect visual_layer_menu_items and set agent_definition display/current/render layer manually if a real mesh option is listed.'",
            "else:",
            "    next_step = 'No Agent visual layer menu was found. Rebuild the Agent definition with a skinned render mesh shape layer from character.fbx.'",
            "ensure_global_attrib('agent_definition_parameter_status', '')",
            "ensure_global_attrib('agent_node_path', '')",
            "ensure_global_attrib('visual_auto_select_status', '')",
            "ensure_global_attrib('visual_auto_select_selected_parameters', '')",
            "ensure_global_attrib('visual_auto_select_candidate_items', '')",
            "ensure_global_attrib('mesh_import_option_status', '')",
            "ensure_global_attrib('mesh_import_enabled_parameters', '')",
            "ensure_global_attrib('mesh_import_candidate_parameters', '')",
            "ensure_global_attrib('agent_reload_status', '')",
            "ensure_global_attrib('agent_reload_pressed_parameters', '')",
            "ensure_global_attrib('agent_reload_candidate_parameters', '')",
            "ensure_global_attrib('visual_layer_menu_parameters', '')",
            "ensure_global_attrib('visual_layer_menu_values', '')",
            "ensure_global_attrib('visual_layer_menu_items', '')",
            "ensure_global_attrib('display_node', '')",
            "ensure_global_attrib('next_step', '')",
            "geo.setGlobalAttribValue('agent_definition_parameter_status', status)",
            "geo.setGlobalAttribValue('agent_node_path', agent_path)",
            "geo.setGlobalAttribValue('visual_auto_select_status', auto_status or 'not_attempted_or_no_candidate')",
            "geo.setGlobalAttribValue('visual_auto_select_selected_parameters', auto_selected or 'none')",
            "geo.setGlobalAttribValue('visual_auto_select_candidate_items', compact(auto_candidates or 'none'))",
            "geo.setGlobalAttribValue('mesh_import_option_status', mesh_import_status or 'not_attempted')",
            "geo.setGlobalAttribValue('mesh_import_enabled_parameters', mesh_import_enabled or 'none')",
            "geo.setGlobalAttribValue('mesh_import_candidate_parameters', mesh_import_candidates or 'none')",
            "geo.setGlobalAttribValue('agent_reload_status', reload_status or 'not_attempted')",
            "geo.setGlobalAttribValue('agent_reload_pressed_parameters', reload_pressed or 'none')",
            "geo.setGlobalAttribValue('agent_reload_candidate_parameters', reload_candidates or 'none')",
            "geo.setGlobalAttribValue('visual_layer_menu_parameters', ', '.join(visual_parameters) or 'none')",
            "geo.setGlobalAttribValue('visual_layer_menu_values', compact(', '.join(visual_values) or 'none'))",
            "geo.setGlobalAttribValue('visual_layer_menu_items', compact('; '.join(visual_items) or 'none'))",
            "geo.setGlobalAttribValue('display_node', 'OUT_AGENT_DEFINITION_PARAMETER_DIAGNOSTIC')",
            "geo.setGlobalAttribValue('next_step', next_step)",
        ]
    )


def _agent_clip_experiment_python_sop(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import hou",
            f"plan = {repr(plan)}",
            "node = hou.pwd()",
            "geo = node.geometry()",
            "inputs = node.inputs()",
            "source_geo = inputs[0].geometry() if inputs and inputs[0] is not None else None",
            "geo.clear()",
            "if source_geo is not None:",
            "    geo.merge(source_geo)",
            "def attr_value(point, name, default=None):",
            "    attrib = point.geometry().findPointAttrib(name)",
            "    if attrib is None:",
            "        return default",
            "    try:",
            "        value = point.attribValue(attrib)",
            "    except Exception:",
            "        return default",
            "    return default if value is None else value",
            "def ensure_point_attrib(name, default):",
            "    attrib = geo.findPointAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Point, name, default)",
            "def ensure_global_attrib(name, default):",
            "    attrib = geo.findGlobalAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Global, name, default)",
            "def node_type_candidates(type_names):",
            "    category = hou.sopNodeTypeCategory()",
            "    available = []",
            "    missing = []",
            "    for type_name in type_names:",
            "        try:",
            "            found = hou.nodeType(category, type_name) is not None",
            "        except Exception:",
            "            found = False",
            "        if found:",
            "            available.append(type_name)",
            "        else:",
            "            missing.append(type_name)",
            "    return available, missing",
            "agent_clip_available, agent_clip_missing = node_type_candidates(('agentclip', 'crowd::agentclip', 'agentcliptransition', 'crowd::agentcliptransition'))",
            "clip_locomotion_available, clip_locomotion_missing = node_type_candidates(('agentcliplocomotion', 'clip_locomotion', 'agentclipproperties'))",
            "crowd_solver_available, crowd_solver_missing = node_type_candidates(('crowdsolver', 'crowd::crowdsolver', 'crowdsource', 'crowd::crowdsource'))",
            "clip_attr = ensure_point_attrib('agent_clip_test_clip_attr', '')",
            "state_attr = ensure_point_attrib('agent_clip_test_state_attr', '')",
            "ready_attr = ensure_point_attrib('agent_clip_test_ready', 0)",
            "candidate_attr = ensure_point_attrib('agent_clip_test_candidates', '')",
            "points = list(geo.points())",
            "ready_count = 0",
            "clip_names = []",
            "for point in points:",
            "    clip = str(attr_value(point, 'clipname', '') or attr_value(point, 'agentclip', '') or attr_value(point, 'clip', '') or attr_value(point, 'current_clip', '') or '')",
            "    state = str(attr_value(point, 'state', '') or attr_value(point, 'crowd_state', '') or attr_value(point, 'agent_state', '') or '')",
            "    is_ready = 1 if clip else 0",
            "    point.setAttribValue(clip_attr, 'clipname')",
            "    point.setAttribValue(state_attr, 'state')",
            "    point.setAttribValue(ready_attr, is_ready)",
            "    point.setAttribValue(candidate_attr, 'clipname, agentclip, clip, agent_clip, current_clip, clip_index')",
            "    ready_count += is_ready",
            "    if clip and clip not in clip_names:",
            "        clip_names.append(clip)",
            "ensure_global_attrib('experiment_mode', '')",
            "ensure_global_attrib('experiment_source', '')",
            "ensure_global_attrib('no_agent_nodes_created', 0)",
            "ensure_global_attrib('available_agent_clip_nodes', '')",
            "ensure_global_attrib('missing_agent_clip_nodes', '')",
            "ensure_global_attrib('available_clip_locomotion_nodes', '')",
            "ensure_global_attrib('available_crowd_solver_nodes', '')",
            "ensure_global_attrib('recommended_clip_attribute', '')",
            "ensure_global_attrib('recommended_state_attribute', '')",
            "ensure_global_attrib('candidate_clip_attributes', '')",
            "ensure_global_attrib('candidate_state_attributes', '')",
            "ensure_global_attrib('candidate_motion_attributes', '')",
            "ensure_global_attrib('detected_clips', '')",
            "ensure_global_attrib('safe_node_test_input', '')",
            "ensure_global_attrib('test_nodes_are_bypassed', 0)",
            "ensure_global_attrib('agent_count', 0)",
            "ensure_global_attrib('ready_agent_count', 0)",
            "ensure_global_attrib('next_connection_step', '')",
            "geo.setGlobalAttribValue('experiment_mode', 'agent_clip_connection_probe')",
            "geo.setGlobalAttribValue('experiment_source', 'OUT_AGENT_CLIP_BRIDGE')",
            "geo.setGlobalAttribValue('no_agent_nodes_created', 1)",
            "geo.setGlobalAttribValue('available_agent_clip_nodes', ', '.join(agent_clip_available) or 'none')",
            "geo.setGlobalAttribValue('missing_agent_clip_nodes', ', '.join(agent_clip_missing) or 'none')",
            "geo.setGlobalAttribValue('available_clip_locomotion_nodes', ', '.join(clip_locomotion_available) or 'none')",
            "geo.setGlobalAttribValue('available_crowd_solver_nodes', ', '.join(crowd_solver_available) or 'none')",
            "geo.setGlobalAttribValue('recommended_clip_attribute', 'clipname')",
            "geo.setGlobalAttribValue('recommended_state_attribute', 'state')",
            "geo.setGlobalAttribValue('candidate_clip_attributes', 'clipname, agentclip, clip, agent_clip, current_clip, clip_index')",
            "geo.setGlobalAttribValue('candidate_state_attributes', 'state, crowd_state, agent_state, state_index')",
            "geo.setGlobalAttribValue('candidate_motion_attributes', 'P, orient, heading, v, speed, target_position')",
            "geo.setGlobalAttribValue('detected_clips', ', '.join(clip_names) or 'none')",
            "geo.setGlobalAttribValue('safe_node_test_input', 'OUT_AGENT_CLIP_NODE_TEST_INPUT')",
            "geo.setGlobalAttribValue('test_nodes_are_bypassed', 1)",
            "geo.setGlobalAttribValue('agent_count', len(points))",
            "geo.setGlobalAttribValue('ready_agent_count', ready_count)",
            "geo.setGlobalAttribValue('next_connection_step', 'Create a separate Agent Clip node test using OUT_AGENT_CLIP_EXPERIMENT as input; try clipname first, then agentclip/clip/clip_index if needed.')",
        ]
    )


def _agent_clip_node_test_result_python_sop(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import hou",
            f"plan = {repr(plan)}",
            "node = hou.pwd()",
            "parent = node.parent()",
            "geo = node.geometry()",
            "inputs = node.inputs()",
            "source_geo = inputs[0].geometry() if inputs and inputs[0] is not None else None",
            "geo.clear()",
            "if source_geo is not None:",
            "    geo.merge(source_geo)",
            "def attr_value(point, name, default=None):",
            "    attrib = point.geometry().findPointAttrib(name)",
            "    if attrib is None:",
            "        return default",
            "    try:",
            "        value = point.attribValue(attrib)",
            "    except Exception:",
            "        return default",
            "    return default if value is None else value",
            "def ensure_point_attrib(name, default):",
            "    attrib = geo.findPointAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Point, name, default)",
            "def ensure_global_attrib(name, default):",
            "    attrib = geo.findGlobalAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Global, name, default)",
            "def is_bypassed(test_node):",
            "    if test_node is None:",
            "        return False",
            "    for method_name in ('isBypassed', 'bypass'):",
            "        method = getattr(test_node, method_name, None)",
            "        if method is None:",
            "            continue",
            "        try:",
            "            value = method()",
            "        except TypeError:",
            "            continue",
            "        except Exception:",
            "            continue",
            "        return bool(value)",
            "    return False",
            "test_node_names = ('TEST_AGENTCLIP_WALK', 'TEST_AGENTCLIP_SIT_DOWN', 'TEST_AGENTCLIP_SIT_IDLE', 'TEST_CLIP_LOCOMOTION', 'TEST_CROWD_SOURCE', 'TEST_CROWD_SOLVER')",
            "existing_test_nodes = []",
            "bypassed_test_nodes = []",
            "for test_name in test_node_names:",
            "    test_node = parent.node(test_name) if parent is not None else None",
            "    if test_node is None:",
            "        continue",
            "    existing_test_nodes.append(test_name)",
            "    if is_bypassed(test_node):",
            "        bypassed_test_nodes.append(test_name)",
            "clip_attr = ensure_point_attrib('node_test_clip_attribute', '')",
            "state_attr = ensure_point_attrib('node_test_state_attribute', '')",
            "ready_attr = ensure_point_attrib('node_test_ready', 0)",
            "wire_attr = ensure_point_attrib('node_test_manual_wire', '')",
            "points = list(geo.points())",
            "ready_count = 0",
            "clip_names = []",
            "state_names = []",
            "for point in points:",
            "    clip = str(attr_value(point, 'clipname', '') or attr_value(point, 'agentclip', '') or attr_value(point, 'clip', '') or attr_value(point, 'current_clip', '') or '')",
            "    state = str(attr_value(point, 'state', '') or attr_value(point, 'crowd_state', '') or attr_value(point, 'agent_state', '') or '')",
            "    ready = 1 if clip else 0",
            "    point.setAttribValue(clip_attr, 'clipname')",
            "    point.setAttribValue(state_attr, 'state')",
            "    point.setAttribValue(ready_attr, ready)",
            "    point.setAttribValue(wire_attr, 'OUT_AGENT_CLIP_NODE_TEST_INPUT -> TEST_AGENTCLIP_*')",
            "    ready_count += ready",
            "    if clip and clip not in clip_names:",
            "        clip_names.append(clip)",
            "    if state and state not in state_names:",
            "        state_names.append(state)",
            "ensure_global_attrib('node_test_result_mode', '')",
            "ensure_global_attrib('input_source', '')",
            "ensure_global_attrib('no_agent_nodes_cooked', 0)",
            "ensure_global_attrib('recommended_first_input', '')",
            "ensure_global_attrib('recommended_clip_attribute', '')",
            "ensure_global_attrib('recommended_state_attribute', '')",
            "ensure_global_attrib('recommended_motion_attributes', '')",
            "ensure_global_attrib('created_test_nodes', '')",
            "ensure_global_attrib('bypassed_test_nodes', '')",
            "ensure_global_attrib('test_nodes_expected_bypassed', 0)",
            "ensure_global_attrib('manual_wire_order', '')",
            "ensure_global_attrib('agent_count', 0)",
            "ensure_global_attrib('ready_agent_count', 0)",
            "ensure_global_attrib('detected_clips', '')",
            "ensure_global_attrib('detected_states', '')",
            "ensure_global_attrib('result_status', '')",
            "ensure_global_attrib('next_step', '')",
            "geo.setGlobalAttribValue('node_test_result_mode', 'agent_clip_node_test_result')",
            "geo.setGlobalAttribValue('input_source', 'OUT_AGENT_CLIP_NODE_TEST_INPUT')",
            "geo.setGlobalAttribValue('no_agent_nodes_cooked', 1)",
            "geo.setGlobalAttribValue('recommended_first_input', 'OUT_AGENT_CLIP_NODE_TEST_INPUT')",
            "geo.setGlobalAttribValue('recommended_clip_attribute', 'clipname')",
            "geo.setGlobalAttribValue('recommended_state_attribute', 'state')",
            "geo.setGlobalAttribValue('recommended_motion_attributes', 'P, orient, v, speed')",
            "geo.setGlobalAttribValue('created_test_nodes', ', '.join(existing_test_nodes) or 'none')",
            "geo.setGlobalAttribValue('bypassed_test_nodes', ', '.join(bypassed_test_nodes) or 'none')",
            "geo.setGlobalAttribValue('test_nodes_expected_bypassed', 1)",
            "geo.setGlobalAttribValue('manual_wire_order', 'OUT_AGENT_CLIP_NODE_TEST_INPUT -> TEST_AGENTCLIP_* -> TEST_CLIP_LOCOMOTION -> TEST_CROWD_SOURCE/TEST_CROWD_SOLVER')",
            "geo.setGlobalAttribValue('agent_count', len(points))",
            "geo.setGlobalAttribValue('ready_agent_count', ready_count)",
            "geo.setGlobalAttribValue('detected_clips', ', '.join(clip_names) or 'none')",
            "geo.setGlobalAttribValue('detected_states', ', '.join(state_names) or 'none')",
            "geo.setGlobalAttribValue('result_status', 'ready_for_manual_agent_clip_wire' if ready_count else 'missing_clip_attributes')",
            "geo.setGlobalAttribValue('next_step', 'Manually wire OUT_AGENT_CLIP_NODE_TEST_INPUT into the bypassed TEST_AGENTCLIP_* nodes, then un-bypass one test node at a time.')",
        ]
    )


def _agent_clip_walk_test_input_python_sop(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import hou",
            f"plan = {repr(plan)}",
            "node = hou.pwd()",
            "geo = node.geometry()",
            "inputs = node.inputs()",
            "source_geo = inputs[0].geometry() if inputs and inputs[0] is not None else None",
            "geo.clear()",
            "if source_geo is not None:",
            "    geo.merge(source_geo)",
            "def attr_value(point, name, default=None):",
            "    attrib = point.geometry().findPointAttrib(name)",
            "    if attrib is None:",
            "        return default",
            "    try:",
            "        value = point.attribValue(attrib)",
            "    except Exception:",
            "        return default",
            "    return default if value is None else value",
            "def ensure_point_attrib(name, default):",
            "    attrib = geo.findPointAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Point, name, default)",
            "def ensure_global_attrib(name, default):",
            "    attrib = geo.findGlobalAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Global, name, default)",
            "def clip_value(point):",
            "    return str(attr_value(point, 'clipname', '') or attr_value(point, 'agentclip', '') or attr_value(point, 'clip', '') or attr_value(point, 'agent_clip', '') or attr_value(point, 'current_clip', '') or '')",
            "all_points = list(geo.points())",
            "fallback_position = (0.0, 0.0, 0.0)",
            "if all_points:",
            "    fallback_position = all_points[0].position()",
            "detected_clips = []",
            "delete_points = []",
            "for point in all_points:",
            "    clip = clip_value(point)",
            "    if clip and clip not in detected_clips:",
            "        detected_clips.append(clip)",
            "    if clip != 'walk':",
            "        delete_points.append(point)",
            "non_walk_count = len(delete_points)",
            "if delete_points:",
            "    geo.deletePoints(delete_points)",
            "walk_ready_attr = ensure_point_attrib('walk_test_ready', 0)",
            "clip_attr = ensure_point_attrib('walk_test_clip_attribute', '')",
            "wire_attr = ensure_point_attrib('walk_test_manual_wire', '')",
            "target_attr = ensure_point_attrib('walk_test_target_node', '')",
            "agent_ready_attr = ensure_point_attrib('agent_clip_ready', 0)",
            "walk_points = list(geo.points())",
            "for point in walk_points:",
            "    point.setAttribValue(walk_ready_attr, 1)",
            "    point.setAttribValue(clip_attr, 'clipname')",
            "    point.setAttribValue(wire_attr, 'OUT_AGENT_CLIP_WALK_TEST_INPUT -> TEST_AGENTCLIP_WALK')",
            "    point.setAttribValue(target_attr, 'TEST_AGENTCLIP_WALK')",
            "    point.setAttribValue(agent_ready_attr, 1)",
            "ensure_global_attrib('walk_test_mode', '')",
            "ensure_global_attrib('input_source', '')",
            "ensure_global_attrib('no_agent_nodes_cooked', 0)",
            "ensure_global_attrib('safe_walk_test_input', '')",
            "ensure_global_attrib('target_test_node', '')",
            "ensure_global_attrib('recommended_clip_attribute', '')",
            "ensure_global_attrib('recommended_group', '')",
            "ensure_global_attrib('manual_wire', '')",
            "ensure_global_attrib('walk_agent_count', 0)",
            "ensure_global_attrib('non_walk_agent_count', 0)",
            "ensure_global_attrib('detected_clips', '')",
            "ensure_global_attrib('result_status', '')",
            "ensure_global_attrib('next_step', '')",
            "geo.setGlobalAttribValue('walk_test_mode', 'agent_clip_walk_only_input')",
            "geo.setGlobalAttribValue('input_source', 'OUT_AGENT_CLIP_NODE_TEST_INPUT')",
            "geo.setGlobalAttribValue('no_agent_nodes_cooked', 1)",
            "geo.setGlobalAttribValue('safe_walk_test_input', 'OUT_AGENT_CLIP_WALK_TEST_INPUT')",
            "geo.setGlobalAttribValue('target_test_node', 'TEST_AGENTCLIP_WALK')",
            "geo.setGlobalAttribValue('recommended_clip_attribute', 'clipname')",
            "geo.setGlobalAttribValue('recommended_group', '@clipname=walk')",
            "geo.setGlobalAttribValue('manual_wire', 'OUT_AGENT_CLIP_WALK_TEST_INPUT -> TEST_AGENTCLIP_WALK')",
            "geo.setGlobalAttribValue('walk_agent_count', len(walk_points))",
            "geo.setGlobalAttribValue('non_walk_agent_count', non_walk_count)",
            "geo.setGlobalAttribValue('detected_clips', ', '.join(detected_clips) or 'none')",
            "geo.setGlobalAttribValue('result_status', 'ready_for_test_agentclip_walk' if walk_points else 'missing_walk_clip')",
            "geo.setGlobalAttribValue('next_step', 'Manually wire OUT_AGENT_CLIP_WALK_TEST_INPUT into TEST_AGENTCLIP_WALK, then un-bypass only TEST_AGENTCLIP_WALK.')",
        ]
    )


def _agent_clip_walk_test_result_python_sop(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import hou",
            f"plan = {repr(plan)}",
            "node = hou.pwd()",
            "parent = node.parent()",
            "geo = node.geometry()",
            "inputs = node.inputs()",
            "source_geo = inputs[0].geometry() if inputs and inputs[0] is not None else None",
            "geo.clear()",
            "if source_geo is not None:",
            "    geo.merge(source_geo)",
            "def attr_value(point, name, default=None):",
            "    attrib = point.geometry().findPointAttrib(name)",
            "    if attrib is None:",
            "        return default",
            "    try:",
            "        value = point.attribValue(attrib)",
            "    except Exception:",
            "        return default",
            "    return default if value is None else value",
            "def ensure_point_attrib(name, default):",
            "    attrib = geo.findPointAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Point, name, default)",
            "def ensure_global_attrib(name, default):",
            "    attrib = geo.findGlobalAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Global, name, default)",
            "def is_bypassed(test_node):",
            "    if test_node is None:",
            "        return False",
            "    for method_name in ('isBypassed', 'bypass'):",
            "        method = getattr(test_node, method_name, None)",
            "        if method is None:",
            "            continue",
            "        try:",
            "            value = method()",
            "        except TypeError:",
            "            continue",
            "        except Exception:",
            "            continue",
            "        return bool(value)",
            "    return False",
            "walk_node = parent.node('TEST_AGENTCLIP_WALK') if parent is not None else None",
            "walk_node_exists = 1 if walk_node is not None else 0",
            "walk_node_bypassed = 1 if is_bypassed(walk_node) else 0",
            "clip_attr = ensure_point_attrib('walk_result_clip_attribute', '')",
            "ready_attr = ensure_point_attrib('walk_result_ready', 0)",
            "wire_attr = ensure_point_attrib('walk_result_manual_wire', '')",
            "points = list(geo.points())",
            "ready_count = 0",
            "clip_names = []",
            "for point in points:",
            "    clip = str(attr_value(point, 'clipname', '') or attr_value(point, 'agentclip', '') or attr_value(point, 'clip', '') or attr_value(point, 'agent_clip', '') or attr_value(point, 'current_clip', '') or '')",
            "    ready = 1 if clip == 'walk' else 0",
            "    point.setAttribValue(clip_attr, 'clipname')",
            "    point.setAttribValue(ready_attr, ready)",
            "    point.setAttribValue(wire_attr, 'OUT_AGENT_CLIP_WALK_TEST_INPUT -> TEST_AGENTCLIP_WALK')",
            "    ready_count += ready",
            "    if clip and clip not in clip_names:",
            "        clip_names.append(clip)",
            "ensure_global_attrib('walk_test_result_mode', '')",
            "ensure_global_attrib('input_source', '')",
            "ensure_global_attrib('no_agent_nodes_cooked', 0)",
            "ensure_global_attrib('target_test_node', '')",
            "ensure_global_attrib('target_test_node_exists', 0)",
            "ensure_global_attrib('target_test_node_bypassed', 0)",
            "ensure_global_attrib('recommended_clip_attribute', '')",
            "ensure_global_attrib('recommended_state_attribute', '')",
            "ensure_global_attrib('recommended_motion_attributes', '')",
            "ensure_global_attrib('manual_wire', '')",
            "ensure_global_attrib('walk_agent_count', 0)",
            "ensure_global_attrib('ready_walk_agent_count', 0)",
            "ensure_global_attrib('detected_clips', '')",
            "ensure_global_attrib('result_status', '')",
            "ensure_global_attrib('next_step', '')",
            "geo.setGlobalAttribValue('walk_test_result_mode', 'agent_clip_walk_only_result')",
            "geo.setGlobalAttribValue('input_source', 'OUT_AGENT_CLIP_WALK_TEST_INPUT')",
            "geo.setGlobalAttribValue('no_agent_nodes_cooked', 1)",
            "geo.setGlobalAttribValue('target_test_node', 'TEST_AGENTCLIP_WALK')",
            "geo.setGlobalAttribValue('target_test_node_exists', walk_node_exists)",
            "geo.setGlobalAttribValue('target_test_node_bypassed', walk_node_bypassed)",
            "geo.setGlobalAttribValue('recommended_clip_attribute', 'clipname')",
            "geo.setGlobalAttribValue('recommended_state_attribute', 'state')",
            "geo.setGlobalAttribValue('recommended_motion_attributes', 'P, orient, v, speed')",
            "geo.setGlobalAttribValue('manual_wire', 'OUT_AGENT_CLIP_WALK_TEST_INPUT -> TEST_AGENTCLIP_WALK')",
            "geo.setGlobalAttribValue('walk_agent_count', len(points))",
            "geo.setGlobalAttribValue('ready_walk_agent_count', ready_count)",
            "geo.setGlobalAttribValue('detected_clips', ', '.join(clip_names) or 'none')",
            "geo.setGlobalAttribValue('result_status', 'ready_for_test_agentclip_walk' if ready_count else 'missing_walk_clip')",
            "geo.setGlobalAttribValue('next_step', 'When ready, connect OUT_AGENT_CLIP_WALK_TEST_INPUT to TEST_AGENTCLIP_WALK and un-bypass only TEST_AGENTCLIP_WALK. Keep other TEST_* nodes bypassed.')",
        ]
    )


def _agent_clip_named_test_input_python_sop(
    plan: dict[str, Any],
    *,
    clip_name: str,
    output_name: str,
    target_node: str,
) -> str:
    return "\n".join(
        [
            "import hou",
            f"plan = {repr(plan)}",
            f"target_clip = {clip_name!r}",
            f"safe_output_name = {output_name!r}",
            f"target_node_name = {target_node!r}",
            "node = hou.pwd()",
            "geo = node.geometry()",
            "inputs = node.inputs()",
            "source_geo = inputs[0].geometry() if inputs and inputs[0] is not None else None",
            "geo.clear()",
            "if source_geo is not None:",
            "    geo.merge(source_geo)",
            "def attr_value(point, name, default=None):",
            "    attrib = point.geometry().findPointAttrib(name)",
            "    if attrib is None:",
            "        return default",
            "    try:",
            "        value = point.attribValue(attrib)",
            "    except Exception:",
            "        return default",
            "    return default if value is None else value",
            "def ensure_point_attrib(name, default):",
            "    attrib = geo.findPointAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Point, name, default)",
            "def ensure_global_attrib(name, default):",
            "    attrib = geo.findGlobalAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Global, name, default)",
            "def safe_set(point, attrib, value):",
            "    try:",
            "        point.setAttribValue(attrib, value)",
            "        return True",
            "    except Exception:",
            "        return False",
            "def clip_value(point):",
            "    return str(attr_value(point, 'clipname', '') or attr_value(point, 'agentclip', '') or attr_value(point, 'clip', '') or attr_value(point, 'agent_clip', '') or attr_value(point, 'current_clip', '') or '')",
            "all_points = list(geo.points())",
            "fallback_position = (0.0, 0.0, 0.0)",
            "if all_points:",
            "    fallback_position = all_points[0].position()",
            "detected_clips = []",
            "delete_points = []",
            "for point in all_points:",
            "    clip = clip_value(point)",
            "    if clip and clip not in detected_clips:",
            "        detected_clips.append(clip)",
            "    if clip != target_clip:",
            "        delete_points.append(point)",
            "filtered_agent_count = len(delete_points)",
            "if delete_points:",
            "    geo.deletePoints(delete_points)",
            "ready_attr = ensure_point_attrib('clip_test_ready', 0)",
            "clip_attr = ensure_point_attrib('clip_test_clip_attribute', '')",
            "wire_attr = ensure_point_attrib('clip_test_manual_wire', '')",
            "target_attr = ensure_point_attrib('clip_test_target_node', '')",
            "target_clip_attr = ensure_point_attrib('clip_test_target_clip', '')",
            "agent_ready_attr = ensure_point_attrib('agent_clip_ready', 0)",
            "synthetic_attr = ensure_point_attrib('synthetic_clip_test_point', 0)",
            "clipname_attr = ensure_point_attrib('clipname', '')",
            "clip_attr_value = ensure_point_attrib('clip', '')",
            "agentclip_attr = ensure_point_attrib('agentclip', '')",
            "agent_clip_attr = ensure_point_attrib('agent_clip', '')",
            "current_clip_attr = ensure_point_attrib('current_clip', '')",
            "state_attr = ensure_point_attrib('state', '')",
            "crowd_state_attr = ensure_point_attrib('crowd_state', '')",
            "agent_state_attr = ensure_point_attrib('agent_state', '')",
            "clip_points = list(geo.points())",
            "synthetic_count = 0",
            "if not clip_points:",
            "    point = geo.createPoint()",
            "    point.setPosition(fallback_position)",
            "    clip_points = [point]",
            "    synthetic_count = 1",
            "for point in clip_points:",
            "    safe_set(point, ready_attr, 1)",
            "    safe_set(point, clip_attr, 'clipname')",
            "    safe_set(point, wire_attr, safe_output_name + ' -> ' + target_node_name)",
            "    safe_set(point, target_attr, target_node_name)",
            "    safe_set(point, target_clip_attr, target_clip)",
            "    safe_set(point, agent_ready_attr, 1)",
            "    safe_set(point, synthetic_attr, synthetic_count)",
            "    safe_set(point, clipname_attr, target_clip)",
            "    safe_set(point, clip_attr_value, target_clip)",
            "    safe_set(point, agentclip_attr, target_clip)",
            "    safe_set(point, agent_clip_attr, target_clip)",
            "    safe_set(point, current_clip_attr, target_clip)",
            "    state_value = 'sitting_down' if target_clip == 'sit_down' else ('sit_idle' if target_clip == 'sit_idle' else target_clip)",
            "    safe_set(point, state_attr, state_value)",
            "    safe_set(point, crowd_state_attr, state_value)",
            "    safe_set(point, agent_state_attr, state_value)",
            "ensure_global_attrib('clip_test_mode', '')",
            "ensure_global_attrib('input_source', '')",
            "ensure_global_attrib('no_agent_nodes_cooked', 0)",
            "ensure_global_attrib('safe_clip_test_input', '')",
            "ensure_global_attrib('target_clip', '')",
            "ensure_global_attrib('target_test_node', '')",
            "ensure_global_attrib('recommended_clip_attribute', '')",
            "ensure_global_attrib('recommended_group', '')",
            "ensure_global_attrib('manual_wire', '')",
            "ensure_global_attrib('clip_agent_count', 0)",
            "ensure_global_attrib('synthetic_clip_test_points', 0)",
            "ensure_global_attrib('filtered_agent_count', 0)",
            "ensure_global_attrib('detected_clips', '')",
            "ensure_global_attrib('result_status', '')",
            "ensure_global_attrib('next_step', '')",
            "geo.setGlobalAttribValue('clip_test_mode', 'agent_clip_named_clip_input')",
            "geo.setGlobalAttribValue('input_source', 'OUT_AGENT_CLIP_NODE_TEST_INPUT')",
            "geo.setGlobalAttribValue('no_agent_nodes_cooked', 1)",
            "geo.setGlobalAttribValue('safe_clip_test_input', safe_output_name)",
            "geo.setGlobalAttribValue('target_clip', target_clip)",
            "geo.setGlobalAttribValue('target_test_node', target_node_name)",
            "geo.setGlobalAttribValue('recommended_clip_attribute', 'clipname')",
            "geo.setGlobalAttribValue('recommended_group', '@clipname=' + target_clip)",
            "geo.setGlobalAttribValue('manual_wire', safe_output_name + ' -> ' + target_node_name)",
            "geo.setGlobalAttribValue('clip_agent_count', len(clip_points))",
            "geo.setGlobalAttribValue('synthetic_clip_test_points', synthetic_count)",
            "geo.setGlobalAttribValue('filtered_agent_count', filtered_agent_count)",
            "geo.setGlobalAttribValue('detected_clips', ', '.join(detected_clips) or 'none')",
            "geo.setGlobalAttribValue('result_status', 'ready_for_test_agentclip_' + target_clip)",
            "geo.setGlobalAttribValue('next_step', 'Manually wire ' + safe_output_name + ' into ' + target_node_name + ', then un-bypass only ' + target_node_name + '.')",
        ]
    )


def _agent_clip_named_test_result_python_sop(
    plan: dict[str, Any],
    *,
    clip_name: str,
    output_name: str,
    target_node: str,
) -> str:
    return "\n".join(
        [
            "import hou",
            f"plan = {repr(plan)}",
            f"target_clip = {clip_name!r}",
            f"safe_output_name = {output_name!r}",
            f"target_node_name = {target_node!r}",
            "node = hou.pwd()",
            "parent = node.parent()",
            "geo = node.geometry()",
            "inputs = node.inputs()",
            "source_geo = inputs[0].geometry() if inputs and inputs[0] is not None else None",
            "geo.clear()",
            "if source_geo is not None:",
            "    geo.merge(source_geo)",
            "def attr_value(point, name, default=None):",
            "    attrib = point.geometry().findPointAttrib(name)",
            "    if attrib is None:",
            "        return default",
            "    try:",
            "        value = point.attribValue(attrib)",
            "    except Exception:",
            "        return default",
            "    return default if value is None else value",
            "def ensure_point_attrib(name, default):",
            "    attrib = geo.findPointAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Point, name, default)",
            "def ensure_global_attrib(name, default):",
            "    attrib = geo.findGlobalAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Global, name, default)",
            "def is_bypassed(test_node):",
            "    if test_node is None:",
            "        return False",
            "    for method_name in ('isBypassed', 'bypass'):",
            "        method = getattr(test_node, method_name, None)",
            "        if method is None:",
            "            continue",
            "        try:",
            "            value = method()",
            "        except TypeError:",
            "            continue",
            "        except Exception:",
            "            continue",
            "        return bool(value)",
            "    return False",
            "target_node = parent.node(target_node_name) if parent is not None else None",
            "target_node_exists = 1 if target_node is not None else 0",
            "target_node_bypassed = 1 if is_bypassed(target_node) else 0",
            "clip_attr = ensure_point_attrib('clip_result_clip_attribute', '')",
            "ready_attr = ensure_point_attrib('clip_result_ready', 0)",
            "wire_attr = ensure_point_attrib('clip_result_manual_wire', '')",
            "points = list(geo.points())",
            "ready_count = 0",
            "clip_names = []",
            "for point in points:",
            "    clip = str(attr_value(point, 'clipname', '') or attr_value(point, 'agentclip', '') or attr_value(point, 'clip', '') or attr_value(point, 'agent_clip', '') or attr_value(point, 'current_clip', '') or '')",
            "    ready = 1 if clip == target_clip else 0",
            "    point.setAttribValue(clip_attr, 'clipname')",
            "    point.setAttribValue(ready_attr, ready)",
            "    point.setAttribValue(wire_attr, safe_output_name + ' -> ' + target_node_name)",
            "    ready_count += ready",
            "    if clip and clip not in clip_names:",
            "        clip_names.append(clip)",
            "ensure_global_attrib('clip_test_result_mode', '')",
            "ensure_global_attrib('input_source', '')",
            "ensure_global_attrib('no_agent_nodes_cooked', 0)",
            "ensure_global_attrib('target_clip', '')",
            "ensure_global_attrib('target_test_node', '')",
            "ensure_global_attrib('target_test_node_exists', 0)",
            "ensure_global_attrib('target_test_node_bypassed', 0)",
            "ensure_global_attrib('recommended_clip_attribute', '')",
            "ensure_global_attrib('recommended_state_attribute', '')",
            "ensure_global_attrib('recommended_motion_attributes', '')",
            "ensure_global_attrib('manual_wire', '')",
            "ensure_global_attrib('clip_agent_count', 0)",
            "ensure_global_attrib('ready_clip_agent_count', 0)",
            "ensure_global_attrib('detected_clips', '')",
            "ensure_global_attrib('result_status', '')",
            "ensure_global_attrib('next_step', '')",
            "geo.setGlobalAttribValue('clip_test_result_mode', 'agent_clip_named_clip_result')",
            "geo.setGlobalAttribValue('input_source', safe_output_name)",
            "geo.setGlobalAttribValue('no_agent_nodes_cooked', 1)",
            "geo.setGlobalAttribValue('target_clip', target_clip)",
            "geo.setGlobalAttribValue('target_test_node', target_node_name)",
            "geo.setGlobalAttribValue('target_test_node_exists', target_node_exists)",
            "geo.setGlobalAttribValue('target_test_node_bypassed', target_node_bypassed)",
            "geo.setGlobalAttribValue('recommended_clip_attribute', 'clipname')",
            "geo.setGlobalAttribValue('recommended_state_attribute', 'state')",
            "geo.setGlobalAttribValue('recommended_motion_attributes', 'P, orient, v, speed')",
            "geo.setGlobalAttribValue('manual_wire', safe_output_name + ' -> ' + target_node_name)",
            "geo.setGlobalAttribValue('clip_agent_count', len(points))",
            "geo.setGlobalAttribValue('ready_clip_agent_count', ready_count)",
            "geo.setGlobalAttribValue('detected_clips', ', '.join(clip_names) or 'none')",
            "geo.setGlobalAttribValue('result_status', 'ready_for_test_agentclip_' + target_clip if ready_count else 'missing_' + target_clip + '_clip')",
            "geo.setGlobalAttribValue('next_step', 'When ready, connect ' + safe_output_name + ' to ' + target_node_name + ' and un-bypass only ' + target_node_name + '. Keep other TEST_* nodes bypassed.')",
        ]
    )


def _agent_clip_clipset_test_result_python_sop(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import hou",
            f"plan = {repr(plan)}",
            "clip_specs = (",
            "    ('walk', 'OUT_AGENT_CLIP_WALK_TEST_INPUT', 'TEST_AGENTCLIP_WALK', 'walking_to_interaction', 1),",
            "    ('sit_down', 'OUT_AGENT_CLIP_SIT_DOWN_TEST_INPUT', 'TEST_AGENTCLIP_SIT_DOWN', 'sitting_down', 0),",
            "    ('sit_idle', 'OUT_AGENT_CLIP_SIT_IDLE_TEST_INPUT', 'TEST_AGENTCLIP_SIT_IDLE', 'sit_idle', 1),",
            ")",
            "node = hou.pwd()",
            "parent = node.parent()",
            "geo = node.geometry()",
            "inputs = node.inputs()",
            "geo.clear()",
            "def attr_value(point, name, default=None):",
            "    attrib = point.geometry().findPointAttrib(name)",
            "    if attrib is None:",
            "        return default",
            "    try:",
            "        value = point.attribValue(attrib)",
            "    except Exception:",
            "        return default",
            "    return default if value is None else value",
            "def ensure_point_attrib(name, default):",
            "    attrib = geo.findPointAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Point, name, default)",
            "def ensure_global_attrib(name, default):",
            "    attrib = geo.findGlobalAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Global, name, default)",
            "def is_bypassed(test_node):",
            "    if test_node is None:",
            "        return False",
            "    for method_name in ('isBypassed', 'bypass'):",
            "        method = getattr(test_node, method_name, None)",
            "        if method is None:",
            "            continue",
            "        try:",
            "            value = method()",
            "        except TypeError:",
            "            continue",
            "        except Exception:",
            "            continue",
            "        return bool(value)",
            "    return False",
            "clip_attr = ensure_point_attrib('clipname', '')",
            "order_attr = ensure_point_attrib('clip_order', 0)",
            "state_attr = ensure_point_attrib('state', '')",
            "loop_attr = ensure_point_attrib('clip_loop', 0)",
            "source_attr = ensure_point_attrib('clip_test_source_output', '')",
            "target_attr = ensure_point_attrib('clip_test_target_node', '')",
            "count_attr = ensure_point_attrib('clip_test_agent_count', 0)",
            "ready_attr = ensure_point_attrib('clip_test_ready', 0)",
            "bypass_attr = ensure_point_attrib('clip_test_node_bypassed', 0)",
            "wire_attr = ensure_point_attrib('clip_test_manual_wire', '')",
            "actual_input_attr = ensure_point_attrib('clip_test_actual_input', '')",
            "counts = {}",
            "present_clips = []",
            "missing_current_clips = []",
            "missing_test_nodes = []",
            "bypassed_nodes = []",
            "prewired_nodes = []",
            "unwired_nodes = []",
            "for index, (clip_name, source_output, target_node_name, state_name, loop) in enumerate(clip_specs):",
            "    source_geo = inputs[index].geometry() if len(inputs) > index and inputs[index] is not None else None",
            "    count = 0",
            "    if source_geo is not None:",
            "        for point in source_geo.points():",
            "            clip = str(attr_value(point, 'clipname', '') or attr_value(point, 'agentclip', '') or attr_value(point, 'clip', '') or attr_value(point, 'agent_clip', '') or attr_value(point, 'current_clip', '') or '')",
            "            if clip == clip_name:",
            "                count += 1",
            "    counts[clip_name] = count",
            "    if count:",
            "        present_clips.append(clip_name)",
            "    else:",
            "        missing_current_clips.append(clip_name)",
            "    test_node = parent.node(target_node_name) if parent is not None else None",
            "    if test_node is None:",
            "        missing_test_nodes.append(target_node_name)",
            "    actual_input_name = ''",
            "    if test_node is not None:",
            "        if is_bypassed(test_node):",
            "            bypassed_nodes.append(target_node_name)",
            "        try:",
            "            input_nodes = test_node.inputs()",
            "        except Exception:",
            "            input_nodes = ()",
            "        input_node = input_nodes[0] if input_nodes else None",
            "        actual_input_name = input_node.name() if input_node is not None else ''",
            "        if actual_input_name == source_output:",
            "            prewired_nodes.append(target_node_name)",
            "        else:",
            "            unwired_nodes.append(target_node_name)",
            "    point = geo.createPoint()",
            "    point.setPosition((float(index), 0.0, 0.0))",
            "    point.setAttribValue(clip_attr, clip_name)",
            "    point.setAttribValue(order_attr, index)",
            "    point.setAttribValue(state_attr, state_name)",
            "    point.setAttribValue(loop_attr, int(loop))",
            "    point.setAttribValue(source_attr, source_output)",
            "    point.setAttribValue(target_attr, target_node_name)",
            "    point.setAttribValue(count_attr, count)",
            "    point.setAttribValue(ready_attr, 1 if count else 0)",
            "    point.setAttribValue(bypass_attr, 1 if target_node_name in bypassed_nodes else 0)",
            "    point.setAttribValue(wire_attr, source_output + ' -> ' + target_node_name)",
            "    point.setAttribValue(actual_input_attr, actual_input_name or 'none')",
            "ensure_global_attrib('clipset_test_result_mode', '')",
            "ensure_global_attrib('no_agent_nodes_cooked', 0)",
            "ensure_global_attrib('expected_clip_sequence', '')",
            "ensure_global_attrib('input_sources', '')",
            "ensure_global_attrib('manual_wire_order', '')",
            "ensure_global_attrib('recommended_clip_attribute', '')",
            "ensure_global_attrib('recommended_state_attribute', '')",
            "ensure_global_attrib('recommended_motion_attributes', '')",
            "ensure_global_attrib('walk_agent_count', 0)",
            "ensure_global_attrib('sit_down_agent_count', 0)",
            "ensure_global_attrib('sit_idle_agent_count', 0)",
            "ensure_global_attrib('present_current_clips', '')",
            "ensure_global_attrib('missing_current_clips', '')",
            "ensure_global_attrib('missing_test_nodes', '')",
            "ensure_global_attrib('bypassed_test_nodes', '')",
            "ensure_global_attrib('prewired_test_nodes', '')",
            "ensure_global_attrib('unwired_test_nodes', '')",
            "ensure_global_attrib('test_nodes_remain_bypassed', 0)",
            "ensure_global_attrib('result_status', '')",
            "ensure_global_attrib('next_step', '')",
            "geo.setGlobalAttribValue('clipset_test_result_mode', 'agent_clip_three_clip_result')",
            "geo.setGlobalAttribValue('no_agent_nodes_cooked', 1)",
            "geo.setGlobalAttribValue('expected_clip_sequence', 'walk -> sit_down -> sit_idle')",
            "geo.setGlobalAttribValue('input_sources', ', '.join(spec[1] for spec in clip_specs))",
            "geo.setGlobalAttribValue('manual_wire_order', 'OUT_AGENT_CLIP_WALK_TEST_INPUT -> TEST_AGENTCLIP_WALK, OUT_AGENT_CLIP_SIT_DOWN_TEST_INPUT -> TEST_AGENTCLIP_SIT_DOWN, OUT_AGENT_CLIP_SIT_IDLE_TEST_INPUT -> TEST_AGENTCLIP_SIT_IDLE')",
            "geo.setGlobalAttribValue('recommended_clip_attribute', 'clipname')",
            "geo.setGlobalAttribValue('recommended_state_attribute', 'state')",
            "geo.setGlobalAttribValue('recommended_motion_attributes', 'P, orient, v, speed')",
            "geo.setGlobalAttribValue('walk_agent_count', counts.get('walk', 0))",
            "geo.setGlobalAttribValue('sit_down_agent_count', counts.get('sit_down', 0))",
            "geo.setGlobalAttribValue('sit_idle_agent_count', counts.get('sit_idle', 0))",
            "geo.setGlobalAttribValue('present_current_clips', ', '.join(present_clips) or 'none')",
            "geo.setGlobalAttribValue('missing_current_clips', ', '.join(missing_current_clips) or 'none')",
            "geo.setGlobalAttribValue('missing_test_nodes', ', '.join(missing_test_nodes) or 'none')",
            "geo.setGlobalAttribValue('bypassed_test_nodes', ', '.join(bypassed_nodes) or 'none')",
            "geo.setGlobalAttribValue('prewired_test_nodes', ', '.join(prewired_nodes) or 'none')",
            "geo.setGlobalAttribValue('unwired_test_nodes', ', '.join(unwired_nodes) or 'none')",
            "geo.setGlobalAttribValue('test_nodes_remain_bypassed', 1 if len(bypassed_nodes) == len(clip_specs) - len(missing_test_nodes) else 0)",
            "if missing_test_nodes:",
            "    status = 'missing_agentclip_test_nodes'",
            "elif unwired_nodes:",
            "    status = 'agentclip_test_nodes_unwired'",
            "else:",
            "    status = 'ready_for_three_clip_agentclip_unbypass_tests'",
            "geo.setGlobalAttribValue('result_status', status)",
            "geo.setGlobalAttribValue('next_step', 'TEST_AGENTCLIP_* nodes are already wired to matching OUT_AGENT_CLIP_*_TEST_INPUT nodes. Un-bypass one TEST_AGENTCLIP_* node at a time. Current-frame coverage is informational; scrub the timeline to see each clip become active.')",
        ]
    )


def _agent_clip_unbypass_guard_python_sop(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import hou",
            f"plan = {repr(plan)}",
            "test_specs = (",
            "    ('walk', 'OUT_AGENT_CLIP_WALK_TEST_INPUT', 'TEST_AGENTCLIP_WALK'),",
            "    ('sit_down', 'OUT_AGENT_CLIP_SIT_DOWN_TEST_INPUT', 'TEST_AGENTCLIP_SIT_DOWN'),",
            "    ('sit_idle', 'OUT_AGENT_CLIP_SIT_IDLE_TEST_INPUT', 'TEST_AGENTCLIP_SIT_IDLE'),",
            ")",
            "node = hou.pwd()",
            "parent = node.parent()",
            "geo = node.geometry()",
            "geo.clear()",
            "def ensure_point_attrib(name, default):",
            "    attrib = geo.findPointAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Point, name, default)",
            "def ensure_global_attrib(name, default):",
            "    attrib = geo.findGlobalAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Global, name, default)",
            "def is_bypassed(test_node):",
            "    if test_node is None:",
            "        return False",
            "    for method_name in ('isBypassed', 'bypass'):",
            "        method = getattr(test_node, method_name, None)",
            "        if method is None:",
            "            continue",
            "        try:",
            "            value = method()",
            "        except TypeError:",
            "            continue",
            "        except Exception:",
            "            continue",
            "        return bool(value)",
            "    return False",
            "clip_attr = ensure_point_attrib('target_clip', '')",
            "node_attr = ensure_point_attrib('test_node', '')",
            "expected_attr = ensure_point_attrib('expected_input', '')",
            "actual_attr = ensure_point_attrib('actual_input', '')",
            "exists_attr = ensure_point_attrib('test_node_exists', 0)",
            "bypass_attr = ensure_point_attrib('test_node_bypassed', 0)",
            "active_attr = ensure_point_attrib('test_node_active', 0)",
            "prewired_attr = ensure_point_attrib('test_node_prewired', 0)",
            "guard_attr = ensure_point_attrib('guard_status', '')",
            "active_nodes = []",
            "bypassed_nodes = []",
            "missing_nodes = []",
            "prewired_nodes = []",
            "unwired_nodes = []",
            "for index, (clip_name, expected_input, test_node_name) in enumerate(test_specs):",
            "    test_node = parent.node(test_node_name) if parent is not None else None",
            "    exists = 1 if test_node is not None else 0",
            "    bypassed = 1 if is_bypassed(test_node) else 0",
            "    actual_input = ''",
            "    if test_node is None:",
            "        missing_nodes.append(test_node_name)",
            "    else:",
            "        try:",
            "            input_nodes = test_node.inputs()",
            "        except Exception:",
            "            input_nodes = ()",
            "        input_node = input_nodes[0] if input_nodes else None",
            "        actual_input = input_node.name() if input_node is not None else ''",
            "        if actual_input == expected_input:",
            "            prewired_nodes.append(test_node_name)",
            "        else:",
            "            unwired_nodes.append(test_node_name)",
            "        if bypassed:",
            "            bypassed_nodes.append(test_node_name)",
            "        else:",
            "            active_nodes.append(test_node_name)",
            "    point = geo.createPoint()",
            "    point.setPosition((float(index), 0.0, 0.0))",
            "    point.setAttribValue(clip_attr, clip_name)",
            "    point.setAttribValue(node_attr, test_node_name)",
            "    point.setAttribValue(expected_attr, expected_input)",
            "    point.setAttribValue(actual_attr, actual_input or 'none')",
            "    point.setAttribValue(exists_attr, exists)",
            "    point.setAttribValue(bypass_attr, bypassed)",
            "    point.setAttribValue(active_attr, 1 if exists and not bypassed else 0)",
            "    point.setAttribValue(prewired_attr, 1 if actual_input == expected_input else 0)",
            "    if not exists:",
            "        point_status = 'missing_test_node'",
            "    elif actual_input != expected_input:",
            "        point_status = 'unwired_test_node'",
            "    elif bypassed:",
            "        point_status = 'safe_bypassed'",
            "    else:",
            "        point_status = 'active_unbypassed'",
            "    point.setAttribValue(guard_attr, point_status)",
            "active_count = len(active_nodes)",
            "ensure_global_attrib('unbypass_guard_mode', '')",
            "ensure_global_attrib('no_agent_nodes_cooked', 0)",
            "ensure_global_attrib('monitored_test_nodes', '')",
            "ensure_global_attrib('active_test_node_count', 0)",
            "ensure_global_attrib('active_test_nodes', '')",
            "ensure_global_attrib('bypassed_test_nodes', '')",
            "ensure_global_attrib('missing_test_nodes', '')",
            "ensure_global_attrib('prewired_test_nodes', '')",
            "ensure_global_attrib('unwired_test_nodes', '')",
            "ensure_global_attrib('safe_to_cook_single_test', 0)",
            "ensure_global_attrib('recommended_display_node', '')",
            "ensure_global_attrib('result_status', '')",
            "ensure_global_attrib('next_step', '')",
            "geo.setGlobalAttribValue('unbypass_guard_mode', 'agent_clip_unbypass_guard')",
            "geo.setGlobalAttribValue('no_agent_nodes_cooked', 1)",
            "geo.setGlobalAttribValue('monitored_test_nodes', ', '.join(spec[2] for spec in test_specs))",
            "geo.setGlobalAttribValue('active_test_node_count', active_count)",
            "geo.setGlobalAttribValue('active_test_nodes', ', '.join(active_nodes) or 'none')",
            "geo.setGlobalAttribValue('bypassed_test_nodes', ', '.join(bypassed_nodes) or 'none')",
            "geo.setGlobalAttribValue('missing_test_nodes', ', '.join(missing_nodes) or 'none')",
            "geo.setGlobalAttribValue('prewired_test_nodes', ', '.join(prewired_nodes) or 'none')",
            "geo.setGlobalAttribValue('unwired_test_nodes', ', '.join(unwired_nodes) or 'none')",
            "safe_single = 1 if active_count <= 1 and not missing_nodes and not unwired_nodes else 0",
            "geo.setGlobalAttribValue('safe_to_cook_single_test', safe_single)",
            "if missing_nodes:",
            "    status = 'missing_agentclip_test_nodes'",
            "    display_node = 'OUT_AGENT_CLIP_THREE_CLIP_TEST_RESULT'",
            "    next_step = 'Create the missing TEST_AGENTCLIP_* nodes or check whether this Houdini build has Agent Clip SOPs.'",
            "elif unwired_nodes:",
            "    status = 'agentclip_test_nodes_unwired'",
            "    display_node = 'OUT_AGENT_CLIP_THREE_CLIP_TEST_RESULT'",
            "    next_step = 'Re-run update_single_agent_seat_prototype so TEST_AGENTCLIP_* nodes are prewired to OUT_AGENT_CLIP_*_TEST_INPUT.'",
            "elif active_count == 0:",
            "    status = 'all_agentclip_tests_bypassed_safe'",
            "    display_node = 'OUT_AGENT_CLIP_UNBYPASS_GUARD'",
            "    next_step = 'Un-bypass only TEST_AGENTCLIP_WALK first, then inspect this guard again before trying sit_down or sit_idle.'",
            "elif active_count == 1:",
            "    status = 'single_agentclip_test_active'",
            "    display_node = active_nodes[0]",
            "    next_step = 'Only one TEST_AGENTCLIP_* node is active. If it cooks cleanly, bypass it again before activating the next clip test.'",
            "else:",
            "    status = 'multiple_agentclip_tests_active_stop'",
            "    display_node = 'OUT_AGENT_CLIP_UNBYPASS_GUARD'",
            "    next_step = 'Bypass all but one TEST_AGENTCLIP_* node before cooking an Agent Clip test.'",
            "geo.setGlobalAttribValue('recommended_display_node', display_node)",
            "geo.setGlobalAttribValue('result_status', status)",
            "geo.setGlobalAttribValue('next_step', next_step)",
        ]
    )


def _agent_clip_activation_sequence_log_python_sop(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import hou",
            f"plan = {repr(plan)}",
            "sequence = (",
            "    ('walk', 'TEST_AGENTCLIP_WALK'),",
            "    ('sit_down', 'TEST_AGENTCLIP_SIT_DOWN'),",
            "    ('sit_idle', 'TEST_AGENTCLIP_SIT_IDLE'),",
            ")",
            "node = hou.pwd()",
            "parent = node.parent()",
            "geo = node.geometry()",
            "geo.clear()",
            "def ensure_point_attrib(name, default):",
            "    attrib = geo.findPointAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Point, name, default)",
            "def ensure_global_attrib(name, default):",
            "    attrib = geo.findGlobalAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Global, name, default)",
            "def is_bypassed(test_node):",
            "    if test_node is None:",
            "        return False",
            "    for method_name in ('isBypassed', 'bypass'):",
            "        method = getattr(test_node, method_name, None)",
            "        if method is None:",
            "            continue",
            "        try:",
            "            value = method()",
            "        except TypeError:",
            "            continue",
            "        except Exception:",
            "            continue",
            "        return bool(value)",
            "    return False",
            "clip_attr = ensure_point_attrib('clip', '')",
            "order_attr = ensure_point_attrib('activation_order', 0)",
            "node_attr = ensure_point_attrib('test_node', '')",
            "expected_status_attr = ensure_point_attrib('expected_status', '')",
            "expected_display_attr = ensure_point_attrib('expected_display_node', '')",
            "current_bypass_attr = ensure_point_attrib('current_test_node_bypassed', 0)",
            "current_active_attr = ensure_point_attrib('current_test_node_active', 0)",
            "safe_attr = ensure_point_attrib('safe_to_activate_individually', 0)",
            "existing_nodes = []",
            "missing_nodes = []",
            "currently_active = []",
            "currently_bypassed = []",
            "for index, (clip_name, test_node_name) in enumerate(sequence):",
            "    test_node = parent.node(test_node_name) if parent is not None else None",
            "    exists = test_node is not None",
            "    bypassed = is_bypassed(test_node) if exists else False",
            "    if exists:",
            "        existing_nodes.append(test_node_name)",
            "        if bypassed:",
            "            currently_bypassed.append(test_node_name)",
            "        else:",
            "            currently_active.append(test_node_name)",
            "    else:",
            "        missing_nodes.append(test_node_name)",
            "    point = geo.createPoint()",
            "    point.setPosition((float(index), 0.0, 0.0))",
            "    point.setAttribValue(clip_attr, clip_name)",
            "    point.setAttribValue(order_attr, index)",
            "    point.setAttribValue(node_attr, test_node_name)",
            "    point.setAttribValue(expected_status_attr, 'single_agentclip_test_active')",
            "    point.setAttribValue(expected_display_attr, test_node_name)",
            "    point.setAttribValue(current_bypass_attr, 1 if exists and bypassed else 0)",
            "    point.setAttribValue(current_active_attr, 1 if exists and not bypassed else 0)",
            "    point.setAttribValue(safe_attr, 1 if exists else 0)",
            "ensure_global_attrib('sequence_log_mode', '')",
            "ensure_global_attrib('no_agent_nodes_cooked', 0)",
            "ensure_global_attrib('how_to_generate_runtime_results', '')",
            "ensure_global_attrib('expected_print_output', '')",
            "ensure_global_attrib('existing_test_nodes', '')",
            "ensure_global_attrib('missing_test_nodes', '')",
            "ensure_global_attrib('currently_active_test_nodes', '')",
            "ensure_global_attrib('currently_bypassed_test_nodes', '')",
            "ensure_global_attrib('result_status', '')",
            "ensure_global_attrib('next_step', '')",
            "geo.setGlobalAttribValue('sequence_log_mode', 'agent_clip_activation_sequence_log')",
            "geo.setGlobalAttribValue('no_agent_nodes_cooked', 1)",
            "geo.setGlobalAttribValue('how_to_generate_runtime_results', 'Run crowd_kinefx.run_agent_clip_activation_sequence() in Houdini Python Shell. This node documents the expected per-clip rows.')",
            "geo.setGlobalAttribValue('expected_print_output', 'walk single_agentclip_test_active TEST_AGENTCLIP_WALK; sit_down single_agentclip_test_active TEST_AGENTCLIP_SIT_DOWN; sit_idle single_agentclip_test_active TEST_AGENTCLIP_SIT_IDLE')",
            "geo.setGlobalAttribValue('existing_test_nodes', ', '.join(existing_nodes) or 'none')",
            "geo.setGlobalAttribValue('missing_test_nodes', ', '.join(missing_nodes) or 'none')",
            "geo.setGlobalAttribValue('currently_active_test_nodes', ', '.join(currently_active) or 'none')",
            "geo.setGlobalAttribValue('currently_bypassed_test_nodes', ', '.join(currently_bypassed) or 'none')",
            "geo.setGlobalAttribValue('result_status', 'ready_for_python_activation_sequence' if not missing_nodes else 'missing_agentclip_test_nodes')",
            "geo.setGlobalAttribValue('next_step', 'Use run_agent_clip_activation_sequence() for the automated guard check, or activate_agent_clip_test(\"walk\") for a single manual cook test.')",
        ]
    )


def _runtime_kinefx_preview_python_sop(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import math",
            "import hou",
            f"plan = {repr(plan)}",
            "node = hou.pwd()",
            "inputs = node.inputs()",
            "walk_geo = inputs[0].geometry() if len(inputs) > 0 and inputs[0] is not None else None",
            "sit_down_geo = inputs[1].geometry() if len(inputs) > 1 and inputs[1] is not None else None",
            "sit_idle_geo = inputs[2].geometry() if len(inputs) > 2 and inputs[2] is not None else None",
            "driver_geo = inputs[3].geometry() if len(inputs) > 3 and inputs[3] is not None else None",
            "geo = node.geometry()",
            "geo.clear()",
            "def attr_value(point, name, default=None):",
            "    attrib = point.geometry().findPointAttrib(name)",
            "    if attrib is None:",
            "        return default",
            "    try:",
            "        value = point.attribValue(attrib)",
            "    except Exception:",
            "        return default",
            "    return default if value is None else value",
            "def ensure_point_attrib(name, default):",
            "    attrib = geo.findPointAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Point, name, default)",
            "def ensure_global_attrib(name, default):",
            "    attrib = geo.findGlobalAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Global, name, default)",
            "def vec3(value, default=(0.0, 0.0, 1.0)):",
            "    if value is None:",
            "        return default",
            "    try:",
            "        return (float(value[0]), float(value[1]), float(value[2]))",
            "    except Exception:",
            "        return default",
            "def first_agent_point(source_geo):",
            "    if source_geo is None:",
            "        return None",
            "    for point in source_geo.points():",
            "        entity = str(attr_value(point, 'entity_type', '') or '')",
            "        if not entity or entity == 'agent':",
            "            return point",
            "    return None",
            "agent_point = first_agent_point(driver_geo)",
            "driver_source = 'input_crowd_clip_state_driver' if agent_point is not None else 'missing_crowd_clip_state_driver'",
            "state = str(attr_value(agent_point, 'agent_state', '') if agent_point is not None else '')",
            "clip = str(attr_value(agent_point, 'current_clip', '') if agent_point is not None else '')",
            "if not clip and agent_point is not None:",
            "    clip = str(attr_value(agent_point, 'clipname', '') or '')",
            "if not clip:",
            "    if state in ('walking_to_interaction', 'aligning_to_interaction'):",
            "        clip = 'walk'",
            "    elif state == 'sitting_down':",
            "        clip = 'sit_down'",
            "    elif state == 'sit_idle':",
            "        clip = 'sit_idle'",
            "target_seat = str(attr_value(agent_point, 'target_seat', '') if agent_point is not None else '')",
            "agent_name = str(attr_value(agent_point, 'name', '') if agent_point is not None else '')",
            "if agent_point is not None:",
            "    pos = agent_point.position()",
            "    agent_pos = (float(pos[0]), float(pos[1]), float(pos[2]))",
            "else:",
            "    loc = plan.get('locomotion') or {}",
            "    seat = loc.get('seat_position') or {'x': 0.0, 'y': 0.0, 'z': 0.0}",
            "    agent_pos = (float(seat.get('x', 0.0)), float(seat.get('y', 0.0)), float(seat.get('z', 0.0)))",
            "driver_heading = vec3(attr_value(agent_point, 'heading', None), (0.0, 0.0, 1.0)) if agent_point is not None else (0.0, 0.0, 1.0)",
            "clip_geo = walk_geo if clip == 'walk' else (sit_down_geo if clip == 'sit_down' else (sit_idle_geo if clip == 'sit_idle' else None))",
            "clip_available = clip_geo is not None and len(clip_geo.points()) > 0",
            "if clip_available:",
            "    geo.merge(clip_geo)",
            "face_x = driver_heading[0]",
            "face_z = driver_heading[2]",
            "yaw = math.atan2(face_x, face_z) if abs(face_x) + abs(face_z) > 1e-8 else 0.0",
            "cos_y = math.cos(yaw)",
            "sin_y = math.sin(yaw)",
            "points = list(geo.points())",
            "if points:",
            "    min_x = min(p.position()[0] for p in points)",
            "    max_x = max(p.position()[0] for p in points)",
            "    min_y = min(p.position()[1] for p in points)",
            "    min_z = min(p.position()[2] for p in points)",
            "    max_z = max(p.position()[2] for p in points)",
            "    pivot = ((min_x + max_x) * 0.5, min_y, (min_z + max_z) * 0.5)",
            "    point_state_attr = ensure_point_attrib('agent_state', '')",
            "    point_clip_attr = ensure_point_attrib('current_clip', '')",
            "    point_clipname_attr = ensure_point_attrib('clipname', '')",
            "    point_target_attr = ensure_point_attrib('target_seat', '')",
            "    for p in points:",
            "        old = p.position()",
            "        lx = float(old[0]) - pivot[0]",
            "        ly = float(old[1]) - pivot[1]",
            "        lz = float(old[2]) - pivot[2]",
            "        rx = lx * cos_y + lz * sin_y",
            "        rz = -lx * sin_y + lz * cos_y",
            "        p.setPosition((agent_pos[0] + rx, agent_pos[1] + ly, agent_pos[2] + rz))",
            "        p.setAttribValue(point_state_attr, state)",
            "        p.setAttribValue(point_clip_attr, clip)",
            "        p.setAttribValue(point_clipname_attr, clip)",
            "        p.setAttribValue(point_target_attr, target_seat)",
            "ensure_global_attrib('runtime_kinefx_mode', '')",
            "ensure_global_attrib('driver_source', '')",
            "ensure_global_attrib('agent_name', '')",
            "ensure_global_attrib('agent_state', '')",
            "ensure_global_attrib('current_clip', '')",
            "ensure_global_attrib('target_seat', '')",
            "ensure_global_attrib('behavior_position', '')",
            "ensure_global_attrib('behavior_heading', '')",
            "ensure_global_attrib('clip_available', 0)",
            "ensure_global_attrib('how_to_check', '')",
            "geo.setGlobalAttribValue('runtime_kinefx_mode', 'runtime_behavior_clip_preview')",
            "geo.setGlobalAttribValue('driver_source', driver_source)",
            "geo.setGlobalAttribValue('agent_name', agent_name)",
            "geo.setGlobalAttribValue('agent_state', state)",
            "geo.setGlobalAttribValue('current_clip', clip)",
            "geo.setGlobalAttribValue('target_seat', target_seat)",
            "geo.setGlobalAttribValue('behavior_position', '{:.3f}, {:.3f}, {:.3f}'.format(agent_pos[0], agent_pos[1], agent_pos[2]))",
            "geo.setGlobalAttribValue('behavior_heading', '{:.3f}, {:.3f}, {:.3f}'.format(driver_heading[0], driver_heading[1], driver_heading[2]))",
            "geo.setGlobalAttribValue('clip_available', int(bool(clip_available)))",
            "geo.setGlobalAttribValue('how_to_check', 'Display OUT_RUNTIME_AGENT_BEHAVIOR. Detail shows runtime clip and driver source.')",
        ]
    )


def _agent_crowd_visual_preview_python_sop(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import hou",
            f"plan = {repr(plan)}",
            "node = hou.pwd()",
            "inputs = node.inputs()",
            "visual_geo = inputs[0].geometry() if len(inputs) > 0 and inputs[0] is not None else None",
            "behavior_geo = inputs[1].geometry() if len(inputs) > 1 and inputs[1] is not None else None",
            "geo = node.geometry()",
            "geo.clear()",
            "def attr_value(point, name, default=None):",
            "    attrib = point.geometry().findPointAttrib(name)",
            "    if attrib is None:",
            "        return default",
            "    try:",
            "        value = point.attribValue(attrib)",
            "    except Exception:",
            "        return default",
            "    return default if value is None else value",
            "def ensure_point_attrib(name, default):",
            "    attrib = geo.findPointAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Point, name, default)",
            "def ensure_global_attrib(name, default):",
            "    attrib = geo.findGlobalAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Global, name, default)",
            "def safe_set(point, attrib, value):",
            "    try:",
            "        point.setAttribValue(attrib, value)",
            "    except Exception:",
            "        pass",
            "def primitive_type_name(prim):",
            "    try:",
            "        return prim.type().name()",
            "    except Exception:",
            "        return 'unknown'",
            "def first_agent_point(source_geo):",
            "    if source_geo is None:",
            "        return None",
            "    for point in source_geo.points():",
            "        entity = str(attr_value(point, 'entity_type', '') or '')",
            "        if not entity or entity == 'agent':",
            "            return point",
            "    points = list(source_geo.points())",
            "    return points[0] if points else None",
            "behavior_point = first_agent_point(behavior_geo)",
            "visual_point_count = len(visual_geo.points()) if visual_geo is not None else 0",
            "visual_prim_count = len(visual_geo.prims()) if visual_geo is not None else 0",
            "behavior_point_count = len(behavior_geo.points()) if behavior_geo is not None else 0",
            "behavior_prim_count = len(behavior_geo.prims()) if behavior_geo is not None else 0",
            "if visual_geo is not None and (visual_point_count or visual_prim_count):",
            "    geo.merge(visual_geo)",
            "    visual_source = 'kinefx_imports/OUT_RUNTIME_AGENT_BEHAVIOR'",
            "else:",
            "    if behavior_geo is not None:",
            "        geo.merge(behavior_geo)",
            "    visual_source = 'agent_crowd_proxy_fallback'",
            "state = str(attr_value(behavior_point, 'state', '') or attr_value(behavior_point, 'agent_state', '') or '') if behavior_point is not None else ''",
            "clip = str(attr_value(behavior_point, 'clipname', '') or attr_value(behavior_point, 'current_clip', '') or attr_value(behavior_point, 'clip', '') or '') if behavior_point is not None else ''",
            "target = str(attr_value(behavior_point, 'target_seat', '') or '') if behavior_point is not None else ''",
            "agent_name = str(attr_value(behavior_point, 'name', '') or attr_value(behavior_point, 'agentname', '') or 'agent_001') if behavior_point is not None else 'agent_001'",
            "agent_state_attr = ensure_point_attrib('agent_state', '')",
            "state_attr = ensure_point_attrib('state', '')",
            "clip_attr = ensure_point_attrib('current_clip', '')",
            "clipname_attr = ensure_point_attrib('clipname', '')",
            "target_attr = ensure_point_attrib('target_seat', '')",
            "name_attr = ensure_point_attrib('name', '')",
            "preview_attr = ensure_point_attrib('visual_preview_source', '')",
            "for point in geo.points():",
            "    safe_set(point, agent_state_attr, state)",
            "    safe_set(point, state_attr, state)",
            "    safe_set(point, clip_attr, clip)",
            "    safe_set(point, clipname_attr, clip)",
            "    safe_set(point, target_attr, target)",
            "    safe_set(point, name_attr, agent_name)",
            "    safe_set(point, preview_attr, visual_source)",
            "primitive_types = []",
            "for prim in geo.prims():",
            "    name = primitive_type_name(prim)",
            "    if name not in primitive_types:",
            "        primitive_types.append(name)",
            "visual_status = 'kinefx_visual_preview_ready' if visual_source.startswith('kinefx') else 'agent_proxy_visual_fallback'",
            "ensure_global_attrib('agent_crowd_visual_preview_status', '')",
            "ensure_global_attrib('visual_source', '')",
            "ensure_global_attrib('behavior_source', '')",
            "ensure_global_attrib('display_node', '')",
            "ensure_global_attrib('agent_crowd_behavior_is_authoritative', 0)",
            "ensure_global_attrib('viewport_only', 0)",
            "ensure_global_attrib('visual_point_count', 0)",
            "ensure_global_attrib('visual_primitive_count', 0)",
            "ensure_global_attrib('behavior_point_count', 0)",
            "ensure_global_attrib('behavior_primitive_count', 0)",
            "ensure_global_attrib('output_point_count', 0)",
            "ensure_global_attrib('output_primitive_count', 0)",
            "ensure_global_attrib('output_primitive_types', '')",
            "ensure_global_attrib('agent_state', '')",
            "ensure_global_attrib('current_clip', '')",
            "ensure_global_attrib('target_seat', '')",
            "ensure_global_attrib('how_to_use', '')",
            "ensure_global_attrib('next_step', '')",
            "geo.setGlobalAttribValue('agent_crowd_visual_preview_status', visual_status)",
            "geo.setGlobalAttribValue('visual_source', visual_source)",
            "geo.setGlobalAttribValue('behavior_source', 'agent_crowd_pipeline/OUT_AGENT_CROWD_BEHAVIOR')",
            "geo.setGlobalAttribValue('display_node', 'OUT_AGENT_CROWD_VISUAL_PREVIEW')",
            "geo.setGlobalAttribValue('agent_crowd_behavior_is_authoritative', 1)",
            "geo.setGlobalAttribValue('viewport_only', 1)",
            "geo.setGlobalAttribValue('visual_point_count', visual_point_count)",
            "geo.setGlobalAttribValue('visual_primitive_count', visual_prim_count)",
            "geo.setGlobalAttribValue('behavior_point_count', behavior_point_count)",
            "geo.setGlobalAttribValue('behavior_primitive_count', behavior_prim_count)",
            "geo.setGlobalAttribValue('output_point_count', len(geo.points()))",
            "geo.setGlobalAttribValue('output_primitive_count', len(geo.prims()))",
            "geo.setGlobalAttribValue('output_primitive_types', ', '.join(primitive_types) or 'none')",
            "geo.setGlobalAttribValue('agent_state', state)",
            "geo.setGlobalAttribValue('current_clip', clip)",
            "geo.setGlobalAttribValue('target_seat', target)",
            "geo.setGlobalAttribValue('how_to_use', 'Use OUT_AGENT_CROWD_VISUAL_PREVIEW only for viewport inspection. Simulation/behavior data remains OUT_AGENT_CROWD_BEHAVIOR.')",
            "geo.setGlobalAttribValue('next_step', 'If this still shows only skeleton/proxy, rebuild the character visual source from a FBX/SOP that contains skinned mesh. The Agent/Crowd behavior path is already separated.')",
        ]
    )


def _kinefx_clip_diagnostic_python_sop(files: CrowdPrototypeFiles, plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import hou",
            f"plan = {repr(plan)}",
            f"walk_fbx = {repr(_as_posix(files.walk_fbx))}",
            f"sit_down_fbx = {repr(_as_posix(files.sit_down_fbx))}",
            f"sit_idle_fbx = {repr(_as_posix(files.sit_idle_fbx))}",
            f"walk_time_expression = {repr(_clip_time_expression('walk', plan))}",
            f"sit_down_time_expression = {repr(_clip_time_expression('sit_down', plan))}",
            f"sit_idle_time_expression = {repr(_clip_time_expression('sit_idle', plan))}",
            "node = hou.pwd()",
            "parent = node.parent()",
            "geo = node.geometry()",
            "inputs = node.inputs()",
            "def input_geo(index):",
            "    if len(inputs) <= index or inputs[index] is None:",
            "        return None",
            "    try:",
            "        return inputs[index].geometry()",
            "    except Exception:",
            "        return None",
            "walk_geo = input_geo(0)",
            "sit_down_geo = input_geo(1)",
            "sit_idle_geo = input_geo(2)",
            "runtime_geo = input_geo(3)",
            "geo.clear()",
            "def ensure_global_attrib(name, default):",
            "    attrib = geo.findGlobalAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Global, name, default)",
            "def count_points(source):",
            "    return len(source.points()) if source is not None else 0",
            "def count_prims(source):",
            "    return len(source.prims()) if source is not None else 0",
            "def userdata(name):",
            "    try:",
            "        return parent.userData(name) or ''",
            "    except Exception:",
            "        return ''",
            "loc = plan.get('locomotion') or {}",
            "walk_points = count_points(walk_geo)",
            "walk_prims = count_prims(walk_geo)",
            "runtime_points = count_points(runtime_geo)",
            "runtime_prims = count_prims(runtime_geo)",
            "status = 'walk_clip_loaded' if (walk_points or walk_prims) else 'walk_clip_missing_or_empty'",
            "if runtime_points or runtime_prims:",
            "    status = status + '_runtime_preview_ready'",
            "ensure_global_attrib('kinefx_clip_diagnostic_status', '')",
            "ensure_global_attrib('display_node', '')",
            "ensure_global_attrib('fbx_refresh_status', '')",
            "ensure_global_attrib('fbx_reload_pressed', '')",
            "ensure_global_attrib('fbx_reload_candidates', '')",
            "ensure_global_attrib('fbx_missing_nodes', '')",
            "ensure_global_attrib('fbx_file_parameters', '')",
            "ensure_global_attrib('walk_fbx', '')",
            "ensure_global_attrib('sit_down_fbx', '')",
            "ensure_global_attrib('sit_idle_fbx', '')",
            "ensure_global_attrib('walk_input_point_count', 0)",
            "ensure_global_attrib('walk_input_primitive_count', 0)",
            "ensure_global_attrib('sit_down_input_point_count', 0)",
            "ensure_global_attrib('sit_idle_input_point_count', 0)",
            "ensure_global_attrib('runtime_preview_point_count', 0)",
            "ensure_global_attrib('runtime_preview_primitive_count', 0)",
            "ensure_global_attrib('current_frame', 0.0)",
            "ensure_global_attrib('walk_speed', 0.0)",
            "ensure_global_attrib('walk_distance', 0.0)",
            "ensure_global_attrib('walk_clip_frames', 0)",
            "ensure_global_attrib('walk_time_expression', '')",
            "ensure_global_attrib('sit_down_time_expression', '')",
            "ensure_global_attrib('sit_idle_time_expression', '')",
            "ensure_global_attrib('next_step', '')",
            "geo.setGlobalAttribValue('kinefx_clip_diagnostic_status', status)",
            "geo.setGlobalAttribValue('display_node', 'OUT_KINEFX_CLIP_DIAGNOSTIC')",
            "geo.setGlobalAttribValue('fbx_refresh_status', userdata('smart_crowd_kinefx_fbx_refresh_status') or 'not_attempted')",
            "geo.setGlobalAttribValue('fbx_reload_pressed', userdata('smart_crowd_kinefx_fbx_reload_pressed') or 'none')",
            "geo.setGlobalAttribValue('fbx_reload_candidates', userdata('smart_crowd_kinefx_fbx_reload_candidates') or 'none')",
            "geo.setGlobalAttribValue('fbx_missing_nodes', userdata('smart_crowd_kinefx_fbx_missing_nodes') or 'none')",
            "geo.setGlobalAttribValue('fbx_file_parameters', userdata('smart_crowd_kinefx_fbx_file_parameters') or 'none')",
            "geo.setGlobalAttribValue('walk_fbx', walk_fbx)",
            "geo.setGlobalAttribValue('sit_down_fbx', sit_down_fbx)",
            "geo.setGlobalAttribValue('sit_idle_fbx', sit_idle_fbx)",
            "geo.setGlobalAttribValue('walk_input_point_count', walk_points)",
            "geo.setGlobalAttribValue('walk_input_primitive_count', walk_prims)",
            "geo.setGlobalAttribValue('sit_down_input_point_count', count_points(sit_down_geo))",
            "geo.setGlobalAttribValue('sit_idle_input_point_count', count_points(sit_idle_geo))",
            "geo.setGlobalAttribValue('runtime_preview_point_count', runtime_points)",
            "geo.setGlobalAttribValue('runtime_preview_primitive_count', runtime_prims)",
            "geo.setGlobalAttribValue('current_frame', float(hou.frame()))",
            "geo.setGlobalAttribValue('walk_speed', float(loc.get('walk_speed', 0.0) or 0.0))",
            "geo.setGlobalAttribValue('walk_distance', float(loc.get('walk_distance', 0.0) or 0.0))",
            "geo.setGlobalAttribValue('walk_clip_frames', int(loc.get('walk_clip_frames', 0) or 0))",
            "geo.setGlobalAttribValue('walk_time_expression', walk_time_expression)",
            "geo.setGlobalAttribValue('sit_down_time_expression', sit_down_time_expression)",
            "geo.setGlobalAttribValue('sit_idle_time_expression', sit_idle_time_expression)",
            "geo.setGlobalAttribValue('next_step', 'If walk is still wrong after fbx_refresh_status reports reloaded_fbx_imports, update animation.yaml from Maya so walk_clip_frames/rootMotion match the new walk.fbx.')",
        ]
    )


def _agent_character_status_python_sop(files: CrowdPrototypeFiles, status: str, message: str) -> str:
    return "\n".join(
        [
            "import hou",
            f"status = {repr(status)}",
            f"message = {repr(message)}",
            f"character_fbx = {repr(_as_posix(files.character_fbx))}",
            "geo = hou.pwd().geometry()",
            "geo.clear()",
            "geo.addAttrib(hou.attribType.Global, 'agent_character_status', '')",
            "geo.addAttrib(hou.attribType.Global, 'character_fbx', '')",
            "geo.addAttrib(hou.attribType.Global, 'message', '')",
            "geo.addAttrib(hou.attribType.Global, 'next_step', '')",
            "geo.setGlobalAttribValue('agent_character_status', status)",
            "geo.setGlobalAttribValue('character_fbx', character_fbx)",
            "geo.setGlobalAttribValue('message', message)",
            "geo.setGlobalAttribValue('next_step', 'Use kinefx_imports/OUT_RUNTIME_AGENT_BEHAVIOR for current preview, or configure Agent SOP with a supported rig/shape source.')",
            "name_attr = geo.addAttrib(hou.attribType.Point, 'name', '')",
            "status_attr = geo.addAttrib(hou.attribType.Point, 'agent_character_status', '')",
            "pscale_attr = geo.addAttrib(hou.attribType.Point, 'pscale', 0.4)",
            "cd_attr = geo.addAttrib(hou.attribType.Point, 'Cd', (1.0, 0.25, 0.1))",
            "p = geo.createPoint()",
            "p.setPosition((0.0, 0.0, 0.0))",
            "p.setAttribValue(name_attr, 'agent_character_status')",
            "p.setAttribValue(status_attr, status)",
            "p.setAttribValue(pscale_attr, 0.4)",
            "p.setAttribValue(cd_attr, (1.0, 0.25, 0.1))",
        ]
    )


def _agent_character_diagnostic_python_sop(files: CrowdPrototypeFiles) -> str:
    return "\n".join(
        [
            "import hou",
            f"character_fbx = {repr(_as_posix(files.character_fbx))}",
            "node = hou.pwd()",
            "geo = node.geometry()",
            "inputs = node.inputs()",
            "source_geo = inputs[0].geometry() if inputs and inputs[0] is not None else None",
            "geo.clear()",
            "if source_geo is not None:",
            "    geo.merge(source_geo)",
            "def ensure_global_attrib(name, default):",
            "    attrib = geo.findGlobalAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Global, name, default)",
            "def intrinsic_names(prim):",
            "    for method_name in ('intrinsicNames', 'intrinsicValueNames'):",
            "        method = getattr(prim, method_name, None)",
            "        if method is None:",
            "            continue",
            "        try:",
            "            return list(method())",
            "        except Exception:",
            "            continue",
            "    return []",
            "def intrinsic_value(prim, name):",
            "    try:",
            "        return prim.intrinsicValue(name)",
            "    except Exception as exc:",
            "        return '<{}>'.format(type(exc).__name__)",
            "def compact_value(value):",
            "    if isinstance(value, (tuple, list)):",
            "        if len(value) > 12:",
            "            return '{} values: {}'.format(len(value), list(value[:12]))",
            "        return repr(list(value))",
            "    text = repr(value)",
            "    return text if len(text) <= 160 else text[:157] + '...'",
            "source_point_count = len(source_geo.points()) if source_geo is not None else 0",
            "source_prim_count = len(source_geo.prims()) if source_geo is not None else 0",
            "primitive_types = []",
            "agent_primitive_count = 0",
            "interesting_intrinsic_names = []",
            "shape_intrinsic_values = []",
            "layer_intrinsic_values = []",
            "clip_intrinsic_values = []",
            "if source_geo is not None:",
            "    for prim in source_geo.prims():",
            "        try:",
            "            prim_type = prim.type().name()",
            "        except Exception:",
            "            prim_type = 'unknown'",
            "        if prim_type not in primitive_types:",
            "            primitive_types.append(prim_type)",
            "        names = intrinsic_names(prim)",
            "        interesting = [name for name in names if any(token in name.lower() for token in ('agent', 'shape', 'layer', 'clip', 'rig'))]",
            "        if 'agent' in prim_type.lower() or any('agent' in name.lower() for name in interesting):",
            "            agent_primitive_count += 1",
            "        for name in interesting:",
            "            if name not in interesting_intrinsic_names:",
            "                interesting_intrinsic_names.append(name)",
            "            value_text = '{}={}'.format(name, compact_value(intrinsic_value(prim, name)))",
            "            lowered = name.lower()",
            "            if 'shape' in lowered:",
            "                shape_intrinsic_values.append(value_text)",
            "            if 'layer' in lowered:",
            "                layer_intrinsic_values.append(value_text)",
            "            if 'clip' in lowered:",
            "                clip_intrinsic_values.append(value_text)",
            "if source_geo is None:",
            "    status = 'missing_agent_source_geometry'",
            "elif agent_primitive_count <= 0:",
            "    status = 'no_agent_primitives_found'",
            "elif not shape_intrinsic_values and not layer_intrinsic_values:",
            "    status = 'agent_skeleton_only_or_shape_library_not_visible'",
            "else:",
            "    status = 'agent_shape_or_layer_intrinsics_found'",
            "next_step = 'Display OUT_AGENT_CHARACTER_DIAGNOSTIC and inspect Detail attributes. If shape/layer values are empty, re-export character.fbx with skinned mesh or configure Agent SOP shape/layer import.'",
            "ensure_global_attrib('agent_character_diagnostic', '')",
            "ensure_global_attrib('character_fbx', '')",
            "ensure_global_attrib('source_point_count', 0)",
            "ensure_global_attrib('source_prim_count', 0)",
            "ensure_global_attrib('primitive_types', '')",
            "ensure_global_attrib('agent_primitive_count', 0)",
            "ensure_global_attrib('interesting_intrinsics', '')",
            "ensure_global_attrib('shape_intrinsics', '')",
            "ensure_global_attrib('layer_intrinsics', '')",
            "ensure_global_attrib('clip_intrinsics', '')",
            "ensure_global_attrib('next_step', '')",
            "geo.setGlobalAttribValue('agent_character_diagnostic', status)",
            "geo.setGlobalAttribValue('character_fbx', character_fbx)",
            "geo.setGlobalAttribValue('source_point_count', source_point_count)",
            "geo.setGlobalAttribValue('source_prim_count', source_prim_count)",
            "geo.setGlobalAttribValue('primitive_types', ', '.join(primitive_types) or 'none')",
            "geo.setGlobalAttribValue('agent_primitive_count', agent_primitive_count)",
            "geo.setGlobalAttribValue('interesting_intrinsics', ', '.join(interesting_intrinsic_names) or 'none')",
            "geo.setGlobalAttribValue('shape_intrinsics', '; '.join(shape_intrinsic_values) or 'none')",
            "geo.setGlobalAttribValue('layer_intrinsics', '; '.join(layer_intrinsic_values) or 'none')",
            "geo.setGlobalAttribValue('clip_intrinsics', '; '.join(clip_intrinsic_values) or 'none')",
            "geo.setGlobalAttribValue('next_step', next_step)",
            "if len(geo.points()) == 0:",
            "    name_attr = geo.addAttrib(hou.attribType.Point, 'name', '')",
            "    status_attr = geo.addAttrib(hou.attribType.Point, 'agent_character_diagnostic', '')",
            "    pscale_attr = geo.addAttrib(hou.attribType.Point, 'pscale', 0.25)",
            "    cd_attr = geo.addAttrib(hou.attribType.Point, 'Cd', (1.0, 0.25, 0.1))",
            "    p = geo.createPoint()",
            "    p.setPosition((0.0, 0.0, 0.0))",
            "    p.setAttribValue(name_attr, 'agent_character_diagnostic')",
            "    p.setAttribValue(status_attr, status)",
            "    p.setAttribValue(pscale_attr, 0.25)",
            "    p.setAttribValue(cd_attr, (1.0, 0.25, 0.1))",
        ]
    )


def _runtime_dop_result_preview_python_sop(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import hou",
            f"plan = {repr(plan)}",
            "node = hou.pwd()",
            "geo = node.geometry()",
            "inputs = node.inputs()",
            "driver_geo = inputs[0].geometry() if inputs and inputs[0] is not None else None",
            "geo.clear()",
            "if driver_geo is not None:",
            "    geo.merge(driver_geo)",
            "def attr_value(point, name, default=None):",
            "    attrib = point.geometry().findPointAttrib(name)",
            "    if attrib is None:",
            "        return default",
            "    try:",
            "        value = point.attribValue(attrib)",
            "    except Exception:",
            "        return default",
            "    return default if value is None else value",
            "def ensure_point_attrib(name, default):",
            "    attrib = geo.findPointAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Point, name, default)",
            "def ensure_global_attrib(name, default):",
            "    attrib = geo.findGlobalAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Global, name, default)",
            "def node_type_name(item):",
            "    if item is None:",
            "        return ''",
            "    try:",
            "        t = item.type()",
            "        return t.name() if t is not None else ''",
            "    except Exception:",
            "        return ''",
            "def path_values(item):",
            "    if item is None:",
            "        return {}",
            "    explicit = ('soppath', 'soppath1', 'sop_path', 'sop_path1', 'source', 'sourcepath', 'source_path', 'geopath', 'geometrypath', 'geometry_path', 'objpath', 'objpath1')",
            "    values = {}",
            "    for parm_name in explicit:",
            "        parm = item.parm(parm_name)",
            "        if parm is None:",
            "            continue",
            "        try:",
            "            value = parm.eval()",
            "        except Exception:",
            "            continue",
            "        if isinstance(value, str) and value:",
            "            values[parm_name] = value",
            "    if values:",
            "        return values",
            "    try:",
            "        parms = item.parms()",
            "    except Exception:",
            "        return values",
            "    for parm in parms:",
            "        try:",
            "            template = parm.parmTemplate()",
            "            label = template.label().lower()",
            "            name = parm.name().lower()",
            "            value = parm.eval()",
            "        except Exception:",
            "            continue",
            "        text = '{} {}'.format(name, label)",
            "        if 'path' not in text:",
            "            continue",
            "        if 'sop' not in text and 'source' not in text and 'geometry' not in text:",
            "            continue",
            "        if isinstance(value, str) and value:",
            "            values[parm.name()] = value",
            "    return values",
            "root = node.parent().parent() if node.parent() is not None else None",
            "dop_network = root.node('crowd_solver_dop_bridge') if root is not None else None",
            "source_geometry = dop_network.node('DOP_SOURCE_GEOMETRY') if dop_network is not None else None",
            "solver = dop_network.node('DOP_CROWD_SOLVER') if dop_network is not None else None",
            "runtime_path = source_geometry.userData('smart_crowd_source_sop_path') if source_geometry is not None else ''",
            "empty_path = source_geometry.userData('smart_crowd_empty_source_sop_path') if source_geometry is not None else ''",
            "source_mode = source_geometry.userData('smart_crowd_source_mode') if source_geometry is not None else ''",
            "values = path_values(source_geometry)",
            "current_path = ', '.join('{}={}'.format(key, value) for key, value in values.items())",
            "source_is_safe = 1 if empty_path and values and all(value == empty_path for value in values.values()) else 0",
            "dop_network_path = dop_network.path() if dop_network is not None else ''",
            "solver_path = solver.path() if solver is not None else ''",
            "solver_type = node_type_name(solver)",
            "ready_attr = ensure_point_attrib('dop_preview_ready', 0)",
            "source_mode_attr = ensure_point_attrib('dop_source_mode', '')",
            "runtime_path_attr = ensure_point_attrib('dop_runtime_source_sop_path', '')",
            "current_path_attr = ensure_point_attrib('dop_current_source_path', '')",
            "safe_attr = ensure_point_attrib('dop_source_is_safe', 0)",
            "solver_attr = ensure_point_attrib('dop_solver_path', '')",
            "points = list(geo.points())",
            "ready_count = 0",
            "clips = []",
            "states = []",
            "for point in points:",
            "    clip = str(attr_value(point, 'clipname', '') or attr_value(point, 'clip', '') or attr_value(point, 'agentclip', '') or attr_value(point, 'current_clip', '') or '')",
            "    state = str(attr_value(point, 'state', '') or attr_value(point, 'crowd_state', '') or attr_value(point, 'agent_state', '') or '')",
            "    ready = 1 if clip else 0",
            "    point.setAttribValue(ready_attr, ready)",
            "    point.setAttribValue(source_mode_attr, source_mode or 'unknown')",
            "    point.setAttribValue(runtime_path_attr, runtime_path or '')",
            "    point.setAttribValue(current_path_attr, current_path or '')",
            "    point.setAttribValue(safe_attr, source_is_safe)",
            "    point.setAttribValue(solver_attr, solver_path or '')",
            "    ready_count += ready",
            "    if clip and clip not in clips:",
            "        clips.append(clip)",
            "    if state and state not in states:",
            "        states.append(state)",
            "ensure_global_attrib('dop_preview_mode', '')",
            "ensure_global_attrib('driver_source', '')",
            "ensure_global_attrib('source_mode', '')",
            "ensure_global_attrib('runtime_source_sop_path', '')",
            "ensure_global_attrib('current_source_path', '')",
            "ensure_global_attrib('source_path_is_safe', 0)",
            "ensure_global_attrib('empty_source_sop_path', '')",
            "ensure_global_attrib('dop_network_path', '')",
            "ensure_global_attrib('dop_solver_path', '')",
            "ensure_global_attrib('solver_node_type', '')",
            "ensure_global_attrib('agent_count', 0)",
            "ensure_global_attrib('ready_agent_count', 0)",
            "ensure_global_attrib('clip_summary', '')",
            "ensure_global_attrib('state_summary', '')",
            "ensure_global_attrib('how_to_check', '')",
            "ensure_global_attrib('next_step', '')",
            "geo.setGlobalAttribValue('dop_preview_mode', 'runtime_dop_handoff_preview')",
            "geo.setGlobalAttribValue('driver_source', 'OUT_AGENT_CLIP_BRIDGE' if driver_geo is not None else 'missing_runtime_driver')",
            "geo.setGlobalAttribValue('source_mode', source_mode or 'unknown')",
            "geo.setGlobalAttribValue('runtime_source_sop_path', runtime_path or '')",
            "geo.setGlobalAttribValue('current_source_path', current_path or '')",
            "geo.setGlobalAttribValue('source_path_is_safe', source_is_safe)",
            "geo.setGlobalAttribValue('empty_source_sop_path', empty_path or '')",
            "geo.setGlobalAttribValue('dop_network_path', dop_network_path)",
            "geo.setGlobalAttribValue('dop_solver_path', solver_path)",
            "geo.setGlobalAttribValue('solver_node_type', solver_type)",
            "geo.setGlobalAttribValue('agent_count', len(points))",
            "geo.setGlobalAttribValue('ready_agent_count', ready_count)",
            "geo.setGlobalAttribValue('clip_summary', ', '.join(clips) or 'none')",
            "geo.setGlobalAttribValue('state_summary', ', '.join(states) or 'none')",
            "geo.setGlobalAttribValue('how_to_check', 'Display OUT_RUNTIME_DOP_RESULT. Points show runtime driver agents; Detail shows DOP source safety and Solver path.')",
            "geo.setGlobalAttribValue('next_step', 'If source_path_is_safe is 1 after smoke tests, the DOP source was returned to OUT_EMPTY_CROWD_SOURCE safely.')",
        ]
    )


def _runtime_timeline_sample_preview_python_sop(rows: list[dict[str, Any]], *, allow_solver_cook: bool) -> str:
    return "\n".join(
        [
            "import hou",
            f"rows = {repr(rows)}",
            f"allow_solver_cook = {int(bool(allow_solver_cook))}",
            "geo = hou.pwd().geometry()",
            "geo.clear()",
            "def ensure_point_attrib(name, default):",
            "    attrib = geo.findPointAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Point, name, default)",
            "def ensure_global_attrib(name, default):",
            "    attrib = geo.findGlobalAttrib(name)",
            "    if attrib is not None:",
            "        return attrib",
            "    return geo.addAttrib(hou.attribType.Global, name, default)",
            "def parse_summary(text):",
            "    result = {}",
            "    for part in str(text or '').split(','):",
            "        item = part.strip()",
            "        if not item or ':' not in item:",
            "            continue",
            "        name, value = item.split(':', 1)",
            "        result[name.strip()] = value.strip()",
            "    return result",
            "frame_attr = ensure_point_attrib('sample_frame', 0)",
            "agent_attr = ensure_point_attrib('agent_name', '')",
            "clip_attr = ensure_point_attrib('sample_clip', '')",
            "state_attr = ensure_point_attrib('sample_state', '')",
            "status_attr = ensure_point_attrib('sample_status', '')",
            "ready_attr = ensure_point_attrib('sample_ready_agent_count', 0)",
            "safe_attr = ensure_point_attrib('sample_source_path_is_safe', 0)",
            "smoke_attr = ensure_point_attrib('sample_smoke_status', '')",
            "solver_safe_attr = ensure_point_attrib('sample_solver_safe_after', 0)",
            "solver_source_safe_attr = ensure_point_attrib('sample_solver_source_is_safe_after', 0)",
            "source_path_attr = ensure_point_attrib('sample_current_source_path', '')",
            "pscale_attr = ensure_point_attrib('pscale', 0.1)",
            "cd_attr = ensure_point_attrib('Cd', (0.2, 0.8, 1.0))",
            "observed_clips = []",
            "observed_states = []",
            "sample_ok = 0",
            "safe_count = 0",
            "solver_safe_count = 0",
            "agent_sample_count = 0",
            "for row_index, row in enumerate(rows):",
            "    frame = int(row.get('frame', row_index + 1) or row_index + 1)",
            "    clips = parse_summary(row.get('agent_clip_summary', '') or row.get('clip_summary', ''))",
            "    states = parse_summary(row.get('agent_state_summary', '') or row.get('state_summary', ''))",
            "    agent_names = sorted(set(clips.keys()) | set(states.keys()))",
            "    if not agent_names:",
            "        agent_names = ['sample_{:03d}'.format(row_index + 1)]",
            "        clips[agent_names[0]] = str(row.get('clip_summary', '') or 'none')",
            "        states[agent_names[0]] = str(row.get('state_summary', '') or 'unknown')",
            "    if row.get('status') == 'sample_ok':",
            "        sample_ok += 1",
            "    if int(row.get('source_path_is_safe', 0) or 0):",
            "        safe_count += 1",
            "    solver_safe = int(row.get('solver_safe_after', 0) or 0)",
            "    solver_source_safe = int(row.get('solver_source_is_safe_after', 0) or 0)",
            "    if not allow_solver_cook or (solver_safe and solver_source_safe):",
            "        solver_safe_count += 1",
            "    for agent_index, agent_name in enumerate(agent_names):",
            "        clip = clips.get(agent_name, 'none') or 'none'",
            "        state = states.get(agent_name, 'unknown') or 'unknown'",
            "        if clip not in ('', 'none') and clip not in observed_clips:",
            "            observed_clips.append(clip)",
            "        if state not in ('', 'unknown') and state not in observed_states:",
            "            observed_states.append(state)",
            "        point = geo.createPoint()",
            "        point.setPosition((float(frame), 0.0, float(agent_index)))",
            "        point.setAttribValue(frame_attr, frame)",
            "        point.setAttribValue(agent_attr, agent_name)",
            "        point.setAttribValue(clip_attr, clip)",
            "        point.setAttribValue(state_attr, state)",
            "        point.setAttribValue(status_attr, str(row.get('status', '')))",
            "        point.setAttribValue(ready_attr, int(row.get('ready_agent_count', 0) or 0))",
            "        point.setAttribValue(safe_attr, int(row.get('source_path_is_safe', 0) or 0))",
            "        point.setAttribValue(smoke_attr, str(row.get('smoke_status', '')))",
            "        point.setAttribValue(solver_safe_attr, solver_safe)",
            "        point.setAttribValue(solver_source_safe_attr, solver_source_safe)",
            "        point.setAttribValue(source_path_attr, str(row.get('current_source_path', '') or ''))",
            "        point.setAttribValue(pscale_attr, 0.22 if clip not in ('', 'none') else 0.12)",
            "        color = (0.55, 0.55, 0.55)",
            "        if clip == 'walk':",
            "            color = (0.2, 0.55, 1.0)",
            "        elif clip == 'sit_down':",
            "            color = (1.0, 0.45, 0.1)",
            "        elif clip == 'sit_idle':",
            "            color = (0.1, 0.8, 0.25)",
            "        point.setAttribValue(cd_attr, color)",
            "        agent_sample_count += 1",
            "ensure_global_attrib('timeline_sample_mode', '')",
            "ensure_global_attrib('allow_solver_cook', 0)",
            "ensure_global_attrib('sample_count', 0)",
            "ensure_global_attrib('sampled_frames', '')",
            "ensure_global_attrib('agent_sample_count', 0)",
            "ensure_global_attrib('timeline_samples_are_static', 0)",
            "ensure_global_attrib('all_samples_ok', 0)",
            "ensure_global_attrib('all_sources_safe', 0)",
            "ensure_global_attrib('all_solver_samples_safe', 0)",
            "ensure_global_attrib('observed_clips', '')",
            "ensure_global_attrib('observed_states', '')",
            "ensure_global_attrib('how_to_check', '')",
            "geo.setGlobalAttribValue('timeline_sample_mode', 'runtime_dop_timeline_samples')",
            "geo.setGlobalAttribValue('allow_solver_cook', allow_solver_cook)",
            "geo.setGlobalAttribValue('sample_count', len(rows))",
            "geo.setGlobalAttribValue('sampled_frames', ', '.join(str(int(row.get('frame', 0) or 0)) for row in rows))",
            "geo.setGlobalAttribValue('agent_sample_count', agent_sample_count)",
            "geo.setGlobalAttribValue('timeline_samples_are_static', 1)",
            "geo.setGlobalAttribValue('all_samples_ok', int(sample_ok == len(rows) if rows else 0))",
            "geo.setGlobalAttribValue('all_sources_safe', int(safe_count == len(rows) if rows else 0))",
            "geo.setGlobalAttribValue('all_solver_samples_safe', int(solver_safe_count == len(rows) if rows else 0))",
            "geo.setGlobalAttribValue('observed_clips', ', '.join(observed_clips) or 'none')",
            "geo.setGlobalAttribValue('observed_states', ', '.join(observed_states) or 'none')",
            "geo.setGlobalAttribValue('how_to_check', 'Display OUT_RUNTIME_TIMELINE_SAMPLES. Each point is one sampled frame/agent; color maps walk, sit_down, sit_idle, or none.')",
        ]
    )


def _apply_behavior_transform_python_sop(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "import math",
            "import hou",
            f"plan = {repr(plan)}",
            "node = hou.pwd()",
            "geo = node.geometry()",
            "source = node.inputs()[0].geometry() if node.inputs() else None",
            "geo.clear()",
            "if source is not None:",
            "    geo.merge(source)",
            "loc = plan['locomotion']",
            "seat_v = loc['seat_position']",
            "app_v = loc['approach_position']",
            "start_v = loc['start_position']",
            "seat = (float(seat_v['x']), float(seat_v['y']), float(seat_v['z']))",
            "app = (float(app_v['x']), float(app_v['y']), float(app_v['z']))",
            "start = (float(start_v['x']), float(start_v['y']), float(start_v['z']))",
            "frame = float(hou.frame())",
            "walk_start = float(loc['walk_start_frame'])",
            "walk_end = float(loc['walk_end_frame'])",
            "align_end = float(loc['align_end_frame'])",
            "sit_down_end = float(loc['sit_down_end_frame'])",
            "def clamp01(value):",
            "    return max(0.0, min(1.0, float(value)))",
            "def lerp(a, b, t):",
            "    return tuple(float(a[i]) + (float(b[i]) - float(a[i])) * t for i in range(3))",
            "def normalized_xz(a, b, fallback=(0.0, 0.0, 1.0)):",
            "    dx = float(b[0]) - float(a[0])",
            "    dz = float(b[2]) - float(a[2])",
            "    length = math.sqrt(dx * dx + dz * dz)",
            "    if length <= 1e-8:",
            "        return fallback",
            "    return (dx / length, 0.0, dz / length)",
            "def yaw_from_heading(heading):",
            "    return math.atan2(float(heading[0]), float(heading[2]))",
            "def blend_yaw(a, b, t):",
            "    delta = (float(b) - float(a) + math.pi) % (math.tau) - math.pi",
            "    return float(a) + delta * clamp01(t)",
            "turn_t = 0.0",
            "if frame <= walk_end:",
            "    current_step = 'Walk'",
            "    current_clip = 'walk'",
            "    t = clamp01((frame - walk_start) / max(walk_end - walk_start, 1.0))",
            "    agent_pos = lerp(start, app, t)",
            "    turn_t = 0.0",
            "elif frame <= align_end:",
            "    current_step = 'Align'",
            "    current_clip = 'walk'",
            "    t = clamp01((frame - walk_end) / max(align_end - walk_end, 1.0))",
            "    agent_pos = app",
            "    turn_t = t",
            "elif frame <= sit_down_end:",
            "    current_step = 'Sit Down'",
            "    current_clip = 'sit_down'",
            "    t = clamp01((frame - align_end) / max(sit_down_end - align_end, 1.0))",
            "    agent_pos = lerp(app, seat, t)",
            "    turn_t = 1.0",
            "else:",
            "    current_step = 'Sit Idle'",
            "    current_clip = 'sit_idle'",
            "    agent_pos = seat",
            "    turn_t = 1.0",
            "walk_heading = normalized_xz(start, app, (0.0, 0.0, 1.0))",
            "sit_heading = normalized_xz(app, seat, walk_heading)",
            "yaw = blend_yaw(yaw_from_heading(walk_heading), yaw_from_heading(sit_heading), turn_t)",
            "behavior_heading = (math.sin(yaw), 0.0, math.cos(yaw))",
            "cos_y = math.cos(yaw)",
            "sin_y = math.sin(yaw)",
            "points = list(geo.points())",
            "if points:",
            "    min_x = min(p.position()[0] for p in points)",
            "    max_x = max(p.position()[0] for p in points)",
            "    min_y = min(p.position()[1] for p in points)",
            "    min_z = min(p.position()[2] for p in points)",
            "    max_z = max(p.position()[2] for p in points)",
            "    pivot = ((min_x + max_x) * 0.5, min_y, (min_z + max_z) * 0.5)",
            "    for p in points:",
            "        old = p.position()",
            "        lx = float(old[0]) - pivot[0]",
            "        ly = float(old[1]) - pivot[1]",
            "        lz = float(old[2]) - pivot[2]",
            "        rx = lx * cos_y + lz * sin_y",
            "        rz = -lx * sin_y + lz * cos_y",
            "        p.setPosition((agent_pos[0] + rx, agent_pos[1] + ly, agent_pos[2] + rz))",
            "geo.addAttrib(hou.attribType.Global, 'current_step', '')",
            "geo.addAttrib(hou.attribType.Global, 'current_clip', '')",
            "geo.addAttrib(hou.attribType.Global, 'target_seat', '')",
            "geo.addAttrib(hou.attribType.Global, 'behavior_position', '')",
            "geo.addAttrib(hou.attribType.Global, 'behavior_heading', '')",
            "geo.addAttrib(hou.attribType.Global, 'walk_speed', 0.0)",
            "geo.addAttrib(hou.attribType.Global, 'walk_distance', 0.0)",
            "geo.addAttrib(hou.attribType.Global, 'frame_ranges', '')",
            "geo.setGlobalAttribValue('current_step', current_step)",
            "geo.setGlobalAttribValue('current_clip', current_clip)",
            "geo.setGlobalAttribValue('target_seat', plan['goal']['interaction_point_id'])",
            "geo.setGlobalAttribValue('behavior_position', '{:.3f}, {:.3f}, {:.3f}'.format(agent_pos[0], agent_pos[1], agent_pos[2]))",
            "geo.setGlobalAttribValue('behavior_heading', '{:.3f}, {:.3f}, {:.3f}'.format(behavior_heading[0], behavior_heading[1], behavior_heading[2]))",
            "geo.setGlobalAttribValue('walk_speed', float(loc['walk_speed']))",
            "geo.setGlobalAttribValue('walk_distance', float(loc['walk_distance']))",
            "geo.setGlobalAttribValue('frame_ranges', '{}-{} walk, {}-{} align_to_seat, {}-{} sit_down, {}+ sit_idle'.format(loc['walk_start_frame'], loc['walk_end_frame'], loc['walk_end_frame'] + 1, loc['align_end_frame'], loc['align_end_frame'] + 1, loc['sit_down_end_frame'], loc['sit_idle_start_frame']))",
        ]
    )


def _selected_point(interaction_data: dict[str, Any], point_id: str) -> dict[str, Any]:
    for interaction in interaction_data.get("interactions") or []:
        for point in interaction.get("points") or []:
            if str(point.get("id") or "") == point_id:
                return dict(point)
    return {}


def _agent_clip_test_specs() -> tuple[dict[str, str], ...]:
    return (
        {"clip": "walk", "node": "TEST_AGENTCLIP_WALK", "input": "OUT_AGENT_CLIP_WALK_TEST_INPUT"},
        {"clip": "sit_down", "node": "TEST_AGENTCLIP_SIT_DOWN", "input": "OUT_AGENT_CLIP_SIT_DOWN_TEST_INPUT"},
        {"clip": "sit_idle", "node": "TEST_AGENTCLIP_SIT_IDLE", "input": "OUT_AGENT_CLIP_SIT_IDLE_TEST_INPUT"},
    )


def _agent_clip_test_spec(clip_name: str) -> dict[str, str]:
    requested = str(clip_name or "").strip()
    for spec in _agent_clip_test_specs():
        if spec["clip"] == requested:
            return spec
    valid = ", ".join(spec["clip"] for spec in _agent_clip_test_specs())
    raise ValueError(f"clip_name must be one of: {valid}")


def _agent_clip_experiment_node(hou: Any, *, parent_path: str, node_name: str):
    root = _prototype_root_node(hou, parent_path=parent_path, node_name=node_name)
    experiment = root.node("agent_clip_experiment")
    if experiment is None:
        raise RuntimeError(f"agent_clip_experiment was not found under: {root.path()}")
    return experiment


def _runtime_driver_source_node(root: Any):
    for path in (
        "agent_crowd_pipeline/OUT_AGENT_CROWD_BEHAVIOR",
        "agent_crowd_pipeline/OUT_AGENT_CLIP_BRIDGE",
        "agent_crowd_pipeline/OUT_CROWD_CLIP_STATE_DRIVER",
        "agent_crowd_pipeline/OUT_BEHAVIOR_AGENT_POINTS",
        "runtime_behavior_preview/OUT_RUNTIME_BEHAVIOR",
    ):
        try:
            node = root.node(path)
        except Exception:
            node = None
        if node is not None:
            return node
    return None


def _plan_from_root_user_data(root: Any) -> dict[str, Any]:
    import ast

    if root is None:
        return {}
    try:
        text = root.userData("smart_crowd_plan") or ""
    except Exception:
        text = ""
    if not text:
        return {}
    try:
        value = ast.literal_eval(text)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _runtime_timeline_sample_frames(
    root: Any,
    *,
    frames: list[int] | tuple[int, ...] | None,
    step: int,
    end_frame: int,
) -> list[int]:
    if frames:
        values = [max(1, int(frame)) for frame in frames]
    else:
        plan = _plan_from_root_user_data(root)
        loc = plan.get("locomotion") or {}
        candidate_values = [
            1,
            int(loc.get("walk_end_frame", 0) or 0),
            int(loc.get("align_end_frame", 0) or 0),
            int(loc.get("sit_down_end_frame", 0) or 0),
            int(loc.get("sit_idle_start_frame", 0) or 0),
        ]
        step_value = max(1, int(step or 24))
        max_frame = max(int(end_frame or 240), max(candidate_values or [1]))
        values = list(range(1, max_frame + 1, step_value))
        values.extend(candidate_values)
        values.append(max_frame)
    return sorted({frame for frame in values if frame >= 1})


def _cook_runtime_driver_chain(root: Any) -> None:
    for path in (
        "agent_crowd_pipeline/OUT_AGENT_SOURCE_POINTS",
        "interaction_points/OUT_SEAT_POINTS",
        "runtime_behavior_preview/OUT_RUNTIME_BEHAVIOR",
        "agent_crowd_pipeline/OUT_BEHAVIOR_AGENT_POINTS",
        "agent_crowd_pipeline/OUT_CROWD_CLIP_STATE_DRIVER",
        "agent_crowd_pipeline/OUT_AGENT_CLIP_BRIDGE",
        "agent_crowd_pipeline/OUT_AGENT_CROWD_BEHAVIOR",
    ):
        try:
            node = root.node(path)
        except Exception:
            node = None
        if node is None:
            continue
        try:
            node.cook(force=True)
        except Exception:
            continue


def _set_runtime_dop_safe_state(root: Any) -> bool:
    try:
        dop_network = root.node("crowd_solver_dop_bridge")
    except Exception:
        dop_network = None
    if dop_network is None:
        return False
    try:
        solver = dop_network.node("DOP_CROWD_SOLVER")
    except Exception:
        solver = None
    if solver is None:
        return False
    return _set_dop_solver_safe_state(solver, True, disconnect_input=True)


def _agent_point_timeline_summary(geo: Any) -> dict[str, str]:
    clip_parts: list[str] = []
    state_parts: list[str] = []
    for index, point in enumerate(geo.points()):
        entity = str(_point_attrib_value(point, "entity_type", "") or "")
        if entity and entity != "agent":
            continue
        name = str(
            _point_attrib_value(point, "name", "")
            or _point_attrib_value(point, "agentname", "")
            or f"agent_{index + 1:03d}"
        )
        clip = str(
            _point_attrib_value(point, "clipname", "")
            or _point_attrib_value(point, "clip", "")
            or _point_attrib_value(point, "agentclip", "")
            or _point_attrib_value(point, "current_clip", "")
            or "none"
        )
        state = str(
            _point_attrib_value(point, "state", "")
            or _point_attrib_value(point, "crowd_state", "")
            or _point_attrib_value(point, "agent_state", "")
            or "unknown"
        )
        clip_parts.append(f"{name}:{clip}")
        state_parts.append(f"{name}:{state}")
    return {
        "clips": ", ".join(clip_parts) or "none",
        "states": ", ".join(state_parts) or "none",
    }


def _summary_values(text: Any) -> list[str]:
    values: list[str] = []
    for part in str(text or "").split(","):
        item = part.strip()
        if not item:
            continue
        value = item.split(":", 1)[1].strip() if ":" in item else item
        if value and value not in values:
            values.append(value)
    return values


def _point_attrib_value(point: Any, name: str, default: Any = None) -> Any:
    try:
        attrib = point.geometry().findPointAttrib(name)
    except Exception:
        return default
    if attrib is None:
        return default
    try:
        value = point.attribValue(attrib)
    except Exception:
        try:
            value = point.attribValue(name)
        except Exception:
            return default
    return default if value is None else value


def _prototype_root_node(hou: Any, *, parent_path: str, node_name: str):
    root_path = "{}/{}".format(parent_path.rstrip("/"), node_name)
    root = hou.node(root_path)
    if root is None:
        parent = hou.node(parent_path)
        root = parent.node(node_name) if parent is not None else None
    if root is None:
        raise RuntimeError(f"Smart Crowd prototype node was not found: {root_path}")
    return root


def _agent_clip_active_state(experiment: Any) -> dict[str, list[str]]:
    active_nodes: list[str] = []
    bypassed_nodes: list[str] = []
    missing_nodes: list[str] = []
    for spec in _agent_clip_test_specs():
        node = experiment.node(spec["node"])
        if node is None:
            missing_nodes.append(spec["node"])
            continue
        if _node_is_bypassed(node):
            bypassed_nodes.append(spec["node"])
        else:
            active_nodes.append(spec["node"])
    return {
        "active_nodes": active_nodes,
        "bypassed_nodes": bypassed_nodes,
        "missing_nodes": missing_nodes,
    }


def _global_attrib_values(node: Any) -> dict[str, Any]:
    if node is None:
        return {}
    try:
        geo = node.geometry()
    except Exception:
        return {}
    if geo is None:
        try:
            inputs = node.inputs()
        except Exception:
            inputs = ()
        for input_node in inputs:
            values = _global_attrib_values(input_node)
            if values:
                return values
        return {}
    values: dict[str, Any] = {}
    for attrib in geo.globalAttribs():
        name = attrib.name()
        try:
            values[name] = geo.attribValue(attrib)
        except Exception:
            try:
                values[name] = geo.attribValue(name)
            except Exception:
                values[name] = None
    return values


def _create_or_update_root(
    parent: Any,
    node_type: str,
    name: str,
    *,
    replace_existing: bool,
    update_existing: bool,
):
    existing = parent.node(name)
    if existing is not None and replace_existing:
        existing.destroy()
        existing = None
    if existing is not None and update_existing:
        return existing, True
    if existing is None:
        return parent.createNode(node_type, name), False
    node = parent.createNode(node_type)
    node.setName(name, unique_name=True)
    return node, False


def _child_or_create(parent: Any, node_type: str, name: str):
    child = parent.node(name)
    if child is not None:
        return child
    return parent.createNode(node_type, name)


def _clear_children(node: Any) -> None:
    for child in node.children():
        child.destroy()


def _create_first_available(hou: Any, parent: Any, type_names: tuple[str, ...], name: str):
    category = parent.childTypeCategory()
    for type_name in type_names:
        if hou.nodeType(category, type_name) is None:
            continue
        return parent.createNode(type_name, name)
    return None


def _available_type_name(hou: Any, parent: Any, type_names: tuple[str, ...]) -> str:
    category = parent.childTypeCategory()
    for type_name in type_names:
        if hou.nodeType(category, type_name) is not None:
            return type_name
    return ""


def _available_crowd_solver_type_name(hou: Any, parent: Any) -> str:
    exact = (
        "crowdsolver",
        "crowdsolver::2.0",
        "crowd::crowdsolver",
        "crowd::crowdsolver::2.0",
    )
    type_name = _available_type_name(hou, parent, exact)
    if type_name:
        return type_name
    matches = _matching_node_type_names(parent.childTypeCategory(), ("crowd", "solver"))
    for name in matches:
        lower = name.lower()
        if "source" in lower or "object" in lower:
            continue
        return name
    return ""


def _available_dop_crowd_solver_type_name(hou: Any, dop_network: Any) -> str:
    exact = (
        "crowdsolver::3.0",
        "crowdsolver::2.0",
        "crowdsolver",
        "crowd::crowdsolver::3.0",
        "crowd::crowdsolver::2.0",
        "crowd::crowdsolver",
    )
    category = dop_network.childTypeCategory()
    for type_name in exact:
        if hou.nodeType(category, type_name) is not None:
            return type_name
    matches = _matching_node_type_names(category, ("crowd", "solver"))
    return matches[-1] if matches else ""


def _available_dop_crowd_object_type_name(hou: Any, dop_network: Any) -> str:
    exact = (
        "crowdobject::3.0",
        "crowdobject::2.0",
        "crowdobject",
        "crowd::crowdobject::3.0",
        "crowd::crowdobject::2.0",
        "crowd::crowdobject",
    )
    category = dop_network.childTypeCategory()
    for type_name in exact:
        if hou.nodeType(category, type_name) is not None:
            return type_name
    matches = _matching_node_type_names(category, ("crowd", "object"))
    for name in reversed(matches):
        lower = name.lower()
        if "source" in lower or "solver" in lower:
            continue
        return name
    return ""


def _available_dop_sop_geometry_type_name(hou: Any, dop_network: Any) -> str:
    exact = (
        "sopgeometry",
        "sopgeometry::2.0",
        "sopgeo",
        "sopgeo::2.0",
    )
    category = dop_network.childTypeCategory()
    for type_name in exact:
        if hou.nodeType(category, type_name) is not None:
            return type_name
    matches = _matching_node_type_names(category, ("sop", "geometry"))
    for name in reversed(matches):
        if "solver" not in name.lower():
            return name
    return ""


def _available_dop_merge_type_name(hou: Any, dop_network: Any) -> str:
    exact = ("merge", "merge::2.0")
    category = dop_network.childTypeCategory()
    for type_name in exact:
        if hou.nodeType(category, type_name) is not None:
            return type_name
    matches = _matching_node_type_names(category, ("merge",))
    return matches[0] if matches else ""


def _hou_category(hou: Any, function_name: str):
    func = getattr(hou, function_name, None)
    if not callable(func):
        return None
    try:
        return func()
    except Exception:
        return None


def _matching_node_type_names(category: Any, required_terms: tuple[str, ...]) -> list[str]:
    if category is None:
        return []
    try:
        node_types = category.nodeTypes()
    except Exception:
        return []
    matches: list[str] = []
    for name, node_type in node_types.items():
        try:
            description = node_type.description()
        except Exception:
            description = ""
        text = f"{name} {description}".lower()
        if all(term.lower() in text for term in required_terms):
            matches.append(name)
    return sorted(matches)


def _crowd_solver_location_hint(sop_solver: list[str], dop_solver: list[str]) -> str:
    if sop_solver:
        return "sop_solver_available"
    if dop_solver:
        return "dop_network_required"
    return "crowd_solver_not_available"


def _create_named_node_safely(parent: Any, type_name: str, name: str):
    try:
        return parent.createNode(type_name, name)
    except Exception:
        return None


def _node_type_name(node: Any) -> str:
    if node is None:
        return ""
    try:
        node_type = node.type()
    except Exception:
        return ""
    if node_type is None:
        return ""
    try:
        return node_type.name()
    except Exception:
        return ""


def _input_summary(node: Any) -> str:
    if node is None:
        return ""
    try:
        inputs = node.inputs()
    except Exception:
        return ""
    parts = []
    for index, input_node in enumerate(inputs):
        if input_node is None:
            continue
        try:
            path = input_node.path()
        except Exception:
            path = input_node.name() if hasattr(input_node, "name") else ""
        parts.append(f"{index}:{path}")
    return ", ".join(parts) or "none"


def _ensure_named_child(parent: Any, type_name: str, name: str):
    child = parent.node(name)
    if child is not None:
        return child
    return _create_named_node_safely(parent, type_name, name)


def _ensure_empty_crowd_source_sop(hou: Any, parent: Any):
    node = parent.node("build_empty_crowd_source")
    if node is None:
        node = parent.createNode("python", "build_empty_crowd_source")
    parm = node.parm("python") if node is not None else None
    if parm is not None:
        parm.set(_empty_crowd_source_python_sop())
    out = parent.node("OUT_EMPTY_CROWD_SOURCE")
    if out is None:
        out = parent.createNode("null", "OUT_EMPTY_CROWD_SOURCE")
    if node is not None:
        out.setInput(0, node)
    return out


def _empty_crowd_source_python_sop() -> str:
    return "\n".join(
        [
            "import hou",
            "geo = hou.pwd().geometry()",
            "geo.clear()",
            "geo.addAttrib(hou.attribType.Global, 'smart_crowd_empty_source', 1)",
        ]
    )


def _safe_set_input(node: Any, index: int, source: Any) -> bool:
    if node is None or source is None:
        return False
    try:
        node.setInput(index, source)
        return True
    except Exception:
        return False


def _safe_disconnect_input(node: Any, index: int) -> bool:
    if node is None:
        return False
    before = _node_input(node, index)
    try:
        node.setInput(index, None)
    except Exception:
        return False
    after = _node_input(node, index)
    return before is not None or after is None


def _node_input(node: Any, index: int):
    if node is None:
        return None
    try:
        return node.input(index)
    except Exception:
        try:
            inputs = node.inputs()
        except Exception:
            return None
        return inputs[index] if 0 <= index < len(inputs) else None


def _node_parent(node: Any):
    if node is None:
        return None
    try:
        return node.parent()
    except Exception:
        return None


def _set_first_existing_parm(node: Any, parm_names: tuple[str, ...], value: Any) -> bool:
    for parm_name in parm_names:
        parm = node.parm(parm_name)
        if parm is not None:
            parm.set(value)
            return True
    return False


def _set_agent_character_file_if_supported(node: Any, fbx_path: str) -> bool:
    """Set an Agent SOP FBX file parameter only when it is clearly file-like.

    Some Houdini builds expose an Agent SOP ``source`` parameter that expects a
    Houdini object path. Assigning a disk FBX path there creates the
    "Missing or bad object" error, so source/object path parameters are
    deliberately excluded here.
    """

    _clear_bad_agent_object_source_path(node)
    _set_agent_input_mode_to_fbx(node)
    explicit = (
        "fbxfile",
        "fbx_file",
        "fbxfilepath",
        "fbx_path",
        "filepath",
        "filename",
    )
    if _set_first_existing_parm(node, explicit, fbx_path):
        return True

    try:
        parms = node.parms()
    except Exception:
        return False

    for parm in parms:
        try:
            template = parm.parmTemplate()
            label = template.label().lower()
            name = parm.name().lower()
        except Exception:
            continue
        text = f"{name} {label}"
        if not ("fbx" in text or "file" in text):
            continue
        if any(token in text for token in ("source", "object", "objpath", "soppath", "clip", "layer", "shape")):
            continue
        try:
            parm.set(fbx_path)
            return True
        except Exception:
            continue
    return False


def _auto_select_agent_visual_layer_if_supported(node: Any) -> dict[str, str]:
    result = {
        "status": "missing_agent_node",
        "selected_parameters": "",
        "candidate_items": "",
    }
    if node is None:
        return result

    try:
        parms = list(node.parms())
    except Exception:
        parms = []

    selected: list[str] = []
    candidates_seen: list[str] = []
    for parm in parms:
        if not _is_agent_visual_layer_parm(parm):
            continue
        try:
            template = parm.parmTemplate()
            items = list(template.menuItems() or [])
            labels = list(template.menuLabels() or [])
        except Exception:
            continue
        scored: list[tuple[int, int, str]] = []
        for index, item in enumerate(items):
            label = labels[index] if index < len(labels) else ""
            score = _agent_visual_menu_item_score(f"{item} {label}")
            if score <= 0:
                continue
            candidates_seen.append(f"{parm.name()}={item}:{label}" if label else f"{parm.name()}={item}")
            scored.append((score, index, item))
        if not scored:
            continue
        scored.sort(reverse=True)
        _, index, item = scored[0]
        if _set_menu_parm_item(parm, index, item):
            selected.append(f"{parm.name()}={item}")

    if selected:
        result["status"] = "selected_visual_layer"
    elif candidates_seen:
        result["status"] = "visual_layer_candidates_found_but_not_set"
    else:
        result["status"] = "no_visual_layer_menu_candidate"
    result["selected_parameters"] = ", ".join(selected) or "none"
    result["candidate_items"] = ", ".join(candidates_seen[:50]) or "none"

    for key, value in (
        ("smart_crowd_visual_auto_select_status", result["status"]),
        ("smart_crowd_visual_auto_selected_parameters", result["selected_parameters"]),
        ("smart_crowd_visual_auto_candidate_items", result["candidate_items"]),
    ):
        try:
            node.setUserData(key, value)
        except Exception:
            pass
    return result


def _enable_agent_mesh_import_options_if_supported(node: Any) -> dict[str, str]:
    result = {
        "status": "missing_agent_node",
        "enabled_parameters": "",
        "candidate_parameters": "",
    }
    if node is None:
        return result

    try:
        parms = list(node.parms())
    except Exception:
        parms = []

    enabled: list[str] = []
    candidates: list[str] = []
    for parm in parms:
        if not _is_agent_mesh_import_toggle(parm):
            continue
        candidates.append(parm.name())
        try:
            parm.set(1)
            enabled.append(parm.name())
        except Exception:
            continue

    if enabled:
        result["status"] = "enabled_mesh_import_options"
    elif candidates:
        result["status"] = "mesh_import_options_found_but_not_set"
    else:
        result["status"] = "no_mesh_import_options_found"
    result["enabled_parameters"] = ", ".join(enabled) or "none"
    result["candidate_parameters"] = ", ".join(candidates) or "none"

    for key, value in (
        ("smart_crowd_mesh_import_option_status", result["status"]),
        ("smart_crowd_mesh_import_enabled_parameters", result["enabled_parameters"]),
        ("smart_crowd_mesh_import_candidate_parameters", result["candidate_parameters"]),
    ):
        try:
            node.setUserData(key, value)
        except Exception:
            pass
    return result


def _reload_agent_definition_if_supported(node: Any) -> dict[str, str]:
    result = {
        "status": "missing_agent_node",
        "pressed_parameters": "",
        "candidate_parameters": "",
    }
    if node is None:
        return result

    try:
        parms = list(node.parms())
    except Exception:
        parms = []

    pressed: list[str] = []
    candidates: list[str] = []
    for parm in parms:
        if not _is_agent_reload_button(parm):
            continue
        candidates.append(parm.name())
        method = getattr(parm, "pressButton", None)
        if method is None:
            continue
        try:
            method()
            pressed.append(parm.name())
        except Exception:
            continue

    if pressed:
        result["status"] = "pressed_reload"
    elif candidates:
        result["status"] = "reload_buttons_found_but_not_pressed"
    else:
        result["status"] = "no_reload_button_found"
    result["pressed_parameters"] = ", ".join(pressed) or "none"
    result["candidate_parameters"] = ", ".join(candidates) or "none"

    for key, value in (
        ("smart_crowd_agent_reload_status", result["status"]),
        ("smart_crowd_agent_reload_pressed_parameters", result["pressed_parameters"]),
        ("smart_crowd_agent_reload_candidate_parameters", result["candidate_parameters"]),
    ):
        try:
            node.setUserData(key, value)
        except Exception:
            pass
    return result


def _refresh_kinefx_fbx_imports_if_supported(parent: Any, files: CrowdPrototypeFiles) -> dict[str, str]:
    result = {
        "status": "missing_kinefx_import_parent",
        "file_parameters": "",
        "reload_pressed": "",
        "reload_candidates": "",
        "missing_nodes": "",
    }
    if parent is None:
        return result

    specs = (
        ("character", "character_fbx", files.character_fbx),
        ("walk", "walk_fbx", files.walk_fbx),
        ("sit_down", "sit_down_fbx", files.sit_down_fbx),
        ("sit_idle", "sit_idle_fbx", files.sit_idle_fbx),
    )
    file_parameters: list[str] = []
    reload_pressed: list[str] = []
    reload_candidates: list[str] = []
    missing_nodes: list[str] = []

    for label, node_name, path in specs:
        node = parent.node(node_name) or parent.node(f"{node_name}_file")
        if node is None:
            missing_nodes.append(node_name)
            continue
        if _set_first_existing_parm(node, ("fbxfile", "file", "filepath", "filename"), _as_posix(path)):
            file_parameters.append(f"{node_name}={_as_posix(path)}")
        reload_result = _press_reload_buttons_if_supported(node)
        if reload_result["candidate_parameters"] != "none":
            reload_candidates.append(f"{node_name}:{reload_result['candidate_parameters']}")
        if reload_result["pressed_parameters"] != "none":
            reload_pressed.append(f"{node_name}:{reload_result['pressed_parameters']}")
        try:
            node.cook(force=True)
        except Exception:
            pass

    if reload_pressed:
        status = "reloaded_fbx_imports"
    elif file_parameters:
        status = "updated_fbx_paths_no_reload_button"
    else:
        status = "no_fbx_imports_updated"

    result["status"] = status
    result["file_parameters"] = "; ".join(file_parameters) or "none"
    result["reload_pressed"] = "; ".join(reload_pressed) or "none"
    result["reload_candidates"] = "; ".join(reload_candidates) or "none"
    result["missing_nodes"] = ", ".join(missing_nodes) or "none"

    for key, value in (
        ("smart_crowd_kinefx_fbx_refresh_status", result["status"]),
        ("smart_crowd_kinefx_fbx_file_parameters", result["file_parameters"]),
        ("smart_crowd_kinefx_fbx_reload_pressed", result["reload_pressed"]),
        ("smart_crowd_kinefx_fbx_reload_candidates", result["reload_candidates"]),
        ("smart_crowd_kinefx_fbx_missing_nodes", result["missing_nodes"]),
    ):
        try:
            parent.setUserData(key, value)
        except Exception:
            pass
    return result


def _press_reload_buttons_if_supported(node: Any) -> dict[str, str]:
    result = {
        "status": "missing_node",
        "pressed_parameters": "",
        "candidate_parameters": "",
    }
    if node is None:
        return result
    try:
        parms = list(node.parms())
    except Exception:
        parms = []
    pressed: list[str] = []
    candidates: list[str] = []
    for parm in parms:
        if not _is_agent_reload_button(parm):
            continue
        candidates.append(parm.name())
        method = getattr(parm, "pressButton", None)
        if method is None:
            continue
        try:
            method()
            pressed.append(parm.name())
        except Exception:
            continue
    if pressed:
        result["status"] = "pressed_reload"
    elif candidates:
        result["status"] = "reload_buttons_found_but_not_pressed"
    else:
        result["status"] = "no_reload_button_found"
    result["pressed_parameters"] = ", ".join(pressed) or "none"
    result["candidate_parameters"] = ", ".join(candidates) or "none"
    return result


def _is_agent_reload_button(parm: Any) -> bool:
    try:
        template = parm.parmTemplate()
        name = parm.name().lower()
        label = template.label().lower()
    except Exception:
        return False
    text = f"{name} {label}"
    if "reload" not in text and "refresh" not in text:
        return False
    return getattr(parm, "pressButton", None) is not None


def _is_agent_mesh_import_toggle(parm: Any) -> bool:
    try:
        template = parm.parmTemplate()
        name = parm.name().lower()
        label = template.label().lower()
    except Exception:
        return False
    text = f"{name} {label}"
    if any(token in text for token in ("collision", "proxy", "shader", "namespace", "unit")):
        return False
    positive = any(token in text for token in ("deforming", "shape", "mesh", "geometry", "skin", "capture"))
    action = any(token in text for token in ("import", "include", "keep", "load", "use", "create"))
    if not (positive and action):
        return False
    try:
        value = parm.eval()
    except Exception:
        return False
    return isinstance(value, (bool, int, float))


def _is_agent_visual_layer_parm(parm: Any) -> bool:
    try:
        template = parm.parmTemplate()
        text = f"{parm.name()} {template.label()}".lower()
        items = list(template.menuItems() or [])
    except Exception:
        return False
    if not items:
        return False
    if not any(token in text for token in ("layer", "shape", "display", "render", "visual", "geometry", "mesh")):
        return False
    if any(token in text for token in ("collisionlayer", "collision layer", "collisionshape", "collision shape")):
        return False
    return True


def _agent_visual_menu_item_score(text: str) -> int:
    lower = str(text or "").lower()
    if not lower or any(token in lower for token in ("collision", "proxy", "subnet_proxy", "geo_proxy", "guide")):
        return 0
    if lower.strip() in ("default", "none", "interpolate", "constant"):
        return 0
    score = 0
    for token, value in (
        ("render", 8),
        ("visual", 7),
        ("mesh", 7),
        ("geometry", 6),
        ("geo", 5),
        ("skin", 5),
        ("body", 5),
        ("deform", 3),
        ("shape", 2),
    ):
        if token in lower:
            score += value
    return score


def _set_menu_parm_item(parm: Any, index: int, item: str) -> bool:
    try:
        parm.set(item)
        return True
    except Exception:
        pass
    try:
        parm.set(index)
        return True
    except Exception:
        return False


def _set_agent_input_mode_to_fbx(node: Any) -> bool:
    for parm_name in ("input", "inputtype", "input_type", "sourcetype", "source_type"):
        parm = node.parm(parm_name)
        if parm is not None and _set_menu_parm_to_matching_item(parm, ("fbx",)):
            return True

    try:
        parms = node.parms()
    except Exception:
        return False

    for parm in parms:
        try:
            template = parm.parmTemplate()
            label = template.label().lower()
            name = parm.name().lower()
        except Exception:
            continue
        if "input" not in f"{name} {label}":
            continue
        if _set_menu_parm_to_matching_item(parm, ("fbx",)):
            return True
    return False


def _set_menu_parm_to_matching_item(parm: Any, needles: tuple[str, ...]) -> bool:
    try:
        template = parm.parmTemplate()
        items = list(template.menuItems() or [])
        labels = list(template.menuLabels() or [])
    except Exception:
        return False

    candidates: list[tuple[int, str]] = []
    for index, item in enumerate(items):
        text = item.lower()
        label = labels[index].lower() if index < len(labels) else ""
        if all(needle in f"{text} {label}" for needle in needles):
            candidates.append((index, item))
    for index, item in candidates:
        try:
            parm.set(item)
            return True
        except Exception:
            pass
        try:
            parm.set(index)
            return True
        except Exception:
            pass
    return False


def _clear_bad_agent_object_source_path(node: Any) -> None:
    for parm_name in ("source", "object", "objpath", "objpath1"):
        parm = node.parm(parm_name)
        if parm is None:
            continue
        try:
            value = parm.eval()
        except Exception:
            continue
        if not _looks_like_fbx_path(value):
            continue
        try:
            parm.set("")
        except Exception:
            pass


def _looks_like_fbx_path(value: Any) -> bool:
    text = str(value or "").strip().replace("\\", "/").lower()
    return text.endswith(".fbx") or ".fbx/" in text


def _disconnect_agent_clip_sources_from_invalid_agent(parent: Any, agent: Any) -> None:
    if agent is None:
        return
    for node_name in ("agentclip_walk", "agentclip_sit_down", "agentclip_sit_idle"):
        node = parent.node(node_name)
        if node is None:
            continue
        try:
            if node.input(0) == agent:
                _disconnect_input(node, 0)
        except Exception:
            pass


def _set_sop_path_like_parms(node: Any, sop_path: str) -> list[str]:
    explicit_names = (
        "soppath",
        "soppath1",
        "sop_path",
        "sop_path1",
        "source",
        "sourcepath",
        "source_path",
        "geopath",
        "geometrypath",
        "geometry_path",
        "objpath",
        "objpath1",
    )
    matched: list[str] = []
    for parm_name in explicit_names:
        parm = node.parm(parm_name)
        if parm is None:
            continue
        try:
            parm.set(sop_path)
            matched.append(parm_name)
        except Exception:
            pass
    if matched:
        return matched

    try:
        parms = node.parms()
    except Exception:
        return matched
    for parm in parms:
        try:
            template = parm.parmTemplate()
            label = template.label().lower()
            name = parm.name().lower()
        except Exception:
            continue
        text = f"{name} {label}"
        is_path = "path" in text
        is_sop_source = "sop" in text or "source" in text or "geometry" in text
        if not (is_path and is_sop_source):
            continue
        try:
            parm.set(sop_path)
            matched.append(parm.name())
        except Exception:
            continue
    return matched


def _set_dop_solver_safe_state(node: Any, safe: bool, *, disconnect_input: bool = False) -> bool:
    if node is None:
        return False
    changed = False
    if safe:
        changed = _safe_bypass(node, True) or changed
        changed = bool(_set_dop_activation(node, False)) or changed
        changed = _set_solver_source_path_safe(node, True) or changed
        changed = _set_solver_gate_safe(node, True) or changed
    else:
        changed = bool(_set_dop_activation(node, True)) or changed
        changed = _set_solver_source_path_safe(node, False) or changed
        changed = _set_solver_gate_safe(node, False) or changed
        active_input = _dop_merge_input_node_for_solver(node)
        if active_input is not None:
            changed = _safe_set_input(node, 0, active_input) or changed
        changed = _safe_bypass(node, False) or changed
    return changed


def _set_dop_activation(node: Any, enabled: bool) -> list[str]:
    explicit_names = (
        "activation",
        "active",
        "enable",
        "enabled",
        "enableobject",
        "enablesolver",
    )
    matched: list[str] = []
    value = 1 if enabled else 0
    for parm_name in explicit_names:
        parm = node.parm(parm_name)
        if parm is None:
            continue
        try:
            parm.set(value)
            matched.append(parm_name)
        except Exception:
            pass
    if matched:
        return matched

    try:
        parms = node.parms()
    except Exception:
        return matched
    for parm in parms:
        try:
            template = parm.parmTemplate()
            label = template.label().lower()
            name = parm.name().lower()
        except Exception:
            continue
        text = f"{name} {label}"
        is_activation = "activation" in text or "enable solver" in text or "enable this solver" in text
        if not is_activation:
            continue
        try:
            parm.set(value)
            matched.append(parm.name())
        except Exception:
            continue
    return matched


def _dop_activation_values(node: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if node is None:
        return values
    try:
        parms = node.parms()
    except Exception:
        return values
    for parm in parms:
        try:
            template = parm.parmTemplate()
            label = template.label().lower()
            name = parm.name().lower()
            value = parm.eval()
        except Exception:
            continue
        text = f"{name} {label}"
        is_activation = name in {"activation", "active", "enable", "enabled", "enableobject", "enablesolver"}
        is_activation = is_activation or "activation" in text or "enable solver" in text or "enable this solver" in text
        if is_activation:
            values[parm.name()] = value
    return values


def _first_dop_activation_value(node: Any) -> int:
    values = _dop_activation_values(node)
    if not values:
        return -1
    try:
        return int(bool(next(iter(values.values()))))
    except Exception:
        return -1


def _node_is_solver_safe(node: Any) -> bool:
    if node is None:
        return False
    if _solver_source_path_is_safe(node):
        return True
    if _node_input(node, 0) is None:
        return True
    if _solver_input_is_safe(node):
        return True
    if _solver_gate_is_safe(node):
        return True
    if _node_is_bypassed(node):
        return True
    values = _dop_activation_values(node)
    if not values:
        return False
    for value in values.values():
        try:
            if int(bool(value)) == 0:
                return True
        except Exception:
            continue
    return False


def _set_solver_source_path_safe(node: Any, safe: bool) -> bool:
    source_geometry = _dop_source_geometry_node_for_solver(node)
    if source_geometry is None:
        return False
    active_path = source_geometry.userData("smart_crowd_source_sop_path") or ""
    empty_path = source_geometry.userData("smart_crowd_empty_source_sop_path") or ""
    target = empty_path if safe else active_path
    if not target:
        return False
    return bool(_set_sop_path_like_parms(source_geometry, target))


def _solver_source_path_is_safe(node: Any) -> bool:
    source_geometry = _dop_source_geometry_node_for_solver(node)
    if source_geometry is None:
        return False
    return _source_geometry_uses_empty_source(source_geometry)


def _source_geometry_uses_empty_source(source_geometry: Any) -> bool:
    empty_path = source_geometry.userData("smart_crowd_empty_source_sop_path") or ""
    if not empty_path:
        return False
    values = _sop_path_like_parm_values(source_geometry)
    if not values:
        return False
    return all(value == empty_path for value in values.values())


def _set_solver_gate_safe(node: Any, safe: bool) -> bool:
    merge = _dop_merge_input_node_for_solver(node)
    if merge is None:
        return False
    changed = False
    if safe:
        safe_input = _dop_safe_input_node_for_solver(node)
        if safe_input is not None:
            changed = _safe_set_input(merge, 0, safe_input) or changed
            changed = _safe_set_input(merge, 1, safe_input) or changed
        for input_index in range(2, 8):
            changed = _safe_disconnect_input(merge, input_index) or changed
    else:
        parent = _node_parent(node)
        crowd_object = parent.node("DOP_CROWD_OBJECT") if parent is not None else None
        source_geometry = parent.node("DOP_SOURCE_GEOMETRY") if parent is not None else None
        if crowd_object is not None:
            changed = _safe_set_input(merge, 0, crowd_object) or changed
        if source_geometry is not None:
            changed = _safe_set_input(merge, 1, source_geometry) or changed
    return changed


def _solver_gate_is_safe(node: Any) -> bool:
    merge = _dop_merge_input_node_for_solver(node)
    if merge is None:
        return False
    safe_input = _dop_safe_input_node_for_solver(node)
    try:
        inputs = merge.inputs()
    except Exception:
        inputs = ()
    connected = [input_node for input_node in inputs if input_node is not None]
    if not connected:
        return True
    if safe_input is None:
        return False
    try:
        safe_path = safe_input.path()
        return all(input_node.path() == safe_path for input_node in connected)
    except Exception:
        return all(input_node is safe_input for input_node in connected)


def _solver_gate_summary(node: Any) -> str:
    merge = _dop_merge_input_node_for_solver(node)
    return _input_summary(merge) if merge is not None else "missing_gate"


def _dop_safe_input_node_for_solver(node: Any):
    parent = _node_parent(node)
    return parent.node("DOP_SAFE_EMPTY") if parent is not None else None


def _dop_merge_input_node_for_solver(node: Any):
    parent = _node_parent(node)
    if parent is None:
        return None
    return parent.node("DOP_MERGE_INPUTS")


def _dop_source_geometry_node_for_solver(node: Any):
    parent = _node_parent(node)
    if parent is None:
        return None
    return parent.node("DOP_SOURCE_GEOMETRY")


def _solver_input_is_safe(node: Any) -> bool:
    current = _node_input(node, 0)
    if current is None:
        return True
    merge = _dop_merge_input_node_for_solver(node)
    try:
        if merge is not None and current.path() == merge.path():
            return _solver_gate_is_safe(node)
    except Exception:
        if current is merge:
            return _solver_gate_is_safe(node)
    safe_input = _dop_safe_input_node_for_solver(node)
    if safe_input is None:
        return False
    try:
        return current.path() == safe_input.path()
    except Exception:
        return current is safe_input


def _update_solver_safety_result(result: dict[str, Any], solver: Any) -> None:
    result["solver_bypassed_after"] = int(_node_is_bypassed(solver))
    result["solver_activation_after"] = _first_dop_activation_value(solver)
    result["solver_activation_parameters"] = ", ".join(
        f"{name}={value}" for name, value in _dop_activation_values(solver).items()
    )
    result["solver_input_summary_after"] = _input_summary(solver)
    result["solver_input_disconnected_after"] = int(_node_input(solver, 0) is None)
    result["solver_input_safe_after"] = int(_solver_input_is_safe(solver))
    result["solver_gate_summary_after"] = _solver_gate_summary(solver)
    result["solver_gate_safe_after"] = int(_solver_gate_is_safe(solver))
    source_geometry = _dop_source_geometry_node_for_solver(solver)
    if source_geometry is not None:
        result["solver_empty_source_sop_path"] = source_geometry.userData("smart_crowd_empty_source_sop_path") or ""
        values = _sop_path_like_parm_values(source_geometry)
        result["solver_source_sop_path_after"] = ", ".join(f"{name}={value}" for name, value in values.items())
        result["solver_source_is_safe_after"] = int(_source_geometry_uses_empty_source(source_geometry))
    safe_input = _dop_safe_input_node_for_solver(solver)
    result["solver_safe_input_path"] = safe_input.path() if safe_input is not None else ""
    result["solver_safe_after"] = int(_node_is_solver_safe(solver))


def _sop_path_like_parm_values(node: Any) -> dict[str, str]:
    values: dict[str, str] = {}
    if node is None:
        return values
    try:
        parms = node.parms()
    except Exception:
        return values
    for parm in parms:
        try:
            value = parm.eval()
            template = parm.parmTemplate()
            label = template.label().lower()
            name = parm.name().lower()
        except Exception:
            continue
        if not isinstance(value, str) or not value:
            continue
        text = f"{name} {label}"
        is_path = "path" in text or value.startswith("/")
        is_sop_source = "sop" in text or "source" in text or "geometry" in text or value.startswith("/obj/")
        if is_path and is_sop_source:
            values[parm.name()] = value
    return values


def _set_crowd_source_agent_count(node: Any, count: int) -> list[str]:
    explicit_names = (
        "npts",
        "numagents",
        "number",
        "nagents",
        "num_agents",
        "agent_count",
        "agentcount",
        "numberofagents",
        "numpoints",
        "num_points",
        "point_count",
        "pointcount",
    )
    matched: list[str] = []
    for parm_name in explicit_names:
        parm = node.parm(parm_name)
        if parm is None:
            continue
        try:
            parm.set(int(count))
            matched.append(parm_name)
        except Exception:
            pass
    if matched:
        return matched

    try:
        parms = node.parms()
    except Exception:
        return matched
    for parm in parms:
        try:
            template = parm.parmTemplate()
            label = template.label().lower()
            name = parm.name().lower()
        except Exception:
            continue
        text = f"{name} {label}"
        is_agent_count = ("agent" in text or "crowd" in text or "point" in text) and any(
            term in text for term in ("count", "number", "num", "amount")
        )
        if not is_agent_count:
            continue
        try:
            parm.set(int(count))
            matched.append(parm.name())
        except Exception:
            continue
    return matched


def _disconnect_input(node: Any, index: int) -> None:
    try:
        node.setInput(index, None)
    except Exception:
        pass


def _safe_bypass(node: Any, enabled: bool) -> bool:
    if node is None:
        return False
    flag = _houdini_bypass_flag()
    if flag is not None:
        method = getattr(node, "setGenericFlag", None)
        if method is not None:
            try:
                method(flag, bool(enabled))
                return True
            except Exception:
                pass
    for method_name in ("setBypass", "bypass"):
        method = getattr(node, method_name, None)
        if method is None:
            continue
        try:
            method(bool(enabled))
            return True
        except Exception:
            continue
    return False


def _node_is_bypassed(node: Any) -> bool:
    if node is None:
        return False
    flag = _houdini_bypass_flag()
    if flag is not None:
        method = getattr(node, "isGenericFlagSet", None)
        if method is not None:
            try:
                return bool(method(flag))
            except Exception:
                pass
    for method_name in ("isBypassed", "bypass"):
        method = getattr(node, method_name, None)
        if method is None:
            continue
        try:
            return bool(method())
        except TypeError:
            continue
        except Exception:
            continue
    return False


def _node_bypass_debug_summary(node: Any) -> str:
    if node is None:
        return "node=none"
    parts = []
    flag = _houdini_bypass_flag()
    if flag is not None:
        method = getattr(node, "isGenericFlagSet", None)
        if method is not None:
            try:
                parts.append(f"generic={int(bool(method(flag)))}")
            except Exception as exc:
                parts.append(f"generic_error={type(exc).__name__}")
    for method_name in ("isBypassed", "bypass"):
        method = getattr(node, method_name, None)
        if method is None:
            continue
        try:
            parts.append(f"{method_name}={int(bool(method()))}")
        except TypeError:
            parts.append(f"{method_name}=requires_arg")
        except Exception as exc:
            parts.append(f"{method_name}_error={type(exc).__name__}")
    return ", ".join(parts) or "no_bypass_query"


def _houdini_bypass_flag() -> Any:
    try:
        import hou

        return hou.nodeFlag.Bypass
    except Exception:
        return None


def _add_or_update_note(parent: Any, name: str, text: str) -> None:
    note = _find_sticky_note(parent, name)
    if note is None:
        _add_note(parent, name, text)
    else:
        note.setText(text)


def _set_switch_frame_expression(hou: Any, switch_node: Any, plan: dict[str, Any]) -> None:
    parm = switch_node.parm("input")
    if parm is None:
        parm = switch_node.parm("index")
    if parm is None:
        return
    loc = plan.get("locomotion") or {}
    sit_down_start = int(loc.get("align_end_frame", 88)) + 1
    sit_idle_start = int(loc.get("sit_idle_start_frame", 129))
    try:
        parm.setExpression(f"if($F < {sit_down_start}, 0, if($F < {sit_idle_start}, 1, 2))", hou.exprLanguage.Hscript)
    except Exception:
        parm.set(0)


def _create_clip_time_shift(hou: Any, parent: Any, input_node: Any, label: str, plan: dict[str, Any]):
    time_shift = _create_first_available(hou, parent, ("timeshift",), f"TIME_{label.upper()}_CLIP")
    if time_shift is None:
        return None
    time_shift.setInput(0, input_node)
    _set_clip_time_expression(hou, time_shift, label, plan)
    return time_shift


def _set_clip_time_expression(hou: Any, time_shift: Any, label: str, plan: dict[str, Any]) -> None:
    parm = time_shift.parm("frame")
    if parm is None:
        parm = time_shift.parm("f")
    if parm is None:
        return
    expression = _clip_time_expression(label, plan)
    if not expression:
        return
    try:
        parm.setExpression(expression, hou.exprLanguage.Hscript)
    except Exception:
        pass


def _clip_time_expression(label: str, plan: dict[str, Any]) -> str:
    loc = plan.get("locomotion") or {}
    if label == "walk":
        walk_start = int(loc.get("walk_start_frame", 1))
        walk_end = int(loc.get("align_end_frame", loc.get("walk_end_frame", walk_start)))
        clip_start = int(loc.get("walk_clip_start_frame", 1))
        clip_frames = max(1, int(loc.get("walk_clip_frames", DEFAULT_WALK_CLIP_FRAMES)))
        clip_last = clip_start + clip_frames - 1
        return f"if($F <= {walk_end}, {clip_start} + (($F - {walk_start}) % {clip_frames}), {clip_last})"
    if label == "sit_down":
        sit_start = int(loc.get("align_end_frame", 1)) + 1
        sit_end = int(loc.get("sit_down_end_frame", sit_start))
        clip_start = int(loc.get("sit_down_clip_start_frame", 1))
        clip_frames = max(1, int(loc.get("sit_down_clip_frames", DEFAULT_SIT_DOWN_FRAMES)))
        clip_last = clip_start + clip_frames - 1
        return f"if($F < {sit_start}, {clip_start}, if($F > {sit_end}, {clip_last}, {clip_start} + ($F - {sit_start})))"
    if label == "sit_idle":
        idle_start = int(loc.get("sit_idle_start_frame", 1))
        clip_start = int(loc.get("sit_idle_clip_start_frame", 1))
        clip_frames = max(1, int(loc.get("sit_idle_clip_frames", DEFAULT_SIT_IDLE_CLIP_FRAMES)))
        return f"{clip_start} + (($F - {idle_start}) % {clip_frames})"
    return ""


def _add_note(parent: Any, name: str, text: str) -> None:
    note = parent.createStickyNote(name)
    note.setText(text)


def _find_sticky_note(parent: Any, name: str):
    sticky_notes = getattr(parent, "stickyNotes", None)
    if sticky_notes is None:
        return None
    for note in sticky_notes():
        try:
            if note.name() == name:
                return note
        except Exception:
            continue
    return None


def _network_note(files: CrowdPrototypeFiles, plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Smart Crowd Seat Prototype",
            "",
            "Files:",
            f"character: {_as_posix(files.character_fbx)}",
            f"walk: {_as_posix(files.walk_fbx)}",
            f"sit_down: {_as_posix(files.sit_down_fbx)}",
            f"sit_idle: {_as_posix(files.sit_idle_fbx)}",
            f"interaction: {_as_posix(files.interaction_yaml)}",
            f"animation: {_as_posix(files.animation_yaml)}",
            "",
            "Behavior:",
            " -> ".join(plan["goal"]["steps"]),
            f"target: {plan['goal']['interaction_point_id']}",
        ]
    )


def _vector_mapping(value: tuple[float, float, float]) -> dict[str, float]:
    return {"x": value[0], "y": value[1], "z": value[2]}


def _mapping_vector(value: Any, fallback: tuple[float, float, float]) -> tuple[float, float, float]:
    if isinstance(value, dict):
        return (float(value.get("x", fallback[0])), float(value.get("y", fallback[1])), float(value.get("z", fallback[2])))
    if isinstance(value, (list, tuple)):
        padded = list(value[:3]) + [fallback[0], fallback[1], fallback[2]]
        return (float(padded[0]), float(padded[1]), float(padded[2]))
    return fallback


def _normalized_vector(
    value: tuple[float, float, float],
    *,
    fallback: tuple[float, float, float],
) -> tuple[float, float, float]:
    length = _distance(value, (0.0, 0.0, 0.0))
    if length <= 1e-8:
        return fallback
    return (value[0] / length, value[1] / length, value[2] / length)


def _distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt(sum((float(a[index]) - float(b[index])) ** 2 for index in range(3)))


def _positive_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if number > 0 else float(default)


def _animation_speed_for_houdini(animation: dict[str, Any], root_motion: dict[str, Any]) -> float:
    speed_houdini = _positive_float(animation.get("speed_houdini"), 0.0)
    if speed_houdini > 0:
        return speed_houdini
    root_speed_houdini = _positive_float(root_motion.get("speed_houdini"), 0.0)
    if root_speed_houdini > 0:
        return root_speed_houdini
    speed = _positive_float(animation.get("speed"), 0.0)
    unit_scale = _positive_float(root_motion.get("unit_scale_to_houdini"), 1.0)
    if speed > 0:
        return speed * unit_scale
    return DEFAULT_WALK_SPEED


def _animation_start_frame(animation: dict[str, Any], root_motion: dict[str, Any]) -> float:
    for source in (animation, root_motion):
        for key in ("startFrame", "start_frame"):
            frame = _positive_float(source.get(key), 0.0)
            if frame > 0:
                return frame
    return 1.0


def _animation_duration_frames(animation: dict[str, Any], root_motion: dict[str, Any], default: float) -> float:
    for source in (animation, root_motion):
        for key in ("durationFrames", "duration_frames", "duration"):
            frames = _positive_float(source.get(key), 0.0)
            if frames > 0:
                return frames
    return default


def _as_posix(path: Path) -> str:
    return str(path).replace("\\", "/")
