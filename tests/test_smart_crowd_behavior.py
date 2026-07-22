from __future__ import annotations

from pathlib import Path

from smartlib.crowd.behavior import Agent, BehaviorGoal, BehaviorRuntimeSettings, BehaviorSystem
from smartlib.crowd.interaction import InteractionPoint, InteractionSystem
from smartlib.crowd.schema import load_behavior_schema
from smartlib.crowd.yamlio import dumps_yaml, loads_yaml
from smartlib.dcc.houdini.crowd_loader import interaction_points_from_data
from smartlib.dcc.houdini.crowd_kinefx import (
    activate_agent_clip_test,
    cook_crowd_dop_solver_smoke_test,
    cook_runtime_crowd_dop_solver_smoke_test,
    cook_agent_clip_test,
    cook_clip_locomotion_test,
    cook_crowd_source_test,
    deactivate_agent_clip_tests,
    ensure_crowd_dop_source_scaffold,
    ensure_runtime_crowd_dop_source_scaffold,
    ensure_runtime_dop_result_preview,
    ensure_crowd_solver_dop_bridge,
    ensure_crowd_solver_test_node,
    apply_runtime_validation_short_distance,
    probe_crowd_dop_bridge_connection,
    probe_crowd_dop_node_types,
    probe_crowd_dop_scaffold_state,
    probe_crowd_solver_connection,
    probe_crowd_node_types,
    refresh_agent_clip_unbypass_guard,
    sample_runtime_dop_result_timeline,
    ensure_runtime_timeline_sample_preview,
    validate_runtime_seat_behavior_timeline,
    run_agent_clip_activation_sequence,
    run_agent_clip_cook_sequence,
    run_clip_locomotion_cook_sequence,
    run_crowd_dop_bridge_connection_sequence,
    run_crowd_dop_source_scaffold_sequence,
    run_crowd_dop_solver_smoke_test_sequence,
    run_crowd_source_cook_sequence,
    run_crowd_solver_connection_sequence,
    CrowdPrototypeFiles,
    build_single_agent_plan_from_data,
    _agent_clip_bridge_python_sop,
    _agent_crowd_behavior_python_sop,
    _agent_crowd_visual_diagnostic_python_sop,
    _agent_definition_parameter_diagnostic_python_sop,
    _agent_clip_clipset_test_result_python_sop,
    _agent_clip_experiment_python_sop,
    _agent_clip_named_test_input_python_sop,
    _agent_clip_named_test_result_python_sop,
    _agent_clip_node_test_result_python_sop,
    _agent_clip_unbypass_guard_python_sop,
    _agent_clip_activation_sequence_log_python_sop,
    _agent_clip_walk_test_input_python_sop,
    _agent_clip_walk_test_result_python_sop,
    _agent_clip_test_specs,
    _agent_clip_active_state,
    _apply_behavior_transform_python_sop,
    _animation_speed_for_houdini,
    _behavior_agent_driver_python_sop,
    _clip_time_expression,
    _crowd_clip_state_driver_python_sop,
    _plan_python_sop,
    _runtime_agent_source_python_sop,
    _runtime_behavior_python_sop,
    _runtime_dop_result_preview_python_sop,
    _runtime_timeline_sample_preview_python_sop,
    _runtime_kinefx_preview_python_sop,
    _kinefx_clip_diagnostic_python_sop,
    _agent_crowd_visual_preview_python_sop,
    _single_agent_preview_python_sop,
    _ensure_kinefx_clip_diagnostic,
    _ensure_agent_character_preview,
    _ensure_agent_character_unpacked_preview,
    _ensure_agent_crowd_behavior_output,
    _ensure_agent_crowd_behavior_unpacked_preview,
    _ensure_agent_crowd_visual_diagnostic,
    _ensure_agent_crowd_visual_preview,
    _ensure_agent_definition_parameter_diagnostic,
    _ensure_agent_crowd_scaffold_nodes,
    _auto_select_agent_visual_layer_if_supported,
    _enable_agent_mesh_import_options_if_supported,
    _reload_agent_definition_if_supported,
    _refresh_kinefx_fbx_imports_if_supported,
    _press_reload_buttons_if_supported,
    _agent_character_diagnostic_python_sop,
)
from smartlib.dcc.maya.crowd_analyzer import _resolve_root


ROOT = Path(__file__).resolve().parents[1]


def test_behavior_schema_loads_options_from_yaml():
    schema = load_behavior_schema(ROOT / "config" / "behavior_schema.yaml")

    assert "seat" in schema.option_ids("interaction_types")
    assert "normal" in schema.option_ids("animation_styles")


