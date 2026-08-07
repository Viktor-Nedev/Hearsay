"""The casefile loop, and the property it exists for.

`TestNobodySeesAnotherHand` is the one that matters. Every other test here
checks that the game works; that one checks it is still a game.
"""

import pytest

from hearsay.casefile.case import deal, find_case
from hearsay.casefile.engine import HINT_AFTER, Answered, CaseState, LogAnswer, apply
from hearsay.engine.rules import CODENAMES
from hearsay.engine.state import Deliver, SeatView, Started


@pytest.fixture(scope="module")
def case():
    return find_case("ashford")


def make_state(case, seats: int = 3) -> CaseState:
    return CaseState(
        game_id="g1",
        code="ABCD",
        case=case,
        seats=tuple(
            SeatView(id=f"c{i}", codename=CODENAMES[i], channel="email")
            for i in range(seats)
        ),
    )


def to(effects, seat_id: str) -> list[str]:
    return [e.payload.text for e in effects if isinstance(e, Deliver) and e.seat_id == seat_id]


def all_text(effects) -> str:
    return "\n".join(e.payload.text for e in effects if isinstance(e, Deliver))


def started(case, seats: int = 3):
    return apply(make_state(case, seats), Started())


def solve(state, text, seat="c0", accusing=False):
    return apply(state, Answered(seat, text, accusing=accusing))


class TestOpening:
    def test_start_deals_to_everyone(self, case):
        state, effects = started(case)
        assert state.started
        for i in range(3):
            assert len(to(effects, f"c{i}")) == 1

    def test_the_dossier_carries_the_case(self, case):
        _, effects = started(case)
        assert "The Ashford Vigil" in to(effects, "c0")[0]
        assert "SOLVE" in to(effects, "c0")[0]

    def test_starting_twice_does_nothing(self, case):
        state, _ = started(case)
        again, effects = apply(state, Started())
        assert again is state
        assert effects == []

    def test_answering_before_the_start(self, case):
        _, effects = solve(make_state(case), "10:53")
        assert "hasn't been opened" in to(effects, "c0")[0]

    def test_a_seat_with_no_evidence_is_told_to_ask(self, case):
        # Eight investigators, five exhibits: three hands are empty.
        _, effects = started(case, seats=8)
        empty = [t for i in range(8) for t in to(effects, f"c{i}") if "dealt nothing" in t]
        assert empty


class TestAnswering:
    def test_a_wrong_answer_is_refused(self, case):
        state, _ = started(case)
        after, effects = solve(state, "11:04")
        assert after.stage_index == 0
        assert after.attempts == 1
        assert "not it" in to(effects, "c0")[0]

    def test_everyone_hears_a_wrong_answer(self, case):
        state, _ = started(case)
        _, effects = solve(state, "11:04")
        for i in range(3):
            assert to(effects, f"c{i}"), f"c{i} heard nothing"

    def test_the_hint_arrives_after_three_attempts(self, case):
        state, _ = started(case)
        for _ in range(HINT_AFTER - 1):
            state, effects = solve(state, "wrong")
            assert case.stages[0].hint not in all_text(effects)
        state, effects = solve(state, "wrong again")
        assert case.stages[0].hint in all_text(effects)

    def test_a_correct_answer_advances(self, case):
        state, _ = started(case)
        after, effects = solve(state, "10:53")
        assert after.stage_index == 1
        assert after.attempts == 0
        assert "that is it" in all_text(effects).lower()

    def test_sloppy_answers_are_accepted(self, case):
        state, _ = started(case)
        assert solve(state, " Ten Fifty-Three ")[0].stage_index == 1

    def test_an_empty_answer_asks_again(self, case):
        state, _ = started(case)
        _, effects = solve(state, "   ")
        assert "SOLVE <your answer>" in to(effects, "c0")[0]

    def test_answers_are_logged_against_the_stage_they_answered(self, case):
        """A correct answer advances before effects run.

        If the log did not carry its own stage the driver would file it against
        the stage after the one it solved.
        """
        state, _ = started(case)
        _, effects = solve(state, "10:53")
        logs = [e for e in effects if isinstance(e, LogAnswer)]
        assert len(logs) == 1
        assert logs[0].stage == 0 and logs[0].correct

    def test_an_unknown_seat_is_ignored(self, case):
        state, _ = started(case)
        after, effects = solve(state, "10:53", seat="stranger")
        assert after is state and effects == []


class TestAccusing:
    def test_accusing_too_early_is_refused(self, case):
        state, _ = started(case)
        after, effects = solve(state, "Nicholas", accusing=True)
        assert after.stage_index == 0
        assert "not there yet" in to(effects, "c0")[0]

    def test_the_case_closes_on_the_final_stage(self, case):
        state, _ = started(case)
        state, _ = solve(state, "10:53")
        state, _ = solve(state, "Nicholas")
        assert state.stage_index == 2
        state, effects = solve(state, "Nicholas", accusing=True)
        assert state.solved
        assert "Nicholas Ashford" in all_text(effects)

    def test_the_closing_shows_what_each_person_held(self, case):
        state, _ = started(case)
        state, _ = solve(state, "10:53")
        state, _ = solve(state, "Nicholas")
        _, effects = solve(state, "Nicholas", accusing=True)
        closing = to(effects, "c0")[-1]
        for codename in CODENAMES[:3]:
            assert codename in closing

    def test_a_closed_case_stays_closed(self, case):
        state, _ = started(case)
        state, _ = solve(state, "10:53")
        state, _ = solve(state, "Nicholas")
        state, _ = solve(state, "Nicholas", accusing=True)
        after, effects = solve(state, "anything")
        assert after is state
        assert "closed" in to(effects, "c0")[0]


class TestNobodySeesAnotherHand:
    """The mode's reason to exist.

    Each investigator is dealt a different part of the file. If a fragment ever
    reaches somebody it was not dealt to, the co-operation stops being
    necessary and the game becomes a reading comprehension test.
    """

    def test_the_opening_carries_only_your_own_evidence(self, case):
        state, effects = started(case, seats=3)
        hands = deal(case.stages[0].fragments, 3)

        for index in range(3):
            mine = to(effects, f"c{index}")[0]
            for other_index, other_hand in enumerate(hands):
                if other_index == index:
                    continue
                for fragment in other_hand:
                    assert fragment not in mine, (
                        f"c{index} was shown a fragment dealt to c{other_index}"
                    )

    def test_every_later_stage_holds_the_same_line(self, case):
        state, _ = started(case, seats=3)
        state, effects = solve(state, "10:53")
        hands = deal(case.stages[1].fragments, 3)

        for index in range(3):
            mine = "\n".join(to(effects, f"c{index}"))
            for other_index, other_hand in enumerate(hands):
                if other_index == index:
                    continue
                for fragment in other_hand:
                    assert fragment not in mine

    def test_you_do_get_your_own(self, case):
        # The obvious other half: withholding everything would also pass above.
        state, effects = started(case, seats=3)
        hands = deal(case.stages[0].fragments, 3)
        for index in range(3):
            mine = to(effects, f"c{index}")[0]
            for fragment in hands[index]:
                assert fragment in mine

    def test_the_full_file_is_only_shown_once_it_is_over(self, case):
        state, effects = started(case, seats=3)
        assert case.explanation not in all_text(effects)

        state, _ = solve(state, "10:53")
        state, _ = solve(state, "Nicholas")
        _, effects = solve(state, "Nicholas", accusing=True)
        assert case.explanation in all_text(effects)
