"""Text-to-speech. Default: edge-tts (free neural voices, no API key).
Offline fallback: pyttsx3 (Windows SAPI).

A generator worker synthesizes sentence t+1 while the player is still
speaking sentence t, so inter-sentence gaps stay near zero - the same
double-buffer trick Clicky uses for its Cartesia path.
"""
from __future__ import annotations

import asyncio
import io
import queue
import threading

DEFAULT_VOICE = "en-US-ChristopherNeural"  # friendly free voice
TAGALOG_VOICE = "fil-PH-BlessicaNeural"    # free Filipino voice


def synthesize_edge(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """Return MP3 bytes for one sentence via edge-tts."""
    import edge_tts

    async def _go() -> bytes:
        buf = io.BytesIO()
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        return buf.getvalue()

    return asyncio.run(_go())


def play_mp3(mp3_bytes: bytes, stop: threading.Event) -> None:
    """Decode MP3 with miniaudio and play it, honoring cancellation."""
    import miniaudio
    import numpy as np
    import sounddevice as sd

    decoded = miniaudio.decode_file(io.BytesIO(mp3_bytes),
                                    output_format=miniaudio.SampleFormat.FLOAT32,
                                    nchannels=1)
    samples = np.frombuffer(decoded.samples, dtype=np.float32)
    with sd.OutputStream(samplerate=decoded.sample_rate, channels=1,
                         dtype="float32") as out:
        step = 4096
        for i in range(0, len(samples), step):
            if stop.is_set():
                break
            out.write(samples[i:i + step])


def speak_offline(text: str, stop: threading.Event) -> None:
    """No-key offline path: Windows SAPI via pyttsx3."""
    import pyttsx3
    engine = pyttsx3.init()
    engine.say(text)
    engine.runAndWait()


class Speaker:
    """Sentence queue with prefetch (synthesize t+1 while playing t)."""

    def __init__(self, voice: str = DEFAULT_VOICE, offline: bool = False):
        self.voice = voice
        self.offline = offline
        self._q: queue.Queue[str | None] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def say(self, sentence: str) -> None:
        self._q.put(sentence)

    def finish(self) -> None:
        self._q.put(None)

    def stop(self) -> None:
        self._stop.set()
        with self._q.mutex:
            self._q.queue.clear()

    def wait(self) -> None:
        if self._thread:
            self._thread.join(timeout=30)

    def _synthesize(self, text: str) -> bytes | None:
        if self.offline:
            return None  # offline path speaks text directly
        try:
            return synthesize_edge(text, self.voice)
        except Exception:
            return b""

    def _run(self) -> None:
        prefetch: queue.Queue[tuple[str, bytes | None] | None] = queue.Queue(maxsize=1)

        def gen() -> None:
            while not self._stop.is_set():
                item = self._q.get()
                if item is None:
                    prefetch.put(None)
                    return
                prefetch.put((item, self._synthesize(item)))

        threading.Thread(target=gen, daemon=True).start()
        while True:
            item = prefetch.get()
            if item is None:
                return
            text, audio = item
            if self._stop.is_set():
                return
            if self.offline:
                speak_offline(text, self._stop)
            elif audio:
                play_mp3(audio, self._stop)
