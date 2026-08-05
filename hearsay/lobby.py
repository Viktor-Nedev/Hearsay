"""Before the game: taking a seat.

A player arrives knowing nothing except a four-character code someone read out to
them. They send it from whatever they happen to be using — Discord, email, Slack
— and that message is the only handle we will ever have on them. From it we take
a conversation, and a conversation is a seat.

Nobody is ever told who else is in the room, only how many. Learning that Ana is
on email would let you reason about Ana; learning that four seats are filled
tells you only that the game can start.
"""

from __future__ import annotations

import logging
import uuid

from hearsay.engine.rules import MAX_SEATS, MIN_SEATS, new_game_code, next_codename
from hearsay.store.db import Store
from hearsay.transport import Payload, Transport

logger = logging.getLogger(__name__)

WELCOME = (
    "You're in. You are {codename}.\n\n"
    "Hearsay is a game about who you can believe. Every player is on a different "
    "app and none of you can see each other — I carry every word between you.\n\n"
    "Seats filled: {filled}. {waiting}\n\n"
    "Nothing to do yet. I'll write when the game starts."
)

ALREADY_IN = "You're already seated as {codename}. Seats filled: {filled}."

NO_SUCH_GAME = (
    "No game with the code {code}.\n\n"
    "Check the code and send it again, like this:  JOIN K7QP"
)

GAME_FULL = "That game is full ({max_seats} seats). Ask the host to start another."

NEED_CODE = "Send a join code, like this:  JOIN K7QP"

LEFT = "You've left the game. Send JOIN and the code again if you change your mind."

NOT_SEATED = "You're not in a game. Send JOIN and a code to take a seat."


class Lobby:
    """Seats players and answers questions about the room. No game logic."""

    def __init__(self, store: Store, transport: Transport) -> None:
        self._store = store
        self._transport = transport

    # -- host ------------------------------------------------------------

    def create_game(self, honest: bool = False) -> str:
        """Open a new lobby and return the code players will send."""
        code = new_game_code()
        while self._store.game_by_code(code):
            code = new_game_code()
        self._store.create_game(f"game_{uuid.uuid4().hex[:12]}", code, honest=honest)
        logger.info("opened game %s", code)
        return code

    # -- players ---------------------------------------------------------

    def join(
        self,
        code: str | None,
        conversation_id: str,
        channel: str,
        connection_id: str,
        message_id: str | None = None,
    ) -> bool:
        """Seat a player. Returns whether they ended up seated."""
        existing = self._store.seat(conversation_id)
        if existing:
            filled = len(self._store.seats(existing.game_id))
            self._reply(existing, ALREADY_IN.format(codename=existing.codename, filled=filled))
            return True

        if not code:
            self._say(conversation_id, channel, connection_id, message_id, NEED_CODE)
            return False

        game = self._store.game_by_code(code)
        if not game:
            self._say(
                conversation_id, channel, connection_id, message_id,
                NO_SUCH_GAME.format(code=code.upper()),
            )
            return False

        taken = [s.codename for s in self._store.seats(game["id"])]
        if len(taken) >= MAX_SEATS:
            self._say(
                conversation_id, channel, connection_id, message_id,
                GAME_FULL.format(max_seats=MAX_SEATS),
            )
            return False

        seat = self._store.add_seat(
            conversation_id=conversation_id,
            game_id=game["id"],
            codename=next_codename(taken),
            channel=channel,
            connection_id=connection_id,
            last_message_id=message_id,
        )

        filled = len(taken) + 1
        missing = max(0, MIN_SEATS - filled)
        waiting = (
            f"Waiting for {missing} more." if missing
            else "Enough to start whenever the host is ready."
        )
        self._transport.send(
            seat,
            Payload(WELCOME.format(codename=seat.codename, filled=filled, waiting=waiting)),
        )
        logger.info("%s joined %s from %s", seat.codename, game["code"], channel)
        return True

    def seat_bot(self, game_id: str) -> str:
        """Fill a seat nobody is sitting in.

        The row is ordinary — the engine never learns this player is not a
        person. Only the conversation id is synthetic, because there is no
        conversation: `channels/outbox.py` drops anything addressed here.
        """
        from hearsay.ai_player import AI_CHANNEL

        taken = [s.codename for s in self._store.seats(game_id)]
        codename = next_codename(taken)
        self._store.add_seat(
            conversation_id=f"ai:{codename.lower()}:{game_id}",
            game_id=game_id,
            codename=codename,
            channel=AI_CHANNEL,
            connection_id="bench",
        )
        logger.info("%s is a bot", codename)
        return codename

    def leave(self, conversation_id: str) -> bool:
        seat = self._store.seat(conversation_id)
        if not seat:
            return False
        self._reply(seat, LEFT)
        self._store.remove_seat(conversation_id)
        logger.info("%s left", seat.codename)
        return True

    def who(self, conversation_id: str) -> bool:
        """Report the size of the room, never its contents."""
        seat = self._store.seat(conversation_id)
        if not seat:
            return False

        seats = self._store.seats(seat.game_id)
        alive = [s for s in seats if s.alive]
        lines = [f"You are {seat.codename}.", f"Seats filled: {len(seats)}."]
        if len(alive) != len(seats):
            lines.append(f"Still in: {len(alive)}.")
        lines.append("I won't tell you who anyone is. That's the game.")
        self._reply(seat, "\n".join(lines))
        return True

    # -- plumbing --------------------------------------------------------

    def _reply(self, seat, text: str) -> None:
        self._transport.send(seat, Payload(text))

    def _say(
        self, conversation_id: str, channel: str, connection_id: str,
        message_id: str | None, text: str,
    ) -> None:
        """Answer someone who has no seat yet, so has no Seat row to send to."""
        from hearsay.store.db import Seat

        placeholder = Seat(
            conversation_id=conversation_id, game_id="", codename="?", role=None,
            alive=True, channel=channel, connection_id=connection_id,
            last_message_id=message_id,
        )
        self._transport.send(placeholder, Payload(text))
