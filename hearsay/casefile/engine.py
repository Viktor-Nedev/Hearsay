"""The casefile loop, as a function.

    apply(state, event) -> (new_state, [effect])

Same shape as the Hearsay machine and the same discipline — no I/O, no database,
no SDK — but a far simpler shape underneath: a linear chain of stages, no roles,
no phases beyond which stage is open.

It emits the *same* effect types as Hearsay, which is why `Driver._execute` did
not have to learn anything about this mode.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from hearsay.casefile import narration
from hearsay.casefile.case import Case, accepts, deal
from hearsay.engine.state import Deliver, Effect, SeatView, Started
from hearsay.transport import Payload

#: Wrong answers before the stage's hint is offered.
HINT_AFTER = 3


# ---------------------------------------------------------------- the state


@dataclass(frozen=True)
class CaseState:
    game_id: str
    code: str
    case: Case
    seats: tuple[SeatView, ...] = ()
    #: Which stage is open. Persisted in `games.round`.
    stage_index: int = 0
    #: Wrong answers on the current stage, counted from `case_answers`.
    attempts: int = 0
    started: bool = False
    solved: bool = False

    @property
    def stage(self):
        return self.case.stage(self.stage_index)

    @property
    def is_final(self) -> bool:
        return self.stage_index >= self.case.final_index

    def seat(self, seat_id: str) -> SeatView | None:
        return next((s for s in self.seats if s.id == seat_id), None)

    def hand_for(self, seat_id: str) -> list[str]:
        """The fragments this seat was dealt for the open stage, and no others."""
        stage = self.stage
        if stage is None:
            return []
        index = next((i for i, s in enumerate(self.seats) if s.id == seat_id), None)
        if index is None:
            return []
        return deal(stage.fragments, len(self.seats))[index]

    def with_(self, **changes) -> "CaseState":
        return replace(self, **changes)


# --------------------------------------------------------------- the events


@dataclass(frozen=True)
class Answered:
    """Somebody offered an answer. `accusing` is True for ACCUSE, False for SOLVE."""

    seat_id: str
    text: str
    accusing: bool = False


@dataclass(frozen=True)
class LogAnswer:
    """Record what was offered. The reveal reads these back.

    Carries its own stage rather than letting the driver read one off the game
    row: a correct answer advances the stage before effects are executed, so the
    row would already say the next one and every solved answer would be filed
    against the stage after the one it solved.
    """

    seat_id: str
    stage: int
    answer: str
    correct: bool


Event = Started | Answered


# ------------------------------------------------------------------- apply


def apply(state: CaseState, event: Event) -> tuple[CaseState, list[Effect]]:
    if isinstance(event, Started):
        return _start(state)
    if isinstance(event, Answered):
        return _answered(state, event)
    return state, []


def _start(state: CaseState) -> tuple[CaseState, list[Effect]]:
    if state.started or not state.seats:
        return state, []

    stage = state.case.stage(0)
    if stage is None:
        return state, []

    opened = state.with_(started=True, stage_index=0, attempts=0)
    effects: list[Effect] = [
        Deliver(
            seat.id,
            Payload(narration.opening(
                opened.case, stage, opened.hand_for(seat.id), len(opened.seats)
            )),
        )
        for seat in opened.seats
    ]
    return opened, effects


def _answered(state: CaseState, event: Answered) -> tuple[CaseState, list[Effect]]:
    seat = state.seat(event.seat_id)
    if seat is None:
        return state, []

    if state.solved:
        return state, [Deliver(seat.id, Payload(
            narration.ALREADY_CLOSED.format(culprit=state.case.culprit)))]

    if not state.started:
        return state, [Deliver(seat.id, Payload(narration.NOT_STARTED))]

    stage = state.stage
    if stage is None:
        return state, []

    answer = (event.text or "").strip()
    if not answer:
        command = "ACCUSE" if state.is_final else "SOLVE"
        return state, [Deliver(seat.id, Payload(
            narration.EMPTY_ANSWER.format(command=command)))]

    # An accusation before the last stage is a guess, and saying so is kinder
    # than accepting it and letting them skip the case they came to solve.
    if event.accusing and not state.is_final:
        return state, [Deliver(seat.id, Payload(narration.TOO_EARLY_TO_ACCUSE.format(
            n=state.stage_index + 1, total=len(state.case.stages))))]

    correct = accepts(stage, answer)
    effects: list[Effect] = [LogAnswer(seat.id, state.stage_index, answer, correct)]

    if not correct:
        attempts = state.attempts + 1
        for other in state.seats:
            effects.append(Deliver(other.id, Payload(narration.wrong(
                state.case, stage, state.stage_index, answer, attempts, HINT_AFTER))))
        return state.with_(attempts=attempts), effects

    for other in state.seats:
        effects.append(Deliver(other.id, Payload(
            narration.solved(state.case, stage, state.stage_index, answer))))

    if state.is_final:
        return _close(state, effects)

    return _advance(state, effects)


def _advance(state: CaseState, effects: list[Effect]) -> tuple[CaseState, list[Effect]]:
    nxt = state.with_(stage_index=state.stage_index + 1, attempts=0)
    stage = nxt.stage
    if stage is None:
        return _close(state, effects)

    for seat in nxt.seats:
        effects.append(Deliver(seat.id, Payload(narration.next_stage(
            nxt.case, stage, nxt.hand_for(seat.id), nxt.stage_index,
            nxt.is_final, len(nxt.seats),
        ))))
    return nxt, effects


def _close(state: CaseState, effects: list[Effect]) -> tuple[CaseState, list[Effect]]:
    """Everyone finally sees the whole file, including what they never held."""
    done = state.with_(solved=True)

    hands = {
        seat.codename: [
            fragment
            for index in range(len(done.case.stages))
            for fragment in _hand_at(done, seat.id, index)
        ]
        for seat in done.seats
    }
    total = sum(len(s.fragments) for s in done.case.stages)

    text = narration.closing(done.case, hands, total)
    for seat in done.seats:
        effects.append(Deliver(seat.id, Payload(text)))
    return done, effects


def _hand_at(state: CaseState, seat_id: str, stage_index: int) -> list[str]:
    stage = state.case.stage(stage_index)
    if stage is None:
        return []
    index = next((i for i, s in enumerate(state.seats) if s.id == seat_id), None)
    if index is None:
        return []
    return deal(stage.fragments, len(state.seats))[index]
