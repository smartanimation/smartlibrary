# Static skeletal layer separation

Rig USD Publish emits three files within the resolved Rig version directory:

- `geo.usd`: mesh points, topology, UVs and geometry hierarchy/transforms from the
  skeletal export's basis geometry. It has no skeleton, blendshape or skin opinions.
- `rig.usd`: Skeleton, binding/weight primvars and BlendShape definitions from the
  input export. Mesh targets are sparse overs, without points, topology or UVs.
- `usdSkel.usd`: relative sublayers combining rig and geometry, the published USD
  entrypoint used by downstream consumers. `rig.usd` alone is not a visible asset.

Receipts use `smartpipeline.usd_skel.v2` and record the geometry, rig and entry files.
Older receipts remain readable. The existing evaluated Geometry Publish is not
silently substituted for bind geometry. Separating independent model version
ownership requires validating that model against the binding basis first.

The splitter rejects time-sampled inputs and removes SkelAnimation opinions and
animationSource connections; Shot animation is authored separately. BlendShape
definitions already present in a source are preserved. It does not generate missing
blendshape targets or repair an incomplete Maya USD export.

DLI's geometry sits below an unbound driver joint. The exporter temporarily moves
those geometry branches to their common transform ancestor, then restores their
parents, local matrices, selection and scene modified flag in a finally block.
The source scene is not saved. The post-export validation also checks actual mesh
points/topology: a Mesh prim with skin properties alone is insufficient.

## DLI / c001 verification (2026-10-01)

The rig basis was read from DLI Context anim v004, the exact reference used by
`ELCD_ep02_s027_c001_preComp_v001_t08.ma`. Its original Work Rig is t05.
The Shot's production range is **278–411**, not its saved playback range 1–120.
All 134 integer frames and all vertices of 66 meshes were compared in world space
between Maya and USD BakeSkinning. Maximum error was **0.001083 cm**, below the
0.01 cm verification tolerance. The bound Skeleton contains 85 joints.

The fixed verification outputs are Rig anim v001 (`geo.usd`, `rig.usd`,
`usdSkel.usd`), USD Animation DLI_main v010 (`animation.usd`,
`animation_asset.usda`, `validation.json`) and USD Composition v022. Composition
retains v019's other products and uses Preview Look low v006. All production
destinations were obtained from ProjectPaths. The Work Scene path/hash is
provenance; Composition pins the published verification receipt and USD layers.
Rig latest is intentionally not promoted by this scoped verification publish.

The end-to-end check found that Preview Look composition authored an explicit
`apiSchemas` list which masked the referenced `SkelBindingAPI`. Look merging now
prepends its material API while preserving weaker APIs. Look-bearing section
definitions carry merge version 2 to prevent reuse of previously built sections.
The saved v022 composition was baked and compared against the verified animation
at all 134 frames; world-space vertex differences were zero.

**Limitation:** Maya's grouped facial BlendShape export remains unsupported.
The three facial weights are zero across all 134 frames of this source, so this
verification establishes joint-driven playback only. It is not evidence that
other shots with expressions, other deformers, subframes or other rigs reproduce.
Such outputs still need deformation comparison and the agreed deform-cache
fallback.

## Manager UI

- Asset Manager → Publish → Rig: the inline **Publish static Rig USD** button
  exports the current Maya scene (including unsaved edits) as the three separated
  layers. The source must exist in the selected Asset/Variant Work area, in any
  department. The normal Maya Rig publish remains available separately.
- Preview → **Static Rig USD** creates a disposable check without allocating a
  production version. Use the Rig panel's `anim` subset for skeletal packages.
- Shot Manager → Publish → Animation: select the existing Maya Rig Context used
  to rebuild Curve Data, and a separate **Static Rig USD** version. Single-cast
  and batch selections both pin the selected receipt and every USD dependency.
  Selecting **None — deform cache** retains the existing cache workflow.
- Publish still captures fixed Scene/Data inputs and runs in the existing isolated
  queue. It evaluates a deform cache, exports/rebases the joint animation, then
  compares topology, UV basis, visibility and world-space vertices at every integer
  frame (0.01 cm tolerance). Matching results publish `animation.usd` plus an
  `animation_asset.usda` referencing the fixed static Rig. The comparison cache is
  a build artifact, not a second published geometry provider.
- Unavailable/unsupported skeletal export or a deformation mismatch publishes
  `deform.usdc` instead. The reason is recorded in the product validation, worker
  log and Smart Composition's reference details. Explicit Shot Sculpt also uses
  the existing cache path. Failure to build the underlying Data/deform result
  remains an error; it cannot be replaced with an unverified result.
- Compatible Look selection from the preceding Composition is retained; an
  incompatible Look is omitted with a recorded warning. **Open Smart Composition**
  opens the Shot's latest completed composition.

UI worker verification with DLI's fixed Curve Data: 66 meshes / 134 frames,
maximum error 0.001083 cm. An intentionally shifted static Rig correctly chose
deform fallback at frame 278 (10 cm difference). Static Rig preview also ran from
the original Work Rig without changing its file hash.

## Bounds for skeletal animation

Skeletal animation and composed Animation layers author mesh and SkelRoot `extent`
samples from evaluated skinning at every integer frame in the publish range.
Only bounds are written; geometry points remain in the static asset. Bind-pose
bounds can otherwise cause Hydra to cull visible animated meshes (DLI head at
c001 frame 364). Smart Composition rebuilds these bounds in its disposable preview
for older products too. Saving creates a new section/composition; existing receipts
and USD files remain immutable. Animation section signatures include the bounds
policy version so older cached sections are not reused.
