# Smart Crowd Behavior

This prototype separates two systems from the first implementation.

- Interaction System owns what exists in the world: seats, doors, stairs and future semantic affordances.
- Behavior System owns what a character wants to do: find an affordance, walk, align, sit and idle.

Behavior code consumes only `interaction_type`. A bench, chair or sofa should all publish a point whose `interaction_type` is `seat`; the behavior does not inspect the source asset type.

## Schema

The schema is `config/behavior_schema.yaml`.

Maya tools read `interaction_types`, `animation_types` and `animation_styles` from that file. New types should be added there instead of as Python enums.

## Maya

Main modules:

- `smartlib.dcc.maya.crowd_interaction`
- `smartlib.dcc.maya.crowd_animation`
- `smartlib.dcc.maya.crowd_analyzer`
- `smartlib.dcc.maya.crowd_exporter`

Example:

```python
from smartlib.dcc.maya import crowd_interaction, crowd_animation, crowd_analyzer, crowd_exporter

crowd_interaction.create_seat_gizmo("Bench_A", interaction_type="seat")
node = crowd_animation.create_animation_properties_node(
    animation_type="interaction",
    animation_style="normal",
    interaction="seat",
)
# Optional for rigs without Smart Crowd metadata:
# select the root motion controller, then store it on this animation node.
crowd_animation.set_root_motion_source_from_selection(node)
crowd_analyzer.analyze_animation_publish(node)
crowd_exporter.export_interaction_yaml("P:/tmp/interaction.yaml")
crowd_exporter.export_animation_yaml("P:/tmp/animation.yaml", node=node)
```

The Interaction Gizmo is curve-based, not a locator. It contains visual children for Seat Point, Approach Point and Forward.

## Houdini

Main modules:

- `smartlib.dcc.houdini.crowd_loader`
- `smartlib.dcc.houdini.crowd_behavior`

The loader converts `interaction.yaml` into point data with at least these attributes:

- `interaction_type`
- `enabled`
- `priority`
- `seat_id`

The data-level prototype can run a single agent goal:

```python
from smartlib.dcc.houdini.crowd_behavior import run_single_agent_interaction_goal

trace = run_single_agent_interaction_goal(
    "P:/tmp/interaction.yaml",
    interaction_type="seat",
)
print(trace.steps)
```

When the character and three FBX clips are ready, place these files in one folder:

```text
character.fbx
walk.fbx
sit_down.fbx
sit_idle.fbx
interaction.yaml
animation.yaml
```

Then run this inside Houdini:

```text
SmartMenu shelf > Create Crowd Seat Prototype
```

Choose the folder containing the FBX clips and YAML files. The shelf button
calls the same create command as the Python example below.

```python
import importlib
import smartlib.dcc.houdini.crowd_kinefx as crowd_kinefx

importlib.reload(crowd_kinefx)
crowd_kinefx.create_single_agent_seat_prototype("D:/Projects/Onishima/test/crowd")
```

This creates a `/obj/smart_crowd_seat_proto` network with:

- `interaction_points`: Python SOP that generates seat point attributes from `interaction.yaml`
- `behavior_plan`: detail attributes for the current single-agent behavior trace
- `kinefx_imports`: best-effort KineFX FBX import nodes for character, walk, sit_down and sit_idle
- `single_agent_controller`: animated viewport preview of the behavior plan
- `runtime_behavior_preview`: condition-based preview that reads CrowdSource/agent points when available
- `agent_crowd_pipeline`: scaffold for the future Agent/Agent Clip/Clip Locomotion/Crowd Source path

For subsequent `interaction.yaml` / `animation.yaml` updates in the same
Houdini session, use the lightweight updater:

```python
import importlib
import smartlib.dcc.houdini.crowd_kinefx as crowd_kinefx

importlib.reload(crowd_kinefx)
crowd_kinefx.update_single_agent_seat_prototype("D:/Projects/Onishima/test/crowd")
```

The updater refreshes `interaction_points`, `behavior_plan`,
`single_agent_controller`, Time Shift expressions and the behavior transform.
It does not rebuild or delete FBX/KineFX import nodes, which avoids crashes that
can occur when cooked FBX import nodes are recreated in Houdini.

To preview behavior in Houdini, display:

