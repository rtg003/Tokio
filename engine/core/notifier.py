"""Notification channel (Telegram). Failures are logged, never raised into
the hot path. Tokens come from the environment and are never logged."""
from __future__ import annotations

import os
from typing import Any


class Notifier:
    def __init__(self, logger: Any | None = None) -> None:
        self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.logger = logger

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.configured:
            if self.logger:
                self.logger.debug("notify.skipped_unconfigured", {"text": text[:200]})
            return False
        try:
            import httpx

            resp = httpx.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=10.0,
            )
            resp.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001 — notifications must never crash the engine
            if self.logger:
                self.logger.warning("notify.failed", {"error": str(exc)[:200]})
            return False
