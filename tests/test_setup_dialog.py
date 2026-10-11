import os

import pytest

from arrow_assistant import config, setup_dialog
from arrow_assistant.__main__ import self_test


def test_save_values_skips_blanks():
    saved = {}
    names = setup_dialog.save_values(
        {"GEMINI_API_KEY": " g ", "GROQ_API_KEY": "", "NVIDIA_API_KEY": None},
        save=lambda n, v: saved.__setitem__(n, v))
    assert names == ["GEMINI_API_KEY"] and saved == {"GEMINI_API_KEY": "g"}


def test_has_llm_key(monkeypatch):
    for k in setup_dialog.LLM_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(config, "_keyring_get", lambda name: None)
    assert not setup_dialog.has_llm_key()
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    assert setup_dialog.has_llm_key()


def test_skip_setup(monkeypatch):
    monkeypatch.setenv("ARROW_SKIP_SETUP", "1")
    assert config.skip_setup()
    monkeypatch.delenv("ARROW_SKIP_SETUP")
    assert not config.skip_setup()


def test_user_dir_uses_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert config.user_dir() == os.path.join(str(tmp_path), "Arrow")


def test_dialog_builds(qtbot_or_app):
    dlg = setup_dialog.KeysDialog()
    assert set(dlg.values()) == {f[0] for f in setup_dialog.FIELDS}


def test_self_test_writes_result(tmp_path):
    out = tmp_path / "r.txt"
    code = self_test(str(out))
    text = out.read_text()
    assert text.startswith("OK") or text.startswith("FAIL")
    assert (code == 0) == text.startswith("OK")


@pytest.fixture
def qtbot_or_app():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
