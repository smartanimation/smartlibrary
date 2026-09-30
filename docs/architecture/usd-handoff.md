# Data-driven USD handoff

## Review Camera Batch consumption

Review Build resolves camera snapshots from the newest completed composition's
fixed Camera products. Independently published `latest.json` files are not mixed
into that batch. A superseded legacy Camera Package in saved Construct is not
reintroduced alongside these cameras. Legacy package selection remains available
when the composition has no Camera snapshot products.

Primary is restored once from its native publish. Each derived camera's pinned
native dependency graph is evaluated in a temporary namespace and world-baked
under its published target name, then the temporary graph is removed. This retains
the published transform/lens result without additional Primary cameras. Static
cameras remain constant. No production Publish artifacts are rewritten by Build.

- Status: Implemented baseline; production-rig acceptance remains project-specific.
- Decision date: 2026-09-14

USD Publish is a profile-independent, pre-Submit handoff. It does not change the
formal Review/PreComp lifecycle or imply review approval. Shot Manager Publish
embeds the Animation USD editor directly in **Publish > Animation**. Cast selection
drives the Source Versions panel (current Maya scene, selected Asset Context Rig,
optional Shot Sculpt). Publish first fixes a saved-scene snapshot. Its independent
Worker exports Animation Curves to a new Data Version using the same exporter as
the Data tab, then pins that Data for the isolated USD rebuild. There is no Curve
input selector or output checkbox.
Output Settings supports Shot/Custom frame ranges; the current contract is USD,
integer-frame Step 1, with namespaces retained. Unsupported Alembic and namespace
stripping controls are disabled. Shot Manager has no Open USD action.
Camera has an inline editor listing the Primary and enabled Smart Camera Playblast
output cameras from the saved scene. Each camera has independent versions. Selecting
Primary publishes all used cameras together; selecting only smartCams updates those
cameras while retaining the base's Primary and other cameras. Native Maya and FBX
outputs remain available. Only after the complete batch succeeds are fixed USD
versions referenced by `camera.usda` in a new `shot.usda` composition.
The Base Composition selector initially shows the newest completed version and
pins the selected version at Publish. Its non-camera products are retained. A Primary
batch replaces the complete Camera membership; a derived-only batch replaces its
selected targets. Choosing None requires a Primary batch and creates a camera-only snapshot.
Timing/units must match the base; failure does not update a composition. Existing
versions are never overwritten and other products are not re-exported.
Assets has an inline Cast registration table (Include, Asset/Variant, Geometry
Source, fixed common Asset USD Version, Payload/Metadata only, State). It replaces
the complete Assets membership in a new snapshot while retaining the selected
base's Camera, Animation and Layout products. Unchecked entries are removed only
from Assets; an existing Layout targeting a removed prim fails validation.
Characters default to Animation USD as geometry provider; their Asset identities
are metadata only with no mesh composition arc. Missing character Asset USD is
allowed for identity-only registration. Static backgrounds/props use Asset USD
payloads and require a completed common entry resolved by AssetPublishResolver.
Common-entry dependencies are frozen and rechecked by the worker. Asset geometry
is not re-exported. A target cannot have both Asset and Animation geometry, even
when opening with LoadAll. This validation also applies to Smart Composition.
Department Load Defaults and Proxy/Render working-set selection belong exclusively
to Smart Composition; Shot Manager does not author either policy. Source Asset
quality variants remain available, and the Asset's own default is inherited.
Character lighting Look binding is not introduced by this registration feature.
Placement and Set Dress use the Layout batch workflow described below. No Submit-triggered
cache generation is introduced. This capture action is available inside Maya only.
Completed Curve Data remains available even if the subsequent USD build fails.

## Fixed inputs

- Animation: newly captured ATOM v3 Data and an explicitly selected, published
  Asset Context Rig (ANIM is preferred in the UI). Source Rig provenance remains
  separate from the rebuild Rig. The resolver validates the selected cast/context
  version; the worker validates channel mappings and baked samples. Arbitrary
  different rigs are not guaranteed equivalent. The service's existing Data API
  still requires the source Rig when no explicit context is supplied.