```text
/obj/smart_crowd_seat_proto*/single_agent_controller/OUT_SINGLE_AGENT_PREVIEW
```

Frame ranges:

```text
Walk      Move from start to approach_position. The walk clip is cycled.
Align     Keep cycling the walk clip and move from approach_position to seat.
Sit Down  Play the sit_down clip at seat without extra behavior translation.
Sit Idle  Hold at seat while the sit_idle clip plays.
```

The preview geometry includes a red `agent_001` point, an approach point, the
target seat point, and a path line. Geometry Spreadsheet attributes include
`current_step`, `current_clip`, and `target_seat`.

To preview the future Crowd Source style behavior, display:

```text
/obj/smart_crowd_seat_proto*/runtime_behavior_preview/OUT_RUNTIME_BEHAVIOR
```

This node does not rebuild FBX imports. It reads input geometry when available
and advances agents by conditions:

```text
IN_AGENTS -> simulate_condition_based_behavior input 0
IN_SEATS  -> simulate_condition_based_behavior input 1
```

By default, `IN_AGENTS` points at:

```text
/obj/smart_crowd_seat_proto*/agent_crowd_pipeline/OUT_AGENT_SOURCE_POINTS
```

`OUT_AGENT_SOURCE_POINTS` is a replaceable point source. It exists so the
Behavior SOP already consumes input agent points. Later, point `IN_AGENTS` at a
real Crowd Source output instead.

and `IN_SEATS` points at:

```text
/obj/smart_crowd_seat_proto*/interaction_points/OUT_SEAT_POINTS
```

If `IN_AGENTS` is empty, fallback agents are generated as points for preview
only. The detail attribute `agent_source` shows which path is active:

```text
input_agents        Uses incoming CrowdSource/agent points.
generated_fallback  Uses temporary generated preview agents.
```

The behavior rules are:

```text
Find available seat within query_radius
Reserve seat with reserved_by
Walk to approach_position
Align/move to seat while walk clip continues
Sit Down at seat
Sit Idle and set occupied=true
```

Important detail attributes:

```text
runtime_mode
agent_source
seat_source
rule_summary
how_to_check
point_attributes
agent_states
current_steps
current_clips
target_seats
distance_to_targets
seat_status
agent_count
input_agent_count
input_seat_count
available_seats
reserved_seats
occupied_seats
query_radius
walk_speed
align_speed
sit_down_duration
```

Important point attributes:

```text
name
entity_type
agent_state
current_step
current_clip
target_seat
distance_to_target
reserved_by
occupied
```

To connect a real Crowd Source, change `runtime_behavior_preview/IN_AGENTS`
`objpath1` to the SOP that outputs the agent points. Required point attributes
are intentionally minimal:

```text
P
name, optional
agent_state, optional
current_step, optional
current_clip, optional
target_seat, optional
```

If `kinefx_imports` still shows only `sit_idle_fbx -> OUT_KINEFX_CLIPS`, the
current Houdini session is using an older cached Python module. Re-run the
command above with `importlib.reload(crowd_kinefx)`. A current network contains:

```text
OUT_WALK
OUT_SIT_DOWN
OUT_SIT_IDLE
TIME_WALK_CLIP
TIME_SIT_DOWN_CLIP
TIME_SIT_IDLE_CLIP
SWITCH_CLIP_BY_FRAME
OUT_KINEFX_CLIPS
APPLY_BEHAVIOR_TRANSFORM
OUT_AGENT_BEHAVIOR
```

Display `OUT_AGENT_BEHAVIOR` to see the selected clip placed along the behavior
path. `OUT_KINEFX_CLIPS` is the raw clip switch before behavior placement.

The runtime behavior output is also bridged into `agent_crowd_pipeline`:

```text
/obj/smart_crowd_seat_proto*/agent_crowd_pipeline/OUT_BEHAVIOR_AGENT_POINTS
```

This node reads:

```text
/obj/smart_crowd_seat_proto*/runtime_behavior_preview/OUT_RUNTIME_BEHAVIOR
```

and extracts only `entity_type = agent` points. It normalizes the behavior
attributes into the names expected by the future Agent Clip path:

```text
name
agentname
agentid
agent_state
state
current_step
current_clip
clipname
target_seat
target_position
heading
orient
distance_to_target
```

