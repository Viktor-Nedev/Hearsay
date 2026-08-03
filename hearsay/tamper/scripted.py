"""Tampering without a model.

Every test and every `sim` run uses this, so the suite stays hermetic and fast
and a game is still playable when no API key works — which, on the key we were
given, is right now.

It is honestly worse than a model. It shifts meaning by surgery on the sentence
rather than by understanding it, and a suspicious player reading two rounds of it
will start to notice the seams. That is an acceptable floor, not the goal: the
point is that a quota error degrades the tampering instead of ending the game.
"""

from __future__ import annotations

import hashlib
import re

from hearsay.tamper.rewriter import match_register

#: Turning a denial into something that points at somebody else.
_ACCUSATIONS = [
    "i saw {target} slip out earlier",
    "{target} wasn't where they said they were",
    "ask {target} about the noise",
    "{target} came back looking rattled",
    "i'd be looking at {target}, not me",
]

#: Small drifts for ambient noise: enough to misread, not enough to accuse.
#: Longer needles first, so "i wasn't" is not eaten by "i was".
_HEDGES = [
    ("i wasn't", "i don't think i was"),
    ("i wasnt", "i dont think i was"),
    ("i didn't", "i'm fairly sure i didn't"),
    ("i didnt", "im fairly sure i didnt"),
    ("all night", "for most of it"),
    ("i was", "i think i was"),
    ("i saw", "i might have seen"),
    ("nothing", "not much"),
    ("everyone", "most of us"),
    ("never", "not that i remember"),
    ("definitely", "probably"),
]

_CODENAME = re.compile(r"\b([A-Z][a-z]{3,9})\b")


def _pick(options: list, seed: str):
    """Deterministic choice, so the same tamper twice gives the same line."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return options[digest[0] % len(options)]


def _splice(original: str, start: int, length: int, replacement: str) -> str:
    """Swap a span, keeping the capitalisation that was there.

    Replacing "I was" with a lowercase "i think i was" would quietly restyle the
    speaker — and capitalisation habits are exactly the tell this game is about.
    """
    was = original[start:start + length]
    letters = [c for c in was if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):
        replacement = replacement.upper()
    elif was[:1].isupper():
        replacement = replacement[:1].upper() + replacement[1:]
    return original[:start] + replacement + original[start + length:]


#: Negative-polarity words that stop making sense once the negation is removed.
#: "i didn't hear anything" inverted gives "i did hear anything" without these.
_POLARITY = [("anything", "something"), ("anyone", "someone"), ("anybody", "somebody")]


def _fix_polarity(text: str) -> str:
    """Repair the grammar an inversion breaks."""
    lowered = text.lower()
    for needle, replacement in _POLARITY:
        if needle in lowered:
            start = lowered.index(needle)
            return _splice(text, start, len(needle), replacement)
    return text


def _match_i_habit(result: str, original: str) -> str:
    """Follow the speaker on whether they capitalise a standalone "I".

    A spliced-in phrase can leave "I think i was", which nobody writes. In a game
    about whether a line sounds like its author, that is the visible seam.
    """
    if not re.search(r"\bI\b", original):
        return result
    return re.sub(r"\bi\b", "I", result)


class ScriptedRewriter:
    """Offline, deterministic, no network."""

    name = "scripted"

    def rewrite(
        self, original: str, speaker: str, instruction: str, *, subtle: bool = False
    ) -> str:
        original = (original or "").strip()
        if not original:
            return original

        if subtle:
            return self._drift(original, speaker)
        return self._accuse(original, speaker, instruction or "")

    # -- ambient noise ----------------------------------------------------

    def _drift(self, original: str, speaker: str) -> str:
        """Soften one claim. The speaker sounds less certain than they were."""
        lowered = original.lower()
        for needle, replacement in _HEDGES:
            if needle in lowered:
                start = lowered.index(needle)
                drifted = _splice(original, start, len(needle), replacement)
                return _match_i_habit(drifted, original)

        # Nothing to soften: add doubt at the front instead.
        return match_register(original, f"honestly, {original[0].lower()}{original[1:]}")

    # -- the impostor's rewrite -------------------------------------------

    def _accuse(self, original: str, speaker: str, instruction: str) -> str:
        """Point the sentence at whoever the instruction names."""
        target = self._target(instruction, speaker)
        if target:
            line = _pick(_ACCUSATIONS, f"{speaker}{original}{target}").format(target=target)
            return match_register(original, line)

        # No name to work with — invert the sentiment so a denial reads as an
        # admission of having been somewhere.
        return match_register(original, self._invert(original))

    def _target(self, instruction: str, speaker: str) -> str | None:
        for name in _CODENAME.findall(instruction):
            if name.lower() != speaker.lower():
                return name
        return None

    def _invert(self, original: str) -> str:
        lowered = original.lower()
        # Longest needles first so "i wasn't" is not matched as bare "wasn't",
        # and apostrophe-less spellings are listed because people type that way.
        for needle, replacement in (
            ("i was not", "i was"),
            ("i wasn't", "i was"),
            ("i wasnt", "i was"),
            ("i didn't", "i did"),
            ("i didnt", "i did"),
            ("wasn't", "was"),
            ("wasnt", "was"),
            ("didn't", "did"),
            ("didnt", "did"),
            ("nothing", "something"),
            ("no one", "someone"),
            ("nobody", "somebody"),
            ("anything", "something"),
        ):
            if needle in lowered:
                start = lowered.index(needle)
                inverted = _fix_polarity(_splice(original, start, len(needle), replacement))
                return _match_i_habit(inverted, original)
        return f"{original.rstrip('.')} — but i'd rather not go into it"
