from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _bootstrap() -> None:
    root = Path(__file__).resolve().parents[1]
    for path in (root / "packages", root):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    _bootstrap()

    from smartlib.core.config_loader import ProjectConfig
    from smartlib.review.playblast_package import find_ffmpeg

    parser = argparse.ArgumentParser(description="Transcode an AE intermediate movie to Apple ProRes 422 Proxy.")
    parser.add_argument("--config-dir", default="")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--remove-source", action="store_true")
    args = parser.parse_args(argv)

    source = Path(args.input)
    target = Path(args.output)
    if not source.is_file():
        _emit({"ok": False, "error": f"AE intermediate was not found: {source}"})
        return 2

    project_config = ProjectConfig(args.config_dir) if args.config_dir else None
    ffmpeg = find_ffmpeg(project_config)
    if not ffmpeg:
        _emit({"ok": False, "error": "ffmpeg was not found in the project Tools configuration."})
        return 3

    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "prores_ks",
        "-profile:v",
        "0",
        "-pix_fmt",
        "yuv422p10le",
        "-c:a",
        "pcm_s16le",
        str(target),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except Exception as exc:
        _emit({"ok": False, "error": str(exc), "source": str(source), "output": str(target)})
        return 4

    if completed.returncode != 0 or not target.is_file():
        _emit(
            {
                "ok": False,
                "error": (completed.stderr or completed.stdout or "ffmpeg failed.").strip(),
                "source": str(source),
                "output": str(target),
            }
        )
        return 5

    if args.remove_source:
        source.unlink(missing_ok=True)
    _emit(
        {
            "ok": True,
            "source": str(source),
            "output": str(target),
            "codec": "Apple ProRes 422 Proxy",
            "videoCodec": "prores_ks",
            "profile": 0,
            "pixelFormat": "yuv422p10le",
            "audioCodec": "pcm_s16le",
            "size": target.stat().st_size,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
