from __future__ import annotations

import json
import re
import shutil
import glob
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


TEXTURE_EXTENSIONS = {
    ".bmp", ".exr", ".gif", ".hdr", ".jpeg", ".jpg", ".png", ".psd",
    ".tga", ".tif", ".tiff", ".tx",
}
_UDIM_RE = re.compile(r"(?<!\d)(?:1\d{3})(?!\d)|<UDIM>|%\(UDIM\)d", re.IGNORECASE)
_SEQUENCE_RE = re.compile(r"(?<!\d)\d{2,}(?!\d)")
_TEXTURE_ROOT_NAMES = {"sourceimages", "textures", "texture", "tex"}


@dataclass(frozen=True)
class TextureReference:
    node: str
    path: str


@dataclass(frozen=True)
class TextureReconnectItem:
    node: str
    source_path: str
    resolved_path: Path | None
    status: str
    match_method: str
    candidates: tuple[Path, ...] = ()


def texture_root_from_package(package_root: str | Path) -> Path | None:
    """Return the package texture root declared by its ingest manifest.

    Package-internal paths come from the manifest.  The production package root
    itself must be supplied by the common path resolver.
    """
    root = Path(package_root)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    for record in manifest.get("files") or []:
        if not isinstance(record, dict) or record.get("role") != "texture_root":
            continue
        relative = Path(str(record.get("path") or ""))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            return None
        candidate = root / relative
        return candidate if candidate.is_dir() else None
    return None


def plan_texture_reconnect(
    references: Iterable[TextureReference], texture_root: str | Path
) -> list[TextureReconnectItem]:
    """Match DCC texture references against one resolver-selected texture root."""
    root = Path(texture_root)
    files = tuple(
        path for path in sorted(root.rglob("*"), key=lambda value: value.as_posix().casefold())
        if path.is_file() and path.suffix.casefold() in TEXTURE_EXTENSIONS
    ) if root.is_dir() else ()
    by_key: dict[str, list[Path]] = {}
    for path in files:
        logical = _logical_texture_path(path)
        values = by_key.setdefault(_texture_key(path.name), [])
        if logical not in values:
            values.append(logical)

    result = []
    for reference in references:
        relative = _texture_relative_path(reference.path)
        if relative:
            exact = root.joinpath(*relative.parts)
            logical_matches = tuple(
                candidate for candidate in by_key.get(_texture_key(relative.name), ())
                if candidate.as_posix().casefold() == exact.as_posix().casefold()
            )
            if len(logical_matches) == 1:
                result.append(TextureReconnectItem(
                    reference.node, reference.path, logical_matches[0], "ready",
                    "relative_path", logical_matches
                ))
                continue
        candidates = tuple(by_key.get(_texture_key(Path(reference.path).name), ()))
        if len(candidates) == 1:
            result.append(TextureReconnectItem(
                reference.node, reference.path, candidates[0], "ready", "filename", candidates
            ))
        elif candidates:
            result.append(TextureReconnectItem(
                reference.node, reference.path, None, "ambiguous", "filename", candidates
            ))
        else:
            result.append(TextureReconnectItem(
                reference.node, reference.path, None, "missing", "", ()
            ))
    return result


def inspect_texture_references(
    references: Iterable[TextureReference],
) -> list[TextureReconnectItem]:
    """Inspect current DCC paths when no ingested package exists yet."""
    result = []
    for reference in references:
        path = Path(reference.path)
        exists = bool(_expand_texture_pattern(path))
        result.append(TextureReconnectItem(
            reference.node, reference.path, path if exists else None,
            "ready" if exists else "missing", "current_path" if exists else "", (),
        ))
    return result


def reconnect_manifest(items: Iterable[TextureReconnectItem]) -> dict:
    rows = []
    for item in items:
        rows.append({
            "node": item.node,
            "source_path": item.source_path,
            "resolved_path": item.resolved_path.as_posix() if item.resolved_path else "",
            "status": item.status,
            "match_method": item.match_method,
            "candidates": [path.as_posix() for path in item.candidates],
        })
    return {"schema": "smartpipeline.texture_reconnect.v1", "textures": rows}


def collect_texture_items(
    items: Iterable[TextureReconnectItem], destination_root: str | Path, *,
    texture_root: str | Path | None = None,
) -> tuple[list[Path], dict]:
    """Collect selected textures into a resolver-provided delivery staging root."""
    destination = Path(destination_root)
    source_root = Path(texture_root).resolve() if texture_root else None
    copied: list[Path] = []
    rows = []
    targets: dict[str, Path] = {}
    for item in items:
        pattern = item.resolved_path or Path(item.source_path)
        for source in _expand_texture_pattern(pattern):
            relative = _collection_relative_path(source, source_root, item.source_path)
            target = destination / relative
            key = target.as_posix().casefold()
            previous = targets.get(key)
            if previous is not None and previous.resolve() != source.resolve():
                raise FileExistsError(f"Texture collection collision: {previous} and {source} -> {target}")
            targets[key] = source
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(source, target)
                copied.append(target)
            rows.append({"node": item.node, "source_path": source.as_posix(), "collected_path": target.as_posix(), "relative_path": relative.as_posix()})
    manifest = {"schema": "smartpipeline.texture_collection.v1", "texture_root": destination.as_posix(), "textures": rows}
    destination.mkdir(parents=True, exist_ok=True)
    (destination.parent / "texture_collection.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return copied, manifest


def _texture_relative_path(value: str) -> Path | None:
    parts = Path(str(value).replace("\\", "/")).parts
    lowered = [part.casefold() for part in parts]
    for index, part in enumerate(lowered):
        if part in _TEXTURE_ROOT_NAMES and index + 1 < len(parts):
            return Path(*parts[index + 1 :])
    return None


def _texture_key(filename: str) -> str:
    name = _UDIM_RE.sub("<udim>", filename.casefold())
    # Keep ordinary version numbers intact, but normalize common frame tokens.
    if not _UDIM_RE.search(filename):
        stem, suffix = Path(name).stem, Path(name).suffix
        match = list(_SEQUENCE_RE.finditer(stem))
        if match and match[-1].end() == len(stem):
            token = match[-1]
            stem = f"{stem[:token.start()]}<frame>"
            name = stem + suffix
    return name


def _logical_texture_path(path: Path) -> Path:
    name = _UDIM_RE.sub("<UDIM>", path.name)
    return path.with_name(name)


def _expand_texture_pattern(path: Path) -> list[Path]:
    value = path.as_posix()
    pattern = re.sub(r"<UDIM>|%\(UDIM\)d", "[0-9][0-9][0-9][0-9]", value, flags=re.IGNORECASE)
    pattern = re.sub(r"#+", lambda match: "[0-9]" * len(match.group(0)), pattern)
    matches = [Path(value) for value in sorted(glob.glob(pattern)) if Path(value).is_file()]
    return matches if matches else ([path] if path.is_file() else [])


def _collection_relative_path(
    source: Path, texture_root: Path | None, original_path: str = ""
) -> Path:
    if texture_root is not None:
        try:
            return source.resolve().relative_to(texture_root)
        except ValueError:
            pass
    declared = _texture_relative_path(original_path)
    if declared is not None:
        return declared.parent / source.name
    return Path(source.name)
