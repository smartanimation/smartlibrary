from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path


LIBRARY_DIRS = ("packages", "scripts", "config", "resources")
LIBRARY_FILES = ("README.md", "pyproject.toml")
TOOLS_DIRS = ("python", "third_party")
IGNORED_NAMES = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
}
IGNORED_SUFFIXES = (".pyc", ".pyo")


def _ignore(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in IGNORED_NAMES or name.endswith(IGNORED_SUFFIXES)
    }


def _copy_required(source_root: Path, destination_root: Path, names: tuple[str, ...]) -> None:
    for name in names:
        source = source_root / name
        if not source.exists():
            raise FileNotFoundError(f"Required distribution input was not found: {source}")
        destination = destination_root / name
        if source.is_dir():
            shutil.copytree(source, destination, ignore=_ignore)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def _git_version(source_root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(source_root), "describe", "--tags", "--always", "--dirty"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unversioned"


def _write_launcher(library_root: Path) -> None:
    launcher = library_root / "SmartLauncher.bat"
    launcher.write_text(
        "@echo off\n"
        "setlocal\n"
        "set \"SMARTLIBRARY_ROOT=%~dp0\"\n"
        "for %%I in (\"%~dp0..\") do set \"SMARTPIPELINE_HOME=%%~fI\"\n"
        "set \"SMARTPIPELINE_ROOT=%~dp0\"\n"
        "set \"SMARTPIPELINE_STUDIO_CONFIG_DIR=%SMARTPIPELINE_HOME%\\smartprojects\"\n"
        "set \"SMARTPIPELINE_STUDIO_CONFIG=%SMARTPIPELINE_HOME%\\smartprojects\\studio.yml\"\n"
        "set \"SMARTPIPELINE_PROJECT_CONFIG_ROOT=%SMARTPIPELINE_HOME%\\smartprojects\\config\"\n"
        "set \"SMARTPIPELINE_TOOLS=%SMARTPIPELINE_HOME%\\smarttools\"\n"
        "set \"PYTHONDONTWRITEBYTECODE=1\"\n"
        "set \"PYTHONPATH=%~dp0packages;%~dp0;%PYTHONPATH%\"\n"
        "\"%SMARTPIPELINE_TOOLS%\\python\\pythonw.exe\" -m smartlib.apps.launcher.main\n"
        "if errorlevel 1 \"%SMARTPIPELINE_TOOLS%\\python\\python.exe\" -m smartlib.apps.launcher.main\n",
        encoding="utf-8",
        newline="\r\n",
    )


def _write_default_studio(path: Path, studio_id: str, studio_name: str, role: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "schema: smartpipeline.studio.v1\n"
        "studio:\n"
        f"  id: {studio_id}\n"
        f"  name: {studio_name}\n"
        f"  role: {role}\n"
        "launcher:\n"
        "  allowed_tools:\n"
        "    - smart_delivery\n"
        "anchors:\n"
        "  smartpipeline_root: '{smartpipeline_root}'\n"
        "  smartpipeline_tools: '{smartpipeline_tools}'\n"
        "runtime:\n"
        "  python: '{smartpipeline_tools}/python/python.exe'\n"
        "third_party:\n"
        "  python:\n"
        "    path: '{smartpipeline_tools}/third_party/python'\n",
        encoding="utf-8",
    )


def _overlay_studio_identity(path: Path, studio_id: str, studio_name: str, role: str) -> None:
    import yaml

    with path.open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    data.setdefault("schema", "smartpipeline.studio.v1")
    data["studio"] = {"id": studio_id, "name": studio_name, "role": role}
    if role == "vendor":
        launcher = dict(data.get("launcher") or {})
        launcher.setdefault("allowed_tools", ["smart_delivery"])
        data["launcher"] = launcher
    anchors = dict(data.get("anchors") or {})
    anchors["smartpipeline_root"] = "{smartpipeline_root}"
    anchors["smartpipeline_tools"] = "{smartpipeline_tools}"
    data["anchors"] = anchors
    runtime = dict(data.get("runtime") or {})
    runtime["python"] = "{smartpipeline_tools}/python/python.exe"
    data["runtime"] = runtime
    third_party = dict(data.get("third_party") or {})
    python_settings = dict(third_party.get("python") or {})
    python_settings["path"] = "{smartpipeline_tools}/third_party/python"
    third_party["python"] = python_settings
    data["third_party"] = third_party
    tools = dict(data.get("tools") or {})
    for unavailable in ("ffmpeg", "usdcat", "usdview"):
        tools.pop(unavailable, None)
    if tools:
        data["tools"] = tools
    else:
        data.pop("tools", None)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        yaml.safe_dump(data, stream, sort_keys=False, allow_unicode=True)


def _tree_summary(root: Path) -> dict[str, int]:
    files = [path for path in root.rglob("*") if path.is_file()]
    return {
        "files": len(files),
        "bytes": sum(path.stat().st_size for path in files),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _smarttools_dependency(source_library: Path) -> dict[str, str]:
    import yaml

    path = source_library / "config" / "distribution" / "smarttools-runtime.yml"
    with path.open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    return {
        "version": str(data["version"]),
        "platform": str(data["platform"]),
        "artifact": str(data["artifact"]),
    }


def build_distribution(args: argparse.Namespace) -> Path:
    source_library = args.source_library.resolve()
    source_projects = args.source_projects.resolve()
    source_tools = args.source_tools.resolve()
    output_root = args.output_root.resolve()
    sources = [source_library, source_projects]
    if args.tools_mode == "copy":
        sources.append(source_tools)
    for source in sources:
        if (
            output_root == source
            or output_root in source.parents
            or source in output_root.parents
        ):
            raise ValueError(f"Output root must not contain or replace a source tree: {output_root}")

    output_root.mkdir(parents=True, exist_ok=True)
    staging = output_root / f".vendor-build-{uuid.uuid4().hex}"
    library = staging / "smartlibrary"
    projects = staging / "smartprojects"
    tools = staging / "smarttools"
    try:
        library.mkdir(parents=True)
        _copy_required(source_library, library, LIBRARY_DIRS)
        _copy_required(source_library, library, LIBRARY_FILES)
        if args.tools_mode == "copy":
            tools.mkdir(parents=True)
            _copy_required(source_tools, tools, TOOLS_DIRS)
        _write_launcher(library)

        if args.projects == "all":
            shutil.copytree(source_projects, projects, ignore=_ignore)
        else:
            projects.mkdir(parents=True)
            studio_source = source_projects / "studio.yml"
            if args.projects == "studio" and studio_source.is_file():
                shutil.copy2(studio_source, projects / "studio.yml")
            else:
                _write_default_studio(
                    projects / "studio.yml", args.studio_id, args.studio_name, args.studio_role
                )
        studio_path = projects / "studio.yml"
        if not studio_path.is_file():
            _write_default_studio(
                studio_path, args.studio_id, args.studio_name, args.studio_role
            )
        _overlay_studio_identity(
            studio_path, args.studio_id, args.studio_name, args.studio_role
        )
        (projects / "config").mkdir(exist_ok=True)

        version = _git_version(source_library)
        (library / "VERSION").write_text(version + "\n", encoding="utf-8")
        manifest = {
            "schema": "smartpipeline.vendor-distribution.v1",
            "version": version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": {
                "smartlibrary": source_library.as_posix(),
                "smartprojects": source_projects.as_posix(),
            },
            "profile": "vendor-minimal",
            "included_tools": [
                "Smart Launcher",
                "Config Creator",
                "Smart Delivery",
                "Preflight Validation",
            ],
            "excluded_tool_runtimes": ["ffmpeg", "maya", "openrv", "usd"],
            "smarttools_runtime": _smarttools_dependency(source_library),
            "trees": {
                "smartlibrary": _tree_summary(library),
                "smartprojects": _tree_summary(projects),
            },
        }
        if args.tools_mode == "copy":
            manifest["source"]["smarttools"] = source_tools.as_posix()
            manifest["trees"]["smarttools"] = _tree_summary(tools)
        manifest["launcher_sha256"] = _sha256(library / "SmartLauncher.bat")
        (staging / "distribution.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        replace_names = ["smartlibrary"]
        if args.tools_mode == "copy":
            replace_names.append("smarttools")
        existing = [
            output_root / name
            for name in replace_names
            if (output_root / name).exists()
        ]
        if existing and not args.replace:
            names = ", ".join(str(path) for path in existing)
            raise FileExistsError(f"Distribution targets already exist; use --replace: {names}")
        if existing:
            backup = output_root / "_previous" / datetime.now().strftime("%Y%m%d_%H%M%S")
            backup.mkdir(parents=True)
            for target in existing:
                shutil.move(str(target), backup / target.name)

        for name in replace_names:
            shutil.move(str(staging / name), output_root / name)
        destination_projects = output_root / "smartprojects"
        if not destination_projects.exists():
            shutil.move(str(projects), destination_projects)
        else:
            for child in projects.iterdir():
                target = destination_projects / child.name
                if not target.exists():
                    shutil.move(str(child), target)
            _overlay_studio_identity(
                destination_projects / "studio.yml",
                args.studio_id,
                args.studio_name,
                args.studio_role,
            )
        shutil.move(str(staging / "distribution.json"), output_root / "distribution.json")
        return output_root
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a minimal SmartPipeline vendor distribution.")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-library", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source-projects", type=Path, default=Path(__file__).resolve().parents[2] / "smartprojects")
    parser.add_argument("--source-tools", type=Path, default=Path(__file__).resolve().parents[2] / "smarttools")
    parser.add_argument(
        "--tools-mode",
        choices=("external", "copy"),
        default="external",
        help="Keep SmartTools as an external versioned Runtime, or copy it for legacy bundles.",
    )
    parser.add_argument("--projects", choices=("none", "studio", "all"), default="studio")
    parser.add_argument("--studio-id", default="vendor_test")
    parser.add_argument("--studio-name", default="Vendor Test")
    parser.add_argument("--studio-role", choices=("internal", "vendor"), default="vendor")
    parser.add_argument("--replace", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    destination = build_distribution(parse_args())
    print(destination)
