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
