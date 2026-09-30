# Received MCR → ANIM

Retarget Setup supports an explicit `input_mode: mcr_to_anim` alongside the
legacy Mocap → MCR → ANIM route. Existing profiles and published versions are
not silently converted.

In Settings, select **New received MCR → ANIM mapping**, then add mapping rows.
Source names refer to the received FBX; Target names refer to the ANIM rig.
Namespaces are ignored for leaf-name lookup, but duplicate matches fail.
Methods are `orient` (rotation), `point` (translation), and `parent` (both).
Each target may occur only once. Disabled rows are excluded.

New rows have **Keep offset** off. For received animation with matching bone
axes, this preserves the captured world motion. Existing rows retain their
saved choice. **Keep offset** preserves the source/target relationship evaluated at the
specified **Offset reference frame**. This is a calibration choice, not an
automatic determination of a correct rest pose. Without it, the target follows
the source's world rotation/position directly. Validate the resulting pose and
motion visually before Publish. The new route does not run the legacy pole,
wrist or dual-arm solvers; mappings must target writable channels.
Explicit `rig_settings` are applied to the ANIM reference before constraints
are created. Missing, locked or connected setting plugs stop the test.
IK and whole-body calibration must be configured and reviewed per rig.

Motion Test takes a received MCR FBX and a bake range. The worker:

1. Loads required plug-ins, creates a new scene, and references the selected ANIM rig.
2. Imports received FBX in Add mode into a separate namespace.
3. Resolves enabled mappings and checks target channel writability before creating constraints.
4. Applies constraints and bakes the configured range at one sample per frame.
5. Removes constraints and received geometry/skeleton, verifies keys, and saves `result.ma`.

The MA references the original ANIM rig; it is not a self-contained copy.
The source rig and received FBX are never overwritten. Test files continue to
use the existing Retarget path resolver. Review the MA before publishing the
settings. Automatic full-body mapping is not part of this implementation.

New Motion Test runs use `tests/v001/`, `tests/v002/`, and so on under the
existing Resolver's character Retarget work area. Each execution reserves a
new directory atomically, including reruns with identical settings. The test
version is independent of the Retarget Data and Publish versions. Existing
UUID-based test folders remain readable and are not renamed, because published
test provenance may refer to them. `run_id` remains the storage key in manifests;
new manifests also record `version` and `created_at`.

Initial real-data check: DLI `MC_LeftHand → A_L_wrist` and
`MC_RightHand → A_R_wrist`, orient/offset enabled, reference frame 0,
frames 1–12, Maya 2024. Six baked channels saved to a test MA.
This verifies the execution route, not a production-approved full-body retarget.

Full-body DLI test: 66 mappings (hips, spine, neck/head, shoulders, arms,
wrists, legs/feet, fingers), 201 channels, frames 1–1012. Arm, foot and spine
`enable` switches are set to 0 for FK evaluation. The leg enable input is
connected and must not be set independently. The saved MA was reopened and
checked for 1012 keys per channel and changing elbow/wrist/knee positions.
The original offset-enabled full-body test is rejected: a moving frame 0 was
incorrectly calibrated against the neutral target. Bake fidelity alone did not
detect the pose error (hip translation 62.4 cm, hip-aligned wrist errors up to
54.4 cm).

Corrected DLI test uses the same 66 mappings with `maintain_offset: false`.
The received FBX and this ANIM rig already share world bone axes. The saved MA
was reopened and all 66 received MC joints compared directly against ANIM J
joints at every integer frame 1–1012, without recentering or alignment. Maximum
position error was 0.000001553 cm; maximum quaternion angular difference was
0.000003819 degrees. All 201 baked channels have 1012 keys. This validates this
DLI input / rig pairing in FK, not other characters or IK switching. Visual
review and Retarget Publish are still separate steps.


## Review Build Manager