- Optional Shot Sculpt: explicit version, or `null`. Absence is normal. A selected
  missing/incompatible version is an error. Later publishes are not auto-adopted.
- Assets: fixed common Asset USD with Proxy available, composed via payload rather than re-exported.
  The existing Maya Proxy Publish requirement for validated Maya + USD is retained.
- Camera: independently versioned Primary / derived USD, each containing one camera.
  Exactly one Primary is required whenever Camera products are composed. Derived
  products pin their source Primary camera.json and its evaluated transform/lens
  fingerprint. Mismatched Primary versions are rejected, including Compose Existing
  Products and Smart Composition previews. Resolution, reference resolution and
  per-layer rules/ranges remain in camera settings metadata.
- Layout: fixed Smart Set Dress JSON and explicit recorded-node to USD-prim mapping.

Selection pins checksums. The worker rechecks inputs before and after generation.
Reference paths and output directories use the existing ProjectPaths resolver.
Maya work runs in a separate mayapy process using the configured runtime. Workers
load and validate the selected project Maya registration's `core` and
`work_stage` plugin profiles before reading a Rig, using the same loader as
Review Build. A missing required plugin aborts the build.
Final-deform export uses explicit geometry export roots in world space so ancestor
joints cannot prune meshes when skeletal export is disabled. All exported groups
are enclosed by the default prim `/Geometry`, preserving multi-root references.
The source Rig hierarchy is not reparented or edited for export.
Cache generation runs in Workspace; verified files are copied into reserved Publish
versions. A completed manifest makes a product/snapshot discoverable. Failed
jobs may leave uncommitted version gaps; they never overwrite old publishes.

## Animation

Shot Manager's Animation Cast list supports Ctrl/Shift multi-selection. A batch
has a shared saved-scene snapshot and frame range, with separate Rig Context,
Rig Version and optional Sculpt choices for every target. All Curve Data is
captured before any rebuild opens another scene. Rebuilds run sequentially in
one job; only success of every selected target commits a new Animation Section
and Composition. A failed batch can leave completed Data and uncommitted USD
files/version gaps, but does not replace the prior Composition.

Animation Publish uses an explicit merge policy: after the execution lease is
acquired, the Worker resolves the newest completed Composition for that shot,
pins it, and records it in the job's `resolved_inputs.json`. It replaces only
selected Animation targets and retains other characters, Assets, Camera and
Layout. Range/FPS/unit mismatch fails rather than dropping existing products.
This execution-time base selection is intentional to retain earlier queued
publishes; generated Sections/Compositions still use immutable fixed references.
Historical characters already omitted from an older broken snapshot are not
implicitly resurrected. Use Compose Existing or publish the desired selection.

Animation Data export bakes constraint-driven transfer channels. Shot-authored
constraint targets outside the controller set are included, while referenced
rig-internal driven joints are not indiscriminately exported. The baseline uses
full Publish-range integer-frame sampling (1.0 frame), preserving outside keys.
Constraint switch boundaries inside the range are included. Subframe accuracy
and minimal-interval baking are not claimed by this version.

Bake samples are compared against the source evaluation. Export is undo-scoped;
the original constraint graph remains intact. The Data manifest records Rig
hashes and evaluated constrained-channel samples. The isolated rebuild checks
these samples after ATOM apply, then exports final-deform USD from cache_geo_set.
Synthetic Maya tests are not proof for every production rig/deformer.

## Optional Sculpt Data

`smartpipeline.shot_sculpt.v1` represents **post-deform object-space point deltas**,
not blendShape controller animation. It pins the Curve Data manifest hash,
frame range, mesh prim paths and topology signatures. Each integer frame has
explicit point deltas. Deltas are combined with the rebuilt final-deform USD.
No matching data is treated as `null`, not an incomplete shot.
The current-scene action creates a new Curve Data version. Existing Sculpt Data
bound to an older Curve manifest is not automatically rebound to this new version;
selecting it fails the strict compatibility check. Cross-version Sculpt reuse is
not implemented by this change, even when the curves appear visually unchanged.

