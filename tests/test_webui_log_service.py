
from webui.services import log_service
from webui.services.log_service import tail_log


def test_tail_log_rejects_unknown_log_name():
    result = tail_log("../../secret")
    assert result["ok"] is False
    assert result["lines"] == []


def test_tail_log_bounds_and_masks(monkeypatch, tmp_path):
    log_path = tmp_path / "subscriber.log"
    secret = "KIS_APP_SECRET=super-secret-value-1234567890"
    log_path.write_text("safe line\n" + secret + "\n<script>alert(1)</script>\n", encoding="utf-8")
    monkeypatch.setattr(log_service, "_ALLOWED_LOGS", {"test": log_path})

    result = tail_log("test", max_lines=2, max_bytes=10_000)

    assert result["ok"] is True
    rendered = "\n".join(result["lines"])
    assert "super-secret-value-1234567890" not in rendered
    assert "safe line" not in rendered  # bounded to last 2 lines
    assert "<script>alert(1)</script>" in rendered  # template layer escapes it


def test_tail_log_masks_complete_and_open_private_key_blocks(monkeypatch, tmp_path):
    path = tmp_path / "subscriber.log"
    monkeypatch.setattr(log_service, "_ALLOWED_LOGS", {"test": path})
    for end in ("-----END PRIVATE KEY-----\n", ""):
        path.write_text("safe line\n-----BEGIN PRIVATE KEY-----\nabc+/123\nshortkey\n" + end)
        result = tail_log("test")
        rendered = "\n".join(result["lines"])
        assert "safe line" in rendered
        assert "abc+/123" not in rendered
        assert "shortkey" not in rendered


def test_tail_log_masks_private_key_when_byte_or_line_window_cuts_begin(monkeypatch, tmp_path):
    path = tmp_path / "subscriber.log"
    monkeypatch.setattr(log_service, "_ALLOWED_LOGS", {"test": path})
    path.write_text("-----BEGIN RSA PRIVATE KEY-----\nabc+/123\nshortkey\n-----END RSA PRIVATE KEY-----\nsafe line\n")
    for max_bytes, max_lines in [(10_000, 3), (65, 100)]:
        result = tail_log("test", max_bytes=max_bytes, max_lines=max_lines)
        rendered = "\n".join(result["lines"])
        assert "safe line" in rendered
        assert "abc+/123" not in rendered
        assert "shortkey" not in rendered


def test_tail_log_masks_window_entirely_inside_open_private_key(monkeypatch, tmp_path):
    path = tmp_path / "subscriber.log"
    monkeypatch.setattr(log_service, "_ALLOWED_LOGS", {"test": path})
    path.write_text("-----BEGIN PRIVATE KEY-----\n" + "abc+/123\n" * 30 + "shortkey\n")
    rendered = "\n".join(tail_log("test", max_bytes=40)["lines"])
    assert "abc+/123" not in rendered
    assert "shortkey" not in rendered
