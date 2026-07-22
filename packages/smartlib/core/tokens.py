from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


TOKEN_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class TokenContext:
    project_root: str = ""
    project_name: str = ""
    episode: str = ""
    sequence: str = ""
    shot: str = ""
    department: str = ""
    task: str = ""
    tool: str = ""
    asset: str = ""
    category: str = ""
    group: str = ""
    variant: str = ""
    subset: str = ""
    version: str = ""
    take: str = ""

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None = None, **overrides: Any) -> "TokenContext":
        source = dict(values or {})
        source.update(overrides)
        fields = cls.__dataclass_fields__.keys()
        return cls(**{key: _stringify_token_value(source.get(key, "")) for key in fields})

    @classmethod
    def from_environment(cls, **overrides: Any) -> "TokenContext":
        values = {
            "project_root": os.environ.get("PROJECT_ROOT") or os.environ.get("SMART_PROJECT_ROOT") or "",
            "project_name": os.environ.get("PROJECT_NAME") or os.environ.get("SMART_PROJECT") or "",
            "episode": os.environ.get("EPISODE") or os.environ.get("SMART_EPISODE") or "",
            "sequence": os.environ.get("SEQUENCE") or os.environ.get("SEQ") or os.environ.get("SMART_SEQUENCE") or "",
            "shot": os.environ.get("SHOT") or os.environ.get("SMART_SHOT") or "",
            "department": os.environ.get("DEPARTMENT") or os.environ.get("DEPT") or "",
            "task": os.environ.get("TASK") or "",
            "tool": os.environ.get("TOOL") or os.environ.get("DCC") or "",
            "asset": os.environ.get("ASSET") or os.environ.get("ASSET_NAME") or "",
            "category": os.environ.get("CATEGORY") or "",
            "group": os.environ.get("GROUP") or "",
            "variant": os.environ.get("VARIANT") or "",
            "subset": os.environ.get("SUBSET") or "",
            "version": os.environ.get("VERSION") or "",
            "take": os.environ.get("TAKE") or "",
        }
        values.update(overrides)
        return cls.from_mapping(values)

    def to_dict(self, extra: Mapping[str, Any] | None = None) -> dict[str, str]:
        values = {key: _stringify_token_value(value) for key, value in asdict(self).items()}
        for key, value in (extra or {}).items():
            values[str(key)] = _stringify_token_value(value)
        if values.get("sequence"):
            values.setdefault("seq", values["sequence"])
        if values.get("department"):
            values.setdefault("dept", values["department"])
        if values.get("asset"):
            values.setdefault("asset_name", values["asset"])
        return values


def resolve_tokens(
    value: Any,
    tokens: Mapping[str, Any] | TokenContext | None = None,
    *,
    missing: str = "keep",
) -> Any:
    token_values = _token_mapping(tokens)
    if isinstance(value, str):
        return resolve_token_string(value, token_values, missing=missing)
    if isinstance(value, Path):
        return Path(resolve_token_string(value.as_posix(), token_values, missing=missing))
    if isinstance(value, list):
        return [resolve_tokens(item, token_values, missing=missing) for item in value]
    if isinstance(value, tuple):
        return tuple(resolve_tokens(item, token_values, missing=missing) for item in value)
    if isinstance(value, dict):
        return {
            resolve_tokens(key, token_values, missing=missing): resolve_tokens(item, token_values, missing=missing)
            for key, item in value.items()
        }
    return value


def resolve_token_string(value: str, tokens: Mapping[str, Any] | TokenContext | None = None, *, missing: str = "keep") -> str:
    token_values = _token_mapping(tokens)

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in token_values and token_values[name] != "":
            return token_values[name]
        if missing == "empty":
            return ""
        if missing == "error":
            raise KeyError(f"Token is not defined: {name}")
        return match.group(0)

    return TOKEN_RE.sub(replace, value)


def unresolved_tokens(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(TOKEN_RE.findall(value))
    if isinstance(value, Path):
        return unresolved_tokens(value.as_posix())
    if isinstance(value, Mapping):
        tokens: set[str] = set()
        for key, item in value.items():
            tokens.update(unresolved_tokens(key))
            tokens.update(unresolved_tokens(item))
        return tokens
    if isinstance(value, (list, tuple, set)):
        tokens: set[str] = set()
        for item in value:
            tokens.update(unresolved_tokens(item))
        return tokens
    return set()


def _token_mapping(tokens: Mapping[str, Any] | TokenContext | None) -> dict[str, str]:
    if isinstance(tokens, TokenContext):
        return tokens.to_dict()
    return {str(key): _stringify_token_value(value) for key, value in dict(tokens or {}).items()}


def _stringify_token_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Path):
        return value.as_posix()
    return str(value)
