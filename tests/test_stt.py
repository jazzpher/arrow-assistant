import io
import wave

import numpy as np

from arrow_assistant import stt


def _wav_bytes(seconds=0.1, rate=16000):
    n = int(seconds * rate)
    pcm = (np.sin(np.arange(n) / 10.0) * 10000).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def test_transcribe_empty_returns_empty():
    assert stt.transcribe(b"", "groq", "key") == ""


def test_groq_called_with_key(monkeypatch):
    calls = {}

    class R:
        text = "  hello world  "

        def raise_for_status(self):
            pass

    def fake_post(url, headers, files, data, timeout):
        calls["url"] = url
        calls["auth"] = headers["Authorization"]
        calls["model"] = data["model"]
        return R()

    monkeypatch.setattr("requests.post", fake_post)
    out = stt.transcribe(_wav_bytes(), "groq", "gk")
    assert out == "hello world"
    assert "groq.com" in calls["url"]
    assert calls["auth"] == "Bearer gk"
    assert "whisper" in calls["model"]


def test_local_fallback_invoked(monkeypatch):
    called = {}
    def fake_local(b):
        called["hit"] = True
        return "local!"
    monkeypatch.setattr(stt, "_transcribe_local", fake_local)
    assert stt.transcribe(_wav_bytes(), "local") == "local!"
    assert called["hit"]


def test_missing_local_speech_gives_install_hint(monkeypatch):
    import builtins
    import pytest
    original_import = builtins.__import__

    def no_whisper(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ModuleNotFoundError("No module named 'faster_whisper'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(stt, "_whisper_model", None)
    monkeypatch.setattr(builtins, "__import__", no_whisper)
    with pytest.raises(RuntimeError, match="requirements-local.txt") as caught:
        stt._get_whisper()
    assert "Groq" in str(caught.value)
    assert stt._whisper_model is None
