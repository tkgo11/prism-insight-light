from __future__ import annotations

import importlib
import sys
import types
from dataclasses import dataclass

import pytest


MODULE_NAME = "pubsub_readiness"
CONSUME_PERMISSION = "pubsub.subscriptions.consume"


class FakePermissionDenied(Exception):
    """Permission-denied stub used when google.api_core is unavailable."""


class FakeNotFound(Exception):
    """Not-found stub used when google.api_core is unavailable."""


class FakeGoogleAPICallError(Exception):
    """Generic API error stub used when google.api_core is unavailable."""


@dataclass
class FakePermissionsResponse:
    permissions: list[str]


@dataclass
class FakeCredentials:
    source: str


class FakeClient:
    permissions: list[str] | None = None
    permission_error: Exception | None = None
    metadata_error: Exception | None = None
    created_kwargs: list[dict] = []
    permission_requests: list[dict] = []
    metadata_requests: list[dict] = []
    closed_count: int = 0

    def __init__(self, *args, **kwargs):
        type(self).created_kwargs.append(dict(kwargs))

    @classmethod
    def reset(cls):
        cls.permissions = None
        cls.permission_error = None
        cls.metadata_error = None
        cls.created_kwargs = []
        cls.permission_requests = []
        cls.metadata_requests = []
        cls.closed_count = 0

    def subscription_path(self, project_id: str, subscription_id: str) -> str:
        return f"projects/{project_id}/subscriptions/{subscription_id}"

    def test_iam_permissions(self, request: dict):
        type(self).permission_requests.append(dict(request))
        if self.permission_error is not None:
            raise self.permission_error
        return FakePermissionsResponse(list(self.permissions or []))

    def get_subscription(self, request=None, subscription=None):
        payload = request or {"subscription": subscription}
        type(self).metadata_requests.append(dict(payload))
        if self.metadata_error is not None:
            raise self.metadata_error
        return {"name": payload["subscription"]}

    def close(self):
        type(self).closed_count += 1


class RaisingClient(FakeClient):
    init_error: Exception | None = None

    @classmethod
    def reset(cls):
        super().reset()
        cls.init_error = None

    def __init__(self, *args, **kwargs):
        if type(self).init_error is not None:
            raise type(self).init_error
        super().__init__(*args, **kwargs)


def _reset_module(name: str) -> None:
    sys.modules.pop(name, None)


def _install_google_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    client_cls=FakeClient,
    credentials_error: Exception | None = None,
):
    google = types.ModuleType("google")
    cloud = types.ModuleType("google.cloud")
    pubsub_v1 = types.ModuleType("google.cloud.pubsub_v1")
    api_core = types.ModuleType("google.api_core")
    exceptions = types.ModuleType("google.api_core.exceptions")
    oauth2 = types.ModuleType("google.oauth2")
    service_account = types.ModuleType("google.oauth2.service_account")

    exceptions.PermissionDenied = FakePermissionDenied
    exceptions.NotFound = FakeNotFound
    exceptions.GoogleAPICallError = FakeGoogleAPICallError

    pubsub_v1.SubscriberClient = client_cls

    class Credentials:
        @staticmethod
        def from_service_account_file(path: str):
            if credentials_error is not None:
                raise credentials_error
            return FakeCredentials(source=path)

    service_account.Credentials = Credentials

    google.cloud = cloud
    google.api_core = api_core
    google.oauth2 = oauth2
    cloud.pubsub_v1 = pubsub_v1
    api_core.exceptions = exceptions
    oauth2.service_account = service_account

    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.pubsub_v1", pubsub_v1)
    monkeypatch.setitem(sys.modules, "google.api_core", api_core)
    monkeypatch.setitem(sys.modules, "google.api_core.exceptions", exceptions)
    monkeypatch.setitem(sys.modules, "google.oauth2", oauth2)
    monkeypatch.setitem(sys.modules, "google.oauth2.service_account", service_account)


def _import_pubsub_readiness(monkeypatch: pytest.MonkeyPatch, **stub_kwargs):
    FakeClient.reset()
    RaisingClient.reset()
    before_modules = set(sys.modules)
    for name in (
        MODULE_NAME,
        "google",
        "google.cloud",
        "google.cloud.pubsub_v1",
        "google.api_core",
        "google.api_core.exceptions",
        "google.oauth2",
        "google.oauth2.service_account",
    ):
        _reset_module(name)
    _install_google_stubs(monkeypatch, **stub_kwargs)
    importlib.invalidate_caches()
    module = importlib.import_module(MODULE_NAME)
    imported_now = set(sys.modules) - before_modules
    assert "subscriber" not in imported_now
    assert not any(name == "trading" or name.startswith("trading.") for name in imported_now)
    return module


def _call_check(module, **kwargs):
    check = getattr(module, "check_pubsub_readiness", None)
    assert callable(check), "pubsub_readiness.check_pubsub_readiness is required"
    return check(**kwargs)


