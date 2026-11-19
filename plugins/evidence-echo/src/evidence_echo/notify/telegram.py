"""Telegram channel: sendMessage (HTML) and sendDocument over the Bot API."""
from pathlib import Path

import httpx

from evidence_echo.notify import NotifyError

API = "https://api.telegram.org"
NO_TOKEN = ("Telegram bot token missing: set it in the plugin settings "
            "(Evidence Echo → Telegram bot token)")


def _redact(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def _call(client, token: str, method: str, **kwargs) -> dict:
    try:
        resp = client.post(f"{API}/bot{token}/{method}", **kwargs)
    except httpx.HTTPError as exc:
        raise NotifyError(f"Telegram {method} failed: {_redact(str(exc), token)}") from None
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code != 200 or not body.get("ok"):
        why = body.get("description") or f"HTTP {resp.status_code}"
        raise NotifyError(f"Telegram {method} failed: {_redact(str(why), token)}")
    return body


def send(token: str, chat_id: str, text_html: str, attachment: Path | None = None, client=None) -> None:
    """The message, then the attachment (when given and readable) as a document."""
    if not token:
        raise NotifyError(NO_TOKEN)
    own = client is None
    client = client or httpx.Client(timeout=30)
    try:
        _call(client, token, "sendMessage", data={
            "chat_id": chat_id, "text": text_html, "parse_mode": "HTML",
            "disable_web_page_preview": "true"})
        if attachment is not None and Path(attachment).is_file():
            path = Path(attachment)
            _call(client, token, "sendDocument", data={"chat_id": chat_id},
                  files={"document": (path.name, path.read_bytes(), "text/html")})
    finally:
        if own:
            client.close()


def chats(token: str, client=None) -> list[dict]:
    """Private chats that wrote to the bot lately: [{"id", "name"}]."""
    if not token:
        raise NotifyError(NO_TOKEN)
    own = client is None
    client = client or httpx.Client(timeout=30)
    try:
        body = _call(client, token, "getUpdates", data={"limit": 100})
    finally:
        if own:
            client.close()
    seen = {}
    for update in body.get("result", []):
        chat = (update.get("message") or {}).get("chat") or {}
        if chat.get("type") == "private" and chat.get("id") is not None:
            name = " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")])) \
                or chat.get("username") or str(chat["id"])
            seen[chat["id"]] = {"id": chat["id"], "name": name}
    return list(seen.values())
