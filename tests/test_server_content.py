from hearsay import server_content
from hearsay.router import parse


class TestSections:
    def test_every_advertised_section_resolves(self):
        for name in server_content.available().replace("`", "").split(", "):
            assert server_content.section(name), name

    def test_aliases_land_on_the_same_text(self):
        assert server_content.section("how-to-play") is server_content.section("play")
        assert server_content.section("private") is server_content.section("rooms")

    def test_case_and_hash_are_forgiven(self):
        # People type the channel name, hash and all.
        assert server_content.section("#Rules") is server_content.section("rules")

    def test_unknown_section(self):
        assert server_content.section("nonsense") is None
        assert server_content.section("") is None

    def test_sections_are_substantial(self):
        for name in ("rules", "how-to-play", "casefile", "hearsay", "private"):
            body = server_content.section(name)
            assert body.startswith("#"), name
            assert len(body) > 400, name

    def test_the_commands_players_need_are_documented(self):
        play = server_content.section("how-to-play")
        for command in ("JOIN", "START", "SOLVE", "ACCUSE", "VOTE", "TAMPER", "WHO"):
            assert command in play, command

    def test_the_email_threading_trap_is_warned_about(self):
        # A new email is a new conversation, which is a new seat. Players who
        # compose instead of replying end up as two people.
        assert "Reply to the agent" in server_content.section("how-to-play")

    def test_private_rooms_explain_that_a_code_is_the_room(self):
        assert "code is the room" in server_content.section("private").lower()

    def test_the_agent_address_is_correct(self):
        assert "hearsay@agents.trycaspianai.com" in server_content.section("how-to-play")


class TestRouting:
    def test_setup_parses_with_a_section(self):
        intent = parse("SETUP rules")
        assert intent.kind == "setup"
        assert intent.arg == "rules"

    def test_setup_is_case_insensitive(self):
        assert parse("setup casefile").kind == "setup"

    def test_bare_setup_has_no_argument(self):
        assert parse("SETUP").arg is None

    def test_setup_mentioned_mid_sentence_is_not_a_command(self):
        assert parse("how do i setup a game").kind == "text"
