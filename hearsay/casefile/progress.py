"""Bridge between the durable rows and the casefile snapshot.

Same split as `store/snapshot.py` and for the same reason: the store knows about
tables, the engine knows about cases, and keeping them apart is what lets every
engine test run without a database.
"""

from __future__ import annotations

from hearsay.casefile.case import find_case
from hearsay.casefile.engine import CaseState
from hearsay.engine.state import SeatView
from hearsay.store.db import Store

PHASE_OPEN = "INVESTIGATING"
PHASE_CLOSED = "SOLVED"


def load_case_state(store: Store, game_id: str) -> CaseState | None:
    row = store.game(game_id)
    if row is None or row["mode"] != "casefile":
        return None

    case = find_case(row["case_id"] or "ashford")
    stage_index = int(row["round"])

    return CaseState(
        game_id=game_id,
        code=row["code"],
        case=case,
        seats=tuple(
            SeatView(id=s.conversation_id, codename=s.codename, channel=s.channel)
            for s in store.seats(game_id)
        ),
        stage_index=stage_index,
        attempts=store.attempts(game_id, stage_index),
        started=row["phase"] != "LOBBY",
        solved=row["phase"] == PHASE_CLOSED,
    )


def save_case_state(store: Store, state: CaseState) -> None:
    phase = PHASE_CLOSED if state.solved else (PHASE_OPEN if state.started else "LOBBY")
    store.set_phase(state.game_id, phase, state.stage_index)
    if state.solved and store.game(state.game_id)["ended_at"] is None:
        store.end_game(state.game_id)
