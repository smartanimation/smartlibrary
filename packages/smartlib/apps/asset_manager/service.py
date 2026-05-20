from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import write_json
from smartlib.core.path_resolver import AssetIdentity, ProjectPaths


DEFAULT_ASSET_DEPARTMENTS = ["model", "look", "rig", "groom"]


@dataclass(frozen=True)
class AssetCreateRequest:
    category: str
    group: str
    name: str
    variant: str = "default"
    description: str = ""

    @property
    def identity(self) -> AssetIdentity:
        return AssetIdentity(
            category=self.category,
            group=self.group,
            name=self.name,
            variant=self.variant,
        )


@dataclass(frozen=True)
class CreatedAsset:
    identity: AssetIdentity
    asset_root: Path
    variant_root: Path
    created_paths: list[Path]


class AssetManagerService:
    """Core/service layer for asset folder and variant creation.

    This is intentionally independent from Qt and DCC modules so it can be tested
    from standalone Python and later called by the existing Asset Manager UI.
    """

    def __init__(self, project_config: ProjectConfig):
        self.project_config = project_config
        project_root = project_config.project_root
        if project_root is None:
            raise RuntimeError("project_root is not set in templates_base.yml")
        self.paths = ProjectPaths(project_root)

    @property
    def asset_departments(self) -> list[str]:
        departments = self.project_config.base.get("asset_depts") or []
        return list(departments) if departments else list(DEFAULT_ASSET_DEPARTMENTS)

    def planned_asset_paths(self, request: AssetCreateRequest) -> list[Path]:
        identity = request.identity
        asset_root = self.paths.asset_root(identity)
        variant_root = self.paths.asset_variant_root(identity)
        paths = [
            asset_root,
            variant_root,
            variant_root / "data",
            variant_root / "publish",
        ]
        paths.extend(variant_root / department / "work" for department in self.asset_departments)
        return paths

    def create_asset(self, request: AssetCreateRequest) -> CreatedAsset:
        default_request = AssetCreateRequest(
            category=request.category,
            group=request.group,
            name=request.name,
            variant="default",
            description=request.description,
        )
        created_paths = self._mkdirs(self.planned_asset_paths(default_request))
        asset_root = self.paths.asset_root(request.identity)
        default_variant_root = self.paths.asset_variant_root(default_request.identity)
        self._ensure_asset_json(default_request, asset_root)
        self._ensure_variant_json(default_request, default_variant_root)
        variant_root = default_variant_root
        if request.variant and request.variant != "default":
            created_paths.extend(self._mkdirs(self.planned_asset_paths(request)[1:]))
            variant_root = self.paths.asset_variant_root(request.identity)
            self._ensure_variant_json(request, variant_root)
        return CreatedAsset(request.identity, asset_root, variant_root, created_paths)

    def create_variant(self, request: AssetCreateRequest) -> CreatedAsset:
        asset_root = self.paths.asset_root(request.identity)
        if not asset_root.exists():
            raise FileNotFoundError(f"Asset does not exist: {asset_root}")
        created_paths = self._mkdirs(self.planned_asset_paths(request)[1:])
        variant_root = self.paths.asset_variant_root(request.identity)
        self._ensure_variant_json(request, variant_root)
        return CreatedAsset(request.identity, asset_root, variant_root, created_paths)

    def _ensure_asset_json(self, request: AssetCreateRequest, asset_root: Path) -> Path:
        path = asset_root / "asset.json"
        if path.exists():
            return path
        data: dict[str, Any] = {
            "asset": request.name,
            "category": request.category,
            "group": request.group,
            "description": request.description,
            "default_variant": request.variant,
        }
        return write_json(path, data)

    def _ensure_variant_json(self, request: AssetCreateRequest, variant_root: Path) -> Path:
        path = variant_root / "variant.json"
        if path.exists():
            return path
        data = {
            "asset": request.name,
            "variant": request.variant,
            "description": request.description,
        }
        return write_json(path, data)

    @staticmethod
    def _mkdirs(paths: list[Path]) -> list[Path]:
        created = []
        for path in paths:
            existed = path.exists()
            path.mkdir(parents=True, exist_ok=True)
            if not existed:
                created.append(path)
        return created
