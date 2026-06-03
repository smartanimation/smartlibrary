from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from smartlib.core.metadata import read_json
from smartlib.core.versioning import format_version, parse_version


SUPPORTED_SCHEMES = {"asset", "shot", "sequence"}
VERSION_ALIASES = {"latest"}


@dataclass(frozen=True)
class ResolveResult:
    virtual_path: str
    resolved_path: Path
    scheme: str
    version: str = ""
    exists: bool = False


class SmartPathResolver:
    """Resolve readable SmartPipeline virtual paths to project filesystem paths.

    This is a Python-side resolver. USD files should continue to use native
    relative paths until a USD ArResolver plugin exists.
    """

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root)

    def resolve(self, virtual_path: str, *, context: str | None = None) -> ResolveResult:
        parsed = urlparse(str(virtual_path))
        scheme = parsed.scheme
        if scheme not in SUPPORTED_SCHEMES:
            raise ValueError(f"Unsupported virtual path scheme: {scheme}")
        relative = parsed.netloc + parsed.path
        relative = relative.strip("/")
        parts = [part for part in relative.split("/") if part]
        if not parts:
            raise ValueError(f"Invalid virtual path: {virtual_path}")

        root, remaining = self._scheme_root(scheme, parts)
        resolved_parts, version = self._resolve_version_alias(root, remaining)
        path = root.joinpath(*resolved_parts)
        return ResolveResult(
            virtual_path=virtual_path,
            resolved_path=path,
            scheme=scheme,
            version=version,
            exists=path.exists(),
        )

    def _scheme_root(self, scheme: str, parts: list[str]) -> tuple[Path, list[str]]:
        if scheme == "asset":
            return self.project_root / "assets", parts
        if scheme == "shot":
            return self.project_root / "shots", parts
        if scheme == "sequence":
            return self.project_root / "sequences", parts
        raise ValueError(f"Unsupported virtual path scheme: {scheme}")

    def _resolve_version_alias(self, root: Path, parts: list[str]) -> tuple[list[str], str]:
        for index, part in enumerate(parts):
            if part not in VERSION_ALIASES:
                continue
            base_dir = root.joinpath(*parts[:index])
            version = self._latest_version(base_dir)
            if not version:
                return parts, ""
            resolved = list(parts)
            resolved[index] = version
            return resolved, version
        version = next((part for part in parts if parse_version(part)), "")
        return parts, version

    @staticmethod
    def _latest_version(base_dir: Path) -> str:
        latest = read_json(base_dir / "latest.json", {}) or {}
        version = str(latest.get("version") or "").strip()
        if version:
            return version
        versions = [
            parsed
            for parsed in (parse_version(path.name) for path in base_dir.glob("v*") if path.is_dir())
            if parsed is not None
        ]
        return format_version(max(versions)) if versions else ""


def resolve(virtual_path: str, project_root: str | Path, *, context: str | None = None) -> ResolveResult:
    return SmartPathResolver(project_root).resolve(virtual_path, context=context)