Use `current_clip` or `clipname` to drive clip selection:

```text
walk
sit_down
sit_idle
```

The next point-based handoff for production Crowd/Agent Clip wiring is:

```text
/obj/smart_crowd_seat_proto*/agent_crowd_pipeline/OUT_CROWD_CLIP_STATE_DRIVER
```

This preserves the behavior driver and adds crowd-friendly aliases:

```text
clip
agentclip
agent_clip
crowd_state
clip_index
state_index
speed
v
clip_loop
clip_transition
crowd_clip_ready
```

Its state-to-clip mapping is:

```text
walking_to_interaction -> walk
aligning_to_interaction -> walk
sitting_down -> sit_down
sit_idle -> sit_idle
```

The first safe bridge toward actual Agent Clip/Crowd Solver nodes is:

```text
/obj/smart_crowd_seat_proto*/agent_crowd_pipeline/OUT_AGENT_CLIP_BRIDGE
```

This node reads only the Crowd Clip State Driver for now. It intentionally does
not read the experimental Agent/Crowd scaffold, because incomplete Agent nodes
can invalidate the bridge while the prototype is still being wired. Important
detail attributes:

```text
bridge_mode
bridge_source
scaffold_point_count
driver_point_count
ready_agent_count
transferred_attributes
next_step
```

Use this bridge as the first input for version-specific Houdini Agent
Clip/Crowd Solver tests.

The non-destructive Agent Clip experiment probe is:

```text
/obj/smart_crowd_seat_proto*/agent_clip_experiment/OUT_AGENT_CLIP_EXPERIMENT
```

This node reads `OUT_AGENT_CLIP_BRIDGE`, detects which Agent/Crowd SOP node
types exist in the current Houdini build, and reports the safest first
connection attributes. The safe input for manual node wiring is:

```text
/obj/smart_crowd_seat_proto*/agent_clip_experiment/OUT_AGENT_CLIP_NODE_TEST_INPUT
```

The safe summary of that manual wiring contract is:

```text
/obj/smart_crowd_seat_proto*/agent_clip_experiment/OUT_AGENT_CLIP_NODE_TEST_RESULT
```

For the first single-clip Agent Clip test, use the walk-only input:

```text
/obj/smart_crowd_seat_proto*/agent_clip_experiment/OUT_AGENT_CLIP_WALK_TEST_INPUT
```

And confirm its safe summary:

```text
/obj/smart_crowd_seat_proto*/agent_clip_experiment/OUT_AGENT_CLIP_WALK_TEST_RESULT
```

The same single-clip test inputs are created for the remaining seat behavior
clips:

```text
/obj/smart_crowd_seat_proto*/agent_clip_experiment/OUT_AGENT_CLIP_SIT_DOWN_TEST_INPUT
/obj/smart_crowd_seat_proto*/agent_clip_experiment/OUT_AGENT_CLIP_SIT_DOWN_TEST_RESULT
/obj/smart_crowd_seat_proto*/agent_clip_experiment/OUT_AGENT_CLIP_SIT_IDLE_TEST_INPUT
/obj/smart_crowd_seat_proto*/agent_clip_experiment/OUT_AGENT_CLIP_SIT_IDLE_TEST_RESULT
```

The three-clip setup summary is:

```text
/obj/smart_crowd_seat_proto*/agent_clip_experiment/OUT_AGENT_CLIP_THREE_CLIP_TEST_RESULT
```

Before cooking any Agent Clip test, check the un-bypass guard:

```text
/obj/smart_crowd_seat_proto*/agent_clip_experiment/OUT_AGENT_CLIP_UNBYPASS_GUARD
```

To see the expected Python activation sequence in the Geometry Spreadsheet:

```text
/obj/smart_crowd_seat_proto*/agent_clip_experiment/OUT_AGENT_CLIP_SEQUENCE_LOG
```

The experiment network may also contain bypassed `TEST_*` Agent/Crowd nodes for
parameter inspection. `TEST_AGENTCLIP_WALK`, `TEST_AGENTCLIP_SIT_DOWN`, and
`TEST_AGENTCLIP_SIT_IDLE` are prewired to their matching
`OUT_AGENT_CLIP_*_TEST_INPUT` nodes, but they remain bypassed and are not
connected to `OUT_AGENT_CLIP_EXPERIMENT`.
Important detail attributes:

