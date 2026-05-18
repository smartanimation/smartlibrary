from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def read_json(path: str | os.PathLike[str], default: Any = None) -> Any:
    json_path = Path(path)
    if not json_path.exists():
        return default
    with json_path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path: str | os.PathLike[str], data: Any) -> Path:
    json_path = Path(path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with json_path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    return json_path


def sidecar_path(path: str | os.PathLike[str], suffix: str = ".json") -> Path:
    source = Path(path)
    return source.with_suffix(source.suffix + suffix)
