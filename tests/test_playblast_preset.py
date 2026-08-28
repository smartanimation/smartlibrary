from smartlib.dcc.maya.playblast_preset import _force_playblast_overlays_off


class _ModelEditorCmds:
    def __init__(self):
        self.edits = []

    def modelEditor(self, panel, edit=False, **kwargs):
        self.edits.append((panel, edit, kwargs))


def test_forced_playblast_overlays_hide_nurbs_curves():
    cmds = _ModelEditorCmds()

    _force_playblast_overlays_off(cmds, "modelPanel1")

    assert ("modelPanel1", True, {"nurbsCurves": False}) in cmds.edits
