"""Official language channels listed in dragon1086/prism-insight's README."""

DEFAULT_TELEGRAM_CHANNEL_URL = "https://t.me/stock_ai_agent"

# These are source links, not a claim that their public history is available.
TELEGRAM_LANGUAGE_CHANNELS = (
    ("ko", "Korean", DEFAULT_TELEGRAM_CHANNEL_URL),
    ("en", "English", "https://t.me/prism_insight_global_en"),
    ("ja", "Japanese", "https://t.me/prism_insight_ja"),
    ("zh", "Chinese", "https://t.me/prism_insight_zh"),
    ("es", "Spanish", "https://t.me/prism_insight_es"),
)
DEFAULT_TELEGRAM_PREVIEW_BACKUPS = tuple(url for code, _, url in TELEGRAM_LANGUAGE_CHANNELS if code != "ko")
