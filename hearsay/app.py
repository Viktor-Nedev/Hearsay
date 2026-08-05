"""The agent itself: one handler, every channel.

    python -m hearsay.app --new         open a game, print the code, start listening
    python -m hearsay.app               resume whatever was already running

Everything a player does arrives here through a single `on_message`, whether they
are on Discord or email, and leaves through a single `Outbox`. Nothing in this
file branches on channel — that knowledge lives in `channels/outbox.py`, and the
engine underneath does not know channels exist at all.

Two things are done the hard way on purpose, both explained at their call sites:
the event loop is hand-rolled so a restart cannot lose messages, and every game
transition is taken under a lock because a game spans conversations while the
SDK only serialises within one.
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import sys
import threading
import time

from caspian_sdk import CommClient, CommError, Interaction, Message

from hearsay.ai_player import Bench, add_noise
from hearsay.channels.inbound import is_playable, truncate_statement
from hearsay.channels.outbox import CapabilityMatrix, Outbox
from hearsay.engine import narration
from hearsay.engine.machine import apply
from hearsay.engine.rules import IMPOSTOR, MIN_SEATS, normalise_codename
from hearsay.engine.state import (
    Deliver,
    Event,
    LogRelay,
    Phase,
    Relay,
    Said,
    SetDeadline,
    Started,
    Voted,
)
from hearsay.lobby import Lobby
from hearsay.router import parse
from hearsay.store.db import Store
from hearsay.store.snapshot import load_state, save_state
from hearsay.tamper import build_rewriter
from hearsay.transport import Payload

logger = logging.getLogger("hearsay")

HELP = (
    "Hearsay — a game about who you can believe.\n\n"
    "  JOIN <code>   take a seat\n"
    "  WHO           how many are in\n"
    "  START         begin, once three have joined\n"
    "  VOTE <name>   when I ask for it\n"
    "  LEAVE         give up your seat\n\n"
    "Anything else you send is treated as speech, and I carry it to the others."
)


class Driver:
    """Wires the pure engine to real people on real channels."""

    def __init__(self, client: CommClient, store: Store, honest: bool = False) -> None:
        self.client = client
        self.store = store
        self.honest = honest
        self.matrix = CapabilityMatrix.from_client(client)
        self.outbox = Outbox(client, self.matrix, store)
        self.lobby = Lobby(store, self.outbox)
        self.rng = random.Random()
        self.rewriter = None if honest else build_rewriter()
        self.bench = Bench(self.rng, self.rewriter)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- the single handler ----------------------------------------------

    def on_message(self, message: Message) -> None:
        """Every inbound message from every channel lands here."""
        if not is_playable(message, self.client):
            return

        seat = self.store.seat(message.conversation_id)
        intent = parse(message.text or "")
        logger.info(
            "<- %s [%s] %s: %r",
            seat.codename if seat else "unseated", message.channel, intent.kind,
            (message.text or "")[:80],
        )

        if seat is None:
            self._greet(message, intent)
            return

        # Remember the newest inbound so outbox can reply-fallback on channels
        # that cannot push (see channels/outbox.py).
        self.store.touch_seat(seat.id, message.id)

        if intent.kind == "join":
            self.lobby.join(intent.arg, message.conversation_id, message.channel,
                            message.connection_id, message.id)
        elif intent.kind == "leave":
            self.lobby.leave(seat.id)
        elif intent.kind == "who":
            self.lobby.who(seat.id)
        elif intent.kind == "help":
            self.outbox.send(seat, Payload(HELP))
        elif intent.kind == "start":
            self._advance(seat.game_id, Started())
        elif intent.kind == "vote":
            self._advance(seat.game_id, Voted(seat.id, intent.arg or ""))
        elif intent.kind == "empty":
            pass
        elif self._handled_as_tamper(seat, intent):
            pass
        else:
            self._advance(seat.game_id, Said(seat.id, truncate_statement(intent.body)))

        self._drain_bots(seat.game_id)

    # -- the impostor's turn ----------------------------------------------

    def _handled_as_tamper(self, seat, intent) -> bool:
        """Deal with the impostor's private turn, if that is what this is."""
        state = load_state(self.store, seat.game_id)
        if state is None or state.phase is not Phase.TAMPER:
            if intent.kind == "tamper":
                self.outbox.send(seat, Payload(narration.TAMPER_NOT_NOW))
                return True
            return False

        view = state.seat(seat.id)
        if view is None or view.role != IMPOSTOR:
            # Everyone else is simply waiting; saying so beats silence.
            self.outbox.send(seat, Payload(narration.WAITING_ON_RELAY))
            return True

        body = (intent.arg if intent.kind == "tamper" else intent.body) or ""
        if not body.strip() or body.strip().lower().startswith("skip"):
            self.outbox.send(seat, Payload(narration.TAMPER_SKIPPED))
            self._advance(seat.game_id, Relay(self._noise_only(state)))
            return True

        self._tamper(seat, state, body)
        return True

    def _tamper(self, seat, state, body: str) -> None:
        """`<codename> <instruction>` — the target is a word, the rest is intent."""
        target_word, _, instruction = body.strip().partition(" ")
        target = state.by_codename(normalise_codename(target_word))
        alive = ", ".join(s.codename for s in state.alive if s.id != seat.id)

        if target is None:
            self.outbox.send(seat, Payload(
                narration.TAMPER_UNKNOWN.format(target=target_word, names=alive)))
            return
        if target.id == seat.id:
            self.outbox.send(seat, Payload(narration.TAMPER_SELF))
            return

        original = state.said(target.id)
        if original is None:
            self.outbox.send(seat, Payload(
                narration.TAMPER_UNKNOWN.format(target=target.codename, names=alive)))
            return

        # A network call, deliberately outside the game lock: nobody else's turn
        # should wait on a language model.
        changed = self.rewriter.rewrite(original, target.codename, instruction.strip())
        if changed == original:
            self.outbox.send(seat, Payload(narration.TAMPER_FAILED))
            self._advance(seat.game_id, Relay(self._noise_only(state)))
            return

        self.outbox.send(seat, Payload(
            narration.TAMPER_DONE.format(codename=target.codename)))
        rewrites = ((target.id, changed, "impostor"),) + tuple(
            add_noise(state, self.rewriter, self.rng, skip={target.id})
        )
        self._advance(seat.game_id, Relay(rewrites))

    def _noise_only(self, state) -> tuple:
        """Even an untouched round drifts sometimes, or a rewrite would be proof."""
        if self.rewriter is None:
            return ()
        return tuple(add_noise(state, self.rewriter, self.rng, skip=set()))

    # -- seats nobody is sitting in ---------------------------------------

    def _drain_bots(self, game_id: str, limit: int = 60) -> None:
        """Let the bots take their turns.

        A queue rather than recursion: a bot's answer produces effects that reach
        other bots, and the last one closing a phase opens the next. Bounded, so
        a rule change that made bots answer forever would stall one game instead
        of wedging the process.
        """
        for _ in range(limit):
            state = load_state(self.store, game_id)
            if state is None:
                return
            event = self.bench.next_event(state)
            if event is None:
                return
            self._advance(game_id, event)
        logger.warning("bot drain hit its limit on %s", game_id)

    def on_interaction(self, interaction: Interaction) -> None:
        """Button taps. Same handler for every channel that has buttons.

        Callback values are `vote:<codename>`; anything else is ignored rather
        than guessed at.
        """
        seat = self.store.seat(interaction.conversation_id or "")
        if seat is None or not interaction.value:
            return
        action, _, argument = interaction.value.partition(":")
        logger.info("<- %s [button] %s", seat.codename, interaction.value)
        if action == "vote":
            self._advance(seat.game_id, Voted(seat.id, argument))
            self._drain_bots(seat.game_id)

    # -- unseated callers -------------------------------------------------

    def _greet(self, message: Message, intent) -> None:
        if intent.kind == "join":
            self.lobby.join(intent.arg, message.conversation_id, message.channel,
                            message.connection_id, message.id)
            return
        # Anyone who writes to the agent without a seat gets told how to get one.
        self.lobby.join(None, message.conversation_id, message.channel,
                        message.connection_id, message.id)

    # -- the engine -------------------------------------------------------

    def _lock(self, game_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(game_id, threading.Lock())

    def _advance(self, game_id: str, event: Event) -> None:
        """Load, apply, save, deliver — all of it under one lock.

        `concurrency="queue"` only serialises within a single conversation, but a
        game spans every player's conversation at once. Two people answering
        simultaneously are two threads reading the same game, and without this
        lock the second save would overwrite the first player's answer.
        """
        with self._lock(game_id):
            state = load_state(self.store, game_id)
            if state is None:
                logger.warning("event for unknown game %s", game_id)
                return
            state, effects = apply(state, event, self.rng)
            save_state(self.store, state)

        # Delivery happens outside the lock: a slow channel must not block the
        # next player's turn.
        self._execute(effects)

    def _execute(self, effects: list) -> None:
        for effect in effects:
            if isinstance(effect, Deliver):
                seat = self.store.seat(effect.seat_id)
                if seat is None:
                    continue
                try:
                    self.outbox.send(seat, effect.payload)
                    logger.info("-> %s [%s]", seat.codename, seat.channel)
                except CommError as exc:
                    logger.error("could not reach %s: %s", seat.codename, exc.detail)
                except Exception:
                    logger.exception("delivery to %s failed", seat.codename)
            elif isinstance(effect, LogRelay):
                seat = self.store.seat(effect.seat_id)
                if seat:
                    row = self.store.game(seat.game_id)
                    self.store.record_relay(seat.game_id, int(row["round"]), effect.seat_id,
                                            effect.original, effect.relayed, effect.cause)
            elif isinstance(effect, SetDeadline):
                # Timers arrive with the scheduler; the machine already emits them.
                logger.debug("deadline %ss for %s", effect.seconds, effect.phase)

    # -- the loop ---------------------------------------------------------

    def run(self, poll_interval: float = 1.0, max_backoff: float = 30.0) -> None:
        """Poll the event stream, persisting our position as we go.

        `client.listen()` would be the idiomatic call, but it cannot be made
        restart-safe: it resumes from the newest event unless handed `from_seq`,
        and the `seq` never reaches a handler — `Message` does not carry it — so
        there is no way to record where we got to. A game runs for hours across
        people who answer email slowly; a restart that skips whatever arrived
        while we were down would lose statements and votes silently.

        So we run the documented custom loop instead, dispatch through the same
        registered handlers, and write the cursor after every event.
        """
        cursor = self.store.get_cursor()
        if cursor == 0:
            cursor = self._latest_seq()
            self.store.set_cursor(cursor)
            logger.info("no saved cursor; starting from seq %s", cursor)
        else:
            logger.info("resuming from seq %s", cursor)

        backoff = poll_interval
        while True:
            try:
                batch = self.client.events(after_seq=cursor, limit=100)
            except KeyboardInterrupt:
                raise
            except Exception:
                logger.warning("poll failed; retrying in %.0fs", backoff, exc_info=True)
                time.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
                continue

            backoff = poll_interval
            if not batch:
                time.sleep(poll_interval)
                continue

            for event in batch:
                try:
                    self.client._dispatch_event(event)
                except Exception:
                    logger.exception("dispatch failed for seq %s", event.get("seq"))
                # Advance even on failure: a message that reliably crashes the
                # handler would otherwise be retried forever and wedge the game.
                cursor = event["seq"]
                self.store.set_cursor(cursor)

    def _latest_seq(self) -> int:
        seq = 0
        while True:
            batch = self.client.events(after_seq=seq, limit=500)
            if not batch:
                return seq
            seq = batch[-1]["seq"]


def connect(client: CommClient) -> list[str]:
    """Bring up every channel we can, and say which ones came up."""
    live: list[str] = []

    username = os.environ.get("HEARSAY_EMAIL_USERNAME", "hearsay")
    try:
        inbox = client.connect_email(username=username)
        live.append(f"email    {inbox.get('address')}")
    except CommError as exc:
        logger.error("email unavailable: %s", exc.detail)

    # Discord only counts once somebody has authorised it; an unfinished install
    # sits at pending_oauth and can neither send nor receive.
    for connection in client._request("GET", "/v1/connections"):
        if connection.get("channel") == "discord":
            if connection.get("status") == "active":
                live.append("discord  connected")
            else:
                live.append(f"discord  {connection.get('status')} — not usable yet")

    return live


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new", action="store_true", help="open a new game and print its code")
    parser.add_argument("--db", default="hearsay.db")
    parser.add_argument("--honest", action="store_true",
                        help="no tampering; the relay passes everything through")
    parser.add_argument("--bots", type=int, default=0, metavar="N",
                        help="fill N seats with bots, so a game can run with one human")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from spike.probe import _load_dotenv

    _load_dotenv()
    if not os.environ.get("CASPIAN_API_KEY"):
        print("CASPIAN_API_KEY is not set. Copy .env.example to .env.", file=sys.stderr)
        return 2

    client = CommClient()
    store = Store(args.db)
    driver = Driver(client, store, honest=args.honest)

    client.on_message(driver.on_message)
    client.on_interaction(driver.on_interaction)

    print("\n  channels")
    for line in connect(client):
        print(f"    {line}")
    print(f"    relay-capable: {', '.join(driver.matrix.relay_capable())}")

    if args.new:
        code = driver.lobby.create_game(honest=args.honest)
        mode = "honest" if args.honest else f"tampering via {driver.rewriter.name}"
        print(f"\n  \033[1mgame {code}\033[0m  ({mode})")

        if args.bots:
            game_id = store.game_by_code(code)["id"]
            filled = [driver.lobby.seat_bot(game_id) for _ in range(args.bots)]
            print(f"    bots seated:   {', '.join(filled)}")

        print(f"    players send:  JOIN {code}")
        print(f"    to the agent on any connected channel, then {MIN_SEATS}+ seats and START\n")

    print("  listening. ctrl-c to stop.\n")
    try:
        driver.run()
    except KeyboardInterrupt:
        print("\n  stopped.")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
