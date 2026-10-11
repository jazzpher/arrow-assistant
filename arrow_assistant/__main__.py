import os
import sys

from . import __version__


def _frozen_stdio() -> None:
    """A windowed .exe has no console: send prints to %APPDATA%\\Arrow\\arrow.log."""
    if getattr(sys, "frozen", False) and (sys.stdout is None or sys.stderr is None):
        from .config import user_dir
        os.makedirs(user_dir(), exist_ok=True)
        log = open(os.path.join(user_dir(), "arrow.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def self_test(out_path: str | None = None) -> int:
    """Import everything the app needs, without opening windows or the mic.
    Used by CI against the built .exe. Writes 'OK ...' or the error."""
    lines, code = [], 0
    try:
        import importlib
        mods = ["PyQt6.QtWidgets", "pynput", "mss", "requests", "PIL", "numpy",
                "sounddevice", "miniaudio", "edge_tts", "keyring", "dotenv",
                "arrow_assistant.app", "arrow_assistant.agent.stack",
                "arrow_assistant.agent.tools", "arrow_assistant.setup_dialog"]
        if sys.platform == "win32":
            mods += ["uiautomation", "pyautogui"]
        for m in mods:
            importlib.import_module(m)
        import keyring
        backend = type(keyring.get_keyring()).__name__
        if sys.platform == "win32" and "Windows" not in backend:
            raise RuntimeError(f"keyring backend is {backend}, expected WinVaultKeyring")
        lines.append(f"OK arrow {__version__} python {sys.version.split()[0]} keyring {backend}")
    except Exception as exc:  # report, don't crash, so CI sees the reason
        lines.append(f"FAIL {type(exc).__name__}: {exc}")
        code = 1
    text = "\n".join(lines)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return code


def main() -> int:
    args = sys.argv[1:]
    _frozen_stdio()
    if "--version" in args:
        print(__version__)
        return 0
    if "--self-test" in args:
        i = args.index("--self-test")
        out = args[i + 1] if len(args) > i + 1 else None
        return self_test(out)
    from .app import ArrowApp
    return ArrowApp().run()


if __name__ == "__main__":
    raise SystemExit(main())
