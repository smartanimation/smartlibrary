SmartPipeline Blender tools.

Recommended: install the Smart Asset Panel as a Blender add-on so it is available on startup.

1. Blender > Edit > Preferences > Add-ons > Install...
2. Select:

```text
P:/dev/smartlibrary/tools/blender/smart_asset_panel_addon.py
```

3. Enable "Pipeline: Smart Asset Panel".
4. Open the panel from:

```text
View3D > Sidebar > SMART ASSET > SMART ASSET
```

For development reloads, run this from the Blender Python Console:

```python
exec(open(r"P:/dev/smartlibrary/tools/blender/register_smart_asset_panel.py", encoding="utf-8").read())
```

The add-on resolves the repository from `SMARTPIPELINE_ROOT`, `SMARTLIBRARY_ROOT`, or the default `P:/dev/smartlibrary`.

Modeling support add-on:

```text
P:/dev/smartlibrary/tools/blender/modeling_support_addon.py
```

Panel:

```text
View3D > Sidebar > SMART MODELING > SMART MODELING
```

Paths:
- Work: `{asset}/{variant}/work/model/blender/{subset}/*.blend`
- Geo data: `{asset}/{variant}/data/geo/{subset}/fbx/v###/geo.fbx`
- Model FBX data: `{asset}/default/data/model/{subset}/fbx/v###/model.fbx`
  - Texture files are copied by Blender's FBX exporter.
