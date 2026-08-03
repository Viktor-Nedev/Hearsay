"""The game, as a function.

    apply(state, event) -> (new_state, [effect])

No I/O, no database, no SDK. Feed it a state and something a player did, get back
the next state and a list of things the world should do about it. Every test in
`tests/test_machine.py` is a few calls to this function and some assertions about
the effects.

The one place a player's words turn into what other players receive is
`_relay_lines()`. In honest mode it is the identity function. When the impostor
gets their power, that is the only function that changes.
"""

from __future__ import annotations

import random

from hearsay.engine import narration
from hearsay.engine.rules import IMPOSTOR, MIN_SEATS, assign_roles, normalise_codename
from hearsay.engine.rules import tally as count_votes
from hearsay.engine.rules import winner as decide_winner
from hearsay.engine.state import (
    Deliver,
    Effect,
    Event,
    GameState,
    LogRelay,
    Phase,
    Relay,
    Said,
    SeatView,
    SetDeadline,
    Started,
    Timeout,
    Voted,
)
from hearsay.transport import Payload

#: How long a phase waits for stragglers before moving on without them.
#: TAMPER is short: everyone else is sitting in silence while it runs.
PHASE_SECONDS = {
    Phase.STATEMENT: 300,
    Phase.TAMPER: 120,
    Phase.DELIBERATE: 240,
    Phase.VOTE: 180,
}


def apply(
    state: GameState, event: Event, rng: random.Random | None = None
) -> tuple[GameState, list[Effect]]:
    if isinstance(event, Started):
        return _start(state, rng)
    if isinstance(event, Said):
        return _said(state, event)
    if isinstance(event, Voted):
        return _voted(state, event)
    if isinstance(event, Relay):
        return _relayed(state, event)
    if isinstance(event, Timeout):
        return _timeout(state, event)
    return state, []


# ------------------------------------------------------------------ start


def _start(state: GameState, rng: random.Random | None) -> tuple[GameState, list[Effect]]:
    if state.phase is not Phase.LOBBY:
        return state, []

    if len(state.seats) < MIN_SEATS:
        need = MIN_SEATS - len(state.seats)
        return state, [
            Deliver(s.id, Payload(narration.TOO_FEW.format(need=need))) for s in state.seats
        ]

    dealt = state.deal(assign_roles([s.id for s in state.seats], rng))
    briefed = dealt.with_(phase=Phase.BRIEF, round=1)

    effects: list[Effect] = [
        Deliver(seat.id, Payload(narration.brief(briefed, seat))) for seat in briefed.seats
    ]

    # BRIEF is transient: the roles are out, so start collecting immediately.
    return _open_collection(briefed.with_(phase=Phase.STATEMENT), effects)


def _open_collection(
    state: GameState, effects: list[Effect] | None = None
) -> tuple[GameState, list[Effect]]:
    """Ask every living player for whatever the current phase collects."""
    effects = list(effects or [])
    for seat in state.alive:
        text = (
            narration.vote_prompt(state, seat)
            if state.phase is Phase.VOTE
            else narration.prompt(state)
        )
        effects.append(Deliver(seat.id, Payload(text)))

    seconds = PHASE_SECONDS.get(state.phase)
    if seconds:
        effects.append(SetDeadline(seconds, state.phase))
    return state, effects


# ------------------------------------------------------------- statements


def _said(state: GameState, event: Said) -> tuple[GameState, list[Effect]]:
    seat = state.seat(event.seat_id)
    if seat is None:
        return state, []
    if not seat.alive:
        return state, [Deliver(seat.id, Payload(narration.ELIMINATED_SILENCE))]

    if state.phase is Phase.VOTE:
        # They wrote prose when a vote was wanted. Point them at the ask again.
        return state, [
            Deliver(seat.id, Payload(narration.vote_error(
                narration.VOTE_PROMPT, state, seat)))
        ]

    if state.phase not in (Phase.STATEMENT, Phase.DELIBERATE):
        return state, [Deliver(seat.id, Payload(narration.NOT_YOUR_TURN))]

    updated = state.record_said(seat.id, event.text)
    remaining = len(updated.waiting_on())
    effects: list[Effect] = [
        Deliver(seat.id, Payload(narration.acknowledge(updated, remaining)))
    ]

    if not updated.everyone_answered():
        return updated, effects

    if updated.phase is Phase.STATEMENT and _can_tamper(updated):
        return _open_tamper(updated, effects)

    return _relay(updated, effects, updated.phase)


