"""Arrow Assistant orchestrator: wires hotkey, mic, capture, memory, KB,
LLM streaming, TTS, and overlay into the push-to-talk loop.

Threading rule: only pyqtSignal crosses thread boundaries. The pipeline
worker thread NEVER touches Qt widgets directly.
"""
from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from . import ai, capture, config, kb
from .hotkey import HotkeyListener
from .memory import Memory
from .overlay import ArrowOverlay
from .errlog import log_error, log_diagnostic
from .points import Point, SpeechFilter, parse_points
from .sentences import SentenceStreamer
from .stt import MicRecorder, transcribe
from .tray import Tray
from .tts import Speaker
from .agent.hotkeys import AgentHotkeys
from .agent.stack import AgentStack
from .agent.trigger import parse_agent_request


class ArrowApp(QObject):
    show_points = pyqtSignal(list)
    status = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._qt_app = QApplication(sys.argv)
        capture.set_dpi_awareness()

        self.provider = config.select_llm()
        self.stt_provider = config.select_stt()
        self.memory = Memory()
        self.overlay = ArrowOverlay(self._qt_app)
        self.recorder = MicRecorder()
        self._cancel = threading.Event()   # one per question; set when a newer press supersedes it
        self._speaker: Speaker | None = None

        self.show_points.connect(self.overlay.point_at)
        self.tray = Tray(self._qt_app, config.hotkey(), self._quit)
        self.status.connect(self.tray.set_status)
        self.error.connect(self.tray.notify)

        self.hotkey = HotkeyListener(
            config.hotkey(), on_press=self._ptt_down, on_release=self._ptt_up)

        # agent mode (separate hotkey; teach mode above is unchanged)
        self.agent = AgentStack(self._qt_app, self.overlay, speak=self._speak_async)
        self._agent_ptt = False
        self.agent_keys = AgentHotkeys({
            config.agent_hotkey(): (self._agent_down, self._agent_up),
            config.panic_hotkey(): self.agent.stop,
            "ctrl+alt+y": self.agent.approve,
            "ctrl+alt+n": self.agent.skip,
        })

    def _speak_async(self, text: str) -> None:
        def run():
            try:
                sp = Speaker()
                sp.start()
                sp.say(text)
                sp.finish()
                sp.wait()
            except Exception as exc:  # TTS must never break the agent
                print(f"[arrow] tts error: {exc}", file=sys.stderr)
        threading.Thread(target=run, daemon=True).start()

    # -- agent hotkey (push-to-talk for a task) -------------------------------
    def _agent_down(self) -> None:
        self._agent_ptt = True
        try:
            self.recorder.start()
        except Exception as exc:
            print(f"[arrow] mic error: {exc}", file=sys.stderr)

    def _agent_up(self) -> None:
        if not self._agent_ptt:
            return
        self._agent_ptt = False
        wav = self.recorder.stop()
        if wav:
            threading.Thread(target=self._agent_from_wav, args=(wav,), daemon=True).start()

    def _agent_from_wav(self, wav: bytes) -> None:
        try:
            text = transcribe(wav, self.stt_provider, config.get_key("GROQ_API_KEY"))
        except Exception as exc:
            print(f"[arrow] stt error: {exc}", file=sys.stderr)
            return
        self.handle_agent_text(text)

    def handle_agent_text(self, text: str) -> None:
        """Called with the transcript of an agent request (hotkey or phrase)."""
        if not text:
            return
        cmd = parse_agent_request(text)
        if cmd is None:                       # agent hotkey: whole utterance is the task
            from .agent.trigger import AgentCommand
            cmd = AgentCommand("task", text.strip())
        if cmd.kind == "stop":
            self.agent.stop()
        elif cmd.kind == "continue":
            self.agent.start_task("", resume=True)
        else:
            self.agent.start_task(cmd.task)

    # -- hotkey callbacks (listener thread) --------------------------------
    def _ptt_down(self) -> None:
        self._cancel.set()  # cancel any in-flight answer
        self._cancel = threading.Event()
        sp = self._speaker
        if sp is not None:
            sp.stop()       # cut audio that is still playing
        try:
            self.recorder.start()
            self.status.emit("listening")
        except Exception as exc:  # no mic, etc.
            self._report(f"mic error: {exc}")

    def _ptt_up(self) -> None:
        wav = self.recorder.stop()   # b"" for accidental taps (< 0.3 s)
        if not wav:
            self.status.emit("idle")
            return
        self.status.emit("thinking")
        # No busy lock: a newer question always runs; the older one was
        # already cancelled by _ptt_down.
        threading.Thread(target=self._pipeline, args=(wav, self._cancel),
                         daemon=True).start()

    def _report(self, msg: str) -> None:
        log_error(msg)
        self.error.emit(msg[:200])

    def _gather_context(self):
        """Screen + memory + docs; independent of the transcript, so it
        runs while STT is in flight (and the screenshot matches the
        moment the user released the key)."""
        app = capture.foreground_app()
        mon, img = capture.primary_shot()
        image_b64 = capture.encode_for_model(img)
        return (app, mon, img, image_b64,
                self.memory.recall(app), kb.lookup(app))

    # -- pipeline (worker thread) -------------------------------------------
    def _pipeline(self, wav: bytes, cancel: threading.Event) -> None:
        speaker: Speaker | None = None
        try:
            if self.provider is None:
                self._report("no LLM key configured - see README setup")
                return
            with ThreadPoolExecutor(max_workers=2) as ex:
                f_stt = ex.submit(transcribe, wav, self.stt_provider,
                                  config.get_key("GROQ_API_KEY"))
                f_ctx = ex.submit(self._gather_context)
                question = f_stt.result()
                app, mon, img, image_b64, mem, kb_text = f_ctx.result()
            if not question or cancel.is_set():
                return
            if parse_agent_request(question) is not None:
                self.handle_agent_text(question)   # "Arrow agent, ..." in teach mode
                return

            streamer = SentenceStreamer()
            tags = SpeechFilter()
            speaker = Speaker()
            self._speaker = speaker
            speaker.start()
            spoken: list[str] = []
            shown: list[Point] = []
            s = capture.scale_factor(img, mon)
            enc_w, enc_h = capture.encoded_size(img)

            def say(text: str) -> None:
                spoken.append(text)
                for sentence in streamer.feed(text):
                    speaker.say(sentence)

            try:
                for chunk in ai.stream_answer(
                        self.provider, question, app, mem, kb_text, image_b64,
                        image_size=(enc_w, enc_h)):
                    if cancel.is_set():
                        speaker.stop()
                        return
                    text, pts = tags.feed(chunk)
                    if text:
                        say(text)
                    if pts and not cancel.is_set():
                        for p in pts:   # clamp inside the image the model saw
                            x = min(max(p.x, 0), enc_w - 1)
                            y = min(max(p.y, 0), enc_h - 1)
                            shown.append(Point(mon["left"] + round(x / s),
                                               mon["top"] + round(y / s), p.label))
                        # arrow appears while she is still talking
                        self.show_points.emit(list(shown))
                tail = tags.flush()
                log_diagnostic(
                    f"teach points: parsed={len(shown)} invalid={tags.invalid_tags} "
                    f"incomplete={tags.incomplete_tags} image={enc_w}x{enc_h} "
                    f"monitor={mon['left']},{mon['top']} scale={s:.4f}")
                if tail:
                    say(tail)
                for sentence in streamer.flush():
                    speaker.say(sentence)
            finally:
                speaker.finish()

            text = "".join(spoken).strip()
            if text:
                self.memory.record(app, question, text)
            speaker.wait()
        except Exception as exc:
            self._report(f"pipeline error: {exc}")
        finally:
            # Only clean up what this run owns: a newer question may already
            # have installed its own speaker (and its own tray status) while
            # this superseded run was still waiting on the network.
            if speaker is not None and self._speaker is speaker:
                self._speaker = None
            if not cancel.is_set():
                self.status.emit("idle")

    def _quit(self) -> None:
        self.hotkey.stop()
        self.agent_keys.stop()
        self.agent.stop()
        self.memory.close()
        self._qt_app.quit()

    def run(self) -> int:
        self.hotkey.start()
        self.agent_keys.start()
        print(f"[arrow] ready - hold {config.hotkey()} and ask. "
              f"LLM: {self.provider.name if self.provider else 'NONE (set a key)'} | "
              f"STT: {self.stt_provider}")
        print(f"[arrow] agent: hold {config.agent_hotkey()} (or say 'Arrow agent, ...') | "
              f"mode={config.agent_mode()} max_steps={config.agent_max_steps()} | "
              f"panic={config.panic_hotkey()} | "
              f"providers={[p.name for p in self.agent.providers] or 'NONE'}")
        return self._qt_app.exec()
