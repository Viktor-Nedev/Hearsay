import random

from hearsay.engine.machine import apply
from hearsay.engine.rules import CODENAMES, IMPOSTOR, WITNESS
from hearsay.engine.state import (
    Deliver,
    GameState,
    LogRelay,
    Phase,
    Relay,
    Said,
    SeatView,
    SetDeadline,
    Started,
    Timeout,
    Voted,
)


def make_state(n: int = 4, honest: bool = True) -> GameState:
    seats = tuple(
        SeatView(id=f"c{i}", codename=CODENAMES[i], channel="discord") for i in range(n)
    )
    return GameState(game_id="g1", code="ABCD", seats=seats, honest=honest)


def to(effects, seat_id: str) -> list[str]:
    return [e.payload.text for e in effects if isinstance(e, Deliver) and e.seat_id == seat_id]


def all_text(effects) -> str:
    return "\n".join(e.payload.text for e in effects if isinstance(e, Deliver))


def everyone_says(state, effects=None, text="something"):
    """Drive a whole collection phase to completion."""
    effects = list(effects or [])
    for seat in state.alive:
        state, new = apply(state, Said(seat.id, f"{seat.codename} says {text}"))
        effects.extend(new)
    return state, effects


def everyone_votes(state, target_of):
    effects = []
    for seat in state.alive:
        state, new = apply(state, Voted(seat.id, target_of(seat)))
        effects.extend(new)
    return state, effects


class TestStart:
    def test_deals_exactly_one_impostor(self):
        state, _ = apply(make_state(), Started(), random.Random(0))
        roles = [s.role for s in state.seats]
        assert roles.count(IMPOSTOR) == 1
        assert roles.count(WITNESS) == 3

    def test_opens_statements_at_round_one(self):
        state, _ = apply(make_state(), Started(), random.Random(0))
        assert state.phase is Phase.STATEMENT
        assert state.round == 1

    def test_briefs_and_prompts_everyone(self):
        state, effects = apply(make_state(), Started(), random.Random(0))
        for seat in state.seats:
            messages = to(effects, seat.id)
            assert len(messages) == 2, f"{seat.codename} got {len(messages)}"
            assert "You are" in messages[0]
            assert "Reply with" in messages[1]

    def test_only_the_impostor_is_told_they_are_the_impostor(self):
        state, effects = apply(make_state(), Started(), random.Random(0))
        told = [s for s in state.seats if "you are the impostor" in to(effects, s.id)[0].lower()]
        assert len(told) == 1
        assert told[0].role == IMPOSTOR

    def test_refuses_below_minimum(self):
        state, effects = apply(make_state(n=2), Started(), random.Random(0))
        assert state.phase is Phase.LOBBY
        assert "1 more" in all_text(effects)

    def test_sets_a_deadline(self):
        _, effects = apply(make_state(), Started(), random.Random(0))
        assert any(isinstance(e, SetDeadline) and e.phase is Phase.STATEMENT for e in effects)

    def test_starting_twice_is_a_no_op(self):
        state, _ = apply(make_state(), Started(), random.Random(0))
        again, effects = apply(state, Started(), random.Random(1))
        assert again is state
        assert effects == []


class TestStatements:
    def setup_method(self):
        self.state, _ = apply(make_state(), Started(), random.Random(0))

    def test_records_and_acknowledges(self):
        state, effects = apply(self.state, Said("c0", "it wasn't me"))
        assert state.said("c0") == "it wasn't me"
        assert "Waiting on 3 more" in to(effects, "c0")[0]

    def test_does_not_relay_early(self):
        state, effects = apply(self.state, Said("c0", "hello"))
        assert state.phase is Phase.STATEMENT
        assert to(effects, "c1") == []

    def test_relays_once_everyone_has_spoken(self):
        state, effects = everyone_says(self.state)
        assert state.phase is Phase.DELIBERATE
        transcript = [m for m in to(effects, "c1") if "Here is what everyone said" in m]
        assert len(transcript) == 1

    def test_transcript_carries_every_speaker(self):
        state, effects = everyone_says(self.state)
        transcript = next(m for m in to(effects, "c0") if "Here is what everyone said" in m)
        for seat in state.alive:
            assert seat.codename in transcript

    def test_a_player_sees_their_own_line(self):
        # Deliberate: when tampering lands, the victim must be able to read their
        # own words altered. "I never said that" only exists if you can see it.
        _, effects = everyone_says(self.state)
        transcript = next(m for m in to(effects, "c0") if "Here is what everyone said" in m)
        assert "Ochre says something" in transcript

    def test_restating_overwrites(self):
        state, _ = apply(self.state, Said("c0", "first"))
        state, _ = apply(state, Said("c0", "second"))
        assert state.said("c0") == "second"

    def test_logs_a_clean_ledger_row_per_speaker(self):
        _, effects = everyone_says(self.state)
        rows = [e for e in effects if isinstance(e, LogRelay)]
        assert len(rows) == 4
        assert all(r.cause == "clean" and r.original == r.relayed for r in rows)

    def test_unknown_seat_is_ignored(self):
        state, effects = apply(self.state, Said("nobody", "hi"))
        assert state is self.state
        assert effects == []


