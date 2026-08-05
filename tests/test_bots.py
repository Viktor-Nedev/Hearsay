"""Seats that play themselves, and the driver that lets them.

The point of these is the last test: one human, three bots, a whole game played
through the public path — which is what a demo needs when four people cannot be
in the same twenty minutes.
"""

import random

import pytest

from hearsay.ai_player import AI_CHANNEL, Bench, is_bot, plan_tamper, suspicion
from hearsay.engine.machine import apply
from hearsay.engine.rules import CODENAMES, IMPOSTOR, WITNESS
from hearsay.engine.state import GameState, Phase, Relay, Said, SeatView, Started, Voted
from hearsay.tamper import ScriptedRewriter
from tests.test_driver import FakeClient, FakeMessage

from hearsay.app import Driver  # noqa: E402  (after FakeClient, for readability)
from hearsay.store.db import Store  # noqa: E402
from hearsay.store.snapshot import load_state  # noqa: E402


def make_state(bots: tuple[int, ...] = (), n: int = 4, honest: bool = False) -> GameState:
    seats = tuple(
        SeatView(
            id=f"c{i}",
            codename=CODENAMES[i],
            channel=AI_CHANNEL if i in bots else "discord",
        )
        for i in range(n)
    )
    return GameState(game_id="g1", code="ABCD", seats=seats, honest=honest)


class TestIsBot:
    def test_recognises_a_bench_seat(self):
        assert is_bot(SeatView(id="x", codename="Ochre", channel=AI_CHANNEL))

    def test_a_person_is_not(self):
        assert not is_bot(SeatView(id="x", codename="Ochre", channel="email"))


class TestBenchTurns:
    def setup_method(self):
        self.bench = Bench(random.Random(0), ScriptedRewriter())

    def test_says_nothing_when_every_seat_is_human(self):
        state, _ = apply(make_state(), Started(), random.Random(0))
        assert self.bench.next_event(state) is None

    def test_speaks_for_a_waiting_bot(self):
        state, _ = apply(make_state(bots=(1,)), Started(), random.Random(0))
        event = self.bench.next_event(state)
        assert isinstance(event, Said)
        assert event.seat_id == "c1"
        assert event.text

    def test_waits_for_a_bot_that_already_spoke(self):
        state, _ = apply(make_state(bots=(1,)), Started(), random.Random(0))
        state, _ = apply(state, Said("c1", "already said something"))
        assert self.bench.next_event(state) is None

    def test_votes_for_a_waiting_bot(self):
        state = self._to_vote(bots=(1,))
        event = self.bench.next_event(state)
        assert isinstance(event, Voted)
        assert event.seat_id == "c1"
        assert event.target in [s.codename for s in state.alive if s.id != "c1"]

    def test_never_votes_for_itself(self):
        state = self._to_vote(bots=(0, 1, 2, 3))
        for _ in range(4):
            event = self.bench.next_event(state)
            if event is None:
                break
            assert event.target != state.seat(event.seat_id).codename
            state, _ = apply(state, event)

    def test_a_bot_impostor_tampers(self):
        state, _ = apply(make_state(bots=(0, 1, 2, 3)), Started(), random.Random(0))
        for i in range(4):
            state, _ = apply(state, Said(f"c{i}", f"seat {i} said a thing"))
        assert state.phase is Phase.TAMPER

        event = self.bench.next_event(state)
        assert isinstance(event, Relay)

    def test_a_human_impostor_is_left_alone(self):
        # The bench must not take the impostor's turn away from a person.
        state = make_state(bots=(0, 1, 2), n=4)
        state = state.deal({"c3": IMPOSTOR, "c0": WITNESS, "c1": WITNESS, "c2": WITNESS})
        state = state.with_(phase=Phase.TAMPER, round=1, statements=(
            ("c0", "a"), ("c1", "b"), ("c2", "c"), ("c3", "d")))
        assert self.bench.next_event(state) is None

    def _to_vote(self, bots):
        state, _ = apply(make_state(bots=bots), Started(), random.Random(0))
        for _ in range(2):
            for i in range(4):
                if state.said(f"c{i}") is None or state.phase is Phase.DELIBERATE:
                    state, _ = apply(state, Said(f"c{i}", f"line from {i}"))
            if state.phase is Phase.TAMPER:
                state, _ = apply(state, Relay(()))
        assert state.phase is Phase.VOTE
        return state


