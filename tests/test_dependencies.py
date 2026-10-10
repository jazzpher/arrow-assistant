"""Keep the normal install independent of the optional native STT stack."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_default_requirements_exclude_optional_speech():
    lines = [s.strip() for s in (ROOT / "requirements.txt").read_text().splitlines()
             if s.strip() and not s.lstrip().startswith("#")]
    assert not any(s.startswith(("faster-whisper", "ctranslate2", "pyttsx3")) for s in lines)
    assert not any(s.startswith("-r requirements-local") for s in lines)


def test_local_requirements_are_explicit():
    text = (ROOT / "requirements-local.txt").read_text()
    assert "faster-whisper>=1.2.0,<2" in text
    assert "pyttsx3>=2.90" in text