def test_yaml_fallback_round_trips_interaction_data():
    data = {
        "interactions": [
            {
                "id": "Bench_A",
                "type": "bench",
                "points": [
                    {
                        "id": "seat_01",
                        "interaction_type": "seat",
                        "position": {"x": 1.0, "y": 0.0, "z": 2.0},
                        "rotation": {"x": 0.0, "y": 90.0, "z": 0.0},
                        "approach_position": {"x": 1.0, "y": 0.0, "z": 1.0},
                        "enabled": True,
                        "priority": 5,
                    }
                ],
            }
        ]
    }
    parsed = loads_yaml(dumps_yaml(data))

    assert parsed["interactions"][0]["points"][0]["interaction_type"] == "seat"
    assert parsed["interactions"][0]["points"][0]["priority"] == 5


def test_interaction_and_behavior_systems_are_separated():
    schema = load_behavior_schema(ROOT / "config" / "behavior_schema.yaml")
    interactions = InteractionSystem(schema)
    interactions.add_point(
        InteractionPoint(
            id="seat_01",
            interaction_type="seat",
            position=(3.0, 0.0, 4.0),
            approach_position=(3.0, 0.0, 2.0),
            priority=10,
        )
    )
    agent = Agent(id="agent_001")
    behavior = BehaviorSystem(interactions)

    trace = behavior.execute_single_agent_goal(agent, BehaviorGoal(name="sit", interaction_type="seat"))

    assert trace.steps == ["Find Seat", "Walk", "Align", "Sit Down", "Sit Idle"]
    assert trace.interaction_point_id == "seat_01"
    assert interactions.find_available("seat") is None
    assert interactions.get("seat_01").occupied is True
    assert interactions.get("seat_01").reserved_by == ""
    assert agent.position == (3.0, 0.0, 4.0)
    assert agent.state == "sit_idle"


def test_behavior_runtime_updates_by_distance_and_occupies_after_sit():
    schema = load_behavior_schema(ROOT / "config" / "behavior_schema.yaml")
    interactions = InteractionSystem(schema)
    interactions.add_point(
        InteractionPoint(
            id="seat_01",
            interaction_type="seat",
            position=(0.0, 0.0, 0.0),
            approach_position=(-1.0, 0.0, 0.0),
        )
    )
    agent = Agent(id="agent_001", position=(-2.0, 0.0, 0.0))
    behavior = BehaviorSystem(interactions)
    goal = BehaviorGoal(name="sit", interaction_type="seat")
    settings = BehaviorRuntimeSettings(dt=1.0, walk_speed=1.0, align_speed=1.0, sit_down_duration=0.5)

    first = behavior.update_agent_goal(agent, goal, settings=settings)
    second = behavior.update_agent_goal(agent, goal, settings=settings)
    third = behavior.update_agent_goal(agent, goal, settings=settings)

    assert first.current_step == "Align"
    assert second.current_step == "Sit Down"
    assert third.current_step == "Sit Idle"
    assert agent.state == "sit_idle"
    assert interactions.get("seat_01").occupied is True


def test_houdini_loader_creates_required_point_attributes():
    data = {
        "interactions": [
            {
                "id": "Bench_A",
                "type": "bench",
                "points": [
                    {
                        "id": "seat_01",
                        "interaction_type": "seat",
                        "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                        "approach_position": {"x": 0.0, "y": 0.0, "z": -1.0},
                        "enabled": True,
                        "priority": 0,
                    }
                ],
            }
        ]
    }

    points = interaction_points_from_data(data, schema_path=ROOT / "config" / "behavior_schema.yaml")

    assert points == [
        {
            "P": (0.0, 0.0, 0.0),
            "interaction_type": "seat",
            "enabled": True,
            "priority": 0,
            "seat_id": "seat_01",
            "interaction_id": "Bench_A",
            "approach_position": (0.0, 0.0, -1.0),
            "occupied": False,
            "reserved_by": "",
        }
    ]


