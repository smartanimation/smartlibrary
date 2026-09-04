from pathlib import Path

from smartlib.apps.sequence_cast_publisher.maya_analysis import sample_frames
from smartlib.apps.sequence_cast_publisher.service import SequenceCastPublisherService
from smartlib.apps.shot_manager import SequenceIdentity, ShotIdentity


class FakeShots:
    def __init__(self, root: Path):
        self.root = root
        self.written = None
        self.published = None

    def load_sequence_cast(self, episode, sequence):
        return {
            "cast": {
                "YOU": {"asset": "YOU", "namespace": "char_YOU", "category": "character"},
                "chair": {"asset": "chair_A", "namespace": "prop_chairA", "category": "prop"},
            }
        }

    def shot_publish_root(self, identity):
        return self.root / "shots" / identity.episode / identity.sequence / identity.shot / "publish"

    def build_cast_data(self, rows):
        return {"schema": "smartpipeline.cast.v3", "cast": {row["cast_key"]: row for row in rows}}

    def write_cast(self, identity, data):
        self.written = (identity, data)

    def publish_shot_cast_from_sequence(self, identity, comment=""):
        self.published = (identity, comment)
        return self.shot_publish_root(identity) / "cast" / "main" / "v002" / "cast.json"


def _service(tmp_path):
    service = SequenceCastPublisherService.__new__(SequenceCastPublisherService)
    service.project_config = object()
    service.shots = FakeShots(tmp_path)
    return service


def test_sample_frames_cover_full_shot_range():
    assert sample_frames(100, 108, 3) == [100, 104, 108]
    assert sample_frames(100, 100, 9) == [100]


def test_candidates_and_selected_cast_publish(tmp_path):
    service = _service(tmp_path)
    sequence = SequenceIdentity("ep02", "s027")
    candidates = service.candidates(sequence)
    assert [(row.cast_key, row.namespace) for row in candidates] == [
        ("chair", "prop_chairA"),
        ("YOU", "char_YOU"),
    ]

    identity = ShotIdentity("ep02", "s027", "c001")
    result = service.publish(identity, ["YOU"], comment="camera review")
    assert result.name == "cast.json"
    assert list(service.shots.written[1]["cast"]) == ["YOU"]
    assert service.shots.published == (identity, "camera review")


def test_next_publish_version_uses_target_shot_versions(tmp_path):
    service = _service(tmp_path)
    identity = ShotIdentity("ep02", "s027", "c001")
    base = service.shots.shot_publish_root(identity) / "cast" / "main"
    (base / "v001").mkdir(parents=True)
    (base / "v003").mkdir()
    assert service.next_publish_version(identity) == "v004"
