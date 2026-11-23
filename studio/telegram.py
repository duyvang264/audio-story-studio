"""Telegram Bot API client (send-only + polling helpers). Dry-run writes to an outbox file."""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from .config import Settings, get_settings

API = "https://api.telegram.org"


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        self.enabled = bool(self.s.telegram_bot_token) and self.s.telegram_mode != "disabled"
        self.dry_run = self.s.telegram_dry_run or not self.enabled
        self.outbox = self.s.storage / "logs" / "telegram_outbox.jsonl"

    def _record(self, method: str, payload: dict) -> dict:
        self.outbox.parent.mkdir(parents=True, exist_ok=True)
        with open(self.outbox, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "method": method, **payload}, ensure_ascii=False) + "\n")
        return {"ok": True, "dry_run": True}

    def _call(self, method: str, data: dict, files: dict | None = None, timeout: float = 60) -> dict:
        if self.dry_run:
            rec = dict(data)
            if files:
                rec["files"] = {k: v[0] for k, v in files.items()}
            return self._record(method, rec)
        url = f"{API}/bot{self.s.telegram_bot_token}/{method}"
        for attempt in range(3):
            try:
                if files:
                    r = httpx.post(url, data=data, files=files, timeout=timeout)
                else:
                    r = httpx.post(url, json=data, timeout=timeout)
                body = r.json()
                if body.get("ok"):
                    return body
                if r.status_code == 429:
                    time.sleep(body.get("parameters", {}).get("retry_after", 3))
                    continue
                raise TelegramError(f"{method}: {body.get('description')}")
            except httpx.HTTPError as e:
                if attempt == 2:
                    raise TelegramError(f"{method}: {type(e).__name__}") from e
                time.sleep(2 ** attempt)
        raise TelegramError(f"{method}: retries exhausted")

    def send_message(self, chat_id: int | str, text: str, reply_markup: dict | None = None) -> dict:
        data = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
        if reply_markup:
            data["reply_markup"] = reply_markup
        return self._call("sendMessage", data)

    def send_video(self, chat_id: int | str, path: Path, caption: str = "", reply_markup: dict | None = None) -> dict:
        data = {"chat_id": str(chat_id), "caption": caption[:1000], "supports_streaming": "true"}
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        if self.dry_run:
            return self._call("sendVideo", data, files={"video": (str(path), None)})
        with open(path, "rb") as f:
            return self._call("sendVideo", data, files={"video": (path.name, f, "video/mp4")}, timeout=600)

    def send_photo(self, chat_id: int | str, path: Path, caption: str = "", reply_markup: dict | None = None) -> dict:
        data = {"chat_id": str(chat_id), "caption": caption[:1000]}
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        if self.dry_run:
            return self._call("sendPhoto", data, files={"photo": (str(path), None)})
        with open(path, "rb") as f:
            return self._call("sendPhoto", data, files={"photo": (path.name, f, "image/jpeg")}, timeout=120)

    def answer_callback(self, callback_id: str, text: str = "") -> dict:
        return self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:190]})

    def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict]:
        if not self.enabled:
            raise TelegramError("TELEGRAM_BOT_TOKEN not configured")
        url = f"{API}/bot{self.s.telegram_bot_token}/getUpdates"
        try:
            r = httpx.post(url, json={"offset": offset, "timeout": timeout,
                                      "allowed_updates": ["message", "callback_query"]}, timeout=timeout + 10)
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise TelegramError(f"getUpdates: {type(e).__name__}") from e
        if not body.get("ok"):
            raise TelegramError(body.get("description"))
        return body["result"]

    def download_document(self, file_id: str, dest: Path, max_bytes: int = 2_000_000) -> Path:
        info = self._call("getFile", {"file_id": file_id})
        if info.get("dry_run"):
            raise TelegramError("cannot download in dry-run mode")
        meta = info["result"]
        if meta.get("file_size", 0) > max_bytes:
            raise TelegramError("file too large")
        r = httpx.get(f"{API}/file/bot{self.s.telegram_bot_token}/{meta['file_path']}", timeout=120)
        r.raise_for_status()
        dest.write_bytes(r.content[:max_bytes])
        return dest