class TestSuspicion:
    def test_counts_who_was_named(self):
        state = make_state(n=4).with_(statements=(
            ("c0", "I think it was Slate"),
            ("c1", "Slate is being quiet"),
            ("c2", "no idea"),
        ))
        assert suspicion(state, "c3")[0] == "Slate"

    def test_ignores_the_reader_and_the_speaker(self):
        state = make_state(n=4).with_(statements=(("c0", "Ochre and Vermilion are quiet"),))
        # Ochre spoke, so naming themselves does not count; Vermilion is reading.
        assert suspicion(state, "c1") == []


class TestPlanTamper:
    def test_produces_a_rewrite(self):
        state, _ = apply(make_state(n=4), Started(), random.Random(0))
        for i in range(4):
            state, _ = apply(state, Said(f"c{i}", "i was asleep the whole time"))
        rewrites = plan_tamper(state, ScriptedRewriter(), random.Random(1))
        assert rewrites
        assert rewrites[0][2] == "impostor"

    def test_never_rewrites_the_impostor(self):
        state, _ = apply(make_state(n=5), Started(), random.Random(0))
        for i in range(5):
            state, _ = apply(state, Said(f"c{i}", "i was asleep the whole time"))
        for seat_id, _, _ in plan_tamper(state, ScriptedRewriter(), random.Random(2)):
            assert seat_id != state.impostor.id


class TestOneHumanGame:
    """The demo shape: one person, the rest of the room on the bench."""

    @pytest.fixture
    def rig(self, tmp_path):
        client = FakeClient()
        store = Store(tmp_path / "t.db")
        driver = Driver(client, store, honest=False)
        driver.rewriter = ScriptedRewriter()
        driver.bench = Bench(random.Random(5), driver.rewriter)
        yield driver, client, store
        store.close()

    def test_a_whole_game_plays_out(self, rig):
        driver, client, store = rig
        code = driver.lobby.create_game()
        game_id = store.game_by_code(code)["id"]

        # One human joins from Discord; three seats are filled from the bench.
        driver.on_message(FakeMessage("conv_human", f"JOIN {code}", channel="discord"))
        for _ in range(3):
            driver.lobby.seat_bot(game_id)

        seats = store.seats(game_id)
        assert len(seats) == 4
        assert sum(1 for s in seats if s.channel == AI_CHANNEL) == 3

        driver.on_message(FakeMessage("conv_human", "START", id="m_start"))

        # The bots answer everything they can; the game should now be waiting on
        # the one person in it, or already finished.
        for turn in range(40):
            state = load_state(store, game_id)
            if state.phase is Phase.GAMEOVER:
                break
            waiting = [s.id for s in state.waiting_on()]
            if "conv_human" not in waiting:
                pytest.fail(f"stalled in {state.phase} waiting on {waiting}")
            if state.phase is Phase.VOTE:
                target = next(s.codename for s in state.alive if s.id != "conv_human")
                driver.on_message(FakeMessage("conv_human", f"VOTE {target}", id=f"v{turn}"))
            else:
                driver.on_message(FakeMessage("conv_human", "my turn to talk", id=f"t{turn}"))

        state = load_state(store, game_id)
        assert state.phase is Phase.GAMEOVER
        assert state.winner in (WITNESS, IMPOSTOR)

    def test_nothing_is_sent_to_a_bot(self, rig):
        driver, client, store = rig
        code = driver.lobby.create_game()
        game_id = store.game_by_code(code)["id"]
        driver.on_message(FakeMessage("conv_human", f"JOIN {code}", channel="discord"))
        for _ in range(3):
            driver.lobby.seat_bot(game_id)
        driver.on_message(FakeMessage("conv_human", "START", id="m_start"))

        # Every delivery went to the one real conversation.
        assert {target for target, _ in client.sent} == {"conv_human"}

    def test_the_ledger_records_the_rewrites(self, rig):
        driver, client, store = rig
        code = driver.lobby.create_game()
        game_id = store.game_by_code(code)["id"]
        driver.on_message(FakeMessage("conv_human", f"JOIN {code}", channel="discord"))
        for _ in range(3):
            driver.lobby.seat_bot(game_id)
        driver.on_message(FakeMessage("conv_human", "START", id="m_start"))
        driver.on_message(FakeMessage("conv_human", "i was asleep", id="m_say"))

        rows = store.ledger(game_id)
        assert rows, "a completed relay should have written the ledger"
        assert {r["cause"] for r in rows} <= {"clean", "impostor", "noise"}