```text
experiment_mode
no_agent_nodes_created
available_agent_clip_nodes
available_clip_locomotion_nodes
available_crowd_solver_nodes
recommended_clip_attribute
recommended_state_attribute
candidate_clip_attributes
candidate_state_attributes
candidate_motion_attributes
detected_clips
safe_node_test_input
test_nodes_are_bypassed
next_connection_step
```

`OUT_AGENT_CLIP_NODE_TEST_RESULT` reports:

```text
node_test_result_mode
no_agent_nodes_cooked
recommended_first_input
recommended_clip_attribute
recommended_state_attribute
recommended_motion_attributes
created_test_nodes
bypassed_test_nodes
manual_wire_order
result_status
next_step
```

`OUT_AGENT_CLIP_SIT_DOWN_TEST_RESULT` and
`OUT_AGENT_CLIP_SIT_IDLE_TEST_RESULT` report the same generic clip test
contract:

```text
clip_test_result_mode
input_source
no_agent_nodes_cooked
target_clip
target_test_node
target_test_node_exists
target_test_node_bypassed
recommended_clip_attribute
recommended_state_attribute
recommended_motion_attributes
manual_wire
clip_agent_count
ready_clip_agent_count
detected_clips
result_status
next_step
```

`OUT_AGENT_CLIP_WALK_TEST_RESULT` reports:

```text
walk_test_result_mode
input_source
no_agent_nodes_cooked
target_test_node
target_test_node_exists
target_test_node_bypassed
recommended_clip_attribute
recommended_state_attribute
recommended_motion_attributes
manual_wire
walk_agent_count
ready_walk_agent_count
detected_clips
result_status
next_step
```

`OUT_AGENT_CLIP_THREE_CLIP_TEST_RESULT` reports:

```text
clipset_test_result_mode
no_agent_nodes_cooked
expected_clip_sequence
input_sources
manual_wire_order
recommended_clip_attribute
recommended_state_attribute
recommended_motion_attributes
walk_agent_count
sit_down_agent_count
sit_idle_agent_count
present_current_clips
missing_current_clips
missing_test_nodes
bypassed_test_nodes
prewired_test_nodes
unwired_test_nodes
test_nodes_remain_bypassed
result_status
next_step
```

`OUT_AGENT_CLIP_UNBYPASS_GUARD` reports:

```text
unbypass_guard_mode
no_agent_nodes_cooked
monitored_test_nodes
active_test_node_count
active_test_nodes
bypassed_test_nodes
missing_test_nodes
prewired_test_nodes
unwired_test_nodes
safe_to_cook_single_test
recommended_display_node
result_status
next_step
```

Expected safe states:

```text
all_agentclip_tests_bypassed_safe
single_agentclip_test_active
```

Displaying `TEST_AGENTCLIP_WALK` does not un-bypass it. To activate exactly one
Agent Clip test and refresh the guard, use Houdini Python:

```python
from smartlib.dcc.houdini import crowd_kinefx

crowd_kinefx.activate_agent_clip_test("walk")
```

Then display the guard again and expect:

```text
result_status = single_agentclip_test_active
recommended_display_node = TEST_AGENTCLIP_WALK
```

After the test, return to the safe state:

```python
crowd_kinefx.deactivate_agent_clip_tests()
```

To step through all three clip tests one at a time and automatically return to
the all-bypassed safe state:

```python
results = crowd_kinefx.run_agent_clip_activation_sequence()
for item in results:
    print(item["clip"], item["result_status"], item["recommended_display_node"])
```

To cook the actual Agent Clip test nodes one at a time and return to the safe
all-bypassed state:

```python
results = crowd_kinefx.run_agent_clip_cook_sequence()
for item in results:
    print(item["clip"], item["cook_status"], item["input_point_count"], item["point_count"], item["error"])
```

Expected first success state:

```text
walk cook_ok
```

For `sit_down` and `sit_idle`, the test input creates a synthetic single test
point when the current timeline frame is not on that clip. Confirm
`input_point_count` first; `point_count` is the Agent Clip node output count.

After the three Agent Clip tests report `cook_ok`, test Clip Locomotion:

