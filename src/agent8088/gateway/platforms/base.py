from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable


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


@runtime_checkable
class StreamSink(Protocol):
    def __call__(self, delta: str) -> None: ...
    def finalize(self, full_text: str) -> None: ...
    def fail(self, err: Exception) -> None: ...


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

    def supports_streaming(self) -> bool:
        return True

    def streaming_overflow_limit(self) -> Optional[int]:
        return None

    async def send_approval_prompt(self, chat_id: str, tool_name: str,
                                     reason: str, paths: str) -> None:
        """Send an approval prompt to the chat. Default: plain text.
        Override in subclasses for buttons/rich UI."""
        text = (f"⚠️ Approval required\n"
                f"Tool: {tool_name}\n"
                f"Reason: {reason}\n"
                f"Paths: {paths}\n\n"
                f"Reply /approve to allow, /deny to block.")
        await self.send_message(chat_id, text)