"""Arrow Assistant orchestrator: wires hotkey, mic, capture, memory, KB,
LLM streaming, TTS, and overlay into the push-to-talk loop.

Threading rule: only pyqtSignal crosses thread boundaries. The pipeline
worker thread NEVER touches Qt widgets directly.
"""
from __future__ import annotations

import sys
import threading

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from . import ai, capture, config, kb
from .hotkey import HotkeyListener
from .memory import Memory
from .overlay import ArrowOverlay
from .points import parse_points
from .sentences import SentenceStreamer
from .stt import MicRecorder, transcribe
from .tray import Tray
from .tts import Speaker


class ArrowApp(QObject):
    show_points = pyqtSignal(list)
    status = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._qt_app = QApplication(sys.argv)
        capture.set_dpi_awareness()

        self.provider = config.select_llm()
        self.stt_provider = config.select_stt()
        self.memory = Memory()
        self.overlay = ArrowOverlay(self._qt_app)
        self.recorder = MicRecorder()
        self._busy = threading.Lock()
        self._cancel = threading.Event()

        self.show_points.connect(self.overlay.point_at)
        self.tray = Tray(self._qt_app, config.hotkey(), self._quit)

        self.hotkey = HotkeyListener(
            config.hotkey(), on_press=self._ptt_down, on_release=self._ptt_up)

    # -- hotkey callbacks (listener thread) --------------------------------
    def _ptt_down(self) -> None:
        self._cancel.set()  # cancel any in-flight answer
        self._cancel = threading.Event()
        try:
            self.recorder.start()
        except Exception as exc:  # no mic, etc.
            print(f"[arrow] mic error: {exc}", file=sys.stderr)

    def _ptt_up(self) -> None:
        wav = self.recorder.stop()
        if not wav:
            return
        if not self._busy.acquire(blocking=False):
            return
        threading.Thread(target=self._pipeline, args=(wav,), daemon=True).start()

    # -- pipeline (worker thread) -------------------------------------------
    def _pipeline(self, wav: bytes) -> None:
        cancel = self._cancel
        try:
            if self.provider is None:
                print("[arrow] no LLM key configured - see README setup", file=sys.stderr)
                return
            question = transcribe(wav, self.stt_provider,
                                  config.get_key("GROQ_API_KEY"))
            if not question or cancel.is_set():
                return
            app = capture.foreground_app()
            mon, img = capture.primary_shot()
            image_b64 = capture.encode_for_model(img)
            mem = self.memory.recall(app)
            kb_text = kb.lookup(app)

            streamer = SentenceStreamer()
            speaker = Speaker()
            speaker.start()
            full: list[str] = []
            try:
                for chunk in ai.stream_answer(
                        self.provider, question, app, mem, kb_text, image_b64):
                    if cancel.is_set():
                        speaker.stop()
                        return
                    full.append(chunk)
                    for sentence in streamer.feed(chunk):
                        speaker.say(sentence)
                for sentence in streamer.flush():
                    speaker.say(sentence)
            finally:
                speaker.finish()

            text, points = parse_points("".join(full))
            if text:
                self.memory.record(app, question, text)
            if points and not cancel.is_set():
                s = capture.scale_factor(img, mon)
                from .points import Point
                mapped = [
                    Point(mon["left"] + round(p.x / s),
                          mon["top"] + round(p.y / s), p.label)
                    for p in points]
                self.show_points.emit(mapped)
            speaker.wait()
        except Exception as exc:
            print(f"[arrow] pipeline error: {exc}", file=sys.stderr)
        finally:
            self._busy.release()

    def _quit(self) -> None:
        self.hotkey.stop()
        self.memory.close()
        self._qt_app.quit()

    def run(self) -> int:
        self.hotkey.start()
        print(f"[arrow] ready - hold {config.hotkey()} and ask. "
              f"LLM: {self.provider.name if self.provider else 'NONE (set a key)'} | "
              f"STT: {self.stt_provider}")
        return self._qt_app.exec()