def test_houdini_kinefx_plan_uses_behavior_trace():
    data = {
        "interactions": [
            {
                "id": "Bench_A",
                "type": "bench",
                "points": [
                    {
                        "id": "Bench_A_seat_01",
                        "interaction_type": "seat",
                        "position": {"x": -12.5, "y": 0.0, "z": -12.4},
                        "approach_position": {"x": -13.5, "y": 0.0, "z": -12.4},
                        "enabled": True,
                        "priority": 0,
                    }
                ],
            }
        ]
    }

    plan = build_single_agent_plan_from_data(data, schema_path=ROOT / "config" / "behavior_schema.yaml")

    assert plan["goal"]["steps"] == ["Find Seat", "Walk", "Align", "Sit Down", "Sit Idle"]
    assert plan["goal"]["interaction_point_id"] == "Bench_A_seat_01"
    assert plan["clips"] == {"Walk": "walk", "Sit Down": "sit_down", "Sit Idle": "sit_idle"}
    assert plan["locomotion"]["walk_distance"] == 3.0
    assert plan["locomotion"]["align_distance"] == 1.0
    assert plan["locomotion"]["walk_speed"] == 1.2
    assert plan["locomotion"]["walk_frames"] == 60
    assert plan["locomotion"]["align_frames"] == 20
    assert plan["locomotion"]["walk_clip_frames"] == 24
    assert plan["runtime"]["agent_count"] == 4
    assert round(plan["runtime"]["align_duration"], 6) == round(plan["locomotion"]["align_frames"] / 24.0, 6)
    assert plan["interaction_points"][0]["reserved_by"] == ""
    script = _plan_python_sop(plan)
    assert "geo.addAttrib(hou.attribType.Global, 'clip_sequence', '')" in script
    assert "geo.addAttrib(hou.attribType.Global, 'walk_speed', 0.0)" in script
    assert "geo.addAttrib(hou.attribType.Global, 'walk_clip_frames', 0)" in script
    assert "geo.addAttrib(hou.attribType.Point, 'behavior_steps', '')" in script
    assert "clip_sequence" in script
    preview_script = _single_agent_preview_python_sop(plan)
    assert "current_step" in preview_script
    assert "current_clip" in preview_script
    assert "loc['walk_end_frame']" in preview_script
    assert "align_to_seat" in preview_script
    transform_script = _apply_behavior_transform_python_sop(plan)
    assert "geo.merge(source)" in transform_script
    assert "behavior_position" in transform_script
    assert "agent_pos = lerp(app, seat, t)" in transform_script
    assert "Sit Idle" in transform_script
    assert "sit_heading = normalized_xz(app, seat" in transform_script
    assert "% 24" in _clip_time_expression("walk", plan)
    assert "if($F < 81" in _clip_time_expression("sit_down", plan)
    runtime_script = _runtime_behavior_python_sop(plan)
    assert "condition_based_preview" in runtime_script
    assert "reserved_by" in runtime_script
    assert "query_radius" in runtime_script
    assert "agent_states" in runtime_script
    assert "target_seats" in runtime_script
    assert "distance_to_targets" in runtime_script
    assert "align_duration" in runtime_script
    assert "return normalized_xz(seat['approach'], seat['position']" in runtime_script
    assert "seat_status" in runtime_script
    assert "point_attributes" in runtime_script
    assert "input_agent_geo" in runtime_script
    assert "input_seat_geo" in runtime_script
    assert "agent_source" in runtime_script
    assert "seat_source" in runtime_script
    assert "input_agent_count" in runtime_script
    assert "input_seat_count" in runtime_script
    source_script = _runtime_agent_source_python_sop(plan)
    assert "replaceable_agent_points_for_runtime_behavior" in source_script
    assert "agent_state" in source_script
    assert "target_seat" in source_script
    assert "validation_short_distance" in source_script
    assert "validation_start_distance" in source_script
    driver_script = _behavior_agent_driver_python_sop(plan)
    assert "import math" in driver_script
    assert "runtime_behavior_to_agent_clip_driver" in driver_script
    assert "OUT_BEHAVIOR_AGENT_POINTS" in driver_script
    assert "clipname" in driver_script
    assert "agentname" in driver_script
    assert "current_clip/clipname selects walk, sit_down, or sit_idle" in driver_script
    assert "target_position" in driver_script
    assert "motion_target_position" in driver_script
    assert "facing_target_position" in driver_script
    assert "move_heading" in driver_script
    assert "return heading_to_target(seat['approach'], seat['position']" in driver_script
    assert "heading" in driver_script
    assert "orient" in driver_script
    crowd_driver_script = _crowd_clip_state_driver_python_sop(plan)
    assert "crowd_clip_state_driver" in crowd_driver_script
    assert "clip_index" in crowd_driver_script
    assert "state_index" in crowd_driver_script
    assert "current_clip" in crowd_driver_script
    assert "agentclip" in crowd_driver_script
    assert "crowd_clip_ready" in crowd_driver_script
    assert "move_heading" in crowd_driver_script
    assert "velocity = (move_heading[0] * speed" in crowd_driver_script
    assert "if state == 'aligning_to_interaction':\n        return 0.0" in crowd_driver_script
    assert "walking_to_interaction:walk" in crowd_driver_script
    bridge_script = _agent_clip_bridge_python_sop(plan)
    assert "agent_clip_attribute_bridge" in bridge_script
    assert "OUT_AGENT_CLIP_BRIDGE" in bridge_script
    assert "crowd_clip_state_driver_only" in bridge_script
    assert "behavior_driver_clip" in bridge_script
    assert "agent_clip_ready" in bridge_script
    assert "move_heading" in bridge_script
    agent_crowd_script = _agent_crowd_behavior_python_sop(plan)
    assert "agent_crowd_behavior_runtime" in agent_crowd_script
    assert "OUT_AGENT_CROWD_BEHAVIOR" in agent_crowd_script
    assert "OUT_AGENT_CROWD_BEHAVIOR_UNPACKED" in agent_crowd_script
    assert "agent_primitive_scaffold" in agent_crowd_script
    assert "agent_crowd_ready" in agent_crowd_script
    assert "clipname" in agent_crowd_script
    assert "orient" in agent_crowd_script
    assert "v" in agent_crowd_script
    assert "agent_primitive_count" in agent_crowd_script
    visual_diag_script = _agent_crowd_visual_diagnostic_python_sop()
    assert "agent_crowd_visual_status" in visual_diag_script
    assert "visual_shape_candidates" in visual_diag_script
    assert "agent_collision_or_proxy_only" in visual_diag_script
    assert "OUT_AGENT_CROWD_VISUAL_DIAGNOSTIC" in visual_diag_script
    assert "skinned render mesh/shape layer" in visual_diag_script
    agent_parm_diag_script = _agent_definition_parameter_diagnostic_python_sop("/obj/agent_definition")
    assert "agent_definition_parameter_status" in agent_parm_diag_script
    assert "visual_auto_select_status" in agent_parm_diag_script
    assert "mesh_import_option_status" in agent_parm_diag_script
    assert "mesh_import_enabled_parameters" in agent_parm_diag_script
    assert "agent_reload_status" in agent_parm_diag_script
    assert "pressed_reload" in agent_parm_diag_script
    assert "visual_layer_menu_items" in agent_parm_diag_script
    assert "OUT_AGENT_DEFINITION_PARAMETER_DIAGNOSTIC" in agent_parm_diag_script
    experiment_script = _agent_clip_experiment_python_sop(plan)
    assert "agent_clip_connection_probe" in experiment_script
    assert "OUT_AGENT_CLIP_BRIDGE" in experiment_script
    assert "ensure_global_attrib('agent_count', 0)" in experiment_script
    assert "no_agent_nodes_created" in experiment_script
    assert "available_agent_clip_nodes" in experiment_script
    assert "recommended_clip_attribute" in experiment_script
    assert "OUT_AGENT_CLIP_NODE_TEST_INPUT" in experiment_script
    assert "test_nodes_are_bypassed" in experiment_script
    assert "clipname, agentclip, clip, agent_clip, current_clip, clip_index" in experiment_script
    result_script = _agent_clip_node_test_result_python_sop(plan)
    assert "agent_clip_node_test_result" in result_script
    assert "OUT_AGENT_CLIP_NODE_TEST_INPUT" in result_script
    assert "TEST_AGENTCLIP_*" in result_script
    assert "no_agent_nodes_cooked" in result_script
    assert "ready_for_manual_agent_clip_wire" in result_script
    walk_input_script = _agent_clip_walk_test_input_python_sop(plan)
    assert "agent_clip_walk_only_input" in walk_input_script
    assert "OUT_AGENT_CLIP_WALK_TEST_INPUT" in walk_input_script
    assert "TEST_AGENTCLIP_WALK" in walk_input_script
    assert "clip != 'walk'" in walk_input_script
    assert "ready_for_test_agentclip_walk" in walk_input_script
    walk_result_script = _agent_clip_walk_test_result_python_sop(plan)
    assert "agent_clip_walk_only_result" in walk_result_script
    assert "OUT_AGENT_CLIP_WALK_TEST_INPUT" in walk_result_script
    assert "target_test_node_bypassed" in walk_result_script
    assert "ready_walk_agent_count" in walk_result_script
    sit_down_input_script = _agent_clip_named_test_input_python_sop(
        plan,
        clip_name="sit_down",
        output_name="OUT_AGENT_CLIP_SIT_DOWN_TEST_INPUT",
        target_node="TEST_AGENTCLIP_SIT_DOWN",
    )
    assert "agent_clip_named_clip_input" in sit_down_input_script
    assert "OUT_AGENT_CLIP_SIT_DOWN_TEST_INPUT" in sit_down_input_script
    assert "TEST_AGENTCLIP_SIT_DOWN" in sit_down_input_script
    assert "target_clip = 'sit_down'" in sit_down_input_script
    assert "synthetic_clip_test_point" in sit_down_input_script
    assert "synthetic_clip_test_points" in sit_down_input_script
    assert "geo.createPoint()" in sit_down_input_script
    assert "fallback_position" in sit_down_input_script
    assert "def safe_set" in sit_down_input_script
    sit_idle_result_script = _agent_clip_named_test_result_python_sop(
        plan,
        clip_name="sit_idle",
        output_name="OUT_AGENT_CLIP_SIT_IDLE_TEST_INPUT",
        target_node="TEST_AGENTCLIP_SIT_IDLE",
    )
    assert "agent_clip_named_clip_result" in sit_idle_result_script
    assert "OUT_AGENT_CLIP_SIT_IDLE_TEST_INPUT" in sit_idle_result_script
    assert "ready_clip_agent_count" in sit_idle_result_script
    clipset_script = _agent_clip_clipset_test_result_python_sop(plan)
    assert "agent_clip_three_clip_result" in clipset_script
    assert "OUT_AGENT_CLIP_SIT_DOWN_TEST_INPUT" in clipset_script
    assert "OUT_AGENT_CLIP_SIT_IDLE_TEST_INPUT" in clipset_script
    assert "walk -> sit_down -> sit_idle" in clipset_script
    assert "clip_test_actual_input" in clipset_script
    assert "prewired_test_nodes" in clipset_script
    assert "unwired_test_nodes" in clipset_script
    assert "test_nodes_remain_bypassed" in clipset_script
    assert "ready_for_three_clip_agentclip_unbypass_tests" in clipset_script
    guard_script = _agent_clip_unbypass_guard_python_sop(plan)
    assert "agent_clip_unbypass_guard" in guard_script
    assert "active_test_node_count" in guard_script
    assert "safe_to_cook_single_test" in guard_script
    assert "all_agentclip_tests_bypassed_safe" in guard_script
    assert "single_agentclip_test_active" in guard_script
    assert "multiple_agentclip_tests_active_stop" in guard_script
    sequence_log_script = _agent_clip_activation_sequence_log_python_sop(plan)
    assert "agent_clip_activation_sequence_log" in sequence_log_script
    assert "ready_for_python_activation_sequence" in sequence_log_script
    assert "expected_print_output" in sequence_log_script
    assert "TEST_AGENTCLIP_SIT_IDLE" in sequence_log_script
    runtime_kinefx_script = _runtime_kinefx_preview_python_sop(plan)
    assert "runtime_behavior_clip_preview" in runtime_kinefx_script
    assert "input_crowd_clip_state_driver" in runtime_kinefx_script
    assert "clip_available" in runtime_kinefx_script
    assert "driver_heading" in runtime_kinefx_script
    assert "Display OUT_RUNTIME_AGENT_BEHAVIOR" in runtime_kinefx_script
    kinefx_diag_script = _kinefx_clip_diagnostic_python_sop(
        CrowdPrototypeFiles(
            ROOT / "character.fbx",
            ROOT / "walk.fbx",
            ROOT / "sit_down.fbx",
            ROOT / "sit_idle.fbx",
            ROOT / "interaction.yaml",
            ROOT / "animation.yaml",
        ),
        plan,
    )
    assert "OUT_KINEFX_CLIP_DIAGNOSTIC" in kinefx_diag_script
    assert "fbx_refresh_status" in kinefx_diag_script
    assert "fbx_reload_pressed" in kinefx_diag_script
    assert "walk_input_point_count" in kinefx_diag_script
    assert "walk_time_expression" in kinefx_diag_script
    assert "update animation.yaml from Maya" in kinefx_diag_script
    visual_preview_script = _agent_crowd_visual_preview_python_sop(plan)
    assert "agent_crowd_visual_preview_status" in visual_preview_script
    assert "OUT_AGENT_CROWD_VISUAL_PREVIEW" in visual_preview_script
    assert "kinefx_imports/OUT_RUNTIME_AGENT_BEHAVIOR" in visual_preview_script
    assert "agent_crowd_pipeline/OUT_AGENT_CROWD_BEHAVIOR" in visual_preview_script
    assert "viewport_only" in visual_preview_script
    assert "agent_crowd_behavior_is_authoritative" in visual_preview_script
    specs = _agent_clip_test_specs()
    assert [spec["clip"] for spec in specs] == ["walk", "sit_down", "sit_idle"]
    assert [spec["node"] for spec in specs] == ["TEST_AGENTCLIP_WALK", "TEST_AGENTCLIP_SIT_DOWN", "TEST_AGENTCLIP_SIT_IDLE"]
    assert callable(activate_agent_clip_test)
    assert callable(cook_crowd_dop_solver_smoke_test)
    assert callable(cook_agent_clip_test)
    assert callable(cook_clip_locomotion_test)
    assert callable(cook_crowd_source_test)
    assert callable(deactivate_agent_clip_tests)
    assert callable(ensure_crowd_dop_source_scaffold)
    assert callable(ensure_runtime_crowd_dop_source_scaffold)
    assert callable(ensure_runtime_dop_result_preview)
    assert callable(ensure_crowd_solver_dop_bridge)
    assert callable(ensure_crowd_solver_test_node)
    assert callable(apply_runtime_validation_short_distance)
    assert callable(_ensure_kinefx_clip_diagnostic)
    assert callable(_ensure_agent_character_preview)
    assert callable(_ensure_agent_character_unpacked_preview)
    assert callable(_ensure_agent_crowd_scaffold_nodes)
    assert callable(_ensure_agent_crowd_behavior_output)
    assert callable(_ensure_agent_crowd_behavior_unpacked_preview)
    assert callable(_ensure_agent_crowd_visual_diagnostic)
    assert callable(_ensure_agent_crowd_visual_preview)
    assert callable(_ensure_agent_definition_parameter_diagnostic)
    assert callable(_auto_select_agent_visual_layer_if_supported)
    assert callable(_enable_agent_mesh_import_options_if_supported)
    assert callable(_reload_agent_definition_if_supported)
    assert callable(_refresh_kinefx_fbx_imports_if_supported)
    assert callable(_press_reload_buttons_if_supported)
    assert callable(_agent_character_diagnostic_python_sop)
    assert callable(probe_crowd_dop_bridge_connection)
    assert callable(probe_crowd_dop_node_types)
    assert callable(probe_crowd_dop_scaffold_state)
    assert callable(probe_crowd_solver_connection)
    assert callable(probe_crowd_node_types)
    assert callable(refresh_agent_clip_unbypass_guard)
    assert callable(sample_runtime_dop_result_timeline)
    assert callable(ensure_runtime_timeline_sample_preview)
    assert callable(validate_runtime_seat_behavior_timeline)
    assert callable(run_agent_clip_activation_sequence)
    assert callable(run_agent_clip_cook_sequence)
    assert callable(run_clip_locomotion_cook_sequence)
    assert callable(run_crowd_dop_bridge_connection_sequence)
    assert callable(run_crowd_dop_source_scaffold_sequence)
    assert callable(run_crowd_dop_solver_smoke_test_sequence)
    assert callable(run_crowd_source_cook_sequence)
    assert callable(run_crowd_solver_connection_sequence)
    assert callable(cook_runtime_crowd_dop_solver_smoke_test)
    assert "blocked_by_missing_guard_geometry" in cook_agent_clip_test.__code__.co_consts
    assert "active_test_nodes" in cook_agent_clip_test.__code__.co_consts
    assert "missing_clip_locomotion_node" in cook_clip_locomotion_test.__code__.co_consts
    assert "missing_crowd_source_node" in cook_crowd_source_test.__code__.co_consts
    assert "missing_crowd_solver_node" in probe_crowd_solver_connection.__code__.co_consts
    assert "ready_for_solver_cook" in probe_crowd_solver_connection.__code__.co_consts
    assert "crowd_solver_requires_dop_network" in ensure_crowd_solver_test_node.__code__.co_consts
    assert "ready_dop_source_scaffold" in ensure_crowd_dop_source_scaffold.__code__.co_consts
    assert "ready_for_dop_source_scaffold" in probe_crowd_dop_node_types.__code__.co_consts
    assert "ready_for_explicit_dop_solver_cook" in probe_crowd_dop_scaffold_state.__code__.co_consts
    assert "ready_for_explicit_solver_cook" in cook_crowd_dop_solver_smoke_test.__code__.co_consts
    assert "solver_bypassed_after" in cook_crowd_dop_solver_smoke_test.__code__.co_consts
    assert "solver_activation_after" in cook_crowd_dop_solver_smoke_test.__code__.co_consts
    assert "solver_input_disconnected_after" in cook_crowd_dop_solver_smoke_test.__code__.co_consts
    assert "solver_input_summary_after" in cook_crowd_dop_solver_smoke_test.__code__.co_consts
    assert "solver_reset_to_safe" in cook_crowd_dop_solver_smoke_test.__code__.co_consts
    assert "solver_bypass_check" in cook_crowd_dop_solver_smoke_test.__code__.co_consts
    assert "ready_runtime_dop_source_scaffold" in ensure_runtime_crowd_dop_source_scaffold.__code__.co_consts
    assert "runtime_behavior_driver" in ensure_runtime_crowd_dop_source_scaffold.__code__.co_consts
    assert "ready_runtime_dop_result_preview" in ensure_runtime_dop_result_preview.__code__.co_consts
    assert "ready_runtime_validation_short_distance" in apply_runtime_validation_short_distance.__code__.co_consts
    assert "OUT_AGENT_CHARACTER" in _ensure_agent_character_preview.__code__.co_consts
    assert "OUT_KINEFX_CLIP_DIAGNOSTIC" in _ensure_kinefx_clip_diagnostic.__code__.co_consts
    assert "OUT_AGENT_CHARACTER_DIAGNOSTIC" in _ensure_agent_character_preview.__code__.co_consts
    assert "OUT_AGENT_CHARACTER_UNPACKED" in _ensure_agent_character_unpacked_preview.__code__.co_consts
    assert "OUT_AGENT_CROWD_BEHAVIOR" in _ensure_agent_crowd_behavior_output.__code__.co_consts
    assert "OUT_AGENT_CROWD_BEHAVIOR_UNPACKED" in _ensure_agent_crowd_behavior_unpacked_preview.__code__.co_consts
    assert "OUT_AGENT_CROWD_VISUAL_DIAGNOSTIC" in _ensure_agent_crowd_visual_diagnostic.__code__.co_consts
    assert "OUT_AGENT_CROWD_VISUAL_PREVIEW" in _ensure_agent_crowd_visual_preview.__code__.co_consts
    assert "OUT_AGENT_DEFINITION_PARAMETER_DIAGNOSTIC" in _ensure_agent_definition_parameter_diagnostic.__code__.co_consts
    character_diag_script = _agent_character_diagnostic_python_sop(
        CrowdPrototypeFiles(
            ROOT / "character.fbx",
            ROOT / "walk.fbx",
            ROOT / "sit_down.fbx",
            ROOT / "sit_idle.fbx",
            ROOT / "interaction.yaml",
            ROOT / "animation.yaml",
        )
    )
    assert "agent_character_diagnostic" in character_diag_script
    assert "shape_intrinsics" in character_diag_script
    assert "layer_intrinsics" in character_diag_script
    assert "ready_for_explicit_runtime_solver_cook" in cook_runtime_crowd_dop_solver_smoke_test.__code__.co_consts
    assert "sample_ok" in sample_runtime_dop_result_timeline.__code__.co_consts
    assert "agent_clip_summary" in sample_runtime_dop_result_timeline.__code__.co_consts
    assert "solver_source_is_safe_after" in sample_runtime_dop_result_timeline.__code__.co_consts
    assert "ready_runtime_timeline_sample_preview" in ensure_runtime_timeline_sample_preview.__code__.co_consts
    assert "runtime_seat_behavior_validated" in validate_runtime_seat_behavior_timeline.__code__.co_consts
    assert "missing_required_runtime_clips" in validate_runtime_seat_behavior_timeline.__code__.co_consts
    runtime_dop_preview_script = _runtime_dop_result_preview_python_sop(plan)
    assert "runtime_dop_handoff_preview" in runtime_dop_preview_script
    assert "OUT_RUNTIME_DOP_RESULT" in runtime_dop_preview_script
    assert "source_path_is_safe" in runtime_dop_preview_script
    assert "dop_source_is_safe" in runtime_dop_preview_script
    timeline_preview_script = _runtime_timeline_sample_preview_python_sop(
        [
            {
                "frame": 1,
                "status": "sample_ok",
                "agent_clip_summary": "agent_001:walk",
                "agent_state_summary": "agent_001:walking_to_interaction",
                "source_path_is_safe": 1,
            }
        ],
        allow_solver_cook=False,
    )
    assert "runtime_dop_timeline_samples" in timeline_preview_script
    assert "OUT_RUNTIME_TIMELINE_SAMPLES" in ensure_runtime_timeline_sample_preview.__code__.co_consts
    assert "sample_clip" in timeline_preview_script
    assert "sample_state" in timeline_preview_script
    assert "timeline_samples_are_static" in timeline_preview_script
    assert "sampled_frames" in timeline_preview_script
    assert "missing_dop_network_node_type" in ensure_crowd_solver_dop_bridge.__code__.co_consts
    assert "ready_for_dop_crowd_solver_network" in probe_crowd_dop_bridge_connection.__code__.co_consts
    assert "dopNodeTypeCategory" in probe_crowd_node_types.__code__.co_consts
    assert callable(_agent_clip_active_state)


