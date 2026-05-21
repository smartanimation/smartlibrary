from __future__ import annotations

from pathlib import Path


def capture_viewport_thumbnail(path: str | Path, *, width: int = 320, height: int = 180) -> Path:
    """Capture the current Maya viewport to a thumbnail image."""

    try:
        import maya.cmds as cmds
    except ImportError as exc:
        raise RuntimeError("Thumbnail capture is available inside Maya.") from exc

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame = cmds.currentTime(query=True)
    selection = cmds.ls(selection=True, long=True) or []
    try:
        cmds.select(clear=True)
        cmds.playblast(
            completeFilename=str(output),
            forceOverwrite=True,
            format="image",
            compression="jpg",
            viewer=False,
            showOrnaments=False,
            offScreen=True,
            frame=[frame],
            widthHeight=[int(width), int(height)],
            percent=100,
        )
    finally:
        if selection:
            existing = [node for node in selection if cmds.objExists(node)]
            if existing:
                cmds.select(existing, replace=True)
    return output
