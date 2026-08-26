from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig, load_config, studio_config_dir
from smartlib.core.metadata import read_json, write_json
from smartlib.core.path_resolver import ProjectPaths
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
EDITORIAL_EXTENSIONS = {".aaf", ".edl", ".mov", ".mp4", ".otio", ".xml"}
ASSET_EXTENSIONS = {".abc", ".fbx", ".ma", ".mb", ".tga", ".tif", ".tiff", ".usd", ".usda", ".usdc"}
SHOT_EXTENSIONS = {".abc", ".fbx", ".mov", ".mp4", ".usd", ".usda", ".usdc", ".wav"}
SEQUENCE_EXTENSIONS = SHOT_EXTENSIONS | {".edl", ".otio", ".xml"}
INTAKE_EXTENSIONS = SUPPORTED_EXTENSIONS | {".rar", ".7z"}

DATE_RE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")
DELIVERY_FOLDER_RE = re.compile(r"^20\d{6}_\d{2}$")
VERSION_RE = re.compile(r"v\d{3,}", re.IGNORECASE)
SHOT_RE = re.compile(r"(?<![a-z0-9])(?P<shot>sh\d{3,4})(?![a-z0-9])", re.IGNORECASE)
EP_RE = re.compile(r"(?<![a-z0-9])(?P<episode>ep\d{2,4})(?![a-z0-9])", re.IGNORECASE)
SEQ_RE = re.compile(r"(?<![a-z0-9])(?P<sequence>(?:sq|seq)\d{2,4})(?![a-z0-9])", re.IGNORECASE)
VIRTUAL_CAMERA_RE = re.compile(
    r"(?P<episode>ep\d{2,4})(?P<sequence>(?:s|sq|seq)\d{2,4})"
    r".*?(?P<target>c\d{2,4}).*?(?P<take>take\d+)",
    re.IGNORECASE,
)
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
    "assembly": "assembly",
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
        return self.action in {"copy", "reject", "expand_package"} and self.status in {"Ready", "Reject"}


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
      incoming/client/YYYYMMDD_##/
      incoming/vendors/<vendor>/YYYYMMDD_##/
      incoming/assets/YYYYMMDD_##/
      incoming/shots/YYYYMMDD_##/
      incoming/_rejected/YYYYMMDD/

    ``incoming`` is an immutable receipt area. Known files are copied into
    production roots while processing state stays in delivery/processed.json.
    """

    def __init__(self, project_config: ProjectConfig):
        self.project_config = project_config
        project_root = project_config.project_root
        if project_root is None:
            raise RuntimeError("project_root is not set in templates_base.yml")
        self.project_root = project_root
        templates = project_config.base.get("templates") or {}
        self.paths = ProjectPaths(project_root, project_config.templates, project_config.project_name)
        self.incoming_root = self._resolve_template(templates.get("incoming_root"), project_root / "incoming")
        self.staging_root = self._resolve_template(templates.get("staging_root"), project_root / "staging")
        self.project_name = project_config.project_name
        self.naming_config = project_config.load("naming.yml")
        self.editorial_naming = (self.naming_config.get("smart_ingest") or {}).get("editorial") or {}

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
            if path.name == "processed.json":
                continue
            if path.suffix.lower() == ".json" and path.name.endswith((".reject.json", ".ingest.json")):
                continue
            if not path.is_file() or self._is_internal_incoming_path(path, include_rejected=include_rejected):
                continue
            if self._is_fbm_member(path):
                continue
            if self._is_processed_source(path):
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
        if not self._is_rejected_path(source) and self._delivery_root(source) is None:
            return self._item(
                source,
                None,
                file_type,
                "none",
                meta.target_type,
                "Needs Metadata",
                "delivery folder must match YYYYMMDD_##",
                meta,
            )
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

        package = self._smart_package_manifest(source) if metadata is None else None
        if package:
            target = dict(package.get("target") or {})
            ingest = dict(package.get("ingest") or {})
            meta = IngestMetadata(
                target_type=str(target.get("target_type") or package.get("package_type") or "").title(),
                project=str(target.get("project") or self.project_name), asset=str(target.get("asset") or ""),
                category=str(target.get("category") or "CH"), group=str(target.get("group") or "main"),
                variant=str(target.get("variant") or "default"), department=str(target.get("department") or "assembly"),
                subset=str(target.get("subset") or "vendor"), format=str(target.get("format") or "zip"),
                episode=str(target.get("episode") or "ep001"), sequence=str(target.get("sequence") or "sq010"),
                shot=str(target.get("shot") or ""), vendor=str((package.get("delivery") or {}).get("received_from") or ""),
                delivery_date=str((package.get("delivery") or {}).get("delivery_date") or ""),
                comment=str((package.get("delivery") or {}).get("comment") or "Smart Delivery package"),
            )
            target_path = self._package_target_root(ingest.get("expected_target_root"), meta)
            if target_path.exists():
                return self._item(source, target_path, "ZIP", "none", meta.target_type, "Conflict", "package target already exists", meta)
            return self._item(source, target_path, "ZIP", "expand_package", meta.target_type, "Ready", "manifest package expansion", meta, selected=True)

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
        editorial_records: list[tuple[PlanItem, Path]] = []

        actionable = [
            item
            for item in items
            if item.selected and item.actionable and item.target_path is not None
        ]
        delivery_roots = sorted(
            {root for item in actionable if (root := self._delivery_root(item.source_path))},
            key=lambda path: str(path).lower(),
        )
        locks: list[Path] = []
        try:
            for delivery_root in delivery_roots:
                locks.append(self._acquire_delivery_lock(delivery_root))

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
                    companion_manifest = None
                    if companion:
                        companion_target = item.target_path.with_suffix(".fbm")
                        shutil.copytree(companion, companion_target)
                        companion_manifest = self._fbm_manifest(companion, companion_target)
                    copied.append(item.target_path)
                    processed_sources.append(item.source_path)
                    state_path = self._record_processed(
                        item,
                        item.target_path,
                        checksum,
                        companion=companion_manifest,
                    )
                    if state_path not in manifests:
                        manifests.append(state_path)
                    if item.target_type == "Editorial":
                        manifests.append(self._write_editorial_source_metadata(item, item.target_path, checksum))
                        editorial_records.append((item, item.target_path))
                    elif item.target_type == "Asset":
                        manifests.append(self._write_asset_data_metadata(item, item.target_path, checksum))
                    elif item.target_type == "Sequence" and item.metadata.department == "virtual_camera":
                        manifests.append(self._write_sequence_package_metadata(item, item.target_path, checksum))
                elif item.action == "reject":
                    shutil.copy2(item.source_path, item.target_path)
                    rejected.append(item.target_path)
                    manifests.append(self._write_rejection_manifest(item, item.target_path))
                elif item.action == "expand_package":
                    checksum = _sha1(item.source_path)
                    written = self._expand_smart_package(item.source_path, item.target_path)
                    copied.extend(written)
                    processed_sources.append(item.source_path)
                    manifests.append(item.target_path / "manifest.json")
                    state_path = self._record_processed(item, item.target_path, checksum)
                    if state_path not in manifests:
                        manifests.append(state_path)
                else:
                    skipped.append(item)
            manifests.extend(self._write_editorial_delivery_manifests(editorial_records))
        finally:
            for lock_path in reversed(locks):
                self._release_delivery_lock(lock_path)
        result = IngestRunResult(copied, rejected, processed_sources, skipped, manifests)
        self._notify_ingest_completed(result)
        return result

    @staticmethod
    def _smart_package_manifest(source: Path) -> dict[str, Any] | None:
        if source.suffix.lower() != ".zip" or not zipfile.is_zipfile(source):
            return None
        try:
            with zipfile.ZipFile(source) as archive:
                data = json.loads(archive.read("manifest.json").decode("utf-8-sig"))
        except (KeyError, ValueError, UnicodeDecodeError, OSError):
            return None
        if not isinstance(data, dict) or not str(data.get("schema") or "").startswith("smart_ingest."):
            return None
        if not bool((data.get("ingest") or {}).get("auto_plan", True)):
            return None
        return data

    def _package_target_root(self, expected: Any, metadata: IngestMetadata) -> Path:
        text = str(expected or "").replace("\\", "/").strip("/")
        if not text and metadata.target_type == "Asset":
            text = f"production/assets/{metadata.category}/{metadata.group}/{metadata.asset}/{metadata.variant}/data/{metadata.department}/{metadata.subset}/v###"
        if not text:
            raise ValueError("Smart package manifest has no expected_target_root")
        relative = Path(text)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Package target must be project-relative: {text}")
        if relative.name.lower() == "v###":
            parent = self.project_root / relative.parent
            return parent / self._next_version(parent)
        return self.project_root / relative

    @staticmethod
    def _expand_smart_package(source: Path, target: Path) -> list[Path]:
        target.mkdir(parents=True, exist_ok=False)
        written: list[Path] = []
        with zipfile.ZipFile(source) as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8-sig"))
            for info in archive.infolist():
                member = Path(info.filename.replace("\\", "/"))
                if info.is_dir() or member.name == "manifest.json":
                    continue
                if member.is_absolute() or ".." in member.parts:
                    raise ValueError(f"Unsafe ZIP member: {info.filename}")
                destination = target / member
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source_stream, destination.open("wb") as target_stream:
                    shutil.copyfileobj(source_stream, target_stream)
                written.append(destination)
        manifest["ingested_from"] = source.as_posix()
        write_json(target / "manifest.json", manifest)
        main_file = str(manifest.get("main_file") or "")
        write_json(target.parent / "latest.json", {"version": target.name, "path": f"{target.name}/{main_file}", "manifest": f"{target.name}/manifest.json"})
        versions_path = target.parent / "versions.json"
        versions = read_json(versions_path, []) or []
        for row in versions:
            if isinstance(row, dict) and row.get("status") == "latest": row["status"] = "available"
        versions.append({"version": target.name, "status": "latest", "comment": str((manifest.get("delivery") or {}).get("comment") or "")})
        write_json(versions_path, versions)
        return written

    def ignore_items(self, items: list[PlanItem], *, reason: str = "not related to CG ingest") -> IngestRunResult:
        processed_sources: list[Path] = []
        skipped: list[PlanItem] = []
        manifests: list[Path] = []
        ignore_items = [item for item in items if item.selected and item.source_path.exists()]
        delivery_roots = sorted(
            {root for item in ignore_items if (root := self._delivery_root(item.source_path))},
            key=lambda path: str(path).lower(),
        )
        locks: list[Path] = []
        try:
            for delivery_root in delivery_roots:
                locks.append(self._acquire_delivery_lock(delivery_root))
            for item in items:
                if not item.selected or not item.source_path.exists():
                    skipped.append(item)
                    continue
                checksum = _sha1(item.source_path)
                ignored = replace(
                    item,
                    target_path=item.source_path,
                    action="ignore",
                    target_type="Ignored",
                    status="Ignored",
                    reason=reason,
                    metadata=replace(item.metadata, target_type="Ignored", comment=reason),
                )
                state_path = self._record_processed(
                    ignored,
                    ignored.source_path,
                    checksum,
                    status="ignored",
                )
                if state_path not in manifests:
                    manifests.append(state_path)
                processed_sources.append(item.source_path)
        finally:
            for lock_path in reversed(locks):
                self._release_delivery_lock(lock_path)
        return IngestRunResult([], [], processed_sources, skipped, manifests)

    def update_item_metadata(self, item: PlanItem, **changes: Any) -> PlanItem:
        metadata = replace(item.metadata, **changes)
        return self.replan(item, metadata)

    def sequence_data_types(self) -> list[str]:
        config = self.project_config.load("templates_shots.yml")
        values = (config.get("sequence_data") or {}).get("types") or []
        return _config_list(values) or ["virtual_camera", "mocap"]

    def editorial_data_roles(self) -> list[str]:
        roles = self.editorial_naming.get("roles") or {}
        return [str(value).strip() for value in roles if str(value).strip()]

    def asset_categories(self) -> list[str]:
        return self._child_dir_names(self.paths.assets_root(), fallback=["CH", "BG", "PR", "characters"])

    def asset_groups(self, category: str) -> list[str]:
        return self._child_dir_names(self.paths.assets_root() / category, fallback=["main"])

    def asset_names(self, category: str, group: str) -> list[str]:
        return self._child_dir_names(self.paths.assets_root() / category / group)

    def asset_variants(self, category: str, group: str, asset: str) -> list[str]:
        return self._child_dir_names(self.paths.assets_root() / category / group / asset, fallback=["default"])

    def asset_departments(self) -> list[str]:
        return _unique_preserve_order([*self._config_asset_departments(), "assembly"])

    def asset_subsets(self, department: str) -> list[str]:
        defaults = {
            "assembly": ["client"],
            "model": ["render", "hires", "proxy", "main"],
            "rig": ["main", "layout"],
            "look": ["main", "render"],
        }
        return defaults.get(department.strip().lower(), ["main"])

    def _config_asset_departments(self) -> list[str]:
        return _config_list(self.project_config.base.get("asset_depts") or [])

    @staticmethod
    def _child_dir_names(root: Path, *, fallback: list[str] | None = None) -> list[str]:
        values = []
        if root.exists():
            values = sorted((path.name for path in root.iterdir() if path.is_dir()), key=str.lower)
        return values or list(fallback or [])

    def restore_processed_manifest(self, manifest_path: str | Path) -> list[Path]:
        """Restore a legacy _processed record.

        New deliveries keep originals in place and use retry_processed_record().
        """
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

    def retry_processed_record(self, state_path: str | Path, record_key: str) -> Path:
        state_file = Path(state_path)
        delivery_root = state_file.parent
        if state_file.name != "processed.json" or self._delivery_root(state_file) != delivery_root:
            raise ValueError(f"Invalid delivery state path: {state_file}")
        lock_path = self._acquire_delivery_lock(delivery_root)
        try:
            data = read_json(state_file, {}) or {}
            files = data.get("files") if isinstance(data.get("files"), dict) else {}
            record = files.get(record_key)
            if not isinstance(record, dict):
                raise KeyError(f"Processed record was not found: {record_key}")
            source_path = delivery_root / Path(record_key)
            if not source_path.is_file():
                raise FileNotFoundError(f"Incoming source was not found: {source_path}")
            record["status"] = "pending"
            retries = record.get("retries") if isinstance(record.get("retries"), list) else []
            retries.append(
                {
                    "requested_at": datetime.now().isoformat(timespec="seconds"),
                    "user": os.environ.get("USERNAME") or os.environ.get("USER") or "",
                }
            )
            record["retries"] = retries
            files[record_key] = record
            data["files"] = files
            data["updated_at"] = datetime.now().isoformat(timespec="seconds")
            _write_json_atomic(state_file, data)
            return source_path
        finally:
            self._release_delivery_lock(lock_path)

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
        virtual_camera = VIRTUAL_CAMERA_RE.search(source.stem)
        if virtual_camera and source.suffix.lower() in {".fbx", ".mov", ".mp4"}:
            return IngestMetadata(
                target_type="Sequence",
                project=self.project_name,
                department="virtual_camera",
                subset=virtual_camera.group("take").lower(),
                format=extension,
                episode=virtual_camera.group("episode").lower(),
                sequence=_normalize_sequence_code(virtual_camera.group("sequence")),
                delivery_date=delivery_text,
            )
        if source.suffix.lower() == ".wav":
            return IngestMetadata(
                target_type="Shot",
                project=self.project_name,
                department="audio",
                subset=self._infer_audio_subset(source),
                format=extension,
                episode=episode,
                sequence=sequence,
                shot=shot or self._shot_token(source.stem),
                delivery_date=delivery_text,
            )

        is_editorial_delivery = bool(
            parts
            and source.suffix.lower() in EDITORIAL_EXTENSIONS
            and parts[0] in {"client", "editorial"}
        )
        if is_editorial_delivery:
            editorial_subset, editorial_shot = self._editorial_role(source, episode, sequence)
            return IngestMetadata(
                target_type="Editorial",
                project=self.project_name,
                department=department or "editorial",
                subset=editorial_subset,
                format=extension,
                episode=episode,
                sequence=sequence,
                shot=editorial_shot or shot,
                delivery_date=delivery_text,
            )
        if len(parts) >= 3 and parts[0] == "vendors":
            return IngestMetadata(
                target_type="Intake",
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
            if subset == "shot_media" and not metadata.shot:
                return None, "shot is required for editorial shot_media"
            version = self._next_editorial_data_version(metadata.episode, metadata.sequence, subset)
            filename = self._editorial_filename(source, metadata)
            return (
                self._editorial_data_root()
                / "data"
                / metadata.episode
                / metadata.sequence
                / subset
                / version
                / filename
            ), "editorial data copy"
        if target_type in {"Intake", "Vendor"}:
            if extension not in INTAKE_EXTENSIONS:
                return None, "extension is not intake-ready"
            delivery = metadata.delivery_date or datetime.now().strftime("%Y%m%d")
            source_name = metadata.vendor or "external"
            return self.paths.production_root() / "intake" / source_name / delivery / source.name, "intake archive copy"
        if target_type == "Asset":
            if extension not in ASSET_EXTENSIONS:
                return None, "extension is not asset data"
            if not metadata.asset or not metadata.department:
                return None, "asset and department are required"
            package_root = self._asset_data_package_root(metadata)
            version = self._next_version(package_root)
            return (
                package_root
                / version
                / source.name
            ), "asset data copy"
        if target_type == "Shot":
            if extension not in SHOT_EXTENSIONS:
                return None, "extension is not shot data"
            if not metadata.shot or not metadata.department:
                return None, "shot and department are required"
            if metadata.department == "audio" or extension == ".wav":
                shot_data_root = (
                    self.project_root
                    / "shots"
                    / metadata.episode
                    / metadata.sequence
                    / metadata.shot
                    / "data"
                    / "audio"
                    / (metadata.subset or "dialog")
                )
                version = self._next_version(shot_data_root)
                return (
                    shot_data_root
                    / version
                    / source.name
                ), "shot audio data copy"
            shot_data_root = (
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
            version = self._next_version(shot_data_root)
            return (
                shot_data_root
                / version
                / source.name
            ), "shot data copy"
        if target_type == "Sequence":
            if extension not in SEQUENCE_EXTENSIONS:
                return None, "extension is not sequence data"
            if not metadata.episode or not metadata.sequence or not metadata.department:
                return None, "episode, sequence, and department are required"
            sequence_data_root = (
                self.project_root
                / "sequences"
                / metadata.episode
                / metadata.sequence
                / "data"
                / metadata.department
            )
            if metadata.department == "virtual_camera":
                if not metadata.subset or metadata.subset == "main":
                    return None, "take is required for virtual_camera data"
                package_root = sequence_data_root / metadata.subset
            else:
                package_root = sequence_data_root / metadata.format / metadata.subset
            version = self._next_version(package_root)
            return (
                package_root
                / version
                / source.name
            ), "sequence data copy"
        return None, "target type is unknown"

    def _write_sequence_package_metadata(
        self,
        item: PlanItem,
        target_path: Path,
        checksum: str,
    ) -> Path:
        version_dir = target_path.parent
        package_root = version_dir.parent
        manifest_path = version_dir / "manifest.json"
        manifest = read_json(manifest_path, {}) or {}
        files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
        files[target_path.suffix.lower().lstrip(".")] = {
            "name": target_path.name,
            "path": target_path.name,
            "source": item.source_path.name,
            "sha1": checksum,
            "size": item.size,
        }
        manifest.update(
            {
                "schema": "smartpipeline.sequence_data.v1",
                "episode": item.metadata.episode,
                "sequence": item.metadata.sequence,
                "data_type": item.metadata.department,
                "take": item.metadata.subset,
                "version": version_dir.name,
                "files": files,
                "comment": item.metadata.comment,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        write_json(manifest_path, manifest)
        self._update_editorial_version_index(package_root, version_dir.name)
        return manifest_path

    def _write_asset_data_metadata(
        self,
        item: PlanItem,
        target_path: Path,
        checksum: str,
    ) -> Path:
        version_dir = target_path.parent
        package_root = version_dir.parent
        manifest_path = version_dir / "manifest.json"
        manifest = read_json(manifest_path, {}) or {}
        file_format = target_path.suffix.lower().lstrip(".")
        files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
        if item.metadata.department == "assembly" and item.metadata.subset == "client":
            files["assembly"] = target_path.name
        else:
            files[file_format] = {
                "name": target_path.name,
                "path": target_path.name,
                "source": item.source_path.name,
                "sha1": checksum,
                "size": item.size,
            }
        manifest.update(
            {
                "schema": "smartpipeline.asset_data.v1",
                "asset": item.metadata.asset,
                "category": item.metadata.category,
                "group": item.metadata.group,
                "variant": item.metadata.variant or "default",
                "data_type": item.metadata.department,
                "subset": item.metadata.subset,
                "format": file_format,
                "version": version_dir.name,
                "files": files,
                "source_file": str(item.source_path).replace("\\", "/"),
                "sha1": checksum,
                "size": item.size,
                "imported_at": datetime.now().isoformat(timespec="seconds"),
                "comment": item.metadata.comment,
            }
        )
        write_json(manifest_path, manifest)
        latest: dict[str, Any] = {"version": version_dir.name, "path": f"{version_dir.name}/{target_path.name}"}
        if item.metadata.department == "assembly" and item.metadata.subset == "client":
            latest["manifest"] = f"{version_dir.name}/manifest.json"
        write_json(package_root / "latest.json", latest)
        self._update_version_index(package_root, version_dir.name, item.metadata.comment)
        return manifest_path

    def _record_processed(
        self,
        item: PlanItem,
        target_path: Path,
        checksum: str,
        *,
        companion: dict[str, Any] | None = None,
        status: str = "processed",
    ) -> Path:
        delivery_root = self._delivery_root(item.source_path)
        if delivery_root is None:
            raise ValueError(f"Delivery folder must match YYYYMMDD_##: {item.source_path}")
        manifest = delivery_root / "processed.json"
        data = read_json(manifest, {}) or {}
        files = data.get("files") if isinstance(data.get("files"), dict) else {}
        key = item.source_path.relative_to(delivery_root).as_posix()
        stat = item.source_path.stat()
        record = self._manifest_data(item, target_path, "processed", checksum=checksum)
        record.update(
            {
                "status": status,
                "source": key,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "processed_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        if companion:
            record["companions"] = [companion]
        files[key] = record
        data.update(
            {
                "schema": "smartpipeline.ingest_state.v1",
                "delivery": delivery_root.name,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "files": files,
            }
        )
        return _write_json_atomic(manifest, data)

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
                "schema": "smartpipeline.editorial_data.v1",
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

    def _write_editorial_delivery_manifests(self, records: list[tuple[PlanItem, Path]]) -> list[Path]:
        grouped: dict[tuple[str, str, str], list[tuple[PlanItem, Path]]] = {}
        for item, target_path in records:
            delivery_id = self._delivery_id(item.source_path)
            key = (item.metadata.episode, item.metadata.sequence, delivery_id)
            grouped.setdefault(key, []).append((item, target_path))

        written = []
        for (episode, sequence, delivery_id), values in grouped.items():
            manifest_path = (
                self._editorial_data_root()
                / "data"
                / episode
                / sequence
                / "deliveries"
                / delivery_id
                / "manifest.json"
            )
            data = read_json(manifest_path, {}) or {}
            entries = data.get("entries") if isinstance(data.get("entries"), list) else []
            by_output = {str(entry.get("output")): dict(entry) for entry in entries if isinstance(entry, dict)}
            for item, target_path in values:
                relative_output = target_path.relative_to(
                    self._editorial_data_root() / "data" / episode / sequence
                ).as_posix()
                by_output[relative_output] = {
                    "role": item.metadata.subset,
                    "shot": item.metadata.shot or None,
                    "version": target_path.parent.name,
                    "file": target_path.name,
                    "output": relative_output,
                    "source": item.source_path.name,
                }
            data.update(
                {
                    "schema": "smartpipeline.editorial_delivery.v1",
                    "episode": episode,
                    "sequence": sequence,
                    "delivery": delivery_id,
                    "received_at": values[0][0].metadata.delivery_date
                    or datetime.now().strftime("%Y%m%d"),
                    "entries": sorted(by_output.values(), key=lambda entry: entry["output"]),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
            written.append(write_json(manifest_path, data))
        return written

    def _update_editorial_version_index(self, source_root: Path, version: str) -> None:
        self._update_version_index(source_root, version)
        write_json(source_root / "latest.json", {"version": version, "path": f"{version}/manifest.json"})

    def _update_version_index(self, source_root: Path, version: str, comment: str = "") -> None:
        existing = read_json(source_root / "versions.json", []) or []
        by_version = {
            str(item.get("version")): dict(item)
            for item in existing
            if isinstance(item, dict) and item.get("version")
        }
        by_version[version] = {"version": version, "status": "latest"}
        if comment:
            by_version[version]["comment"] = comment
        for name, item in by_version.items():
            if name != version and item.get("status") == "latest":
                item["status"] = "ingested"
        versions = sorted(by_version.values(), key=lambda item: parse_version(str(item["version"])))
        write_json(source_root / "versions.json", versions)

    def _asset_data_package_root(self, metadata: IngestMetadata) -> Path:
        return (
            self.paths.assets_root()
            / metadata.category
            / metadata.group
            / metadata.asset
            / (metadata.variant or "default")
            / "data"
            / metadata.department
            / (metadata.subset or "main")
        )

    def _editorial_data_root(self) -> Path:
        return self.paths.editorial_data_root().parent

    def _notify_ingest_completed(self, result: IngestRunResult) -> None:
        webhook_url = self._google_chat_webhook_url()
        if not webhook_url:
            return
        text = self._google_chat_ingest_message(result)
        try:
            _post_json(webhook_url, {"text": text})
        except Exception as exc:
            print(f"Smart Ingest Google Chat notification failed: {exc}")

    def _google_chat_webhook_url(self) -> str:
        smart_ingest = self._smart_ingest_secrets()
        direct_url = str(smart_ingest.get("google_chat_webhook_url") or "").strip()
        notifications = smart_ingest.get("notifications") if isinstance(smart_ingest.get("notifications"), dict) else {}
        google_chat = notifications.get("google_chat") if isinstance(notifications.get("google_chat"), dict) else {}
        if google_chat and google_chat.get("enabled") is False:
            return ""
        nested_url = str(google_chat.get("webhook_url") or "").strip()
        env_name = str(google_chat.get("webhook_env") or "").strip()
        env_url = str(os.environ.get(env_name) or "").strip() if env_name else ""
        return nested_url or direct_url or env_url

    def _smart_ingest_secrets(self) -> dict[str, Any]:
        secrets_path = self._secrets_path()
        if secrets_path is None:
            return {}
        data = load_config(secrets_path)
        smart_ingest = data.get("smart_ingest") if isinstance(data, dict) else {}
        return smart_ingest if isinstance(smart_ingest, dict) else {}

    def _secrets_path(self) -> Path | None:
        configured_dir = studio_config_dir()
        candidates = []
        if configured_dir:
            candidates.append(configured_dir / "secrets.yml")
        for path in [self.project_config.config_dir, *self.project_config.config_dir.parents]:
            if path.name == "smartprojects":
                candidates.append(path / "secrets.yml")
                break
        for path in candidates:
            if path.exists():
                return path
        return None

    def _google_chat_ingest_message(self, result: IngestRunResult) -> str:
        lines = [
            "Smart Ingest completed",
            f"Project: {self.project_name}",
            f"Copied: {len(result.copied)}",
            f"Rejected: {len(result.rejected)}",
            f"Skipped: {len(result.skipped)}",
        ]
        if result.copied:
            lines.append("")
            lines.append("Copied files:")
            for path in result.copied[:10]:
                lines.append(f"- {path.name} -> {path}")
            if len(result.copied) > 10:
                lines.append(f"- ... and {len(result.copied) - 10} more")
        if result.rejected:
            lines.append("")
            lines.append("Rejected files:")
            for path in result.rejected[:10]:
                lines.append(f"- {path.name} -> {path}")
            if len(result.rejected) > 10:
                lines.append(f"- ... and {len(result.rejected) - 10} more")
        return "\n".join(lines)

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
        if ".ingest.lock" in parts:
            return True
        if include_rejected and parts[0] == "_rejected":
            return any(part.startswith("_") for part in parts[1:])
        return any(part.startswith("_") for part in parts)

    def _delivery_root(self, path: Path) -> Path | None:
        try:
            relative = path.resolve().relative_to(self.incoming_root.resolve())
        except ValueError:
            return None
        current = self.incoming_root
        for part in relative.parts:
            current = current / part
            if DELIVERY_FOLDER_RE.fullmatch(part):
                return current
        return None

    def _is_processed_source(self, path: Path) -> bool:
        delivery_root = self._delivery_root(path)
        if delivery_root is None:
            return False
        state = read_json(delivery_root / "processed.json", {}) or {}
        files = state.get("files") if isinstance(state.get("files"), dict) else {}
        try:
            key = path.relative_to(delivery_root).as_posix()
        except ValueError:
            return False
        record = files.get(key)
        if not isinstance(record, dict) or record.get("status") not in {"processed", "ignored"}:
            return False
        stat = path.stat()
        return record.get("size") == stat.st_size and record.get("mtime_ns") == stat.st_mtime_ns

    def _acquire_delivery_lock(self, delivery_root: Path) -> Path:
        self._require_incoming_path(delivery_root)
        lock_path = delivery_root / ".ingest.lock"
        try:
            lock_path.mkdir()
        except FileExistsError as exc:
            owner = read_json(lock_path / "owner.json", {}) or {}
            owner_text = " / ".join(
                value
                for value in (
                    str(owner.get("user") or ""),
                    str(owner.get("machine") or ""),
                    str(owner.get("acquired_at") or ""),
                )
                if value
            )
            suffix = f"\nOwner: {owner_text}" if owner_text else ""
            raise RuntimeError(f"Delivery is locked by another ingest process: {delivery_root}{suffix}") from exc
        owner = {
            "schema": "smartpipeline.ingest_lock.v1",
            "delivery": delivery_root.name,
            "user": os.environ.get("USERNAME") or os.environ.get("USER") or "",
            "machine": socket.gethostname(),
            "pid": os.getpid(),
            "acquired_at": datetime.now().isoformat(timespec="seconds"),
        }
        _write_json_atomic(lock_path / "owner.json", owner)
        return lock_path

    def _release_delivery_lock(self, lock_path: Path) -> None:
        self._require_incoming_path(lock_path)
        if lock_path.name != ".ingest.lock":
            raise ValueError(f"Refusing to remove non-ingest lock: {lock_path}")
        if lock_path.exists():
            shutil.rmtree(lock_path)

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
        configured.add("assembly")
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
        if extension in {".edl", ".xml", ".otio"}:
            return "cut"
        return "main"

    def _infer_audio_subset(self, source: Path) -> str:
        tokens = {token.lower() for token in re.split(r"[_\-. ]+", source.stem) if token}
        if tokens & {"mx", "music", "bgm"}:
            return "music"
        if tokens & {"sfx", "fx"}:
            return "sfx"
        if tokens & {"amb", "ambience", "ambient"}:
            return "ambience"
        return "dialog"

    def _editorial_role(self, source: Path, episode: str, sequence: str) -> tuple[str, str]:
        extension = source.suffix.lower().lstrip(".")
        edit_extensions = self._editorial_role_extensions("edit_source", {"aaf", "edl", "xml", "otio"})
        sequence_extensions = self._editorial_role_extensions("offline", {"mov", "mp4"})
        shot_extensions = self._editorial_role_extensions("shot_media", {"mov", "mp4"})
        if extension in edit_extensions:
            return "edit_source", ""
        if extension in shot_extensions:
            suffix = self._editorial_identity_suffix(source, episode, sequence)
            shot = self._shot_token(suffix)
            if shot:
                return f"shot_media/{shot}", shot
        if extension in sequence_extensions:
            return "offline", ""
        return "edit_source", ""

    def _editorial_role_extensions(self, role: str, fallback: set[str]) -> set[str]:
        roles = self.editorial_naming.get("roles") or {}
        config = roles.get(role) if isinstance(roles, dict) else None
        values = config.get("extensions") if isinstance(config, dict) else None
        parsed = _config_list(values)
        return {value.lower().lstrip(".") for value in parsed} if parsed else fallback

    def _infer_editorial_identity(self, source: Path) -> tuple[str, str]:
        value = "/".join(self._relative_to_incoming(source).parts)
        episode_prefixes = _config_list(self.editorial_naming.get("episode_prefixes")) or ["ep"]
        sequence_prefixes = _config_list(self.editorial_naming.get("sequence_prefixes")) or ["seq", "sq", "s"]
        episode = _prefixed_number(value, episode_prefixes)
        sequence = _prefixed_number(value, sequence_prefixes)
        return episode, sequence

    def _infer_asset_name(self, tokens: list[str], department: str) -> str:
        ignored = {department, "model", "rig", "look", "render", "cache", "assembly", "client", "main", "default"}
        for token in tokens:
            low = token.lower()
            if low in ignored or VERSION_RE.fullmatch(low) or DATE_RE.fullmatch(low) or SHOT_RE.fullmatch(low):
                continue
            return token.upper()
        return ""

    def _next_editorial_data_version(self, episode: str, sequence: str, subset: str) -> str:
        return self._next_version(self._editorial_data_root() / "data" / episode / sequence / subset)

    def _next_version(self, root: Path) -> str:
        versions = [parse_version(path.name) for path in root.glob("v*") if path.is_dir()] if root.exists() else []
        return format_version(next_version([value for value in versions if value]))

    def _editorial_filename(self, source: Path, metadata: IngestMetadata) -> str:
        template = str(self.editorial_naming.get("filename") or "{episode}_{sequence}{extension}")
        base_name = template.format(
            episode=metadata.episode,
            sequence=metadata.sequence,
            extension=source.suffix.lower(),
            stem=source.stem,
        )
        if metadata.subset.startswith("shot_media/") and metadata.shot:
            return f"{Path(base_name).stem}_{metadata.shot}{source.suffix.lower()}"
        if metadata.subset in {"edit_source", "offline"}:
            return base_name
        if source.suffix.lower() in {".mov", ".mp4"}:
            return "offline" + source.suffix.lower()
        if source.suffix.lower() == ".wav":
            return "dialog" + source.suffix.lower()
        if source.suffix.lower() in {".edl", ".xml", ".otio"}:
            return f"{metadata.sequence}_cut{source.suffix.lower()}"
        return source.name

    @staticmethod
    def _editorial_identity_suffix(source: Path, episode: str, sequence: str) -> str:
        identity = re.search(
            rf"{re.escape(episode)}[_-]?{re.escape(sequence)}",
            source.stem,
            re.IGNORECASE,
        )
        if not identity:
            return ""
        suffix = source.stem[identity.end() :].strip("_- .")
        return re.sub(r"[^A-Za-z0-9_-]+", "_", suffix).strip("_")

    def _shot_token(self, value: str) -> str:
        prefixes = {"c", "sh"}
        for profile in (self.naming_config.get("shot_naming") or {}).get("profiles", {}).values():
            if isinstance(profile, dict) and profile.get("prefix"):
                prefixes.add(str(profile["prefix"]).lower())
        for prefix in sorted(prefixes, key=len, reverse=True):
            match = re.match(rf"(?P<shot>{re.escape(prefix)}\d+)(?:[_-]|$)", value, re.IGNORECASE)
            if match:
                return match.group("shot").lower()
        return ""

    def _delivery_id(self, source: Path) -> str:
        delivery_root = self._delivery_root(source)
        parent_name = delivery_root.name if delivery_root else ""
        delivery_date = self._date_from_path(source)
        value = parent_name or (delivery_date.strftime("%Y%m%d") if delivery_date else "delivery")
        return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_") or "delivery"


def _first_match(pattern: re.Pattern[str], value: str, group: str) -> str:
    match = pattern.search(value)
    return match.group(group).lower() if match else ""


def _normalize_sequence_code(value: str) -> str:
    match = re.fullmatch(r"(?P<prefix>seq|sq|s)(?P<number>\d+)", value, re.IGNORECASE)
    if not match:
        return value.lower()
    number = match.group("number")
    return f"{match.group('prefix').lower()}{number.zfill(max(3, len(number)))}"


def _config_list(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        return [item.strip().strip("'\"") for item in text.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen = set()
    unique = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


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


def _write_json_atomic(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    write_json(temporary, data)
    temporary.replace(path)
    return path


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        response_body = response.read().decode("utf-8")
    return json.loads(response_body) if response_body else {}


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
