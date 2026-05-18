from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json, write_json
from smartlib.core.path_resolver import ProjectPaths
from smartlib.core.validation import ValidationIssue


DEFAULT_SHOT_DEPARTMENTS = ["layout", "anim", "fx", "lighting", "comp"]
DEFAULT_REVIEW_LAYERS = {
    "CHA": {"members": [], "order": 20},
    "CHB": {"members": [], "order": 10},
    "BG": {"members": [], "order": 0},
    "FX": {"members": [], "order": 30},
}
VALID_ASSET_PUBLISH = {"approved", "latest"}


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

    def shot_root(self, identity: ShotIdentity) -> Path:
        return self.paths.shot_root(identity.episode, identity.sequence, identity.shot)

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

    def cast_from_rows(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        cast: dict[str, Any] = {}
        review_layers: dict[str, dict[str, Any]] = {}
        for row in rows:
            cast_key = str(row.get("cast_key") or row.get("Cast Key") or "").strip()
            if not cast_key:
                continue
            role = str(row.get("role") or row.get("Role") or "CHA").strip()
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
        return {"cast": cast, "review_layers": review_layers or DEFAULT_REVIEW_LAYERS}


def validate_cast_data(cast_data: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    cast = cast_data.get("cast") or {}
    review_layers = cast_data.get("review_layers") or {}
    namespaces: dict[str, str] = {}

    for cast_key, entry in cast.items():
        namespace = str(entry.get("namespace") or "")
        role = str(entry.get("role") or "")
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


def _is_version_label(value: str) -> bool:
    return len(value) >= 4 and value.lower().startswith("v") and value[1:].isdigit()


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}
