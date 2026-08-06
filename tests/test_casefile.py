"""The evidence, and how it is split.

The invariant that matters is the last class: the two fragments a stage cannot
be solved without must never reach the same investigator. Get that wrong and the
mode still runs, still looks fine, and quietly stops being a co-operative game.
"""

import pytest

from hearsay.casefile import accepts, deal, find_case, load_case
from hearsay.casefile.case import CASES_DIR, available_cases

#: Group sizes a real game will actually see.
SEAT_COUNTS = [2, 3, 4, 5, 6]


@pytest.fixture(scope="module")
def ashford():
    return find_case("ashford")


class TestLoading:
    def test_the_shipped_case_loads(self, ashford):
        assert ashford.id == "ashford"
        assert ashford.title == "The Ashford Vigil"
        assert len(ashford.stages) == 3

    def test_every_stage_is_answerable(self, ashford):
        for stage in ashford.stages:
            assert stage.answers, f"{stage.id} has no accepted answer"
            assert stage.fragments, f"{stage.id} has no evidence"
            assert stage.brief.strip()

    def test_the_solution_is_present(self, ashford):
        assert ashford.culprit
        assert len(ashford.explanation) > 200

    def test_the_culprit_is_one_of_the_suspects(self, ashford):
        assert any(ashford.culprit in s or s in ashford.culprit for s in ashford.suspects)

    def test_the_final_stage_accepts_the_culprit(self, ashford):
        # Otherwise a team that solves it correctly is told they are wrong.
        assert accepts(ashford.stages[ashford.final_index], ashford.culprit)

    def test_unknown_case_lists_what_exists(self):
        with pytest.raises(FileNotFoundError, match="ashford"):
            find_case("no-such-case")

    def test_a_case_can_be_loaded_by_path(self):
        assert load_case(CASES_DIR / "ashford.toml").id == "ashford"

    def test_available_cases(self):
        assert "ashford" in available_cases()

    def test_stage_lookup_is_bounded(self, ashford):
        assert ashford.stage(0) is not None
        assert ashford.stage(len(ashford.stages)) is None
        assert ashford.stage(-1) is None


class TestAnswers:
    def test_exact(self, ashford):
        assert accepts(ashford.stages[0], "10:53")

    def test_punctuation_and_case_do_not_matter(self, ashford):
        for given in ("10.53", " 10:53 ", "Ten Fifty-Three", "TEN FIFTY THREE"):
            assert accepts(ashford.stages[0], given), given

    def test_the_decoy_time_is_rejected(self, ashford):
        # 11:04 is what the clock says. The clock lies.
        assert not accepts(ashford.stages[0], "11:04")

    def test_empty_is_rejected(self, ashford):
        assert not accepts(ashford.stages[0], "")
        assert not accepts(ashford.stages[0], "   ")

    def test_surname_alone_is_enough(self, ashford):
        assert accepts(ashford.stages[1], "Nicholas")
        assert accepts(ashford.stages[1], "nicholas ashford")


class TestDealing:
    def test_every_fragment_is_held_by_somebody(self, ashford):
        for stage in ashford.stages:
            for seats in SEAT_COUNTS:
                dealt = [f for hand in deal(stage.fragments, seats) for f in hand]
                assert sorted(dealt) == sorted(stage.fragments), (stage.id, seats)

    def test_hands_are_even(self, ashford):
        hands = deal(ashford.stages[0].fragments, 3)
        assert [len(h) for h in hands] == [2, 2, 1]

    def test_more_investigators_than_evidence(self, ashford):
        hands = deal(ashford.stages[0].fragments, 8)
        assert sum(len(h) for h in hands) == len(ashford.stages[0].fragments)
        assert [] in hands, "extra hands should be honestly empty"

    def test_one_investigator_gets_everything(self, ashford):
        # Degenerate but legal: solo play is a reading exercise, not a game.
        hands = deal(ashford.stages[0].fragments, 1)
        assert len(hands[0]) == len(ashford.stages[0].fragments)

    def test_zero_seats_is_refused(self, ashford):
        with pytest.raises(ValueError):
            deal(ashford.stages[0].fragments, 0)

    def test_dealing_is_stable(self, ashford):
        first = deal(ashford.stages[0].fragments, 4)
        assert first == deal(ashford.stages[0].fragments, 4)


class TestNobodyCanSolveItAlone:
    """The mode's reason to exist.

    A stage is built around two fragments that mean nothing apart — a time, and
    the reason the time is wrong. If one investigator is dealt both, they answer
    without speaking to anyone and the co-operative game silently becomes a
    quiz. Cases put that pair first; `deal()` separates consecutive fragments.
    """

    def test_the_keystone_pair_is_always_split(self, ashford):
        for stage in ashford.stages:
            for seats in SEAT_COUNTS:
                hands = deal(stage.fragments, seats)
                holder = {}
                for index, hand in enumerate(hands):
                    for fragment in hand:
                        holder[fragment] = index
                first, second = stage.fragments[0], stage.fragments[1]
                assert holder[first] != holder[second], (
                    f"{stage.id} at {seats} seats: one investigator holds both "
                    f"keystone fragments and can solve it alone"
                )

    def test_no_hand_holds_a_majority_of_the_evidence(self, ashford):
        for stage in ashford.stages:
            for seats in (3, 4, 5):
                biggest = max(len(h) for h in deal(stage.fragments, seats))
                assert biggest <= len(stage.fragments) // 2 + 1
