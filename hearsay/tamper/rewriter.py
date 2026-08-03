"""The rewriter interface, the guards around it, and how one gets chosen.

A rewrite runs inside a live game with people waiting on it, so the contract is
deliberately forgiving: `rewrite()` never raises and never blocks forever. If the
model is unreachable, out of quota, or produces something unusable, the caller
gets the original text back. A tamper that quietly does nothing costs the
impostor a turn. A tamper that emits "Sure! Here is the rewritten line:" tells
five people the game is rigged.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

#: A rewrite that runs away with itself stops sounding like the speaker.
MAX_GROWTH = 2.5
MIN_CHARS = 2

#: Openings that betray a model narrating its own task rather than doing it.
_META = re.compile(
    r"^\s*(sure|certainly|okay|ok|here('s| is)|rewritten|revised|new version|"
    r"as requested|i('ve| have) rewritten|output|result)\b[:,]?",
    re.IGNORECASE,
)

#: Models sometimes wrap output in quotes or code fences even when told not to.
_WRAPPERS = [
    (re.compile(r"^```[a-z]*\s*|\s*```$", re.IGNORECASE), ""),
    (re.compile(r'^\s*["“‘\'](.*)["”’\']\s*$', re.DOTALL), r"\1"),
]


@runtime_checkable
class Rewriter(Protocol):
    """Turns one line into a different line that the same person might have said."""

    def rewrite(
        self, original: str, speaker: str, instruction: str, *, subtle: bool = False
    ) -> str: ...


def clean(candidate: str) -> str:
    """Strip the packaging a model puts around an answer."""
    text = (candidate or "").strip()
    for pattern, replacement in _WRAPPERS:
        text = pattern.sub(replacement, text).strip()
    # Collapse to one line: a statement is one line, and a model that returns
    # three has misunderstood the job.
    return " ".join(text.split())


def guard(original: str, candidate: str) -> str | None:
    """Accept a rewrite, or return None to say fall back to the original."""
    text = clean(candidate)

    if len(text) < MIN_CHARS:
        logger.info("rewrite rejected: empty")
        return None

    if _META.match(text):
        logger.info("rewrite rejected: model narrated the task — %r", text[:60])
        return None

    limit = max(MIN_CHARS * 20, int(len(original) * MAX_GROWTH))
    if len(text) > limit:
        logger.info("rewrite rejected: %d chars against a %d limit", len(text), limit)
        return None

    if text.strip().lower() == original.strip().lower():
        logger.info("rewrite rejected: identical to the original")
        return None

    return text


def match_register(original: str, rewritten: str) -> str:
    """Carry over the speaker's typing habits.

    Somebody who never capitalises is recognisable by that alone. A rewrite in
    tidy sentence case reads as somebody else even when the words are right, and
    in a game about authorship that is the whole ballgame.
    """
    if not original or not rewritten:
        return rewritten

    letters = [c for c in original if c.isalpha()]
    if letters and all(c.islower() for c in letters):
        rewritten = rewritten.lower()
    elif letters and all(c.isupper() for c in letters):
        rewritten = rewritten.upper()

    # Match terminal punctuation: a habitual full stop, or a habitual lack of one.
    if original.rstrip() and rewritten.rstrip():
        if original.rstrip()[-1] not in ".!?" and rewritten.rstrip()[-1] in ".":
            rewritten = rewritten.rstrip().rstrip(".")

    return rewritten


def build_rewriter(prefer_offline: bool = False) -> Rewriter:
    """Pick a backend: Gemini when the key works, the scripted one otherwise.

    Never raises. A missing or broken key downgrades the tampering rather than
    stopping the game — `spike/llm_probe.py` says which case you are in.
    """
    from hearsay.tamper.scripted import ScriptedRewriter

    if prefer_offline or not os.environ.get("GEMINI_API_KEY"):
        return ScriptedRewriter()

    try:
        from hearsay.tamper.gemini import GeminiRewriter

        return GeminiRewriter()
    except Exception:
        logger.warning("Gemini unavailable; falling back to scripted", exc_info=True)
        return ScriptedRewriter()