Sequence Mocap inputs require a reviewed `mcr_to_anim` Retarget Publish for
**each** assigned cast member. The existing character Retarget resolver supplies
the published version. Missing Publish, changed rig/plugin fingerprints, or
unreviewed tests block the recipe before Maya staging. Draft settings are not
used, and the Build path does not fall back to importing an unretargeted FBX.

The cast preview uses the ANIM scene pinned by that Publish. Bake runs in the
existing cast namespace and preserves the staged cameras and other cast.
The recipe's sequence frame range is baked at one sample per frame without
retiming. The sequence time unit must match the profile. The construction
manifest records the Retarget version, path, fingerprint, profile snapshot,
ANIM path and received FBX alongside other resolved inputs.

DLI v001 is approved for full-body FK only; IK matching is deferred. JIN needs
its own test and reviewed Publish before a DLI + JIN sequence can build.


## Asset Manager Retarget tab and Motion Test history

Asset Manager has a dedicated **Retarget** tab, using the same editor as
Retarget Setup. Character-specific editors retain their drafts when switching
assets. The Publish page's Open Retarget Setup action now selects this tab.
A running test must finish or be cancelled before Asset Manager closes.

**Build Motion Test (Worker)** saves the current settings as a Data Version
when needed. Tests tied to Data versions use a readable scene basename:
`ELCD_JIN_retarget_test_data-v003.ma` (legacy route: `.mb`) inside the
Resolver's `tests/v###/` directory. The test manifest records `source_data`.
Calls without a Data version retain the existing `result.ma` / `result.mb`
compatibility names. Existing test folders and published provenance are not
migrated or renamed.

The dedicated `review_build_manager.retarget_worker` runs in a separate
mayapy process. For received MCR it builds, saves, reopens, verifies baked keys
at each sampled frame, and writes `numeric_audit.json` with source-to-mapped
ANIM control world-position and quaternion-angle differences. This is a
measurement, not automatic visual approval or a skin-deformation check;
intentional offsets and different body proportions can produce differences.
Logs and report files remain alongside the test scene. This worker is launched
from Retarget; the existing shot/sequence Job Queue UI is unchanged.

**Test Results** lists numbered runs newest first and older ID-based runs.
**Load Test Result** restores settings, saved Data association, FBX/range,
logs, numerical report and visual-review state. **Open Selected Result in Maya**
opens that test in the Maya session hosting Asset Manager, using its existing
OPEN SCENE action and unsaved-change confirmation. External standalone launches
show guidance to open Asset Manager inside Maya; no new Maya process is started. Loading/opening does not mark it
reviewed. After checking the result, explicitly mark visual review and publish;
Publish still validates the current settings, rig dependencies and motion file.


## Shared MCP test and new characters

ELCD common template `elcd_humanoid_v002` contains the 66 full-body FK mappings,
no-offset transfer and arm/foot/spine FK settings verified on DLI and JIN.
New character profiles resolve their own published ANIM rig through the existing
asset resolver. Existing Draft/Data/Publish profiles are retained. Use **New
from Template** to initialize from the project template, or **Load Common
Full-body FK Mappings** to replace mappings/FK settings while retaining the
selected character's rig paths. The intermediate MCR rig row is hidden in
direct received-MCR mode.

On character selection, Motion Test selects the common FBX and frame range from
`retarget_library("test_motion")/manifest.json`. **Use Common Test FBX** restores
that selection; Browse Motion still allows an explicit alternate FBX. ELCD's
current standard is `humanoid_retarget_test_v001.fbx`, frames 1–254 at 24 fps.
A shared source skeleton does not guarantee equal bone lengths or target axes;
per-character numerical and visual review remains necessary. YOU ANIM v001
completed all 201 channels over 254 frames in the worker check; angular error
was ~0.00000382 degrees, and raw source/target position difference reached
10.49 cm. That position difference has not been classified as rig proportion
versus transfer error; this check does not approve or publish YOU.
