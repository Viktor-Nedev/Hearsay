"""Play a whole game through the real driver, with nobody in the room.

    python -m hearsay.rehearse --mode hearsay
    python -m hearsay.rehearse --mode casefile
    python -m hearsay.rehearse --mode casefile --watch Ochre

`sim.py` exercises the *engine*: pure functions, no store, no router, no outbox.
This exercises everything between a message arriving and a message going out —
the router, the mode branch, the per-game lock, the SQLite round-trip, the
capability-aware outbox, the bench. That layer had two bugs no unit test could
see, and both were found by hand:

  * `_drain_bots` ran for every game and handed a casefile to the Hearsay
    snapshot loader, which raised on `Phase('INVESTIGATING')`.
  * `LogAnswer` read its stage off the game row, which a correct answer had
    already advanced, so every solved answer was filed one stage late.

Finding those by hand was luck. This makes it a command.

It is also the script for the demo: what it prints is what the recording shows,
so the running order can be written from real output instead of imagination.
"""

from __future__ import annotations

import argparse
import random
import shutil
import tempfile
from pathlib import Path

from hearsay.ai_player import Bench, speak
from hearsay.app import Driver
from hearsay.casefile.progress import load_case_state
from hearsay.engine.state import Phase
from hearsay.store.db import Store
from hearsay.store.snapshot import load_state
from hearsay.tamper import ScriptedRewriter

DIM, BOLD, GREEN, RED, MAGENTA, RESET = (
    "\033[2m", "\033[1m", "\033[32m", "\033[31m", "\033[35m", "\033[0m"
)

#: Channels the rehearsal seats people on, so the transcript shows the split.
HUMAN_CHANNELS = ["discord", "email", "discord", "email", "slack"]


