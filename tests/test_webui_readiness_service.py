from webui.services.readiness_service import get_config_status, get_readiness_summary


def test_config_status_is_allowlisted_and_masked():
    env = {
        "GCP_PROJECT_ID": "project-123",
        "GCP_PUBSUB_SUBSCRIPTION_ID": "sub-123",
        "GCP_CREDENTIALS_PATH": "C:/very/secret/service-account.json",
    }
    status = get_config_status(env)
    rendered = repr(status)
    assert status["status"] == "ready"
    assert "project-123" in rendered
    assert "sub-123" in rendered
    assert "C:/very/secret" not in rendered


def test_readiness_summary_static_does_not_run_live_check(monkeypatch):
    import pubsub_readiness

    def fail_live(*args, **kwargs):  # pragma: no cover - should never be called
        raise AssertionError("live Pub/Sub check called")

    monkeypatch.setattr(pubsub_readiness, "check_pubsub_readiness", fail_live)
    result = get_readiness_summary(run_live_check=False)
    assert "live Pub/Sub check not requested" in result["message"]