# ----------------------------------------------------------------- tamper


def _can_tamper(state: GameState) -> bool:
    """Only when there is a living impostor and the game is not in honest mode."""
    return not state.honest and any(s.role == IMPOSTOR for s in state.alive)


def _open_tamper(state: GameState, effects: list[Effect]) -> tuple[GameState, list[Effect]]:
    """Show the impostor everything, before anyone else sees any of it.

    Seeing the round first is the whole of the power. Everyone else has already
    been told their statement landed, so their silence here reads as waiting
    rather than as something happening.
    """
    tampering = state.with_(phase=Phase.TAMPER)
    impostor = next(s for s in tampering.alive if s.role == IMPOSTOR)

    lines = [
        (seat.codename, text)
        for seat in tampering.alive
        if (text := dict(tampering.statements).get(seat.id)) is not None
    ]
    effects.append(
        Deliver(impostor.id, Payload(narration.tamper_prompt(tampering, lines)))
    )
    effects.append(SetDeadline(PHASE_SECONDS[Phase.TAMPER], Phase.TAMPER))
    return tampering, effects


def _relayed(state: GameState, event: Relay) -> tuple[GameState, list[Effect]]:
    """The tamper window closed. Whatever was decided, deliver the round."""
    if state.phase is not Phase.TAMPER:
        return state, []
    return _relay(state.with_(rewrites=event.rewrites), [], Phase.STATEMENT)


# ------------------------------------------------------------------ relay


def _relay(
    state: GameState, effects: list[Effect], source: Phase
) -> tuple[GameState, list[Effect]]:
    """Deliver the round's words — a different transcript per recipient."""
    relaying = state.with_(phase=Phase.RELAY)
    collected = (
        dict(state.statements) if source is Phase.STATEMENT else dict(state.deliberations)
    )

    # One ledger row per speaker, not per recipient: a rewrite is decided once.
    for seat in relaying.alive:
        original = collected.get(seat.id)
        if original is None:
            continue
        rewrite = relaying.rewrite_for(seat.id)
        relayed, cause = rewrite if rewrite else (original, "clean")
        effects.append(LogRelay(seat.id, original, relayed, cause))

    intro = (
        narration.RELAY_INTRO if source is Phase.STATEMENT
        else narration.RELAY_INTRO_DELIBERATE
    )
    for recipient in relaying.alive:
        lines = _relay_for(relaying, recipient, collected)
        effects.append(
            Deliver(recipient.id, Payload(narration.transcript(relaying, lines, intro)))
        )

    nxt = Phase.DELIBERATE if source is Phase.STATEMENT else Phase.VOTE
    # Rewrites are spent: they belong to the statements just delivered, and must
    # not bleed into the deliberation relay later this round.
    return _open_collection(relaying.with_(phase=nxt, rewrites=()), effects)


def _relay_for(
    state: GameState, recipient: SeatView, collected: dict[str, str]
) -> list[tuple[str, str]]:
    """What one person receives.

    **This is the seam.** The speaker always reads back exactly what they typed;
    everyone else reads whatever the impostor decided they said. That asymmetry
    is the game: the victim finds out only when the room quotes something at them
    they never wrote, and by then the only witness is the thing that changed it.
    """
    lines: list[tuple[str, str]] = []
    for seat in state.alive:
        original = collected.get(seat.id)
        if original is None:
            continue
        if seat.id == recipient.id:
            lines.append((seat.codename, original))
            continue
        rewrite = state.rewrite_for(seat.id)
        lines.append((seat.codename, rewrite[0] if rewrite else original))
    return lines


# ------------------------------------------------------------------ votes


