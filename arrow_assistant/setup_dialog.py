"""First-run window for API keys, so the installed app needs no .env file.

Keys are stored in Windows Credential Manager through keyring (config.save_key),
never in a plain file. Shown at startup when no LLM key exists, and from the
tray menu ("API keys...").
"""
from __future__ import annotations

from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel,
                             QLineEdit, QVBoxLayout)

from . import config

FIELDS = [
    ("GEMINI_API_KEY", "Gemini key (recommended)", "https://aistudio.google.com/apikey"),
    ("GROQ_API_KEY", "Groq key (for your voice)", "https://console.groq.com/keys"),
    ("OPENROUTER_API_KEY", "OpenRouter key (optional)", "https://openrouter.ai/keys"),
    ("NVIDIA_API_KEY", "NVIDIA key (optional)", "https://build.nvidia.com"),
]
LLM_KEYS = ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "NVIDIA_API_KEY")


def has_llm_key() -> bool:
    return any(config.get_key(k) for k in LLM_KEYS)


def save_values(values: dict[str, str], save=config.save_key) -> list[str]:
    """Save non-empty values; returns the names saved."""
    saved = []
    for name, value in values.items():
        value = (value or "").strip()
        if value:
            save(name, value)
            saved.append(name)
    return saved


class KeysDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Arrow Assistant - API keys")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Paste your free API keys. You need a Gemini (or OpenRouter / NVIDIA) key "
            "for answers and a Groq key so Arrow can hear you.<br>"
            + " &middot; ".join(f'<a href="{url}">{label.split(" (")[0]}</a>'
                                for _, label, url in FIELDS[:2])
            + "<br>Keys are saved in Windows Credential Manager. Leave a box empty to keep "
              "the key you already saved.")
        intro.setWordWrap(True)
        intro.setOpenExternalLinks(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.edits: dict[str, QLineEdit] = {}
        for name, label, _ in FIELDS:
            e = QLineEdit(self)
            e.setEchoMode(QLineEdit.EchoMode.Password)
            e.setPlaceholderText("saved" if config.get_key(name) else "")
            form.addRow(label, e)
            self.edits[name] = e
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, str]:
        return {name: e.text() for name, e in self.edits.items()}


def ask_for_keys(parent=None) -> bool:
    """Show the dialog; True when something was saved."""
    dlg = KeysDialog(parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    return bool(save_values(dlg.values()))
