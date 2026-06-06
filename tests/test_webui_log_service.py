from pathlib import Path

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
