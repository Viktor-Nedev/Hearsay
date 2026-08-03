import json
import urllib.error

import pytest

from hearsay.tamper.gemini import GeminiRewriter
from hearsay.tamper.rewriter import build_rewriter, clean, guard, match_register
from hearsay.tamper.scripted import ScriptedRewriter


class TestClean:
    def test_strips_quotes(self):
        assert clean('"i was asleep"') == "i was asleep"

    def test_strips_code_fences(self):
        assert clean("```\ni was asleep\n```") == "i was asleep"

    def test_collapses_to_one_line(self):
        assert clean("i was\n\nasleep") == "i was asleep"

    def test_leaves_plain_text_alone(self):
        assert clean("i was asleep") == "i was asleep"


class TestGuard:
    def test_accepts_a_normal_rewrite(self):
        assert guard("i was asleep", "i saw Jade outside") == "i saw Jade outside"

    def test_rejects_empty(self):
        assert guard("i was asleep", "   ") is None

    def test_rejects_narration(self):
        # The failure that would tell five people the game is rigged.
        for bad in (
            "Sure! Here is the rewritten line: i saw Jade",
            "Here's the line: i saw Jade",
            "Rewritten: i saw Jade",
            "Okay, i saw Jade",
        ):
            assert guard("i was asleep", bad) is None, bad

    def test_rejects_runaway_length(self):
        assert guard("i was asleep", "x " * 200) is None

    def test_allows_growth_on_very_short_lines(self):
        # A three-character original must not force a three-character rewrite.
        assert guard("no", "i saw Jade near the door") is not None

    def test_rejects_an_unchanged_line(self):
        assert guard("i was asleep", "I WAS ASLEEP") is None


class TestMatchRegister:
    def test_keeps_all_lowercase(self):
        assert match_register("i was asleep", "I Saw Jade Outside") == "i saw jade outside"

    def test_keeps_shouting(self):
        assert match_register("I WAS ASLEEP", "i saw jade") == "I SAW JADE"

    def test_leaves_mixed_case_alone(self):
        assert match_register("I was asleep.", "I saw Jade.") == "I saw Jade."

    def test_drops_a_full_stop_the_speaker_never_uses(self):
        assert match_register("i was asleep", "i saw jade.") == "i saw jade"

    def test_handles_empty(self):
        assert match_register("", "anything") == "anything"


class TestScripted:
    def setup_method(self):
        self.r = ScriptedRewriter()

    def test_accusation_names_the_target(self):
        out = self.r.rewrite("i was asleep", "Ochre", "make it look like Jade left the room")
        assert "jade" in out.lower()
        assert out != "i was asleep"

    def test_never_accuses_the_speaker_themselves(self):
        out = self.r.rewrite("i was asleep", "Ochre", "make Ochre sound guilty")
        assert "ochre" not in out.lower()

    def test_is_deterministic(self):
        args = ("i was asleep", "Ochre", "point at Jade")
        assert self.r.rewrite(*args) == self.r.rewrite(*args)

    def test_preserves_lowercase_habit(self):
        out = self.r.rewrite("i was asleep the whole time", "Ochre", "point at Jade")
        assert out == out.lower()

    def test_subtle_drift_softens_a_claim(self):
        out = self.r.rewrite("i was asleep", "Ochre", "", subtle=True)
        assert out != "i was asleep"
        assert "think" in out or "honestly" in out

    def test_subtle_drift_introduces_nobody(self):
        out = self.r.rewrite("i saw nothing", "Ochre", "", subtle=True)
        assert "jade" not in out.lower() and "vermilion" not in out.lower()

    def test_inverts_a_denial_without_a_target(self):
        out = self.r.rewrite("i didn't leave the room", "Ochre", "make them sound guilty")
        assert out != "i didn't leave the room"

    def test_empty_input_stays_empty(self):
        assert self.r.rewrite("", "Ochre", "anything") == ""

    def test_drift_keeps_a_capitalised_speaker_capitalised(self):
        # "I think i was" is the seam a player notices in a game about authorship.
        out = self.r.rewrite("I was in the kitchen with Amber.", "Slate", "", subtle=True)
        assert "i was" not in out
        assert out == "I think I was in the kitchen with Amber."

    def test_drift_keeps_shouting(self):
        out = self.r.rewrite("I WAS NOWHERE NEAR IT", "Amber", "", subtle=True)
        assert out == "I THINK I WAS NOWHERE NEAR IT"

    def test_drift_keeps_lowercase_lowercase(self):
        assert self.r.rewrite("i was asleep", "Ochre", "", subtle=True) == "i think i was asleep"

    def test_inversion_repairs_negative_polarity(self):
        # Inverting "didn't see anyone" naively yields "did see anyone".
        out = self.r.rewrite("i didn't see anyone", "Jade", "no name here")
        assert out == "i did see someone"

    def test_longer_needles_win(self):
        # "i wasn't" must not be matched as bare "wasn't".
        out = self.r.rewrite("i wasn't there", "Jade", "", subtle=True)
        assert out.startswith("i don't think i was")

    def test_output_passes_its_own_guard(self):
        # Whatever the scripted backend emits must survive the same checks a
        # model's output does, or the fallback would be rejected downstream.
        for original in ("i was asleep", "I WAS HERE ALL NIGHT", "Nothing happened."):
            out = self.r.rewrite(original, "Ochre", "point at Jade")
            assert guard(original, out) is not None, original


