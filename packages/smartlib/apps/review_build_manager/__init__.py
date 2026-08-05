from smartlib.apps.review_build_manager.service import (
    ReviewBuildManagerService,
    ReviewOutput,
    ReviewShotStatus,
)
from smartlib.apps.review_build_manager.orchestrator import (
    BUILD_MODES,
    BuildValidation,
    SceneBuildOrchestrator,
    SceneBuildPlan,
)


def show(*args, **kwargs):
    from smartlib.apps.review_build_manager.window import show as show_window

    return show_window(*args, **kwargs)


def __getattr__(name):
    if name == "ReviewBuildManagerWindow":
        from smartlib.apps.review_build_manager.window import ReviewBuildManagerWindow

        return ReviewBuildManagerWindow
    raise AttributeError(name)

__all__ = [
    "ReviewBuildManagerService",
    "ReviewBuildManagerWindow",
    "ReviewOutput",
    "ReviewShotStatus",
    "BUILD_MODES",
    "BuildValidation",
    "SceneBuildOrchestrator",
    "SceneBuildPlan",
    "show",
]
