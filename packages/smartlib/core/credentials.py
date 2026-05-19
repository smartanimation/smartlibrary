from __future__ import annotations

import os
from pathlib import Path


CREDENTIAL_ENV_VARS = ("CREDENTIALS_PATH", "GOOGLE_APPLICATION_CREDENTIALS", "CREDENTIALS_DIR")


def credentials_path() -> Path | None:
    for name in CREDENTIAL_ENV_VARS:
        value = os.environ.get(name)
        if not value:
            continue
        path = Path(value.strip().strip('"'))
        if path.is_dir():
            path = path / "credentials.json"
        return path

    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "credentials.json"
    return None