The initial capture action selects an unsculpted Animation USD product manifest
generated from the same Curve Data and a sculpted USD with matching hierarchy/
transforms, then versions only their point corrections as Data.
It is not a Maya sculpt-authoring tool or a Maya blendShape reconstruction adapter.

## Composition

Section layers now have independent versions under
`publish/usd/sections/{animation|assets|camera|layout}/v###/`.
Each directory contains its named `.usda` and `manifest.json`, which pins the
member product receipts, dependencies, timing and units. Existing per-target
product directories remain unchanged. The common `usd_section_dir` resolver owns
these paths; there is no application-local path expansion.

New `publish/usd/composition/v###/` directories contain only `shot.usda` and
`manifest.json`. The entrypoint sublayers fixed Section versions, never latest.
The manifest's `sections` pins Section receipts; `layers` retains resolved
layer-file references for existing readers, not copies inside the Composition.
Empty sections are omitted. The complete selection is validated before new
Sections are allocated; identical fixed member selections/timing reuse existing
versions. Layout also compares the resulting sparse opinions, since a changed
Asset transform stack can affect their values.

Smart Composition selects Section versions when opening this new format. Legacy
snapshots still open through the product selector and are not rewritten. Saving
a new composition from either format produces the independent-Section format.
Per-target editing APIs remain available and assemble the corresponding Sections.
Only changed Sections are exported; unchanged ones keep their original paths.

- `shot.usda`: composition entrypoint with timing/units.
- `animation.usda`: per-character final-deform references.
- `assets.usda`: fixed Asset payloads and metadata-only Cast registrations at stable instance paths.
- `layout.usda`: transform/visibility overrides on those instances/children.
- `camera.usda`: one Primary and the selected derived Camera references.

Set Dress JSON stays authoritative. Its supported transform/visibility edits are
converted to USD opinions; arbitrary Maya rig attributes fail rather than being
silently ignored. Original Asset USD files are never edited or flattened. Mapping
uses explicit node IDs because Maya UUID/UFE paths are not automatically portable
to Shot USD prim paths. Overrides inside instanceable proxies are rejected.

Each version contains the complete selected reference/placement state relative
to Asset publishes, not a chain of incremental patches against old layout versions.
Addition/removal is expressed by including/excluding instances in the selection.
The existing recorder does not record arbitrary object creation/deletion events.

**Compose Existing Products** creates a new snapshot from chosen product versions
without running an animation export. Duplicate target versions, multiple Primary
cameras and incompatible derived-camera provenance are rejected. Snapshots explicitly list their included targets and are
marked partial/not-reviewed; no whole-shot completeness is inferred.

## Publish job management

Shot Manager copies a saved Maya scene into the Build area, then registers a
detached Publish job. Curve Data or native Primary Camera publication runs in
the Worker. Shot Manager does not own the background process or execute
post-bake composition callbacks.
Review Build Manager's Job Queue displays Animation USD, Camera Batch USD, Layout USD, legacy Primary Camera USD and Assets USD
jobs alongside existing Build jobs; a per-project execution lease prevents their
workers running concurrently. Publish remains separate from Review submission.

The request freezes input versions/checksums, the chosen Base Composition, Maya
executable/environment and plugin profile configuration at registration. The
worker checks the immutable request, loads required project plugins, performs USD
generation/validation and creates composition before returning success. Progress,
request, result and worker log files use the existing resolved USD handoff build
directory. Missing plugins, changed inputs, launch errors and abnormal exits are
reported as failures; the queue continues with the next job.

Publish submission now uses detached supervisors rather than a Maya-owned
QProcess. Animation and Camera require a saved scene; a modified scene prompts
Save and Submit / Cancel, and an unnamed scene must first be saved. The scene is
copied into the resolved USD handoff build directory and checksummed. Maya-listed
file dependencies are recorded and checked before/after evaluation. These files
are not copied: changing one causes failure, rather than silently changing the
input. Files accessed only through custom plug-in/script logic may require
additional project-specific dependency collectors.

