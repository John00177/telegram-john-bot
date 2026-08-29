"""Unified AI client using raw HTTP (httpx) — no provider SDKs.

OpenAI: uses the Responses API (POST /v1/responses). The response JSON must be
walked manually — `output_text` is a convenience property added by the OpenAI
Python SDK, it does not exist in the raw JSON payload.

Anthropic: uses the Messages API (POST /v1/messages) directly.
"""
from __future__ import annotations

import logging
from typing import Iterable

import httpx

from config import Config
from memory import Turn

logger = logging.getLogger("ai_client")

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# o-series reasoning models reject the `temperature` param entirely.
_NO_TEMPERATURE_PREFIXES = ("o1", "o3", "o4")


def _openai_supports_temperature(model: str) -> bool:
    return not model.startswith(_NO_TEMPERATURE_PREFIXES)


class AIClientError(RuntimeError):
    """Raised when the AI provider returns an error or an unparsable response."""


class AIClient:
    def __init__(self, config: Config):
        self._config = config

    async def generate_reply(self, history: Iterable[Turn], user_message: str) -> str:
        if self._config.ai_provider == "openai":
            return await self._generate_openai(history, user_message)
        return await self._generate_anthropic(history, user_message)

    async def _generate_openai(self, history: Iterable[Turn], user_message: str) -> str:
        model = self._config.openai_model
        input_items = [{"role": turn.role, "content": turn.content} for turn in history]
        input_items.append({"role": "user", "content": user_message})

        payload: dict = {
            "model": model,
            "instructions": self._config.system_prompt,
            "input": input_items,
        }
        if _openai_supports_temperature(model):
            payload["temperature"] = 0.9

        headers = {
            "Authorization": f"Bearer {self._config.openai_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(OPENAI_RESPONSES_URL, json=payload, headers=headers)

        if resp.status_code != 200:
            logger.error("OpenAI API error %s: %s", resp.status_code, resp.text)
            raise AIClientError(f"OpenAI API returned {resp.status_code}: {resp.text}")

        data = resp.json()
        text = _extract_openai_text(data)
        if not text:
            logger.error("OpenAI response had no text output: %s", data)
            raise AIClientError("OpenAI response contained no text output")
        return text

    async def _generate_anthropic(self, history: Iterable[Turn], user_message: str) -> str:
        messages = [{"role": turn.role, "content": turn.content} for turn in history]
        messages.append({"role": "user", "content": user_message})

        payload = {
            "model": self._config.anthropic_model,
            "system": self._config.system_prompt,
            "messages": messages,
            "max_tokens": 1024,
            "temperature": 0.9,
        }
        headers = {
            "x-api-key": self._config.anthropic_api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(ANTHROPIC_MESSAGES_URL, json=payload, headers=headers)

        if resp.status_code != 200:
            logger.error("Anthropic API error %s: %s", resp.status_code, resp.text)
            raise AIClientError(f"Anthropic API returned {resp.status_code}: {resp.text}")

        data = resp.json()
        text = _extract_anthropic_text(data)
        if not text:
            logger.error("Anthropic response had no text output: %s", data)
            raise AIClientError("Anthropic response contained no text output")
        return text


def _extract_openai_text(data: dict) -> str:
    """Walk the Responses API `output` array, skipping reasoning items.

    Shape: output -> [ { type: "reasoning", ... }, { type: "message", content: [
        { type: "output_text", text: "..." } ] } ]
    """
    chunks: list[str] = []
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue  # skip "reasoning" and any other non-message items
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                text = content.get("text", "")
                if text:
                    chunks.append(text)
    return "".join(chunks).strip()


def _extract_anthropic_text(data: dict) -> str:
    """Walk the Messages API `content` array, concatenating text blocks."""
    chunks: list[str] = []
    for block in data.get("content", []):
        if block.get("type") == "text":
            text = block.get("text", "")
            if text:
                chunks.append(text)
    return "".join(chunks).strip()