```python
results = crowd_kinefx.run_clip_locomotion_cook_sequence()
for item in results:
    print(
        item["clip"],
        item["cook_status"],
        item["agent_clip_point_count"],
        item["input_point_count"],
        item["point_count"],
        item["error"],
    )
```

Expected first success state:

```text
walk cook_ok
```

After Clip Locomotion reports `cook_ok` for all clips, test Crowd Source. This
keeps Crowd Solver bypassed:

```python
results = crowd_kinefx.run_crowd_source_cook_sequence()
for item in results:
    print(
        item["clip"],
        item["cook_status"],
        item["clip_locomotion_point_count"],
        item["input_point_count"],
        item["point_count"],
        item["error"],
    )
```

Expected first success state:

```text
walk cook_ok
```

If Houdini does not have the Crowd Source node type available, the function
returns `missing_crowd_source_node` instead of touching the solver.

When Crowd Source reports `cook_ok`, probe the Crowd Solver connection. This
does not cook the solver; `TEST_CROWD_SOLVER` remains bypassed:

If `missing_crowd_solver_node` appears, first inspect the Crowd node types that
this Houdini session exposes:

```python
info = crowd_kinefx.probe_crowd_node_types()
for key, value in info.items():
    print(key, value)
```

Then ask the tool to create `TEST_CROWD_SOLVER` if a SOP Crowd Solver type is
available:

```python
print(crowd_kinefx.ensure_crowd_solver_test_node())
```

If `solver_location_hint = dop_network_required`, this Houdini exposes Crowd
Solver as a DOP node, so the next implementation step is a DOP Network bridge
rather than a SOP `TEST_CROWD_SOLVER`.

Create and probe the DOP Network bridge without cooking the DOP simulation:

```python
bridge = crowd_kinefx.ensure_crowd_solver_dop_bridge()
print(bridge)
```

Then validate the Crowd Source -> DOP bridge handoff:

```python
results = crowd_kinefx.run_crowd_dop_bridge_connection_sequence()
for item in results:
    print(
        item["clip"],
        item["probe_status"],
        item["crowd_source_status"],
        item["crowd_source_point_count"],
        item["source_sop_path"],
        item["dop_network_path"],
        item["dop_crowd_solver_path"],
        item["dop_crowd_solver_node_type"],
        item["error"],
    )
```

Expected first success state:

```text
walk ready_for_dop_crowd_solver_network
```

This only creates the DOP scaffold. The actual Crowd Object / source binding
and Solver cooking are separate next steps.

Inspect the DOP node types needed for source binding:

```python
info = crowd_kinefx.probe_crowd_dop_node_types()
for key, value in info.items():
    print(key, value)
```

Create the non-cooked DOP source scaffold:

```python
results = crowd_kinefx.run_crowd_dop_source_scaffold_sequence()
for item in results:
    print(
        item["clip"],
        item["status"],
        item["crowd_source_status"],
        item["crowd_source_point_count"],
        item["source_sop_path"],
        item["dop_crowd_object_node_type"],
        item["dop_source_geometry_node_type"],
        item["source_path_parameters"],
        item["wire_summary"],
        item["error"],
    )
```

Expected first success state:

```text
walk ready_dop_source_scaffold
```

If `missing_dop_source_scaffold_node_type` appears, report
`probe_crowd_dop_node_types()` output before cooking any DOP node.

Inspect the DOP scaffold state before any Solver cook:

```python
state = crowd_kinefx.probe_crowd_dop_scaffold_state()
for key, value in state.items():
    print(key, value)
```

Then run the guarded Solver smoke-test preflight. By default this does not cook
the DOP Solver:

```python
results = crowd_kinefx.run_crowd_dop_solver_smoke_test_sequence()
for item in results:
    print(
        item["clip"],
        item["smoke_status"],
        item["prepare_status"],
        item["scaffold_status"],
        item["cook_attempted"],
        item["solver_bypassed_before"],
        item["solver_bypassed_after"],
        item["solver_activation_before"],
        item["solver_activation_after"],
        item["solver_input_disconnected_after"],
        item["solver_input_safe_after"],
        item["solver_gate_safe_after"],
        item["solver_source_is_safe_after"],
        item["solver_safe_after"],
        item["solver_reset_to_safe"],
        item["solver_empty_source_sop_path"],
        item["solver_source_sop_path_after"],
        item["solver_safe_input_path"],
        item["solver_activation_parameters"],
        item["solver_input_summary_after"],
        item["solver_gate_summary_after"],
        item["solver_bypass_check"],
        item["error"],
    )
```

