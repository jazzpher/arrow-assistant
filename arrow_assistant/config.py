"""Configuration: provider selection and API keys.

Priority for keys: environment / .env first, then Windows Credential
Manager (keyring). Nothing is ever sent anywhere except the provider
you picked - there is no proxy server in this app.

Free-tier defaults:
  LLM  : Gemini (AI Studio free key) > OpenRouter (free models) > NVIDIA Build
  STT  : Groq (free tier) > local faster-whisper (no key)
  TTS  : edge-tts (free, no key) > local pyttsx3 (no key)
"""
from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv is a nicety, not a requirement
    pass

_SERVICE = "arrow-assistant"


def _keyring_get(name: str) -> str | None:
    try:
        import keyring
        return keyring.get_password(_SERVICE, name)
    except Exception:
        return None


def get_key(name: str) -> str | None:
    """Read an API key from env/.env, falling back to the OS keyring."""
    val = os.environ.get(name)
    if val:
        return val.strip()
    return _keyring_get(name)


def save_key(name: str, value: str) -> None:
    try:
        import keyring
        keyring.set_password(_SERVICE, name, value)
    except Exception:
        pass  # settings dialog surfaces the failure; env still works


DEFAULT_HOTKEY = "ctrl+alt+space"


@dataclass(frozen=True)
class LLMProvider:
    name: str          # gemini | openrouter | nvidia
    api_key: str
    model: str
    base_url: str | None = None  # OpenAI-compatible endpoint when set


GEMINI_MODEL = os.environ.get("ARROW_GEMINI_MODEL", "gemini-2.5-flash")
OPENROUTER_MODEL = os.environ.get(
    "ARROW_OPENROUTER_MODEL", "google/gemini-2.0-flash-exp:free")
NVIDIA_MODEL = os.environ.get(
    "ARROW_NVIDIA_MODEL", "meta/llama-3.2-90b-vision-instruct")
GROQ_WHISPER_MODEL = "whisper-large-v3-turbo"


def select_llm() -> LLMProvider | None:
    """Pick the first configured vision LLM, in free-tier-friendly order."""
    key = get_key("GEMINI_API_KEY")
    if key:
        return LLMProvider("gemini", key, GEMINI_MODEL)
    key = get_key("OPENROUTER_API_KEY")
    if key:
        return LLMProvider("openrouter", key, OPENROUTER_MODEL,
                           base_url="https://openrouter.ai/api/v1")
    key = get_key("NVIDIA_API_KEY")
    if key:
        return LLMProvider("nvidia", key, NVIDIA_MODEL,
                           base_url="https://integrate.api.nvidia.com/v1")
    return None


def select_stt() -> str:
    """Return 'groq' when a Groq key exists, else 'local'."""
    return "groq" if get_key("GROQ_API_KEY") else "local"


def hotkey() -> str:
    return os.environ.get("ARROW_HOTKEY", DEFAULT_HOTKEY).lower()


# -- agent mode -------------------------------------------------------------
AGENT_MODES = ("step", "task", "auto")


def agent_hotkey() -> str:
    return os.environ.get("ARROW_AGENT_HOTKEY", "ctrl+alt+a").lower()


def panic_hotkey() -> str:
    return os.environ.get("ARROW_PANIC_HOTKEY", "ctrl+alt+esc").lower()


def agent_mode() -> str:
    """step (confirm every action, default) | task | auto."""
    m = os.environ.get("ARROW_AGENT_MODE", "step").lower()
    return m if m in AGENT_MODES else "step"


def agent_max_steps() -> int:
    try:
        n = int(os.environ.get("ARROW_AGENT_MAX_STEPS", "25"))
    except ValueError:
        n = 25
    return max(1, min(n, 200))


AGENT_GEMINI_MODEL = os.environ.get("ARROW_AGENT_GEMINI_MODEL", "gemini-2.5-flash")
AGENT_OPENROUTER_MODEL = os.environ.get(
    "ARROW_AGENT_OPENROUTER_MODEL", "google/gemini-2.0-flash-exp:free")
AGENT_NVIDIA_MODEL = os.environ.get(
    "ARROW_AGENT_NVIDIA_MODEL", "meta/llama-3.2-90b-vision-instruct")


def select_agent_providers() -> list[LLMProvider]:
    """Every configured vision provider, best planner first.

    The agent loop makes one call per step, so it needs a fallback chain:
    when one free tier runs out, the next provider takes over.
    """
    out: list[LLMProvider] = []
    key = get_key("GEMINI_API_KEY")
    if key:
        out.append(LLMProvider("gemini", key, AGENT_GEMINI_MODEL))
    key = get_key("NVIDIA_API_KEY")
    if key:
        out.append(LLMProvider("nvidia", key, AGENT_NVIDIA_MODEL,
                               base_url="https://integrate.api.nvidia.com/v1"))
    key = get_key("OPENROUTER_API_KEY")
    if key:
        out.append(LLMProvider("openrouter", key, AGENT_OPENROUTER_MODEL,
                               base_url="https://openrouter.ai/api/v1"))
    return out
