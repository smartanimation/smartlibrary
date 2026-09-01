from __future__ import annotations

import json

from scripts.resolve_ae_review_movie_path import main


def test_resolve_ae_review_movie_path_uses_project_paths(tmp_path, capsys):
    config_dir = tmp_path / "config" / "ELCD"
    config_dir.mkdir(parents=True)
    project_root = tmp_path / "projects" / "ELCD"
    (config_dir / "templates_base.yml").write_text(
        "\n".join(
            [
                "anchors:",
                '  project_name: "ELCD"',
                f'  project_root: "{project_root.as_posix()}"',
                "shot_dept_partitions:",
                '  default: "cg"',
            ]
        ),
        encoding="utf-8",
    )
    (config_dir / "templates_shots.yml").write_text(
        "\n".join(
            [
                "templates:",
                '  workspace_root: "{project_root}/workspace"',
                '  shot_workspace_root: "{workspace_root}/{workspace_partition}/shots/{episode}/{sequence}/{shot}"',
                '  shot_review_root: "{shot_workspace_root}/review"',
                '  shot_review_movie: "{shot_review_root}/{department}/mov"',
            ]
        ),
        encoding="utf-8",
    )

    filename = "ELCD_ep02_s027_c001_compTemp_v001_t001.mov"
    assert main(
        [
            "--config-dir",
            str(config_dir),
            "--episode",
            "ep02",
            "--sequence",
            "s027",
            "--shot",
            "c001",
            "--department",
            "anim",
            "--filename",
            filename,
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    expected_dir = (
        project_root
        / "workspace/cg/shots/ep02/s027/c001/review/anim/mov"
    )
    assert payload["directory"] == expected_dir.as_posix()
    assert payload["path"] == (expected_dir / filename).as_posix()
