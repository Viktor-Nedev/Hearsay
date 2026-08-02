"""The driver: one handler, both channels, nothing lost across a restart."""

import threading
from dataclasses import dataclass, field

import pytest

from hearsay.app import Driver
from hearsay.engine.rules import MIN_SEATS
from hearsay.engine.state import Phase
from hearsay.store.db import Store
from hearsay.store.snapshot import load_state

LIVE_CHANNELS = [
    {"channel": "email", "capabilities": ["initiate", "media", "receive", "reply", "send"]},
    {"channel": "discord", "capabilities": ["interactions", "reactions", "receive",
                                            "reply", "send", "initiate"]},
]


@dataclass
class FakeMessage:
    conversation_id: str
    text: str
    channel: str = "discord"
    connection_id: str = "conn_1"
    id: str = "msg_1"
    subject: str | None = None
    sender: dict | None = field(default_factory=lambda: {"address": "p@example.com"})


@dataclass
class FakeInteraction:
    conversation_id: str
    value: str


class FakeClient:
    """Records deliveries; never touches the network."""

    def __init__(self, events=None, auto_generated: set[str] | None = None):
        self.sent: list[tuple[str, str]] = []
        self._events = events or []
        self.dispatched: list[int] = []
        #: Message ids the gateway would flag as machine-generated. The real
        #: gateway is the authority here and overrides our local heuristic, so
        #: the fake has to be able to say yes as well as no.
        self.auto_generated = auto_generated or set()

    def channels(self):
        return LIVE_CHANNELS

    def send_message(self, conversation_id, text=None, blocks=None):
        self.sent.append((conversation_id, text))
        return {"id": "out"}

    def reply(self, message_id, text=None, blocks=None):
        self.sent.append((message_id, text))
        return {"id": "out"}

    def on_message(self, fn):
        return fn

    def on_interaction(self, fn):
        return fn

    def events(self, after_seq=0, limit=100, type=None):
        return [e for e in self._events if e["seq"] > after_seq][:limit]

    def _dispatch_event(self, event):
        self.dispatched.append(event["seq"])

    def _request(self, method, path):
        if path.startswith("/v1/messages/"):
            message_id = path.rsplit("/", 1)[-1]
            return {"auto_generated": message_id in self.auto_generated}
        return []

    def texts_to(self, conversation_id: str) -> list[str]:
        return [t for target, t in self.sent if target == conversation_id]


@pytest.fixture
def rig(tmp_path):
    client = FakeClient()
    store = Store(tmp_path / "t.db")
    driver = Driver(client, store, honest=True)
    yield driver, client, store
    store.close()


def seat_everyone(driver, code, n=MIN_SEATS, channels=("discord", "email", "discord")):
    for i in range(n):
        driver.on_message(FakeMessage(
            conversation_id=f"conv_{i}", text=f"JOIN {code}",
            channel=channels[i % len(channels)], id=f"msg_join_{i}",
        ))


class TestSeating:
    def test_join_from_two_channels_into_one_game(self, rig):
        driver, client, store = rig
        code = driver.lobby.create_game(honest=True)
        seat_everyone(driver, code)

        game = store.game_by_code(code)
        seats = store.seats(game["id"])
        assert len(seats) == MIN_SEATS
        # The qualification rule, exercised: one handler, players on two channels.
        assert {s.channel for s in seats} == {"discord", "email"}

    def test_unseated_stranger_is_told_how_to_join(self, rig):
        driver, client, _ = rig
        driver.on_message(FakeMessage("conv_x", "hello?"))
        assert "JOIN" in client.texts_to("conv_x")[0]

    def test_bad_code_is_refused(self, rig):
        driver, client, store = rig
        driver.on_message(FakeMessage("conv_x", "JOIN ZZZZ"))
        assert store.seat("conv_x") is None
        assert "No game with the code" in client.texts_to("conv_x")[0]

    def test_bounce_never_takes_a_seat(self, rig):
        driver, client, store = rig
        client.auto_generated.add("msg_bounce")
        code = driver.lobby.create_game(honest=True)
        driver.on_message(FakeMessage(
            "conv_bounce", f"JOIN {code}", channel="email", id="msg_bounce",
            sender={"address": "MAILER-DAEMON@amazonses.com"},
        ))
        assert store.seat("conv_bounce") is None
        assert client.texts_to("conv_bounce") == []

    def test_gateway_can_clear_a_false_positive(self, rig):
        # A real player whose message merely looks automated must still get in:
        # the local heuristic flags it, the gateway overrules, the seat is taken.
        driver, _, store = rig
        code = driver.lobby.create_game(honest=True)
        driver.on_message(FakeMessage(
            "conv_ooo", f"JOIN {code}", channel="email", id="msg_ooo",
            sender={"address": "noreply@example.com"},
        ))
        assert store.seat("conv_ooo") is not None

    def test_who_reports_without_naming(self, rig):
        driver, client, _ = rig
        code = driver.lobby.create_game(honest=True)
        seat_everyone(driver, code)
        driver.on_message(FakeMessage("conv_0", "WHO", id="m2"))
        answer = client.texts_to("conv_0")[-1]
        assert "Seats filled: 3" in answer
        assert "Vermilion" not in answer

    def test_last_inbound_is_remembered(self, rig):
        # outbox needs this to reply-fallback on channels that cannot push.
        driver, _, store = rig
        code = driver.lobby.create_game(honest=True)
        seat_everyone(driver, code)
        driver.on_message(FakeMessage("conv_0", "WHO", id="msg_latest"))
        assert store.seat("conv_0").last_message_id == "msg_latest"


