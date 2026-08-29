"""Configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_SYSTEM_PROMPT = """You are John. You are the person your friends and contacts are messaging on Telegram.

Personality:
- Friendly, warm, and conversational
- Use casual language, short sentences, occasional emojis
- You have opinions and preferences
- You ask follow-up questions to keep conversations going
- You're helpful but not overly formal

Rules:
- NEVER reveal you are an AI, bot, or automated system
- NEVER mention 'as an AI', 'I don't have feelings', or similar giveaways
- If you don't know something, say 'I'm not sure' or 'Let me think' like a real person would
- Keep responses natural — sometimes short (1-2 sentences), sometimes longer if needed
- Match the energy and tone of the person messaging you
- If someone asks something very personal you shouldn't know, deflect naturally

You ARE John. Chat naturally."""


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _env(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.environ.get(name, default)
    if required and not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


def _resolve_webhook_url() -> str | None:
    explicit = os.environ.get("WEBHOOK_URL")
    if explicit:
        return explicit.rstrip("/")
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    if domain:
        return f"https://{domain}"
    return None


@dataclass
class Config:
    telegram_bot_token: str
    telegram_webhook_secret: str

    ai_provider: str
    openai_api_key: str | None
    anthropic_api_key: str | None
    openai_model: str
    anthropic_model: str

    owner_telegram_user_id: int | None
    webhook_url: str | None
    port: int
    system_prompt: str
    max_history_per_chat: int

    @classmethod
    def from_env(cls) -> "Config":
        token = _env("TELEGRAM_BOT_TOKEN", required=True)
        secret = _env("TELEGRAM_WEBHOOK_SECRET", required=True)
        assert token and secret

        if len(secret) < 20:
            raise ConfigError(
                "TELEGRAM_WEBHOOK_SECRET must be at least 20 characters long "
                "(Telegram requires this for the X-Telegram-Bot-Api-Secret-Token header)"
            )

        provider = (_env("AI_PROVIDER", "openai") or "openai").strip().lower()
        if provider not in ("openai", "anthropic"):
            raise ConfigError(f"AI_PROVIDER must be 'openai' or 'anthropic', got {provider!r}")

        openai_key = _env("OPENAI_API_KEY")
        anthropic_key = _env("ANTHROPIC_API_KEY")

        if provider == "openai" and not openai_key:
            raise ConfigError("OPENAI_API_KEY is required when AI_PROVIDER=openai")
        if provider == "anthropic" and not anthropic_key:
            raise ConfigError("ANTHROPIC_API_KEY is required when AI_PROVIDER=anthropic")

        owner_id_raw = _env("OWNER_TELEGRAM_USER_ID")
        owner_id = int(owner_id_raw) if owner_id_raw else None

        return cls(
            telegram_bot_token=token,
            telegram_webhook_secret=secret,
            ai_provider=provider,
            openai_api_key=openai_key,
            anthropic_api_key=anthropic_key,
            openai_model=_env("OPENAI_MODEL", "gpt-4o-mini") or "gpt-4o-mini",
            anthropic_model=_env("ANTHROPIC_MODEL", "claude-sonnet-5") or "claude-sonnet-5",
            owner_telegram_user_id=owner_id,
            webhook_url=_resolve_webhook_url(),
            port=_env_int("PORT", 8000),
            system_prompt=_env("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT) or DEFAULT_SYSTEM_PROMPT,
            max_history_per_chat=_env_int("MAX_HISTORY_PER_CHAT", 20),
        )
