"""The shape of a game in flight, and the two vocabularies around it.

Everything here is immutable. The machine takes a state and an event and returns
a *new* state plus a list of effects; it never mutates, never touches the
database and never sends anything. That is what lets a whole game run in a test
with no API key, no channels and nobody waiting on email.

Events come in from players. Effects go out to the world. The driver in `app.py`
is the only thing that knows either of them is real.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from hearsay.transport import Payload


class Phase(str, Enum):
    """Where a game is. `str` mixin so it stores and compares as plain text."""

    LOBBY = "LOBBY"
    BRIEF = "BRIEF"
    STATEMENT = "STATEMENT"
    #: The impostor alone, deciding whose words to change. Everyone else waits.
    TAMPER = "TAMPER"
    RELAY = "RELAY"
    DELIBERATE = "DELIBERATE"
    VOTE = "VOTE"
    REVEAL = "REVEAL"
    GAMEOVER = "GAMEOVER"

    #: Phases where the machine is waiting on humans rather than on itself.
    @property
    def collects_input(self) -> bool:
        return self in {Phase.STATEMENT, Phase.DELIBERATE, Phase.VOTE}


@dataclass(frozen=True)
class SeatView:
    """One player, as the engine sees them. No conversation, no channel plumbing."""

    id: str
    codename: str
    role: str | None = None
    alive: bool = True
    channel: str = "unknown"


# ---------------------------------------------------------------- events


@dataclass(frozen=True)
class Started:
    """The host called it: deal roles and begin."""


@dataclass(frozen=True)
class Said:
    """A player wrote something. What it means depends on the phase."""

    seat_id: str
    text: str


@dataclass(frozen=True)
class Voted:
    seat_id: str
    target: str


@dataclass(frozen=True)
class Timeout:
    """A phase deadline expired. Carries the phase so a late timer for an
    already-advanced phase can be ignored rather than skipping a round."""

    phase: Phase


@dataclass(frozen=True)
class Relay:
    """Close the tamper window and deliver the round.

    `rewrites` is `(seat_id, replacement, cause)` — what each named speaker will
    appear to have said, and why. Empty means an honest relay.

    The rewriting itself happens in the driver, because it is a network call to a
    language model and the machine is pure. By the time this event arrives the
    strings are already decided; the machine only routes them.
    """

    rewrites: tuple[tuple[str, str, str], ...] = ()


Event = Started | Said | Voted | Timeout | Relay


# --------------------------------------------------------------- effects


@dataclass(frozen=True)
class Deliver:
    """Send this payload to this seat. One per recipient — never a broadcast.

    Keeping fan-out explicit is what makes the leak tests possible: every
    assertion about who learned what reads straight off the effect list.
    """

    seat_id: str
    payload: Payload


@dataclass(frozen=True)
class LogRelay:
    """Record what a player said against what was actually delivered.

    In honest mode `original == relayed` and `cause` is 'clean'. The tamper step
    will emit these with 'impostor' or 'noise' without the machine changing shape.
    """

    seat_id: str
    original: str
    relayed: str
    cause: str = "clean"


@dataclass(frozen=True)
class SetDeadline:
    seconds: int
    phase: Phase


Effect = Deliver | LogRelay | SetDeadline


# ----------------------------------------------------------------- state


@dataclass(frozen=True)
class GameState:
    game_id: str
    code: str
    phase: Phase = Phase.LOBBY
    round: int = 0
    honest: bool = False
    seats: tuple[SeatView, ...] = ()
    #: seat id -> what they said this round
    statements: tuple[tuple[str, str], ...] = ()
    deliberations: tuple[tuple[str, str], ...] = ()
    #: seat id -> codename they voted for this round
    votes: tuple[tuple[str, str], ...] = ()
    #: (seat_id, replacement, cause) for this round's relay. Cleared each round.
    rewrites: tuple[tuple[str, str, str], ...] = ()
    winner: str | None = None

    # -- lookups ---------------------------------------------------------

    def seat(self, seat_id: str) -> SeatView | None:
        return next((s for s in self.seats if s.id == seat_id), None)

    def by_codename(self, codename: str) -> SeatView | None:
        target = codename.strip().lower()
        return next((s for s in self.seats if s.codename.lower() == target), None)

    @property
    def alive(self) -> tuple[SeatView, ...]:
        return tuple(s for s in self.seats if s.alive)

    @property
    def impostor(self) -> SeatView | None:
        from hearsay.engine.rules import IMPOSTOR

        return next((s for s in self.seats if s.role == IMPOSTOR), None)

    def said(self, seat_id: str) -> str | None:
        return dict(self.statements).get(seat_id)

    def deliberated(self, seat_id: str) -> str | None:
        return dict(self.deliberations).get(seat_id)

    def voted(self, seat_id: str) -> str | None:
        return dict(self.votes).get(seat_id)

    # -- the current collection ------------------------------------------

    def collected(self) -> dict[str, str]:
        """Whatever the current phase is gathering, keyed by seat."""
        if self.phase is Phase.STATEMENT:
            return dict(self.statements)
        if self.phase is Phase.DELIBERATE:
            return dict(self.deliberations)
        if self.phase is Phase.VOTE:
            return dict(self.votes)
        return {}

    def everyone_answered(self) -> bool:
        """True once every living player has had their turn this phase."""
        answered = set(self.collected())
        return all(s.id in answered for s in self.alive)

    def waiting_on(self) -> tuple[SeatView, ...]:
        answered = set(self.collected())
        return tuple(s for s in self.alive if s.id not in answered)

    # -- transitions ------------------------------------------------------

    def with_(self, **changes) -> "GameState":
        return replace(self, **changes)

    def record_said(self, seat_id: str, text: str) -> "GameState":
        field = "statements" if self.phase is Phase.STATEMENT else "deliberations"
        current = dict(getattr(self, field))
        current[seat_id] = text
        return self.with_(**{field: tuple(current.items())})

    def record_vote(self, seat_id: str, target: str) -> "GameState":
        current = dict(self.votes)
        current[seat_id] = target
        return self.with_(votes=tuple(current.items()))

    def rewrite_for(self, seat_id: str) -> tuple[str, str] | None:
        """What this speaker will appear to have said, and why. None if untouched."""
        for target, replacement, cause in self.rewrites:
            if target == seat_id:
                return replacement, cause
        return None

    def clear_round(self) -> "GameState":
        return self.with_(statements=(), deliberations=(), votes=(), rewrites=())

    def kill(self, seat_id: str) -> "GameState":
        return self.with_(
            seats=tuple(
                replace(s, alive=False) if s.id == seat_id else s for s in self.seats
            )
        )

    def deal(self, roles: dict[str, str]) -> "GameState":
        return self.with_(
            seats=tuple(replace(s, role=roles.get(s.id, s.role)) for s in self.seats)
        )
