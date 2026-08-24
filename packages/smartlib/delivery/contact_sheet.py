from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def generate_contact_sheet(
    movies: list[Path],
    output: str | Path,
    *,
    ffmpeg: str = "",
) -> tuple[Path | None, str]:
    """Create first/middle/last representative frames for each review movie."""

    executable = ffmpeg or shutil.which("ffmpeg") or ""
    if not executable:
        return None, "ffmpeg was not found"
    sources = [path for path in movies if path.is_file()]
    if not sources:
        return None, "no review movies were found"
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    probe = _ffprobe_for(executable)
    if not probe:
        return None, "ffprobe was not found"
    command = [executable, "-y"]
    durations = []
    for source in sources:
        duration = _duration(probe, source)
        if duration <= 0:
            return None, f"could not determine movie duration: {source}"
        durations.append(duration)
        for timestamp in (0.0, duration / 2.0, max(0.0, duration - 0.05)):
            command.extend(["-ss", f"{timestamp:.6f}", "-i", str(source)])
    filters = []
    strips = []
    for index in range(len(sources)):
        labels = []
        for sample in range(3):
            label = f"f{index}_{sample}"
            input_index = index * 3 + sample
            filters.append(f"[{input_index}:v]scale=480:-1,setsar=1[{label}]")
            labels.append(f"[{label}]")
        strip = f"strip{index}"
        filters.append(f"{''.join(labels)}hstack=inputs=3[{strip}]")
        strips.append(f"[{strip}]")
    filters.append(f"{''.join(strips)}vstack=inputs={len(strips)}[out]")
    command.extend(["-filter_complex", ";".join(filters), "-map", "[out]", "-frames:v", "1", str(target)])
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        return None, (result.stderr or result.stdout or "ffmpeg failed").strip()
    return target, ""


def _ffprobe_for(ffmpeg: str) -> str:
    executable = Path(ffmpeg)
    sibling = executable.with_name("ffprobe" + executable.suffix)
    if sibling.is_file():
        return str(sibling)
    return shutil.which("ffprobe") or ""


def _duration(ffprobe: str, movie: Path) -> float:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(movie)],
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        return float(result.stdout.strip()) if result.returncode == 0 else 0.0
    except ValueError:
        return 0.0
