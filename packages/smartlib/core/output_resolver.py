from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from smartlib.core.config_loader import ProjectConfig
from smartlib.core.tokens import resolve_token_string, unresolved_tokens


WINDOWS_FORBIDDEN = re.compile(r'[<>:"/\\|?*]')


@dataclass(frozen=True)
class ResolvedOutput:
    key: str
    directory: Path
    filename: str

    @property
    def path(self) -> Path:
        return self.directory / self.filename


class OutputPathResolver:
    """Resolve client-configurable output directories and file names."""

    def __init__(self, project_config: ProjectConfig):
        self.project_config = project_config

    @property
    def definitions(self) -> dict[str, dict[str, Any]]:
        data = self.project_config.load("naming.yml") or {}
        return dict(data.get("outputs") or {})

    def resolve(
        self,
        key: str,
        tokens: Mapping[str, Any],
        *,
        default_directory: str = "",
        default_filename: str = "",
    ) -> ResolvedOutput:
        definition = dict(self.definitions.get(key) or {})
        directory_template = str(definition.get("directory") or default_directory)
        filename_template = str(definition.get("filename") or default_filename)
        values = self._values(tokens)
        directory = self._expand(directory_template, values)
        filename = self._expand(filename_template, values)
        missing = unresolved_tokens({"directory": directory, "filename": filename})
        if missing:
            raise KeyError(f"Unresolved output tokens for {key}: {', '.join(sorted(missing))}")
        if not filename:
            raise ValueError(f"Output filename is empty: {key}")
        if WINDOWS_FORBIDDEN.search(filename):
            raise ValueError(f"Output filename contains a forbidden character: {filename}")
        return ResolvedOutput(key, Path(directory), filename)

    def _values(self, tokens: Mapping[str, Any]) -> dict[str, Any]:
        values = {
            "project_root": self.project_config.project_root.as_posix(),
            "project_name": self.project_config.project_name,
        }
        values.update(self.project_config.templates)
        values.update(dict(tokens))
        values.setdefault("project", values["project_name"])
        if values.get("sequence"):
            values.setdefault("seq", values["sequence"])
        if values.get("department"):
            values.setdefault("dept", values["department"])
        if values.get("dcc"):
            values.setdefault("tool", values["dcc"])
        if not values.get("workspace_partition"):
            base = self.project_config.load("templates_base.yml") or {}
            configured = base.get("shot_dept_partitions") or {}
            department = str(values.get("department") or values.get("dept") or "").strip()
            values["workspace_partition"] = str(
                configured.get(department) or configured.get("default") or "cg"
            ).strip()
        return values

    @staticmethod
    def _expand(template: str, values: Mapping[str, Any]) -> str:
        result = str(template)
        for _ in range(12):
            expanded = resolve_token_string(result, values, missing="keep")
            if expanded == result:
                break
            result = expanded
        return result
