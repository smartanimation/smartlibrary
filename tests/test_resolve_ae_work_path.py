from __future__ import annotations

import json

from scripts.resolve_ae_work_path import main


def test_resolve_ae_work_path_uses_project_paths(tmp_path, capsys):
    config_dir = tmp_path / "config" / "ELCD"
    config_dir.mkdir(parents=True)
    project_root = tmp_path / "projects" / "ELCD"
    (config_dir / "templates_base.yml").write_text(
        "\n".join(
            [
                "anchors:",
                '  project_name: "ELCD"',
                f'  project_root: "{project_root.as_posix()}"',
            ]
        ),
        encoding="utf-8",
    )
    (config_dir / "templates_shots.yml").write_text(
        "\n".join(
            [
                "templates:",
                '  shot_root: "{project_root}/shots/{episode}/{sequence}/{shot}"',
                '  shot_work: "{project_root}/resolved_work/{episode}/{sequence}/{shot}/{department}/{dcc}"',
            ]
        ),
        encoding="utf-8",
    )

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
            "--dcc",
            "ae",
            "--option",
            "main",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["work_root"] == (
        project_root / "resolved_work" / "ep02" / "s027" / "c001" / "anim" / "ae" / "main"
    ).as_posix()
