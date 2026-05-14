from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONTEXT_ENV_VAR = "DCC_CONTEXT_PATH"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def default_context_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "smartuserdata" / "current_context.json"
    return Path.home() / ".smartuserdata" / "current_context.json"


@dataclass(frozen=True)
class WorkContext:
    episode: str
    sequence: str
    shot: str
    task: str = ""
    artist: str = ""
    project_root: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def shot_code(self) -> str:
        return f"{self.episode}_{self.sequence}_{self.shot}"

    @property
    def shot_root(self) -> Path | None:
        if not self.project_root:
            return None
        return Path(self.project_root) / self.episode / self.sequence / self.shot


def get_context_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path:
        return Path(path).expanduser()

    env_path = os.environ.get(CONTEXT_ENV_VAR)
    if env_path:
        return Path(env_path).expanduser()

    return default_context_path()


def validate_token(name: str, value: str) -> None:
    if not value:
        raise ValueError(f"{name} is required")
    if not TOKEN_RE.match(value):
        raise ValueError(f"{name} must contain only letters, numbers, '_' or '-': {value}")


def to_forward_slash_path(path: str | os.PathLike[str]) -> str:
    return str(path).replace("\\", "/")


def create_context(
    episode: str,
    sequence: str,
    shot: str,
    *,
    task: str = "",
    artist: str = "",
    project_root: str | os.PathLike[str] = "",
    extra: dict[str, Any] | None = None,
    path: str | os.PathLike[str] | None = None,
) -> WorkContext:
    validate_token("episode", episode)
    validate_token("sequence", sequence)
    validate_token("shot", shot)

    context = WorkContext(
        episode=episode,
        sequence=sequence,
        shot=shot,
        task=task,
        artist=artist,
        project_root=to_forward_slash_path(project_root) if project_root else "",
        extra=extra or {},
    )
    save_context(context, path=path)
    return context


def save_context(context: WorkContext, *, path: str | os.PathLike[str] | None = None) -> Path:
    context_path = get_context_path(path)
    context_path.parent.mkdir(parents=True, exist_ok=True)

    data = asdict(context)
    if data.get("project_root"):
        data["project_root"] = to_forward_slash_path(data["project_root"])
    data["shot_code"] = context.shot_code
    if context.shot_root:
        data["shot_root"] = to_forward_slash_path(context.shot_root)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{context_path.name}.",
        suffix=".tmp",
        dir=str(context_path.parent),
        text=True,
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(temp_name, context_path)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)

    return context_path


def load_context(*, path: str | os.PathLike[str] | None = None) -> WorkContext:
    context_path = get_context_path(path)
    with context_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    return WorkContext(
        episode=data["episode"],
        sequence=data["sequence"],
        shot=data["shot"],
        task=data.get("task", ""),
        artist=data.get("artist", ""),
        project_root=data.get("project_root", ""),
        extra=data.get("extra", {}),
        updated_at=data.get("updated_at", ""),
    )


def export_to_environment(context: WorkContext) -> None:
    os.environ["EPISODE"] = context.episode
    os.environ["SEQUENCE"] = context.sequence
    os.environ["SHOT"] = context.shot
    os.environ["SHOT_CODE"] = context.shot_code
    os.environ["TASK"] = context.task

    if context.project_root:
        os.environ["PROJECT_ROOT"] = context.project_root
    if context.shot_root:
        os.environ["SHOT_ROOT"] = str(context.shot_root)


def print_context(context: WorkContext) -> None:
    print(f"Episode : {context.episode}")
    print(f"Sequence: {context.sequence}")
    print(f"Shot    : {context.shot}")
    print(f"Task    : {context.task}")
    print(f"Code    : {context.shot_code}")
    if context.shot_root:
        print(f"Root    : {context.shot_root}")


if __name__ == "__main__":
    ctx = create_context(
        "EP001",
        "SQ010",
        "SH010",
        task="anim",
        artist=os.environ.get("USERNAME") or os.environ.get("USER") or "",
        project_root="/my_project/shots",
    )
    print_context(ctx)
    print(f"Saved to: {get_context_path()}")
