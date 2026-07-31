import pytest

from hearsay.channels.outbox import CapabilityMatrix, Outbox
from hearsay.store.db import Seat, Store
from hearsay.transport import FakeTransport, Payload

# The shape the live gateway actually returns — captured on Day 1 into
# docs/capabilities.json, trimmed to the channels Hearsay uses.
LIVE_CHANNELS = [
    {"channel": "email", "provider": "ses",
     "capabilities": ["initiate", "media", "receive", "reply", "send"]},
    {"channel": "discord", "provider": "discord",
     "capabilities": ["group_visibility", "initiate", "interactions", "media",
                      "reactions", "receive", "reply", "see_bots", "send"]},
    {"channel": "slack", "provider": "slack",
     "capabilities": ["interactions", "media", "reactions", "receive", "reply", "send"]},
    {"channel": "phone", "provider": "twilio",
     "capabilities": ["initiate", "otp", "receive", "reply", "send"]},
    {"channel": "phone", "provider": "telnyx",
     "capabilities": ["initiate", "otp", "receive", "reply", "send"]},
]


class RecordingClient:
    """Stands in for CommClient; records which delivery path was taken."""

    def __init__(self):
        self.pushed = []
        self.replied = []

    def send_message(self, conversation_id, text=None, blocks=None):
        self.pushed.append((conversation_id, text, blocks))
        return {"id": "msg_out"}

    def reply(self, message_id, text=None, blocks=None):
        self.replied.append((message_id, text, blocks))
        return {"id": "msg_out"}


def make_seat(channel="discord", last_message_id=None):
    return Seat(
        conversation_id="conv_a", game_id="g1", codename="Ochre", role=None, alive=True,
        channel=channel, connection_id="conn_1", last_message_id=last_message_id,
    )


@pytest.fixture
def matrix():
    return CapabilityMatrix(LIVE_CHANNELS)


class TestCapabilityMatrix:
    def test_reads_live_shape(self, matrix):
        assert matrix.can("email", "send")
        assert matrix.can("discord", "interactions")
        assert not matrix.can("email", "interactions")

    def test_unknown_channel_can_do_nothing(self, matrix):
        assert not matrix.can("carrier-pigeon", "send")

    def test_unions_providers_backing_one_channel(self, matrix):
        # phone arrives twice (twilio, telnyx); the channel keeps both sets.
        assert matrix.can("phone", "send")
        assert matrix.can("phone", "otp")

    def test_relay_capable_channels(self, matrix):
        assert set(matrix.relay_capable()) == {"email", "discord", "slack", "phone"}

    def test_email_can_cold_start(self, matrix):
        # Day 1's finding: the docs say initiate is SMS-only. It is not.
        assert matrix.can("email", "initiate")
        assert matrix.can("discord", "initiate")


class TestOutboxDelivery:
    def test_pushes_when_channel_supports_send(self, matrix, tmp_path):
        client, store = RecordingClient(), Store(tmp_path / "t.db")
        Outbox(client, matrix, store).send(make_seat("discord"), Payload("Round 1 begins"))
        assert client.pushed == [("conv_a", "Round 1 begins", None)]
        assert client.replied == []
        store.close()

    def test_falls_back_to_reply_without_send(self, tmp_path):
        # A hypothetical channel that can only answer, never initiate contact.
        matrix = CapabilityMatrix([{"channel": "carrier", "capabilities": ["receive", "reply"]}])
        client, store = RecordingClient(), Store(tmp_path / "t.db")
        seat = make_seat("carrier", last_message_id="msg_7")
        Outbox(client, matrix, store).send(seat, Payload("Round 1 begins"))
        assert client.pushed == []
        assert client.replied == [("msg_7", "Round 1 begins", None)]
        store.close()

    def test_raises_when_unreachable(self, tmp_path):
        matrix = CapabilityMatrix([{"channel": "carrier", "capabilities": ["receive"]}])
        client, store = RecordingClient(), Store(tmp_path / "t.db")
        with pytest.raises(RuntimeError, match="cannot reach seat"):
            Outbox(client, matrix, store).send(make_seat("carrier"), Payload("hello"))
        store.close()

    def test_blocks_pass_through(self, matrix, tmp_path):
        client, store = RecordingClient(), Store(tmp_path / "t.db")
        blocks = [{"type": "heading", "text": "Round 1"}]
        Outbox(client, matrix, store).send(make_seat(), Payload("Round 1", blocks=blocks))
        assert client.pushed[0][2] == blocks
        store.close()

    def test_button_support_follows_channel(self, matrix, tmp_path):
        store = Store(tmp_path / "t.db")
        outbox = Outbox(RecordingClient(), matrix, store)
        assert outbox.supports_buttons(make_seat("discord"))
        assert not outbox.supports_buttons(make_seat("email"))
        store.close()


class TestPayload:
    def test_rejects_empty_text(self):
        # Every message must say something; a blank push is always a bug.
        with pytest.raises(ValueError):
            Payload("")
        with pytest.raises(ValueError):
            Payload("   ")


class TestFakeTransport:
    def test_records_per_seat(self):
        fake = FakeTransport()
        fake.send(make_seat(), Payload("one"))
        assert fake.texts_to("conv_a") == ["one"]

    def test_leak_detection(self):
        fake = FakeTransport()
        fake.send(make_seat(), Payload("the impostor is Jade"))
        assert fake.leaked("conv_a", "Jade")
        assert not fake.leaked("conv_b", "Jade")
