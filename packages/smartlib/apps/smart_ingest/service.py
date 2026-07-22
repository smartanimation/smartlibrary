from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json, write_json
from smartlib.core.versioning import format_version, next_version, parse_version


SUPPORTED_EXTENSIONS = {
    ".abc",
    ".aaf",
    ".edl",
    ".fbx",
    ".ma",
    ".mb",
    ".mov",
    ".mp4",
    ".otio",
    ".tga",
    ".tif",
    ".tiff",
    ".usd",
    ".usda",
    ".usdc",
    ".wav",
    ".xml",
    ".zip",
    ".aep",
    ".jpg",
    ".jpeg",
    ".pdf",
    ".png",
}
EDITORIAL_EXTENSIONS = {".aaf", ".edl", ".mov", ".mp4", ".otio", ".wav", ".xml"}
ASSET_EXTENSIONS = {".abc", ".fbx", ".ma", ".mb", ".tga", ".tif", ".tiff", ".usd", ".usda", ".usdc"}
SHOT_EXTENSIONS = {".abc", ".fbx", ".mov", ".mp4", ".usd", ".usda", ".usdc", ".wav"}
SEQUENCE_EXTENSIONS = SHOT_EXTENSIONS | {".edl", ".otio", ".xml"}
VENDOR_EXTENSIONS = SUPPORTED_EXTENSIONS | {".rar", ".7z"}

DATE_RE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")
VERSION_RE = re.compile(r"v\d{3,}", re.IGNORECASE)
SHOT_RE = re.compile(r"(?<![a-z0-9])(?P<shot>sh\d{3,4})(?![a-z0-9])", re.IGNORECASE)
EP_RE = re.compile(r"(?<![a-z0-9])(?P<episode>ep\d{2,4})(?![a-z0-9])", re.IGNORECASE)
SEQ_RE = re.compile(r"(?<![a-z0-9])(?P<sequence>(?:sq|seq)\d{2,4})(?![a-z0-9])", re.IGNORECASE)
DEPARTMENT_ALIASES = {
    "anim": "anim",
    "animation": "anim",
    "comp": "comp",
    "fx": "fx",
    "groom": "groom",
    "layout": "layout",
    "light": "light",
    "lighting": "light",
    "look": "look",
    "model": "model",
    "rig": "rig",
}


@dataclass(frozen=True)
class IngestMetadata:
    target_type: str = ""
    project: str = ""
    asset: str = ""
    category: str = "characters"
    group: str = "main"
    variant: str = "default"
    department: str = ""
    subset: str = "main"
    format: str = ""
    episode: str = "ep001"
    sequence: str = "sq010"
    shot: str = ""
    vendor: str = ""
    delivery_date: str = ""
    comment: str = "ingest via Smart Ingest"


@dataclass(frozen=True)
class PlanItem:
    id: str
    source_path: Path
    target_path: Path | None
    file_type: str
    action: str
    target_type: str
    status: str
    reason: str
    metadata: IngestMetadata
    size: int = 0
    selected: bool = False

    @property
    def actionable(self) -> bool:
        return self.action in {"copy", "reject"} and self.status in {"Ready", "Reject"}


@dataclass(frozen=True)
class IngestRunResult:
    copied: list[Path]
    rejected: list[Path]
    processed_sources: list[Path]
    skipped: list[PlanItem]
    manifests: list[Path]


