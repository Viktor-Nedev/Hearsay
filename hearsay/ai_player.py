"""Seats that play themselves.

Social deduction needs a room. Waiting for four people to be free at the same
time is the slowest part of building this, and on the day of a demo it is a
single point of failure — so the room can be filled with bots and the game plays
with one human in it.

A bot seat is an ordinary row in the database with a synthetic `conversation_id`
and `channel="ai"`. The engine cannot tell the difference and does not try; the
only place in the whole system that knows bots exist is one branch in
`channels/outbox.py`, which drops the payload instead of sending it.

Bots decide from the game *state*, never by reading the messages addressed to
them. Parsing our own prose back into intent would be a second, worse copy of
the rules — and it would drift the moment the wording changed.
"""

from __future__ import annotations

import random

from hearsay.engine import rules
from hearsay.engine.state import Event, GameState, Phase, Relay, Said, SeatView, Voted

#: How often the agent garbles a line nobody asked it to. Without a few changes
#: nobody authored, every rewrite is provably the impostor's and the game
#: collapses into a single accusation.
NOISE_RATE = 0.25

#: Marks a seat nobody is sitting in.
AI_CHANNEL = "ai"


def is_bot(seat) -> bool:
    return getattr(seat, "channel", None) == AI_CHANNEL


OPENERS = [
    "I was nowhere near this.",
    "Someone here is lying and it isn't me.",
    "I've got nothing to hide.",
    "This feels rehearsed.",
    "I don't like how quiet {other} is being.",
    "{other} answered too fast.",
    "Ask {other} where they were.",
    "I'll say what I said last time: nothing.",
    "Whoever it is, they're enjoying this.",
    "{other} is trying very hard to sound relaxed.",
]

FOLLOWUPS = [
    "I'm not changing my story.",
    "Still think it's {other}.",
    "Fine — {other}, explain yourself.",
    "I've heard enough.",
    "Nobody has actually answered me.",
    "{other} still hasn't said anything useful.",
]


def line(pool: list[str], speaker: str, others: list[str], rng: random.Random) -> str:
    template = rng.choice(pool)
    return template.format(other=rng.choice(others) if others else speaker)


def speak(state: GameState, seat: SeatView, rng: random.Random) -> str:
    pool = OPENERS if state.phase is Phase.STATEMENT else FOLLOWUPS
    others = [s.codename for s in state.alive if s.id != seat.id]
    return line(pool, seat.codename, others, rng)


def suspicion(state: GameState, seat_id: str) -> list[str]:
    """Who this seat has heard named, most-accused first.

    Bots that vote at random almost never converge, so a game runs forever and
    tells you nothing. Real players converge because the transcript concentrates
    suspicion — so the bots read it too.

    Note this reads the *canonical* statements rather than the per-recipient
    view, so a bot is not fooled by a rewrite aimed at somebody else. Bots are
    scenery; making them properly deceivable is a game-design question, not a
    plumbing one.
    """
    heard = dict(state.statements) | dict(state.deliberations)
    counts: dict[str, int] = {}
    for speaker_id, text in heard.items():
        for other in state.alive:
            if other.id in (seat_id, speaker_id):
                continue
            if other.codename.lower() in text.lower():
                counts[other.codename] = counts.get(other.codename, 0) + 1
    return [name for name, _ in sorted(counts.items(), key=lambda kv: -kv[1])]


def choose_vote(state: GameState, seat: SeatView, rng: random.Random) -> str:
    others = [s.codename for s in state.alive if s.id != seat.id]
    suspects = [name for name in suspicion(state, seat.id) if name in others]
    return suspects[0] if suspects else rng.choice(others)


def plan_tamper(state: GameState, rewriter, rng: random.Random) -> tuple:
    """What the impostor changes, plus whatever the agent garbles on its own.

    Used by bot impostors and by the simulator. A human impostor names their own
    target and instruction; only the ambient noise is shared.
    """
    statements = dict(state.statements)
    impostor = next((s for s in state.alive if s.role == rules.IMPOSTOR), None)
    candidates = [s for s in state.alive if s.id in statements and s is not impostor]
    if not candidates:
        return ()

    rewrites: list[tuple[str, str, str]] = []

    victim = rng.choice(candidates)
    others = [s.codename for s in state.alive
              if s.id != victim.id and (impostor is None or s.id != impostor.id)]
    if others:
        instruction = f"make it look like {victim.codename} saw {rng.choice(others)} sneaking off"
        changed = rewriter.rewrite(statements[victim.id], victim.codename, instruction)
        if changed != statements[victim.id]:
            rewrites.append((victim.id, changed, "impostor"))

    rewrites.extend(add_noise(state, rewriter, rng, skip={victim.id}))
    return tuple(rewrites)


def add_noise(state: GameState, rewriter, rng: random.Random, skip: set[str]) -> list[tuple]:
    """A distortion nobody chose, so a rewrite is never provably the impostor's."""
    statements = dict(state.statements)
    untouched = [s for s in state.alive if s.id in statements and s.id not in skip]
    if not untouched or rng.random() >= NOISE_RATE:
        return []

    seat = rng.choice(untouched)
    drifted = rewriter.rewrite(statements[seat.id], seat.codename, "", subtle=True)
    if drifted == statements[seat.id]:
        return []
    return [(seat.id, drifted, "noise")]


class Bench:
    """Decides what the empty seats do next."""

    def __init__(self, rng: random.Random | None = None, rewriter=None) -> None:
        self.rng = rng or random.Random()
        self.rewriter = rewriter

    def next_event(self, state: GameState) -> Event | None:
        """One thing a bot wants to do right now, or None if it is all on humans.

        Returns a single event rather than a batch so the caller drives a bounded
        loop: each bot turn changes the state, which may hand the next turn to a
        different bot, or back to a person.
        """
        if state.phase in (Phase.STATEMENT, Phase.DELIBERATE):
            waiting = [s for s in state.waiting_on() if is_bot(s)]
            if waiting:
                seat = waiting[0]
                return Said(seat.id, speak(state, seat, self.rng))
            return None

        if state.phase is Phase.VOTE:
            waiting = [s for s in state.waiting_on() if is_bot(s)]
            if waiting:
                seat = waiting[0]
                return Voted(seat.id, choose_vote(state, seat, self.rng))
            return None

        if state.phase is Phase.TAMPER:
            impostor = next((s for s in state.alive if s.role == rules.IMPOSTOR), None)
            if impostor is not None and is_bot(impostor) and self.rewriter is not None:
                return Relay(plan_tamper(state, self.rewriter, self.rng))
            return None

        return None