class _RecordingClient:
    """Stands in for CommClient. Records instead of sending; never touches the network."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, bool]] = []

    def channels(self):
        return [
            {"channel": "email",
             "capabilities": ["receive", "reply", "send", "initiate", "media"]},
            {"channel": "discord",
             "capabilities": ["receive", "reply", "send", "initiate",
                              "interactions", "reactions", "media"]},
            {"channel": "slack",
             "capabilities": ["receive", "reply", "send", "interactions", "media"]},
        ]

    def send_message(self, conversation_id, text=None, blocks=None):
        self.sent.append((conversation_id, text or "", blocks is not None))
        return {"id": "out"}

    def reply(self, message_id, text=None, blocks=None):
        self.sent.append((message_id, text or "", blocks is not None))
        return {"id": "out"}

    def on_message(self, fn):
        return fn

    def on_interaction(self, fn):
        return fn

    def _request(self, method, path):
        if path.startswith("/v1/messages/"):
            return {"auto_generated": False}
        return []

    def to(self, conversation_id: str) -> list[tuple[str, bool]]:
        return [(t, rich) for target, t, rich in self.sent if target == conversation_id]


class _Message:
    """The shape `Driver.on_message` reads. Deliberately not the SDK's class —
    the point is to exercise our code, not theirs."""

    def __init__(self, conversation_id, text, channel="discord", msg_id="m"):
        self.conversation_id = conversation_id
        self.text = text
        self.channel = channel
        self.connection_id = f"conn_{channel}"
        self.id = msg_id
        self.subject = None
        self.sender = {"address": f"{conversation_id}@example.com"}


def _rig(tmp: Path, seed: int, honest: bool = False):
    client = _RecordingClient()
    store = Store(tmp / "rehearse.db")
    driver = Driver(client, store, honest=honest)
    driver.rewriter = ScriptedRewriter()
    driver.bench = Bench(random.Random(seed), driver.rewriter)
    driver.rng = random.Random(seed)
    return driver, client, store


def _seat_people(driver, code: str, count: int) -> list[str]:
    seats = []
    for i in range(count):
        conv = f"conv_{i}"
        driver.on_message(_Message(conv, f"JOIN {code}",
                                   channel=HUMAN_CHANNELS[i % len(HUMAN_CHANNELS)],
                                   msg_id=f"join{i}"))
        seats.append(conv)
    return seats


# ------------------------------------------------------------------ hearsay


def rehearse_hearsay(driver, client, store, humans: int, bots: int) -> bool:
    code = driver.lobby.create_game()
    game_id = store.game_by_code(code)["id"]
    people = _seat_people(driver, code, humans)
    for _ in range(bots):
        driver.lobby.seat_bot(game_id)

    seats = store.seats(game_id)
    print(f"{BOLD}HEARSAY{RESET}  game {code}  ·  "
          + "  ".join(f"{s.codename}/{s.channel}" for s in seats) + "\n")

    driver.on_message(_Message(people[0], "START", msg_id="start"))

    for turn in range(60):
        state = load_state(store, game_id)
        if state is None or state.phase is Phase.GAMEOVER:
            break

        waiting = [s.id for s in state.waiting_on()]
        mine = [c for c in people if c in waiting]

        if state.phase is Phase.TAMPER:
            impostor = state.impostor
            if impostor and impostor.id in people:
                others = [s.codename for s in state.alive if s.id != impostor.id]
                victim = others[0]
                # Name a third person in the instruction. A rewrite told only to
                # "make them sound guilty" has nobody to point at and falls back
                # to hedging the sentence, which is not what the demo will show.
                blamed = others[1] if len(others) > 1 else victim
                print(f"  {MAGENTA}TAMPER {victim}{RESET} -> point at {blamed}  "
                      f"{DIM}(by {impostor.codename}){RESET}")
                driver.on_message(_Message(
                    impostor.id,
                    f"TAMPER {victim} make it look like they saw {blamed} sneaking off",
                    msg_id=f"t{turn}"))
                continue
            print(f"  {RED}stalled: bot impostor did not act{RESET}")
            return False

        if not mine:
            print(f"  {RED}stalled in {state.phase} waiting on {waiting}{RESET}")
            return False

        for conv in mine:
            if state.phase is Phase.VOTE:
                target = next(s.codename for s in state.alive if s.id != conv)
                driver.on_message(_Message(conv, f"VOTE {target}", msg_id=f"v{turn}{conv}"))
            else:
                # Real sentences, not placeholders. A rewriter given "conv_1
                # says something" produces something that tells you nothing
                # about what the demo will look like.
                seat = state.seat(conv)
                driver.on_message(_Message(conv, speak(state, seat, driver.rng),
                                           msg_id=f"s{turn}{conv}"))

    state = load_state(store, game_id)
    if state is None or state.phase is not Phase.GAMEOVER:
        print(f"  {RED}never finished{RESET}")
        return False

    rows = store.ledger(game_id)
    altered = [r for r in rows if r["cause"] != "clean"]
    print(f"  {GREEN}finished{RESET}  ·  {state.winner} win  ·  "
          f"{state.impostor.codename} was the impostor")
    print(f"  relays logged: {len(rows)}, of which {len(altered)} altered")
    for row in altered[:3]:
        print(f"    {DIM}{row['cause']:<9}{RESET} {row['original'][:34]!r} "
              f"-> {row['relayed'][:34]!r}")
    return True


# ----------------------------------------------------------------- casefile


def rehearse_casefile(driver, client, store, humans: int) -> bool:
    from hearsay.casefile.case import find_case

    case = find_case("ashford")
    code = driver.lobby.create_game(mode="casefile", case_id=case.id)
    game_id = store.game_by_code(code)["id"]
    people = _seat_people(driver, code, humans)

    seats = store.seats(game_id)
    print(f"{BOLD}CASEFILE{RESET}  {case.title}  ·  case {code}  ·  "
          + "  ".join(f"{s.codename}/{s.channel}" for s in seats) + "\n")

    driver.on_message(_Message(people[0], "START", msg_id="start"))

    state = load_case_state(store, game_id)
    for seat in state.seats:
        held = [h.splitlines()[0].strip("* ") for h in state.hand_for(seat.id)]
        print(f"  {seat.codename:<11}{DIM}holds{RESET} " + (", ".join(held) or "nothing"))
    print()

    # One wrong answer first, so the nudge path runs too.
    driver.on_message(_Message(people[0], "SOLVE 11:04", msg_id="wrong"))
    print(f"  {DIM}SOLVE 11:04{RESET} -> rejected, "
          f"{load_case_state(store, game_id).attempts} attempt")

    for index, stage in enumerate(case.stages):
        state = load_case_state(store, game_id)
        if state.solved:
            break
        answer = stage.answers[0]
        command = "ACCUSE" if state.is_final else "SOLVE"
        driver.on_message(_Message(people[index % len(people)], f"{command} {answer}",
                                   msg_id=f"a{index}"))
        after = load_case_state(store, game_id)
        mark = GREEN + "closed" + RESET if after.solved else f"stage {after.stage_index}"
        print(f"  {command} {answer:<12} -> {mark}")

    state = load_case_state(store, game_id)
    if not state.solved:
        print(f"  {RED}case never closed{RESET}")
        return False

    logged = store.answers(game_id)
    print(f"\n  {GREEN}finished{RESET}  ·  {case.culprit}  ·  {len(logged)} answers logged")
    for row in logged:
        tick = GREEN + "correct" + RESET if row["correct"] else RED + "wrong  " + RESET
        print(f"    {DIM}stage {row['stage']}{RESET}  {tick}  {row['answer']!r}")
    return True


# --------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("hearsay", "casefile", "both"), default="both")
    parser.add_argument("--humans", type=int, default=2,
                        help="seats played by a person (the rest are bots, hearsay only)")
    parser.add_argument("--bots", type=int, default=3)
    parser.add_argument("--seed", type=int, default=2)
    parser.add_argument("--watch", help="print every message one codename received")
    args = parser.parse_args(argv)

    tmp = Path(tempfile.mkdtemp(prefix="hearsay-rehearse-"))
    ok = True
    try:
        modes = ["casefile", "hearsay"] if args.mode == "both" else [args.mode]
        for mode in modes:
            room = tmp / mode
            room.mkdir(parents=True, exist_ok=True)
            driver, client, store = _rig(room, args.seed)
            print()

            if mode == "hearsay":
                ok &= rehearse_hearsay(driver, client, store, args.humans, args.bots)
            else:
                ok &= rehearse_casefile(driver, client, store, max(2, args.humans))

            if args.watch:
                _dump_watch(client, store, args.watch)
            store.close()

        print()
        print(f"{GREEN}rehearsal clean{RESET}" if ok else f"{RED}rehearsal failed{RESET}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _dump_watch(client: _RecordingClient, store: Store, codename: str) -> None:
    target = next((s for s in _all_seats(store) if s.codename.lower() == codename.lower()), None)
    if target is None:
        print(f"\n  no seat called {codename}")
        return
    print(f"\n{BOLD}── everything {target.codename} received {'─' * 30}{RESET}")
    for text, rich in client.to(target.conversation_id):
        tag = f" {MAGENTA}[with buttons]{RESET}" if rich else ""
        print(f"\n{tag}\n" + "\n".join("  " + line for line in text.split("\n")))


def _all_seats(store: Store):
    seats = []
    for row in store._db.execute("SELECT id FROM games"):
        seats.extend(store.seats(row["id"]))
    return seats


if __name__ == "__main__":
    raise SystemExit(main())
