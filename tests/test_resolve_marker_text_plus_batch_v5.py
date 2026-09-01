from smartlib.dcc.resolve.marker_text_plus_batch_v5 import _set_h_anchor_left


def test_detects_new_horizontal_anchor_input_id():
    class Input:
        def GetAttrs(self):
            return {"INPS_ID": "HorizontalJustificationNew", "INPS_Name": "H Anchor"}
    class Tool:
        def __init__(self): self.values = {}
        def SetInput(self, name, value): self.values[name] = value; return True
        def GetInputList(self): return {1: Input()}
    tool = Tool()
    applied = _set_h_anchor_left(tool)
    assert "HorizontalJustificationNew" in applied
    assert tool.values["HorizontalJustificationNew"] == -1.0
