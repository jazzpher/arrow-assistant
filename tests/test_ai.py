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
    status_code = 200
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


class _KeyResp:
    def __init__(self, status=200, lines=()):
        self.status_code = status
        self._lines = list(lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):  # real requests would embed the full URL
        raise AssertionError("raise_for_status must not be used")

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)


def test_gemini_key_in_header_not_url(monkeypatch):
    seen = {}

    def fake_post(url, **kw):
        seen["url"], seen["headers"] = url, kw.get("headers", {})
        return _KeyResp(200, ['data: {"candidates":[{"content":{"parts":[{"text":"hi"}]}}]}'])

    monkeypatch.setattr(ai.requests, "post", fake_post)
    prov = LLMProvider("gemini", "SECRET123", "gemini-x")
    out = list(ai.stream_answer(prov, "q", "app", "", None, None))
    assert out == ["hi"]
    assert "SECRET123" not in seen["url"] and "key=" not in seen["url"]
    assert seen["headers"]["x-goog-api-key"] == "SECRET123"


def test_http_error_message_has_no_key(monkeypatch):
    monkeypatch.setattr(ai.requests, "post", lambda url, **kw: _KeyResp(429))
    prov = LLMProvider("gemini", "SECRET123", "gemini-x")
    try:
        list(ai.stream_answer(prov, "q", "app", "", None, None))
    except Exception as exc:
        assert "SECRET123" not in str(exc) and "429" in str(exc)
    else:
        raise AssertionError("expected an error")


def test_openai_compat_error_has_no_key(monkeypatch):
    monkeypatch.setattr(ai.requests, "post", lambda url, **kw: _KeyResp(500))
    prov = LLMProvider("nvidia", "SECRET123", "m", base_url="https://x/v1")
    try:
        list(ai.stream_answer(prov, "q", "app", "", None, None))
    except Exception as exc:
        assert "SECRET123" not in str(exc)
    else:
        raise AssertionError("expected an error")
