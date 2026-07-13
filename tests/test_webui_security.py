from webui.services.masking import allowlisted_env_status, mask_secret_value, mask_text


def test_mask_secret_value_keeps_only_edges():
    raw = "abcd1234567890wxyz"
    masked = mask_secret_value(raw)
    assert masked.startswith("abcd")
    assert masked.endswith("wxyz")
    assert "1234567890" not in masked


def test_mask_text_removes_representative_kis_and_gcp_secrets():
    private_key = "-----BEGIN PRIVATE KEY-----\nABCDEF1234567890SECRET\n-----END PRIVATE KEY-----"
    kis_secret = "KIS_APP_SECRET=super-secret-value-1234567890"
    token = "Bearer abcdefghijklmnopqrstuvwxyz123456"
    account = "12345678-90"
    body = f'{private_key}\n{kis_secret}\n{token}\naccount={account}\n"private_key_id":"private-key-id-1234567890"'

    masked = mask_text(body)

    assert "ABCDEF1234567890SECRET" not in masked
    assert "super-secret-value-1234567890" not in masked
    assert "abcdefghijklmnopqrstuvwxyz123456" not in masked
    assert account not in masked
    assert "private-key-id-1234567890" not in masked
    assert "[MASKED" in masked


def test_allowlisted_env_status_does_not_return_raw_sensitive_path_or_token():
    env = {
        "GCP_PROJECT_ID": "safe-project",
        "GCP_PUBSUB_SUBSCRIPTION_ID": "safe-subscription",
        "GCP_CREDENTIALS_PATH": "C:/secrets/service-account.json",
        "WEBUI_HOST": "127.0.0.1",
    }
    items = allowlisted_env_status(env)
    rendered = repr(items)
    assert "safe-project" in rendered
    assert "safe-subscription" in rendered
    assert "C:/secrets" not in rendered
    assert "service-account.json" in rendered


def test_mask_text_removes_structured_app_keys_and_passwords():
    body = '{"app_key":"fake-app-key-value-1234567890","password":"fake-password-value-1234567890"}'
    masked = mask_text(body)
    assert "fake-app-key-value-1234567890" not in masked
    assert "fake-password-value-1234567890" not in masked


def test_mask_text_removes_yaml_secret_values():
    body = "app_secret: abc$123\npassword: hunter2\nmy_app: short\n"
    masked = mask_text(body)
    assert "abc$123" not in masked
    assert "hunter2" not in masked
    assert "short" not in masked
