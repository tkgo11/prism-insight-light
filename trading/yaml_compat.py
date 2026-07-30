"""Small, explicit wrapper around the required PyYAML dependency."""

from __future__ import annotations

from typing import Any

try:
    import yaml as _pyyaml
except ModuleNotFoundError as exc:  # pragma: no cover - installation failure
    raise ModuleNotFoundError(
        "PyYAML is required; install the project dependencies from requirements.txt"
    ) from exc


def safe_load(stream: Any) -> Any:
    return _pyyaml.safe_load(stream)


def safe_dump(data: Any, *args: Any, **kwargs: Any) -> str:
    return _pyyaml.safe_dump(data, *args, **kwargs)
