# Retarget Setup

Retarget Setup authors one reusable profile per character asset. Clothing variants do not own profiles; a different body/skeleton is a different asset. Clothing simulation belongs to Houdini. Received human mocap uses the common source skeleton.

## Ownership and entry points

- `python -m smartlib.apps.retarget_setup --config-dir <project-config>`
- `bat/run_retarget_setup.bat`
- Launcher > SmartTools > Retarget Setup (subject to the existing tool allowlist)
- Asset Manager > Publish > Open Retarget Setup, carrying the selected character

The Asset Manager Publish type selector no longer contains Retarget. Its ordinary Data/Publish file browsing remains available. Existing `smartlib.apps.asset_manager.retarget_publish` functions remain available for legacy callers; the independent tool uses `smartlib.apps.retarget_setup.service`.

## Authoring

Choose a character, create from the project template (or bundled fallback), or import an existing profile. MCR and ANM resolve through the existing asset publish resolver, with the legacy `rig/MCR` and `rig/ANM` lookup confined to ProjectPaths. Browse can explicitly select a rig when project configuration does not resolve it. Refresh Rig Dependencies is an explicit update and invalidates the current test.

Pole-vector enable flags and distance ratios have dedicated controls. Transfer nodes are grouped in a checkbox tree; unchecking a node adds it to the exclusion list. Advanced settings expose the existing transfer profile as JSON, including transfer nodes, wrist definitions and rig settings. Templates are materialized snapshots: changing the template file later never silently changes a saved profile. New from Template explicitly starts again from the current template.

- Draft is editable and can be saved before dependencies are complete.
- Data Versions are immutable configuration snapshots, independent of test FBX/frame range.
- Publish registers a saved, tested Data Version for Scene Build.

All new pipeline locations come from the existing ProjectPaths resolver. Character-wide paths resolve the existing variant segment to `.` (no clothing directory), retaining configured work/data/publish templates. No new resolver or path configuration is introduced. Old default-variant Data/Publish histories and work profiles can be loaded as Draft; they are not rewritten or migrated automatically. Other clothing-specific legacy profiles must be imported explicitly.

## Maya test and Publish

Select a received or standard FBX and frame range. Run Maya Test launches the existing `tools/maya/bake_mocap_to_rig.py` in a separate configured mayapy process; the artist's current scene is unaffected. Each run has a unique directory containing its materialized input, output scene, report, status and log. These are work artifacts, not Review or delivery output.

A run passes only when Maya exits successfully, the result exists, some plugs were keyed, and no attributes are missing or fail to receive keys. Intentionally locked channels are reported separately; unclassified skips remain failures. Open Result in Maya and check the resulting motion before marking it reviewed. Saving the same settings as a Data Version can follow the test without repeating it.

Publish verifies a fingerprint of the saved settings plus rig/plug-in path, size and modification time. It also checks that the test motion has not changed and that the successful test has been reviewed. Settings/dependency changes require another test. This is a dependency freshness check, not a geometric quality metric. Review confirmation is an operator acknowledgment, not a separate approval workflow.

Scene Build can use `RetargetService.resolve_published(version=...)` to obtain the exact profile and provenance. Scene Build supplies the received FBX and frame range as job inputs; these never become character settings. This change does not automatically alter existing Scene Build consumers or introduce batch conversion.

## Verification

Automated tests cover shared storage, legacy reads, immutable versions, test failure/skipped plugs, stale input rejection, publish provenance and Qt state changes. Test outputs belong under `.tmp/`.
