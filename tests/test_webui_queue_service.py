from webui.services.queue_service import summarize_queue


def test_summarize_missing_queue_is_safe(tmp_path):
    result = summarize_queue(tmp_path / "missing.json")
    assert result == {"ok": True, "path_label": "missing.json", "count": 0, "items": [], "error": None}


def test_summarize_queue_returns_read_only_safe_fields(tmp_path):
    queue = tmp_path / "queue.json"
    queue.write_text('[{"execute_at":"2026-06-07T09:00:00+09:00","created_at":"now","signal":{"type":"BUY","market":"KR","ticker":"005930","company_name":"Samsung"}}]', encoding="utf-8")
    result = summarize_queue(queue)
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["items"][0]["ticker"] == "005930"
    assert "execute" not in result


def test_summarize_malformed_queue_safe_error(tmp_path):
    queue = tmp_path / "queue.json"
    queue.write_text('{bad json', encoding="utf-8")
    result = summarize_queue(queue)
    assert result["ok"] is False
    assert "Traceback" not in result["error"]
