"""Frame-aligned comparisons of the exact submitted and applied clean movies."""
from pathlib import Path
from fractions import Fraction
import json
import shutil
import subprocess

from smartlib.apps.shot_manager.animation_publish import file_hash
from smartlib.core.metadata import read_json


def movie_info(ffmpeg, movie):
    probe = Path(ffmpeg).with_name("ffprobe.exe" if Path(ffmpeg).suffix == ".exe" else "ffprobe")
    result = subprocess.run([str(probe), "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=width,height,r_frame_rate,nb_read_frames", "-of", "json", str(movie)],
        capture_output=True, text=True, check=True)
    row = json.loads(result.stdout)["streams"][0]
    return {"width":int(row["width"]), "height":int(row["height"]),
            "fps":str(Fraction(row["r_frame_rate"])), "frames":int(row["nb_read_frames"])}


def create_comparison(paths, snapshot, applied, directory, ffmpeg, frame_range, fps):
    receipt = snapshot["review_source"]
    source_manifest = read_json(receipt["source_manifest"], {})
    build_path = paths.project_dependency(source_manifest["review_build"])
    build = read_json(build_path, {})
    if list(build["frame_range"]) != list(frame_range) or float(build["fps"]) != float(fps):
        raise ValueError("Original and applied Review timing differs; rebuild the source Review first")
    original = paths.artifact_file(build_path.parent, build["clean_movie"])
    if not original.is_file():
        raise FileNotFoundError("Original clean Review movie is missing: " + str(original))
    before, after = movie_info(ffmpeg, original), movie_info(ffmpeg, applied)
    if before != after or before["frames"] != frame_range[1] - frame_range[0] + 1:
        raise ValueError(f"Review comparison requires matching FPS, frame count and resolution: {before} / {after}")
    files = {name:paths.artifact_file(directory, filename) for name,filename in
        {"original":"original.mov", "applied":"applied.mov", "comparison_50":"comparison_50.mov"}.items()}
    subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(original), "-i", str(applied),
        "-filter_complex", "[0:v]setpts=PTS-STARTPTS[a];[1:v]setpts=PTS-STARTPTS[b];[a][b]blend=all_expr='A*0.5+B*0.5'[v]",
        "-map", "[v]", "-map", "0:a?", "-c:v", "prores_ks", "-profile:v", "0", "-pix_fmt", "yuv422p10le",
        "-c:a", "pcm_s16le", str(files["comparison_50"])],capture_output=True,text=True,check=True)
    shutil.copy2(original, files["original"])
    shutil.copy2(applied, files["applied"])
    if movie_info(ffmpeg, files["comparison_50"]) != before:
        raise ValueError("Comparison output does not match the input frame contract")
    provenance = {"blend":0.5, "frame_range":list(frame_range), "media":before,
        "original_source":{"path":str(original), "sha256":file_hash(original)},
        "applied_source":{"path":str(applied), "sha256":file_hash(Path(applied))},
        "artifacts":{name:{"file":path.name,"sha256":file_hash(path)} for name,path in files.items()}}
    return files, provenance
