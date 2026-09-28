import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MessageEvent:
    platform: str
    chat_id: str
    chat_type: str
    user_id: str
    text: str
    attachments: list = field(default_factory=list)
    thread_id: Optional[str] = None
    reply_to_message_id: Optional[str] = None
    raw: Optional[dict] = None


@dataclass
class SendResult:
    message_id: Optional[str] = None
    ok: bool = True
    error: Optional[str] = None


# Strong refs for fire-and-forget tasks: the event loop keeps only weak refs,
# so a queued-message replay could be GC'd mid-flight (audit L2).
_BG_TASKS: set = set()


def spawn_task(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        coro.close()
        return None
    task = loop.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


class BaseChannelAdapter(ABC):
    platform: str = "base"

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def send_message(self, chat_id: str, text: str, **meta) -> str: ...

    @abstractmethod
    async def edit_message(self, chat_id: str, msg_id: str, text: str) -> None: ...

    @abstractmethod
    async def on_message(self, event: MessageEvent) -> None: ...

    async def send_approval_prompt(self, chat_id: str, tool_name: str,
                                   reason: str, paths: str, nonce: str = "") -> None:
        """Send an approval prompt to the chat. Default: plain text.
        Override in subclasses for buttons/rich UI. `nonce` binds button views
        to the one pending approval they were built for (audit M3)."""
        text = (f"⚠️ Approval required\n"
                f"Tool: {tool_name}\n"
                f"Reason: {reason}\n"
                f"Paths: {paths}\n\n"
                f"Reply /approve to allow, /deny to block.")
        await self.send_message(chat_id, text)
