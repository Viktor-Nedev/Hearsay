"""Delivering a payload to a seat, whatever channel that seat happens to be on.

Day 1 taught us not to hardcode what a channel can do: the SDK docs, the examples
README and the project's own test suite each claimed something different from what
the live gateway reports (see FIELDNOTES.md). So the matrix is fetched, not
assumed, and delivery degrades on its own:

    send      -> send_message()  — push into the conversation, unprompted
    otherwise -> reply()         — answer the seat's last inbound message

The fallback matters because ``send`` is the whole relay. If a channel ever loses
it, the game keeps running in a slightly worse mode instead of dying.
"""

from __future__ import annotations

import logging

from hearsay.store.db import Seat, Store
from hearsay.transport import Payload

logger = logging.getLogger(__name__)


class CapabilityMatrix:
    """What each channel can actually do, according to the gateway itself."""

    def __init__(self, channels: list[dict]) -> None:
        self._caps: dict[str, set[str]] = {}
        for entry in channels:
            name = entry.get("channel")
            if not name:
                continue
            # Two providers can back one channel (phone: twilio and telnyx).
            # Union them: if either can do it, the channel can.
            self._caps.setdefault(name, set()).update(entry.get("capabilities") or [])

    @classmethod
    def from_client(cls, client) -> "CapabilityMatrix":
        return cls(client.channels())

    def can(self, channel: str, capability: str) -> bool:
        return capability in self._caps.get(channel, set())

    def channels(self) -> list[str]:
        return sorted(self._caps)

    def relay_capable(self) -> list[str]:
        """Channels that can be pushed to without being asked first."""
        return sorted(name for name, caps in self._caps.items() if "send" in caps)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        rows = ", ".join(f"{name}={sorted(caps)}" for name, caps in sorted(self._caps.items()))
        return f"CapabilityMatrix({rows})"


class Outbox:
    """The real Transport: turns an effect into a message on someone's phone."""

    def __init__(self, client, matrix: CapabilityMatrix, store: Store) -> None:
        self._client = client
        self._matrix = matrix
        self._store = store

    def send(self, seat: Seat, payload: Payload) -> None:
        # Blocks are provider-neutral and degrade to clean text on channels that
        # cannot render them, so they are always safe to pass through as-is.
        blocks = payload.blocks

        if self._matrix.can(seat.channel, "send"):
            self._client.send_message(seat.conversation_id, text=payload.text, blocks=blocks)
            return

        if seat.last_message_id:
            logger.info(
                "%s lacks `send`; replying to last inbound message instead", seat.channel
            )
            self._client.reply(seat.last_message_id, text=payload.text, blocks=blocks)
            return

        raise RuntimeError(
            f"cannot reach seat {seat.codename}: channel {seat.channel!r} has no `send` "
            "capability and the seat has no inbound message to reply to"
        )

    def supports_buttons(self, seat: Seat) -> bool:
        """Whether this seat can vote by tapping instead of typing."""
        return self._matrix.can(seat.channel, "interactions")
