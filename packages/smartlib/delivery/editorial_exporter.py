from __future__ import annotations

import json
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from smartlib.core.metadata import read_json
from smartlib.delivery.vendor_exporter import PackageResult


SCHEMA = "smart_delivery.editorial_package.v1"
SUPPORTED_MAPPING_SCHEMAS = {
    "smartpipeline.editorial_insert.v1",
    "smartpipeline.editorial_insert.v2",
}


@dataclass(frozen=True)
class EditorialShotSource:
    key: str
    shot: str
    cg_shot_id: str
    editorial_event_id: str
    editorial_event_uid: str
    source: Path | None
    member: str
    media_version: str
    available: bool


@dataclass(frozen=True)
class EditorialPackageSource:
    mapping_path: Path
    publish_root: Path
    mapping: dict[str, Any]
    episode: str
    timeline_revision: str
    media: tuple[tuple[Path, str], ...]
    shots: tuple[EditorialShotSource, ...]
    registry_path: Path | None


def resolve_editorial_package_source(
    mapping_path: str | Path, *, selected_shot_keys: set[str] | None = None,
    paths: Any | None = None,
) -> EditorialPackageSource:
    path = Path(mapping_path)
    data = read_json(path, None)
    if not isinstance(data, dict):
        raise ValueError(f"Editorial mapping is not valid JSON: {path}")
    schema = str(data.get("schema") or "")
    if schema not in SUPPORTED_MAPPING_SCHEMAS:
        raise ValueError(f"Unsupported Editorial mapping schema: {schema or '(missing)'}")
    episode = str(data.get("episode") or "").strip()
    timeline_revision = str(data.get("timeline_revision") or data.get("revision") or "").strip()
    if not episode or not timeline_revision:
        raise ValueError("Editorial mapping requires episode and timeline revision.")
    publish_root = _publish_root(path)
    rows: list[tuple[Path, str]] = []
    shot_rows: list[EditorialShotSource] = []
    seen_members: set[str] = set()
    for shot in data.get("shots") or []:
        shot_name = str(shot.get("shot") or "").strip()
        cg_shot_id = str(shot.get("cg_shot_id") or "").strip()
        event_id = str(shot.get("editorial_event_id") or "").strip()
        event_uid = str(shot.get("editorial_event_uid") or "").strip()
        key = _shot_key(shot)
        value = str(shot.get("editorial_primary") or "").strip()
        source = _resolve_media_path(path, publish_root, schema, value) if value else None
        if (not source or not source.is_file()) and paths and shot.get("event_storage_id"):
            source = _latest_event_hud(paths, episode, str(shot["event_storage_id"]))
        member = f"media/edit/{source.name}" if source else ""
        available = bool(source and source.is_file())
        shot_rows.append(EditorialShotSource(
            key=key, shot=shot_name, cg_shot_id=cg_shot_id,
            editorial_event_id=event_id, editorial_event_uid=event_uid,
            source=source, member=member,
            media_version=_media_version(source), available=available,
        ))
        if selected_shot_keys is None:
            if not available:
                continue
        elif key not in selected_shot_keys:
            continue
        elif not available:
            if value:
                raise FileNotFoundError(f"Editorial HUD movie was not found: {source}")
            raise ValueError(f"Editorial HUD is missing for {shot_name or key or 'shot'}.")
        if member in seen_members:
            raise ValueError(f"Editorial package has duplicate movie name: {source.name}")
        seen_members.add(member)
        rows.append((source, member))
    if selected_shot_keys is not None:
        unknown = selected_shot_keys - {row.key for row in shot_rows}
        if unknown:
            raise ValueError(f"Editorial selection contains unknown shots: {', '.join(sorted(unknown))}")
    if not rows:
        raise ValueError("No deliverable Editorial HUD movies are selected.")
    registry = publish_root / "identity" / "shot_registry.json"
    return EditorialPackageSource(
        mapping_path=path, publish_root=publish_root, mapping=data,
        episode=episode, timeline_revision=timeline_revision,
        media=tuple(rows), shots=tuple(shot_rows),
        registry_path=registry if registry.is_file() else None,
    )


