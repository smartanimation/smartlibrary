# Project Maya postopen scripts

In Config Creator, select a project and its Maya software registration, then open
**Postopen Scripts**. Use **+ File** to register Python files, **Up / Down** (or drag)
to set their order, and save the project. Each Maya registration has its own list.

The existing `ProjectPaths` resolver locates scripts under
`production/settings/tools/maya/postopen`. A configured `production_root` is honored.
Only registered files run. Paths in `software_<registration>.yml` are relative:

```yaml
postopen_scripts:
  - renderGlobalSet.py
  - reloadTextures.py
```

Launch Maya again from SmartLauncher after installing this feature. The launcher
passes `SMART_SOFTWARE_ID` so duplicate/versioned Maya registrations use the exact
selected configuration. Scripts and settings are read again on each successful
interactive `SceneOpened` event. New scenes, references/imports, and batch workers
do not trigger this hook. Startup installation itself does not execute the scripts.
Keep the SmartPipeline `maya_user_setup` enabled.

Existing scripts containing top-level Python statements work unchanged. New scripts
can define a function:

```python
def run(context):
    import maya.cmds as cmds
    print(context['project_id'], context['file_path'])
    cmds.setAttr('hardwareRenderingGlobals.defaultLightIntensity', 1)
```

`context` contains `project_id`, `project_root`, `config_dir`, `file_path`,
`software` (`maya`), `software_id` (selected registration), and `event` (`postopen`).
It is also available as a global to top-level scripts. Each file gets its own dict.
Top-level statements execute once, followed by `run(context)` when defined; keep
actions inside the function when using that style. An `if __name__ == '__main__'`
block is not executed. Use the common resolver for additional pipeline paths.

Missing/invalid files are checked before the list runs. A script error stops the
remaining list and appears in Maya's warning output and Script Editor traceback.
The scene stays open and earlier script changes are not rolled back. Scripts must
not open another scene; recursive hook invocation is suppressed.

ELCD test folder: `D:/Projects/ELCD/production/settings/tools/maya/postopen`.
Register `renderGlobalSet.py`, then `reloadTextures.py`, to apply rendering settings
before refreshing viewport textures. These scripts can remain unchanged.
