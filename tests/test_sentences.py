from arrow_assistant.sentences import SentenceStreamer, split_sentences


def test_split_basic():
    assert split_sentences("Click File. Then Export! Okay?") == \
        ["Click File.", "Then Export!", "Okay?"]


def test_split_single():
    assert split_sentences("Just one.") == ["Just one."]


def test_split_ignores_empty():
    assert split_sentences("   ") == []


def test_streamer_flushes_on_boundary():
    s = SentenceStreamer()
    assert s.feed("Click the File") == []
    assert s.feed(" menu. Then hit") == ["Click the File menu."]
    assert s.feed(" Export!") == []  # trailing ! waits for more text
    assert s.flush() == ["Then hit Export!"]


def test_streamer_flush_returns_tail():
    s = SentenceStreamer()
    assert s.feed("One. Two") == ["One."]
    assert s.feed("") == []
    assert s.flush() == ["Two"]


def test_streamer_question_marks():
    s = SentenceStreamer()
    out = s.feed("Sure! Want the steps? Here")
    assert out == ["Sure!", "Want the steps?"]
    assert s.flush() == ["Here"]


def test_streamer_no_boundary_yields_nothing_until_flush():
    s = SentenceStreamer()
    assert s.feed("no punctuation here") == []
    assert s.flush() == ["no punctuation here"]


def test_streamer_abbreviation_like_flow():
    # boundaries require whitespace after the punctuation
    s = SentenceStreamer()
    assert s.feed("version 3.2 is installed") == []
    assert s.flush() == ["version 3.2 is installed"]
