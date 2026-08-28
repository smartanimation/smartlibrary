from types import SimpleNamespace

from smartlib.apps.launcher.main import software_process_env_vars
from smartlib.apps.review_build_manager.service import ReviewBuildManagerService


def test_launcher_merges_legacy_maya_settings_with_explicit_environment():
    values = software_process_env_vars(
        {
            "MAYA_COLOR_MANAGEMENT_SYNCOLOR": 1,
            "env_vars": {
                "MAYA_UI_LANGUAGE": "en_US",
                "MAYA_COLOR_MANAGEMENT_SYNCOLOR": 0,
            },
            "path": "maya.exe",
        }
    )

    assert values == {
        "MAYA_COLOR_MANAGEMENT_SYNCOLOR": 0,
        "MAYA_UI_LANGUAGE": "en_US",
    }


def test_review_build_worker_receives_legacy_maya_settings():
    service = object.__new__(ReviewBuildManagerService)
    service.project_config = SimpleNamespace()
    service.maya_software_config = lambda: {
        "MAYA_COLOR_MANAGEMENT_SYNCOLOR": 1,
        "env_vars": {"MAYA_UI_LANGUAGE": "en_US"},
        "paths": {},
    }

    env_vars, paths = service.maya_process_environment()

    assert env_vars["MAYA_COLOR_MANAGEMENT_SYNCOLOR"] == "1"
    assert env_vars["MAYA_UI_LANGUAGE"] == "en_US"
    assert paths == {}
