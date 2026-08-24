from pathlib import Path

from smartlib.core.metadata import write_json
from smartlib.core.resolver import SmartPathResolver


def test_resolve_shot_latest_from_latest_json(tmp_path: Path):
    root = tmp_path / "project"
    base = root / "production" / "shots" / "ep001" / "seq010" / "sh0010" / "data" / "layout" / "proxy" / "KUMA_main" / "cache"
    version_dir = base / "v012"
    version_dir.mkdir(parents=True)
    target = version_dir / "KUMA_main.usd"
    target.write_text("#usda 1.0\n", encoding="utf-8")
    write_json(base / "latest.json", {"version": "v012", "path": "v012/KUMA_main.usd"})

    result = SmartPathResolver(root).resolve(
        "shot://ep001/seq010/sh0010/data/layout/proxy/KUMA_main/cache/latest/KUMA_main.usd"
    )

    assert result.version == "v012"
    assert result.resolved_path == target
    assert result.exists


def test_resolve_sequence_path(tmp_path: Path):
    root = tmp_path / "project"
    path = root / "production" / "sequences" / "ep001" / "seq010" / "publish" / "layout" / "v003" / "sequence_layout.usda"
    path.parent.mkdir(parents=True)
    path.write_text("#usda 1.0\n", encoding="utf-8")

    result = SmartPathResolver(root).resolve("sequence://ep001/seq010/publish/layout/v003/sequence_layout.usda")

    assert result.version == "v003"
    assert result.resolved_path == path
    assert result.exists