The Worker opens the fixed scene with its recorded workspace. Animation captures
new Curve Data there and then rebuilds from that Data plus the selected Rig.
Camera captures its native publish there before portable USD generation. Assets
registration remains scene-independent unless Smart Maker placements are explicitly
enabled. That option uses the same saved-scene worker to capture STATIC/CURVE
attachment-root matrices; deforming Props remain Animation USD providers and
never receive a second Assets placement. See `docs/prop-usd-workflow.md`.
Closing Maya is safe after the independent runner's
Accepted receipt; before that acknowledgement, the UI explicitly asks the user to
wait. The detached supervisor owns the Worker, log, exit handling and final
validation. Host job objects that prohibit Windows breakaway cause launch failure
instead of an unsupported continuation promise.

Project-wide receipts live at the common resolver's
`publish_job_registry_dir()` (Workspace/jobs/publish). Review Build Manager
reconnects to these receipts after restart, including completed/failed jobs.
An OS-owned project lease serializes local Publish runners and legacy Review Build
execution; it is released on process exit. Supervisors launch jobs in registration
order. Execution is local-host only, not a cross-machine farm scheduler. No
automatic retry/resume after reboot or runner failure is implemented.

## Verification details

- `tests/test_shot_usd_batch.py`: independent camera versions, Primary replacement,
  provenance rejection, grouped Layout, layer priority, reuse and Data discovery.
- `tests/maya_shot_usd_batch_smoke.py`: Maya 2024 synthetic Primary/CHA/BGA publication,
  CHA-only resolution update, stale Primary rejection, full Primary refresh, CURVE
  Placement and multiple Set Dress layers through USD composition and the worker.

- `tests/test_detached_publish.py`: save gate, dependency pinning, OS lease and
  receipt reconnection without repeated completion events.
- `tests/test_animation_batch.py`: multi-character retention, per-character UI
  settings, single scene capture and no Composition/Section commit on failure.
- `tests/maya_detached_publish_smoke.py` and `tests/maya_detached_animation_smoke.py`:
  saved-scene Camera/Animation submission followed by parent Maya exit; outputs
  remain under `.tmp/`.
- `tests/test_assets_publish.py`: Cast discovery, payload load/unload, metadata-only
  registration, duplicate providers, membership replacement and inline queue UI.
- `tests/maya_assets_publish_smoke.py`: real Maya 2024 Assets worker/queue and USD
  LoadNone/LoadAll validation; synthetic output only under `.tmp/`.
- `tests/test_usd_sections.py`: independent section versions, reuse, old snapshot
  compatibility, fixed-section selection, corruption and validation failures.

- `tests/test_usd_handoff.py`: real USD composition, sparse layout, provenance,
  optional Sculpt, re-composition, and basic dialog construction.
- `tests/maya_usd_handoff_smoke.py`: Maya 2024 synthetic constraint/ATOM/rig/USD
  roundtrip and isolated handoff service; output only under `.tmp/`.
# Static Primary Camera

Camera Publish Output Format supports `Static` in addition to Shot / Custom Range.
Static Frame defaults to shot start and may be selected explicitly. The saved-scene
worker samples world transform and lens settings once, freezes the native Maya
camera, and exports constant FBX / USD cameras (USD has no time samples). The shot
range remains unchanged for composition compatibility.

`camera_motion` records `mode: static`, `animation_required: false`, and
`sample_frame` in camera.json, publish.json, USD layer customLayerData, the Camera
prim customData, and product / section input metadata. Render consumers can inspect
this explicit intent even after composition. Animated publications use
`mode: animated`, `animation_required: true`; missing legacy metadata does not
mean static. Static does not infer that other shot content is unanimated.

## Shot Layout batches

Publish > Placements lists Smart Maker marker locators. Publish > Set Dress lists
scene-embedded Smart Set Dress shot layers, including their mute state. Both allow
multiple selection. The Smart Maker Publish Placement button is removed; editing,
assignment and Export Metadata remain available there.

