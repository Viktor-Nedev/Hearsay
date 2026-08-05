"""One effect, two surfaces.

These are the qualification rule expressed as assertions: the same `Deliver`
becomes a card with tappable buttons on Discord and a plain sentence in an
inbox, and the engine that produced it knows about neither.
"""

import random

import pytest

from hearsay.channels.render import KIND_VOTE, VOTE, enrich, vote_payload
from hearsay.engine.machine import apply
from hearsay.engine.rules import CODENAMES
from hearsay.engine.state import Deliver, GameState, Phase, Relay, Said, SeatView, Started
from hearsay.transport import Payload


def make_state(n: int = 4) -> GameState:
    seats = tuple(
        SeatView(id=f"c{i}", codename=CODENAMES[i], channel="discord") for i in range(n)
    )
    return GameState(game_id="g", code="ABCD", seats=seats, round=2, phase=Phase.VOTE)


class TestVotePayload:
    def test_one_button_per_candidate(self):
        state = make_state()
        payload = vote_payload(state, state.seat("c0"), "vote please")
        buttons = payload.blocks[0]["buttons"]
        assert [b["label"] for b in buttons] == ["Vermilion", "Slate", "Indigo"]

    def test_never_offers_a_button_for_yourself(self):
        state = make_state()
        payload = vote_payload(state, state.seat("c0"), "vote please")
        assert "Ochre" not in [b["label"] for b in payload.blocks[0]["buttons"]]

    def test_button_values_match_what_the_handler_parses(self):
        # app.on_interaction splits on the colon and routes the `vote` action.
        state = make_state()
        payload = vote_payload(state, state.seat("c0"), "vote please")
        for button in payload.blocks[0]["buttons"]:
            action, _, argument = button["value"].partition(":")
            assert action == VOTE
            assert state.by_codename(argument) is not None

    def test_skips_the_dead(self):
        state = make_state().kill("c1")
        payload = vote_payload(state, state.seat("c0"), "vote please")
        assert "Vermilion" not in [b["label"] for b in payload.blocks[0]["buttons"]]

    def test_text_survives_as_the_fallback(self):
        # A client that hides blocks must still see the instructions.
        state = make_state()
        payload = vote_payload(state, state.seat("c0"), "Reply with: VOTE <name>")
        assert "VOTE <name>" in payload.text

    def test_no_candidates_leaves_it_as_text(self):
        state = make_state(n=1)
        payload = vote_payload(state, state.seat("c0"), "vote please")
        assert payload.blocks is None


class TestEnrich:
    def test_buttons_for_a_channel_that_has_them(self):
        state = make_state()
        out = enrich(Payload("vote please"), KIND_VOTE, state, state.seat("c0"), True)
        assert out.blocks is not None

    def test_prose_for_a_channel_that_does_not(self):
        state = make_state()
        out = enrich(Payload("vote please"), KIND_VOTE, state, state.seat("c0"), False)
        assert out.blocks is None
        assert out.text == "vote please"

    def test_unmarked_payloads_pass_through_untouched(self):
        state = make_state()
        payload = Payload("here is what everyone said")
        assert enrich(payload, "", state, state.seat("c0"), True) is payload


class TestEngineMarksTheVote:
    """The engine labels the message; it does not know what a button is."""

    def _to_vote(self):
        state, _ = apply(make_state_fresh(), Started(), random.Random(0))
        for _ in range(2):
            for i in range(4):
                state, effects = apply(state, Said(f"c{i}", f"line {i}"))
            if state.phase is Phase.TAMPER:
                state, effects = apply(state, Relay(()))
        return state, effects

    def test_vote_prompts_are_marked(self):
        state, effects = self._to_vote()
        assert state.phase is Phase.VOTE
        votes = [e for e in effects if isinstance(e, Deliver) and e.kind == KIND_VOTE]
        assert len(votes) == len(state.alive)

    def test_nothing_else_is_marked(self):
        state, _ = apply(make_state_fresh(), Started(), random.Random(0))
        state, effects = apply(state, Said("c0", "just talking"))
        assert all(e.kind == "" for e in effects if isinstance(e, Deliver))


def make_state_fresh(n: int = 4) -> GameState:
    seats = tuple(
        SeatView(id=f"c{i}", codename=CODENAMES[i], channel="discord") for i in range(n)
    )
    return GameState(game_id="g", code="ABCD", seats=seats, honest=True)


@pytest.mark.parametrize("supports_buttons", [True, False])
def test_the_same_effect_reaches_both_channels(supports_buttons):
    """The rule, in one assertion: identical upstream, different arrival."""
    state = make_state()
    effect = Deliver("c0", Payload("Reply with: VOTE <name>"), kind=KIND_VOTE)
    out = enrich(effect.payload, effect.kind, state, state.seat("c0"), supports_buttons)
    assert "VOTE <name>" in out.text
    assert (out.blocks is not None) is supports_buttons
