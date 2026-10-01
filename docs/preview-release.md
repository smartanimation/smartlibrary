# Published Geometry + Look Release

Asset Manager's independent USD Release tab provides a Quality sidebar, Geometry/Look/Rig
input table (subset, selected version, latest, state), summary, expandable Release
history, and copy/open controls for the selected Release and common entrypoint paths.
Currently proxy is enabled; render is a disabled future option. Quality is independent of Maya
work Context: low inputs may be released as proxy and high inputs as render.
Both currently use Preview Look, not final renderer shading. The operation runs
without Maya. History lists proxy releases and their fixed input versions. Selecting
a history row or its child displays that Release's paths without changing input choices.
Check in usdview validates the selected published inputs and builds a disposable
composition outside Production; it does not allocate a Release version. Release USD
validates again before writing. Selection alone does not claim compatibility validation.

For characters, render means a separate high mesh, not subdivision of the low mesh.
Maya ANIM work may continue using low. This Release currently composes static
Geometry and Look, optionally with a static Rig; driving the high mesh with Shot animation still requires a
compatible high Rig/Skin or an evaluated high deformation export. Switching quality
does not transfer a low point cache onto a different topology.

Release authors `asset.usd` with Look and Geometry sublayers, and publishes a new
version of the existing shared Asset USD entrypoint. It does not export the current
scene or require a Maya binary in the Release. Source Work Scenes remain recorded by
their Geometry/Look publishes. All output paths use ProjectPaths.

The Release checks Asset identity, receipt locations, artifact hashes, complete mesh
bindings, topology/UV compatibility and texture dependencies before publication.
Receipts pin the selected Geometry/Look, Look authoring inputs and Texture artifacts.
Older releases remain unchanged. Context history shows the selected input versions.
The existing Pack service remains available for legacy releases.

## Optional static Rig

Select `Rig (optional) / anim / v###` to include a separated `usd_skel.v2` publish.
`None (static)` keeps the Geometry + Look workflow. Rig is explicitly selected,
never silently added to existing Releases. History records all three fixed inputs.
The version list also accepts the earlier DLI experimental v001 receipt; its missing
latest pointer is displayed as `-`, rather than inventing a latest designation.

Rig and Geometry must have the same unambiguous mesh names, topology, UVs and local
bind points (maximum tolerance 0.01 cm). Rig owns bind coordinate frames and hierarchy
visibility; selected Geometry owns the mesh data through references. Release does not
duplicate points or silently replace the selected Geometry with the Rig package's geo.
Look bindings are mapped to that hierarchy. Rig receipts and package files are hashed
and pinned. The common Asset entry preserves SkelRoot inside each quality variant,
so animation can still drive the released skeleton. Incompatible static inputs are
reported before allocating a Release. Shot animation's existing deform fallback is
unchanged; this operation does not evaluate a Maya shot or create animation.

The Maya Preview Look adapter supports static UDIM (Mari) file nodes connected
directly to Lambert color. All source tiles must match the selected Texture Publish
by filename and content. Publish materializes the selected tiles beside look.usd,
so tiles originating in different Texture versions share one immutable UDIM path.
The source Texture publications remain unchanged. Preview, Release and Shot
dependency checks expand the pattern into individual files and reject missing tiles.
Maya 2024's older USD runtime is supported by a compatibility implementation.

Texture intake Usage is descriptive metadata: it does not automatically connect
materials. Roughness/Normal maps and arbitrary Arnold/Karma networks remain outside
the current Lambert Preview adapter. Animated file nodes and non-Mari tiling modes
are rejected. Preview materials use st; UDIM uses multiple tiles within that UV set.
