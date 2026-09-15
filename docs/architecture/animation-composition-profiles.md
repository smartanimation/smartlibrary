# Project Profiles and Animation Composition Snapshots

- Status: Accepted
- Decision date: 2026-09-06
- Scope: Config Creator, Animation Build/Publish, department Composition Snapshots

## Project configuration

Config Creator → **Project Profile** edits these fields in the existing
project `project_settings.yml`, preserving the other project settings:

```yaml
pipeline_profile: usd_animation
pipeline_profile_version: 1
```

| Profile | Required Animation deliverables | Composition entrypoint |
| --- | --- | --- |
| `usd_animation` | Final Deform USD, including shot sculpt | `shot.usda` |
| `alembic_cache` | Final Deform ABC | `shot_manifest.json` |
| `maya_rend_atom` | ATOM v3 with transfer manifest + applied REND Maya scene | `shot_render.ma` |

The USD contract uses evaluated final geometry. Skeleton animation and sculpt
deltas are not downstream requirements. Maya Source retains the editable work.
Product requirements are versioned code contracts, not independently editable
boolean flags. Unsupported profile names/versions fail validation.

Unconfigured projects retain the existing workflow. A profile change affects
future builds/publishes; an existing snapshot retains its original profile.
Asset quality profiles (FAST/WORK/REND) remain a separate configuration concept.

## User workflow

1. In Config Creator, choose the project's **Project Profile** and save.
2. In **Review Build Manager**, choose **Shot** scope, the shot, department
   (usually `anim`) and task used for the review build (for example `preComp`).
3. Check **Planned Snapshot**, run **Submit for Review**, and wait for success.
4. Open **Composition Snapshot** in the Build panel. Select a `READY` scene
   from the left **Construct Scenes** list. Only completed Submit for Review
   records are shown. Each row identifies the Build version, scene filename,
   Review version, delivery profile, task and submission time.
   The right **Published Snapshot** table is an editable draft with Use, Type,
   Name, Category, Context, Version and State columns. Construct Inputs is removed.
   Select **New from selected Construct** to build new animation products. Use
   enables/excludes each cast member; ATOM projects also select a fixed REND Rig
   version, which receives a fresh ATOM application. USD/ABC uses the selected
   Construct's final evaluated motion. AEPs and movies are not source scenes.
5. With department **animation**, click **Build & Publish Animation**. There is
   no animation file picker. The configured mayapy builds from the selected
   Construct, exports final USD/ABC or ATOM plus applied REND scene, then publishes.
   The operation appears in the shared **Job Queue** and waits for earlier jobs.
   Select its row to inspect the Maya executable, input scene, worker log and
   failure reason. A job completes only after the composition is published.
6. Select an existing Snapshot version to revise its composition. Version selects
   a compatible published member bundle (ATOM and applied REND stay together).
   **Publish Snapshot Revision** commits the draft as a new immutable version;
   existing versions and adopted downstream snapshots are untouched.
   **Open Shot** opens the selected published entrypoint, not unsaved edits.
   Drafts are retained per source/Snapshot while the window is open. State shows
   READY, CHANGED, EXCLUDED or MISSING; an empty composition cannot be published.
7. Switch to **effects** or **lighting** and use **Adopt Upstream Snapshot** to
   choose a fixed published version. Upstream updates never apply automatically.
   USD departments can create a new version with **Adopt Look Publish**.

**Planned Snapshot** describes the inputs before a Review Build; **Composition
Snapshot** records the resulting published animation inputs for downstream work.
Shot Manager's profile-based animation actions open this Review Build Manager tab.

The submission's `source_manifest.json` is the source of truth for the Construct
path; discovery uses the existing Review destination resolver. New submissions
record a Construct SHA-256. Selection pins the scene and Review/Build/validation
receipts, which are checked before Build, after Build and before Publish. Changed
scenes or failed/missing validation block publication. Older submissions without
an original checksum are identified in the UI and pin the current scene at
selection time; the original reviewed bytes cannot be verified retrospectively.
The final snapshot retains this provenance without requiring Workspace files to
remain present when downstream tools load it.

Each Cast instance is an independent target. `cast.json` entries may explicitly
set `animation_required: false` for static assets that do not produce animation
deliverables. All other entries are required unless explicitly excluded by Use;
exclusions are recorded in the new Snapshot without modifying cast.json.
Unexpected missing members still block publishing.
The cast definition is copied into the snapshot.

