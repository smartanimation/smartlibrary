from smartlib.dcc.resolve.marker_text_plus import MarkerCaption, build_styled_text_expression, create_marker_text_plus, format_frames


def test_36_frames_at_24_fps_is_one_second_and_twelve_frames():
    assert format_frames(36, 24) == "01 + 12"


def test_expression_has_shot_countup_newline_and_duration():
    expression = build_styled_text_expression([MarkerCaption("c001", 0, 36)], fps=24)
    assert '"c001\\n"' in expression
    assert 'string.format("%04d", time - 0 + 1)' in expression
    assert '" (01 + 12)"' in expression
    assert "time >= 0 and time < 36" in expression


def test_create_text_plus_uses_current_item_offset():
    class Tool:
        def __init__(self): self.inputs, self.expressions, self.attrs = {}, {}, {}
        def SetAttrs(self, value): self.attrs.update(value)
        def SetInput(self, name, value): self.inputs[name] = value
        def SetExpression(self, name, value): self.expressions[name] = value
    class Comp:
        def __init__(self): self.tool, self.locked = Tool(), False
        def GetAttrs(self): return {"COMPN_GlobalStart": 0}
        def FindTool(self, _name): return None
        def AddTool(self, name): assert name == "TextPlus"; return self.tool
        def Lock(self): self.locked = True
        def Unlock(self): self.locked = False
    class Item:
        def GetStart(self): return 1010
    class Timeline:
        def GetMarkers(self): return {10: {"name": "c001", "duration": 36}}
        def GetStartFrame(self): return 1000
        def GetCurrentVideoItem(self): return Item()
        def GetSetting(self, key): return "24" if key == "timelineFrameRate" else None
    class Project:
        def GetCurrentTimeline(self): return Timeline()
    class Manager:
        def GetCurrentProject(self): return Project()
    class Resolve:
        def GetProjectManager(self): return Manager()
    comp = Comp()
    tool = create_marker_text_plus(resolve_app=Resolve(), comp=comp)
    assert tool.inputs == {"Size": 0.04, "Center": {1: 0.4, 2: 0.25}}
    assert tool.attrs == {"TOOLS_Name": "index2"}
    assert "time - 0 + 1" in tool.expressions["StyledText"]
    assert comp.locked is False
