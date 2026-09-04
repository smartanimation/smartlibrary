"""File-based transport for immutable review-worker command arguments."""
from __future__ import annotations

import json
from pathlib import Path

from smartlib.core.metadata import sidecar_path


SCHEMA = "smartpipeline.review_worker_request.v1"


def write_job_request(status_path: str | Path, arguments: list[str]) -> Path:
    # The status path already belongs to the resolved job directory. Use the
    # shared sidecar convention, not another pipeline path template.
    path = sidecar_path(status_path, ".request.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump({"schema": SCHEMA, "arguments": arguments}, stream,
                  ensure_ascii=False, indent=2)
        stream.write("\n")
    return path


def expand_job_arguments(arguments: list[str]) -> list[str]:
    """Keep legacy CLI compatible; a request file is an exclusive input mode."""
    if "--job-file" not in arguments:
        return arguments
    if len(arguments) != 2 or arguments[0] != "--job-file":
        raise ValueError("Use --job-file PATH without other worker arguments.")
    path = Path(arguments[1])
    with path.open(encoding="utf-8-sig") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise ValueError(f"Unsupported review job request schema: {path}")
    values = payload.get("arguments")
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError(f"Review job arguments must be a list of strings: {path}")
    if "--job-file" in values:
        raise ValueError("Nested review job request files are not supported.")
    return values
