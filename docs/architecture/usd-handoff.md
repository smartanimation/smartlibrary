# Data-driven USD handoff

- Status: Implemented baseline; production-rig acceptance remains project-specific.
- Decision date: 2026-09-14

USD Publish is a profile-independent, pre-Submit handoff. It does not change the
formal Review/PreComp lifecycle or imply review approval. Shot Manager Publish
embeds the Animation USD editor directly in **Publish > Animation**. Cast selection
drives the Source Versions panel (current Maya scene, selected Asset Context Rig,
optional Shot Sculpt). Publish first exports the current scene's Animation Curves
to a new Data Version using the same exporter as the Data tab, then pins that Data
for the isolated USD rebuild. There is no Curve input selector or output checkbox.
Output Settings supports Shot/Custom frame ranges; the current contract is USD,
integer-frame Step 1, with namespaces retained. Unsupported Alembic and namespace
stripping controls are disabled. Shot Manager has no Open USD action.
Other publishing categories retain their existing workflows. No Submit-triggered
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
- Assets: fixed Proxy USD from Asset Publish, referenced rather than re-exported.
  The existing Maya Proxy Publish requirement for validated Maya + USD is retained.
- Camera: explicitly selected versioned Primary USD containing one camera.
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

- `shot.usda`: composition entrypoint with timing/units.
- `animation.usda`: per-character final-deform references.
- `assets.usda`: fixed Asset references at stable instance paths.
- `layout.usda`: transform/visibility overrides on those instances/children.
- `camera.usda`: one selected Primary reference.

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
without running an animation export. Duplicate target versions or multiple Primary
cameras are rejected. Snapshots explicitly list their included targets and are
marked partial/not-reviewed; no whole-shot completeness is inferred.

## Verification

- `tests/test_usd_handoff.py`: real USD composition, sparse layout, provenance,
  optional Sculpt, re-composition, and basic dialog construction.
- `tests/maya_usd_handoff_smoke.py`: Maya 2024 synthetic constraint/ATOM/rig/USD
  roundtrip and isolated handoff service; output only under `.tmp/`.
