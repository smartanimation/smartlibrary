from __future__ import annotations

from pathlib import Path
from typing import Any


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load YAML with PyYAML when present, otherwise a small pipeline-safe subset."""

    yaml_path = Path(path)
    try:
        import yaml
    except ImportError:
        text = yaml_path.read_text(encoding="utf-8")
        data = loads_yaml(text)
    else:
        with yaml_path.open("r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {yaml_path}")
    return data


def write_yaml(path: str | Path, data: dict[str, Any]) -> None:
    yaml_path = Path(path)
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(dumps_yaml(data), encoding="utf-8")


def dumps_yaml(data: Any) -> str:
    lines = _dump_node(data, 0)
    return "\n".join(lines).rstrip() + "\n"


def loads_yaml(text: str) -> dict[str, Any]:
    lines = _prepared_lines(text)
    if not lines:
        return {}
    value, index = _parse_node(lines, 0, lines[0][0])
    if index < len(lines):
        raise ValueError(f"Unexpected YAML content at line {index + 1}: {lines[index][1]}")
    if not isinstance(value, dict):
        raise ValueError("YAML root must be a mapping.")
    return value


def _prepared_lines(text: str) -> list[tuple[int, str]]:
    result = []
    for raw in text.splitlines():
        line = _strip_comment(raw.rstrip())
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        result.append((indent, line.strip()))
    return result


def _strip_comment(line: str) -> str:
    quote = ""
    for index, char in enumerate(line):
        if char in {"'", '"'} and (index == 0 or line[index - 1] != "\\"):
            quote = "" if quote == char else char if not quote else quote
        if char == "#" and not quote:
            return line[:index].rstrip()
    return line


def _parse_node(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if lines[index][0] != indent:
        raise ValueError(f"Invalid YAML indentation at line {index + 1}.")
    if lines[index][1].startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_map(lines, index, indent)


def _parse_list(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[list[Any], int]:
    result = []
    while index < len(lines):
        current_indent, stripped = lines[index]
        if current_indent != indent or not stripped.startswith("- "):
            break
        item_text = stripped[2:].strip()
        index += 1
        if not item_text:
            if index < len(lines) and lines[index][0] > indent:
                value, index = _parse_node(lines, index, lines[index][0])
            else:
                value = None
        elif _looks_like_key_value(item_text):
            key, raw_value = _split_key_value(item_text)
            value = {}
            if raw_value:
                value[key] = _parse_scalar(raw_value)
            elif index < len(lines) and lines[index][0] > indent:
                value[key], index = _parse_node(lines, index, lines[index][0])
            else:
                value[key] = {}
            if index < len(lines) and lines[index][0] > indent:
                more, index = _parse_map(lines, index, lines[index][0])
                value.update(more)
        else:
            value = _parse_scalar(item_text)
        result.append(value)
    return result, index


def _parse_map(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        current_indent, stripped = lines[index]
        if current_indent != indent or stripped.startswith("- "):
            break
        if not _looks_like_key_value(stripped):
            raise ValueError(f"Expected key/value pair at line {index + 1}: {stripped}")
        key, raw_value = _split_key_value(stripped)
        index += 1
        if raw_value:
            result[key] = _parse_scalar(raw_value)
        elif index < len(lines) and lines[index][0] > indent:
            result[key], index = _parse_node(lines, index, lines[index][0])
        else:
            result[key] = {}
    return result, index


def _looks_like_key_value(text: str) -> bool:
    return ":" in text and not text.startswith(("'", '"'))


def _split_key_value(text: str) -> tuple[str, str]:
    key, value = text.split(":", 1)
    key = key.strip()
    if not key:
        raise ValueError(f"YAML key cannot be empty: {text}")
    return key, value.strip()


def _parse_scalar(value: str) -> Any:
    if value in {"[]", "{}"}:
        return [] if value == "[]" else {}
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    lowered = value.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"null", "none", "~"}:
        return None
    try:
        if any(char in value for char in (".", "e", "E")):
            return float(value)
        return int(value)
    except ValueError:
        return value


def _dump_node(value: Any, indent: int) -> list[str]:
    if isinstance(value, dict):
        return _dump_dict(value, indent)
    if isinstance(value, list):
        return _dump_list(value, indent)
    return [" " * indent + _format_scalar(value)]


def _dump_dict(data: dict[str, Any], indent: int) -> list[str]:
    lines = []
    prefix = " " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            if value:
                lines.append(f"{prefix}{key}:")
                lines.extend(_dump_dict(value, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {{}}")
        elif isinstance(value, list):
            if value:
                lines.append(f"{prefix}{key}:")
                lines.extend(_dump_list(value, indent + 2))
            else:
                lines.append(f"{prefix}{key}: []")
        else:
            lines.append(f"{prefix}{key}: {_format_scalar(value)}")
    return lines


def _dump_list(values: list[Any], indent: int) -> list[str]:
    lines = []
    prefix = " " * indent
    for value in values:
        if isinstance(value, dict):
            if not value:
                lines.append(f"{prefix}- {{}}")
                continue
            items = list(value.items())
            first_key, first_value = items[0]
            if isinstance(first_value, (dict, list)):
                lines.append(f"{prefix}- {first_key}:")
                lines.extend(_dump_node(first_value, indent + 4))
            else:
                lines.append(f"{prefix}- {first_key}: {_format_scalar(first_value)}")
            for key, nested_value in items[1:]:
                if isinstance(nested_value, (dict, list)):
                    lines.append(f"{prefix}  {key}:")
                    lines.extend(_dump_node(nested_value, indent + 4))
                else:
                    lines.append(f"{prefix}  {key}: {_format_scalar(nested_value)}")
        elif isinstance(value, list):
            lines.append(f"{prefix}-")
            lines.extend(_dump_list(value, indent + 2))
        else:
            lines.append(f"{prefix}- {_format_scalar(value)}")
    return lines


def _format_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "" or text.lower() in {"true", "false", "null", "none", "yes", "no", "on", "off"}:
        return repr(text)
    if any(char in text for char in (":", "#", "\n")) or text.strip() != text:
        return repr(text)
    return text
