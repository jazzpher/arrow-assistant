import json

from arrow_assistant import ai
from arrow_assistant.config import LLMProvider


def test_build_user_content_full():
    parts = ai.build_user_content("saan export?", "excel.exe", "mem",
                                  "docs", True, "QUJD")
    assert parts[0]["type"] == "text"
    assert "saan export?" in parts[0]["text"]
    assert "excel.exe" in parts[0]["text"]
    assert "mem" in parts[0]["text"] and "docs" in parts[0]["text"]
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].endswith("QUJD")


def test_build_user_content_no_image_no_kb():
    parts = ai.build_user_content("q", "app", "", None, False, None)
    assert len(parts) == 1
    assert "Documentation" not in parts[0]["text"]
    assert "Recent history" not in parts[0]["text"]


def test_gemini_contents_shape():
    parts = ai.build_user_content("q", "app", "", None, True, "AAAA")
    contents = ai._to_gemini_contents(parts)
    assert contents[0]["role"] == "user"
    assert contents[0]["parts"][0] == {"text": contents[0]["parts"][0]["text"]}
    img = contents[0]["parts"][1]["inline_data"]
    assert img["mime_type"] == "image/jpeg" and img["data"] == "AAAA"


class _FakeResp:
    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)


def test_gemini_stream_parses_sse(monkeypatch):
    chunks = [
        {"candidates": [{"content": {"parts": [{"text": "Click File. "}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "Then Export."}]}}]},
    ]
    lines = [f"data: {json.dumps(c)}" for c in chunks] + ["data: [DONE]"]
    monkeypatch.setattr(ai.requests, "post",
                        lambda *a, **k: _FakeResp(lines))
    p = LLMProvider("gemini", "key", "gemini-2.5-flash")
    out = "".join(ai.stream_answer(p, "q", "app", "", None, "AAAA"))
    assert out == "Click File. Then Export."


def test_openai_stream_parses_sse(monkeypatch):
    chunks = [
        {"choices": [{"delta": {"content": "Press F1. "}}]},
        {"choices": [{"delta": {"content": "Done."}}]},
    ]
    lines = [f"data: {json.dumps(c)}" for c in chunks] + ["data: [DONE]"]
    monkeypatch.setattr(ai.requests, "post",
                        lambda *a, **k: _FakeResp(lines))
    p = LLMProvider("openrouter", "key", "m", base_url="https://x/v1")
    out = "".join(ai.stream_answer(p, "q", "app", "", None, None))
    assert out == "Press F1. Done."
