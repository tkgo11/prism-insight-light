"""Small YAML compatibility layer used when PyYAML is unavailable.

The project depends on PyYAML in normal installations.  Some CI/test
sandboxes import the package before dependencies are installed, so this module
loads PyYAML when present and otherwise provides a minimal safe_load/safe_dump
implementation that supports the repository's simple config/token YAML shapes.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

_yaml_spec = importlib.util.find_spec("yaml")
_pyyaml = importlib.import_module("yaml") if _yaml_spec is not None else None


def safe_load(stream: Any) -> Any:
    if _pyyaml is not None:
        return _pyyaml.safe_load(stream)

    text = stream.read() if hasattr(stream, "read") else str(stream)
    lines = _prepare_lines(text)
    if not lines:
        return None
    value, _ = _parse_block(lines, 0, lines[0][0])
    return value


def safe_dump(data: Any, *args: Any, **kwargs: Any) -> str:
    if _pyyaml is not None:
        return _pyyaml.safe_dump(data, *args, **kwargs)

    sort_keys = kwargs.get("sort_keys", True)
    indent = int(kwargs.get("indent", 2))
    return _dump_value(data, 0, indent, sort_keys).rstrip() + "\n"


def _prepare_lines(text: str) -> list[tuple[int, str]]:
    prepared: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        stripped = _strip_comment(raw_line).rstrip()
        if not stripped.strip():
            continue
        prepared.append((len(stripped) - len(stripped.lstrip(" ")), stripped.lstrip(" ")))
    return prepared


def _strip_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in {'"', "'"}:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
            continue
        if char == "#" and quote is None:
            return line[:index]
    return line


def _parse_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return None, index
    if lines[index][1].startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_map(lines, index, indent)


def _parse_map(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent or content.startswith("- "):
            break
        if current_indent > indent:
            index += 1
            continue
        key, value_text = _split_key_value(content)
        if value_text == "":
            next_index = index + 1
            if next_index < len(lines) and lines[next_index][0] > current_indent:
                value, index = _parse_block(lines, next_index, lines[next_index][0])
            else:
                value, index = None, next_index
        else:
            value, index = _parse_scalar(value_text), index + 1
        result[key] = value
    return result, index


def _parse_list(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent or not content.startswith("- "):
            break
        item_text = content[2:].strip()
        if item_text == "":
            next_index = index + 1
            if next_index < len(lines) and lines[next_index][0] > current_indent:
                item, index = _parse_block(lines, next_index, lines[next_index][0])
            else:
                item, index = None, next_index
            result.append(item)
            continue
        if ":" in item_text and not item_text.startswith(('"', "'")):
            key, value_text = _split_key_value(item_text)
            item: dict[str, Any] = {key: _parse_scalar(value_text) if value_text else None}
            index += 1
            if index < len(lines) and lines[index][0] > current_indent:
                extra, index = _parse_map(lines, index, lines[index][0])
                item.update(extra)
            result.append(item)
            continue
        result.append(_parse_scalar(item_text))
        index += 1
    return result, index


def _split_key_value(content: str) -> tuple[str, str]:
    key, separator, value = content.partition(":")
    if not separator:
        raise ValueError(f"Invalid YAML line: {content}")
    return _unquote(key.strip()), value.strip()


def _parse_scalar(value: str) -> Any:
    if value == "":
        return None
    lowered = value.lower()
    if lowered in {"null", "~", "none"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if value[0:1] in {'"', "'"} and value[-1:] == value[0]:
        return _unquote(value)
    try:
        return int(value.replace("_", ""), 10)
    except ValueError:
        pass
    try:
        return float(value.replace("_", ""))
    except ValueError:
        return value


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        if value[0] == '"':
            return json.loads(value)
        return value[1:-1].replace("''", "'")
    return value


def _dump_value(value: Any, level: int, indent: int, sort_keys: bool) -> str:
    prefix = " " * level
    if isinstance(value, dict):
        keys: Iterable[Any] = sorted(value) if sort_keys else value.keys()
        parts: list[str] = []
        for key in keys:
            item = value[key]
            if isinstance(item, (dict, list)):
                parts.append(f"{prefix}{key}:")
                parts.append(_dump_value(item, level + indent, indent, sort_keys).rstrip())
            else:
                parts.append(f"{prefix}{key}: {_format_scalar(item)}")
        return "\n".join(parts) + "\n"
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                keys = list(sorted(item) if sort_keys else item.keys())
                if not keys:
                    parts.append(f"{prefix}- {{}}")
                    continue
                first_key = keys[0]
                first_value = item[first_key]
                if isinstance(first_value, (dict, list)):
                    parts.append(f"{prefix}- {first_key}:")
                    parts.append(_dump_value(first_value, level + indent, indent, sort_keys).rstrip())
                else:
                    parts.append(f"{prefix}- {first_key}: {_format_scalar(first_value)}")
                for key in keys[1:]:
                    nested = item[key]
                    if isinstance(nested, (dict, list)):
                        parts.append(f"{' ' * (level + indent)}{key}:")
                        parts.append(_dump_value(nested, level + (2 * indent), indent, sort_keys).rstrip())
                    else:
                        parts.append(f"{' ' * (level + indent)}{key}: {_format_scalar(nested)}")
            elif isinstance(item, list):
                parts.append(f"{prefix}-")
                parts.append(_dump_value(item, level + indent, indent, sort_keys).rstrip())
            else:
                parts.append(f"{prefix}- {_format_scalar(item)}")
        return "\n".join(parts) + "\n"
    return f"{prefix}{_format_scalar(value)}\n"


def _format_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return json.dumps(value.isoformat())
    return json.dumps(str(value), ensure_ascii=False)
