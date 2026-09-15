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


def test_output_is_a_job_queue_detail_tab():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = ReviewBuildManagerWindow.__new__(ReviewBuildManagerWindow)
    QtWidgets.QMainWindow.__init__(window)
    window.service = SimpleNamespace(stage_profiles=lambda: ["WORK"])

    page = window._build_job_queue_page()

    assert window.job_queue_detail_tabs.count() == 2
    assert window.job_queue_detail_tabs.tabText(0) == "Job Details / Error Details"
    assert window.job_queue_detail_tabs.tabText(1) == "Output"
    assert window.job_queue_detail_tabs.widget(1) is window.output_page
    assert window.output_table.parentWidget() is window.output_page
    page.deleteLater()
    window.deleteLater()
    app.processEvents()


def test_job_selection_switches_output_to_the_job_shot():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = ReviewBuildManagerWindow.__new__(ReviewBuildManagerWindow)
    QtWidgets.QMainWindow.__init__(window)
    window.service = SimpleNamespace(
        stage_profiles=lambda: ["WORK"],
        list_constructs=lambda *_args: [],
    )
    window.department_combo = QtWidgets.QComboBox()
    window.department_combo.addItem("anim")
    window.task_combo = QtWidgets.QComboBox()
    window.task_combo.addItem("preComp")
    page = window._build_job_queue_page()
    identity = SimpleNamespace(episode="ep02", sequence="s027", shot="c002")
    output = SimpleNamespace(
        version="v003",
        state="COMPLETE",
        updated="2026-09-07",
        movie="D:/review/c002.mov",
        directory="D:/review",
    )
    window.rows = [
        SimpleNamespace(
            identity=identity,
            state="READY",
            source_version="v002",
            output_label="v003",
            message="",
            outputs=[output],
        )
    ]
    window.queue_jobs = [
        {
            "id": "#0001",
            "identity": ("ep02", "s027", "c002"),
            "scope": "shot",
            "state": "COMPLETE",
            "task": "Complete",
            "department": "anim",
            "task_name": "preComp",
        }
    ]
    window.queue_table.insertRow(0)
    window.queue_table.selectRow(0)
    window._show_selected_job_details()

    assert "ep02/s027/c002" in window.detail_title.text()
    assert window.output_table.rowCount() == 1
    assert window.output_table.item(0, 0).text() == "v003"
    page.deleteLater()
    window.deleteLater()
    app.processEvents()