Expected first preflight state:

```text
walk ready_for_explicit_solver_cook
```

Only after the preflight is clean, explicitly allow a one-frame DOP cook:

```python
results = crowd_kinefx.run_crowd_dop_solver_smoke_test_sequence(
    allow_solver_cook=True,
    frame=1,
)
for item in results:
    print(
        item["clip"],
        item["smoke_status"],
        item["cook_attempted"],
        item["solver_bypassed_after"],
        item["solver_activation_after"],
        item["solver_input_disconnected_after"],
        item["solver_input_safe_after"],
        item["solver_gate_safe_after"],
        item["solver_source_is_safe_after"],
        item["solver_safe_after"],
        item["solver_reset_to_safe"],
        item["solver_empty_source_sop_path"],
        item["solver_source_sop_path_after"],
        item["solver_safe_input_path"],
        item["solver_activation_parameters"],
        item["solver_input_summary_after"],
        item["solver_gate_summary_after"],
        item["solver_bypass_check"],
        item["error"],
    )
```

After the clip-by-clip Solver smoke test is clean, switch the DOP source to the
runtime behavior driver without cooking the Solver:

```python
info = crowd_kinefx.ensure_runtime_crowd_dop_source_scaffold()
for key, value in info.items():
    print(key, value)
```

Expected success state:

```text
status ready_runtime_dop_source_scaffold
source_mode runtime_behavior_driver
source_path_is_safe_after 1
```

Then run the guarded runtime preflight. This still does not cook the DOP Solver:

```python
item = crowd_kinefx.cook_runtime_crowd_dop_solver_smoke_test()
print(
    item["source_mode"],
    item["smoke_status"],
    item["prepare_status"],
    item["scaffold_status"],
    item["cook_attempted"],
    item["runtime_source_point_count"],
    item["runtime_ready_agent_count"],
    item["solver_source_is_safe_after"],
    item["solver_safe_after"],
    item["solver_reset_to_safe"],
    item["runtime_clip_summary"],
    item["solver_empty_source_sop_path"],
    item["solver_source_sop_path_after"],
    item["error"],
)
```

Expected preflight state:

```text
runtime_behavior_driver ready_for_explicit_runtime_solver_cook
```

Only after that preflight is clean, explicitly allow one runtime DOP cook:

```python
item = crowd_kinefx.cook_runtime_crowd_dop_solver_smoke_test(
    allow_solver_cook=True,
    frame=1,
)
print(
    item["source_mode"],
    item["smoke_status"],
    item["cook_attempted"],
    item["runtime_source_point_count"],
    item["runtime_ready_agent_count"],
    item["solver_source_is_safe_after"],
    item["solver_safe_after"],
    item["solver_reset_to_safe"],
    item["runtime_clip_summary"],
    item["solver_empty_source_sop_path"],
    item["solver_source_sop_path_after"],
    item["error"],
)
```

Expected one-frame runtime state:

```text
runtime_behavior_driver cook_ok
```

Create or refresh the displayable runtime DOP handoff result node:

```python
info = crowd_kinefx.ensure_runtime_dop_result_preview()
for key, value in info.items():
    print(key, value)
```

Expected result:

```text
status ready_runtime_dop_result_preview
driver_source OUT_AGENT_CLIP_BRIDGE
source_path_is_safe 1
```

Then display this node:

```text
/obj/smart_crowd_seat_proto/runtime_dop_result_preview/OUT_RUNTIME_DOP_RESULT
```

Point attributes show the runtime agents being handed to the DOP scaffold:

```text
dop_preview_ready
dop_source_mode
dop_runtime_source_sop_path
dop_current_source_path
dop_source_is_safe
dop_solver_path
```

Detail attributes show:

```text
dop_preview_mode
driver_source
source_mode
runtime_source_sop_path
current_source_path
source_path_is_safe
empty_source_sop_path
dop_network_path
dop_solver_path
solver_node_type
agent_count
ready_agent_count
clip_summary
state_summary
```

