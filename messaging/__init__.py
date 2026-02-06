"""
PRISM-INSIGHT Messaging Module

Redis Streams-based trading signal publishing module.
"""
from messaging.redis_signal_publisher import (
    SignalPublisher,
    get_signal_publisher,
    publish_buy_signal,
    publish_sell_signal,
)

__all__ = [
    "SignalPublisher",
    "get_signal_publisher",
    "publish_buy_signal",
    "publish_sell_signal",
]
