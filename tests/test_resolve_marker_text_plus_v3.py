from smartlib.dcc.resolve.marker_text_plus_v3 import _merge_over_media


class Output:
    def __init__(self, tool): self.tool = tool
    def GetTool(self): return self.tool


class Input:
    def __init__(self, output=None): self.output = output
    def GetConnectedOutput(self): return self.output


class Tool:
    def __init__(self, name, reg_id):
        self.Name, self.reg_id = name, reg_id
        self.output, self.input = Output(self), Input()
        self.connections = {}
    def GetAttrs(self): return {"TOOLS_RegID": self.reg_id}
    def SetAttrs(self, attrs): self.Name = attrs["TOOLS_Name"]
    def FindMainInput(self, _index): return self.input
    def FindMainOutput(self, _index): return self.output
    def ConnectInput(self, name, output): self.connections[name] = output; self.input.output = output; return True


def test_merge_is_inserted_between_media_and_media_out():
    media, text, media_out = Tool("MediaIn1", "MediaIn"), Tool("MarkerTextPlus", "TextPlus"), Tool("MediaOut1", "MediaOut")
    media_out.input.output = media.output
    class Comp:
        def __init__(self): self.merge = None
        def FindTool(self, name): return media_out if name == "MediaOut1" else None
        def AddTool(self, name): assert name == "Merge"; self.merge = Tool("Merge1", "Merge"); return self.merge
    comp = Comp()
    merge = _merge_over_media(comp, text)
    assert merge.connections["Background"] is media.output
    assert merge.connections["Foreground"] is text.output
    assert media_out.connections["Input"] is merge.output