class TestDeliberation:
    def setup_method(self):
        state, _ = apply(make_state(), Started(), random.Random(0))
        self.state, _ = everyone_says(state)

    def test_follows_statements(self):
        assert self.state.phase is Phase.DELIBERATE

    def test_second_relay_opens_voting(self):
        state, effects = everyone_says(self.state, text="again")
        assert state.phase is Phase.VOTE
        assert "VOTE" in to(effects, "c0")[-1]

    def test_vote_prompt_omits_the_reader(self):
        state, effects = everyone_says(self.state, text="again")
        prompt = to(effects, "c0")[-1]
        assert "Ochre" not in prompt.split("Still in:")[1].split("**Reply")[0]


class TestVoting:
    def setup_method(self):
        state, _ = apply(make_state(), Started(), random.Random(0))
        state, _ = everyone_says(state)
        self.state, _ = everyone_says(state, text="again")

    def test_records_a_vote(self):
        state, effects = apply(self.state, Voted("c0", "Vermilion"))
        assert state.voted("c0") == "Vermilion"
        assert "Vote recorded: Vermilion" in to(effects, "c0")[0]

    def test_accepts_sloppy_input(self):
        state, _ = apply(self.state, Voted("c0", "  vermilion. "))
        assert state.voted("c0") == "Vermilion"

    def test_rejects_unknown_name(self):
        state, effects = apply(self.state, Voted("c0", "Mauve"))
        assert state.voted("c0") is None
        assert "nobody called Mauve" in to(effects, "c0")[0]

    def test_rejects_self_vote(self):
        state, effects = apply(self.state, Voted("c0", "Ochre"))
        assert state.voted("c0") is None
        assert "can't vote for yourself" in to(effects, "c0")[0]

    def test_eliminates_on_majority(self):
        state, _ = everyone_votes(self.state, lambda s: "Vermilion" if s.id != "c1" else "Ochre")
        assert state.seat("c1").alive is False

    def test_tie_eliminates_nobody(self):
        pairs = {"c0": "Vermilion", "c1": "Ochre", "c2": "Indigo", "c3": "Slate"}
        state, effects = everyone_votes(self.state, lambda s: pairs[s.id])
        assert all(s.alive for s in state.seats)
        assert "Nobody had a majority" in all_text(effects)

    def test_reveal_never_states_the_role(self):
        _, effects = everyone_votes(
            self.state, lambda s: "Vermilion" if s.id != "c1" else "Ochre"
        )
        reveal = next(m for m in to(effects, "c0") if "The vote fell on" in m)
        assert "witness" not in reveal.lower()
        assert "impostor" not in reveal.lower()

    def test_next_round_opens_clean(self):
        state, _ = everyone_votes(self.state, lambda s: "Vermilion" if s.id != "c1" else "Ochre")
        assert state.phase is Phase.STATEMENT
        assert state.round == 2
        assert state.statements == () and state.votes == ()

    def test_dead_player_cannot_vote(self):
        state, _ = everyone_votes(self.state, lambda s: "Vermilion" if s.id != "c1" else "Ochre")
        _, effects = apply(state, Voted("c1", "Ochre"))
        assert "can't speak" in to(effects, "c1")[0]

    def test_dead_player_cannot_speak(self):
        state, _ = everyone_votes(self.state, lambda s: "Vermilion" if s.id != "c1" else "Ochre")
        _, effects = apply(state, Said("c1", "let me back in"))
        assert "can't speak" in to(effects, "c1")[0]

    def test_prose_during_voting_is_redirected(self):
        _, effects = apply(self.state, Said("c0", "I think it's Slate"))
        assert "VOTE" in to(effects, "c0")[0]

    def test_vote_outside_voting_phase_is_refused(self):
        state, _ = apply(make_state(), Started(), random.Random(0))
        _, effects = apply(state, Voted("c0", "Vermilion"))
        assert "isn't voting time" in to(effects, "c0")[0]