def _voted(state: GameState, event: Voted) -> tuple[GameState, list[Effect]]:
    seat = state.seat(event.seat_id)
    if seat is None:
        return state, []
    if not seat.alive:
        return state, [Deliver(seat.id, Payload(narration.ELIMINATED_SILENCE))]
    if state.phase is not Phase.VOTE:
        return state, [Deliver(seat.id, Payload(narration.VOTE_NOT_NOW))]

    name = normalise_codename(event.target or "")
    target = state.by_codename(name)

    if target is None:
        return state, [Deliver(seat.id, Payload(
            narration.vote_error(narration.VOTE_UNKNOWN, state, seat, name)))]
    if target.id == seat.id:
        return state, [Deliver(seat.id, Payload(
            narration.vote_error(narration.VOTE_SELF, state, seat, name)))]
    if not target.alive:
        return state, [Deliver(seat.id, Payload(
            narration.vote_error(narration.VOTE_DEAD, state, seat, target.codename)))]

    updated = state.record_vote(seat.id, target.codename)
    remaining = len(updated.waiting_on())
    ack = (
        narration.VOTE_ACK_LAST if remaining == 0 else narration.VOTE_ACK
    ).format(target=target.codename, remaining=remaining)
    effects: list[Effect] = [Deliver(seat.id, Payload(ack))]

    if not updated.everyone_answered():
        return updated, effects

    return _resolve(updated, effects)


def _resolve(state: GameState, effects: list[Effect]) -> tuple[GameState, list[Effect]]:
    eliminated_name, counts = count_votes(dict(state.votes))
    eliminated = state.by_codename(eliminated_name) if eliminated_name else None

    revealing = state.with_(phase=Phase.REVEAL)
    if eliminated:
        revealing = revealing.kill(eliminated.id)

    for seat in state.seats:
        if seat.alive or (eliminated and seat.id == eliminated.id):
            effects.append(Deliver(seat.id, Payload(
                narration.reveal(revealing, seat, eliminated, counts))))

    roles = {s.id: s.role for s in revealing.seats if s.role}
    won = decide_winner(roles, [s.id for s in revealing.alive])
    if won:
        return _finish(revealing, won, effects)

    nxt = revealing.clear_round().with_(phase=Phase.STATEMENT, round=revealing.round + 1)
    return _open_collection(nxt, effects)


def _finish(state: GameState, won: str, effects: list[Effect]) -> tuple[GameState, list[Effect]]:
    final = state.with_(phase=Phase.GAMEOVER, winner=won)
    closing = narration.CLOSING_HONEST if final.honest else ""
    text = narration.game_over(final, won, closing or narration.CLOSING_HONEST)
    for seat in final.seats:
        effects.append(Deliver(seat.id, Payload(text)))
    return final, effects


# ---------------------------------------------------------------- timeout


def _timeout(state: GameState, event: Timeout) -> tuple[GameState, list[Effect]]:
    """Move on without the stragglers.

    Guarded on phase: a timer set for round 2's vote must not fire into round 3
    and skip a whole statement round.
    """
    if state.phase is not event.phase:
        return state, []

    if state.phase is Phase.TAMPER:
        # The impostor sat on it. Everyone else is waiting in silence, so the
        # round goes through untouched rather than hanging on one player.
        return _relay(state, [], Phase.STATEMENT)

    if not state.phase.collects_input:
        return state, []

    if state.phase is Phase.VOTE:
        # An unopposed vote still resolves; no votes at all is a deadlock.
        return _resolve(state, [])

    if not state.collected():
        # Nobody said anything. There is nothing to relay, so skip the transcript
        # and open the next phase rather than stalling here forever.
        return _skip_empty(state)

    if state.phase is Phase.STATEMENT and _can_tamper(state):
        return _open_tamper(state, [])

    return _relay(state, [], state.phase)


def _skip_empty(state: GameState) -> tuple[GameState, list[Effect]]:
    """A collection phase nobody answered. Advance rather than stall forever."""
    nxt = Phase.DELIBERATE if state.phase is Phase.STATEMENT else Phase.VOTE
    return _open_collection(state.with_(phase=nxt), [])


def living_impostor(state: GameState) -> SeatView | None:
    """Convenience for the driver; the machine never needs it."""
    return next((s for s in state.alive if s.role == IMPOSTOR), None)