class TestGeminiOffline:
    """The transport, without touching the network."""

    def _rewriter(self):
        return GeminiRewriter(api_key="test-key", model="gemini-2.5-flash")

    def test_requires_a_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            GeminiRewriter()

    def test_extract_concatenates_parts(self):
        # A thinking model puts the answer after its reasoning; parts[0] alone
        # comes back empty.
        data = {"candidates": [{"content": {"parts": [{"text": ""}, {"text": "i saw Jade"}]}}]}
        assert GeminiRewriter._extract(data) == "i saw Jade"

    def test_extract_handles_no_candidates(self):
        assert GeminiRewriter._extract({"candidates": []}) is None

    def test_extract_handles_empty_text(self):
        data = {"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}
        assert GeminiRewriter._extract(data) is None

    def test_falls_back_to_the_original_when_generation_fails(self, monkeypatch):
        r = self._rewriter()
        monkeypatch.setattr(r, "_generate", lambda *a, **k: None)
        assert r.rewrite("i was asleep", "Ochre", "point at Jade") == "i was asleep"

    def test_falls_back_when_the_guard_rejects(self, monkeypatch):
        r = self._rewriter()
        monkeypatch.setattr(r, "_generate", lambda *a, **k: "Sure! Here is the line: i saw Jade")
        assert r.rewrite("i was asleep", "Ochre", "point at Jade") == "i was asleep"

    def test_applies_register_to_a_good_rewrite(self, monkeypatch):
        r = self._rewriter()
        monkeypatch.setattr(r, "_generate", lambda *a, **k: "I Saw Jade Outside")
        assert r.rewrite("i was asleep", "Ochre", "point at Jade") == "i saw jade outside"

    def test_quota_error_asks_for_the_advertised_delay(self):
        r = self._rewriter()
        body = json.dumps({"error": {
            "status": "RESOURCE_EXHAUSTED",
            "message": "quota",
            "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                         "retryDelay": "23s"}],
        }}).encode()
        exc = urllib.error.HTTPError("u", 429, "quota", {}, _Body(body))
        assert r._handle_http_error(exc, attempt=0) == 20  # capped at MAX_RETRY_WAIT

    def test_permission_denied_gives_up_immediately(self):
        r = self._rewriter()
        body = json.dumps({"error": {"status": "PERMISSION_DENIED",
                                     "message": "denied"}}).encode()
        exc = urllib.error.HTTPError("u", 403, "denied", {}, _Body(body))
        assert r._handle_http_error(exc, attempt=0) is None

    def test_gives_up_on_the_last_attempt(self):
        r = self._rewriter()
        body = json.dumps({"error": {"status": "RESOURCE_EXHAUSTED"}}).encode()
        exc = urllib.error.HTTPError("u", 429, "quota", {}, _Body(body))
        assert r._handle_http_error(exc, attempt=2) is None


class TestBuildRewriter:
    def test_offline_when_asked(self):
        assert isinstance(build_rewriter(prefer_offline=True), ScriptedRewriter)

    def test_offline_without_a_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert isinstance(build_rewriter(), ScriptedRewriter)

    def test_gemini_when_a_key_is_present(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        assert isinstance(build_rewriter(), GeminiRewriter)


class _Body:
    """Minimal file-like for HTTPError, which json.load() reads.

    HTTPError treats the body as a temporary file and closes it on collection,
    so `close` has to exist or the GC raises during teardown.
    """

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self, *_):
        return self._payload

    def close(self):
        pass
