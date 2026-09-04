"""Project-scoped UI selection, separate from the scene open in this process.

This is live application state, not the versioned USD Shot Context artifact.
Scene identity is always resolved on demand through ShotManagerService.
"""
from __future__ import annotations

import logging
import os
from weakref import WeakMethod


class ShotContext:
    def __init__(self, project_config=None):
        self._project_config = project_config
        self.selected_shot = None
        self._listeners = set()

    def select_shot(self, identity):
        if self._project_config is not None:
            try:
                publish_shared_selection(self._project_config, identity)
            except Exception:
                logging.getLogger(__name__).exception("Could not publish shared shot selection")
        if identity == self.selected_shot:
            return
        self.selected_shot = identity
        for reference in tuple(self._listeners):
            listener = reference()
            if listener is None:
                self._listeners.discard(reference)
            else:
                try:
                    listener(identity)
                except Exception:
                    logging.getLogger(__name__).exception("Shot selection listener failed")

    def subscribe(self, listener):
        self._listeners.add(WeakMethod(listener))

    def unsubscribe(self, listener):
        self._listeners.discard(WeakMethod(listener))

    def scene_shot(self, service):
        """Return the current Maya shot; never fall back to selection or tokens."""
        try:
            import maya.cmds as cmds
        except ImportError:
            return None
        scene_path = str(cmds.file(query=True, sceneName=True) or "")
        return service.shot_identity_from_path(scene_path) if scene_path else None


_CONTEXTS = {}


def get_shot_context(project_config):
    """Share selection between tools in the same project and application process."""
    key = os.path.normcase(os.path.abspath(str(project_config.config_dir)))
    return _CONTEXTS.setdefault(key, ShotContext(project_config))


def publish_shared_selection(project_config, identity):
    """Export selection for explicit cross-process Current actions, not scene edits."""
    from scripts.dcc_context import WorkContext, save_context

    context = WorkContext(
        episode=identity.episode if identity else "",
        sequence=identity.sequence if identity else "",
        shot=identity.shot if identity else "",
        extra={
            "kind": "smartpipeline.shot_selection.v1",
            "project": project_config.project_name,
            "projectRoot": str(project_config.project_root or ""),
            "configDir": str(project_config.config_dir),
            "available": identity is not None,
        },
    )
    return save_context(context)


def read_shared_selection():
    """Read the last exported selection without restoring in-process selection."""
    from scripts.dcc_context import load_context, validate_token

    context = load_context()
    extra = context.extra
    if not isinstance(extra, dict) or extra.get("kind") != "smartpipeline.shot_selection.v1":
        raise ValueError("No shared Shot Manager selection is available")
    if not extra.get("available"):
        raise ValueError("No shot is selected in Shot Manager")
    for key in ("episode", "sequence", "shot"):
        validate_token(key, getattr(context, key))
    if not extra.get("project") or not extra.get("configDir"):
        raise ValueError("Shared shot selection has no project configuration")
    return {
        "source": "shot_manager",
        "project": extra["project"],
        "projectRoot": extra.get("projectRoot", ""),
        "configDir": extra["configDir"],
        "episode": context.episode,
        "sequence": context.sequence,
        "shot": context.shot,
        "updatedAt": context.updated_at,
    }
