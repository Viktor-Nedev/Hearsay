import pytest

from hearsay.lobby import Lobby
from hearsay.store.db import Store
from hearsay.transport import FakeTransport


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "t.db")
    transport = FakeTransport()
    lobby = Lobby(store, transport)
    yield lobby, store, transport
    store.close()


def join(lobby, conv, code, channel="discord"):
    return lobby.join(code, conv, channel, "conn_1", message_id=f"msg_{conv}")


class TestCreateGame:
    def test_returns_a_usable_code(self, setup):
        lobby, store, _ = setup
        code = lobby.create_game()
        assert store.game_by_code(code) is not None

    def test_codes_are_distinct(self, setup):
        lobby, _, _ = setup
        assert len({lobby.create_game() for _ in range(20)}) == 20


class TestJoin:
    def test_first_player_gets_first_codename(self, setup):
        lobby, store, transport = setup
        code = lobby.create_game()
        assert join(lobby, "conv_a", code)
        assert store.seat("conv_a").codename == "Ochre"
        assert "You are Ochre" in transport.last_to("conv_a").text

    def test_second_player_gets_second_codename(self, setup):
        lobby, store, _ = setup
        code = lobby.create_game()
        join(lobby, "conv_a", code)
        join(lobby, "conv_b", code)
        assert store.seat("conv_b").codename == "Vermilion"

    def test_players_join_from_different_channels(self, setup):
        # The whole premise: one lobby, one handler, players on separate apps.
        lobby, store, _ = setup
        code = lobby.create_game()
        join(lobby, "conv_a", code, channel="discord")
        join(lobby, "conv_b", code, channel="email")
        seats = store.seats(store.game_by_code(code)["id"])
        assert {s.channel for s in seats} == {"discord", "email"}
        assert len(seats) == 2

    def test_unknown_code_is_refused(self, setup):
        lobby, store, transport = setup
        assert not join(lobby, "conv_a", "ZZZZ")
        assert store.seat("conv_a") is None
        assert "No game with the code ZZZZ" in transport.last_to("conv_a").text

    def test_missing_code_prompts(self, setup):
        lobby, _, transport = setup
        assert not join(lobby, "conv_a", None)
        assert "JOIN K7QP" in transport.last_to("conv_a").text

    def test_rejoin_is_idempotent(self, setup):
        lobby, store, _ = setup
        code = lobby.create_game()
        join(lobby, "conv_a", code)
        join(lobby, "conv_a", code)
        assert len(store.seats(store.game_by_code(code)["id"])) == 1

    def test_game_fills_up(self, setup):
        from hearsay.engine.rules import MAX_SEATS

        lobby, store, transport = setup
        code = lobby.create_game()
        for i in range(MAX_SEATS):
            assert join(lobby, f"conv_{i}", code)
        assert not join(lobby, "conv_overflow", code)
        assert "full" in transport.last_to("conv_overflow").text

    def test_welcome_counts_seats(self, setup):
        lobby, _, transport = setup
        code = lobby.create_game()
        join(lobby, "conv_a", code)
        join(lobby, "conv_b", code)
        assert "Seats filled: 2" in transport.last_to("conv_b").text

    def test_welcome_never_names_other_players(self, setup):
        lobby, _, transport = setup
        code = lobby.create_game()
        join(lobby, "conv_a", code)
        join(lobby, "conv_b", code)
        # Vermilion must not learn that Ochre exists by name.
        assert not transport.leaked("conv_b", "Ochre")


class TestLeave:
    def test_removes_the_seat(self, setup):
        lobby, store, _ = setup
        code = lobby.create_game()
        join(lobby, "conv_a", code)
        assert lobby.leave("conv_a")
        assert store.seat("conv_a") is None

    def test_leaving_without_a_seat_is_a_no_op(self, setup):
        lobby, _, _ = setup
        assert not lobby.leave("conv_nobody")

    def test_codename_is_freed(self, setup):
        lobby, store, _ = setup
        code = lobby.create_game()
        join(lobby, "conv_a", code)
        lobby.leave("conv_a")
        join(lobby, "conv_b", code)
        assert store.seat("conv_b").codename == "Ochre"


class TestWho:
    def test_reports_own_codename_and_count(self, setup):
        lobby, _, transport = setup
        code = lobby.create_game()
        join(lobby, "conv_a", code)
        join(lobby, "conv_b", code)
        lobby.who("conv_a")
        text = transport.last_to("conv_a").text
        assert "You are Ochre" in text
        assert "Seats filled: 2" in text

    def test_never_lists_other_players(self, setup):
        lobby, _, transport = setup
        code = lobby.create_game()
        join(lobby, "conv_a", code)
        join(lobby, "conv_b", code)
        transport.clear()
        lobby.who("conv_a")
        assert not transport.leaked("conv_a", "Vermilion")

    def test_unseated_player_gets_nothing(self, setup):
        lobby, _, _ = setup
        assert not lobby.who("conv_nobody")
