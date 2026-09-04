import json
from pathlib import Path
import subprocess
import sys

import pytest

from smartlib.apps.review_build_manager.job_request import (
    expand_job_arguments, write_job_request, SCHEMA,
)
from smartlib.apps.review_build_manager import worker


def arguments(status):
    return ["--config-dir", "project config", "--episode", "ep02",
            "--sequence", "s027", "--shot", "c002", "--output-version", "v001",
            "--status-file", str(status)]


def test_large_unicode_request_roundtrips_without_command_line_payload(tmp_path):
    status = tmp_path / "job status.json"
    snapshot = {"inputs": [{"name": "キャラ", "path": 'D:/素材/"テスト".ma',
                            "details": "x" * 100000}]}
    argv = arguments(status) + ["--planned-snapshot-json", json.dumps(snapshot, ensure_ascii=False)]
    request = write_job_request(status, argv)
    assert request.parent == status.parent
    assert not status.exists()
    assert expand_job_arguments(["--job-file", str(request)]) == argv
    assert len(subprocess.list2cmdline([sys.executable, "--job-file", str(request)])) < 32767


def test_worker_parser_receives_all_original_values(tmp_path, monkeypatch):
    argv = arguments(tmp_path / "status.json") + [
        "--construct-json", '{"components":[]}',
        "--overrides-json", '{"cast_contexts":{"JIN":"ANIM"}}',
        "--planned-snapshot-json", '{"inputs":[{"enabled":false}]}',
        "--review-cache-policy", "rebuild_all", "--scope", "sequence",
    ]
    request = write_job_request(tmp_path / "status.json", argv)
    captured = []
    monkeypatch.setattr(worker, "run", lambda args: captured.append(vars(args)) or 0)
    assert worker.main(["--job-file", str(request)]) == 0
    assert worker.main(argv) == 0
    assert captured[0] == captured[1]


def test_request_is_immutable(tmp_path):
    status = tmp_path / "job.json"
    request = write_job_request(status, ["first"])
    with pytest.raises(FileExistsError):
        write_job_request(status, ["second"])
    assert expand_job_arguments(["--job-file", str(request)]) == ["first"]


@pytest.mark.parametrize("payload", [
    {}, {"schema": SCHEMA, "arguments": [1]},
    {"schema": SCHEMA, "arguments": ["--job-file", "nested.json"]},
])
def test_invalid_request_fails_before_maya(tmp_path, payload, capsys):
    request = tmp_path / "bad.json"
    request.write_text(json.dumps(payload), encoding="utf-8")
    assert worker.main(["--job-file", str(request)]) == 1
    assert "Could not load review job request" in capsys.readouterr().err


def test_missing_request_reports_error(tmp_path, capsys):
    assert worker.main(["--job-file", str(tmp_path / "absent.json")]) == 1
    assert "Could not load review job request" in capsys.readouterr().err


def test_large_request_launches_in_real_subprocess(tmp_path):
    argv = arguments(tmp_path / "status.json") + ["--construct-json", json.dumps({"data": "x" * 100000})]
    request = write_job_request(tmp_path / "status.json", argv)
    # Exercise the real parser in a child process without starting Maya or
    # building scene/output files.
    script = (
        "from smartlib.apps.review_build_manager import worker; "
        "worker.run=lambda args: print(len(args.construct_json)) or 0; "
        "raise SystemExit(worker.main())"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, "--job-file", str(request)],
        cwd=str(Path(__file__).resolve().parents[1] / "packages"),
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert str(len(argv[-1])) in result.stdout
