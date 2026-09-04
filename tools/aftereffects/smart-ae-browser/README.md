# smart AE browser

`smart AE browser` is a CEP panel for After Effects. It reads project definitions from `P:/dev/smartlibrary/config`, imports a manifest exported by smart render, builds an output watch list, detects updated output files, and replaces matching footage items in the current AE project.

## Files

- `index.html`: panel UI
- `css/panel.css`: After Effects style dark UI
- `js/app.js`: manifest parsing, output watch list, replace queue, state handling
- `js/cep-bridge.js`: small CEP `evalScript` bridge
- `jsx/host.jsx`: After Effects file dialogs, file snapshots, and footage replacement
- `sample/project_v010.json`: sample smart render manifest

## Project Context

The Project selector is populated from `config/<project>/templates_base.yml`.

Launcher and SmartRender pass the current context through environment variables and this handoff file:

```text
%APPDATA%/smartuserdata/smart_ae_browser_context.json
```

Supported context fields:

```json
{
  "source": "launcher",
  "project": "STKB",
  "projectRoot": "D:/Projects/STKB",
  "configDir": "P:/dev/smartlibrary/config/STKB",
  "episode": "ep01",
  "sequence": "sq010",
  "shot": "sh010",
  "manifest": "D:/Projects/STKB/..."
}
```

## State and Current

The panel restores its previous selection when it opens. Use `Current`, beside
the refresh button, to explicitly switch to the latest shot selected in Shot
Manager. This only changes the browser filters; it does not open or build an AEP.

## Manifest Shape

The panel accepts any of these top-level item arrays:

- `items`
- `outputs`
- `assets`
- `renders`

Each item can use these fields:

```json
{
  "project": "Showcase",
  "episode": "ep01",
  "sequence": "sq010",
  "shot": "sh010",
  "name": "comp_main",
  "sourcePath": "/path/to/current/comp_main_v010.mov",
  "outputPath": "/path/to/latest/comp_main_v011.mov",
  "version": "v010",
  "latestVersion": "v011"
}
```

`project`, `episode`, `sequence`, and `shot` can be set on the manifest or on each item. Item values override manifest values. If they are not present, the panel tries to infer them from paths such as `/Projects/Showcase/ep01/sq010/sh010/...`.

`sourcePath` is used to find existing AE footage. If the path does not match, the panel falls back to the AE footage item name.

## Install For Development

From this folder:

```powershell
.\install-dev.ps1
```

Or copy this folder manually to the CEP extensions directory:

```powershell
$src = "P:\dev\smartlibrary\tools\aftereffects\smart-ae-browser"
$dst = "$env:APPDATA\Adobe\CEP\extensions\smart-ae-browser"
New-Item -ItemType Directory -Force -Path $dst
Copy-Item -Path "$src\*" -Destination $dst -Recurse -Force
```

Enable unsigned CEP extensions once per machine:

```powershell
reg add "HKCU\Software\Adobe\CSXS.11" /v PlayerDebugMode /t REG_SZ /d 1 /f
reg add "HKCU\Software\Adobe\CSXS.12" /v PlayerDebugMode /t REG_SZ /d 1 /f
```

Restart After Effects, then open `Window > Extensions > smart AE browser`.

## Current Behavior

- `Import Manifest...`: opens a JSON manifest via After Effects file dialog.
- `Build`: converts the selected manifest into Output Watch and Replace Queue rows.
- `Open`: opens saved browser state.
- `Save`: saves browser state as JSON.
- `Refresh`: polls output paths through ExtendScript file snapshots.
- `Replace All`: replaces AE footage whose current source path or item name matches the manifest row.
- `Publish`: saves the active AEP, validates the final and `stage`
  compositions in After Effects, snapshots footage/font dependencies, and calls
  the shared Review Workflow backend. The immutable package is written to:

  ```text
  {shot_root}/publish/precomp/v###/
    aftereffects/precomp.aep
    metadata/input_schema.json
    metadata/composition.json
    metadata/validation.json
    metadata/dependency_snapshot.json
    metadata/publish.json
  ```

  Stage input IDs come from the selected Preview Render manifest (Playblast
  Settings) and match layer names such as `CHA` or `BGA`; no `INPUT_*` prefix is
  required. Layer order and duplicate uses are preserved as metadata. A missing
  required Stage layer blocks publish, while the same layer ID pointing at
  different sources is a warning. The final comp must contain exactly one layer
  and no layer effects, but its source comp is recorded rather than fixed.

The browser preview outside AE uses the bundled demo data and stores panel state in `localStorage`.
