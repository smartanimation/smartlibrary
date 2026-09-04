import json

from smartlib.dcc.maya import camera_portable


def test_update_publish_registers_portable_files_atomically(tmp_path):
    snapshot = tmp_path / "camera.json"
    snapshot.write_text(json.dumps({"files": {"ma": "primary_cam.ma"}}), encoding="utf-8")
    publish = tmp_path / "publish.json"
    publish.write_text(json.dumps({"files": {"camera": "camera.json", "ma": "primary_cam.ma"}}), encoding="utf-8")

    camera_portable.update_publish(
        snapshot,
        status="complete",
        files={"fbx": "primary_cam.fbx", "usd": "primary_cam.usd"},
    )

    camera_data = json.loads(snapshot.read_text(encoding="utf-8"))
    publish_data = json.loads(publish.read_text(encoding="utf-8"))
    assert camera_data["portable_export"] == {
        "status": "complete",
        "camera_name": "primary_cam",
        "files": {"fbx": "primary_cam.fbx", "usd": "primary_cam.usd"},
    }
    assert camera_data["files"]["usd"] == "primary_cam.usd"
    assert publish_data["files"]["fbx"] == "primary_cam.fbx"
    assert not list(tmp_path.glob("*.tmp"))


def test_update_publish_records_failure_without_exchange_files(tmp_path):
    snapshot = tmp_path / "camera.json"
    snapshot.write_text("{}", encoding="utf-8")
    (tmp_path / "publish.json").write_text("{}", encoding="utf-8")

    camera_portable.update_publish(snapshot, status="failed", error="USD unavailable")

    data = json.loads(snapshot.read_text(encoding="utf-8"))
    assert data["portable_export"]["status"] == "failed"
    assert data["portable_export"]["error"] == "USD unavailable"
    assert "files" not in data
