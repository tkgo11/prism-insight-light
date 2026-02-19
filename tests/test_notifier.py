from unittest.mock import patch, MagicMock
from trading.notifier import SlackNotifier, DiscordNotifier

def test_slack_notifier_success():
    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        notifier = SlackNotifier("http://slack.webhook")
        assert notifier.send_message("Test message") is True
        mock_post.assert_called_once()

def test_slack_notifier_failure():
    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 404
        notifier = SlackNotifier("http://slack.webhook")
        assert notifier.send_message("Test message") is False

def test_discord_notifier_payload():
    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 204
        notifier = DiscordNotifier("http://discord.webhook")
        notifier.send_message("Message", title="Title", color="green")
        
        # Verify payload structure
        args, kwargs = mock_post.call_args
        payload = kwargs["json"]
        assert "embeds" in payload
        assert payload["embeds"][0]["title"] == "Title"
        assert payload["embeds"][0]["color"] == 5763719