The detached worker captures selected Data versions from the fixed saved scene,
then builds Layout from that Data. Placement Data retains the existing locator and
member records and adds evaluated marker / attachment-root matrix samples. STATIC
samples shot start; CURVE samples every integer frame. Set Dress is static-only:
recorded overrides never receive time samples; motion
belongs to Marker CURVE. Layout Output is read-only and follows the selected
Marker settings (Static, Shot Range, or Mixed per Marker). Set Dress always shows
Static without a range selector. Composition timing remains the shot range for
compatibility even when all opinions are static. Set Dress Data preserves
the existing SetDressPackage schema and one-layer export convention. Fixed versions
are also visible in the Data tab. Explicit node-to-USD-prim selections in the Publish
panel avoid assuming Maya UUIDs or namespaces identify USD prims. Top Set Dress
layers remain strongest; muted layers produce no opinions.

Layout products are versioned by locator/layer. The Layout Section groups their
opinions into `placement.usda` and `setdress.usda`, referencing member files such as
`placement/chair_place_loc.usd` and `setdress/chair.usd`. Set Dress is stronger than
Placement. Source Asset layers are never modified. The Section freezes all member
files, and unchanged Sections are reused. Composition is committed only after the
complete batch validates; captured Data can remain available after Build failure.

Placement requires a single registered Asset geometry root and cannot target
Animation-owned geometry or duplicate another placement.
Assembly replacements within that root are identified by parent Cast and Assembly
instance ID. Placement Data freezes evaluated geometry transforms, world vertices and their
export correspondence. Layout resolves the existing public group in the selected
background USD and authors its world transform only when all mapped geometry has
the same rigid delta. Missing IDs, ambiguous mappings, deformation and overlapping
parent/child placements fail; the Asset hierarchy and source layers stay unchanged.
Legacy Assets with embedded
Smart Maker transforms must first be republished with that option disabled; Layout
rejects mixing both owners. Set Dress retains sparse transform/visibility support;
unsupported attributes, incompatible transform stacks and instance proxies fail
explicitly. Existing compositions and Data are never migrated or overwritten.

## Set Dress mapping assistance

Static Asset USD export through `export_proxy_usd` embeds correspondence on exported
transform prims: `smartpipeline:setdress:sourceId` and `sourcePath`. IDs use an
existing `smartSetDressId` string attribute when present, otherwise the source Maya
UUID. Paths record the namespace-independent source DAG hierarchy. The exporter
records only exact exported paths, including its known flattened joint-hierarchy
mapping; omitted groups are not guessed. No preparation is required for ordinary
new publishes. An optional persistent `smartSetDressId` can be authored on selected
source groups when IDs must survive deliberate node replacement.

Publish > Set Dress first scopes matching to the Cast instance's namespace, then
fills unique exported correspondence matches. Multiple copies of one Asset remain
distinct. Existing Asset USD without correspondence gets ranked name/hierarchy
candidates, which require manual selection. The table displays Asset mapping,
Saved mapping, candidate, or unresolved status. Instance proxies and non-transform
prims are excluded.

`Save Mapping in Scene` stores confirmed choices in the Smart Set Dress network
node, scoped to the shot. Save the Maya scene to retain them after reopening.
Publish also stores its choices before the normal Save and Submit gate. Saved
choices retain Asset identity, source USD version path and source ID; they are
reused only when the prim and provenance still match. Changed versions can be
re-resolved from exported correspondence, otherwise the prior path is offered
for confirmation. Clearing a row and saving removes its stored choice.

Reference instances can share Maya UUIDs. New Set Dress recordings therefore
qualify referenced node IDs with the reference-node UUID. Resolution requires
that same reference identity. Legacy layers with one UUID assigned to different
nodes are rejected instead of merged; re-record those layers to disambiguate them.

Verified by `test_setdress_mapping.py` (matching, version changes, UI persistence)
and `maya_setdress_mapping_smoke.py` (real Maya export, duplicate Asset references,
and scene save/reopen). All synthetic artifacts remain under `.tmp/`.