def test_returns_ready_when_consume_permission_is_confirmed(monkeypatch):
    module = _import_pubsub_readiness(monkeypatch)
    FakeClient.permissions = [CONSUME_PERMISSION]

    result = _call_check(
        module,
        project_id="demo-project",
        subscription_id="demo-sub",
        credentials_path=None,
    )

    assert result.status == "ready"
    assert result.exit_code == 0
    assert "READY:" in result.message
    assert CONSUME_PERMISSION in FakeClient.permission_requests[0]["permissions"]
    assert FakeClient.permission_requests[0]["resource"] == "projects/demo-project/subscriptions/demo-sub"


def test_returns_denied_when_consume_permission_is_missing(monkeypatch):
    module = _import_pubsub_readiness(monkeypatch)
    FakeClient.permissions = []

    result = _call_check(
        module,
        project_id="demo-project",
        subscription_id="demo-sub",
        credentials_path=None,
    )

    assert result.status == "denied"
    assert result.exit_code == 2
    assert CONSUME_PERMISSION in result.message


def test_returns_denied_when_permission_probe_raises_permission_denied(monkeypatch):
    module = _import_pubsub_readiness(monkeypatch)
    FakeClient.permission_error = FakePermissionDenied("consume denied")

    result = _call_check(
        module,
        project_id="demo-project",
        subscription_id="demo-sub",
        credentials_path=None,
    )

    assert result.status == "denied"
    assert result.exit_code == 2
    assert "NOT READY:" in result.message


def test_returns_indeterminate_when_probe_fails_without_denial(monkeypatch):
    module = _import_pubsub_readiness(monkeypatch)
    FakeClient.permission_error = FakeGoogleAPICallError("probe unavailable")

    result = _call_check(
        module,
        project_id="demo-project",
        subscription_id="demo-sub",
        credentials_path=None,
    )

    assert result.status == "indeterminate"
    assert result.exit_code == 3
    assert "INDETERMINATE:" in result.message


def test_keeps_success_when_metadata_lookup_fails_but_consume_succeeds(monkeypatch):
    module = _import_pubsub_readiness(monkeypatch)
    FakeClient.permissions = [CONSUME_PERMISSION]
    FakeClient.metadata_error = FakePermissionDenied("metadata hidden")

    result = _call_check(
        module,
        project_id="demo-project",
        subscription_id="demo-sub",
        credentials_path=None,
    )

    assert result.status == "ready"
    assert result.exit_code == 0
    assert any("metadata probe unavailable" in entry.lower() for entry in result.diagnostics)


def test_returns_hard_failure_when_required_config_is_missing(monkeypatch):
    module = _import_pubsub_readiness(monkeypatch)
    monkeypatch.delenv(module.ENV_PROJECT_ID, raising=False)
    monkeypatch.delenv(module.ENV_SUBSCRIPTION_ID, raising=False)
    monkeypatch.delenv(module.ENV_CREDENTIALS_PATH, raising=False)

    result = _call_check(module, project_id=None, subscription_id=None, credentials_path=None)

    assert result.status == "failure"
    assert result.exit_code == 1
    assert "GCP_PROJECT_ID" in result.message
    assert "GCP_PUBSUB_SUBSCRIPTION_ID" in result.message


def test_returns_hard_failure_when_credentials_path_is_not_a_readable_file(monkeypatch, tmp_path):
    module = _import_pubsub_readiness(monkeypatch)
    unreadable_path = tmp_path / "not-a-file"
    unreadable_path.mkdir()

    result = _call_check(
        module,
        project_id="demo-project",
        subscription_id="demo-sub",
        credentials_path=str(unreadable_path),
    )

    assert result.status == "failure"
    assert result.exit_code == 1
    assert "credentials path is not a file" in result.message


def test_returns_hard_failure_when_credentials_parse_or_client_init_fails(monkeypatch, tmp_path):
    credentials_file = tmp_path / "service-account.json"
    credentials_file.write_text("{}", encoding="utf-8")
    module = _import_pubsub_readiness(
        monkeypatch,
        client_cls=RaisingClient,
        credentials_error=ValueError("bad service account json"),
    )

    result = _call_check(
        module,
        project_id="demo-project",
        subscription_id="demo-sub",
        credentials_path=str(credentials_file),
    )

    assert result.status == "failure"
    assert result.exit_code == 1
    assert "unable to load credentials file" in result.message or "unable to create Pub/Sub client" in result.message


def test_returns_hard_failure_when_subscription_resource_is_missing(monkeypatch):
    module = _import_pubsub_readiness(monkeypatch)
    FakeClient.permissions = []
    FakeClient.metadata_error = FakeNotFound("subscription missing")

    result = _call_check(
        module,
        project_id="demo-project",
        subscription_id="demo-sub",
        credentials_path=None,
    )

    assert result.status == "failure"
    assert result.exit_code == 1
    assert "subscription not found" in result.message
