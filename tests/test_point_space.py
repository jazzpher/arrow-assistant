"""Gemini POINT tags are on a 0-1000 grid (found by the real-Windows e2e run)."""
from arrow_assistant import ai
from arrow_assistant.config import LLMProvider
from arrow_assistant.points import to_image_px

GEM = LLMProvider("gemini", "k", "gemini-3.8-flash")
OR = LLMProvider("openrouter", "k", "m", base_url="https://x")


def test_point_space_per_provider():
    assert ai.point_space(GEM) == "norm1000"
    assert ai.point_space(OR) == "pixels"
    assert ai.point_space(None) == "pixels"
    assert ai.point_space(object()) == "pixels"


def test_system_prompt_per_provider():
    assert ai.system_prompt(OR) == ai.SYSTEM_PROMPT
    g = ai.system_prompt(GEM)
    assert "normalized to 0-1000" in g and "INTEGER pixel coordinates" not in g
    assert "Put the tag FIRST" in g and "never invent a location" in g


def test_gemini_user_content_says_norm():
    parts = ai.build_user_content("q", "a", "", None, True, "QUJD", (1568, 882), space="norm1000")
    assert "0-1000" in parts[0]["text"] and "1568x882" not in parts[0]["text"]
    parts = ai.build_user_content("q", "a", "", None, True, "QUJD", (1568, 882))
    assert "1568x882" in parts[0]["text"]


def test_to_image_px_norm_matches_e2e_notepad():
    # real Gemini answer on a 1024x768 shot: File menu rect (48,71)-(80,90)
    x, y = to_image_px(63, 103, "norm1000", 1024, 768)
    assert 48 <= x <= 80 and 71 <= y <= 90


def test_to_image_px_pixels_and_clamp():
    assert to_image_px(10, 20, "pixels", 100, 50) == (10, 20)
    assert to_image_px(999, -5, "pixels", 100, 50) == (99, 0)
    assert to_image_px(1000, 1000, "norm1000", 1568, 882) == (1567, 881)


def test_stream_answer_sends_gemini_prompt(monkeypatch):
    sent = {}

    class R:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_lines(self, decode_unicode=True): return iter(())

    def post(url, json=None, headers=None, stream=None, timeout=None):
        sent.update(json)
        return R()
    monkeypatch.setattr(ai.requests, "post", post)
    list(ai.stream_answer(GEM, "q", "a", "", None, "QUJD", image_size=(1024, 768)))
    assert sent["system_instruction"]["parts"][0]["text"] == ai.GEMINI_SYSTEM_PROMPT
    assert "0-1000" in sent["contents"][0]["parts"][0]["text"]