class SmartIngestService:
    """Plan and execute incoming-file ingest.

    Recommended project layout:
      incoming/editorial/YYYYMMDD/
      incoming/vendors/<vendor>/YYYYMMDD/
      incoming/assets/YYYYMMDD/
      incoming/shots/YYYYMMDD/
      incoming/_rejected/YYYYMMDD/
      incoming/<type>/YYYYMMDD/_processed/

    ``incoming`` remains a receipt area. Known files are copied into the
    production data roots, and every handled source gets an audit manifest.
    """

    def __init__(self, project_config: ProjectConfig):
        self.project_config = project_config
        project_root = project_config.project_root
        if project_root is None:
            raise RuntimeError("project_root is not set in templates_base.yml")
        self.project_root = project_root
        templates = project_config.base.get("templates") or {}
        self.incoming_root = self._resolve_template(templates.get("incoming_root"), project_root / "incoming")
        self.staging_root = self._resolve_template(templates.get("staging_root"), project_root / "staging")
        self.project_name = project_config.project_name
        naming = project_config.load("naming.yml")
        self.editorial_naming = (naming.get("smart_ingest") or {}).get("editorial") or {}

    def scan(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        extensions: set[str] | None = None,
        include_rejected: bool = False,
    ) -> list[Path]:
        if not self.incoming_root.exists():
            return []
        allowed = {ext.lower() for ext in extensions} if extensions else None
        files: list[Path] = []
        for path in sorted(self.incoming_root.rglob("*")):
            if path.suffix.lower() == ".json" and path.name.endswith((".reject.json", ".ingest.json")):
                continue
            if not path.is_file() or self._is_internal_incoming_path(path, include_rejected=include_rejected):
                continue
            if self._is_fbm_member(path):
                continue
            if allowed is not None and path.suffix.lower() not in allowed:
                continue
            delivery_date = self._date_from_path(path) or datetime.fromtimestamp(path.stat().st_mtime).date()
            if date_from and delivery_date < date_from:
                continue
            if date_to and delivery_date > date_to:
                continue
            files.append(path)
        return files

    def incoming_date_range(self) -> tuple[date, date] | None:
        dates = []
        for path in self.scan(include_rejected=True):
            dates.append(self._date_from_path(path) or datetime.fromtimestamp(path.stat().st_mtime).date())
        if not dates:
            return None
        return min(dates), max(dates)

    def auto_plan(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        extensions: set[str] | None = None,
        include_rejected: bool = False,
    ) -> list[PlanItem]:
        return [
            self.plan_file(path)
            for path in self.scan(
                date_from=date_from,
                date_to=date_to,
                extensions=extensions,
                include_rejected=include_rejected,
            )
        ]

    def plan_file(self, source_path: str | Path, metadata: IngestMetadata | None = None) -> PlanItem:
        source = Path(source_path)
        if self._is_rejected_path(source):
            meta = metadata or replace(self._infer_metadata(source), target_type="Rejected")
            return self._item(source, source, source.suffix.lower().lstrip(".").upper() or "FILE", "none", "Rejected", "Reject", "previously rejected", meta)
        meta = metadata or self._infer_metadata(source)
        file_type = source.suffix.lower().lstrip(".").upper() or "FILE"
        if not source.exists():
            return self._item(source, None, file_type, "none", meta.target_type, "Missing", "source file does not exist", meta)
        if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
            reject_meta = replace(meta, target_type="Rejected", format=source.suffix.lower().lstrip("."))
            return self._item(
                source,
                self._rejected_path(source, reject_meta),
                file_type,
                "reject",
                "Rejected",
                "Reject",
                "unsupported file type",
                reject_meta,
                selected=True,
            )

        target_path, reason = self._target_path(source, meta)
        if target_path is None:
            return self._item(source, None, file_type, "none", meta.target_type, "Needs Metadata", reason, meta)
        if target_path.exists():
            return self._item(source, target_path, file_type, "none", meta.target_type, "Conflict", "target already exists", meta)
        companion = self._fbm_companion(source)
        if companion and target_path.with_suffix(".fbm").exists():
            return self._item(source, target_path, file_type, "none", meta.target_type, "Conflict", "FBM target already exists", meta)
        return self._item(source, target_path, file_type, "copy", meta.target_type, "Ready", reason, meta, selected=True)

    def replan(self, item: PlanItem, metadata: IngestMetadata) -> PlanItem:
        return self.plan_file(item.source_path, metadata)

    def ingest_selected(self, items: list[PlanItem], *, create_folders: bool = True) -> IngestRunResult:
        copied: list[Path] = []
        rejected: list[Path] = []
        processed_sources: list[Path] = []
        skipped: list[PlanItem] = []
        manifests: list[Path] = []

        for item in items:
            if not item.selected or not item.actionable or item.target_path is None:
                skipped.append(item)
                continue
            if create_folders:
                item.target_path.parent.mkdir(parents=True, exist_ok=True)
            if item.action == "copy":
                checksum = _sha1(item.source_path)
                companion = self._fbm_companion(item.source_path)
                shutil.copy2(item.source_path, item.target_path)
                companion_target = None
                companion_manifest = None
                if companion:
                    companion_target = item.target_path.with_suffix(".fbm")
                    shutil.copytree(companion, companion_target)
                    companion_manifest = self._fbm_manifest(companion, companion_target)
                processed_path = self._move_to_processed(item.source_path)
                if companion:
                    processed_companion = self._move_to_processed(companion)
                    if companion_manifest is not None:
                        companion_manifest["processed_source_path"] = str(processed_companion)
                copied.append(item.target_path)
                processed_sources.append(processed_path)
                manifests.append(
                    self._write_processed_manifest(
                        item,
                        item.target_path,
                        processed_path,
                        checksum,
                        companion=companion_manifest,
                    )
                )
                if item.target_type == "Editorial":
                    manifests.append(self._write_editorial_source_metadata(item, item.target_path, checksum))
            elif item.action == "reject":
                shutil.copy2(item.source_path, item.target_path)
                rejected.append(item.target_path)
                manifests.append(self._write_rejection_manifest(item, item.target_path))
            else:
                skipped.append(item)
        return IngestRunResult(copied, rejected, processed_sources, skipped, manifests)

    def update_item_metadata(self, item: PlanItem, **changes: Any) -> PlanItem:
        metadata = replace(item.metadata, **changes)
        return self.replan(item, metadata)

    def restore_processed_manifest(self, manifest_path: str | Path) -> list[Path]:
        manifest_file = Path(manifest_path)
        data = read_json(manifest_file, {}) or {}
        if data.get("state") != "processed":
            raise ValueError(f"Manifest is not a processed ingest record: {manifest_file}")

        processed_source = Path(str(data.get("processed_source_path") or ""))
        original_source = Path(str(data.get("source_path") or ""))
        if not processed_source.is_file():
            raise FileNotFoundError(f"Processed source was not found: {processed_source}")
        self._require_incoming_path(processed_source)
        self._require_incoming_path(original_source)

        restored = [_copy_restored_file(processed_source, original_source)]
        for companion in data.get("companions") or []:
            if not isinstance(companion, dict):
                continue
            processed_companion = Path(str(companion.get("processed_source_path") or ""))
            original_companion = Path(str(companion.get("source_path") or ""))
            if not processed_companion.is_dir() or not original_companion:
                continue
            self._require_incoming_path(processed_companion)
            self._require_incoming_path(original_companion)
            companion_target = _unique_path(original_companion)
            companion_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(processed_companion, companion_target)
            restored.append(companion_target)

        history = data.get("restores") if isinstance(data.get("restores"), list) else []
        history.append(
            {
                "restored_at": datetime.now().isoformat(timespec="seconds"),
                "paths": [str(path) for path in restored],
            }
        )
        data["restores"] = history
        write_json(manifest_file, data)
        return restored

    def _require_incoming_path(self, path: Path) -> None:
        try:
            path.resolve().relative_to(self.incoming_root.resolve())
        except ValueError as exc:
            raise ValueError(f"Restore path is outside incoming root: {path}") from exc

    def _infer_metadata(self, source: Path) -> IngestMetadata:
        relative = self._relative_to_incoming(source)
        parts = [part.lower() for part in relative.parts]
        stem_tokens = [token for token in re.split(r"[_\-. ]+", source.stem) if token]
        delivery_date = self._date_from_path(source)
        delivery_text = delivery_date.strftime("%Y%m%d") if delivery_date else ""
        extension = source.suffix.lower().lstrip(".")

        episode, sequence = self._infer_editorial_identity(source)
        episode = episode or _first_match(EP_RE, source.name, "episode") or "ep001"
        sequence = sequence or _first_match(SEQ_RE, source.name, "sequence") or "sq010"
        if sequence.startswith("seq"):
            sequence = "sq" + sequence[3:]
        shot = _first_match(SHOT_RE, source.name, "shot") or ""
        department = self._infer_department(stem_tokens)
        subset = self._infer_subset(stem_tokens, department)

        is_editorial_delivery = bool(
            parts
            and source.suffix.lower() in EDITORIAL_EXTENSIONS
            and parts[0] in {"client", "editorial"}
        )
        if is_editorial_delivery:
            return IngestMetadata(
                target_type="Editorial",
                project=self.project_name,
                department=department or "editorial",
                subset=str(self.editorial_naming.get("subset") or "source"),
                format=extension,
                episode=episode,
                sequence=sequence,
                shot=shot,
                delivery_date=delivery_text,
            )
        if len(parts) >= 3 and parts[0] == "vendors":
            return IngestMetadata(
                target_type="Vendor",
                project=self.project_name,
                department=department,
                subset=subset,
                format=extension,
                episode=episode,
                sequence=sequence,
                shot=shot,
                vendor=relative.parts[1],
                delivery_date=delivery_text,
            )
        if shot:
            return IngestMetadata(
                target_type="Shot",
                project=self.project_name,
                department=department,
                subset=subset,
                format=extension,
                episode=episode,
                sequence=sequence,
                shot=shot,
                delivery_date=delivery_text,
            )
        if (episode != "ep001" or sequence != "sq010") and source.suffix.lower() in SEQUENCE_EXTENSIONS:
            return IngestMetadata(
                target_type="Sequence",
                project=self.project_name,
                department=department,
                subset=subset,
                format=extension,
                episode=episode,
                sequence=sequence,
                delivery_date=delivery_text,
            )

        asset = self._infer_asset_name(stem_tokens, department)
        if asset and source.suffix.lower() in ASSET_EXTENSIONS:
            return IngestMetadata(
                target_type="Asset",
                project=self.project_name,
                asset=asset,
                department=department,
                subset=subset,
                format=extension,
                delivery_date=delivery_text,
            )
        return IngestMetadata(project=self.project_name, format=extension, delivery_date=delivery_text)

    def _target_path(self, source: Path, metadata: IngestMetadata) -> tuple[Path | None, str]:
        target_type = metadata.target_type
        extension = source.suffix.lower()
        if target_type == "Editorial":
            if extension not in EDITORIAL_EXTENSIONS:
                return None, "extension is not editorial-ready"
            subset = metadata.subset or self._infer_editorial_subset(source)
            version = self._next_editorial_data_version(metadata.episode, metadata.sequence, subset)
            filename = self._editorial_filename(source, metadata)
            return (
                self.project_root
                / "editorial"
                / "data"
                / metadata.episode
                / metadata.sequence
                / subset
                / version
                / filename
            ), "editorial data copy"
        if target_type == "Vendor":
            if extension not in VENDOR_EXTENSIONS or not metadata.vendor:
                return None, "vendor or extension is unknown"
            delivery = metadata.delivery_date or datetime.now().strftime("%Y%m%d")
            return self.staging_root / "vendors" / metadata.vendor / delivery / source.name, "vendor staging copy"
        if target_type == "Asset":
            if extension not in ASSET_EXTENSIONS:
                return None, "extension is not asset data"
            if not metadata.asset or not metadata.department:
                return None, "asset and department are required"
            version = self._next_version(
                self.project_root
                / "assets"
                / metadata.category
                / metadata.group
                / metadata.asset
                / metadata.variant
                / "data"
                / metadata.department
                / metadata.subset
            )
            return (
                self.project_root
                / "assets"
                / metadata.category
                / metadata.group
                / metadata.asset
                / metadata.variant
                / "data"
                / metadata.department
                / metadata.subset
                / version
                / source.name
            ), "asset data copy"
        if target_type == "Shot":
            if extension not in SHOT_EXTENSIONS:
                return None, "extension is not shot data"
            if not metadata.shot or not metadata.department:
                return None, "shot and department are required"
            version = self._next_version(
                self.project_root
                / "shots"
                / metadata.episode
                / metadata.sequence
                / metadata.shot
                / "data"
                / metadata.department
                / metadata.format
                / metadata.subset
            )
            return (
                self.project_root
                / "shots"
                / metadata.episode
                / metadata.sequence
                / metadata.shot
                / "data"
                / metadata.department
                / metadata.format
                / metadata.subset
                / version
                / source.name
            ), "shot data copy"
        if target_type == "Sequence":
            if extension not in SEQUENCE_EXTENSIONS:
                return None, "extension is not sequence data"
            if not metadata.episode or not metadata.sequence or not metadata.department:
                return None, "episode, sequence, and department are required"
            version = self._next_version(
                self.project_root
                / "sequences"
                / metadata.episode
                / metadata.sequence
                / "data"
                / metadata.department
                / metadata.format
                / metadata.subset
            )
            return (
                self.project_root
                / "sequences"
                / metadata.episode
                / metadata.sequence
                / "data"
                / metadata.department
                / metadata.format
                / metadata.subset
                / version
                / source.name
            ), "sequence data copy"
        return None, "target type is unknown"

    def _write_processed_manifest(
        self,
        item: PlanItem,
        target_path: Path,
        processed_path: Path,
        checksum: str,
        *,
        companion: dict[str, Any] | None = None,
    ) -> Path:
        manifest = processed_path.with_suffix(processed_path.suffix + ".ingest.json")
        data = self._manifest_data(item, target_path, "processed", checksum=checksum)
        data["processed_source_path"] = str(processed_path)
        if companion:
            data["companions"] = [companion]
        return write_json(manifest, data)

    def _write_rejection_manifest(self, item: PlanItem, rejected_path: Path) -> Path:
        sidecar = rejected_path.with_suffix(rejected_path.suffix + ".reject.json")
        return write_json(sidecar, self._manifest_data(item, rejected_path, "rejected"))

    def _write_editorial_source_metadata(self, item: PlanItem, target_path: Path, checksum: str) -> Path:
        version_dir = target_path.parent
        source_root = version_dir.parent
        manifest_path = version_dir / "manifest.json"
        manifest = read_json(manifest_path, {}) or {}
        files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
        entry = {
            "name": target_path.name,
            "format": target_path.suffix.lower().lstrip("."),
            "source_name": item.source_path.name,
            "source_path": str(item.source_path),
            "sha1": checksum,
            "size": item.size,
        }
        files = [value for value in files if value.get("name") != target_path.name]
        files.append(entry)
        files.sort(key=lambda value: (value.get("format", ""), value.get("name", "")))
        manifest.update(
            {
                "schema": "smartpipeline.editorial_source.v1",
                "episode": item.metadata.episode,
                "sequence": item.metadata.sequence,
                "subset": item.metadata.subset,
                "version": version_dir.name,
                "received_at": item.metadata.delivery_date or datetime.now().strftime("%Y%m%d"),
                "comment": item.metadata.comment,
                "files": files,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        write_json(manifest_path, manifest)
        write_json(
            version_dir / "validation.json",
            {
                "status": "OK",
                "errors": [],
                "warnings": [],
                "files": [value["name"] for value in files],
                "validated_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        self._update_editorial_version_index(source_root, version_dir.name)
        return manifest_path

    def _update_editorial_version_index(self, source_root: Path, version: str) -> None:
        existing = read_json(source_root / "versions.json", []) or []
        by_version = {
            str(item.get("version")): dict(item)
            for item in existing
            if isinstance(item, dict) and item.get("version")
        }
        by_version[version] = {"version": version, "status": "latest"}
        for name, item in by_version.items():
            if name != version and item.get("status") == "latest":
                item["status"] = "ingested"
        versions = sorted(by_version.values(), key=lambda item: parse_version(str(item["version"])))
        write_json(source_root / "versions.json", versions)
        write_json(source_root / "latest.json", {"version": version, "path": f"{version}/manifest.json"})

    def _manifest_data(self, item: PlanItem, output_path: Path, state: str, *, checksum: str | None = None) -> dict[str, Any]:
        return {
            "state": state,
            "source_path": str(item.source_path),
            "output_path": str(output_path),
            "target_type": item.target_type,
            "action": item.action,
            "status": item.status,
            "reason": item.reason,
            "file_size": item.size,
            "sha1": checksum or _sha1(item.source_path),
            "metadata": asdict(item.metadata),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _rejected_path(self, source: Path, metadata: IngestMetadata) -> Path:
        delivery = metadata.delivery_date or datetime.now().strftime("%Y%m%d")
        return self.incoming_root / "_rejected" / delivery / source.name

    def _item(
        self,
        source: Path,
        target: Path | None,
        file_type: str,
        action: str,
        target_type: str,
        status: str,
        reason: str,
        metadata: IngestMetadata,
        *,
        selected: bool = False,
    ) -> PlanItem:
        return PlanItem(
            id=_stable_id(source),
            source_path=source,
            target_path=target,
            file_type=file_type,
            action=action,
            target_type=target_type,
            status=status,
            reason=reason,
            metadata=metadata,
            size=self._package_size(source),
            selected=selected,
        )

    def _package_size(self, source: Path) -> int:
        if not source.exists():
            return 0
        size = source.stat().st_size
        companion = self._fbm_companion(source)
        if companion:
            size += sum(path.stat().st_size for path in companion.rglob("*") if path.is_file())
        return size

    def _fbm_companion(self, source: Path) -> Path | None:
        if source.suffix.lower() != ".fbx" or not source.parent.exists():
            return None
        expected = source.stem.lower() + ".fbm"
        for sibling in source.parent.iterdir():
            if sibling.is_dir() and sibling.name.lower() == expected:
                return sibling
        return None

    @staticmethod
    def _is_fbm_member(path: Path) -> bool:
        return any(parent.suffix.lower() == ".fbm" for parent in path.parents)

    @staticmethod
    def _fbm_manifest(source: Path, target: Path) -> dict[str, Any]:
        files = []
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            files.append(
                {
                    "path": path.relative_to(source).as_posix(),
                    "size": path.stat().st_size,
                    "sha1": _sha1(path),
                }
            )
        return {
            "type": "fbm",
            "source_path": str(source),
            "output_path": str(target),
            "files": files,
        }

    def _resolve_template(self, value: str | None, fallback: Path) -> Path:
        if not value:
            return fallback
        return Path(value.format(project_root=self.project_root))

    def _relative_to_incoming(self, path: Path) -> Path:
        try:
            return path.relative_to(self.incoming_root)
        except ValueError:
            return Path(path.name)

    def _is_internal_incoming_path(self, path: Path, *, include_rejected: bool = False) -> bool:
        relative = self._relative_to_incoming(path)
        parts = relative.parts
        if not parts:
            return False
        if include_rejected and parts[0] == "_rejected":
            return any(part.startswith("_") for part in parts[1:])
        return any(part.startswith("_") for part in parts)

    def _is_rejected_path(self, path: Path) -> bool:
        relative = self._relative_to_incoming(path)
        return bool(relative.parts and relative.parts[0] == "_rejected")

    def _date_from_path(self, path: Path) -> date | None:
        for part in self._relative_to_incoming(path).parts:
            match = DATE_RE.search(part)
            if match:
                try:
                    return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                except ValueError:
                    return None
        return None

    def _infer_department(self, tokens: list[str]) -> str:
        configured = set(self.project_config.base.get("asset_depts") or []) | set(self.project_config.base.get("shot_depts") or [])
        for token in tokens:
            department = DEPARTMENT_ALIASES.get(token.lower(), token.lower())
            if department in configured or department in DEPARTMENT_ALIASES.values():
                return department
        return ""

    def _infer_subset(self, tokens: list[str], department: str) -> str:
        ignored = {department, "v001", "v002", "v003", "main", ""}
        for token in reversed(tokens):
            low = token.lower()
            if low in ignored or VERSION_RE.fullmatch(low) or DATE_RE.fullmatch(low):
                continue
            if low in DEPARTMENT_ALIASES:
                continue
            return low
        return "main"

    def _infer_editorial_subset(self, source: Path) -> str:
        extension = source.suffix.lower()
        if extension in {".mov", ".mp4"}:
            return "offline"
        if extension == ".wav":
            return "audio"
        if extension in {".edl", ".xml", ".otio"}:
            return "cut"
        return "main"

    def _infer_editorial_identity(self, source: Path) -> tuple[str, str]:
        value = "/".join(self._relative_to_incoming(source).parts)
        episode_prefixes = self.editorial_naming.get("episode_prefixes") or ["ep"]
        sequence_prefixes = self.editorial_naming.get("sequence_prefixes") or ["seq", "sq", "s"]
        episode = _prefixed_number(value, episode_prefixes)
        sequence = _prefixed_number(value, sequence_prefixes)
        return episode, sequence

    def _infer_asset_name(self, tokens: list[str], department: str) -> str:
        ignored = {department, "model", "rig", "look", "render", "cache", "main", "default"}
        for token in tokens:
            low = token.lower()
            if low in ignored or VERSION_RE.fullmatch(low) or DATE_RE.fullmatch(low) or SHOT_RE.fullmatch(low):
                continue
            return token.upper()
        return ""

    def _next_editorial_data_version(self, episode: str, sequence: str, subset: str) -> str:
        return self._next_version(self.project_root / "editorial" / "data" / episode / sequence / subset)

    def _next_version(self, root: Path) -> str:
        versions = [parse_version(path.name) for path in root.glob("v*") if path.is_dir()] if root.exists() else []
        return format_version(next_version([value for value in versions if value]))

    def _editorial_filename(self, source: Path, metadata: IngestMetadata) -> str:
        if metadata.subset == str(self.editorial_naming.get("subset") or "source"):
            template = str(self.editorial_naming.get("filename") or "{episode}_{sequence}{extension}")
            base_name = template.format(
                episode=metadata.episode,
                sequence=metadata.sequence,
                extension=source.suffix.lower(),
                stem=source.stem,
            )
            suffix = self._editorial_source_suffix(source, metadata)
            if suffix:
                return f"{Path(base_name).stem}_{suffix}{source.suffix.lower()}"
            return base_name
        if source.suffix.lower() in {".mov", ".mp4"}:
            return "offline" + source.suffix.lower()
        if source.suffix.lower() == ".wav":
            return "dialog" + source.suffix.lower()
        if source.suffix.lower() in {".edl", ".xml", ".otio"}:
            return f"{metadata.sequence}_cut{source.suffix.lower()}"
        return source.name

    @staticmethod
    def _editorial_source_suffix(source: Path, metadata: IngestMetadata) -> str:
        identity = re.search(
            rf"{re.escape(metadata.episode)}[_-]?{re.escape(metadata.sequence)}",
            source.stem,
            re.IGNORECASE,
        )
        if not identity:
            return ""
        suffix = source.stem[identity.end() :].strip("_- .")
        return re.sub(r"[^A-Za-z0-9_-]+", "_", suffix).strip("_")

    def _move_to_processed(self, source: Path) -> Path:
        processed_dir = source.parent / "_processed"
        processed_dir.mkdir(parents=True, exist_ok=True)
        target = _unique_path(processed_dir / source.name)
        shutil.move(str(source), str(target))
        return target


def _first_match(pattern: re.Pattern[str], value: str, group: str) -> str:
    match = pattern.search(value)
    return match.group(group).lower() if match else ""


def _prefixed_number(value: str, prefixes: list[str]) -> str:
    for prefix in sorted((str(item) for item in prefixes), key=len, reverse=True):
        match = re.search(
            rf"(?<![a-z0-9])(?P<token>{re.escape(prefix)}\d{{2,4}})(?![a-z0-9])",
            value,
            re.IGNORECASE,
        )
        if match:
            return match.group("token").lower()
    return ""


def _stable_id(path: Path) -> str:
    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}_{index:03d}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not create a unique path for: {path}")


def _copy_restored_file(source: Path, target: Path) -> Path:
    restored_target = _unique_path(target)
    restored_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, restored_target)
    return restored_target
