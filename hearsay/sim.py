"""Play a whole game in the terminal, with nobody waiting on email.

    python -m hearsay.sim
    python -m hearsay.sim --seats 6 --seed 7
    python -m hearsay.sim --watch Ochre     # exactly what one player receives

The point is not test coverage — `tests/` does that. The point is being able to
*read* a game in one second and judge whether it holds together, before asking
four humans to spend twenty minutes finding out that it does not.

It runs through the real store, so every round is written to SQLite and reloaded
from it. If persistence is broken the simulation notices before a live game does.
"""

from __future__ import annotations

import argparse
import random
import sys
import uuid

from hearsay.engine import rules
from hearsay.engine.machine import apply
from hearsay.engine.state import (
    Deliver,
    GameState,
    LogRelay,
    Phase,
    Said,
    Started,
    Voted,
)
from hearsay.store.db import Store
from hearsay.store.snapshot import load_state, save_state

CHANNELS = ["discord", "email", "slack", "discord", "email", "slack"]

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


def _line(pool: list[str], speaker: str, others: list[str], rng: random.Random) -> str:
    template = rng.choice(pool)
    return template.format(other=rng.choice(others) if others else speaker)


def _suspicion(state: GameState, seat_id: str) -> list[str]:
    """Who this seat has heard named, most-accused first.

    Bots that vote at random almost never converge, so a simulated game runs
    forever and tells you nothing. Real players converge because the transcript
    concentrates suspicion — so the bots read it too, and fall back to random
    when nobody has been named.
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


def _seed_game(store: Store, seats: int, rng: random.Random) -> str:
    game_id = f"sim_{uuid.uuid4().hex[:8]}"
    store.create_game(game_id, rules.new_game_code(), honest=True)
    for i in range(seats):
        store.add_seat(
            conversation_id=f"conv_{i}",
            game_id=game_id,
            codename=rules.CODENAMES[i],
            channel=CHANNELS[i % len(CHANNELS)],
            connection_id="sim",
            last_message_id=f"msg_{i}",
        )
    return game_id


def _drive(state: GameState, rng: random.Random) -> tuple[GameState, list, list]:
    """Answer whatever the current phase is asking for, from every living seat.

    Returns the new state, the effects, and a transcript of what the bots did —
    the state itself cannot be inspected afterwards, because closing a phase
    clears the round.
    """
    effects: list = []
    cast: list[tuple[str, str]] = []

    if state.phase in (Phase.STATEMENT, Phase.DELIBERATE):
        pool = OPENERS if state.phase is Phase.STATEMENT else FOLLOWUPS
        for seat in state.alive:
            others = [s.codename for s in state.alive if s.id != seat.id]
            text = _line(pool, seat.codename, others, rng)
            cast.append((seat.codename, text))
            state, new = apply(state, Said(seat.id, text))
            effects += new

    elif state.phase is Phase.VOTE:
        for seat in state.alive:
            others = [s.codename for s in state.alive if s.id != seat.id]
            suspects = [n for n in _suspicion(state, seat.id) if n in others]
            target = suspects[0] if suspects else rng.choice(others)
            cast.append((seat.codename, target))
            state, new = apply(state, Voted(seat.id, target))
            effects += new

    return state, effects, cast


def _render(state: GameState, before: GameState, effects: list, cast: list) -> None:
    if before.phase is Phase.STATEMENT:
        print(f"\n\033[1m── Round {before.round} " + "─" * 44 + "\033[0m")

    if before.phase in (Phase.STATEMENT, Phase.DELIBERATE):
        label = "statements" if before.phase is Phase.STATEMENT else "discussion"
        print(f"  {label}")
        for codename, text in cast:
            print(f"    {codename:<11}{text}")

        relays = [e for e in effects if isinstance(e, LogRelay)]
        altered = [r for r in relays if r.cause != "clean"]
        note = f"  \033[35m({len(altered)} altered)\033[0m" if altered else ""
        print(f"    \033[2m→ relayed to {len(state.alive)} seats{note}\033[0m")

    if before.phase is Phase.VOTE:
        print("  votes")
        for codename, target in cast:
            print(f"    {codename:<11}→ {target}")
        just_out = [
            s for s in state.seats
            if not s.alive and before.seat(s.id) and before.seat(s.id).alive
        ]
        if just_out:
            print(f"  \033[31m✗ {just_out[0].codename} voted out\033[0m")
        else:
            print("  \033[33m— deadlock, nobody out\033[0m")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seats", type=int, default=5)
    parser.add_argument("--seed", type=int, default=None, help="fix for a repeatable game")
    parser.add_argument("--watch", help="print every message one codename receives")
    parser.add_argument("--max-rounds", type=int, default=12)
    args = parser.parse_args(argv)

    if not rules.MIN_SEATS <= args.seats <= rules.MAX_SEATS:
        print(f"seats must be {rules.MIN_SEATS}-{rules.MAX_SEATS}", file=sys.stderr)
        return 2

    rng = random.Random(args.seed)
    store = Store(":memory:")
    game_id = _seed_game(store, args.seats, rng)

    state = load_state(store, game_id)
    state, effects = apply(state, Started(), rng)
    save_state(store, state)

    watched: list[str] = []
    watch_seat = state.by_codename(args.watch) if args.watch else None
    if watch_seat:
        watched += [e.payload.text for e in effects
                    if isinstance(e, Deliver) and e.seat_id == watch_seat.id]

    print(f"\n\033[1mHEARSAY\033[0m  {args.seats} seats  ·  honest mode  ·  seed {args.seed}")
    print("  " + "  ".join(f"{s.codename}/{s.channel}" for s in state.seats))

    rounds = 0
    while state.phase is not Phase.GAMEOVER and rounds < args.max_rounds:
        before = state
        state, effects, cast = _drive(state, rng)

        # Round-trip through SQLite every step, so a persistence bug shows up
        # here rather than in front of four humans.
        save_state(store, state)
        reloaded = load_state(store, game_id)
        if reloaded.phase is not state.phase or reloaded.round != state.round:
            print(f"\n\033[31mPERSISTENCE MISMATCH\033[0m "
                  f"memory={state.phase}/{state.round} disk={reloaded.phase}/{reloaded.round}")
            return 1

        if watch_seat:
            watched += [e.payload.text for e in effects
                        if isinstance(e, Deliver) and e.seat_id == watch_seat.id]

        _render(state, before, effects, cast)
        if before.phase is Phase.VOTE:
            rounds += 1

    print()
    if state.phase is Phase.GAMEOVER:
        impostor = state.impostor
        who = "the witnesses win" if state.winner == rules.WITNESS else "the impostor wins"
        print(f"\033[1m{who}\033[0m — {impostor.codename} was the impostor "
              f"(on {impostor.channel})")
    else:
        print(f"stopped after {rounds} rounds without a winner")

    if watch_seat:
        print(f"\n\033[1m── everything {watch_seat.codename} received " + "─" * 26 + "\033[0m")
        for message in watched:
            print("\n" + "\n".join("  " + line for line in message.split("\n")))

    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
