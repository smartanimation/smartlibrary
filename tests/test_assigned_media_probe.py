import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from smartlib.editorial import assigned_media


def probe(monkeypatch, stdout, stderr=b"", returncode=0):
    run = Mock(return_value=SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode))
    monkeypatch.setattr(assigned_media.subprocess, "run", run)
    service = SimpleNamespace(_ffmpeg_path=lambda: Path("ffmpeg.exe"))
    return service, run


def test_probe_reads_utf8_without_locale_decoder(monkeypatch):
    payload = {"streams": [{"codec_type": "video", "tags": {"title": "編集動画"}}]}
    service, run = probe(monkeypatch, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    data, video = assigned_media.probe_movie(service, Path("offline.mov"))
    assert data == payload
    assert video["tags"]["title"] == "編集動画"
    assert not run.call_args.kwargs.get("text", False)


@pytest.mark.parametrize("output", [None, b""])
def test_missing_output_is_actionable(monkeypatch, output):
    service, _ = probe(monkeypatch, output)
    with pytest.raises(ValueError, match="ffprobe failed.*offline.mov.*No JSON output"):
        assigned_media.probe_movie(service, Path("offline.mov"))


def test_nonzero_exit_reports_stderr(monkeypatch):
    service, _ = probe(monkeypatch, b"", b"Invalid input", 1)
    with pytest.raises(ValueError, match="exit 1.*Invalid input"):
        assigned_media.probe_movie(service, Path("offline.mov"))


@pytest.mark.parametrize("output", [b"invalid", b"\xff", b"null", b"{}"])
def test_invalid_output_is_actionable(monkeypatch, output):
    service, _ = probe(monkeypatch, output)
    with pytest.raises(ValueError, match="ffprobe returned"):
        assigned_media.probe_movie(service, Path("offline.mov"))
