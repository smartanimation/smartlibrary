import sys
from types import SimpleNamespace

from smartlib.apps.review_build_manager.window import ReviewBuildManagerWindow, QtCore


class StartupHarness(QtCore.QObject):
    _worker_process_error = ReviewBuildManagerWindow._worker_process_error
    _worker_started = ReviewBuildManagerWindow._worker_started
    _worker_finished = ReviewBuildManagerWindow._worker_finished

    def __init__(self, process):
        super().__init__()
        self.worker_process = process
        self.active_job = {"state": "STARTING", "stderr": ""}
        self.job = self.active_job
        self.results = []

    def _update_queue_row(self, job):
        pass

    def _poll_active_job(self):
        pass

    def _finish_active_job(self, success):
        self.results.append(success)
        self.job["state"] = "COMPLETE" if success else "FAILED"
        self.active_job = None
        self.worker_process = None


def test_actual_qprocess_failed_start_finishes_job(tmp_path):
    app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    process = QtCore.QProcess()
    harness = StartupHarness(process)
    process.errorOccurred.connect(harness._worker_process_error)
    process.finished.connect(harness._worker_finished)
    process.setProgram(str(tmp_path / "nonexistent-mayapy.exe"))
    process.start()
    assert not process.waitForStarted(5000)
    app.processEvents()
    assert harness.results == [False]
    assert harness.job["state"] == "FAILED"
    assert "mayapy failed to start:" in harness.job["message"]
    assert process.errorString() in harness.job["stderr"]


def test_actual_qprocess_success_reports_started_and_finishes():
    app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    process = QtCore.QProcess()
    harness = StartupHarness(process)
    process.errorOccurred.connect(harness._worker_process_error)
    process.started.connect(harness._worker_started)
    process.finished.connect(harness._worker_finished)
    process.setProgram(sys.executable)
    process.setArguments(["-c", "pass"])
    process.start()
    assert process.waitForStarted(5000)
    assert "Initialize Maya (mayapy PID" in harness.job["task"]
    assert process.waitForFinished(5000)
    app.processEvents()
    assert harness.results == [True]


def test_old_process_signals_do_not_finish_next_job():
    process = object()
    harness = SimpleNamespace(
        sender=lambda: object(), worker_process=process,
        active_job={"state": "STARTING"},
    )
    ReviewBuildManagerWindow._worker_process_error(harness, QtCore.QProcess.FailedToStart)
    ReviewBuildManagerWindow._worker_finished(harness, 1, None)
    assert harness.active_job["state"] == "STARTING"


def test_launch_details_include_program_directory_and_argument_length():
    process = SimpleNamespace(
        program=lambda: "mayapy.exe", arguments=lambda: ["--construct-json", "{}"],
        workingDirectory=lambda: "workspace",
    )
    details = ReviewBuildManagerWindow._worker_launch_details(process)
    assert "Program: mayapy.exe" in details
    assert "Working directory: workspace" in details
    assert "Command line characters (quoted):" in details
