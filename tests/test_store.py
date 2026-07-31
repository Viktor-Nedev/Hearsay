import pytest

from hearsay.store.db import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def game(store):
    store.create_game("g1", "K7QP")
    return "g1"


def seat(store, game_id, conv, codename, channel="discord"):
    return store.add_seat(conv, game_id, codename, channel, "conn_1")


class TestCursor:
    def test_starts_at_zero(self, store):
        assert store.get_cursor() == 0

    def test_round_trips(self, store):
        store.set_cursor(4211)
        assert store.get_cursor() == 4211

    def test_survives_reopen(self, store, tmp_path):
        # The whole point: listen() would otherwise resume from the newest event
        # and silently drop everything that arrived while we were down.
        store.set_cursor(99)
        store.close()
        reopened = Store(tmp_path / "test.db")
        assert reopened.get_cursor() == 99
        reopened.close()


class TestGames:
    def test_lookup_by_code_is_case_insensitive(self, store, game):
        assert store.game_by_code("k7qp")["id"] == game

    def test_unknown_code(self, store, game):
        assert store.game_by_code("ZZZZ") is None

    def test_ended_game_is_not_joinable(self, store, game):
        store.end_game(game)
        assert store.game_by_code("K7QP") is None

    def test_phase_advances(self, store, game):
        store.set_phase(game, "STATEMENT", 2)
        row = store.game(game)
        assert row["phase"] == "STATEMENT"
        assert row["round"] == 2


class TestSeats:
    def test_seat_is_keyed_by_conversation(self, store, game):
        s = seat(store, game, "conv_a", "Ochre")
        assert s.id == "conv_a"
        assert store.seat("conv_a").codename == "Ochre"

    def test_two_seats_cannot_share_a_conversation(self, store, game):
        seat(store, game, "conv_a", "Ochre")
        # Structural isolation: one conversation is one seat, enforced by the PK.
        with pytest.raises(Exception):
            seat(store, game, "conv_a", "Vermilion")

    def test_codename_is_unique_per_game(self, store, game):
        seat(store, game, "conv_a", "Ochre")
        with pytest.raises(Exception):
            seat(store, game, "conv_b", "Ochre")

    def test_seats_ordered_by_join_time(self, store, game):
        seat(store, game, "conv_a", "Ochre")
        seat(store, game, "conv_b", "Vermilion")
        assert [s.codename for s in store.seats(game)] == ["Ochre", "Vermilion"]

    def test_alive_only_filter(self, store, game):
        seat(store, game, "conv_a", "Ochre")
        seat(store, game, "conv_b", "Vermilion")
        store.eliminate("conv_a")
        assert [s.codename for s in store.seats(game, alive_only=True)] == ["Vermilion"]

    def test_lookup_by_codename_is_case_insensitive(self, store, game):
        seat(store, game, "conv_a", "Ochre")
        assert store.seat_by_codename(game, "ochre").id == "conv_a"

    def test_touch_records_last_inbound(self, store, game):
        seat(store, game, "conv_a", "Ochre")
        store.touch_seat("conv_a", "msg_9")
        # outbox needs this to reply-fallback when a channel lacks `send`.
        assert store.seat("conv_a").last_message_id == "msg_9"

    def test_seats_span_channels(self, store, game):
        seat(store, game, "conv_a", "Ochre", channel="discord")
        seat(store, game, "conv_b", "Vermilion", channel="email")
        assert {s.channel for s in store.seats(game)} == {"discord", "email"}


class TestRoundData:
    def test_statement_round_trips(self, store, game):
        seat(store, game, "conv_a", "Ochre")
        store.record_statement(game, 1, "conv_a", "It wasn't me")
        assert store.statements(game, 1) == {"conv_a": "It wasn't me"}

    def test_restating_overwrites(self, store, game):
        seat(store, game, "conv_a", "Ochre")
        store.record_statement(game, 1, "conv_a", "first")
        store.record_statement(game, 1, "conv_a", "second")
        assert store.statements(game, 1)["conv_a"] == "second"

    def test_statements_are_scoped_to_round(self, store, game):
        seat(store, game, "conv_a", "Ochre")
        store.record_statement(game, 1, "conv_a", "round one")
        assert store.statements(game, 2) == {}

    def test_vote_can_be_changed(self, store, game):
        seat(store, game, "conv_a", "Ochre")
        store.record_vote(game, 1, "conv_a", "Jade")
        store.record_vote(game, 1, "conv_a", "Slate")
        assert store.votes(game, 1) == {"conv_a": "Slate"}


class TestLedger:
    def test_records_what_was_said_versus_delivered(self, store, game):
        seat(store, game, "conv_a", "Ochre")
        store.record_relay(game, 1, "conv_a", "I was asleep", "I was with Jade", "impostor")
        entry = store.ledger(game)[0]
        assert entry["original"] == "I was asleep"
        assert entry["relayed"] == "I was with Jade"
        assert entry["cause"] == "impostor"

    def test_ordered_by_round(self, store, game):
        seat(store, game, "conv_a", "Ochre")
        store.record_relay(game, 2, "conv_a", "b", "b", "clean")
        store.record_relay(game, 1, "conv_a", "a", "a", "clean")
        assert [e["round"] for e in store.ledger(game)] == [1, 2]
