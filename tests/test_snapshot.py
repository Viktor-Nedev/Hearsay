"""The property a live game depends on: a restart must lose nothing.

`listen()` resumes from the newest event unless handed a cursor, so a process
that dies mid-round and comes back must rebuild the game from disk exactly as it
was. These tests are the offline stand-in for pulling the plug.
"""

import random

import pytest

from hearsay.engine import rules
from hearsay.engine.machine import apply
from hearsay.engine.state import Phase, Said, Started, Voted
from hearsay.store.db import Store
from hearsay.store.snapshot import load_state, save_state


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def seed(store, seats=4, game_id="g1"):
    store.create_game(game_id, "K7QP", honest=True)
    channels = ["discord", "email", "slack", "discord"]
    for i in range(seats):
        store.add_seat(f"conv_{i}", game_id, rules.CODENAMES[i], channels[i % 4], "conn_1")
    return game_id


def reload(store, game_id):
    """Simulate a crash and restart: nothing survives except the database."""
    return load_state(store, game_id)


class TestRoundTrip:
    def test_lobby_state_loads(self, store):
        game_id = seed(store)
        state = load_state(store, game_id)
        assert state.phase is Phase.LOBBY
        assert state.code == "K7QP"
        assert state.honest is True
        assert len(state.seats) == 4

    def test_unknown_game(self, store):
        assert load_state(store, "nope") is None

    def test_seat_details_survive(self, store):
        game_id = seed(store)
        state = load_state(store, game_id)
        assert [s.codename for s in state.seats] == rules.CODENAMES[:4]
        assert {s.channel for s in state.seats} == {"discord", "email", "slack"}

    def test_roles_survive(self, store):
        game_id = seed(store)
        state, _ = apply(load_state(store, game_id), Started(), random.Random(0))
        save_state(store, state)

        restored = reload(store, game_id)
        assert restored.impostor is not None
        assert restored.impostor.id == state.impostor.id
        assert sum(1 for s in restored.seats if s.role == rules.IMPOSTOR) == 1

    def test_phase_and_round_survive(self, store):
        game_id = seed(store)
        state, _ = apply(load_state(store, game_id), Started(), random.Random(0))
        save_state(store, state)
        assert reload(store, game_id).phase is Phase.STATEMENT
        assert reload(store, game_id).round == 1


class TestMidRoundCrash:
    def _started(self, store):
        game_id = seed(store)
        state, _ = apply(load_state(store, game_id), Started(), random.Random(0))
        save_state(store, state)
        return game_id, state

    def test_partial_statements_survive(self, store):
        game_id, state = self._started(store)
        state, _ = apply(state, Said("conv_0", "I was asleep"))
        save_state(store, state)

        restored = reload(store, game_id)
        assert restored.said("conv_0") == "I was asleep"
        assert restored.said("conv_1") is None
        assert len(restored.waiting_on()) == 3

    def test_the_game_continues_after_a_restart(self, store):
        game_id, state = self._started(store)
        state, _ = apply(state, Said("conv_0", "first"))
        save_state(store, state)

        # Everything in memory is gone; carry on from disk alone.
        resumed = reload(store, game_id)
        for seat_id in ("conv_1", "conv_2", "conv_3"):
            resumed, _ = apply(resumed, Said(seat_id, "me too"))
        save_state(store, resumed)

        assert resumed.phase is Phase.DELIBERATE
        assert reload(store, game_id).phase is Phase.DELIBERATE

    def test_statements_and_deliberations_do_not_collide(self, store):
        # Both are one row per seat per round; without a kind column the second
        # phase would silently overwrite the first.
        game_id, state = self._started(store)
        for seat_id in ("conv_0", "conv_1", "conv_2", "conv_3"):
            state, _ = apply(state, Said(seat_id, f"{seat_id} statement"))
        for seat_id in ("conv_0", "conv_1", "conv_2", "conv_3"):
            state, _ = apply(state, Said(seat_id, f"{seat_id} deliberation"))
        save_state(store, state)

        restored = reload(store, game_id)
        assert restored.phase is Phase.VOTE
        assert store.statements(game_id, 1)["conv_0"] == "conv_0 statement"
        assert store.statements(game_id, 1, "deliberation")["conv_0"] == "conv_0 deliberation"

    def test_partial_votes_survive(self, store):
        game_id, state = self._started(store)
        for kind in range(2):
            for seat_id in ("conv_0", "conv_1", "conv_2", "conv_3"):
                state, _ = apply(state, Said(seat_id, f"line {kind}"))
        assert state.phase is Phase.VOTE

        state, _ = apply(state, Voted("conv_0", "Vermilion"))
        save_state(store, state)

        restored = reload(store, game_id)
        assert restored.voted("conv_0") == "Vermilion"
        assert restored.phase is Phase.VOTE

    def test_elimination_survives(self, store):
        game_id, state = self._started(store)
        for kind in range(2):
            for seat_id in ("conv_0", "conv_1", "conv_2", "conv_3"):
                state, _ = apply(state, Said(seat_id, f"line {kind}"))
        for seat_id in ("conv_0", "conv_2", "conv_3"):
            state, _ = apply(state, Voted(seat_id, "Vermilion"))
        state, _ = apply(state, Voted("conv_1", "Ochre"))
        save_state(store, state)

        restored = reload(store, game_id)
        assert restored.seat("conv_1").alive is False
        assert len(restored.alive) == 3

    def test_new_round_starts_empty_on_disk(self, store):
        game_id, state = self._started(store)
        for kind in range(2):
            for seat_id in ("conv_0", "conv_1", "conv_2", "conv_3"):
                state, _ = apply(state, Said(seat_id, f"line {kind}"))
        for seat_id in ("conv_0", "conv_2", "conv_3"):
            state, _ = apply(state, Voted(seat_id, "Vermilion"))
        state, _ = apply(state, Voted("conv_1", "Ochre"))
        save_state(store, state)

        restored = reload(store, game_id)
        assert restored.round == 2
        assert restored.statements == ()
        assert restored.votes == ()


class TestIdempotence:
    def test_saving_twice_changes_nothing(self, store):
        game_id = seed(store)
        state, _ = apply(load_state(store, game_id), Started(), random.Random(0))
        save_state(store, state)
        first = reload(store, game_id)
        save_state(store, state)
        assert reload(store, game_id) == first