class TestWinning:
    def _play_until_over(self, seed: int = 0, n: int = 4):
        state, _ = apply(make_state(n), Started(), random.Random(seed))
        impostor = state.impostor
        guard = 0
        while state.phase is not Phase.GAMEOVER and guard < 20:
            guard += 1
            if state.phase in (Phase.STATEMENT, Phase.DELIBERATE):
                state, _ = everyone_says(state)
            elif state.phase is Phase.VOTE:
                # Everyone hunts the impostor; the impostor votes for anyone else.
                def pick(seat):
                    if seat.id == impostor.id:
                        return next(s.codename for s in state.alive if s.id != seat.id)
                    return impostor.codename

                state, _ = everyone_votes(state, pick)
        return state, impostor

    def test_witnesses_win_when_impostor_is_voted_out(self):
        state, impostor = self._play_until_over()
        assert state.phase is Phase.GAMEOVER
        assert state.winner == WITNESS
        assert not state.seat(impostor.id).alive

    def test_impostor_wins_at_parity(self):
        state, _ = apply(make_state(3), Started(), random.Random(0))
        impostor = state.impostor
        victim = next(s for s in state.alive if s.id != impostor.id)
        state, _ = everyone_says(state)
        state, _ = everyone_says(state, text="again")

        # The victim cannot vote for themselves, so they vote the impostor —
        # and still lose 2-1. Three players, one out, parity reached.
        state, effects = everyone_votes(
            state,
            lambda s: victim.codename if s.id != victim.id else impostor.codename,
        )
        assert state.phase is Phase.GAMEOVER
        assert state.winner == IMPOSTOR
        assert "impostor wins" in all_text(effects).lower()

    def test_game_over_names_the_impostor_to_everyone(self):
        state, _ = apply(make_state(3), Started(), random.Random(0))
        impostor = state.impostor
        victim = next(s for s in state.alive if s.id != impostor.id)
        state, _ = everyone_says(state)
        state, _ = everyone_says(state, text="again")
        state, effects = everyone_votes(
            state,
            lambda s: victim.codename if s.id != victim.id else impostor.codename,
        )
        # Only now, with the game finished, is the role made public.
        for seat in state.seats:
            final = to(effects, seat.id)[-1]
            assert "was the impostor" in final
            assert impostor.codename in final


class TestLeakInvariants:
    """The two properties the whole premise rests on.

    Both are asserted against the exact phrases that attribute a role, not
    against the bare word "impostor" — the vote prompt legitimately says "one of
    you is the impostor" to everyone, which is framing rather than a leak.
    """

    #: The only two sentences in the game that tie a role to a person.
    OWN_ROLE = "You are the impostor"
    PUBLIC_ROLE = "was the impostor"

    def test_only_the_impostor_is_told_their_role(self):
        state, effects = apply(make_state(5), Started(), random.Random(3))
        impostor = state.impostor

        told = [s.id for s in state.seats
                if any(self.OWN_ROLE in m for m in to(effects, s.id))]
        assert told == [impostor.id]

    def test_role_stays_secret_through_a_whole_round(self):
        state, _ = apply(make_state(5), Started(), random.Random(3))

        collected = []
        state, e = everyone_says(state)
        collected += e
        state, e = everyone_says(state, text="again")
        collected += e
        state, e = everyone_votes(state, lambda s: "Amber" if s.codename != "Amber" else "Ochre")
        collected += e

        assert state.phase is not Phase.GAMEOVER, "expected the game to continue"
        for seat in state.seats:
            for message in to(collected, seat.id):
                assert self.PUBLIC_ROLE not in message
                assert self.OWN_ROLE not in message

    def test_a_seat_only_receives_words_the_machine_relayed(self):
        state, _ = apply(make_state(4), Started(), random.Random(1))

        # One player says something carrying a distinctive marker.
        secret = "PINEAPPLE-9134"
        state, effects = apply(state, Said("c0", f"my alibi is {secret}"))

        # Before the relay, nobody else has seen it.
        for seat_id in ("c1", "c2", "c3"):
            assert all(secret not in m for m in to(effects, seat_id))

        # Only the remaining players speak, so c0's line stays as written.
        collected = []
        for seat_id in ("c1", "c2", "c3"):
            state, e = apply(state, Said(seat_id, "nothing to add"))
            collected += e

        # It reaches them exactly once, inside the transcript the machine built.
        relayed = [m for m in to(collected, "c1") if secret in m]
        assert len(relayed) == 1
        assert "Here is what everyone said" in relayed[0]


