"""First-run privacy notice. Password-field blocking is NOT full screenshot privacy."""
from __future__ import annotations

import os
import sys
from pathlib import Path

NOTICE = (
    "PRIVACY: agent mode sends screenshots of your screen to cloud AI providers "
    "(Gemini, NVIDIA, OpenRouter). Free tiers may use what you send to improve their products "
    "(Gemini free tier: 'Used to improve our products: Yes'), so treat every screenshot as "
    "not private. Arrow blocks password/OTP/card fields and sensitive windows, but that does not "
    "cover everything visible on screen: chats, emails, documents, notifications. "
    "Close anything private before you start, and consider ARROW_AGENT_SCOPE (app allowlist)."
)
ENV_OK = "ARROW_AGENT_PRIVACY_OK"


def ack_path() -> Path:
    return Path.home() / "Documents" / "Arrow Logs" / "privacy_ack.txt"


def acknowledged() -> bool:
    return os.environ.get(ENV_OK) == "1" or ack_path().exists()


def record_ack() -> None:
    p = ack_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("acknowledged: screenshots go to cloud providers\n", encoding="utf-8")


def ensure_console(input_fn=input, out=print) -> bool:
    """Show the notice once; True when the user has accepted (now or earlier)."""
    if acknowledged():
        return True
    out(NOTICE)
    if not sys.stdin or not getattr(sys.stdin, "isatty", lambda: False)() and input_fn is input:
        out(f"Not interactive. Run `python -m arrow_assistant.agent.privacy` once to accept.")
        return False
    if input_fn("Type YES to accept and continue: ").strip().upper() == "YES":
        record_ack()
        return True
    return False


def main() -> int:
    return 0 if ensure_console() else 1


if __name__ == "__main__":
    sys.exit(main())
