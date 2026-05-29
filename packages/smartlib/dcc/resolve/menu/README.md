# Smart Editorial Export Resolve Menu

This folder contains a DaVinci Resolve menu bootstrap script.

## Script

- `Smart_Editorial_Export.py`

Copy this file to a Resolve Scripts folder.

User install example:

```text
%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Comp\Smart_Editorial_Export.py
```

All-users install example:

```text
C:\ProgramData\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Comp\Smart_Editorial_Export.py
```

After restarting Resolve, the command should appear under:

```text
Workspace > Scripts > Comp > Smart_Editorial_Export
```

Some Resolve installations may prefer:

```text
Fusion\Scripts\Utility
```

## Environment

The bootstrap reads these environment variables if available:

```text
SMARTPIPELINE_ROOT=P:/dev/smartlibrary
PROJECT_CONFIG_DIR=P:/dev/smartlibrary/config/STKB
```

If they are not set, it falls back to:

```text
P:/dev/smartlibrary
P:/dev/smartlibrary/config/STKB
```

## What It Opens

The script launches:

```python
smartlib.dcc.resolve.export_timeline_ui.show()
```

The UI can stage editorial media, create cutting markers, and save:

```text
editorial/work/{episode}/{sequence}/v###/events.csv
editorial/work/{episode}/{sequence}/v###/manifest.json
```
