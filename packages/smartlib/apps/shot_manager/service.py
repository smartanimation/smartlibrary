from __future__ import annotations

import csv
import os
import re
from dataclasses import asdict, dataclass, field
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.credentials import credentials_path
from smartlib.core.metadata import read_json, sidecar_path, write_json
from smartlib.core.path_resolver import ProjectPaths
from smartlib.core.selection_context import read_selected_asset
from smartlib.core.validation import ValidationIssue


DEFAULT_SHOT_DEPARTMENTS = ["layout", "anim", "fx", "lighting", "comp"]
DEFAULT_REVIEW_LAYERS = {
    "CHA": {"members": [], "order": 20},
    "CHB": {"members": [], "order": 10},
    "BGA": {"members": [], "order": 0},
    "FX": {"members": [], "order": 30},
    "ENV": {"members": [], "order": -10},
}
ROLE_ALIASES = {
    "BG": "BGA",
    "BACKGROUND": "BGA",
    "BACK": "BGA",
    "SET": "BGA",
    "ENVIRONMENT": "BGA",
}
VALID_ASSET_PUBLISH = {"approved", "latest"}
CAST_CSV_COLUMNS = [
    "episode",
    "sequence",
    "shot",
    "cast_key",
    "asset",
    "variant",
    "role",
    "namespace",
    "asset_publish",
    "required",
    "note",
]


@dataclass(frozen=True)
class ShotIdentity:
    episode: str
    sequence: str
    shot: str

    @property
    def code(self) -> str:
        return f"{self.episode}_{self.sequence}_{self.shot}"


@dataclass(frozen=True)
class CastEntry:
    asset: str
    variant: str = "default"
    role: str = "CHA"
    namespace: str = ""
    asset_publish: str = "approved"
    note: str = ""
    required: bool = True


@dataclass(frozen=True)
class ReviewLayer:
    members: list[str] = field(default_factory=list)
    order: int = 0
    camera: dict[str, Any] = field(default_factory=dict)
    resolution: dict[str, Any] = field(default_factory=dict)
    ae: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BuildPreviewItem:
    cast_key: str
    asset: str
    variant: str
    namespace: str
    role: str
    review_layer: str
    asset_publish: str
    required: bool
    status: str
    asset_root: str = ""
    variant_root: str = ""
    publish_path: str = ""
    message: str = ""


@dataclass(frozen=True)
class ShotWorkFile:
    department: str
    file: str
    path: str
    updated: str
    version: int = 0
    take: int = 0
    comment: str = ""


@dataclass(frozen=True)
class ShotCreateRequest:
    episode: str
    sequence: str
    shot: str
    fps: int = 24
    cut_in: int = 1001
    cut_out: int = 1001
    status: str = "wip"

    @property
    def identity(self) -> ShotIdentity:
        return ShotIdentity(self.episode, self.sequence, self.shot)


