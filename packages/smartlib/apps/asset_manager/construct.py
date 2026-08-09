"""Version-resolved asset construction recipes.

This module deliberately has no Qt or DCC dependency.  The UI chooses inputs and
versions here, then a DCC adapter performs the actual import/apply operation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_VERSION_RE = re.compile(r"^v(\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class ConstructVersion:
    version: str
    path: Path
    updated: float
    comment: str = ""


@dataclass
class ConstructInput:
    logical_id: str
    data_type: str
    target: str
    representation: str
    versions: list[ConstructVersion]
    selected_version: str
    latest_version: str
    use: bool = True

    @property
    def selected(self) -> ConstructVersion | None:
        return next(
            (item for item in self.versions if item.version == self.selected_version),
            None,
        )

    @property
    def state(self) -> str:
        if not self.selected:
            return "MISSING"
        if self.selected_version != self.latest_version:
            return "UPDATE AVAILABLE"
        return "LATEST"


class AssetConstructService:
    """Discovers versioned data and persists the chosen construction recipe."""

    schema = "smartpipeline.asset_construct.v1"

    def __init__(self, variant_root: str | Path):
        self.variant_root = Path(variant_root)
        self.data_root = self.variant_root / "data"
        self.recipe_path = self.variant_root / "construct" / "construct_manifest.json"

    def discover(self) -> list[ConstructInput]:
        recipe = self.load_recipe()
        saved = {
            str(item.get("id")): item
            for item in recipe.get("inputs", [])
            if isinstance(item, dict) and item.get("id")
        }
        groups: dict[str, dict[str, Any]] = {}
        if self.data_root.exists():
            for path in sorted(self.data_root.rglob("*")):
                if not path.is_file() or path.suffix.lower() == ".json":
                    continue
                parsed = self._parse_versioned_path(path)
                if not parsed:
                    continue
                logical_id, version, data_type, target, representation = parsed
                group = groups.setdefault(
                    logical_id,
                    {
                        "data_type": data_type,
                        "target": target,
                        "representation": representation,
                        "versions": [],
                    },
                )
                group["versions"].append(
                    ConstructVersion(
                        version=version,
                        path=path,
                        updated=path.stat().st_mtime,
                        comment=self._comment_for(path),
                    )
                )

        result: list[ConstructInput] = []
        for logical_id, group in sorted(groups.items()):
            versions = sorted(group["versions"], key=lambda item: self._version_number(item.version))
            latest = versions[-1].version
            selection = saved.get(logical_id, {})
            selected = str(selection.get("version") or latest)
            if selected not in {item.version for item in versions}:
                selected = latest
            result.append(
                ConstructInput(
                    logical_id=logical_id,
                    data_type=group["data_type"],
                    target=group["target"],
                    representation=group["representation"],
                    versions=versions,
                    selected_version=selected,
                    latest_version=latest,
                    use=bool(selection.get("use", True)),
                )
            )
        return result

    def load_recipe(self) -> dict[str, Any]:
        if not self.recipe_path.exists():
            return {}
        try:
            return json.loads(self.recipe_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}

    def save_recipe(
        self,
        inputs: list[ConstructInput],
        *,
        asset: str = "",
        variant: str = "default",
        department: str = "",
    ) -> Path:
        payload = {
            "schema": self.schema,
            "asset": asset,
            "variant": variant,
            "department": department,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "inputs": [
                {
                    "id": item.logical_id,
                    "type": item.data_type,
                    "target": item.target,
                    "representation": item.representation,
                    "version": item.selected_version,
                    "latest": item.latest_version,
                    "use": item.use,
                    "path": str(item.selected.path) if item.selected else "",
                }
                for item in inputs
            ],
        }
        self.recipe_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.recipe_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.recipe_path)
        return self.recipe_path

    def _parse_versioned_path(
        self, path: Path
    ) -> tuple[str, str, str, str, str] | None:
        relative = path.relative_to(self.data_root)
        parts = list(relative.parts)
        version_index = next(
            (index for index, part in enumerate(parts) if _VERSION_RE.match(part)),
            None,
        )
        if version_index is None or version_index == 0:
            return None
        version = parts[version_index]
        prefix = parts[:version_index]
        suffix = parts[version_index + 1 :]
        logical_id = "/".join(prefix + ["{version}"] + suffix)
        if prefix[0].lower() == "rig" and len(prefix) > 1:
            data_type = "/".join(prefix[:2])
            qualifiers = prefix[2:]
        else:
            data_type = prefix[0]
            qualifiers = prefix[1:]
        representation = qualifiers[-1] if qualifiers else ""
        target_parts = qualifiers[:-1] if qualifiers else []
        target = "/".join(target_parts) or path.stem
        return logical_id, version, data_type, target, representation

    @staticmethod
    def _version_number(version: str) -> int:
        match = _VERSION_RE.match(version)
        return int(match.group(1)) if match else -1

    @staticmethod
    def _comment_for(path: Path) -> str:
        for name in ("data.json", "metadata.json", "publish.json"):
            metadata_path = path.parent / name
            if not metadata_path.exists():
                continue
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            if isinstance(payload, dict):
                return str(payload.get("comment") or payload.get("note") or "")
        return ""
