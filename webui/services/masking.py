"""Allowlist-first DTO helpers and defense-in-depth text masking."""

from __future__ import annotations

import html
import os
import re
from pathlib import Path
from typing import Mapping

_SECRET_KEYWORDS = ("secret", "token", "key", "password", "credential", "private")
_SAFE_PUBLIC_KEYS = {
    "GCP_PROJECT_ID",
    "GCP_PUBSUB_SUBSCRIPTION_ID",
    "TELEGRAM_SIGNAL_CHANNEL_URL",
    "TELEGRAM_FETCH_PAGES",
    "WEBUI_HOST",
    "WEBUI_PORT",
    "WEBUI_ALLOW_NON_LOOPBACK",
}

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH |)PRIVATE KEY-----",
    re.DOTALL,
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_JSON_SECRET_RE = re.compile(
    r'(?i)("(?:private_key|private_key_id|client_secret|token|access_token|refresh_token|app_secret|my_sec)"\s*:\s*")([^"]+)(")'
)
_ENV_SECRET_RE = re.compile(
    r"(?im)^([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PRIVATE|CREDENTIAL|KEY)[A-Z0-9_]*\s*=\s*)(.+)$"
)
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")
_ACCOUNT_RE = re.compile(r"\b\d{8}(?:-?\d{2})?\b")


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    if key in _SAFE_PUBLIC_KEYS:
        return False
    return any(word in normalized for word in _SECRET_KEYWORDS) or normalized in {"my_app", "my_sec", "account", "cano"}


def mask_secret_value(value: object, *, keep: int = 4) -> str:
    if value is None:
        return "missing"
    text = str(value)
    if not text:
        return "missing"
    if len(text) <= keep * 2:
        return "*" * len(text)
    return f"{text[:keep]}{'*' * 8}{text[-keep:]}"


def safe_path_label(path_value: str | None) -> str:
    if not path_value:
        return "missing"
    try:
        return Path(path_value).name or "configured"
    except Exception:  # noqa: BLE001 - defensive display only
        return "configured"


def config_item(name: str, value: object | None) -> dict[str, object]:
    configured = value not in (None, "")
    item: dict[str, object] = {"name": name, "configured": configured}
    if not configured:
        item["value"] = "missing"
    elif name.endswith("PATH"):
        item["value"] = safe_path_label(str(value))
    elif is_sensitive_key(name):
        item["value"] = mask_secret_value(value)
    else:
        item["value"] = str(value)
    return item


def allowlisted_env_status(env: Mapping[str, str] | None = None) -> list[dict[str, object]]:
    source = env if env is not None else os.environ
    names = [
        "GCP_PROJECT_ID",
        "GCP_PUBSUB_SUBSCRIPTION_ID",
        "GCP_CREDENTIALS_PATH",
        "TELEGRAM_SIGNAL_CHANNEL_URL",
        "TELEGRAM_FETCH_PAGES",
        "WEBUI_HOST",
        "WEBUI_PORT",
        "WEBUI_ALLOW_NON_LOOPBACK",
    ]
    return [config_item(name, source.get(name)) for name in names]


def mask_text(text: object, extra_values: Mapping[str, str] | None = None) -> str:
    """Best-effort masking for logs/errors; config DTOs must still be allowlisted."""
    masked = "" if text is None else str(text)
    masked = _PRIVATE_KEY_RE.sub("[MASKED_PRIVATE_KEY]", masked)
    masked = _BEARER_RE.sub("Bearer [MASKED_TOKEN]", masked)
    masked = _JSON_SECRET_RE.sub(lambda m: f"{m.group(1)}[MASKED]{m.group(3)}", masked)
    masked = _ENV_SECRET_RE.sub(lambda m: f"{m.group(1)}[MASKED]", masked)

    if extra_values:
        for key, value in extra_values.items():
            if not value or not is_sensitive_key(key):
                continue
            masked = masked.replace(str(value), mask_secret_value(value))

    masked = _ACCOUNT_RE.sub(lambda m: mask_secret_value(m.group(0), keep=2), masked)
    masked = _LONG_TOKEN_RE.sub(lambda m: mask_secret_value(m.group(0)), masked)
    return masked


def escape_masked_text(text: object, extra_values: Mapping[str, str] | None = None) -> str:
    return html.escape(mask_text(text, extra_values=extra_values))
