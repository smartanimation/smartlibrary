import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from smartlib.apps.review_build_manager.window import (
    QtWidgets,
    ReviewBuildManagerWindow,
    build_content_state_color,
)


def test_planned_snapshot_uses_full_size_input_and_layer_tabs():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = ReviewBuildManagerWindow.__new__(ReviewBuildManagerWindow)
    QtWidgets.QMainWindow.__init__(window)
    window.service = SimpleNamespace(
        review_profile_ids=lambda: ["fast_default", "work_default"],
        default_review_profile_id=lambda: "fast_default",
        delivery_profile_ids=lambda: ["internal"],
    )

    page = window._build_planned_snapshot_page()

    assert window.planned_snapshot_tabs.count() == 2
    assert window.planned_snapshot_tabs.tabText(0) == "Resolved Inputs"
    assert window.planned_snapshot_tabs.tabText(1) == "Review Layers"
    assert window.planned_inputs_table.parentWidget() is window.planned_snapshot_tabs.widget(0)
    assert window.planned_layers_table.parentWidget() is window.planned_snapshot_tabs.widget(1)
    page.deleteLater()
    window.deleteLater()
    app.processEvents()


def test_planned_state_colors_match_build_contents():
    assert build_content_state_color("READY") == "#80bd72"
    assert build_content_state_color("UPDATE AVAILABLE") == "#f2ae30"
    assert build_content_state_color("EXCLUDED") == "#999999"
    assert build_content_state_color("MISSING") == "#ef665d"
