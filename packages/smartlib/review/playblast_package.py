from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
from string import Formatter
from typing import Any

from smartlib.core.config_loader import ProjectConfig, expand_config_tokens


FRAME_TOKEN = "####"


@dataclass(frozen=True)
class PlayblastPackagePaths:
    root: Path
    mov: Path
    image_sequence: Path
    image_prefix: Path
    image_pattern: str
    slate_sequence: Path
    slate_prefix: Path
    slate_pattern: str
    metadata_review: Path
    metadata_playblast: Path
    metadata_source_scene: Path
    thumbnail: Path
    ae_dir: Path


@dataclass(frozen=True)
class ReviewBuildPackagePaths:
    root: Path
    manifest: Path
    script: Path
    log: Path
    template_project: Path
    template_used: Path
    slots: Path


def resolve_playblast_package(
    project_config: ProjectConfig,
    *,
    area: str,
    shot_root: str | Path,
    shot: str,
    dept: str,
    version: str,
    take: str,
    layer: str,
) -> PlayblastPackagePaths:
    package = _package_config(project_config)
    values = {
        "project_root": _path_text(project_config.project_root),
        "project_name": project_config.project_name,
        "shot_root": _path_text(shot_root),
        "shot": _clean_token(shot, "shot"),
        "dept": _clean_token(dept, "dept"),
        "version": normalize_version(version),
        "take": normalize_take(take),
        "layer": _clean_token(layer, "CHA"),
        "frame": FRAME_TOKEN,
    }
    root_template = str(((package.get("roots") or {}).get(area)) or "{shot_root}/output/review/{dept}/{layer}/{version}/{take}")
    root = Path(_format(root_template, values))
    paths = package.get("paths") or {}
    image_sequence = root / _format(str(paths.get("image_sequence") or "images/{shot}_{dept}_{layer}_{version}_{take}_{frame}.png"), values)
    slate_sequence = root / _format(
        str(paths.get("slate_sequence") or "slate/{shot}_{dept}_slate_{version}_{take}_{frame}.png"),
        values,
    )
    return PlayblastPackagePaths(
        root=root,
        mov=root / _format(str(paths.get("mov") or "mov/{shot}_{dept}_{version}_{take}.mov"), values),
        image_sequence=image_sequence,
        image_prefix=_without_frame_token(image_sequence),
        image_pattern=_ffmpeg_pattern(image_sequence),
        slate_sequence=slate_sequence,
        slate_prefix=_without_frame_token(slate_sequence),
        slate_pattern=_ffmpeg_pattern(slate_sequence),
        metadata_review=root / _format(str(paths.get("metadata_review") or "metadata/review.json"), values),
        metadata_playblast=root / _format(str(paths.get("metadata_playblast") or "metadata/playblast.json"), values),
        metadata_source_scene=root / _format(str(paths.get("metadata_source_scene") or "metadata/source_scene.json"), values),
        thumbnail=root / _format(str(paths.get("thumbnail") or "thumbnail/{shot}_{dept}_{version}_{take}.jpg"), values),
        ae_dir=root / _format(str(paths.get("ae_dir") or "ae"), values),
    )


def resolve_review_build_package(
    project_config: ProjectConfig,
    *,
    area: str,
    shot_root: str | Path,
    shot: str,
    dept: str,
    version: str,
    take: str,
) -> ReviewBuildPackagePaths:
    package = _package_config(project_config)
    values = {
        "project_root": _path_text(project_config.project_root),
        "project_name": project_config.project_name,
        "shot_root": _path_text(shot_root),
        "shot": _clean_token(shot, "shot"),
        "dept": _clean_token(dept, "dept"),
        "version": normalize_version(version),
        "take": normalize_take(take),
    }
    root_key = f"{area}_review_build"
    root_template = str(((package.get("roots") or {}).get(root_key)) or "{shot_root}/output/review/{dept}/review_build/{version}/{take}")
    root = Path(_format(root_template, values))
    paths = package.get("paths") or {}
    return ReviewBuildPackagePaths(
        root=root,
        manifest=root / _format(str(paths.get("review_build_manifest") or "{shot}_{dept}_build_{version}_{take}.json"), values),
        script=root / _format(str(paths.get("review_build_script") or "ae/scripts/{shot}_{dept}_build_{version}_{take}.jsx"), values),
        log=root / _format(str(paths.get("review_build_log") or "ae/data/{shot}_{dept}_build_{version}_{take}.log"), values),
        template_project=root / _format(str(paths.get("review_build_template_project") or "ae/review_project.aep"), values),
        template_used=root / _format(str(paths.get("review_build_template_used") or "ae/template_used.json"), values),
        slots=root / _format(str(paths.get("review_build_slots") or "slots.json"), values),
    )


