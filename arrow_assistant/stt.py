"""Speech-to-text. Groq Whisper API (free tier) by default, local
faster-whisper as a no-key offline fallback.

Audio is captured from the default mic at 16kHz PCM16 while the hotkey
is held (push-to-talk), then transcribed on release.
"""
from __future__ import annotations

import io
import threading
import wave

import numpy as np

SAMPLE_RATE = 16_000
MIN_PRESS_SECONDS = 0.3   # shorter taps are accidents; never sent to STT


class MicRecorder:
    """Records mic audio while the hotkey is held."""

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._chunks: list[np.ndarray] = []
        self._stream = None
        self._lock = threading.Lock()

    def start(self) -> None:
        import sounddevice as sd
        with self._lock:
            self._chunks = []
            self._stream = sd.InputStream(
                samplerate=self.sample_rate, channels=1, dtype="int16",
                callback=lambda indata, frames, t, s: self._chunks.append(indata.copy()))
            self._stream.start()

    def stop(self) -> bytes:
        """Stop recording and return WAV bytes."""
        with self._lock:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return b""
        pcm = np.concatenate(chunks, axis=0)
        if len(pcm) < self.sample_rate * MIN_PRESS_SECONDS:
            return b""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm.tobytes())
        return buf.getvalue()


def transcribe(wav_bytes: bytes, provider: str, groq_key: str | None = None) -> str:
    if not wav_bytes:
        return ""
    if provider == "groq":
        return _transcribe_groq(wav_bytes, groq_key)
    return _transcribe_local(wav_bytes)


def _transcribe_groq(wav_bytes: bytes, api_key: str | None) -> str:
    import requests
    resp = requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": ("speech.wav", wav_bytes, "audio/wav")},
        data={"model": "whisper-large-v3-turbo", "response_format": "text"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text.strip()


_whisper_model = None
_whisper_lock = threading.Lock()


def _get_whisper():
    """Load the local model once and reuse it (loading is the slow part)."""
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            try:
                from faster_whisper import WhisperModel  # lazy: heavy import
            except ImportError as exc:
                raise RuntimeError(
                    "Local speech recognition is not installed or could not load. "
                    "Add your free Groq key in Settings to use cloud speech, or run "
                    "python -m pip install -r requirements-local.txt in a supported "
                    "64-bit Python environment (recommended: Python 3.12)."
                ) from exc
            _whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
        return _whisper_model


def _transcribe_local(wav_bytes: bytes) -> str:
    model = _get_whisper()
    segments, _ = model.transcribe(io.BytesIO(wav_bytes))
    return " ".join(seg.text.strip() for seg in segments).strip()
