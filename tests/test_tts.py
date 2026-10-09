import threading

from arrow_assistant import tts


def test_speaker_prefetch_order(monkeypatch):
    synth_order, play_order = [], []
    monkeypatch.setattr(tts, "synthesize_edge",
                        lambda text, voice: synth_order.append(text) or b"MP3")
    monkeypatch.setattr(tts, "play_mp3",
                        lambda audio, stop: play_order.append(audio))
    s = tts.Speaker()
    s.start()
    s.say("One.")
    s.say("Two.")
    s.finish()
    s.wait()
    assert synth_order == ["One.", "Two."]
    assert play_order == [b"MP3", b"MP3"]


def test_speaker_stop_clears_queue(monkeypatch):
    played = []
    started = threading.Event()

    def slow_synth(text, voice):
        started.set()
        threading.Event().wait(0.2)
        return b"MP3"

    monkeypatch.setattr(tts, "synthesize_edge", slow_synth)
    monkeypatch.setattr(tts, "play_mp3", lambda a, st: played.append(a))
    s = tts.Speaker()
    s.start()
    s.say("One.")
    started.wait(2)
    s.say("Two.")
    s.say("Three.")
    s.stop()
    s.finish()
    s.wait()
    assert len(played) <= 2  # queued sentences were dropped


def test_offline_uses_pyttsx3_path(monkeypatch):
    spoken = []
    monkeypatch.setattr(tts, "speak_offline",
                        lambda text, stop: spoken.append(text))
    s = tts.Speaker(offline=True)
    s.start()
    s.say("Kamusta.")
    s.finish()
    s.wait()
    assert spoken == ["Kamusta."]
