"""Codenames, roles, and the arithmetic of who goes home.

Players get colours, not names. Three reasons, in order of importance:

1. Anonymity is the game. If you know Ana is on email you can reason about her
   channel; if you only know Ochre, you cannot.
2. The transcript stays readable and the demo video carries no personal data.
3. Phase 3 needs stable, unambiguous handles to rewrite — "make Ochre sound
   defensive" is a cleaner instruction than one built on a real name.
"""

from __future__ import annotations

import random
import secrets
import string

# Visually and phonetically distinct: no two start with the same letter, so a
# player can vote with a single word and never be ambiguous.
CODENAMES = [
    "Ochre",
    "Vermilion",
    "Slate",
    "Indigo",
    "Amber",
    "Jade",
    "Umber",
    "Rose",
    "Cobalt",
    "Fawn",
]

MIN_SEATS = 3
MAX_SEATS = len(CODENAMES)

WITNESS = "witness"
IMPOSTOR = "impostor"


def new_game_code(length: int = 4) -> str:
    """Short, unambiguous join code. No vowels (no accidental words), no 0/O/1/I."""
    alphabet = "BCDFGHJKLMNPQRSTVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def next_codename(taken: list[str]) -> str:
    """First free codename. Deterministic, so a rejoin gets a predictable seat."""
    used = {name.lower() for name in taken}
    for name in CODENAMES:
        if name.lower() not in used:
            return name
    raise ValueError(f"game is full: {MAX_SEATS} seats maximum")


def assign_roles(seat_ids: list[str], rng: random.Random | None = None) -> dict[str, str]:
    """Exactly one impostor; everyone else witnesses."""
    if len(seat_ids) < MIN_SEATS:
        raise ValueError(f"need at least {MIN_SEATS} players, got {len(seat_ids)}")
    rng = rng or random.Random()
    impostor = rng.choice(seat_ids)
    return {seat_id: (IMPOSTOR if seat_id == impostor else WITNESS) for seat_id in seat_ids}


def tally(votes: dict[str, str]) -> tuple[str | None, dict[str, int]]:
    """Count votes cast (voter -> target codename).

    Returns the eliminated codename and the full count. A tie eliminates nobody:
    deadlock is a real outcome and it keeps the impostor's job interesting.
    """
    counts: dict[str, int] = {}
    for target in votes.values():
        counts[target] = counts.get(target, 0) + 1
    if not counts:
        return None, counts

    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None, counts
    return ranked[0][0], counts


def winner(roles: dict[str, str], alive: list[str]) -> str | None:
    """Who has won, if anyone.

    Witnesses win the moment the impostor is out. The impostor wins on parity —
    once they are half the room, the vote can no longer remove them.
    """
    impostors = [s for s in alive if roles.get(s) == IMPOSTOR]
    if not impostors:
        return WITNESS
    if len(impostors) * 2 >= len(alive):
        return IMPOSTOR
    return None


def normalise_codename(raw: str) -> str:
    """Loosen matching so `vote OCHRE`, `Ochre.` and `ochre,` all land."""
    return raw.strip().strip(string.punctuation).title()
