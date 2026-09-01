from smartlib.dcc.resolve.marker_text_plus_batch_v3 import _set_gray_background


def test_translucent_gray_text_background():
    class Tool:
        def __init__(self): self.inputs = {}
        def SetInput(self, name, value): self.inputs[name] = value
    tool = Tool()
    _set_gray_background(tool)
    assert tool.inputs["Enabled2"] == 1.0
    assert tool.inputs["ElementShape2"] == 2.0
    assert tool.inputs["Red2"] == tool.inputs["Green2"] == tool.inputs["Blue2"] == 0.35
    assert tool.inputs["Alpha2"] == 0.5
