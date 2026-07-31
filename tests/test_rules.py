import random

import pytest

from hearsay.engine.rules import (
    CODENAMES,
    IMPOSTOR,
    MAX_SEATS,
    WITNESS,
    assign_roles,
    new_game_code,
    next_codename,
    normalise_codename,
    tally,
    winner,
)


class TestGameCode:
    def test_length_and_alphabet(self):
        code = new_game_code()
        assert len(code) == 4
        # No vowels means no accidental words; no 0/O/1/I means no misreading.
        assert not set(code) & set("AEIOU01")

    def test_codes_differ(self):
        assert len({new_game_code() for _ in range(50)}) > 40


class TestCodenames:
    def test_first_seat_gets_first_name(self):
        assert next_codename([]) == CODENAMES[0]

    def test_skips_taken(self):
        assert next_codename(["Ochre"]) == "Vermilion"

    def test_matching_is_case_insensitive(self):
        assert next_codename(["ochre", "VERMILION"]) == "Slate"

    def test_all_distinct_initials(self):
        # A player votes with one word; two names sharing a letter invites error.
        assert len({name[0] for name in CODENAMES}) == len(CODENAMES)

    def test_full_game_raises(self):
        with pytest.raises(ValueError, match="full"):
            next_codename(list(CODENAMES))

    def test_normalise(self):
        assert normalise_codename("  OCHRE, ") == "Ochre"
        assert normalise_codename("jade.") == "Jade"


class TestAssignRoles:
    def test_exactly_one_impostor(self):
        roles = assign_roles(["a", "b", "c", "d"], random.Random(0))
        assert list(roles.values()).count(IMPOSTOR) == 1
        assert list(roles.values()).count(WITNESS) == 3

    def test_everyone_gets_a_role(self):
        seats = ["a", "b", "c"]
        assert set(assign_roles(seats, random.Random(1))) == set(seats)

    def test_rejects_too_few_players(self):
        with pytest.raises(ValueError, match="at least"):
            assign_roles(["a", "b"])

    def test_impostor_varies_across_seeds(self):
        picks = {
            next(s for s, r in assign_roles(["a", "b", "c"], random.Random(i)).items()
                 if r == IMPOSTOR)
            for i in range(30)
        }
        assert len(picks) > 1


class TestTally:
    def test_clear_majority(self):
        eliminated, counts = tally({"a": "Ochre", "b": "Ochre", "c": "Jade"})
        assert eliminated == "Ochre"
        assert counts == {"Ochre": 2, "Jade": 1}

    def test_tie_eliminates_nobody(self):
        eliminated, counts = tally({"a": "Ochre", "b": "Jade"})
        assert eliminated is None
        assert counts == {"Ochre": 1, "Jade": 1}

    def test_no_votes(self):
        assert tally({}) == (None, {})

    def test_single_vote_decides(self):
        eliminated, _ = tally({"a": "Slate"})
        assert eliminated == "Slate"


class TestWinner:
    def test_nobody_has_won_yet(self):
        roles = {"a": IMPOSTOR, "b": WITNESS, "c": WITNESS, "d": WITNESS}
        assert winner(roles, ["a", "b", "c", "d"]) is None

    def test_witnesses_win_when_impostor_is_out(self):
        roles = {"a": IMPOSTOR, "b": WITNESS, "c": WITNESS}
        assert winner(roles, ["b", "c"]) == WITNESS

    def test_impostor_wins_at_parity(self):
        roles = {"a": IMPOSTOR, "b": WITNESS}
        assert winner(roles, ["a", "b"]) == IMPOSTOR

    def test_impostor_survives_three_way(self):
        roles = {"a": IMPOSTOR, "b": WITNESS, "c": WITNESS}
        assert winner(roles, ["a", "b", "c"]) is None