def test_houdini_kinefx_plan_uses_behavior_walk_speed_override():
    data = {
        "interactions": [
            {
                "id": "Bench_A",
                "type": "bench",
                "points": [
                    {
                        "id": "Bench_A_seat_01",
                        "interaction_type": "seat",
                        "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                        "approach_position": {"x": -1.0, "y": 0.0, "z": 0.0},
                        "enabled": True,
                        "priority": 0,
                    }
                ],
            }
        ]
    }
    animation_data = {"behavior": {"fps": 24, "walk_speed": 2.0, "start_distance": 4.0}}

    plan = build_single_agent_plan_from_data(
        data,
        animation_data=animation_data,
        schema_path=ROOT / "config" / "behavior_schema.yaml",
    )

    assert plan["locomotion"]["walk_speed"] == 2.0
    assert plan["locomotion"]["walk_distance"] == 4.0
    assert plan["locomotion"]["walk_frames"] == 48


def test_houdini_kinefx_plan_uses_maya_root_motion_speed_houdini():
    data = {
        "interactions": [
            {
                "id": "Bench_A",
                "type": "bench",
                "points": [
                    {
                        "id": "Bench_A_seat_01",
                        "interaction_type": "seat",
                        "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                        "approach_position": {"x": -1.0, "y": 0.0, "z": 0.0},
                        "enabled": True,
                        "priority": 0,
                    }
                ],
            }
        ]
    }
    animation_data = {
        "animation": {
            "speed": 6.0,
            "speed_houdini": 0.06,
            "rootMotion": {
                "source": "Male:local_C0_ctl",
                "source_namespace": "Male",
                "source_node": "local_C0_ctl",
                "axis": "tz",
                "duration_frames": 24,
                "distance": 6.0,
                "distance_houdini": 0.06,
                "unit": "cm",
                "unit_scale_to_houdini": 0.01,
                "speed_houdini": 0.06,
            },
        },
        "behavior": {
            "fps": 24,
            "start_distance": 0.12,
        },
    }

    plan = build_single_agent_plan_from_data(
        data,
        animation_data=animation_data,
        schema_path=ROOT / "config" / "behavior_schema.yaml",
    )

    assert plan["locomotion"]["walk_speed"] == 0.06
    assert round(plan["locomotion"]["walk_distance"], 6) == 0.12
    assert plan["locomotion"]["walk_frames"] == 48
    assert plan["locomotion"]["walk_clip_frames"] == 24
    assert _animation_speed_for_houdini({"speed": 6.0}, {"unit_scale_to_houdini": 0.01}) == 0.06


