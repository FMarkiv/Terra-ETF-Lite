"""Telegram delivery (ported from the full tracker, lite-adapted).

Credentials are read **env-first, then a local YAML**:

* In **CI** (GitHub Actions) the bot token / chat id come from the
  ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID`` secrets (env vars) — nothing is
  ever written into the public repo.
* On the **desktop** they come from ``config/telegram.yaml`` (git-ignored).

Env wins when both are present. ``python-telegram-bot`` is imported lazily, so
formatting/preview works with no library and no creds configured.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import yaml

from .formatter import DEFAULTS, format_alert

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "telegram.yaml"

_PLACEHOLDERS = {"", "YOUR_BOT_TOKEN", "YOUR_CHAT_ID", None}
_HARD_LIMIT = 4096
_RETRY_DELAY = 5


class TelegramConfigError(RuntimeError):
    """Raised for a missing/placeholder bot token or chat id."""


class TelegramSender:
    def __init__(self, config_path: str | Path = DEFAULT_CONFIG_PATH, overrides: dict | None = None):
        self.config_path = Path(config_path)
        self.config = self._load_config(overrides or {})

    def _load_config(self, overrides: dict) -> dict:
        cfg = dict(DEFAULTS)
        if self.config_path.exists():
            cfg.update(yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {})
        # Env vars (CI secrets) take precedence over any file values.
        if os.environ.get("TELEGRAM_BOT_TOKEN"):
            cfg["bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
        if os.environ.get("TELEGRAM_CHAT_ID"):
            cfg["chat_id"] = os.environ["TELEGRAM_CHAT_ID"]
        cfg.update(overrides)
        return cfg

    def _require_credentials(self) -> tuple[str, str]:
        token = str(self.config.get("bot_token") or "").strip()
        chat_id = str(self.config.get("chat_id") or "").strip()
        missing = []
        if token in _PLACEHOLDERS:
            missing.append("bot_token / TELEGRAM_BOT_TOKEN")
        if chat_id in _PLACEHOLDERS:
            missing.append("chat_id / TELEGRAM_CHAT_ID")
        if missing:
            raise TelegramConfigError(
                f"Telegram {' and '.join(missing)} not set. Provide them as env "
                f"vars (CI secrets) or in {self.config_path} (desktop)."
            )
        return token, chat_id

    def format_message(self, delta_result) -> str:
        return format_alert(delta_result, self.config)

    async def send_delta_alert(self, delta_result) -> bool:
        return await self.send_raw_message(self.format_message(delta_result))

    async def send_raw_message(self, text: str) -> bool:
        token, chat_id = self._require_credentials()
        parse_mode = "HTML" if self.config.get("include_monospace_blocks", True) else None
        try:
            from telegram import Bot
            from telegram.error import TelegramError
        except ImportError as exc:  # pragma: no cover
            raise TelegramConfigError(
                "python-telegram-bot is not installed. `pip install python-telegram-bot`."
            ) from exc

        bot = Bot(token=token)
        chunks = _split_message(text, _HARD_LIMIT)
        for i, chunk in enumerate(chunks, 1):
            if not await self._send_one(bot, TelegramError, chat_id, chunk, parse_mode):
                logger.error("Telegram send failed on chunk %d/%d", i, len(chunks))
                return False
        logger.info("Telegram alert sent (%d message(s)) to chat %s", len(chunks), chat_id)
        return True

    async def _send_one(self, bot, TelegramError, chat_id, text, parse_mode) -> bool:
        for attempt in (1, 2):
            try:
                await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
                return True
            except TelegramError as exc:
                logger.warning("Telegram send attempt %d failed: %s", attempt, exc)
                if attempt == 1:
                    await asyncio.sleep(_RETRY_DELAY)
        return False

    def send_delta_alert_sync(self, delta_result) -> bool:
        return asyncio.run(self.send_delta_alert(delta_result))


def _split_message(text: str, limit: int) -> list[str]:
    """Split into <= ``limit`` chunks on blank-line boundaries, keeping <pre>
    blocks balanced per chunk (Telegram parses each message independently)."""
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for section in text.split("\n\n"):
        piece = (cur + "\n\n" + section) if cur else section
        if len(piece) <= limit:
            cur = piece
            continue
        if cur:
            chunks.append(cur)
            cur = ""
        if len(section) <= limit:
            cur = section
        else:
            line_buf = ""
            for line in section.split("\n"):
                lp = (line_buf + "\n" + line) if line_buf else line
                if len(lp) <= limit:
                    line_buf = lp
                else:
                    if line_buf:
                        chunks.append(line_buf)
                    line_buf = line[:limit]
            cur = line_buf
    if cur:
        chunks.append(cur)
    return _balance_pre(chunks)


def _balance_pre(chunks: list[str]) -> list[str]:
    out, carry_open = [], False
    for chunk in chunks:
        if carry_open:
            chunk = "<pre>\n" + chunk
        carry_open = chunk.count("<pre>") > chunk.count("</pre>")
        if carry_open:
            chunk = chunk + "\n</pre>"
        out.append(chunk)
    return out