To see the current-frame behavior while scrubbing the timeline, display:

```text
/obj/smart_crowd_seat_proto/runtime_dop_result_preview/OUT_RUNTIME_DOP_RESULT
```

For a short validation timeline, place `agent_001` near the Seat approach point
without editing `interaction.yaml` or `animation.yaml`:

```python
info = crowd_kinefx.apply_runtime_validation_short_distance(
    start_distance=0.0,
    spawn_radius=0.75,
)
for key, value in info.items():
    print(key, value)
```

Expected result:

```text
status ready_runtime_validation_short_distance
validation_short_distance 1
```

To build a static frame-by-frame table, sample the runtime handoff without
cooking the Solver:

```python
rows = crowd_kinefx.sample_runtime_dop_result_timeline(end_frame=480)
for item in rows:
    print(
        item["frame"],
        item["status"],
        item["point_count"],
        item["ready_agent_count"],
        item["source_path_is_safe"],
        item["agent_clip_summary"],
        item["agent_state_summary"],
        item["error"],
    )
```

Expected rows should show `agent_001` moving from `walk` toward `sit_down`
and then `sit_idle` as frames advance. Agents with no seat remain `none`.
If only `walk` appears, increase `end_frame` or confirm the printed
`agent_state_summary`.

Only after the non-Solver sample is clean, the same timeline can perform a
guarded one-frame DOP smoke cook at each sampled frame:

```python
rows = crowd_kinefx.sample_runtime_dop_result_timeline(
    frames=(1, 48, 96, 144, 192, 240),
    allow_solver_cook=True,
)
for item in rows:
    print(
        item["frame"],
        item["status"],
        item["smoke_status"],
        item["cook_attempted"],
        item["solver_source_is_safe_after"],
        item["solver_safe_after"],
        item["solver_reset_to_safe"],
        item["agent_clip_summary"],
        item["error"],
    )
```

Create a displayable timeline sample node from the same sampled rows:

```python
info = crowd_kinefx.ensure_runtime_timeline_sample_preview(end_frame=480)
for key, value in info.items():
    print(key, value)
```

Expected result:

```text
status ready_runtime_timeline_sample_preview
all_samples_ok 1
all_sources_safe 1
observed_clips walk, sit_down, sit_idle
```

Then display:

```text
/obj/smart_crowd_seat_proto/runtime_timeline_samples/OUT_RUNTIME_TIMELINE_SAMPLES
```

This node is a static sampled table, not an animated node. Each point is one
sampled frame/agent. Useful point attributes:

```text
sample_frame
agent_name
sample_clip
sample_state
sample_source_path_is_safe
sample_smoke_status
sample_solver_safe_after
```

Detail includes:

```text
timeline_samples_are_static
sampled_frames
observed_clips
observed_states
```

Run the runtime seat behavior acceptance check:

```python
result = crowd_kinefx.validate_runtime_seat_behavior_timeline(end_frame=480)
for key, value in result.items():
    print(key, value)
```

Expected result:

```text
status runtime_seat_behavior_validated
missing_clips none
all_samples_ok 1
all_sources_safe 1
observed_clips walk, sit_down, sit_idle
```

If the guarded DOP smoke cook should be included in the acceptance check:

```python
result = crowd_kinefx.validate_runtime_seat_behavior_timeline(
    frames=(1, 48, 96, 144, 192, 240, 360, 480),
    allow_solver_cook=True,
)
for key, value in result.items():
    print(key, value)
```

```python
results = crowd_kinefx.run_crowd_solver_connection_sequence()
for item in results:
    print(
        item["clip"],
        item["probe_status"],
        item["crowd_source_status"],
        item["crowd_source_point_count"],
        item["solver_input_point_count"],
        item["solver_bypassed"],
        item["crowd_solver_node_type"],
        item["error"],
    )
```

Expected first success state:

```text
walk ready_for_solver_cook
```

Only continue to an actual Solver cook after every row reports
`ready_for_solver_cook` and `solver_bypassed = 1`.

Stop and re-bypass nodes if:

```text
multiple_agentclip_tests_active_stop
```

`OUT_AGENT_CLIP_SEQUENCE_LOG` reports the expected three Python Shell rows in:

