"""How the engine reaches a seat, and how tests avoid the network entirely.

The engine never sends anything itself — it returns effects, and a Transport
executes them. That split is what lets a whole game run in a unit test with no
API key, no channels, and no waiting for a human on email.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from hearsay.store.db import Seat


@dataclass
class Payload:
    """One outbound message, before it knows what channel it is going to.

    ``text`` is always present and always sufficient — it is what an email or SMS
    player receives. ``blocks`` is the richer rendering for Discord and Slack;
    channels without block support fall back to ``text`` automatically.
    """

    text: str
    blocks: list[dict] | None = None

    def __post_init__(self) -> None:
        if not self.text or not self.text.strip():
            raise ValueError("payload text must not be empty")


@runtime_checkable
class Transport(Protocol):
    """Anything that can deliver a payload to a seat."""

    def send(self, seat: Seat, payload: Payload) -> None: ...


@dataclass
class FakeTransport:
    """Records instead of sending. The backbone of the offline test suite."""

    sent: list[tuple[str, Payload]] = field(default_factory=list)

    def send(self, seat: Seat, payload: Payload) -> None:
        self.sent.append((seat.id, payload))

    # -- helpers for assertions ------------------------------------------

    def to(self, seat_id: str) -> list[Payload]:
        """Everything delivered to one seat, in order."""
        return [payload for target, payload in self.sent if target == seat_id]

    def last_to(self, seat_id: str) -> Payload | None:
        received = self.to(seat_id)
        return received[-1] if received else None

    def texts_to(self, seat_id: str) -> list[str]:
        return [payload.text for payload in self.to(seat_id)]

    def recipients(self) -> list[str]:
        """Distinct seats reached, in first-contact order."""
        seen: list[str] = []
        for target, _ in self.sent:
            if target not in seen:
                seen.append(target)
        return seen

    def leaked(self, seat_id: str, secret: str) -> bool:
        """True if `secret` ever reached `seat_id`.

        The isolation assertion the whole game rests on: used in tests to prove
        one player's words never surface in another player's thread except
        through a deliberate relay.
        """
        return any(secret in payload.text for payload in self.to(seat_id))

    def clear(self) -> None:
        self.sent.clear()
