from pathlib import Path

from smartlib.core.metadata import read_json, write_json
from smartlib.review.decisions import ReviewDecisionService


def _review(base: Path, version: str) -> Path:
    path = base / version / "review.json"
    write_json(path, {"version": version, "movie": "review.mov"})
    (path.parent / "review.mov").write_text("movie", encoding="utf-8")
    (path.parent / "source_manifest.json").write_text("{}", encoding="utf-8")
    return path


def test_approved_pointer_can_target_older_than_latest(tmp_path: Path):
    v001 = _review(tmp_path, "v001")
    _review(tmp_path, "v002")
    write_json(tmp_path / "latest.json", {"version": "v002", "path": "v002/review.json"})

    record = ReviewDecisionService().decide(v001, "APPROVED", author="reviewer")

    approved = read_json(tmp_path / "approved.json", {})
    assert record.version == "v001"
    assert approved["version"] == "v001"
    assert ReviewDecisionService.approved_review(tmp_path) == v001.resolve()
    assert len(list((tmp_path / "decisions").glob("*.json"))) == 1


def test_request_changes_only_clears_same_approved_version(tmp_path: Path):
    v001 = _review(tmp_path, "v001")
    v002 = _review(tmp_path, "v002")
    service = ReviewDecisionService()
    service.decide(v001, "APPROVED", author="reviewer")

    service.decide(v002, "CHANGES_REQUESTED", author="reviewer")
    assert ReviewDecisionService.approved_review(tmp_path) == v001.resolve()

    service.decide(v001, "REVOKED", author="reviewer")
    assert ReviewDecisionService.approved_review(tmp_path) is None
