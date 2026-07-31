"""Deciding whether an inbound message came from a human who is playing.

The gateway knows: every message it stores carries ``auto_generated``, and the
bounce we triggered on Day 1 is correctly flagged ``True``. The SDK's ``Message``
dataclass does not carry the field (see FIELDNOTES.md), so an ``on_message``
handler cannot tell a bounce from a player.

That matters more here than in most agents. Hearsay relays statements verbatim to
every other player. Left unfiltered, an out-of-office reply becomes:

    Ochre says: "I am currently out of the office and will reply on Monday."

and a bounce becomes a paragraph of SES diagnostics attributed to a human.

We filter cheaply first (sender and shape, no network) and only confirm against
the REST record when a message looks suspicious — one extra call on the rare
message rather than on every one.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Senders that are never players.
_ROBOT_SENDERS = re.compile(
    r"^(mailer-daemon|postmaster|no-?reply|do-?not-?reply|bounce|notification)s?@",
    re.IGNORECASE,
)

# Openings that betray an autoresponder rather than a person taking a turn.
_AUTO_OPENINGS = re.compile(
    r"^\s*(automatic reply|auto(matic)?[- ]?response|out of (the )?office|"
    r"an error occurred while trying to deliver|"
    r"delivery status notification|undeliverable|this is an automatically generated)",
    re.IGNORECASE,
)

_MAX_STATEMENT_CHARS = 2000


def looks_automated(sender: dict | None, text: str | None, subject: str | None = None) -> bool:
    """Cheap, offline verdict on whether a message came from a machine."""
    address = ((sender or {}).get("address") or "").strip()
    if address and _ROBOT_SENDERS.match(address):
        return True

    for candidate in (subject, text):
        if candidate and _AUTO_OPENINGS.match(candidate.strip()):
            return True

    return False


def is_playable(message, client=None) -> bool:
    """Whether this message should be treated as a player's turn.

    Pass ``client`` to confirm a suspicious message against the gateway's own
    ``auto_generated`` flag before discarding it, so a real player who happens to
    open with "Out of the office..." as a joke is not silently dropped.
    """
    if not (message.text or "").strip():
        return False

    if not looks_automated(message.sender, message.text, message.subject):
        return True

    if client is None:
        logger.info("dropping likely-automated message %s", message.id)
        return False

    # Suspicious: ask the gateway what it recorded.
    try:
        record = client._request("GET", f"/v1/messages/{message.id}")
    except Exception:
        logger.warning("could not confirm %s against REST; dropping", message.id, exc_info=True)
        return False

    auto = bool(record.get("auto_generated"))
    if auto:
        logger.info("gateway confirms %s is auto-generated; dropping", message.id)
    return not auto


def truncate_statement(text: str) -> str:
    """Keep a statement to something the other players will actually read."""
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= _MAX_STATEMENT_CHARS:
        return cleaned
    return cleaned[: _MAX_STATEMENT_CHARS - 1].rstrip() + "…"
