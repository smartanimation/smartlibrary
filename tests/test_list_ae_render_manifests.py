from pathlib import Path
from types import SimpleNamespace

from scripts.list_ae_render_manifests import _latest_output_rows


def test_latest_output_rows_expand_resolved_receipt_paths() -> None:
    class Service:
        def latest_preview_render_outputs(self, identity, *, department):
            assert identity.shot == "c003"
            assert department == "anim"
            return {
                "CHA": {
                    "version": "v001",
                    "take": "t02",
                    "output_dir": "D:/shot/render/anim/layers/CHA/v001/t02",
                    "pattern": "shot_CHA_v001_t02_####.png",
                    "first_file": "shot_CHA_v001_t02_0632.png",
                }
            }

    rows = _latest_output_rows(Service(), SimpleNamespace(shot="c003"), "anim")

    assert rows == [
        {
            "id": "CHA",
            "name": "CHA",
            "layer": "CHA",
            "version": "v001",
            "take": "t02",
            "output_dir": "D:/shot/render/anim/layers/CHA/v001/t02",
            "pattern": "shot_CHA_v001_t02_####.png",
            "first_file": "shot_CHA_v001_t02_0632.png",
            "outputPath": "D:/shot/render/anim/layers/CHA/v001/t02/shot_CHA_v001_t02_####.png",
            "first_frame_file": "D:/shot/render/anim/layers/CHA/v001/t02/shot_CHA_v001_t02_0632.png",
        }
    ]


def test_latest_output_rows_preserve_absolute_receipt_paths() -> None:
    class Service:
        def latest_preview_render_outputs(self, _identity, *, department):
            return {
                "BGA": {
                    "output_dir": "D:/resolved",
                    "pattern": "E:/cache/BGA_v002_t003_####.png",
                    "first_file": "E:/cache/BGA_v002_t003_1001.png",
                }
            }

    row = _latest_output_rows(Service(), SimpleNamespace(shot="c003"), "anim")[0]

    assert Path(row["outputPath"]).as_posix() == "E:/cache/BGA_v002_t003_####.png"
    assert Path(row["first_frame_file"]).as_posix() == "E:/cache/BGA_v002_t003_1001.png"
