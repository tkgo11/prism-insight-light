#!/usr/bin/env python3
"""Isolated Pub/Sub readiness helper."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ENV_PROJECT_ID = "GCP_PROJECT_ID"
ENV_SUBSCRIPTION_ID = "GCP_PUBSUB_SUBSCRIPTION_ID"
ENV_CREDENTIALS_PATH = "GCP_CREDENTIALS_PATH"
CONSUME_PERMISSION = "pubsub.subscriptions.consume"

EXIT_READY = 0
EXIT_FAILURE = 1
EXIT_DENIED = 2
EXIT_INDETERMINATE = 3


@dataclass(frozen=True)
class ReadinessResult:
    status: str
    exit_code: int
    message: str
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class _MetadataDiagnostic:
    state: str
    message: str | None = None


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _resolve_setting(value: str | None, env_name: str) -> str | None:
    return _clean_text(value) if value is not None else _clean_text(os.environ.get(env_name))


def _format_missing(names: Iterable[str]) -> str:
    missing = list(names)
    if len(missing) == 1:
        return missing[0]
    return ", ".join(missing[:-1]) + f" and {missing[-1]}"


def _credentials_label(raw_path: str | None) -> str:
    if not raw_path:
        return "provided credentials file"
    try:
        name = Path(raw_path).name
    except Exception:  # noqa: BLE001 - defensive path formatting
        name = ""
    return name or "provided credentials file"


def _result(status: str, exit_code: int, message: str, diagnostics: list[str] | None = None) -> ReadinessResult:
    return ReadinessResult(
        status=status,
        exit_code=exit_code,
        message=message,
        diagnostics=tuple(diagnostics or ()),
    )


def _hard_failure(reason: str, diagnostics: list[str] | None = None) -> ReadinessResult:
    return _result("failure", EXIT_FAILURE, f"NOT READY: {reason}", diagnostics)


def _denied(subscription_path: str, diagnostics: list[str] | None = None) -> ReadinessResult:
    return _result(
        "denied",
        EXIT_DENIED,
        f"NOT READY: missing {CONSUME_PERMISSION} on {subscription_path}",
        diagnostics,
    )


def _ready(subscription_path: str, diagnostics: list[str] | None = None) -> ReadinessResult:
    return _result(
        "ready",
        EXIT_READY,
        f"READY: consume permission confirmed for {subscription_path}",
        diagnostics,
    )


def _indeterminate(subscription_path: str, reason: str, diagnostics: list[str] | None = None) -> ReadinessResult:
    return _result(
        "indeterminate",
        EXIT_INDETERMINATE,
        f"INDETERMINATE: consume probe skipped for {subscription_path} ({reason})",
        diagnostics,
    )


def _validate_credentials_path(credentials_path: str | None) -> tuple[Path | None, ReadinessResult | None]:
    if not credentials_path:
        return None, None

    path = Path(credentials_path).expanduser()
    label = _credentials_label(credentials_path)
    if not path.exists():
        return None, _hard_failure(f"credentials file not found ({label})")
    if not path.is_file():
        return None, _hard_failure(f"credentials path is not a file ({label})")
    try:
        with path.open("rb"):
            pass
    except OSError:
        return None, _hard_failure(f"credentials file is unreadable ({label})")
    return path, None


def _close_quietly(subscriber) -> None:
    try:
        subscriber.close()
    except Exception:
        pass


def _load_pubsub_dependencies():
    try:
        from google.api_core import exceptions as google_exceptions
        from google.cloud import pubsub_v1
    except Exception as exc:  # noqa: BLE001 - import errors should become hard failures
        return None, None, None, _hard_failure(f"Pub/Sub libraries unavailable ({type(exc).__name__})")

    try:
        from google.oauth2 import service_account
    except Exception:
        service_account = None

    return google_exceptions, pubsub_v1, service_account, None


def _load_credentials(credentials_path: Path, service_account) -> tuple[object | None, ReadinessResult | None]:
    if service_account is None:
        return None, _hard_failure("service-account credentials support unavailable")
    try:
        credentials = service_account.Credentials.from_service_account_file(str(credentials_path))
    except Exception as exc:  # noqa: BLE001 - keep errors concise and non-secret
        return None, _hard_failure(f"unable to load credentials file ({type(exc).__name__})")

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials_path)
    return credentials, None


def _check_subscription_metadata(subscriber, subscription_path: str, google_exceptions) -> _MetadataDiagnostic:
    get_subscription = getattr(subscriber, "get_subscription", None)
    if not callable(get_subscription):
        return _MetadataDiagnostic("unavailable", "supplemental metadata probe unavailable")

    try:
        get_subscription(request={"subscription": subscription_path})
    except google_exceptions.NotFound:
        return _MetadataDiagnostic("missing", "supplemental metadata probe: subscription not found")
    except google_exceptions.PermissionDenied:
        return _MetadataDiagnostic("unavailable", "supplemental metadata probe unavailable (permission denied)")
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        return _MetadataDiagnostic(
            "unavailable",
            f"supplemental metadata probe unavailable ({type(exc).__name__})",
        )
    return _MetadataDiagnostic("found")


def check_pubsub_readiness(
    project_id: str | None = None,
    subscription_id: str | None = None,
    credentials_path: str | None = None,
) -> ReadinessResult:
    """Check whether Pub/Sub consume access is ready for the subscription."""

    resolved_project_id = _resolve_setting(project_id, ENV_PROJECT_ID)
    resolved_subscription_id = _resolve_setting(subscription_id, ENV_SUBSCRIPTION_ID)
    resolved_credentials_path = _resolve_setting(credentials_path, ENV_CREDENTIALS_PATH)

    missing = []
    if not resolved_project_id:
        missing.append(ENV_PROJECT_ID)
    if not resolved_subscription_id:
        missing.append(ENV_SUBSCRIPTION_ID)
    if missing:
        return _hard_failure(f"missing {_format_missing(missing)}")

    validated_credentials_path, validation_error = _validate_credentials_path(resolved_credentials_path)
    if validation_error:
        return validation_error

    google_exceptions, pubsub_v1, service_account, dependency_error = _load_pubsub_dependencies()
    if dependency_error:
        return dependency_error

    credentials = None
    if validated_credentials_path:
        credentials, credentials_error = _load_credentials(validated_credentials_path, service_account)
        if credentials_error:
            return credentials_error

    try:
        subscriber = (
            pubsub_v1.SubscriberClient(credentials=credentials)
            if credentials is not None
            else pubsub_v1.SubscriberClient()
        )
    except Exception as exc:  # noqa: BLE001 - client init failure is actionable
        return _hard_failure(f"unable to create Pub/Sub client ({type(exc).__name__})")

    try:
        subscription_path = subscriber.subscription_path(resolved_project_id, resolved_subscription_id)
    except Exception as exc:  # noqa: BLE001 - malformed identifiers should fail fast
        _close_quietly(subscriber)
        return _hard_failure(f"unable to build subscription path ({type(exc).__name__})")

    diagnostics: list[str] = []
    try:
        try:
            response = subscriber.test_iam_permissions(
                request={
                    "resource": subscription_path,
                    "permissions": [CONSUME_PERMISSION],
                }
            )
        except google_exceptions.PermissionDenied:
            primary_status = "denied"
            primary_reason = None
        except AttributeError:
            primary_status = "indeterminate"
            primary_reason = "test_iam_permissions unavailable"
        except Exception as exc:  # noqa: BLE001 - non-denial probe failures are indeterminate
            primary_status = "indeterminate"
            primary_reason = type(exc).__name__
        else:
            permissions = set(getattr(response, "permissions", ()) or ())
            if CONSUME_PERMISSION in permissions:
                primary_status = "ready"
                primary_reason = None
            else:
                primary_status = "denied"
                primary_reason = None

        metadata = _check_subscription_metadata(subscriber, subscription_path, google_exceptions)
    finally:
        _close_quietly(subscriber)

    if metadata.message:
        diagnostics.append(metadata.message)

    if primary_status == "ready":
        return _ready(subscription_path, diagnostics)

    if metadata.state == "missing":
        return _hard_failure(f"subscription not found for {subscription_path}", diagnostics)

    if primary_status == "denied":
        return _denied(subscription_path, diagnostics)

    return _indeterminate(subscription_path, primary_reason or "unknown reason", diagnostics)


__all__ = [
    "CONSUME_PERMISSION",
    "ENV_CREDENTIALS_PATH",
    "ENV_PROJECT_ID",
    "ENV_SUBSCRIPTION_ID",
    "EXIT_DENIED",
    "EXIT_FAILURE",
    "EXIT_INDETERMINATE",
    "EXIT_READY",
    "ReadinessResult",
    "check_pubsub_readiness",
]
