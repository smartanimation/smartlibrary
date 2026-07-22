# Maya Plug-ins

## Smart Viewport Gate Guides

`plug-ins/smart_viewport_gate_guides.py` is a Maya 2026 Python plug-in that draws text and guide lines inside the active camera resolution gate in Viewport 2.0.

The default Maya cameras `persp`, `top`, `front`, and `side` are ignored so the overlay only appears on scene cameras.

### Load

In Maya, open Plug-in Manager and load:

```text
P:/dev/smartlibrary/tools/maya/plug-ins/smart_viewport_gate_guides.py
```

Or run:

```python
import maya.cmds as cmds

cmds.loadPlugin(r"P:/dev/smartlibrary/tools/maya/plug-ins/smart_viewport_gate_guides.py")
cmds.SmartGateGuide()
```

### Use

Select the created `SmartGateGuide#` locator and edit the Extra Attributes.

Useful attributes:

- `Camera`: optional camera transform or shape name. Leave blank to draw in every camera view.
- `Top Left Text`, `Top Center Text`, `Top Right Text`
- `Bottom Left Text`, `Bottom Center Text`, `Bottom Right Text`
- `Show Resolution Gate`
- `Show Center Line`
- `Show Rule Of Thirds`
- `Show Diagonal Cross`

Supported text tokens:

- `{counter}`
- `{animTime}`: 24-frame time display, for example frame 240 becomes `(10 + 00)` and frame 36 becomes `(01 + 12)`.
- `{scene}`
- `{camera}`
- `{camera_clean}`
- `{focal_length}`
- `{frame_start}`
- `{frame_end}`
- `{total_frames}`
- `{username}`
- `{date}`