This composition is the animation input to the downstream department. Existing
layout, camera, FX and lighting authoring workflows continue to own their
respective content; this change does not export those departments' data from the
animation scene.

## Path ownership

Extend the existing `ProjectPaths`; do not introduce a second Path Resolver.
The configured Shot Build and Shot Publish roots are preserved. Logical layout:

```text
{shot_build}/anim/maya/animation/v###/
  source / per-instance generated files / build_manifest.json

{shot_publish}/
  animation/
    source/maya/v###/animation.ma (or .mb), manifest.json
    {instance}/deform/v###/deform.usdc (or .abc), manifest.json, validation.json
    {instance}/transfer/v###/animation.atom, animation_manifest.json, manifest.json, validation.json
    {instance}/rend/v###/rend_animation.ma, manifest.json, validation.json
    composition/v###/manifest.json, shot.usda (or shot_manifest.json / shot_render.ma)
  effects/composition/v###/...
  lighting/composition/v###/...
```

Only products required by the selected profile are generated. Source, each
instance/product, and each department composition have independent versions.
This restriction applies to the profile-based Animation Build operation.
The separate [Data-driven USD handoff](usd-handoff.md) can generate explicitly
selected pre-Submit products regardless of profile. It does not change existing
profile snapshots or imply Review approval.
Directories are reserved without overwriting existing versions. Failed work can
leave uncommitted version gaps; it never replaces an existing snapshot.

All path construction and Manifest-relative source resolution use ProjectPaths.
New helper methods are `animation_artifact_dir`, `composition_dir`,
`animation_build_dir`, `artifact_file`, `manifest_source`,
`project_dependency`, and `dependency_files`.

## Build and Publish contracts

Workspace Build Manifest schema: `smartpipeline.animation_build.v1`.

```json
{
  "schema": "smartpipeline.animation_build.v1",
  "pipeline_profile": "usd_animation",
  "pipeline_profile_version": 1,
  "shot": {"episode": "ep02", "sequence": "s027", "shot": "c003"},
  "frame_range": [1001, 1100],
  "fps": 24,
  "source_workfile": "source.ma",
  "source_sha256": "<source checksum>",
  "dependencies": {},
  "members": [{
    "instance_id": "Hero_01",
    "asset": "Hero",
    "variant": "default",
    "products": {
      "deform": {
        "source": "Hero_01/deform.usdc",
        "sha256": "<deform checksum>",
        "evaluation": "final_deform",
        "frame_range": [1001, 1100],
        "topology_signature": "<topology checksum>",
        "validation": {
          "ok": true,
          "source_sha256": "<deform checksum>",
          "validator": "maya_final_deform.v1"
        }
      }
    }
  }]
}
```

Source paths can be absolute or relative to the Build Manifest. Dependency paths
must identify existing, versioned Production files. Workspace source paths in
published metadata are provenance, not the runtime entrypoint. Source scenes
are archived; final deliverables are copied and their checksums verified.

Snapshot schema: `smartpipeline.animation_composition.v1`. It records its
profile contract, cast, frame range/FPS, archived source, per-instance products,
fixed dependencies, Look selections, and the fixed upstream snapshot.
References contain paths and SHA-256 hashes. Readers reject missing or modified
published dependencies rather than substituting a newer version.

Shot entrypoints are generated in Workspace before promotion. A final atomic
rename of `manifest.pending.json` to `manifest.json` commits the composition.
Consumers discover only completed manifests; there is no mutable `latest`
reference inside an adopted snapshot.

For ATOM, `animation_manifest.json` preserves the existing v3 transfer contract,
namespace mapping and static values. A REND receipt must identify the exact
ATOM checksum that was applied. REND scenes retain fixed dependency provenance.

## Look adoption

For USD snapshots, Effects/Lighting can use **Adopt Look Publish**. This creates a
new department version without changing Animation geometry or motion.

The selected Look Publish Manifest supplies:

```json
{
  "path": "look.usda",
  "prim_path": "/Look",
  "topology_signature": "<matching Final Deform topology checksum>",
  "variants": {"look": "wet"}
}
```

The Look root maps to `/Shot/{instance}`; its children must match the exported
geometry hierarchy. The Look layer is restricted to shading/binding opinions
and material subsets. Geometry, transforms, activation changes, payloads and
references are rejected, including opinions in unselected variants. Texture
dependencies must resolve to fixed Production versions. Look version, variant
selection and snapshot history are recorded independently.

