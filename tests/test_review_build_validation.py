from pathlib import Path

from smartlib.apps.review_build_manager import worker


class _FakeCmds:
    def __init__(self):
        self.queries = []

    def referenceQuery(self, path, isLoaded=False):
        self.queries.append(path)
        return True

    def unknownPlugin(self, **_kwargs):
        return []

    def ls(self, **_kwargs):
        return []


class _PluginCmds:
    def __init__(self, failing=()):
        self.loaded = set()
        self.failing = set(failing)

    def pluginInfo(self, name, query=False, loaded=False, version=False):
        if version:
            return "1.0" if name in self.loaded else ""
        return name in self.loaded

    def loadPlugin(self, name, quiet=False):
        if name in self.failing:
            raise RuntimeError("plug-in was not found")
        self.loaded.add(name)


class _PluginConfig:
    def load(self, _name):
        return {
            "plugin_profiles": {
                "core": {"required": [], "optional": ["fbxmaya"]},
                "work_stage": {"required": ["clgIKNode"], "optional": []},
            }
        }


class _ColorCmds:
    def __init__(self):
        self.values = {
            "defaultColorMgtGlobals.configFileEnabled": True,
            "defaultColorMgtGlobals.configFilePath": "<MAYA_RESOURCES>/legacy/config.ocio",
        }

    def ls(self, type=None):
        return ["defaultColorMgtGlobals"] if type == "colorManagementGlobals" else []

    def objExists(self, name):
        return name in self.values

    def setAttr(self, name, value, **_kwargs):
        self.values[name] = value


def test_construct_validation_does_not_reference_query_usd_proxy(tmp_path: Path):
    scene = tmp_path / "shot.ma"
    maya_reference = tmp_path / "asset.mb"
    usd_proxy = tmp_path / "context.usda"
    for path in (scene, maya_reference, usd_proxy):
        path.write_text("", encoding="utf-8")
    cmds = _FakeCmds()

    results = worker._construct_scene_validation(
        cmds, scene, [str(maya_reference), str(usd_proxy)]
    )

    assert cmds.queries == [str(maya_reference)]
    assert not [row for row in results if row["code"] == "UNLOADED_REFERENCES"]


def test_build_plugin_profile_loads_core_and_work_stage_plugins():
    cmds = _PluginCmds()

    report = worker._load_build_plugins(cmds, _PluginConfig(), "WORK STAGE")

    assert report["profile"] == "work_stage"
    assert cmds.loaded == {"fbxmaya", "clgIKNode"}
    assert all(row["loaded"] for row in report["plugins"])


def test_required_build_plugin_failure_blocks_worker():
    cmds = _PluginCmds(failing={"clgIKNode"})

    try:
        worker._load_build_plugins(cmds, _PluginConfig(), "WORK STAGE")
    except RuntimeError as exc:
        assert "clgIKNode" in str(exc)
    else:
        raise AssertionError("Required plug-in failure did not block the build")


def test_syncolor_build_removes_scene_ocio_override(monkeypatch):
    monkeypatch.setenv("MAYA_COLOR_MANAGEMENT_SYNCOLOR", "1")
    cmds = _ColorCmds()

    result = worker._normalize_color_management_for_save(cmds)

    assert result == {"mode": "syncolor", "changed": True}
    assert cmds.values["defaultColorMgtGlobals.configFileEnabled"] is False
    assert cmds.values["defaultColorMgtGlobals.configFilePath"] == ""
