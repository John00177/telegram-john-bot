"""FastAPI webhook server for the Telegram Business auto-reply bot."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from telegram import Update

from ai_client import AIClient, AIClientError
from config import Config, ConfigError
from handlers import MessageHandler, OwnerRegistry
from memory import ConversationMemory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("main")

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"

# Populated during the lifespan startup; read by the /webhook and / routes.
state: dict = {}


async def _telegram_api_call(token: str, method: str, payload: dict | None = None) -> dict:
    url = f"{TELEGRAM_API_BASE.format(token=token)}/{method}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, json=payload or {})
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API call {method} failed: {data}")
    return data["result"]


async def _register_webhook(config: Config) -> None:
    if not config.webhook_url:
        logger.warning(
            "No WEBHOOK_URL configured and RAILWAY_PUBLIC_DOMAIN not set — "
            "skipping webhook registration. Set WEBHOOK_URL manually."
        )
        return

    webhook_endpoint = f"{config.webhook_url}/webhook"
    result = await _telegram_api_call(
        config.telegram_bot_token,
        "setWebhook",
        {
            "url": webhook_endpoint,
            "secret_token": config.telegram_webhook_secret,
            # Business updates are NOT delivered unless explicitly requested.
            "allowed_updates": ["business_message", "message"],
        },
    )
    logger.info("Webhook registered at %s (result=%s)", webhook_endpoint, result)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        config = Config.from_env()
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        raise

    me = await _telegram_api_call(config.telegram_bot_token, "getMe")
    logger.info("Authenticated as @%s (id=%s)", me.get("username"), me.get("id"))

    await _register_webhook(config)

    owner_registry = OwnerRegistry(config.telegram_bot_token, config.owner_telegram_user_id)
    memory = ConversationMemory(max_history_per_chat=config.max_history_per_chat)
    ai_client = AIClient(config)
    message_handler = MessageHandler(bot_id=me["id"], owner_registry=owner_registry)

    state["config"] = config
    state["bot_info"] = me
    state["owner_registry"] = owner_registry
    state["memory"] = memory
    state["ai_client"] = ai_client
    state["message_handler"] = message_handler

    yield

    state.clear()


app = FastAPI(title="Telegram Business Auto-Reply Bot", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def status_page():
    bot_info = state.get("bot_info", {})
    config: Config | None = state.get("config")
    memory: ConversationMemory | None = state.get("memory")
    return JSONResponse(
        {
            "status": "running",
            "bot_username": bot_info.get("username"),
            "bot_id": bot_info.get("id"),
            "ai_provider": config.ai_provider if config else None,
            "webhook_url": config.webhook_url if config else None,
            "active_chats": memory.chat_count() if memory else 0,
        }
    )


@app.post("/webhook")
async def webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    config: Config = state["config"]

    if x_telegram_bot_api_secret_token != config.telegram_webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid secret token")

    payload = await request.json()
    update = Update.de_json(payload, bot=None)
    if update is None:
        return {"ok": True}

    message_handler: MessageHandler = state["message_handler"]
    incoming = await message_handler.should_reply(update)
    if incoming is None:
        return {"ok": True}

    memory: ConversationMemory = state["memory"]
    ai_client: AIClient = state["ai_client"]
    history = memory.get_history(incoming.chat_id)

    try:
        reply_text = await ai_client.generate_reply(history, incoming.text)
    except AIClientError:
        logger.exception("AI generation failed for chat_id=%s", incoming.chat_id)
        return {"ok": True}

    send_payload: dict = {"chat_id": incoming.chat_id, "text": reply_text}
    if incoming.business_connection_id:
        send_payload["business_connection_id"] = incoming.business_connection_id

    try:
        await _telegram_api_call(config.telegram_bot_token, "sendMessage", send_payload)
    except Exception:
        logger.exception("Failed to send reply for chat_id=%s", incoming.chat_id)
        return {"ok": True}

    memory.add_exchange(incoming.chat_id, incoming.text, reply_text)
    return {"ok": True}
