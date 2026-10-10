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


def _audio_stream(monkeypatch, on_write=None):
    """Use actual miniaudio decode without opening a real audio device."""
    import sys
    from types import SimpleNamespace
    writes, options = [], {}

    class Stream:
        def __init__(self, **kwargs):
            options.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def write(self, samples):
            writes.append(samples.copy())
            if on_write:
                on_write()

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(OutputStream=Stream))
    return writes, options


def test_play_mp3_decodes_real_bytes_in_memory(monkeypatch):
    from pathlib import Path
    import miniaudio
    import numpy as np
    audio = (Path(__file__).parent / "fixtures" / "tone.mp3").read_bytes()
    calls = []
    decode = miniaudio.decode

    def real_decode(data, **kwargs):
        assert isinstance(data, bytes)
        calls.append(data)
        return decode(data, **kwargs)

    def no_file_decode(*args, **kwargs):
        raise AssertionError("MP3 bytes must not use the filename decoder")

    monkeypatch.setattr(miniaudio, "decode", real_decode)
    monkeypatch.setattr(miniaudio, "decode_file", no_file_decode)
    writes, options = _audio_stream(monkeypatch)
    tts.play_mp3(audio, threading.Event())
    assert calls == [audio]
    assert options == {"samplerate": 44100, "channels": 1, "dtype": "float32"}
    samples = np.concatenate(writes)
    assert samples.dtype == np.float32
    assert np.isfinite(samples).all()
    assert np.any(samples != 0)
    assert all(len(chunk) <= 4096 for chunk in writes)


def test_play_mp3_honors_stop_between_chunks(monkeypatch):
    import array
    import sys
    from types import SimpleNamespace
    stop = threading.Event()
    monkeypatch.setitem(sys.modules, "miniaudio", SimpleNamespace(
        SampleFormat=SimpleNamespace(FLOAT32="float32"),
        decode=lambda data, **kwargs: SimpleNamespace(
            sample_rate=24000, samples=array.array("f", [0.1] * 9000))))
    writes, options = _audio_stream(monkeypatch, stop.set)
    tts.play_mp3(b"mp3", stop)
    assert len(writes) == 1
    assert len(writes[0]) == 4096
    assert options["samplerate"] == 24000


def test_play_mp3_does_not_write_when_already_stopped(monkeypatch):
    from pathlib import Path
    stop = threading.Event()
    stop.set()
    writes, _ = _audio_stream(monkeypatch)
    audio = (Path(__file__).parent / "fixtures" / "tone.mp3").read_bytes()
    tts.play_mp3(audio, stop)
    assert writes == []
