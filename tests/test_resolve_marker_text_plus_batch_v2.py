from smartlib.dcc.resolve.marker_text_plus_batch_v2 import _set_black_left_aligned


def test_text_is_black_and_left_aligned():
    class Tool:
        def __init__(self): self.inputs = {}
        def SetInput(self, name, value): self.inputs[name] = value
    tool = Tool()
    _set_black_left_aligned(tool)
    assert tool.inputs == {
        "HorizontalJustification": -1.0,
        "Red1": 0.0,
        "Green1": 0.0,
        "Blue1": 0.0,
    }
