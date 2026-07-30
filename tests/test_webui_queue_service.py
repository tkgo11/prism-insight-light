import json

from webui.services.queue_service import summarize_queue


def test_summarize_missing_queue_is_safe(tmp_path):
    result = summarize_queue(tmp_path / "missing.json")
    assert result == {
        "ok": True,
        "path_label": "missing.json",
        "count": 0,
        "displayed_count": 0,
        "pending_count": 0,
        "failed_count": 0,
        "truncated": False,
        "items": [],
        "error": None,
    }


def test_summarize_queue_returns_read_only_safe_fields(tmp_path):
    queue = tmp_path / "queue.json"
    queue.write_text('[{"execute_at":"2026-06-07T09:00:00+09:00","created_at":"now","signal":{"type":"BUY","market":"KR","ticker":"005930","company_name":"Samsung"}}]', encoding="utf-8")
    result = summarize_queue(queue)
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["items"][0]["ticker"] == "005930"
    assert result["items"][0]["signal_type"] == "BUY"


def test_summarize_queue_reports_quarantined_failures_without_secret_leak(tmp_path):
    queue = tmp_path / "queue.json"
    queue.write_text(
        '[{"execute_at":"2026-06-07T09:00:00+09:00","created_at":"now",'
        '"status":"failed","failure_message":"Bearer abcdefghijklmnopqrstuvwxyz123456",'
        '"signal":{"type":"BUY","market":"KR","ticker":"005930"}}]',
        encoding="utf-8",
    )

    result = summarize_queue(queue)

    assert result["pending_count"] == 0
    assert result["failed_count"] == 1
    assert "abcdefghijklmnopqrstuvwxyz123456" not in result["items"][0]["failure_message"]
    assert "execute" not in result


def test_summarize_malformed_queue_safe_error(tmp_path):
    queue = tmp_path / "queue.json"
    queue.write_text('{bad json', encoding="utf-8")
    result = summarize_queue(queue)
    assert result["ok"] is False
    assert result["pending_count"] == 0
    assert result["failed_count"] == 0
    assert "Traceback" not in result["error"]


def test_summarize_queue_bounds_displayed_items(tmp_path):
    queue = tmp_path / "queue.json"
    queue.write_text(
        json.dumps(
            [
                {
                    "execute_at": str(index),
                    "signal": {"type": "BUY", "ticker": str(index)},
                }
                for index in range(501)
            ]
        ),
        encoding="utf-8",
    )

    result = summarize_queue(queue)

    assert result["count"] == 501
    assert result["displayed_count"] == 500
    assert result["pending_count"] == 501
    assert result["truncated"] is True


def test_summarize_queue_rejects_oversized_file(tmp_path):
    queue = tmp_path / "queue.json"
    queue.write_bytes(b"[" + b" " * (1024 * 1024) + b"]")

    result = summarize_queue(queue)

    assert result["ok"] is False
    assert "display limit" in result["error"]
    assert result["items"] == []
