"""Message handling: loop protection + AI reply dispatch.

LOOP PROTECTION IS NON-NEGOTIABLE. A Telegram Business connection delivers
EVERY message in a connected chat to the webhook — including the bot's own
outgoing replies (sent via sendMessage on behalf of the business account),
the owner's own outgoing messages typed from their phone, and messages from
other bots in the same chat. If any of those are treated as "a message to
reply to", the bot will reply to itself forever.

should_reply() is the single choke point all of that filtering runs through.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from telegram import Update

logger = logging.getLogger("handlers")

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"


@dataclass
class IncomingMessage:
    chat_id: int
    text: str
    business_connection_id: str | None
    sender_user_id: int | None


class OwnerRegistry:
    """Tracks the owner's Telegram user id per business_connection_id.

    Recovered from getBusinessConnection on first contact after a restart,
    since the in-memory cache does not survive a process restart and there
    is no database.
    """

    def __init__(self, bot_token: str, configured_owner_id: int | None):
        self._bot_token = bot_token
        self._configured_owner_id = configured_owner_id
        self._owner_by_connection: dict[str, int] = {}

    async def get_owner_id(self, business_connection_id: str | None) -> int | None:
        if self._configured_owner_id is not None:
            return self._configured_owner_id
        if business_connection_id is None:
            return None
        if business_connection_id in self._owner_by_connection:
            return self._owner_by_connection[business_connection_id]
        return await self._recover_from_telegram(business_connection_id)

    async def _recover_from_telegram(self, business_connection_id: str) -> int | None:
        url = f"{TELEGRAM_API_BASE.format(token=self._bot_token)}/getBusinessConnection"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json={"business_connection_id": business_connection_id})
            data = resp.json()
        except Exception:
            logger.exception("Failed to recover owner id from getBusinessConnection")
            return None

        if not data.get("ok"):
            logger.warning("getBusinessConnection failed: %s", data)
            return None

        result = data["result"]
        owner_id = result.get("user", {}).get("id")
        can_reply = result.get("can_reply")
        logger.info(
            "Recovered business connection %s: owner_id=%s can_reply=%s",
            business_connection_id, owner_id, can_reply,
        )
        if owner_id is not None:
            self._owner_by_connection[business_connection_id] = owner_id
        return owner_id

    def remember(self, business_connection_id: str, owner_id: int) -> None:
        self._owner_by_connection[business_connection_id] = owner_id


class MessageHandler:
    def __init__(self, bot_id: int, owner_registry: OwnerRegistry):
        self._bot_id = bot_id
        self._owner_registry = owner_registry

    async def should_reply(self, update: Update) -> IncomingMessage | None:
        """Returns the IncomingMessage to reply to, or None if this update
        must be ignored to avoid a reply loop or because it's not a text
        message from a genuine business contact.
        """
        message = update.business_message or update.message
        if message is None:
            return None

        if message.text is None:
            return None  # only handle plain text for now (stickers/photos/etc. skipped)

        chat_id = message.chat_id
        sender = message.from_user
        sender_id = sender.id if sender else None

        # 1. Never reply to ourselves.
        if sender_id == self._bot_id:
            return None

        # 2. sender.id == chat.id: in a private chat this identifies the
        #    *other* party normally, but Telegram Business also echoes the
        #    connected account's own outgoing messages with sender == the
        #    business account's user, which in a 1:1 chat equals chat_id
        #    only when the business owner IS the chat partner (impossible)
        #    — the real heuristic is: if sender_id == chat_id, the message
        #    was authored by the channel/business account itself, not the
        #    remote party, so skip it.
        if sender_id is not None and sender_id == chat_id:
            return None

        # 3. sender_business_bot flag — set when the message was sent by a
        #    bot operating on behalf of the business account (e.g. us).
        is_business_bot = getattr(message, "sender_business_bot", None)
        if is_business_bot:
            return None

        # 4. Never treat the owner's own outgoing messages (typed by hand
        #    from their phone/desktop) as something to reply to.
        business_connection_id = getattr(message, "business_connection_id", None)
        owner_id = await self._owner_registry.get_owner_id(business_connection_id)
        if owner_id is not None and sender_id == owner_id:
            return None

        # 5. Ignore other bots entirely.
        if sender is not None and sender.is_bot:
            return None

        return IncomingMessage(
            chat_id=chat_id,
            text=message.text,
            business_connection_id=business_connection_id,
            sender_user_id=sender_id,
        )