class TestPlaying:
    def _started(self, driver):
        code = driver.lobby.create_game(honest=True)
        seat_everyone(driver, code)
        driver.on_message(FakeMessage("conv_0", "START", id="m_start"))
        return driver.store.game_by_code(code)["id"]

    def test_start_deals_roles_and_prompts(self, rig):
        driver, client, store = rig
        game_id = self._started(driver)
        state = load_state(store, game_id)
        assert state.phase is Phase.STATEMENT
        assert state.impostor is not None
        for i in range(MIN_SEATS):
            assert any("Reply with" in t for t in client.texts_to(f"conv_{i}"))

    def test_start_below_minimum_is_refused(self, rig):
        driver, client, store = rig
        code = driver.lobby.create_game(honest=True)
        seat_everyone(driver, code, n=2)
        driver.on_message(FakeMessage("conv_0", "START", id="m_s"))
        assert load_state(store, store.game_by_code(code)["id"]).phase is Phase.LOBBY
        assert "more before this can start" in client.texts_to("conv_0")[-1]

    def test_free_text_becomes_a_statement(self, rig):
        driver, _, store = rig
        game_id = self._started(driver)
        driver.on_message(FakeMessage("conv_0", "I was asleep", id="m3"))
        assert load_state(store, game_id).said("conv_0") == "I was asleep"

    def test_a_full_round_relays_to_everyone(self, rig):
        driver, client, store = rig
        game_id = self._started(driver)
        for i in range(MIN_SEATS):
            driver.on_message(FakeMessage(f"conv_{i}", f"seat {i} speaking", id=f"m_s{i}"))
        assert load_state(store, game_id).phase is Phase.DELIBERATE
        for i in range(MIN_SEATS):
            assert any("Here is what everyone said" in t for t in client.texts_to(f"conv_{i}"))

    def test_vote_command_is_routed(self, rig):
        driver, client, store = rig
        game_id = self._started(driver)
        for kind in range(2):
            for i in range(MIN_SEATS):
                driver.on_message(FakeMessage(f"conv_{i}", f"line {kind}", id=f"m{kind}{i}"))
        assert load_state(store, game_id).phase is Phase.VOTE

        driver.on_message(FakeMessage("conv_0", "VOTE Vermilion", id="m_v"))
        assert load_state(store, game_id).voted("conv_0") == "Vermilion"

    def test_button_vote_is_routed(self, rig):
        driver, _, store = rig
        game_id = self._started(driver)
        for kind in range(2):
            for i in range(MIN_SEATS):
                driver.on_message(FakeMessage(f"conv_{i}", f"line {kind}", id=f"m{kind}{i}"))

        driver.on_interaction(FakeInteraction("conv_0", "vote:Vermilion"))
        assert load_state(store, game_id).voted("conv_0") == "Vermilion"

    def test_unknown_button_value_is_ignored(self, rig):
        driver, _, store = rig
        game_id = self._started(driver)
        driver.on_interaction(FakeInteraction("conv_0", "something:else"))
        assert load_state(store, game_id).phase is Phase.STATEMENT

    def test_ledger_records_every_relay(self, rig):
        driver, _, store = rig
        game_id = self._started(driver)
        for i in range(MIN_SEATS):
            driver.on_message(FakeMessage(f"conv_{i}", f"seat {i}", id=f"m_s{i}"))
        rows = store.ledger(game_id)
        assert len(rows) == MIN_SEATS
        assert all(r["cause"] == "clean" and r["original"] == r["relayed"] for r in rows)

    def test_help_is_answered(self, rig):
        driver, client, _ = rig
        code = driver.lobby.create_game(honest=True)
        seat_everyone(driver, code)
        driver.on_message(FakeMessage("conv_0", "help", id="m_h"))
        assert "JOIN <code>" in client.texts_to("conv_0")[-1]


class TestConcurrency:
    def test_simultaneous_answers_do_not_overwrite_each_other(self, rig):
        """The reason the lock exists.

        `concurrency="queue"` serialises within one conversation, but a game
        spans every player's conversation. Two people answering at the same
        instant are two threads on one game.
        """
        driver, _, store = rig
        code = driver.lobby.create_game(honest=True)
        seat_everyone(driver, code)
        driver.on_message(FakeMessage("conv_0", "START", id="m_start"))
        game_id = store.game_by_code(code)["id"]

        start = threading.Barrier(MIN_SEATS)

        def speak(i):
            start.wait()
            driver.on_message(FakeMessage(f"conv_{i}", f"seat {i} spoke", id=f"m_c{i}"))

        threads = [threading.Thread(target=speak, args=(i,)) for i in range(MIN_SEATS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        state = load_state(store, game_id)
        # Every answer survived, so the round closed and moved on.
        assert state.phase is Phase.DELIBERATE


class TestCursor:
    def test_first_run_skips_history(self, tmp_path):
        # Otherwise the Day 1 test emails would be replayed as game input.
        client = FakeClient(events=[{"seq": 1}, {"seq": 2}, {"seq": 3}])
        store = Store(tmp_path / "t.db")
        driver = Driver(client, store)
        assert driver._latest_seq() == 3
        store.close()

    def test_cursor_persists_across_restart(self, tmp_path):
        store = Store(tmp_path / "t.db")
        store.set_cursor(42)
        store.close()

        reopened = Store(tmp_path / "t.db")
        assert reopened.get_cursor() == 42
        reopened.close()
