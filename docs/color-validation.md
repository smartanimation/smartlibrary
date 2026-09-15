# SmartPipeline Color Validation

Status: PROTO configured for Maya/RV. Settings and Maya native CPU image tests verified.
Full cross-application framebuffer validation is blocked by license/authentication failures.

## PROTO verification — 2026-09-11

Installed the exact official `studio-config-v1.0.0_aces-v1.3_ocio-v2.1.ocio` release at:
`P:/dev/smartprojects/config/PROTO/color/ocio/studio-config-v1.0.0_aces-v1.3_ocio-v2.1/config.ocio`.
SHA-256: `2bc58c0f48e805fe14154655cd3b541e6870688c68e06fb84589e249e5dbe0a9`.

The selected contract is ACEScg / sRGB - Display / ACES 1.0 - SDR Video.
`color.validation_hosts: [Maya, RV]` excludes Nuke until it is available.
Config Creator exposes host checkboxes and preserves this selection on save.

- Maya 2024, native OCIO 2.2.1: actual settings match.
- RV 2025.1.0, native OCIO 2.3.2: actual OCIO nodes and config cache identity match.
- RV executable comes from SmartPipeline studio.yml:
  `C:/Program Files/Autodesk/RV-2025.1.0/bin/rv.exe`.
- Maya MImage float EXR roundtrip: maximum absolute error 0.
- Maya colorManagementConvert native CPU display transform: maximum absolute error 0.
  This is not a Maya viewport framebuffer capture.
- Arnold 7.2.4.1 / MtoA 5.3.4.1 emission-grey-card render: maximum RGB error 5.96e-8.
  Batch logs report failed license authorization / watermarks, so it is not approved.
- RVIO cannot obtain a license after Flow Production Tracking token validation fails.
  Native snapshots were blank half-float images and are rejected, not promoted to evidence.

Artifacts: `Y:/PROTO/workspace/validation/color/runs/maya-rv-ocio21-001/`.
Open `report.html`, `color_validation.ma`, or `color_validation.rv` there.
The overall result is FAIL/incomplete; no approved baseline has been created.

Maya requires **removing** MAYA_COLOR_MANAGEMENT_SYNCOLOR from its process environment.
Setting it to the string "0" was observed to activate an incompatible path and cause
config/display errors. SmartLauncher now removes it for enabled project color contracts.

RV auto-setup currently supports EXR-only sessions. Do not mix display-referred movies
into a color-validation session. RV's working space means the source pipeline's
output / display pipeline's input, rather than a global compositing working-space knob.


## Project configuration

Recommended location:

```text
smartprojects/config/<project>/
  project_settings.yml
  color/ocio/<versioned-config-name>/config.ocio
```

Keep each version immutable. Select the relative config reference in Config Creator
under Project Profile > Color Validation / OCIO. The working space is fixed to ACEScg.
The same absolute config is resolved by `ProjectPaths.color_config_file` and passed
to the DCC processes by Smart Launcher, after software environment overrides.

```yaml
color:
  enabled: true
  config: color/ocio/studio-config-v1.0.0_aces-v1.3_ocio-v2.1/config.ocio
  working_space: ACEScg
  display: sRGB - Display
  view: ACES 1.0 - SDR Video
```

The previous 2.4 config was rejected by the tested Maya. The installed 2.1
profile is an unmodified official release and loads successfully. Each project can
select its own versioned config reference.

SDR display selection must match the actual monitor; the example uses sRGB.
Review output requiring a Rec.709/BT.1886 monitor should use the matching display
from the config. Display and View are distinct settings.

## Implemented

- Config Creator editing, persistence and project switching.
- Launcher OCIO environment for Maya/Nuke/RV; Maya SynColor override disabled.
- Maya startup and scene-open application with readback and SDK compatibility gate.
- Nuke startup registration, OCIO root/working/viewer setup and CPU node EXR export.
  This adapter still requires validation against the installed Nuke version.
- RV source/display/output pipeline setup, active-config cache readback, session export
  and a CLI that invokes the RV installation resolved by Smart Launcher.
- 32-bit float RGB EXR test chart with negative, near-black, 18% grey, saturated,
  white and HDR values. Reference display transform uses the validation Python's
  OCIO CPU implementation and records its version.
