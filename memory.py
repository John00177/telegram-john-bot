"""In-memory per-chat conversation history."""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Turn:
    role: str  # "user" or "assistant"
    content: str


class ConversationMemory:
    """Keeps the last N message pairs per chat_id, entirely in process memory.

    State is lost on restart by design — there is no persistence layer.
    """

    def __init__(self, max_history_per_chat: int = 20):
        self._max_history_per_chat = max_history_per_chat
        self._chats: dict[int, deque[Turn]] = defaultdict(
            lambda: deque(maxlen=max_history_per_chat)
        )

    def add_exchange(self, chat_id: int, user_message: str, assistant_reply: str) -> None:
        history = self._chats[chat_id]
        history.append(Turn(role="user", content=user_message))
        history.append(Turn(role="assistant", content=assistant_reply))

    def get_history(self, chat_id: int) -> list[Turn]:
        return list(self._chats.get(chat_id, ()))

    def clear(self, chat_id: int) -> None:
        self._chats.pop(chat_id, None)

    def chat_count(self) -> int:
        return len(self._chats)
