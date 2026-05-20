from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smartlib.core.metadata import read_json, write_json


@dataclass(frozen=True)
class FFMpegCommandResult:
    command: list[str]
    output: Path
    executed: bool = False


def ffmpeg_executable(pipeline_root: str | Path) -> Path:
    exe = Path(pipeline_root) / "tools" / "ffmpeg" / "ffmpeg.exe"
    if not exe.exists():
        raise FileNotFoundError(f"ffmpeg.exe was not found: {exe}")
    return exe


def compose_layer_movies_from_review_json(
    review_json: str | Path,
    pipeline_root: str | Path,
    *,
    execute: bool = False,
) -> list[FFMpegCommandResult]:
    review_json = Path(review_json)
    version_dir = review_json.parent
    data = read_json(review_json, {})
    ffmpeg = ffmpeg_executable(pipeline_root)
    fps = int(data.get("fps") or 24)
    results = []

    for layer_name in (data.get("ae") or {}).get("layer_order", []):
        layer = (data.get("layers") or {}).get(layer_name) or {}
        beauty = _beauty_result(layer)
        if not beauty:
            continue
        first_file = str(beauty.get("first_file") or "")
        if not first_file:
            continue
        first_path = version_dir / first_file
        if not first_path.exists():
            continue
        pattern = _ffmpeg_sequence_pattern(first_path)
        output = first_path.parent / "beauty.mov"
        command = [
            str(ffmpeg),
            "-y",
            "-framerate",
            str(fps),
            "-start_number",
            str(_frame_number(first_path)),
            "-i",
            str(pattern),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ]
        if execute:
            subprocess.run(command, check=True)
        results.append(FFMpegCommandResult(command=command, output=output, executed=execute))

    if execute and results:
        data["ffmpeg_layer_movies"] = {
            result.output.parent.parent.name: str(result.output.relative_to(version_dir).as_posix())
            for result in results
        }
        write_json(review_json, data)
    return results


def _beauty_result(layer: dict[str, Any]) -> dict[str, Any] | None:
    actual = layer.get("actual_outputs") or {}
    beauty = actual.get("beauty")
    return beauty if isinstance(beauty, dict) else None


def _ffmpeg_sequence_pattern(first_path: Path) -> Path:
    name = first_path.name
    frame = f"{_frame_number(first_path):04d}"
    return first_path.with_name(name.replace(frame, "%04d"))


def _frame_number(path: Path) -> int:
    stem = path.stem
    token = stem.rsplit("_", 1)[-1]
    return int(token)
