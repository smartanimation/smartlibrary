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

The default save locations are:

- Shot: `<PROJECT_ROOT>/data/setdress/shot/<sequence>/<shot>.setdress.json`
- Sequence: `<PROJECT_ROOT>/data/setdress/sequence/<sequence>.setdress.json`

`SMART_SET_DRESS_ROOT` can override `PROJECT_ROOT`. If neither environment
variable exists, the current directory is used as the project root.