USD Look adoption is implemented here. Native Maya/Alembic shading workflows
continue to use their DCC tools; their Look data is not interpreted as USD.
Model/topology/skin changes require a new Animation Build and Deform publish.

## Review / PreComp

The accepted [Review Artifact Lifecycle](review-artifact-lifecycle.md) remains the
authority. PreComp is the AEP handoff artifact under `publish/precomp`.
Animation Build paths use the task name `animation`; they do not reuse PreComp.
Render Manifest is not used as an Animation Composition Manifest.

## Runtime and verification

Maya builds require mayapy with the existing Maya USD / Alembic / ATOM plugins.
The Config Creator and ordinary manifest operations use the existing Python
environment. Standalone USD publication/Look adoption additionally needs the
`usd` extra (`usd-core`). Maya uses the USD runtime shipped with Maya USD.

- Automated contract/integration coverage: `tests/test_animation_composition.py`.
- Actual Maya roundtrip: from the repository root, run
  `mayapy tests/maya_animation_profile_smoke.py`.
- Maya fixtures/reports are written only under `.tmp/animation-profile-maya-*`.
- The Maya test compares skeleton + sculpt results against USD/ABC world-space
  vertex positions, and checks applied REND animation values and shot timing.

The synthetic Maya test verifies the implemented contracts. Production rigs,
custom deformers, Look hierarchy conventions and renderer plugins still require
project-specific acceptance checks.

### REND construction and ATOM destination matching

The REND adapter uses the shared Shot builder to reference and group each fixed
REND publish with the project's namespace, reference-group and timing rules.
ATOM attribute matching retains the complete namespace and rig-relative DAG
hierarchy while ignoring outer Shot groups. Only a unique matching destination
is accepted; ambiguous or structurally different targets still fail validation.
Per-attribute application failures are retained in the worker result and Queue
error details.

REND deliverables retain fixed-version Asset References and ATOM animation edits;
they never import references merely to save a standalone rig. Dependency paths
and hashes remain pinned. The Build receipt records nested reference depth,
namespace, reference-node name, file type and path. Shot ASCII entrypoints emit
Maya reference-depth declarations so nested Assets load on normal reopen.
Unknown binary data stays in the referenced Asset; it is not deleted or forced
into the ASCII REND scene. Tests reopen both the REND scene and composed Shot.


### Snapshot confirmation review

After Build Publish Animation or Publish Snapshot Revision, Review Build Manager
queues a separate Snapshot Internal Review job. Create Internal Review reruns this
step for the selected published Snapshot without publishing animation again.
The project-configured Maya runtime and the existing internal Review movie/PDF
pipeline are used. Failures are shown in Job Queue and do not undo a successful
Animation Publish.

The exact submitted Construct is reopened to retain cameras and Review setup.
Its animation cast references are replaced by the selected published members;
excluded members are removed. Render caches are bypassed for this verification.
The source manifest records the Snapshot path, version and SHA-256; the report
lists published animation versions. Verification Constructs are excluded from
the Animation source chooser to avoid publishing a downstream result again.

Movies, reports and review receipts follow the existing internal delivery
resolver. They do not mark the Snapshot or source Review as approved.

The Review Report lists the complete recorded composition inventory across pages,
including excluded inputs, source-only ANIM inputs, applied products, pinned
Asset/texture dependencies, Review Layers, Render Manifest and PreComp. Each
entry retains its role, state, version, path and available SHA-256. The same
inventory is stored as report_inputs in the submission source manifest.


Review PDFs omit filesystem paths and SHA-256; these remain in source_manifest.json.
Snapshot verification bakes the recorded Review Layer cameras into the workspace
Construct before replacing animation references. The overlay uses the rendering
layer camera instead of independently selecting the Primary camera.

Each Snapshot review submission includes original.mov, applied.mov and
comparison_50.mov. These use clean images; comparison_50 is an equal blend with
the original audio. FPS, resolution and frame count must match. Source movie
paths and hashes, artifact hashes and blend settings are recorded in JSON.

The PDF is a compact summary: thumbnail, Overview, Review Layer Camera and
main Resolved Inputs (type, name, context, version). Full dependency inventory,
excluded inputs, paths and checksums remain in JSON. Additional pages are only
used when the number of main inputs exceeds one page.
