import os

from arrow_assistant import config
from arrow_assistant.config import LLMProvider


def _clear(monkeypatch):
    for k in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "NVIDIA_API_KEY",
              "GROQ_API_KEY", "ARROW_HOTKEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(config, "_keyring_get", lambda name: None)


def test_gemini_wins(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    p = config.select_llm()
    assert p.name == "gemini" and p.api_key == "g"


def test_openrouter_second(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    p = config.select_llm()
    assert p.name == "openrouter"
    assert "openrouter.ai" in p.base_url


def test_nvidia_third(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("NVIDIA_API_KEY", "n")
    p = config.select_llm()
    assert p.name == "nvidia"
    assert "nvidia.com" in p.base_url


def test_none_when_no_keys(monkeypatch):
    _clear(monkeypatch)
    assert config.select_llm() is None


def test_stt_groq_with_key(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "x")
    assert config.select_stt() == "groq"


def test_stt_local_without_key(monkeypatch):
    _clear(monkeypatch)
    assert config.select_stt() == "local"


def test_hotkey_default(monkeypatch):
    _clear(monkeypatch)
    assert config.hotkey() == "ctrl+alt+space"


def test_hotkey_override(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ARROW_HOTKEY", "CTRL+SHIFT+Q")
    assert config.hotkey() == "ctrl+shift+q"
