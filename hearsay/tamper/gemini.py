"""Gemini backend for the rewriter.

Written against what the API actually does rather than what the docs suggest,
because the two differed at every step (see FIELDNOTES.md):

* auth is `x-goog-api-key` or `?key=`; an `Authorization: Bearer` header is a 401
* a key that lists models happily can still 403 every one of them
* 429 carries a `RetryInfo` detail with a usable `retryDelay`
* text can land in a later element of `content.parts`, so reading `parts[0]`
  silently yields an empty string on thinking models

Nothing here raises into a running game. Every failure path returns the original
line and logs why.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request

from hearsay.tamper.rewriter import clean, guard, match_register

logger = logging.getLogger(__name__)

BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash"
TIMEOUT = 30
MAX_ATTEMPTS = 2
MAX_RETRY_WAIT = 20

#: After a quota refusal, stop asking for a while. The free tier's limit is not
#: a burst that clears in the advertised twenty seconds — it stays exhausted, so
#: retrying every rewrite costs forty seconds each and still fails. A live game
#: with people waiting cannot afford that, so the first refusal opens a circuit
#: and everything goes straight to the fallback until it closes again.
COOLDOWN_SECONDS = 180

SYSTEM = """You rewrite a single line of dialogue in a social deduction game.

The line was written by a player called {speaker}. You will be told what the \
rewrite should make them appear to say. Produce that line as {speaker} would \
have written it.

Rules, in order of importance:
1. Output ONLY the rewritten line. No preamble, no quotes, no explanation.
2. Keep their voice: same register, same capitalisation habits, same punctuation \
quirks, same typos if they have them. If they write in lowercase, so do you.
3. Keep it about the same length. Never more than twice as long.
4. Never mention rewriting, editing, the game, or yourself.
5. It must be sayable by a real person mid-argument. No narration."""

SUBTLE_NOTE = """
This one is a small drift, not an accusation. Shift the certainty or a detail \
just enough that a careful reader would take a different meaning. Do not \
introduce new people or events."""


class GeminiRewriter:
    """Calls Gemini; falls back to the original line on any failure."""

    name = "gemini"

    def __init__(
        self, api_key: str | None = None, model: str | None = None, fallback=None
    ) -> None:
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self.model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL

        # Falling back to the *original line* would mean the impostor's turn
        # silently did nothing, and the game would quietly become honest mode —
        # which is the one thing worth watching, gone. A cruder rewrite is much
        # better than no rewrite, so a quota failure degrades quality instead of
        # removing the mechanic.
        if fallback is None:
            from hearsay.tamper.scripted import ScriptedRewriter

            fallback = ScriptedRewriter()
        self.fallback = fallback
        self._cooldown_until = 0.0

    # -- the interface ----------------------------------------------------

    def rewrite(
        self, original: str, speaker: str, instruction: str, *, subtle: bool = False
    ) -> str:
        original = (original or "").strip()
        if not original:
            return original

        system = SYSTEM.format(speaker=speaker) + (SUBTLE_NOTE if subtle else "")
        prompt = (
            f"{speaker} wrote:\n{original}\n\n"
            f"Make it appear they said: {instruction or 'something subtly different'}\n\n"
            f"The rewritten line:"
        )

        raw = self._generate(system, prompt)
        if raw is None:
            return self._degrade(original, speaker, instruction, subtle)

        accepted = guard(original, raw)
        if accepted is None:
            return self._degrade(original, speaker, instruction, subtle)

        return match_register(original, accepted)

    def _degrade(self, original: str, speaker: str, instruction: str, subtle: bool) -> str:
        if self.fallback is None:
            return original
        return self.fallback.rewrite(original, speaker, instruction, subtle=subtle)

    # -- transport --------------------------------------------------------

    def _generate(self, system: str, prompt: str) -> str | None:
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 1.0, "maxOutputTokens": 2048},
        }
        if time.time() < self._cooldown_until:
            logger.debug("gemini cooling down; using the fallback")
            return None

        body = json.dumps(payload).encode()

        for attempt in range(MAX_ATTEMPTS):
            request = urllib.request.Request(
                f"{BASE}/models/{self.model}:generateContent",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self.api_key,
                    "User-Agent": "hearsay/0.1",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                    return self._extract(json.load(response))
            except urllib.error.HTTPError as exc:
                retry_after = self._handle_http_error(exc, attempt)
                if retry_after is None:
                    return None
                time.sleep(retry_after)
            except Exception:
                logger.warning("gemini call failed", exc_info=True)
                return None

        logger.warning("gemini still rate-limited after %d attempts", MAX_ATTEMPTS)
        return None

    def _handle_http_error(self, exc: urllib.error.HTTPError, attempt: int) -> float | None:
        """Returns seconds to wait, or None to give up."""
        try:
            error = json.load(exc).get("error", {})
        except Exception:
            error = {}
        status = error.get("status", str(exc.code))

        if exc.code == 429 and attempt < MAX_ATTEMPTS - 1:
            delay = 5.0
            for detail in error.get("details", []):
                if detail.get("@type", "").endswith("RetryInfo"):
                    raw = str(detail.get("retryDelay", "5s")).rstrip("s")
                    try:
                        delay = float(raw) + 1
                    except ValueError:
                        pass
            wait = min(delay, MAX_RETRY_WAIT)
            logger.info("gemini quota hit; retrying in %.0fs", wait)
            return wait

        if exc.code == 429:
            self._cooldown_until = time.time() + COOLDOWN_SECONDS
            logger.warning("gemini out of quota; falling back for %ds", COOLDOWN_SECONDS)
            return None

        logger.warning("gemini %s: %s", status, error.get("message", "")[:100])
        return None

    @staticmethod
    def _extract(data: dict) -> str | None:
        candidates = data.get("candidates") or []
        if not candidates:
            logger.info("gemini returned no candidates (%s)", data.get("promptFeedback"))
            return None

        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts") or []
        # Concatenate every part: thinking models put the answer after their
        # reasoning, and parts[0] alone comes back empty.
        text = clean("".join(part.get("text", "") for part in parts))

        if not text:
            logger.info("gemini returned empty text (finish=%s)", candidate.get("finishReason"))
            return None
        return text
