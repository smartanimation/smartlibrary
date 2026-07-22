from __future__ import annotations

from pathlib import Path
import glob
import os
import shutil
import subprocess
from typing import Any

from smartlib.core.config_loader import expand_config_tokens


def open_output_in_rv(settings: dict[str, Any], project_config=None) -> tuple[bool, str]:
    executable = find_rv_executable(project_config)
    if not executable:
        return False, "OpenRV executable was not found."
    targets = output_targets(settings)
    if not targets:
        return False, "No playblast files found. Run Selected Output Playblast first."
    command = [executable, *[str(target) for target in targets]]
    subprocess.Popen(command, cwd=str(targets[0].parent))
    return True, str(targets[0])


def find_rv_executable(project_config=None) -> str:
    config_path = _rv_path_from_config(project_config)
    if config_path:
        return config_path
    for env_name in ("SMART_RENDER_RV_PATH", "OPENRV_PATH", "RV_PATH"):
        path = os.environ.get(env_name)
        if path and Path(path).exists():
            return str(Path(path))
    repo_root = _repo_root()
    for path in sorted((repo_root / "tools" / "OpenRV").glob("OpenRV-*/bin/rv.exe"), reverse=True):
        if path.exists():
            return str(path)
    for name in ("rv", "rv.exe"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def output_targets(settings: dict[str, Any]) -> list[Path]:
    for key in ("movie_path", "mov", "review_movie"):
        movie = str(settings.get(key) or "").strip()
        if movie and Path(movie).is_file():
            return [Path(movie)]
    package_root = str(settings.get("package_root") or "").strip()
    if package_root and Path(package_root).is_dir():
        movies = sorted((Path(package_root) / "mov").glob("*.mov"))
        if movies:
            return [movies[0]]
        for folder in ("images", "image_sequence", "slate"):
            files = _media_files_recursive(Path(package_root) / folder)
            if files:
                return [files[0]]
    output = str(settings.get("output_path") or "").strip()
    if not output:
        return []
    path = Path(output.replace("\\", "/"))
    if path.is_file():
        return [path]
    if path.is_dir():
        files = _media_files(path)
        return [files[0]] if files else []
    parent = path.parent
    if not parent.exists():
        return []
    compression = str(settings.get("compression") or "").strip().lstrip(".")
    extensions = _candidate_extensions(path, compression)
    matches: list[Path] = []
    for ext in extensions:
        matches.extend(parent.glob(f"{path.name}.*.{ext}"))
        matches.extend(parent.glob(f"{path.name}*.{ext}"))
        if path.suffix:
            matches.extend(parent.glob(f"{path.stem}.*.{ext}"))
            matches.extend(parent.glob(f"{path.stem}*.{ext}"))
    matches = sorted({item for item in matches if item.is_file()})
    return [matches[0]] if matches else []


def _rv_path_from_config(project_config) -> str:
    if project_config is None:
        return ""
    try:
        data = project_config.load("tools.yml")
    except Exception:
        return ""
    path = (((data.get("tools") or {}).get("openrv") or {}).get("path") or "")
    if not path:
        return ""
    text = expand_config_tokens(str(path), project_config)
    if "{version}" in text:
        candidates = sorted(Path(item) for item in glob.glob(text.replace("{version}", "*")) if Path(item).exists())
        return str(candidates[-1]) if candidates else ""
    candidate = Path(text)
    return str(candidate) if candidate.exists() else ""


def _candidate_extensions(path: Path, compression: str) -> list[str]:
    extensions = []
    if path.suffix:
        extensions.append(path.suffix.lstrip("."))
    if compression:
        extensions.append(compression)
    extensions.extend(["mov", "mp4", "jpg", "jpeg", "png", "exr"])
    return _dedupe([ext.lower() for ext in extensions if ext])


def _media_files(path: Path) -> list[Path]:
    extensions = {"mov", "mp4", "jpg", "jpeg", "png", "exr"}
    return sorted(item for item in path.iterdir() if item.is_file() and item.suffix.lower().lstrip(".") in extensions)


def _media_files_recursive(path: Path) -> list[Path]:
    if not path.exists():
        return []
    extensions = {"mov", "mp4", "jpg", "jpeg", "png", "exr"}
    return sorted(item for item in path.rglob("*") if item.is_file() and item.suffix.lower().lstrip(".") in extensions)


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _repo_root() -> Path:
    return Path(os.environ.get("SMARTPIPELINE_ROOT") or os.environ.get("SMARTLIBRARY_ROOT") or Path(__file__).resolve().parents[3])
