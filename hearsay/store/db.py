"""SQLite persistence for games, seats, transcripts and the event cursor.

Three things force real storage rather than in-memory state:

1. A game spans hours. An email player answers when they get round to it.
2. ``client.listen()`` starts from the newest event unless told otherwise, so a
   restart silently drops everything that arrived while we were down. We keep our
   own cursor and hand it back as ``from_seq`` (see FIELDNOTES.md).
3. The ledger is the forensics record — what a player actually said versus what
   everyone else received. It has to survive the game it describes.

A seat is keyed by ``conversation_id``, never by sender: ``message.sender`` is an
opaque dict whose shape differs per channel, while a conversation is stable per
thread. That makes isolation structural — one seat is one conversation, and two
seats cannot share a thread.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id          TEXT PRIMARY KEY,
    code        TEXT NOT NULL UNIQUE,
    phase       TEXT NOT NULL,
    round       INTEGER NOT NULL DEFAULT 0,
    honest      INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    ended_at    REAL
);

CREATE TABLE IF NOT EXISTS seats (
    conversation_id TEXT PRIMARY KEY,
    game_id         TEXT NOT NULL REFERENCES games(id),
    codename        TEXT NOT NULL,
    role            TEXT,
    alive           INTEGER NOT NULL DEFAULT 1,
    channel         TEXT NOT NULL,
    connection_id   TEXT NOT NULL,
    last_message_id TEXT,
    joined_at       REAL NOT NULL,
    UNIQUE (game_id, codename)
);

-- `kind` separates the two collection phases: a round holds one statement and
-- one deliberation per seat, and they must not overwrite each other.
CREATE TABLE IF NOT EXISTS statements (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id    TEXT NOT NULL REFERENCES games(id),
    round      INTEGER NOT NULL,
    seat_id    TEXT NOT NULL REFERENCES seats(conversation_id),
    kind       TEXT NOT NULL DEFAULT 'statement',
    text       TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS statements_unique
    ON statements(game_id, round, seat_id, kind);

CREATE TABLE IF NOT EXISTS votes (
    game_id    TEXT NOT NULL REFERENCES games(id),
    round      INTEGER NOT NULL,
    voter_seat TEXT NOT NULL REFERENCES seats(conversation_id),
    target     TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (game_id, round, voter_seat)
);

-- The forensics record. `cause` is 'clean', 'impostor' or 'noise'.
CREATE TABLE IF NOT EXISTS ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id    TEXT NOT NULL REFERENCES games(id),
    round      INTEGER NOT NULL,
    seat_id    TEXT NOT NULL,
    original   TEXT NOT NULL,
    relayed    TEXT NOT NULL,
    cause      TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS cursor (
    k   TEXT PRIMARY KEY,
    seq INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS seats_by_game ON seats(game_id);
CREATE INDEX IF NOT EXISTS ledger_by_game ON ledger(game_id, round);
"""


@dataclass(frozen=True)
class Seat:
    """One player. Identified by the conversation they speak through."""

    conversation_id: str
    game_id: str
    codename: str
    role: str | None
    alive: bool
    channel: str
    connection_id: str
    last_message_id: str | None

    @property
    def id(self) -> str:
        return self.conversation_id


