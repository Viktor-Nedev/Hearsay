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
    Relay,
    Said,
    Started,
    Voted,
)
from hearsay.store.db import Store
from hearsay.store.snapshot import load_state, save_state
from hearsay.ai_player import FOLLOWUPS, OPENERS, choose_vote, line, plan_tamper
from hearsay.tamper import build_rewriter

CHANNELS = ["discord", "email", "slack", "discord", "email", "slack"]


def _seed_game(store: Store, seats: int, rng: random.Random, honest: bool = False) -> str:
    game_id = f"sim_{uuid.uuid4().hex[:8]}"
    store.create_game(game_id, rules.new_game_code(), honest=honest)
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


def _drive(state: GameState, rng: random.Random, rewriter=None) -> tuple[GameState, list, list]:
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
            text = line(pool, seat.codename, others, rng)
            cast.append((seat.codename, text))
            state, new = apply(state, Said(seat.id, text))
            effects += new

    elif state.phase is Phase.TAMPER:
        rewrites = plan_tamper(state, rewriter, rng) if rewriter else ()
        for seat_id, text, cause in rewrites:
            cast.append((state.seat(seat_id).codename, f"[{cause}] {text}"))
        state, new = apply(state, Relay(rewrites))
        effects += new

    elif state.phase is Phase.VOTE:
        for seat in state.alive:
            target = choose_vote(state, seat, rng)
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

        # Only after the relay actually happened. With tampering on, statements
        # are held back until the impostor has had their turn.
        relays = [e for e in effects if isinstance(e, LogRelay)]
        if relays:
            altered = [r for r in relays if r.cause != "clean"]
            note = f"  \033[35m({len(altered)} altered)\033[0m" if altered else ""
            print(f"    \033[2m→ relayed to {len(state.alive)} seats{note}\033[0m")

    if before.phase is Phase.TAMPER:
        if cast:
            print("  \033[35mtampering\033[0m")
            for codename, text in cast:
                print(f"    {codename:<11}{text}")
        else:
            print("  \033[2mtampering   (nothing changed)\033[0m")

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
    parser.add_argument("--honest", action="store_true",
                        help="no tampering; the relay passes everything through")
    parser.add_argument("--live-llm", action="store_true",
                        help="use Gemini for rewrites instead of the offline backend")
    args = parser.parse_args(argv)

    if not rules.MIN_SEATS <= args.seats <= rules.MAX_SEATS:
        print(f"seats must be {rules.MIN_SEATS}-{rules.MAX_SEATS}", file=sys.stderr)
        return 2

    rng = random.Random(args.seed)
    store = Store(":memory:")
    game_id = _seed_game(store, args.seats, rng, honest=args.honest)

    from spike.probe import _load_dotenv

    _load_dotenv()
    rewriter = None if args.honest else build_rewriter(prefer_offline=not args.live_llm)

    state = load_state(store, game_id)
    state, effects = apply(state, Started(), rng)
    save_state(store, state)

    watched: list[str] = []
    watch_seat = state.by_codename(args.watch) if args.watch else None
    if watch_seat:
        watched += [e.payload.text for e in effects
                    if isinstance(e, Deliver) and e.seat_id == watch_seat.id]

    mode = "honest" if args.honest else f"tampering via {getattr(rewriter, 'name', '?')}"
    print(f"\n\033[1mHEARSAY\033[0m  {args.seats} seats  ·  {mode}  ·  seed {args.seed}")
    print("  " + "  ".join(f"{s.codename}/{s.channel}" for s in state.seats))

    rounds = 0
    while state.phase is not Phase.GAMEOVER and rounds < args.max_rounds:
        before = state
        state, effects, cast = _drive(state, rng, rewriter)

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
