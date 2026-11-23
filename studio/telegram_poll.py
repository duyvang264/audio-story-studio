"""Long-polling Telegram runner (TELEGRAM_MODE=polling) — no public HTTPS URL required.

Do not run this while an n8n Telegram Trigger webhook is registered for the same bot.
"""
from __future__ import annotations

import logging
import time

from .commands import handle_update
from .config import ensure_storage, get_settings
from .telegram import TelegramClient, TelegramError

log = logging.getLogger("studio.telegram")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Telegram API URLs contain the bot token; do not log HTTP request URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    s = get_settings()
    ensure_storage(s)
    if s.telegram_mode != "polling":
        raise SystemExit(f"TELEGRAM_MODE={s.telegram_mode}; polling runner disabled")
    if not s.allowed_user_ids:
        raise SystemExit("TELEGRAM_ALLOWED_USER_IDS is empty; refusing to start")
    tg = TelegramClient(s)
    offset = None
    log.info("polling Telegram for %d allowed users", len(s.allowed_user_ids))
    while True:
        try:
            for upd in tg.get_updates(offset):
                offset = upd["update_id"] + 1
                try:
                    handle_update(upd, s, tg)
                except Exception:  # noqa: BLE001 — one bad update must not stop the bot
                    log.exception("update %s failed", upd.get("update_id"))
        except TelegramError as e:
            log.warning("getUpdates failed: %s", e)
            time.sleep(5)
        except Exception:  # noqa: BLE001 — the bot must survive anything and keep polling
            log.exception("polling loop error")
            time.sleep(10)


if __name__ == "__main__":
    main()
