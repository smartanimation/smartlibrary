from pathlib import Path

from smartlib.apps.smart_delivery.service import expand_sequence


def test_expand_sequence_resolves_hash_pattern(tmp_path: Path):
    for frame in (1001, 1002, 1003):
        (tmp_path / f"shot_CHA.{frame}.png").write_text(str(frame), encoding="utf-8")
    (tmp_path / "unrelated.txt").write_text("x", encoding="utf-8")

    rows = expand_sequence(str(tmp_path / "shot_CHA.####.png"))

    assert [row.name for row in rows] == [
        "shot_CHA.1001.png",
        "shot_CHA.1002.png",
        "shot_CHA.1003.png",
    ]


def test_expand_sequence_resolves_printf_pattern(tmp_path: Path):
    for frame in (278, 279):
        (tmp_path / f"CHA.{frame:04d}.png").write_text(str(frame), encoding="utf-8")

    rows = expand_sequence(str(tmp_path / "CHA.%04d.png"))

    assert [row.name for row in rows] == ["CHA.0278.png", "CHA.0279.png"]
