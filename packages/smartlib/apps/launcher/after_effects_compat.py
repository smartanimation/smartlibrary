"""Windows launch preflight for known After Effects compatibility failures."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable, Mapping


INCOMPATIBLE_MACHINE_LEARNING_VERSIONS = {(1, 24, 4, 0)}


@dataclass(frozen=True)
class CameraRawCompatibilityIssue:
    plugin: Path
    backup: Path
    plugin_version: tuple[int, int, int, int] | None
    machine_learning_version: tuple[int, int, int, int]


def is_after_effects_2024(software_id: str, executable: str | os.PathLike[str]) -> bool:
    identity = " ".join((str(software_id or ""), str(executable or ""))).lower()
    compact = "".join(character for character in identity if character.isalnum())
    return "aftereffects2024" in compact or "afterfx2024" in compact


def camera_raw_plugin_path(environ: Mapping[str, str] | None = None) -> Path | None:
    values = os.environ if environ is None else environ
    program_files = values.get("ProgramW6432") or values.get("ProgramFiles")
    if not program_files:
        return None
    return (
        Path(program_files)
        / "Common Files"
        / "Adobe"
        / "Plug-Ins"
        / "CC"
        / "File Formats"
        / "Camera Raw.8bi"
    )


def windows_file_version(path: str | os.PathLike[str]) -> tuple[int, int, int, int] | None:
    if os.name != "nt":
        return None

    class VS_FIXEDFILEINFO(ctypes.Structure):
        _fields_ = [
            ("dwSignature", wintypes.DWORD),
            ("dwStrucVersion", wintypes.DWORD),
            ("dwFileVersionMS", wintypes.DWORD),
            ("dwFileVersionLS", wintypes.DWORD),
            ("dwProductVersionMS", wintypes.DWORD),
            ("dwProductVersionLS", wintypes.DWORD),
            ("dwFileFlagsMask", wintypes.DWORD),
            ("dwFileFlags", wintypes.DWORD),
            ("dwFileOS", wintypes.DWORD),
            ("dwFileType", wintypes.DWORD),
            ("dwFileSubtype", wintypes.DWORD),
            ("dwFileDateMS", wintypes.DWORD),
            ("dwFileDateLS", wintypes.DWORD),
        ]

    version_api = ctypes.windll.version
    size = version_api.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    buffer = ctypes.create_string_buffer(size)
    if not version_api.GetFileVersionInfoW(str(path), 0, size, buffer):
        return None
    value = ctypes.c_void_p()
    length = wintypes.UINT()
    if not version_api.VerQueryValueW(buffer, "\\", ctypes.byref(value), ctypes.byref(length)):
        return None
    info = ctypes.cast(value, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
    return (
        info.dwFileVersionMS >> 16,
        info.dwFileVersionMS & 0xFFFF,
        info.dwFileVersionLS >> 16,
        info.dwFileVersionLS & 0xFFFF,
    )


def _backup_path(plugin: Path, version: tuple[int, int, int, int] | None) -> Path:
    version_label = ".".join(str(part) for part in version[:2]) if version else "unknown"
    candidate = plugin.with_name(f"{plugin.name}.disabled-{version_label}")
    index = 2
    while candidate.exists():
        candidate = plugin.with_name(f"{plugin.name}.disabled-{version_label}-{index}")
        index += 1
    return candidate


def detect_camera_raw_issue(
    software_id: str,
    executable: str | os.PathLike[str],
    *,
    environ: Mapping[str, str] | None = None,
    version_reader: Callable[[str | os.PathLike[str]], tuple[int, int, int, int] | None] = windows_file_version,
) -> CameraRawCompatibilityIssue | None:
    if not is_after_effects_2024(software_id, executable):
        return None
    plugin = camera_raw_plugin_path(environ)
    if plugin is None or not plugin.is_file():
        return None
    machine_learning = plugin.with_name("Microsoft.AI.MachineLearning.dll")
    if not machine_learning.is_file():
        return None
    machine_learning_version = version_reader(machine_learning)
    if machine_learning_version not in INCOMPATIBLE_MACHINE_LEARNING_VERSIONS:
        return None
    plugin_version = version_reader(plugin)
    return CameraRawCompatibilityIssue(
        plugin=plugin,
        backup=_backup_path(plugin, plugin_version),
        plugin_version=plugin_version,
        machine_learning_version=machine_learning_version,
    )


def _validated_rename(source: Path, destination: Path) -> None:
    expected_suffix = ("Adobe", "Plug-Ins", "CC", "File Formats")
    if source.name.casefold() != "camera raw.8bi":
        raise ValueError("Only Camera Raw.8bi can be disabled by this helper.")
    if tuple(part.casefold() for part in source.parent.parts[-4:]) != tuple(
        part.casefold() for part in expected_suffix
    ):
        raise ValueError("Camera Raw is not in the expected Adobe common plug-in directory.")
    if destination.parent != source.parent or not destination.name.casefold().startswith(
        "camera raw.8bi.disabled-"
    ):
        raise ValueError("Invalid Camera Raw backup path.")
    if destination.exists():
        raise FileExistsError(destination)
    source.rename(destination)


def disable_camera_raw_elevated(
    issue: CameraRawCompatibilityIssue,
    *,
    python_executable: str | os.PathLike[str] | None = None,
) -> bool:
    """Run this module elevated and wait for its narrowly validated rename."""
    if os.name != "nt":
        raise OSError("Camera Raw remediation is only supported on Windows.")

    executable = str(python_executable or sys.executable)
    arguments = subprocess.list2cmdline(
        [str(Path(__file__).resolve()), "--disable", str(issue.plugin), str(issue.backup)]
    )

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    execute_info = SHELLEXECUTEINFOW()
    execute_info.cbSize = ctypes.sizeof(execute_info)
    execute_info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    execute_info.lpVerb = "runas"
    execute_info.lpFile = executable
    execute_info.lpParameters = arguments
    execute_info.lpDirectory = str(issue.plugin.parent)
    execute_info.nShow = 0  # SW_HIDE
    shell_execute = ctypes.windll.shell32.ShellExecuteExW
    shell_execute.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell_execute.restype = wintypes.BOOL
    wait_for_process = ctypes.windll.kernel32.WaitForSingleObject
    wait_for_process.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    wait_for_process.restype = wintypes.DWORD
    get_exit_code = ctypes.windll.kernel32.GetExitCodeProcess
    get_exit_code.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    get_exit_code.restype = wintypes.BOOL
    close_handle = ctypes.windll.kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    if not shell_execute(ctypes.byref(execute_info)):
        raise ctypes.WinError()
    try:
        wait_for_process(execute_info.hProcess, 0xFFFFFFFF)
        exit_code = wintypes.DWORD()
        if not get_exit_code(execute_info.hProcess, ctypes.byref(exit_code)):
            raise ctypes.WinError()
        return exit_code.value == 0 and not issue.plugin.exists() and issue.backup.is_file()
    finally:
        close_handle(execute_info.hProcess)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--disable", action="store_true")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    options = parser.parse_args(argv)
    if not options.disable:
        parser.error("Only --disable is supported.")
    try:
        _validated_rename(options.source, options.destination)
    except Exception as exc:
        print(f"Camera Raw remediation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
