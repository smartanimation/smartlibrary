from smartlib.dcc.resolve.marker_text_plus_v2 import _set_input_expression


def test_expression_falls_back_to_styled_text_input():
    class Input:
        def __init__(self): self.expression = None
        def SetExpression(self, value): self.expression = value
    class Tool:
        SetExpression = None
        def __init__(self): self.StyledText = Input()
    tool = Tool()
    _set_input_expression(tool, "StyledText", "Text(time)")
    assert tool.StyledText.expression == "Text(time)"