class Store:
    def __init__(self, path: str | Path = "hearsay.db") -> None:
        self.path = str(path)
        # check_same_thread=False: the phase scheduler ticks on a background
        # thread while listen() dispatches on another. All writes go through
        # this one connection, which sqlite serialises internally.
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # ---- cursor -----------------------------------------------------------

    def get_cursor(self, key: str = "events") -> int:
        row = self._db.execute("SELECT seq FROM cursor WHERE k = ?", (key,)).fetchone()
        return int(row["seq"]) if row else 0

    def set_cursor(self, seq: int, key: str = "events") -> None:
        self._db.execute(
            "INSERT INTO cursor (k, seq) VALUES (?, ?) "
            "ON CONFLICT(k) DO UPDATE SET seq = excluded.seq",
            (key, seq),
        )
        self._db.commit()

    # ---- games ------------------------------------------------------------

    def create_game(self, game_id: str, code: str, honest: bool = False) -> None:
        self._db.execute(
            "INSERT INTO games (id, code, phase, round, honest, created_at) "
            "VALUES (?, ?, 'LOBBY', 0, ?, ?)",
            (game_id, code.upper(), int(honest), time.time()),
        )
        self._db.commit()

    def game_by_code(self, code: str) -> sqlite3.Row | None:
        return self._db.execute(
            "SELECT * FROM games WHERE code = ? AND ended_at IS NULL", (code.upper(),)
        ).fetchone()

    def game(self, game_id: str) -> sqlite3.Row | None:
        return self._db.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()

    def set_phase(self, game_id: str, phase: str, round_no: int | None = None) -> None:
        if round_no is None:
            self._db.execute("UPDATE games SET phase = ? WHERE id = ?", (phase, game_id))
        else:
            self._db.execute(
                "UPDATE games SET phase = ?, round = ? WHERE id = ?", (phase, round_no, game_id)
            )
        self._db.commit()

    def end_game(self, game_id: str) -> None:
        self._db.execute("UPDATE games SET ended_at = ? WHERE id = ?", (time.time(), game_id))
        self._db.commit()

    # ---- seats ------------------------------------------------------------

    def add_seat(
        self,
        conversation_id: str,
        game_id: str,
        codename: str,
        channel: str,
        connection_id: str,
        last_message_id: str | None = None,
    ) -> Seat:
        self._db.execute(
            "INSERT INTO seats (conversation_id, game_id, codename, channel, connection_id,"
            " last_message_id, joined_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (conversation_id, game_id, codename, channel, connection_id,
             last_message_id, time.time()),
        )
        self._db.commit()
        seat = self.seat(conversation_id)
        assert seat is not None
        return seat

    def seat(self, conversation_id: str) -> Seat | None:
        row = self._db.execute(
            "SELECT * FROM seats WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
        return _row_to_seat(row) if row else None

    def seats(self, game_id: str, alive_only: bool = False) -> list[Seat]:
        sql = "SELECT * FROM seats WHERE game_id = ?"
        if alive_only:
            sql += " AND alive = 1"
        sql += " ORDER BY joined_at"
        return [_row_to_seat(r) for r in self._db.execute(sql, (game_id,))]

    def seat_by_codename(self, game_id: str, codename: str) -> Seat | None:
        row = self._db.execute(
            "SELECT * FROM seats WHERE game_id = ? AND codename = ? COLLATE NOCASE",
            (game_id, codename),
        ).fetchone()
        return _row_to_seat(row) if row else None

    def touch_seat(self, conversation_id: str, last_message_id: str) -> None:
        """Remember the newest inbound message so outbox can reply-fallback to it."""
        self._db.execute(
            "UPDATE seats SET last_message_id = ? WHERE conversation_id = ?",
            (last_message_id, conversation_id),
        )
        self._db.commit()

    def assign_role(self, conversation_id: str, role: str) -> None:
        self._db.execute(
            "UPDATE seats SET role = ? WHERE conversation_id = ?", (role, conversation_id)
        )
        self._db.commit()

    def eliminate(self, conversation_id: str) -> None:
        self._db.execute(
            "UPDATE seats SET alive = 0 WHERE conversation_id = ?", (conversation_id,)
        )
        self._db.commit()

    def remove_seat(self, conversation_id: str) -> None:
        self._db.execute("DELETE FROM seats WHERE conversation_id = ?", (conversation_id,))
        self._db.commit()

    # ---- round data -------------------------------------------------------

    def record_statement(
        self, game_id: str, round_no: int, seat_id: str, text: str,
        kind: str = "statement",
    ) -> None:
        self._db.execute(
            "INSERT INTO statements (game_id, round, seat_id, kind, text, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(game_id, round, seat_id, kind) DO UPDATE SET text = excluded.text",
            (game_id, round_no, seat_id, kind, text, time.time()),
        )
        self._db.commit()

    def statements(
        self, game_id: str, round_no: int, kind: str = "statement"
    ) -> dict[str, str]:
        rows = self._db.execute(
            "SELECT seat_id, text FROM statements WHERE game_id = ? AND round = ? AND kind = ?",
            (game_id, round_no, kind),
        )
        return {r["seat_id"]: r["text"] for r in rows}

    def record_vote(self, game_id: str, round_no: int, voter_seat: str, target: str) -> None:
        self._db.execute(
            "INSERT INTO votes (game_id, round, voter_seat, target, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(game_id, round, voter_seat) DO UPDATE SET target = excluded.target",
            (game_id, round_no, voter_seat, target, time.time()),
        )
        self._db.commit()

    def votes(self, game_id: str, round_no: int) -> dict[str, str]:
        rows = self._db.execute(
            "SELECT voter_seat, target FROM votes WHERE game_id = ? AND round = ?",
            (game_id, round_no),
        )
        return {r["voter_seat"]: r["target"] for r in rows}

    # ---- ledger -----------------------------------------------------------

    def record_relay(
        self, game_id: str, round_no: int, seat_id: str, original: str, relayed: str, cause: str
    ) -> None:
        """Log what was said versus what was delivered. Unused until Phase 3."""
        self._db.execute(
            "INSERT INTO ledger (game_id, round, seat_id, original, relayed, cause, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (game_id, round_no, seat_id, original, relayed, cause, time.time()),
        )
        self._db.commit()

    def ledger(self, game_id: str) -> list[sqlite3.Row]:
        return list(
            self._db.execute(
                "SELECT * FROM ledger WHERE game_id = ? ORDER BY round, id", (game_id,)
            )
        )


def _row_to_seat(row: sqlite3.Row) -> Seat:
    return Seat(
        conversation_id=row["conversation_id"],
        game_id=row["game_id"],
        codename=row["codename"],
        role=row["role"],
        alive=bool(row["alive"]),
        channel=row["channel"],
        connection_id=row["connection_id"],
        last_message_id=row["last_message_id"],
    )