class EditorialPackageBuilder:
    def build(
        self, *, mapping_path: str | Path, output: str | Path,
        recipient: str, process: str, delivery_revision: str, delivery_batch: str,
        selected_shot_keys: set[str] | None = None, paths: Any | None = None,
    ) -> PackageResult:
        source = resolve_editorial_package_source(
            mapping_path, selected_shot_keys=selected_shot_keys, paths=paths,
        )
        archive = Path(output)
        archive.parent.mkdir(parents=True, exist_ok=True)
        if archive.exists():
            raise FileExistsError(f"Output ZIP already exists: {archive}")
        packaged_mapping = json.loads(json.dumps(source.mapping))
        selected = (
            selected_shot_keys
            if selected_shot_keys is not None
            else {row.key for row in source.shots if row.available}
        )
        member_by_key = {
            row.key: row.member
            for row in source.shots
            if row.key in selected and row.member
        }
        for shot in packaged_mapping.get("shots") or []:
            shot["package_editorial_primary"] = member_by_key.get(_shot_key(shot), "")
        manifest = {
            "schema": SCHEMA,
            "profile": "editorial",
            "delivery": {
                "recipient": recipient, "process": process,
                "delivery_revision": delivery_revision,
                "delivery_batch": delivery_batch,
                "delivery_date": date.today().strftime("%Y%m%d"),
            },
            "source": {
                "episode": source.episode,
                "timeline_revision": source.timeline_revision,
                "editorial_mapping": source.mapping_path.as_posix(),
            },
            "selection": {
                "selected_shot_keys": [
                    row.key for row in source.shots
                    if selected_shot_keys is None or row.key in selected_shot_keys
                ],
                "excluded_shot_keys": [
                    row.key for row in source.shots
                    if selected_shot_keys is not None and row.key not in selected_shot_keys
                ],
            },
            "files": [
                {
                    "role": "editorial_hud",
                    "path": row.member,
                    "source": row.source.as_posix(),
                    "shot_key": row.key,
                    "shot": row.shot,
                    "media_version": row.media_version,
                }
                for row in source.shots
                if row.key in selected and row.available and row.source
            ],
        }
        members = ["manifest.json", "metadata/editorial_mapping.json"]
        if source.registry_path:
            members.append("metadata/shot_registry.json")
        members.extend(member for _path, member in source.media)
        with tempfile.TemporaryDirectory(prefix="smart-editorial-delivery-") as temp:
            temp_root = Path(temp)
            manifest_file = temp_root / "manifest.json"
            mapping_file = temp_root / "editorial_mapping.json"
            manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            mapping_file.write_text(json.dumps(packaged_mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as handle:
                handle.write(manifest_file, "manifest.json")
                handle.write(mapping_file, "metadata/editorial_mapping.json")
                if source.registry_path:
                    handle.write(source.registry_path, "metadata/shot_registry.json")
                for media_path, member in source.media:
                    handle.write(media_path, member)
        return PackageResult(archive=archive, manifest=manifest, files=tuple(members))


def _media_version(source: Path | None) -> str:
    if not source:
        return ""
    candidate = source.parent.parent.name
    return candidate if candidate.lower().startswith("v") else ""


def _shot_key(shot: dict[str, Any]) -> str:
    return str(
        shot.get("editorial_event_uid")
        or shot.get("cg_shot_id")
        or shot.get("editorial_event_id")
        or shot.get("shot")
        or ""
    ).strip()


def _latest_event_hud(paths: Any, episode: str, event_storage_id: str) -> Path | None:
    root = paths.editorial_event_media_root(episode, event_storage_id)
    versions = sorted(
        (path for path in root.glob("v*") if path.is_dir()),
        key=lambda path: path.name.lower(), reverse=True,
    ) if root.is_dir() else []
    for version in versions:
        edit_dir = paths.editorial_event_media_edit_dir(
            episode, event_storage_id, version.name
        )
        movies = sorted(edit_dir.glob("*.mov")) if edit_dir.is_dir() else []
        if movies:
            return movies[-1]
    return None


def _publish_root(mapping_path: Path) -> Path:
    parts = mapping_path.as_posix().split("/")
    try:
        index = len(parts) - 1 - parts[::-1].index("revisions")
    except ValueError as exc:
        raise ValueError(f"Editorial mapping is not under a revisions directory: {mapping_path}") from exc
    return Path("/".join(parts[:index]))


def _resolve_media_path(mapping_path: Path, publish_root: Path, schema: str, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    if schema == "smartpipeline.editorial_insert.v2" or value.replace("\\", "/").startswith("revisions/"):
        return publish_root / candidate
    return mapping_path.parent.parent / candidate