class ShotManagerService:
    """Core/service layer for shot folders, shot.json, and cast.json."""

    def __init__(self, project_config: ProjectConfig):
        self.project_config = project_config
        project_root = project_config.project_root
        if project_root is None:
            raise RuntimeError("project_root is not set in templates_base.yml")
        self.paths = ProjectPaths(project_root)

    @property
    def shot_departments(self) -> list[str]:
        departments = self.project_config.base.get("shot_depts") or []
        return list(departments) if departments else list(DEFAULT_SHOT_DEPARTMENTS)

    @property
    def project_fps(self) -> int:
        fps = (self.project_config.base.get("anchors") or {}).get("fps", 24)
        try:
            return int(fps)
        except (TypeError, ValueError):
            return 24

    def shot_root(self, identity: ShotIdentity) -> Path:
        return self.paths.shot_root(identity.episode, identity.sequence, identity.shot)

    def list_shots(self) -> list[ShotIdentity]:
        shots_root = self.paths.shots_root()
        if not shots_root.exists():
            return []
        shots: list[ShotIdentity] = []
        for shot_json in shots_root.glob("*/*/*/shot.json"):
            shot_root = shot_json.parent
            try:
                sequence_root = shot_root.parent
                episode_root = sequence_root.parent
                shots.append(
                    ShotIdentity(
                        episode=episode_root.name,
                        sequence=sequence_root.name,
                        shot=shot_root.name,
                    )
                )
            except Exception:
                continue
        return sorted(shots, key=lambda item: (item.episode.lower(), item.sequence.lower(), item.shot.lower()))

    def load_shot(self, identity: ShotIdentity) -> dict[str, Any]:
        return read_json(self.shot_root(identity) / "shot.json", {})

    def shot_work_dir(self, identity: ShotIdentity, department: str) -> Path:
        return self.paths.shot_work_dir(identity.episode, identity.sequence, identity.shot, department)

    def shot_work_file_path(
        self,
        identity: ShotIdentity,
        department: str,
        version: int,
        take: int,
        ext: str = "ma",
    ) -> Path:
        version_label = f"v{version:03d}"
        take_label = f"{take:02d}"
        filename = f"{identity.shot}_{department}_{version_label}_{take_label}.{ext.lstrip('.')}"
        return self.shot_work_dir(identity, department) / filename

    def next_shot_work_path(
        self,
        identity: ShotIdentity,
        department: str,
        current_path: str | Path | None = None,
        next_version: bool = False,
        ext: str = "ma",
    ) -> Path:
        parsed = parse_shot_work_file(Path(current_path).name) if current_path else None
        if parsed and parsed.get("shot") != identity.shot:
            parsed = None
        if parsed:
            department = parsed["department"]
            ext = parsed["ext"]
            version = parsed["version"] + 1 if next_version else parsed["version"]
            if next_version:
                take = 1
            else:
                take = self.next_shot_work_take(identity, department, version, ext)
        else:
            version = 1
            take = self.next_shot_work_take(identity, department, version, ext)
        return self.shot_work_file_path(identity, department, version, take, ext)

    def next_shot_work_take(self, identity: ShotIdentity, department: str, version: int, ext: str = "ma") -> int:
        max_take = 0
        work_dir = self.shot_work_dir(identity, department)
        if work_dir.exists():
            for path in work_dir.iterdir():
                parsed = parse_shot_work_file(path.name)
                if not parsed:
                    continue
                if parsed["shot"] == identity.shot and parsed["department"] == department and parsed["version"] == version and parsed["ext"] == ext:
                    max_take = max(max_take, parsed["take"])
        return max_take + 1

    def list_shot_work_files(self, identity: ShotIdentity, department: str | None = None) -> list[ShotWorkFile]:
        departments = [department] if department else self.shot_departments
        files: list[ShotWorkFile] = []
        for dept in departments:
            work_dir = self.shot_work_dir(identity, dept)
            if not work_dir.exists():
                continue
            for path in work_dir.iterdir():
                if not path.is_file() or path.suffix.lower() not in {".ma", ".mb"}:
                    continue
                parsed = parse_shot_work_file(path.name) or {}
                metadata = read_json(sidecar_path(path), {}) or {}
                comment = str(metadata.get("comment") or "")
                updated = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                files.append(
                    ShotWorkFile(
                        department=dept,
                        file=path.name,
                        path=str(path),
                        updated=updated,
                        version=int(parsed.get("version") or 0),
                        take=int(parsed.get("take") or 0),
                        comment=comment,
                    )
                )
        return sorted(files, key=lambda item: (item.department, item.version, item.take, item.file.lower()), reverse=True)

    def write_shot_work_metadata(
        self,
        path: str | Path,
        identity: ShotIdentity,
        department: str,
        scene_info: dict[str, Any] | None = None,
        comment: str = "",
    ) -> Path:
        work_path = Path(path)
        parsed = parse_shot_work_file(work_path.name) or {}
        data = {
            "episode": identity.episode,
            "sequence": identity.sequence,
            "shot": identity.shot,
            "department": department,
            "version": parsed.get("version"),
            "take": parsed.get("take"),
            "comment": comment,
            "source": work_path.name,
            "scene_info": scene_info or {},
        }
        return write_json(sidecar_path(work_path), data)

    def validate_cast(self, identity: ShotIdentity) -> list[ValidationIssue]:
        return validate_cast_data(self.load_cast(identity))

    def build_preview(self, identity: ShotIdentity) -> list[BuildPreviewItem]:
        cast_data = self.load_cast(identity)
        cast = cast_data.get("cast") or {}
        review_layers = cast_data.get("review_layers") or {}
        member_to_layer = {}
        for layer_name, layer in review_layers.items():
            for member in layer.get("members", []):
                member_to_layer[member] = layer_name

        items: list[BuildPreviewItem] = []
        for cast_key, entry in sorted(cast.items()):
            asset_name = str(entry.get("asset") or "")
            variant = str(entry.get("variant") or "default")
            asset_publish = str(entry.get("asset_publish") or "approved")
            required = bool(entry.get("required", True))
            asset_root = self.find_asset_root(asset_name)
            if not asset_root:
                items.append(
                    BuildPreviewItem(
                        cast_key=cast_key,
                        asset=asset_name,
                        variant=variant,
                        namespace=str(entry.get("namespace") or cast_key),
                        role=str(entry.get("role") or ""),
                        review_layer=member_to_layer.get(cast_key, ""),
                        asset_publish=asset_publish,
                        required=required,
                        status="missing" if required else "optional missing",
                        message="Asset folder was not found.",
                    )
                )
                continue

            variant_root = asset_root / variant
            if not variant_root.exists():
                items.append(
                    BuildPreviewItem(
                        cast_key=cast_key,
                        asset=asset_name,
                        variant=variant,
                        namespace=str(entry.get("namespace") or cast_key),
                        role=str(entry.get("role") or ""),
                        review_layer=member_to_layer.get(cast_key, ""),
                        asset_publish=asset_publish,
                        required=required,
                        status="missing" if required else "optional missing",
                        asset_root=str(asset_root),
                        message="Asset variant folder was not found.",
                    )
                )
                continue

            publish_path = self.resolve_asset_publish(variant_root, asset_publish)
            status = "resolved" if publish_path else ("missing" if required else "optional missing")
            items.append(
                BuildPreviewItem(
                    cast_key=cast_key,
                    asset=asset_name,
                    variant=variant,
                    namespace=str(entry.get("namespace") or cast_key),
                    role=str(entry.get("role") or ""),
                    review_layer=member_to_layer.get(cast_key, ""),
                    asset_publish=asset_publish,
                    required=required,
                    status=status,
                    asset_root=str(asset_root),
                    variant_root=str(variant_root),
                    publish_path=str(publish_path) if publish_path else "",
                    message="" if publish_path else "Publish was not found.",
                )
            )
        return items

    def find_asset_root(self, asset_name: str) -> Path | None:
        assets_root = self.paths.assets_root()
        if not asset_name or not assets_root.exists():
            return None
        matches = sorted(assets_root.glob(f"*/*/{asset_name}"))
        return matches[0] if matches else None

    def resolve_asset_publish(self, variant_root: Path, asset_publish: str) -> Path | None:
        publish_root = variant_root / "publish"
        if not publish_root.exists():
            return None
        if _is_version_label(asset_publish):
            candidates = sorted(publish_root.glob(f"*/*/{asset_publish}/*"))
            files = [path for path in candidates if path.is_file() and path.name not in {"publish.json", "validation.json"}]
            return _preferred_publish(files)
        if asset_publish == "approved":
            approved = self._approved_publish_paths(publish_root)
            if approved:
                return _preferred_publish(approved)
        latest = self._latest_publish_paths(publish_root)
        return _preferred_publish(latest)

    def _latest_publish_paths(self, publish_root: Path) -> list[Path]:
        paths = []
        for latest_json in publish_root.glob("*/*/latest.json"):
            latest = read_json(latest_json, {})
            if latest.get("path"):
                path = latest_json.parent / latest["path"]
                if path.exists():
                    paths.append(path)
        return paths

    def _approved_publish_paths(self, publish_root: Path) -> list[Path]:
        paths = []
        for versions_json in publish_root.glob("*/*/versions.json"):
            versions = read_json(versions_json, [])
            approved_versions = [
                item.get("version")
                for item in versions
                if item.get("status") in {"approved", "latest"} and item.get("version")
            ]
            for version in approved_versions:
                version_dir = versions_json.parent / version
                if version_dir.exists():
                    paths.extend(
                        path for path in version_dir.iterdir()
                        if path.is_file() and path.name not in {"publish.json", "validation.json"}
                    )
        return paths

    def planned_shot_paths(self, request: ShotCreateRequest) -> list[Path]:
        identity = request.identity
        shot_root = self.shot_root(identity)
        paths = [
            self.paths.sequence_root(identity.episode, identity.sequence),
            shot_root,
            shot_root / "data",
            shot_root / "publish",
        ]
        paths.extend(shot_root / department / "work" for department in self.shot_departments)
        return paths

    def create_shot(self, request: ShotCreateRequest) -> Path:
        request = ShotCreateRequest(
            episode=request.episode,
            sequence=request.sequence,
            shot=request.shot,
            fps=self.project_fps,
            cut_in=request.cut_in,
            cut_out=request.cut_out,
            status=request.status,
        )
        for path in self.planned_shot_paths(request):
            path.mkdir(parents=True, exist_ok=True)
        sequence_path = self.paths.sequence_root(request.episode, request.sequence) / "sequence.json"
        if not sequence_path.exists():
            write_json(sequence_path, {"episode": request.episode, "sequence": request.sequence})
        shot_root = self.shot_root(request.identity)
        self.write_shot_json(request)
        self.ensure_cast_json(request.identity)
        return shot_root

    def write_shot_json(self, request: ShotCreateRequest) -> Path:
        duration = max(0, request.cut_out - request.cut_in + 1)
        data = {
            "episode": request.episode,
            "sequence": request.sequence,
            "shot": request.shot,
            "status": request.status,
            "editorial": {
                "fps": request.fps,
                "cut_in": request.cut_in,
                "cut_out": request.cut_out,
                "duration": duration,
                "handles": {"head": 0, "tail": 0},
            },
        }
        return write_json(self.shot_root(request.identity) / "shot.json", data)

    def ensure_cast_json(self, identity: ShotIdentity) -> Path:
        path = self.shot_root(identity) / "cast.json"
        if path.exists():
            return path
        return write_json(path, {"cast": {}, "review_layers": DEFAULT_REVIEW_LAYERS})

    def load_cast(self, identity: ShotIdentity) -> dict[str, Any]:
        return read_json(self.shot_root(identity) / "cast.json", {"cast": {}, "review_layers": {}})

    def write_cast(self, identity: ShotIdentity, cast_data: dict[str, Any]) -> Path:
        issues = validate_cast_data(cast_data)
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            messages = ", ".join(issue.message for issue in errors)
            raise ValueError(f"Invalid cast data: {messages}")
        return write_json(self.shot_root(identity) / "cast.json", cast_data)

    def review_layers(self, identity: ShotIdentity) -> dict[str, dict[str, Any]]:
        return _defaulted_review_layers(self.load_cast(identity).get("review_layers"))

    def write_review_layers(self, identity: ShotIdentity, review_layers: dict[str, Any]) -> Path:
        cast_data = self.load_cast(identity)
        cast_data["review_layers"] = _defaulted_review_layers(review_layers)
        return self.write_cast(identity, cast_data)

    def review_layer_rows(self, identity: ShotIdentity) -> list[dict[str, Any]]:
        rows = []
        for layer_name, layer in self.review_layers(identity).items():
            camera = layer.get("camera") or {}
            resolution = layer.get("resolution") or {}
            ae = layer.get("ae") or {}
            rows.append(
                {
                    "layer": layer_name,
                    "members": ", ".join(layer.get("members") or []),
                    "camera": camera.get("name", ""),
                    "camera_publish": camera.get("version", ""),
                    "publish_type": camera.get("publish_type", "camera"),
                    "width": resolution.get("width", ""),
                    "height": resolution.get("height", ""),
                    "scale": resolution.get("scale", ""),
                    "ae_slot": ae.get("template_slot", ""),
                    "comp_name": ae.get("comp_name", layer_name),
                    "order": layer.get("order", 0),
                    "three_d_layer": bool(layer.get("three_d_layer", False)),
                    "frame_range": layer.get("frame_range", "Animation"),
                    "take": layer.get("take", 1),
                }
            )
        return sorted(rows, key=lambda item: (int(item.get("order") or 0), str(item.get("layer"))))

    def cast_from_rows(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return self.build_cast_data(rows)

    def import_cast_csv(self, identity: ShotIdentity, path: str | Path) -> Path:
        rows = self.read_cast_csv(path, identity=identity)
        cast_data = self.build_cast_data(rows, existing=self.load_cast(identity))
        return self.write_cast(identity, cast_data)

    def import_cast_spreadsheet(self, identity: ShotIdentity, sheet_id: str | None = None) -> Path:
        self.sync_cast_spreadsheet_cache(sheet_id=sheet_id)
        return self.import_cast_cache(identity)

    def import_cast_cache(self, identity: ShotIdentity) -> Path:
        rows = read_json(self.cast_cache_path, [])
        if not isinstance(rows, list):
            raise RuntimeError(f"Cast cache is not a list: {self.cast_cache_path}")
        rows = [row for row in rows if isinstance(row, dict) and _row_matches_identity(row, identity)]
        cast_data = self.build_cast_data(rows, existing=self.load_cast(identity))
        return self.write_cast(identity, cast_data)

    def sync_cast_spreadsheet_cache(self, sheet_id: str | None = None) -> Path:
        rows = self.read_cast_spreadsheet(sheet_id=sheet_id)
        return write_json(self.cast_cache_path, rows)

    def export_cast_csv(self, identity: ShotIdentity, path: str | Path) -> Path:
        cast_data = self.load_cast(identity)
        rows = self.cast_data_to_rows(identity, cast_data)
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=CAST_CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return output

    def read_cast_csv(self, path: str | Path, identity: ShotIdentity | None = None) -> list[dict[str, Any]]:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        if identity is None:
            return rows
        return [
            row for row in rows
            if _row_matches_identity(row, identity)
        ]

    def read_cast_spreadsheet(self, sheet_id: str | None = None) -> list[dict[str, Any]]:
        sheet_id = sheet_id or self.cast_sheet_id
        if not sheet_id:
            raise RuntimeError("google_sheets.cast_list_id is not set in templates_base.yml")
        path = self.credentials_path()
        if not path or not path.exists():
            raise RuntimeError("Credentials file was not found. Set CREDENTIALS_PATH, CREDENTIALS_DIR, or %APPDATA%/credentials.json.")
        try:
            import gspread
        except ImportError as exc:
            raise RuntimeError(f"gspread is not installed for this Python: {exc}") from exc
        gc = gspread.service_account(filename=str(path))
        return gc.open_by_key(sheet_id).sheet1.get_all_records()

    @property
    def cast_cache_path(self) -> Path:
        return self.project_config.config_dir / ".cache" / "cast_list.json"

    @property
    def cast_sheet_id(self) -> str:
        google_sheets = self.project_config.base.get("google_sheets") or {}
        if isinstance(google_sheets, dict):
            return str(google_sheets.get("cast_list_id", "")).strip()
        return ""

    @staticmethod
    def credentials_path() -> Path | None:
        return credentials_path()

    def cast_data_to_rows(self, identity: ShotIdentity, cast_data: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        cast = cast_data.get("cast") or {}
        for cast_key, entry in sorted(cast.items()):
            rows.append(
                {
                    "episode": identity.episode,
                    "sequence": identity.sequence,
                    "shot": identity.shot,
                    "cast_key": cast_key,
                    "asset": entry.get("asset", ""),
                    "variant": entry.get("variant", "default"),
                    "role": entry.get("role", ""),
                    "namespace": entry.get("namespace", ""),
                    "asset_publish": entry.get("asset_publish", "approved"),
                    "required": "TRUE" if entry.get("required", True) else "FALSE",
                    "note": entry.get("note", ""),
                }
            )
        return rows

    def build_cast_data(
        self,
        rows: list[dict[str, Any]],
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cast: dict[str, Any] = {}
        review_layers: dict[str, dict[str, Any]] = _defaulted_review_layers((existing or {}).get("review_layers"))
        for layer in review_layers.values():
            layer["members"] = []
        for row in rows:
            cast_key = str(row.get("cast_key") or row.get("Cast Key") or "").strip()
            if not cast_key:
                continue
            role = _normalize_role(row.get("role") or row.get("Role") or "CHA")
            entry = CastEntry(
                asset=str(row.get("asset") or row.get("Asset") or "").strip(),
                variant=str(row.get("variant") or row.get("Variant") or "default").strip() or "default",
                role=role,
                namespace=str(row.get("namespace") or row.get("Namespace") or cast_key).strip(),
                asset_publish=str(row.get("asset_publish") or row.get("Asset Publish") or "approved").strip(),
                note=str(row.get("note") or row.get("Note") or "").strip(),
                required=_parse_bool(row.get("required", row.get("Required", True))),
            )
            cast[cast_key] = asdict(entry)
            layer = review_layers.setdefault(role, {"members": [], "order": len(review_layers) * 10})
            layer["members"].append(cast_key)
        return {"cast": cast, "review_layers": review_layers or deepcopy(DEFAULT_REVIEW_LAYERS)}

    def selected_asset_for_cast(self, existing_cast: dict[str, Any] | None = None) -> dict[str, Any]:
        selected = read_selected_asset(self.project_config)
        if not selected.get("asset"):
            return {}
        role = _role_from_asset_selection(selected)
        cast_key = _unique_cast_key(existing_cast or {}, selected.get("asset"))
        return {
            "cast_key": cast_key,
            "asset": selected.get("asset", ""),
            "variant": selected.get("variant", "default") or "default",
            "role": role,
            "namespace": cast_key,
            "asset_publish": "approved",
            "required": True,
            "note": f"from Asset Manager: {selected.get('category', '')}/{selected.get('group', '')}",
        }


def validate_cast_data(cast_data: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    cast = cast_data.get("cast") or {}
    review_layers = _defaulted_review_layers(cast_data.get("review_layers"))
    namespaces: dict[str, str] = {}

    for cast_key, entry in cast.items():
        namespace = str(entry.get("namespace") or "")
        role = _normalize_role(entry.get("role") or "")
        asset_publish = str(entry.get("asset_publish") or "")
        if namespace in namespaces:
            issues.append(ValidationIssue("namespace_duplicate", f"namespace is duplicated: {namespace}", "error"))
        namespaces[namespace] = cast_key
        if role and role not in review_layers:
            issues.append(ValidationIssue("missing_review_layer", f"role has no review layer: {role}", "error"))
        if asset_publish not in VALID_ASSET_PUBLISH and not _is_version_label(asset_publish):
            issues.append(ValidationIssue("invalid_asset_publish", f"asset_publish is invalid: {asset_publish}", "error"))

    for layer_name, layer in review_layers.items():
        for member in layer.get("members", []):
            if member not in cast:
                issues.append(ValidationIssue("missing_cast_member", f"{layer_name} member is missing from cast: {member}", "error"))
        resolution = layer.get("resolution") or {}
        for key in ("width", "height"):
            if key in resolution and int(resolution.get(key) or 0) <= 0:
                issues.append(ValidationIssue("invalid_resolution", f"{layer_name}.{key} must be positive", "error"))

    return issues


SHOT_WORK_RE = re.compile(
    r"^(?P<shot>.+?)_(?P<department>[^_]+)_v(?P<version>\d+)_(?P<take>\d+)\.(?P<ext>[^.]+)$"
)


def parse_shot_work_file(filename: str) -> dict[str, Any] | None:
    match = SHOT_WORK_RE.match(filename)
    if not match:
        return None
    data = match.groupdict()
    data["version"] = int(data["version"])
    data["take"] = int(data["take"])
    return data


def _defaulted_review_layers(review_layers: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    merged = deepcopy(DEFAULT_REVIEW_LAYERS)
    for name, layer in (review_layers or {}).items():
        normalized_name = _normalize_role(name)
        incoming = dict(layer or {})
        if normalized_name in merged:
            existing_members = list(merged[normalized_name].get("members") or [])
            incoming_members = list(incoming.get("members") or [])
            incoming["members"] = existing_members + [member for member in incoming_members if member not in existing_members]
        merged[normalized_name] = incoming
        merged[normalized_name].setdefault("members", [])
        merged[normalized_name].setdefault("order", DEFAULT_REVIEW_LAYERS.get(normalized_name, {}).get("order", len(merged) * 10))
    return merged


def _normalize_role(value: Any) -> str:
    role = str(value or "").strip().upper() or "CHA"
    return ROLE_ALIASES.get(role, role)


def _role_from_asset_selection(selected: dict[str, Any]) -> str:
    text = " ".join(
        str(selected.get(key, ""))
        for key in ("category", "group", "asset_type", "type")
    ).lower()
    if any(token in text for token in ("env", "environment", "bg", "background", "set")):
        return "BGA"
    if "fx" in text:
        return "FX"
    return "CHA"


def _unique_cast_key(existing_cast: dict[str, Any], asset_name: Any) -> str:
    base = re.sub(r"[^0-9A-Za-z_]+", "_", str(asset_name or "asset")).strip("_") or "asset"
    candidate = f"{base}_main"
    if candidate not in existing_cast:
        return candidate
    index = 2
    while f"{base}_{index:02d}" in existing_cast:
        index += 1
    return f"{base}_{index:02d}"


def _is_version_label(value: str) -> bool:
    return len(value) >= 4 and value.lower().startswith("v") and value[1:].isdigit()


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def _row_matches_identity(row: dict[str, Any], identity: ShotIdentity) -> bool:
    episode = str(row.get("episode") or row.get("Episode") or "").strip()
    sequence = str(row.get("sequence") or row.get("Sequence") or "").strip()
    shot = str(row.get("shot") or row.get("Shot") or "").strip()
    if not episode and not sequence and not shot:
        return True
    return episode == identity.episode and sequence == identity.sequence and shot == identity.shot


def _preferred_publish(paths: list[Path]) -> Path | None:
    if not paths:
        return None
    priority = ["/asset/", "/rig/anim/", "/rig/layout/", "/model/hires/"]
    normalized = [(path.as_posix().lower(), path) for path in paths]
    for marker in priority:
        matches = [path for text, path in normalized if marker in text]
        if matches:
            return sorted(matches, key=lambda path: path.as_posix().lower())[-1]
    return sorted(paths, key=lambda path: path.as_posix().lower())[-1]
