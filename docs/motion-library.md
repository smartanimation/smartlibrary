# Motion Library

Motion is independent of character assets. The existing ProjectPaths owns all motion paths; configurable motion_* templates live in templates_assets.yml.

## Work and Publish

- Work: workspace/cg/motion/{category}/{group}/{asset}/{variant}/work/anim/maya/{clip}/{project}_{asset}_{variant}_anim_{clip}_{version}_{take}.mb
- Received originals: production/motion/{category}/{group}/{asset}/{variant}/reference/motion/{receipt_id}/{original_name}.fbx
- Clip Publish: production/motion/{category}/{group}/{asset}/{variant}/publish/clip/{clip}/v###/{asset}_{variant}_{clip}.mb, .fbx and manifest.json.

Clips version independently. Takes belong only to Work. Registration copies and records hashes; it never moves or overwrites originals. Existing trial FBX exports are copied beside Work and are not classified as received mocap originals.

In Maya, open a registered Work scene and use SmartMenu > Animation > Motion Clip Publish. Save first. Choose the deformation skeleton root and placement anchor; set root motion (stationary / in_place / locomotion), forward axis and loop. The current playback range is the export range. These semantic choices are deliberately not inferred from the clip name.

Publish preserves the MB and its asset references. FBX contains a baked skeleton, not the source rig controls or character geometry. Geometry belongs to the separately versioned Agent definition from assets. Bone names are namespace-independent in the manifest; the temporary export namespace may be retained in FBX. Multiple joint roots require selecting the intended skeleton, not exporting every joint in the rig.

The exporter preserves intermediate transforms and joint scale compensation, and samples the selected hierarchy once per frame into a temporary skeleton, exports it, then restores time, selection and dirty state. It does not bake onto the source rig. Manifest records timing, unit/up-axis, forward direction, root-motion mode, loop flag, skeleton root, bone names, reference paths/hashes and placement-anchor matrix at the first frame. It does not automatically retarget mocap, remove root motion, normalize a seated pose, or certify a seamless loop.

Incomplete publishes remain marked _building and never advance latest. Completed outputs are immutable and hashes are checked on resolution. Publish version allocation uses an exclusive clip lock.

## API

`MotionLibraryService.register_work` copies an MB and optional trial FBX into Work. `register_received_fbx` archives incoming FBX with a receipt. `publish` is the DCC-neutral publisher with an exporter callback. `resolve_clip` resolves fixed/latest complete versions and checks hashes.

Maya Python fallback entry:

```python
from smartlib.apps.motion_library.window import show
show(config_dir="P:/dev/smartprojects/config/ELCD")
```

## ELCD test registration

Target: character/mob/MBGm/default. Clips: sit_idle_01 and walk_01. Both source scenes reference assets/.../publish/asset/anim/v002/MBGm_default.mb and use 24fps, frames 1-120, cm, Y-up. Sit Work uses v001/t02; Walk Work uses v001/t01. Source-registration JSON is kept beside each Work file.

The trial files were registered, not declared approved clips. Forward direction, placement anchor, root-motion and looping semantics must be selected before production Clip Publish.