class TestTampering:
    """The twist: one round, told differently to different people."""

    def _to_statements(self, n=4):
        state, _ = apply(make_state(n, honest=False), Started(), random.Random(0))
        for i in range(n):
            state, effects = apply(state, Said(f"c{i}", f"seat {i} original line"))
        return state, effects

    def test_statements_stop_at_the_tamper_phase(self):
        state, _ = self._to_statements()
        assert state.phase is Phase.TAMPER

    def test_only_the_impostor_is_offered_the_choice(self):
        state, effects = self._to_statements()
        offered = [s.id for s in state.seats
                   if any("TAMPER" in m for m in to(effects, s.id))]
        assert offered == [state.impostor.id]

    def test_nothing_is_relayed_before_the_impostor_decides(self):
        _, effects = self._to_statements()
        assert "Here is what everyone said" not in all_text(effects)

    def test_the_author_reads_their_own_words(self):
        """The invariant the whole twist rests on."""
        state, _ = self._to_statements()
        state, effects = apply(state, Relay((("c0", "i saw Slate slip out", "impostor"),)))

        own = next(m for m in to(effects, "c0") if "Here is what everyone said" in m)
        assert "seat 0 original line" in own
        assert "i saw Slate slip out" not in own

    def test_everyone_else_reads_the_rewrite(self):
        state, _ = self._to_statements()
        state, effects = apply(state, Relay((("c0", "i saw Slate slip out", "impostor"),)))

        for seat_id in ("c1", "c2", "c3"):
            theirs = next(m for m in to(effects, seat_id) if "Here is what everyone said" in m)
            assert "i saw Slate slip out" in theirs
            assert "seat 0 original line" not in theirs

    def test_untouched_speakers_are_identical_for_everyone(self):
        state, _ = self._to_statements()
        state, effects = apply(state, Relay((("c0", "changed", "impostor"),)))
        for seat_id in ("c0", "c1", "c2", "c3"):
            theirs = next(m for m in to(effects, seat_id) if "Here is what everyone said" in m)
            assert "seat 2 original line" in theirs

    def test_ledger_records_one_row_per_speaker(self):
        state, _ = self._to_statements()
        _, effects = apply(state, Relay((("c0", "changed", "impostor"),)))
        rows = [e for e in effects if isinstance(e, LogRelay)]
        assert len(rows) == 4
        tampered = [r for r in rows if r.cause == "impostor"]
        assert len(tampered) == 1
        assert tampered[0].original == "seat 0 original line"
        assert tampered[0].relayed == "changed"

    def test_noise_is_logged_separately(self):
        state, _ = self._to_statements()
        _, effects = apply(state, Relay((("c1", "drifted", "noise"),)))
        causes = {e.cause for e in effects if isinstance(e, LogRelay)}
        assert causes == {"clean", "noise"}

    def test_skipping_relays_everything_untouched(self):
        state, _ = self._to_statements()
        state, effects = apply(state, Relay(()))
        assert state.phase is Phase.DELIBERATE
        assert all(e.cause == "clean" for e in effects if isinstance(e, LogRelay))

    def test_a_silent_impostor_does_not_stall_the_game(self):
        state, _ = self._to_statements()
        state, effects = apply(state, Timeout(Phase.TAMPER))
        assert state.phase is Phase.DELIBERATE
        assert "Here is what everyone said" in all_text(effects)

    def test_rewrites_do_not_leak_into_the_deliberation_relay(self):
        state, _ = self._to_statements()
        state, _ = apply(state, Relay((("c0", "changed", "impostor"),)))
        assert state.rewrites == ()

        for i in range(4):
            state, effects = apply(state, Said(f"c{i}", f"seat {i} second line"))
        theirs = next(m for m in to(effects, "c1") if "Last words before the vote" in m)
        assert "changed" not in theirs
        assert "seat 0 second line" in theirs

    def test_honest_mode_never_opens_the_tamper_phase(self):
        state, _ = apply(make_state(4, honest=True), Started(), random.Random(0))
        for i in range(4):
            state, effects = apply(state, Said(f"c{i}", f"line {i}"))
        assert state.phase is Phase.DELIBERATE
        assert "TAMPER" not in all_text(effects)

    def test_relay_event_outside_the_tamper_phase_is_ignored(self):
        state, _ = apply(make_state(4, honest=False), Started(), random.Random(0))
        after, effects = apply(state, Relay((("c0", "changed", "impostor"),)))
        assert after is state
        assert effects == []


class TestTimeout:
    def setup_method(self):
        self.state, _ = apply(make_state(), Started(), random.Random(0))

    def test_relays_with_partial_answers(self):
        state, _ = apply(self.state, Said("c0", "only me"))
        state, effects = apply(state, Timeout(Phase.STATEMENT))
        assert state.phase is Phase.DELIBERATE
        assert "only me" in all_text(effects)

    def test_stale_timer_is_ignored(self):
        # A timer set for statements must not fire once voting has begun.
        state, _ = everyone_says(self.state)
        state, _ = everyone_says(state, text="again")
        after, effects = apply(state, Timeout(Phase.STATEMENT))
        assert after is state
        assert effects == []

    def test_silent_phase_advances_rather_than_stalling(self):
        state, effects = apply(self.state, Timeout(Phase.STATEMENT))
        assert state.phase is Phase.DELIBERATE
        assert "Here is what everyone said" not in all_text(effects)