```text
expected_print_output
```

The Crowd Clip State Driver is also read by the KineFX prototype:

```text
/obj/smart_crowd_seat_proto*/kinefx_imports/OUT_RUNTIME_AGENT_BEHAVIOR
```

Display this node to see the first runtime agent using the selected KineFX clip.
It reads:

```text
/obj/smart_crowd_seat_proto*/agent_crowd_pipeline/OUT_CROWD_CLIP_STATE_DRIVER
```

Its detail attributes show:

```text
runtime_kinefx_mode
driver_source
agent_name
agent_state
current_clip
target_seat
behavior_position
clip_available
```

`agent_crowd_pipeline` is still a safe scaffold for the next production path,
but it now has a behavior-side driver output ready for wiring:

```text
Agent definition
  -> Agent Clips: walk, sit_down, sit_idle
  -> Clip Locomotion
  -> Crowd Source with one agent
  -> OUT_CROWD_CLIP_STATE_DRIVER drives state / clip / target
  -> OUT_AGENT_CLIP_BRIDGE verifies the driver contract before agent wiring
  -> agent_clip_experiment probes Houdini node availability and clip attrs
```

## Walk Speed Tuning

`animation.yaml` may include an optional `behavior` block. Houdini uses this to
compute the walk frame range from distance and speed:

```yaml
behavior:
  fps: 24
  walk_speed: 1.2
  start_distance: 3.0
  align_frames: 16
  sit_down_frames: 40
  runtime_agent_count: 4
  runtime_seed: 7
  runtime_spawn_radius: 5.0
  seat_query_radius: 8.0
```

Formula:

```text
walk_frames = ceil(start_distance / walk_speed * fps)
```

If `behavior.walk_speed` is omitted, Houdini uses `animation.speed_houdini` or
`rootMotion.speed_houdini` when available, otherwise it converts
`animation.speed` with `rootMotion.unit_scale_to_houdini`. This is still a
preview calculation. The prototype cycles `walk.fbx` with a Time Shift SOP
through both Walk and Align, moves from approach to seat during Align, and keeps
Sit Down fixed at the seat. The next production step is Agent Clip plus Clip
Locomotion so the walk clip's real root motion drives the travel distance.

Maya Analyzer records rig-specific root motion as metadata while keeping
Behavior independent from rig names. For an mGear walk where
`Male:local_C0_ctl.translateZ = 6` over 24 frames at 24fps, the exported
`animation.yaml` should contain:

Root motion source resolution order:

```text
1. root argument passed to analyze_animation_publish(...)
2. Animation Properties rootMotionSource
3. Rig metadata: smartCrowdRole = root_motion
4. Currently selected transform
5. Last-resort name candidates such as local_C0_ctl, root, Hips, COG
```

For owned rigs, mark the reusable rig control once:

```python
from smartlib.dcc.maya import crowd_animation

# Select the root motion controller first.
crowd_animation.mark_root_motion_role()
```

For received rigs where metadata cannot be added reliably, store the source on
the Animation Properties node for that publish:

```python
from smartlib.dcc.maya import crowd_animation

# Select the root motion controller first.
crowd_animation.set_root_motion_source_from_selection(node)
```

```yaml
animation:
  fps: 24
  unit: cm
  startFrame: 1
  endFrame: 24
  durationFrames: 24
  durationSeconds: 1
  rootMotion:
    source: Male:local_C0_ctl
    source_namespace: Male
    source_node: local_C0_ctl
    axis: tz
    distance: 6
    distance_houdini: 0.06
    unit: cm
    unit_scale_to_houdini: 0.01
    speed_houdini: 0.06
  speed: 6
  speed_houdini: 0.06
```

Houdini locomotion speed priority:

```text
behavior.walk_speed
animation.speed_houdini
animation.rootMotion.speed_houdini
animation.speed * animation.rootMotion.unit_scale_to_houdini
default 1.2
```

Expected steps:

```text
Find Seat -> Walk -> Align -> Sit Down -> Sit Idle
```

When the goal succeeds, the selected interaction point is marked occupied in the Interaction System.
During runtime-style previews, a seat can be reserved before it is occupied:

```text
reserved_by = agent id while the agent is walking/aligning/sitting_down
occupied = true after the sit_down duration completes
```
