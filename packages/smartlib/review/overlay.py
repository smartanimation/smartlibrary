from __future__ import annotations

import subprocess
from pathlib import Path


SCHEMA = "smartpipeline.review_overlay.v1"


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(float(seconds) * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{fraction:02d}"


def _ass_text(value) -> str:
    return str(value or "").replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def write_review_overlay_ass(overlay: dict, path: str | Path) -> Path:
    """Compile the DCC-neutral overlay contract into a deterministic ASS file."""
    if overlay.get("schema") != SCHEMA:
        raise ValueError("Unsupported Review Overlay schema.")
    width, height = [int(value) for value in overlay["resolution"]]
    fps = float(overlay["fps"])
    start, end = [int(value) for value in overlay["frame_range"]]
    duration = (end - start + 1) / fps
    static_end = _ass_time(duration)
    source = Path(str(overlay.get("source_file") or "")).name
    top_left = f"{overlay.get('project', '')}  {overlay.get('shot', '')}"
    top_right = (
        f"{overlay.get('department', '')} / {overlay.get('task', '')}"
        f"\n{overlay.get('created_at', '')}"
    )
    bottom_left = source
    events = [
        f"Dialogue: 0,0:00:00.00,{static_end},TopLeft,,0,0,0,,{_ass_text(top_left)}",
        f"Dialogue: 0,0:00:00.00,{static_end},TopRight,,0,0,0,,{_ass_text(top_right)}",
        f"Dialogue: 0,0:00:00.00,{static_end},BottomLeft,,0,0,0,,{_ass_text(bottom_left)}",
    ]
    samples = overlay.get("samples") or []
    by_frame = {int(row["frame"]): row for row in samples}
    last = samples[0] if samples else {}
    for index, frame in enumerate(range(start, end + 1)):
        last = by_frame.get(frame, last)
        camera = last.get("camera") or overlay.get("camera") or ""
        focal = float(last.get("focal_length_mm") or 0)
        horizontal = float(last.get("horizontal_fov_deg") or 0)
        vertical = float(last.get("vertical_fov_deg") or 0)
        camera_text = f"{camera}  {focal:.2f} mm  FOV {horizontal:.2f} x {vertical:.2f} deg"
        frame_text = f"FRAME {frame}"
        event_start = _ass_time(index / fps)
        event_end = _ass_time((index + 1) / fps)
        events.extend([
            f"Dialogue: 0,{event_start},{event_end},BottomRight,,0,0,0,,{_ass_text(camera_text)}",
            f"Dialogue: 0,{event_start},{event_end},BottomCenter,,0,0,0,,{frame_text}",
        ])
    font_size = max(16, round(height / 34))
    margin = max(12, round(height * 0.025))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: TopLeft,Arial,{font_size},&H00FFFFFF,&H00FFFFFF,&H60383838,&H60383838,0,0,0,0,100,100,0,0,3,1,0,7,{margin},{margin},{margin},1
Style: TopRight,Arial,{font_size},&H00FFFFFF,&H00FFFFFF,&H60383838,&H60383838,0,0,0,0,100,100,0,0,3,1,0,9,{margin},{margin},{margin},1
Style: BottomLeft,Arial,{font_size},&H00FFFFFF,&H00FFFFFF,&H60383838,&H60383838,0,0,0,0,100,100,0,0,3,1,0,1,{margin},{margin},{margin},1
Style: BottomCenter,Arial,{font_size},&H00FFFFFF,&H00FFFFFF,&H60383838,&H60383838,-1,0,0,0,100,100,0,0,3,1,0,2,{margin},{margin},{margin},1
Style: BottomRight,Arial,{font_size},&H00FFFFFF,&H00FFFFFF,&H60383838,&H60383838,0,0,0,0,100,100,0,0,3,1,0,3,{margin},{margin},{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")
    return target


def overlay_review_movie(*, clean_movie: str | Path, overlay: dict,
                         overlay_json: str | Path, mov_path: str | Path,
                         ffmpeg: str) -> tuple[bool, str]:
    source = Path(clean_movie)
    if not ffmpeg:
        return False, "ffmpeg was not found."
    if not source.is_file():
        return False, f"Clean Review Movie was not found: {source}"
    ass_path = Path(overlay_json).with_suffix(".ass")
    write_review_overlay_ass(overlay, ass_path)
    filter_path = ass_path.resolve(strict=False).as_posix()
    filter_path = filter_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    target = Path(mov_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-y", "-i", str(source), "-vf", f"subtitles='{filter_path}'",
        "-map", "0:v:0", "-map", "0:a?", "-c:v", "prores_ks", "-profile:v", "0",
        "-pix_fmt", "yuv422p10le", "-c:a", "copy", str(target),
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
    except Exception as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "ffmpeg failed.").strip()
    return True, str(target)
