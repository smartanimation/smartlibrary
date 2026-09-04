from types import SimpleNamespace

import pytest

from scripts.config_creator import ConfigCreatorApp


class _Text:
    def __init__(self, value):
        self.value = value

    def toPlainText(self):
        return self.value

    def text(self):
        return self.value


class _Choice:
    def __init__(self, value):
        self.value = value

    def currentText(self):
        return self.value


class _Number:
    def __init__(self, value):
        self.number = value

    def value(self):
        return self.number


def _review_harness(renderer="maya_hardware2"):
    return SimpleNamespace(
        review_profiles_edit=_Text(
            "fast_default:\n"
            "  stage: FAST\n"
            f"  renderer: {renderer}\n"
            "  image_format: png\n"
            "work_default:\n"
            "  extends: fast_default\n"
            "  stage: WORK\n"
            "  renderer: maya_playblast\n"
        ),
        delivery_profiles_edit=_Text(
            "internal:\n  review_profile: fast_default\n  container: mov\n"
        ),
        default_review_profile_combo=_Choice("fast_default"),
        missing_precomp_policy_combo=_Choice("allow_project_default"),
        default_precomp_edit=_Text("{project_root}/templates/review/base_comp.aep"),
        review_success_days=_Number(3),
        review_failed_days=_Number(30),
        review_logs_days=_Number(90),
    )


def test_review_config_accepts_fast_hardware_and_saves_default_profile():
    result = ConfigCreatorApp._review_config_from_ui(_review_harness())

    assert result["default_review_profile"] == "fast_default"
    assert result["review_profiles"]["fast_default"]["stage"] == "FAST"
    assert result["review_profiles"]["fast_default"]["renderer"] == "maya_hardware2"


def test_review_config_rejects_unknown_renderer():
    with pytest.raises(ValueError, match="unsupported renderer"):
        ConfigCreatorApp._review_config_from_ui(_review_harness("viewport_magic"))
