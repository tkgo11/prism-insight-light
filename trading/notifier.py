import logging
import requests
import json
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class BaseNotifier(ABC):
    """Abstract base class for notifiers."""
    
    @abstractmethod
    def send_message(self, message: str, title: str = None, color: str = None) -> bool:
        pass

class SlackNotifier(BaseNotifier):
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send_message(self, message: str, title: str = None, color: str = None) -> bool:
        if not self.webhook_url: return False
        
        payload = {
            "text": f"*{title}*\n{message}" if title else message
        }
        
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=5)
            if resp.status_code != 200:
                logger.error(f"Slack notification failed: {resp.status_code} {resp.text}")
                return False
            return True
        except Exception as e:
            logger.error(f"Slack notification error: {e}")
            return False

class DiscordNotifier(BaseNotifier):
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send_message(self, message: str, title: str = None, color: str = None) -> bool:
        if not self.webhook_url: return False

        # Map color names to integer (decimal) color codes for Discord embeds
        color_map = {
            "green": 5763719,  # Success
            "red": 15548997,   # Error
            "blue": 3447003,   # Info
            "yellow": 16776960 # Warning
        }
        discord_color = color_map.get(color, 3447003)

        payload = {
            "embeds": [{
                "title": title or "Notification",
                "description": message,
                "color": discord_color
            }]
        }
        
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=5)
            if resp.status_code not in (200, 204):
                logger.error(f"Discord notification failed: {resp.status_code} {resp.text}")
                return False
            return True
        except Exception as e:
            logger.error(f"Discord notification error: {e}")
            return False

class NotifierManager:
    def __init__(self, slack_webhook: str = None, discord_webhook: str = None):
        self.notifiers = []
        if slack_webhook:
            self.notifiers.append(SlackNotifier(slack_webhook))
        if discord_webhook:
            self.notifiers.append(DiscordNotifier(discord_webhook))
            
    def send(self, message: str, title: str = "PRISM INSIGHT", color: str = "blue"):
        for notifier in self.notifiers:
            notifier.send_message(message, title, color)
