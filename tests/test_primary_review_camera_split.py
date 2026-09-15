import hashlib
import json
from pathlib import Path

import pytest

from smartlib.core.camera_package import camera_package_info
from smartlib.dcc.maya import primary_camera, review_camera_rules


def write_json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_review_rules_reference_primary_by_relative_path_and_fingerprint(tmp_path):
    primary = write_json(
        tmp_path / "camera" / "main" / "main" / "v003" / "camera.json",
        {"schema": primary_camera.SCHEMA, "camera": "cam", "version": "v003"},
    )
    review_path = tmp_path / "camera" / "review" / "main" / "v002" / "camera.json"
    data = {
        "schema": review_camera_rules.SCHEMA,
        "primary_camera": {
            "path": str(primary),
            "version": "v003",
            "camera": "cam",
            "sha256": hashlib.sha256(primary.read_bytes()).hexdigest(),
        },
        "reference_resolution": [1920, 1080],
        "rows": [],
    }

    portable = review_camera_rules.make_reference_relative(data, review_path)

    assert not Path(portable["primary_camera"]["path"]).is_absolute()
    assert review_camera_rules.resolve_primary_path(portable, review_path) == primary.resolve()


def test_review_rules_are_identified_without_embedded_primary_files(tmp_path):
    path = write_json(
        tmp_path / "camera.json",
        {
            "schema": review_camera_rules.SCHEMA,
            "primary_camera": {"camera": "cam", "version": "v004", "path": "../../v004/camera.json"},
            "reference_resolution": [1280, 720],
            "rows": [{
                "layer": "CHA", "camera": "smartCam_CHA", "width": 1280,
                "height": 720, "start": 1001, "end": 1100,
                "version": 1, "take": 1, "camera_rule": {"mode": "shared"},
            }],
        },
    )

    info = camera_package_info(path)

    assert info["kind"] == "Review Camera Rules"
    assert info["primary"] == "cam"
    assert "Primary Publish: v004" in info["summary"]
    assert "CHA: smartCam_CHA" in info["summary"]


def test_primary_schema_is_supported_by_build_dispatch(monkeypatch):
    from smartlib.dcc.maya import camera_publish

    monkeypatch.setattr(
        primary_camera, "restore", lambda data, **kwargs: (data["camera"], kwargs["provenance"])
    )
    result = camera_publish.restore_package(
        {"schema": primary_camera.SCHEMA, "camera": "cam"},
        cmds=object(), provenance="primary/camera.json",
    )
    assert result == ("cam", "primary/camera.json")
