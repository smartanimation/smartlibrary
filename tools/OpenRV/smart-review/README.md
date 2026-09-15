# Smart Review RV Plugin

This is the RV-side Smart Review panel. It is intentionally a docked RV plugin,
not a replacement player. The panel resolves SmartLibrary review packages and
pushes media into the current RV session.

## Layout

- `Project` is global and sits above the `Asset` / `Shot` tabs.
- `Asset` has `Current Shot/Sequence`, asset selection, quick-check presets,
  and RV actions.
- `Shot` has `Current Shot/Sequence`, sequence navigation, multi-shot
  selection, selection operation buttons, review modes including `Contact
  Sheet`, and RV actions.
- The action buttons are arranged as:
  - `Load Into Current Session` | `Replace Current Sources`
  - `Open New Session` | `Build RV Session`

## Development Install

From PowerShell:

```powershell
P:\dev\smartlibrary\tools\openrv\smart-review\install-dev.ps1
setx SMARTLIBRARY_ROOT "P:\dev\smartlibrary"
```

Restart RV and enable/open `Tools > Smart Review`.

External launchers can open RV directly into this plugin by setting:

- `SMART_REVIEW_PROJECT`: config project name, for example `STKB`
- `SMART_REVIEW_REVIEW_JSON`: review package JSON to load
- `SMART_REVIEW_SELECTED_SHOTS`: JSON list or comma-separated shot names to check/load
- `SMART_REVIEW_AUTO_LOAD=1`: load the package media on startup
- `SMART_REVIEW_SHOW_PANEL=1`: show the Smart Review dock on startup

The script installs:

- `%APPDATA%\RV\Packages\smart_review-0.1.rvpkg`
- `%APPDATA%\RV\Python\smart_review.py`
- `%APPDATA%\RV\Mu\rvload2`
- `%APPDATA%\TweakSoftware\RV\Packages\smart_review-0.1.rvpkg`
- `%APPDATA%\TweakSoftware\RV\Python\smart_review.py`
- `%APPDATA%\TweakSoftware\RV\Mu\rvload2`

`rvload2` must live under `Mu` because RV resolves package files from
`dirname(dirname(rvload2))\Packages`.
Python modes must also be present under `Python` because RV imports them as
normal Python modules.

If your RV uses a different user support path, pass it explicitly:

```powershell
.\install-dev.ps1 -SupportRoot "C:\path\to\rv\support"
```

## Current Scope

The first implementation resolves existing SmartLibrary `latest.json` /
`review.json` packages and loads their media through RV's `commands.addSources`.
Advanced RV graph construction for grid/contact-sheet layouts is the next layer.

## Shot Browser

Open `Smart Review > Shot Browser` in RV, or `Open Shot Browser` on the Shot tab.
The browser is a separate, non-modal window and uses the existing project configuration
and shared Path Resolver. The `packages` directory from SMARTLIBRARY_ROOT must be available,
as it is for the existing resolver integration. PySide2 or PySide6 is required.

- Sequence supports Ctrl multi-selection and Shift range selection; All sequences resets
  the scope. Selected sequences are combined in episode/sequence/shot order for RV.
- Episode and Sequence lists, Task buttons, search, Latest / All versions / exact
  version, and availability filters narrow the thumbnail grid.
- Task corresponds to Smart Review's Department (layout, anim, fx, light, comp).
- Source `working` reads the resolver's working review movie directory; `internal`
  and `client` read formal review manifests; `publish` reads published review packages.
  Raw render layers are not treated as review movies. Missing movies remain visible.
- Drag from the empty space around cards for rectangle selection; Ctrl adds or removes
  individual cards; Shift selects a range. Select all applies to the visible results.
- Push to RV appends the exact selected movies and versions in shot order.
  Open New Session uses RV's existing new-session operation. Missing files are skipped.
- NEW VERSION compares against the highest version successfully loaded from this browser
  on this device, per project/shot/task/source. It does not indicate review approval.
  First-time entries say Not loaded on this device. History is stored in QSettings.
- Refresh rescans uploads. Disk scanning runs in a worker; only visible thumbnails are
  requested from FFmpeg, with an in-memory cache and a 12-second decode timeout.
  FFmpeg is found using the existing pipeline helper, then PATH. Without FFmpeg,
  version information and movie loading remain available.

Validation: `python -m pytest tests/test_shot_browser.py tests/test_rv.py`.
