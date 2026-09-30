# Prop USD workflow

Publish remains format-independent: Maya and USD are both supported.

## Asset Manager

- Prop `LO`: Release & Pack writes Maya binary and static geometry USD. The
  Context directory remains `lo`; the shared entry's USD quality is `proxy`.
- Prop `REND`: the same route publishes render geometry; Context remains
  `rend` and USD quality is `render`.
- Prop `ANIM`: retains the rig/Context workflow. Deformed geometry is published
  through Shot Manager Animation, not baked into a static Asset release.
- Environment supports `PROXY`, `REND`, and legacy `RENDER` Context names.

The existing common Path Resolver supplies all publish/entry locations. Old
versions are never renamed. The common entry retains both qualities and variants.
`cache_geo_set` supplies polygon geometry for static releases; no skeleton is
required. Save the source as `.mb` before Release & Pack.

## Shot Manager

In Assets, choose **Asset USD** for static or transform-only Props. Select the
fixed common entry version. Both LO-only and REND-only entries can be registered;
registration does not author a quality/load policy for Smart Composition.

For placements, enable **Use Smart Maker placements (STATIC / CURVE) from saved
scene**. This is opt-in to preserve existing scene-independent registrations.
Each selected Asset USD target requires exactly one Smart Maker placement assigned
to its Cast key, attached to the **whole asset root corresponding to its USD
defaultPrim**, not an internal controller or mesh group.

- `STATIC` samples that attachment root's evaluated world matrix at shot start.
- `CURVE` samples the evaluated world matrix every frame (including both ends).
- Parent hierarchy and constraints are included by world-space evaluation.
- The sampled root stack replaces the referenced root stack to avoid applying
  the Asset root transform twice. Internal part animation/deformation is not
  captured: use Animation USD for that case.

The saved scene is copied and checked by the existing detached Publish worker.
The resulting Assets product freezes matrices, motion mode, and source scene
name/checksum. Later scene edits do not change that product. FPS/units/up-axis,
missing attachment roots, and duplicate placement assignments are validated.
Subframe sampling and motion-blur shutter samples are not included.

For deforming Props, publish the final deformation from **Animation**, using the
published rig Context and the existing Animation Data contract. In Assets select
**Animation USD** for that Cast key. Its Assets row is registration only: neither
an extra geometry payload nor a second placement transform is applied.

## Verification

- `test_prop_usd.py`: LO/REND mapping, immutable entries, retry, resolver discovery.
- `test_assets_publish.py`: unloaded payload placement, reconstruction and provider
  duplication prevention.
- `maya_prop_placement_smoke.py`: real Maya geometry export and constrained
  STATIC/CURVE world transforms, using only synthetic files under `.tmp`.
