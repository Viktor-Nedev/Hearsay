"""Turning whatever a human typed into something the engine understands.

The hard part is not the commands, it is email. A reply from an email client
carries the entire quoted thread underneath it, plus a signature, plus possibly
an "On Tuesday, Hearsay wrote:" attribution line. Left alone, a player who
replies "I agree" to a message that happened to contain the word VOTE would cast
a vote they never intended, and a statement would arrive with the whole previous
round stapled to it.

So every inbound message is cut down to what the human actually typed *now*
before anything looks at it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A line that is quoted material rather than new writing.
_QUOTE_MARKERS = re.compile(
    r"^\s*(>|On .{0,80}wrote:|-{2,}\s*Original Message|_{5,}|From:\s)",
    re.IGNORECASE,
)

# Signature delimiter, per RFC 3676: a line containing exactly "-- ".
_SIGNATURE = re.compile(r"^--\s*$")

# Common mobile/client sign-offs that are not part of the message.
_SENT_FROM = re.compile(r"^\s*(sent from my .{0,40}|get outlook for .{0,20})$", re.IGNORECASE)

_COMMANDS = {"join", "leave", "who", "start", "vote", "help", "tamper",
             "solve", "accuse"}


@dataclass(frozen=True)
class Intent:
    """What the player meant. `kind` is a command name or 'text'."""

    kind: str
    arg: str | None = None
    body: str = ""


def strip_quoted(raw: str) -> str:
    """Keep only the lines the sender wrote in this message.

    Stops at the first quote marker or signature delimiter, because everything
    after it belongs to an earlier message.
    """
    if not raw:
        return ""

    kept: list[str] = []
    for line in raw.replace("\r\n", "\n").split("\n"):
        if _QUOTE_MARKERS.match(line) or _SIGNATURE.match(line):
            break
        if _SENT_FROM.match(line):
            continue
        kept.append(line)

    return "\n".join(kept).strip()


def parse(raw: str) -> Intent:
    """Classify one inbound message.

    Commands are recognised only on the first line, and only as the first word.
    Anything else is free text — a statement, an accusation, a plain reply.
    """
    body = strip_quoted(raw or "")
    if not body:
        return Intent(kind="empty", body="")

    first_line = body.split("\n", 1)[0].strip()
    word, _, rest = first_line.partition(" ")
    keyword = word.strip().strip(":,.!").lower()

    if keyword in _COMMANDS:
        arg = rest.strip() or None
        return Intent(kind=keyword, arg=arg, body=body)

    return Intent(kind="text", body=body)
