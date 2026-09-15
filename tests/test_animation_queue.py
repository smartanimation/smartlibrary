from types import SimpleNamespace
from pathlib import Path
from PySide6 import QtCore
from smartlib.apps.review_build_manager.window import ReviewBuildManagerWindow
from smartlib.apps.review_build_manager.composition_ui import CompositionSnapshotMixin
from smartlib.core.metadata import write_json


def test_animation_job_waits_for_active_review_job():
    animation = {"kind": "animation_publish"}
    window = SimpleNamespace(active_job={"kind": "review"}, pending_jobs=[animation])
    ReviewBuildManagerWindow._start_next_job(window)
    assert window.pending_jobs == [animation]


def test_animation_dispatch_uses_common_queue():
    job = {"kind": "animation_publish", "elapsed": QtCore.QElapsedTimer()}
    launched = []
    window = SimpleNamespace(active_job=None, pending_jobs=[job], _start_animation_job=launched.append)
    ReviewBuildManagerWindow._start_next_job(window)
    assert window.active_job is job
    assert launched == [job]
    assert not window.pending_jobs


def test_publish_failure_is_returned_to_queue(tmp_path):
    result = write_json(tmp_path / "result.json", {"ok": True, "manifest": "build.json"})
    job = {"result_file": str(result), "identity": ("ep", "seq", "shot")}
    def fail(*args):
        raise ValueError("Invalid REND receipt")
    window = SimpleNamespace(_update_queue_row=lambda job: None,
        _composition_service=lambda: SimpleNamespace(publish_build=fail))
    assert not CompositionSnapshotMixin._complete_animation_job(window, job, True)
    assert job["message"] == "Invalid REND receipt"


def test_worker_error_is_retained_in_queue(tmp_path):
    result = write_json(tmp_path / "result.json", {"ok": False, "error": "REND Asset Context not found: DLI"})
    job = {"result_file": str(result)}
    assert not CompositionSnapshotMixin._complete_animation_job(SimpleNamespace(), job, False)
    assert job["message"] == "REND Asset Context not found: DLI"


def test_stage_profile_uses_published_context_label(tmp_path, monkeypatch):
    from smartlib.apps.asset_manager.context import AssetContextService
    from smartlib.core.asset_publish_resolver import AssetPublishResolver
    monkeypatch.setattr(AssetContextService, "__init__", lambda self, config: None)
    monkeypatch.setattr(AssetContextService, "stage_context_for_asset", lambda *args: "CHAR_REND")
    monkeypatch.setattr(AssetContextService, "load_context", lambda *args: {"profile_labels": {"CHAR_REND": "REND"}})
    variant = tmp_path / "character" / "main" / "DLI" / "default"
    published = variant / "publish" / "asset" / "rend" / "v003" / "DLI.mb"
    published.parent.mkdir(parents=True)
    published.write_bytes(b"fixture")
    assert AssetPublishResolver(None).resolve_context(variant, "REND") == published