- Float EXR comparison, NaN/Inf and dimension rejection, channel metrics, tolerances,
  PNG previews, amplified differences and HTML/JSON report.
- Missing, partial, failed or altered host evidence fails the overall result.
- Explicit named approval, immutable baseline directory, content hash verification
  and regression comparisons. Approval is unavailable until complete host evidence exists.
- Disposable mayapy emission-grey-card scene and Arnold smoke-render entrypoint.
  The PROTO smoke render was executed; license warning blocks baseline approval.

Self-contained configs are supported initially. FileTransform/LUT bundles are rejected
until dependency resolution and hashing are implemented; they are never silently
treated as a self-contained config.

## Commands

Install the optional Python dependencies into a validation environment:

```powershell
python -m pip install numpy OpenImageIO opencolorio
```

With `smartlib` importable:

```powershell
python -m smartlib.color prepare --config-dir P:/dev/smartprojects/config/PROJECT --run validation-001
mayapy -m smartlib.color maya <resolved-manifest.json>
mayapy -m smartlib.color maya <resolved-manifest.json> --render
mayapy -m smartlib.color maya <resolved-manifest.json> --images
python -m smartlib.color rv <resolved-manifest.json> --config-dir P:/dev/smartprojects/config/PROTO
python -m smartlib.color compare <resolved-manifest.json>
python -m smartlib.color compare <resolved-manifest.json> --baseline <resolved-baseline-directory>
python -m smartlib.color approve <resolved-manifest.json> --config-dir <project-config> --baseline approved-001 --reviewer "Reviewer name"
```

`prepare` prints the manifest path. Run IDs cannot be reused. All generated paths
come from the existing common ProjectPaths resolver. Results live under the configured
workspace's `validation/color/runs/<run-id>`; baselines are under
`validation/color/baselines/<baseline-id>`. They are not Review submissions and never
go into the `output` submission tree. The filenames ending in `_output.exr` describe
test images only.

Nuke, in a **disposable empty script**:

```python
from smartlib.color.validation import load_manifest
from smartlib.dcc.nuke.color_validation import run
run(load_manifest(manifest_path))
```

RV, with its actual OCIO nodes active:

```python
from smartlib.color.validation import load_manifest
from smartlib.dcc.rv.color_validation import snapshot
snapshot(load_manifest(manifest_path))
```

These exports deliberately remain `partial`. CPU display output is not proof of a
Maya/Nuke/RV GUI framebuffer match. Host adapters must add genuine framebuffer
captures and effective-config readback before emitting complete evidence. Do not
manually promote partial receipts or copy reference images into host output filenames.

## Remaining work / acceptance

1. Resolve RV/Flow Production Tracking authentication and RVIO licensing.
2. Provide valid Arnold batch licensing, or validate through licensed interactive Maya rendering.
3. Rerun RV GPU float export on a new run ID after authentication is resolved.
4. Finish Maya input roundtrip/display framebuffer export and Nuke viewer export.
5. Expand the executed Arnold grey-card smoke test to texture
   decoding, lighting and highlight cases, and validate those against known values.
6. Compare every host's real exports; visually approve on the intended monitor.
   Browser PNG previews do not establish physical monitor calibration.
7. Register an approved baseline, then rerun after DCC/Arnold/driver/config updates.

The downloaded lookdev scene was inspected without modification. It is a Maya 2022
scene with many old renderer/plugin requirements, so it is unsuitable as the sole
deterministic numerical fixture. A reviewed copy can later supplement the dedicated
test scene for subjective material/lighting checks.

## References

- [OCIO Studio Config profile requirements](https://opencolorio.readthedocs.io/en/main/configurations/aces_studio.html)
- [Maya color management commands](https://help.autodesk.com/cloudhelp/2026/ENU/Maya-Tech-Docs/Commands/colorManagementPrefs.html)
- [Nuke viewer registration](https://learn.foundry.com/nuke/developers/13.2/pythonreference/_modules/nukescripts/ViewerProcess.html)
- [RV OCIO and GPU processing](https://openrv.readthedocs.io/en/latest/rv-manuals/rv-user-manual/rv-user-manual-chapter-eleven.html)
