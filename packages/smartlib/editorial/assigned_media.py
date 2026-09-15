"""Offline references for explicitly assigned production shots."""
from __future__ import annotations

import json
import subprocess

from smartlib.core.metadata import read_json, write_json


def probe_movie(service, movie):
    result = subprocess.run([
        str(service._ffmpeg_path().with_name("ffprobe.exe")), "-v", "error",
        "-show_streams", "-show_format", "-of", "json", str(movie),
    ], check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("Offline movie has no video stream")
    return data, video


def validate_offline_ranges(service, movie, events, plan):
    data, video = probe_movie(service, movie)
    rate = str(video.get("avg_frame_rate") or "0/1").split("/")
    fps = float(rate[0]) / float(rate[1]) if len(rate) == 2 else float(rate[0])
    if abs(fps - service.fps) > 0.001:
        raise ValueError(f"Offline fps {fps:g} differs from project fps {service.fps}")
    duration = float(video.get("duration") or data.get("format", {}).get("duration") or 0)
    frames = int(video.get("nb_frames") or round(duration * service.fps))
    origin = int(plan["offline_origin"])
    for event in events:
        if event.cut_in < origin or event.cut_out - origin >= frames:
            raise ValueError(f"{event.shot}: selected range lies outside the {frames}-frame offline movie")


def write_assigned_media(service, cuts, shots, movie, publish_dir, plan):
    paths = service.shots.paths
    fps = service.fps
    data, video = probe_movie(service, movie)
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    ffmpeg = str(service._ffmpeg_path())
    assignments = {row["cut"]: row for row in plan["rows"] if row["enabled"]}
    cut_files = {}
    for cut in cuts:
        row = assignments[cut.shot]
        directory = paths.shot_data_version_dir(
            cut.episode, cut.sequence, row["work_shot"],
            "editorial_reference", "cuts", cut.shot, publish_dir.name,
        )
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / "offline.mov"
        subprocess.run([
            ffmpeg, "-y", "-ss", str((cut.cut_in - int(plan["offline_origin"])) / fps),
            "-i", str(movie), "-t", str(cut.duration / fps),
            "-map", "0:v:0", "-map", "0:a:0?", "-r", str(fps),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", str(output),
        ], check=True, capture_output=True)
        cut_files[cut.shot] = output
        write_json(directory / "mapping.json", row)

    audio_files, work_shots = [], []
    for shot in shots:
        directory = paths.shot_data_version_dir(
            shot.episode, shot.sequence, shot.shot, "editorial_reference", "", "main", publish_dir.name,
        )
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / "offline.mov"
        command = [ffmpeg, "-y"]
        filters, concat_labels = [], []
        cursor = shot.maya_range[0]
        for i, segment in enumerate(shot.editorial_segments):
            command += ["-i", str(cut_files[segment["cut"]])]
            gap = segment["maya_in"] - cursor
            if gap:
                filters.append(f"color=c=black:s={video['width']}x{video['height']}:r={fps}:d={gap / fps}[g{i}]")
                concat_labels.append(f"[g{i}]")
                if has_audio:
                    filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={gap / fps}[ga{i}]")
                    concat_labels.append(f"[ga{i}]")
            filters.append(f"[{i}:v]setpts=PTS-STARTPTS,setsar=1[v{i}]")
            concat_labels.append(f"[v{i}]")
            if has_audio:
                filters.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a{i}]")
                concat_labels.append(f"[a{i}]")
            cursor = segment["maya_out"] + 1
        streams = 2 if has_audio else 1
        filters.append("".join(concat_labels) + f"concat=n={len(concat_labels) // streams}:v=1:a={int(has_audio)}[v]" + ("[a]" if has_audio else ""))
        command += ["-filter_complex", ";".join(filters), "-map", "[v]"]
        if has_audio:
            command += ["-map", "[a]", "-c:a", "pcm_s16le"]
        command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(output)]
        subprocess.run(command, check=True, capture_output=True)
        if has_audio:
            audio_dir = paths.shot_data_version_dir(
                shot.episode, shot.sequence, shot.shot, "audio", "", "", publish_dir.name,
            )
            audio_dir.mkdir(parents=True, exist_ok=True)
            audio = audio_dir / f"{shot.shot}.wav"
            subprocess.run([ffmpeg, "-y", "-i", str(output), "-vn", "-c:a", "pcm_s16le", str(audio)], check=True, capture_output=True)
            write_json(audio_dir.parent / "latest.json", {
                "version": publish_dir.name, "path": str(audio), "fps": fps,
                "cut_in": shot.maya_range[0], "cut_out": shot.maya_range[1],
                "editorial_publish": str(publish_dir),
            })
            audio_files.append(audio)
        work_shots.append({**service._shot_row(shot), "reference_movie": str(output)})
        write_json(directory / "mapping.json", {"shot": shot.shot, "maya_range": shot.maya_range,
                   "segments": shot.editorial_segments, "movie": str(output)})
    metadata = publish_dir / "metadata" / "editorial.json"
    payload = read_json(metadata, {})
    payload["work_shots"] = work_shots
    payload["cut_media"] = {key: str(value) for key, value in cut_files.items()}
    write_json(metadata, payload)
    return audio_files