def test_maya_root_motion_source_resolution_prefers_explicit_rules():
    cmds = _FakeMayaCmds()

    assert _resolve_root(cmds, "Arg:root", "anim_node") == "Arg:root"
    assert _resolve_root(cmds, None, "anim_node") == "Shot:manual_root"

    cmds.attrs["anim_node.rootMotionSource"] = ""
    assert _resolve_root(cmds, None, "anim_node") == "Rig:metadata_root"

    cmds.attrs.pop("Rig:metadata_root.smartCrowdRole")
    assert _resolve_root(cmds, None, "anim_node") == "Selected:root"

    cmds.selection = []
    assert _resolve_root(cmds, None, "anim_node") == "local_C0_ctl"


class _FakeMayaCmds:
    def __init__(self) -> None:
        self.nodes = {
            "anim_node": "network",
            "Arg:root": "transform",
            "Shot:manual_root": "transform",
            "Rig:metadata_root": "transform",
            "Selected:root": "transform",
            "local_C0_ctl": "transform",
        }
        self.attrs = {
            "anim_node.rootMotionSource": "Shot:manual_root",
            "Rig:metadata_root.smartCrowdRole": "root_motion",
        }
        self.selection = ["Selected:root"]

    def objExists(self, name: str) -> bool:
        return name in self.nodes or name in self.attrs

    def getAttr(self, plug: str):
        return self.attrs.get(plug)

    def ls(self, *args, **kwargs):
        node_type = kwargs.get("type")
        if kwargs.get("selection"):
            values = list(self.selection)
        elif args:
            pattern = str(args[0])
            values = [node for node in self.nodes if _matches_maya_pattern(pattern, node)]
        else:
            values = list(self.nodes)
        if node_type:
            values = [node for node in values if self.nodes.get(node) == node_type]
        return values


def _matches_maya_pattern(pattern: str, node: str) -> bool:
    if pattern == node:
        return True
    if "*" not in pattern:
        return False
    prefix, _, suffix = pattern.partition("*")
    return node.startswith(prefix) and node.endswith(suffix)
