# Smart Set Dress

Smart Set Dress records transform and visibility edits as ordered, non-destructive
JSON layers. The top layer has override priority.

## Open in Maya

Reload `SmartMenu`, then choose **Layout > Smart Set Dress**.

**Reload SmartMenu** reloads the `smart_menu` Python module from disk before
rebuilding the menu. Smart Set Dress modules are also reloaded whenever the
tool is opened, so Maya does not need to be restarted during development.

Alternatively:

```python
from smartlib.apps import set_dress
set_dress.show()
```

## Workflow

1. Select a set hierarchy in Maya.
2. Select or create the destination layer.
3. Click **Record**.
4. Move, rotate, scale, or hide objects in the viewport.
5. Click **Stop & Capture**.
6. Reorder layers by dragging. Mute, restore, or apply layers as needed.
7. Choose `shot` or `sequence`, then click **Save Layers**.

When **Selected hierarchy** is disabled, all scene transforms are inspected.
Layer files are saved as `*.setdress.json`. Node UUIDs are stored alongside DAG
paths so renames and reparenting can usually be resolved.

The default working-data locations are:

- Shot: `<shot_root>/data/setdress/<package>.setdress.json`
- Sequence: `<sequence_root>/data/setdress/<package>.setdress.json`

**Publish** validates and saves the current package, then creates an immutable
version:

- Shot: `<shot_root>/publish/setdress/<package>/v###/<package>.setdress.json`
- Sequence: `<sequence_root>/publish/setdress/<package>/v###/<package>.setdress.json`

Shot Manager exposes these versions under the **Set Dress** DataType. In Maya,
select a published version and click **Apply Set Dress**.

Project paths are resolved through the active project configuration and its
`shot_root`/`sequences_root` templates.
