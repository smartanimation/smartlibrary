from __future__ import annotations

import subprocess
from pathlib import Path


def launch_process(
    executable: str | Path,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    cwd: str | Path | None = None,
) -> subprocess.Popen:
    command = [str(executable)]
    command.extend(args or [])
    return subprocess.Popen(command, env=env, cwd=str(cwd) if cwd else None)
