from hearsay.router import Intent, parse, strip_quoted


class TestStripQuoted:
    def test_plain_text_survives(self):
        assert strip_quoted("I think it was Slate.") == "I think it was Slate."

    def test_cuts_at_angle_bracket_quote(self):
        raw = "I agree with that.\n\n> Ochre said something suspicious\n> and then left"
        assert strip_quoted(raw) == "I agree with that."

    def test_cuts_at_attribution_line(self):
        raw = "Not me.\n\nOn Tuesday, 30 July 2026, Hearsay wrote:\nRound 2 begins"
        assert strip_quoted(raw) == "Not me."

    def test_cuts_at_signature_delimiter(self):
        assert strip_quoted("Vote Amber\n\n-- \nViktor\nSofia") == "Vote Amber"

    def test_drops_mobile_signoff(self):
        assert strip_quoted("It was Jade\nSent from my iPhone") == "It was Jade"

    def test_cuts_at_forwarded_header(self):
        raw = "Here is my statement.\n\nFrom: hearsay@agents.trycaspianai.com\nSubject: Round 1"
        assert strip_quoted(raw) == "Here is my statement."

    def test_handles_crlf(self):
        assert strip_quoted("Line one\r\n> quoted") == "Line one"

    def test_empty_input(self):
        assert strip_quoted("") == ""
        assert strip_quoted("   \n  ") == ""


class TestParse:
    def test_join_with_code(self):
        assert parse("JOIN K7QP") == Intent(kind="join", arg="K7QP", body="JOIN K7QP")

    def test_command_is_case_insensitive(self):
        assert parse("join k7qp").kind == "join"

    def test_command_tolerates_punctuation(self):
        assert parse("JOIN: K7QP").kind == "join"

    def test_bare_command_has_no_arg(self):
        intent = parse("who")
        assert intent.kind == "who"
        assert intent.arg is None

    def test_vote_carries_target(self):
        assert parse("vote Ochre").arg == "Ochre"

    def test_free_text_is_text(self):
        intent = parse("I was with Amber the whole time")
        assert intent.kind == "text"
        assert intent.body == "I was with Amber the whole time"

    def test_empty_message_is_empty(self):
        assert parse("").kind == "empty"

    def test_command_only_counts_as_first_word(self):
        # The trap: a statement that merely mentions voting must not cast a vote.
        intent = parse("I think we should vote Slate but I am not sure")
        assert intent.kind == "text"

    def test_command_ignored_inside_quoted_reply(self):
        # The email trap: VOTE appears, but only in the quoted history.
        raw = "No idea honestly.\n\n> VOTE Ochre\n> was what I sent last round"
        intent = parse(raw)
        assert intent.kind == "text"
        assert intent.body == "No idea honestly."

    def test_vote_survives_quoted_history(self):
        raw = "VOTE Amber\n\n> Round 2 transcript\n> Ochre: it wasn't me"
        intent = parse(raw)
        assert intent.kind == "vote"
        assert intent.arg == "Amber"
