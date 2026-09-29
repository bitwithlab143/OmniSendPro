"""Provider abstraction (ADR-007): the job runner only depends on this protocol."""

from __future__ import annotations

from email.message import EmailMessage
from typing import Protocol


class EmailProvider(Protocol):
    async def connect(self) -> None: ...

    async def send(self, message: EmailMessage, sender: str, recipient: str) -> str | None:
        """Send one message; return the provider's response text. Raises on failure."""
        ...

    async def close(self) -> None: ...
