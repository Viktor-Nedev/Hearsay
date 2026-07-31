from dataclasses import dataclass

from hearsay.channels.inbound import is_playable, looks_automated, truncate_statement


@dataclass
class FakeMessage:
    id: str = "msg_1"
    text: str | None = "It wasn't me"
    subject: str | None = None
    sender: dict | None = None


class TestLooksAutomated:
    def test_human_message_passes(self):
        assert not looks_automated({"address": "ana@example.com"}, "I was with Jade")

    def test_mailer_daemon(self):
        # Exactly the bounce the Day 1 gate triggered.
        assert looks_automated({"address": "MAILER-DAEMON@amazonses.com"}, "An error occurred")

    def test_noreply_variants(self):
        for address in ("noreply@x.com", "no-reply@x.com", "do-not-reply@x.com"):
            assert looks_automated({"address": address}, "hello"), address

    def test_out_of_office_body(self):
        assert looks_automated({"address": "ana@example.com"}, "Out of office until Monday")

    def test_automatic_reply_subject(self):
        assert looks_automated(
            {"address": "ana@example.com"}, "I'll be back", subject="Automatic reply: Round 1"
        )

    def test_bounce_body(self):
        text = "An error occurred while trying to deliver the mail to the following recipients:"
        assert looks_automated({"address": "someone@example.com"}, text)

    def test_missing_sender_is_not_automated_by_itself(self):
        assert not looks_automated(None, "I think it was Slate")

    def test_word_appearing_mid_message_is_fine(self):
        # Only the opening counts; a player may talk about being out of office.
        assert not looks_automated(
            {"address": "ana@example.com"}, "I said I was out of office, remember?"
        )


class TestIsPlayable:
    def test_normal_message(self):
        assert is_playable(FakeMessage(sender={"address": "ana@example.com"}))

    def test_empty_text_is_not_a_turn(self):
        assert not is_playable(FakeMessage(text="   "))

    def test_bounce_dropped_without_client(self):
        msg = FakeMessage(text="An error occurred while trying to deliver the mail to")
        assert not is_playable(msg)

    def test_gateway_confirms_automated(self):
        class Client:
            def _request(self, method, path):
                return {"auto_generated": True}

        msg = FakeMessage(sender={"address": "MAILER-DAEMON@amazonses.com"}, text="bounce")
        assert not is_playable(msg, Client())

    def test_gateway_clears_a_false_positive(self):
        # A player who opens with "Out of office" as a joke is still a player.
        class Client:
            def _request(self, method, path):
                return {"auto_generated": False}

        msg = FakeMessage(sender={"address": "ana@example.com"}, text="Out of office, ha")
        assert is_playable(msg, Client())

    def test_unreachable_gateway_drops_suspicious_message(self):
        class Client:
            def _request(self, method, path):
                raise RuntimeError("network down")

        msg = FakeMessage(sender={"address": "mailer-daemon@ses.com"}, text="bounce")
        assert not is_playable(msg, Client())


class TestTruncate:
    def test_collapses_whitespace(self):
        assert truncate_statement("I  was\n\nwith   Jade") == "I was with Jade"

    def test_short_text_untouched(self):
        assert truncate_statement("It wasn't me") == "It wasn't me"

    def test_long_text_is_cut(self):
        result = truncate_statement("x" * 5000)
        assert len(result) == 2000
        assert result.endswith("…")
