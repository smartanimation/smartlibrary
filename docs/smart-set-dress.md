# Smart Set Dress

Smart Set Dress records changed Maya plugs as ordered, non-destructive JSON
layers. The top layer has override priority.

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

Choose **Maya** or **USD** in Target before recording. Maya mode captures Maya
transform hierarchies; USD mode captures UFE prim hierarchies from a
`mayaUsdProxyShape`. Both target types can coexist in one Set Dress package.

USD changes are authored to the MayaUSD stage session layer, so the source USD
background file is not modified. USD mode requires `mayaUsdPlugin` plus the
MayaUSD Python and UFE bindings. With **Selected hierarchy** disabled, all
transformable prims on every MayaUSD ProxyShape are inspected.

1. Choose the target and select a Maya hierarchy or MayaUSD prim hierarchy.
2. Select or create the destination layer.
3. Click **Record**.
4. Move, rotate, scale, hide objects, or edit rig variant controls.
5. Click **Stop & Capture**.
6. Reorder layers by dragging. Mute, restore, or apply layers as needed.
7. Name each layer (for example `Desk_set` or `Chair`), choose its
   `shot` or `sequence` scope, then click **Save Layers**.

**Save Layers** writes each nonempty layer to its own named working file.
The read-only **Layer** field reflects the selection; there is no shared
`main` package name to enter. **Publish Layer** is temporarily hidden from
the UI (the publishing backend remains available). **History** operates on
the selected layer only. Loading a layer or restoring its history retains
other layers in the scene. Empty placeholder layers are not exported.

Layer filenames are sanitized using the existing package naming rules.
Names that resolve to the same filename (including case-only differences)
are rejected before saving; a different layer ID cannot overwrite an existing
file just by reusing its name.

While Record is active, a red banner displays **RECORDING** and the destination
layer name, the window title includes **RECORDING**, and **Stop & Capture** is
highlighted in red. These indicators clear after a successful capture or a
scene reset. A failed capture keeps the indicators active so recording is not
mistaken for a completed save. The native OS title-bar color is not modified.

Maya recording includes transform/visibility plus unlocked scalar attributes
exposed as keyable or in the Channel Box. This includes common rig variant
controls (bool, enum, numeric, and string) while excluding matrices, arrays,
message plugs, outputs hidden from normal authoring, and other internal data.
Only values that differ between Record and Stop are stored. Transform and Shape
nodes in the selected hierarchy are inspected. When **Selected hierarchy** is
disabled, all Transform and Shape nodes in the scene are inspected; unrelated
DG nodes are not included.
Layer files are saved as `*.setdress.json`. Node UUIDs are stored alongside DAG
paths so renames and reparenting can usually be resolved.

Layer edits are automatically synchronized after capture, rename, reorder,
mute, delete, and scope changes. Two recovery copies are
maintained:

- One working JSON per named layer under `data/setdress`
- A compressed, checksummed payload on the scene's `smartSetDressData` network node

The manager restores the embedded copy when the Maya scene is reopened. A
before-save scene callback also forces synchronization. No attributes are added
to referenced set assets or transform nodes.

When the manager remains open during **Open Scene** or **New Scene**, its
in-memory package, working path, and active recording are cleared before Maya
changes scenes. After the change, data is restored only from the new scene's
own `smartSetDressData` node. A scene without embedded data starts with a clean
Set Dress state; data retained from the previous scene is never autosaved into
it.

`Stop & Capture` and an explicit `Save Layers` also create lightweight working
revisions:

```text
<shot_root>/data/setdress/.history/<layer>/r####.setdress.json
```

The newest 30 revisions are retained by default. Routine autosaves update only
the working JSON, so mute and reorder operations do not create excessive
history. Use **History** to restore a revision; the current state is checkpointed
before restoration. Set `SMART_SET_DRESS_HISTORY_LIMIT` to change the retained
count.

The default working-data locations are:

- Shot: `<shot_root>/data/setdress/<layer>.setdress.json`
- Sequence: `<sequence_root>/data/setdress/<layer>.setdress.json`

**Publish Layer** validates and saves the selected layer, then creates an
immutable version independent of every other layer:

- Shot: `<shot_root>/publish/setdress/<layer>/v###/<layer>.setdress.json`
- Sequence: `<sequence_root>/publish/setdress/<layer>/v###/<layer>.setdress.json`

Shot Manager exposes these versions under the **Set Dress** DataType. In Maya,
select a published version and click **Apply Set Dress**.

Review Build Manager composes all enabled Set Dress components during Build,
applies both Maya and USD layers after the referenced scene content exists, and
embeds the composed package on the resulting Maya scene. Opening Smart Set
Dress from a built scene therefore restores the same layer information instead
of only preserving the evaluated attribute values.

In **WORK STAGE**, editable shot Set Dress data is shown in Build Contents with
version `WORK`. It takes precedence over a published package with the same
name, so current layer edits can be rebuilt before publishing. Publish-only
packages with different names remain selectable.

Shot Manager and Build Contents list the named layers independently. Each
file contains only that layer's modified plugs and their base values, so an
excluded layer's attributes are not restored by another layer. Saved stack
order is retained when composing the layer files for Build.

Existing multi-layer packages remain readable. Saving their layers creates
the individual files; the original bundle is retained for recovery and is
hidden from working candidates once all its nonempty layers have been split.
Older names for the same layer ID are likewise retained on disk but only the
most recently saved file is listed. Removing a layer from the scene does not
delete its saved working file or immutable publishes; exclude it explicitly
in Build Contents when it should no longer be built.

Project paths are resolved through the active project configuration and its
`shot_root`/`sequences_root` templates.