def normalize_version(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "v001"
    if text.lower().startswith("v"):
        number = text[1:]
        return f"v{int(number):03d}" if number.isdigit() else text
    return f"v{int(text):03d}" if text.isdigit() else text


def normalize_take(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "t001"
    if text.lower().startswith("take"):
        text = text[4:]
    elif text.lower().startswith("t"):
        text = text[1:]
    return f"t{int(text):03d}" if text.isdigit() else text


def latest_take_for_package(package_root: str | Path, current: Any = "") -> str:
    current_label = normalize_take(current) if str(current or "").strip() else ""
    root = Path(str(package_root or "").replace("\\", "/"))
    if not str(package_root or "").strip():
        return current_label
    best_label = current_label
    best_number = _take_number(best_label)
    parent = root.parent
    if not parent.exists():
        return best_label
    try:
        children = list(parent.iterdir())
    except Exception:
        return best_label
    for child in children:
        if not child.is_dir():
            continue
        number = _take_number(child.name)
        if number and number > best_number:
            best_number = number
            best_label = normalize_take(child.name)
    return best_label


def _take_number(value: Any) -> int:
    text = str(value or "").strip().lower()
    if text.startswith("take"):
        text = text[4:]
    elif text.startswith("t"):
        text = text[1:]
    try:
        return int(text)
    except ValueError:
        return 0


def next_available_package_root(package_root: str | Path) -> Path:
    root_text = str(package_root or "").strip()
    root = Path(root_text.replace("\\", "/")) if root_text else Path()
    if not root_text or not root.exists():
        return root
    parent = root.parent
    best_number = _take_number(root.name)
    try:
        children = list(parent.iterdir())
    except Exception:
        children = []
    for child in children:
        if child.is_dir():
            best_number = max(best_number, _take_number(child.name))
    return parent / normalize_take(str(best_number + 1))


def snapshot_output_to_publish(output_root: str | Path, publish_root: str | Path, *, unique: bool = False) -> Path:
    source = Path(output_root)
    target = next_available_package_root(publish_root) if unique else Path(publish_root)
    if not source.exists():
        raise RuntimeError(f"Output package was not found: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise RuntimeError(f"Publish package already exists: {target}")
    shutil.copytree(source, target)
    return target


def find_ffmpeg(project_config: ProjectConfig | None = None) -> str:
    for env_name in ("SMARTLIB_FFMPEG", "FFMPEG_PATH"):
        value = os.environ.get(env_name)
        if value and Path(value).exists():
            return str(Path(value))
    if project_config is not None:
        try:
            path = (((project_config.load("tools.yml").get("tools") or {}).get("ffmpeg") or {}).get("path") or "")
        except Exception:
            path = ""
        if path:
            path = expand_config_tokens(str(path), project_config)
        if path and Path(path).exists():
            return str(Path(path))
    found = shutil.which("ffmpeg")
    return found or ""


def encode_prores_proxy_mov(
    *,
    image_pattern: str,
    mov_path: str | Path,
    start_frame: int,
    fps: int | float = 24,
    ffmpeg: str = "",
    slate_pattern: str = "",
) -> tuple[bool, str]:
    ffmpeg = ffmpeg or find_ffmpeg()
    if not ffmpeg:
        return False, "ffmpeg was not found."
    target = Path(mov_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-framerate",
        str(fps),
        "-start_number",
        str(start_frame),
        "-i",
        image_pattern,
    ]
    if slate_pattern:
        command.extend(["-start_number", str(start_frame), "-i", slate_pattern, "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto"])
    command.extend(["-c:v", "prores_ks", "-profile:v", "0", "-pix_fmt", "yuv422p10le", str(target)])
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except Exception as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "ffmpeg failed.").strip()
    return True, str(target)


def extract_thumbnail_from_mov(
    *,
    mov_path: str | Path,
    thumbnail_path: str | Path,
    ffmpeg: str = "",
) -> tuple[bool, str]:
    ffmpeg = ffmpeg or find_ffmpeg()
    if not ffmpeg:
        return False, "ffmpeg was not found."
    source = Path(mov_path)
    if not source.exists():
        return False, f"Movie was not found: {source}"
    target = Path(thumbnail_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, "-y", "-i", str(source), "-frames:v", "1", "-q:v", "2", str(target)]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except Exception as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "ffmpeg failed.").strip()
    return True, str(target)


def _package_config(project_config: ProjectConfig) -> dict[str, Any]:
    data = project_config.load("review_package.yml")
    return (data.get("playblast_package") or {}) if isinstance(data, dict) else {}


def _without_frame_token(path: Path) -> Path:
    name = path.name
    for token in (f"_{FRAME_TOKEN}", f".{FRAME_TOKEN}", FRAME_TOKEN):
        name = name.replace(token, "")
    return path.parent / Path(name).with_suffix("").name


def _ffmpeg_pattern(path: Path) -> str:
    return path.as_posix().replace(FRAME_TOKEN, "%04d")


def _format(template: str, values: dict[str, Any]) -> str:
    needed = {field for _, field, _, _ in Formatter().parse(template) if field}
    data = dict(values)
    for key, value in values.items():
        if isinstance(value, Path):
            data[key] = value.as_posix()
    if any(field not in data for field in needed):
        return template
    return template.format(**data).replace("\\", "/")


def _clean_token(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    return "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in text).strip("_") or fallback


def _path_text(path: str | Path | None) -> str:
    return Path(path).as_posix() if path else ""
