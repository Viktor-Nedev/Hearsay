"""Bridge between the durable rows and the immutable snapshot the machine uses.

Kept out of `db.py` on purpose. The store is a dumb repository that knows about
tables; the machine is a pure function that knows about games. If `Store`
imported `GameState` the import graph would close a loop
(`store -> engine.state -> transport -> store`), and more importantly the two
would stop being separable — which is the property that lets every engine test
run without a database.
"""

from __future__ import annotations

from hearsay.engine.state import GameState, Phase, SeatView
from hearsay.store.db import Store

DELIBERATION = "deliberation"


def load_state(store: Store, game_id: str) -> GameState | None:
    """Rebuild a game from disk, exactly as the machine last left it."""
    row = store.game(game_id)
    if row is None:
        return None

    round_no = int(row["round"])
    seats = tuple(
        SeatView(
            id=seat.conversation_id,
            codename=seat.codename,
            role=seat.role,
            alive=seat.alive,
            channel=seat.channel,
        )
        for seat in store.seats(game_id)
    )

    return GameState(
        game_id=game_id,
        code=row["code"],
        phase=Phase(row["phase"]),
        round=round_no,
        honest=bool(row["honest"]),
        seats=seats,
        statements=tuple(store.statements(game_id, round_no).items()),
        deliberations=tuple(store.statements(game_id, round_no, DELIBERATION).items()),
        votes=tuple(store.votes(game_id, round_no).items()),
        winner=None if row["ended_at"] is None else _winner_of(store, game_id),
    )


def save_state(store: Store, state: GameState) -> None:
    """Persist a snapshot. Idempotent — saving the same state twice is a no-op."""
    store.set_phase(state.game_id, state.phase.value, state.round)

    for seat in state.seats:
        stored = store.seat(seat.id)
        if stored is None:
            continue
        if seat.role and stored.role != seat.role:
            store.assign_role(seat.id, seat.role)
        if stored.alive and not seat.alive:
            store.eliminate(seat.id)

    for seat_id, text in state.statements:
        store.record_statement(state.game_id, state.round, seat_id, text)
    for seat_id, text in state.deliberations:
        store.record_statement(state.game_id, state.round, seat_id, text, DELIBERATION)
    for seat_id, target in state.votes:
        store.record_vote(state.game_id, state.round, seat_id, target)

    if state.winner and store.game(state.game_id)["ended_at"] is None:
        store.end_game(state.game_id)


def _winner_of(store: Store, game_id: str) -> str | None:
    """Infer the winner of a finished game from who was left standing.

    The outcome is not stored as a column: it is a function of the roles and who
    survived, so deriving it keeps one source of truth.
    """
    from hearsay.engine.rules import winner

    seats = store.seats(game_id)
    roles = {s.conversation_id: s.role for s in seats if s.role}
    return winner(roles, [s.conversation_id for s in seats if s.alive])
