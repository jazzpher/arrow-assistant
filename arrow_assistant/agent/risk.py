"""Risk gate. Filled in by the safety milestone; the loop only depends on
the Assessment shape, so M2 ships with a permissive default."""
from __future__ import annotations

from dataclasses import dataclass, field

from .actions import Action


@dataclass(frozen=True)
class Assessment:
    level: str = "safe"            # safe | confirm | block
    reasons: tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        return self.level == "block"

    @property
    def confirm(self) -> bool:
        return self.level == "confirm"


SAFE = Assessment()


def assess_permissive(ctx) -> Assessment:
    return SAFE


@dataclass
class RiskContext:
    action: Action
    app: str = "unknown"
    title: str = ""
    label: str = ""                # what the action targets (UIA name wins over model label)
    task: str = ""
    pt: tuple[int, int] | None = None
    scope_apps: frozenset = field(default_factory=frozenset)
    hud_rects: list = field(default_factory=list)
    target_is_password: bool = False


# ---------------------------------------------------------------------------
# The real gate. Code-level, not prompt-level: the model cannot talk its way
# past it. Order: hard blocks first, then always-confirm, then safe.
# ---------------------------------------------------------------------------
import re  # noqa: E402

from .actions import POINTER_KINDS  # noqa: E402

# Never touch these apps at all (we do not even screenshot-reason about them).
NOGO_APPS = frozenset({
    "keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe",
    "lastpass.exe", "dashlane.exe", "enpass.exe", "nordpass.exe",
    "credwiz.exe", "consent.exe", "windowsdefender.exe", "securityhealthsystray.exe",
    "securityhealthhost.exe", "mpcmdrun.exe", "msiexec.exe", "regedit.exe",
    "authenticator.exe", "winauthenticator.exe",
})
NOGO_TITLE = re.compile(
    r"(password manager|keepass|bitwarden|1password|lastpass|dashlane|"
    r"credential manager|windows security|virus (&|and) threat|user account control|"
    r"registry editor|authenticator|recovery key|2fa|two-factor|one-time (code|password))",
    re.I)
TERMINAL_APPS = frozenset({"cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe",
                           "wt.exe", "conhost.exe", "bash.exe", "wsl.exe"})
FINANCE_TITLE = re.compile(
    r"(bank|banking|paypal|gcash|maya|paymaya|checkout|payment|billing|credit card|"
    r"debit card|\bbdo\b|\bbpi\b|metrobank|unionbank|landbank|security bank|wise|"
    r"stripe|wallet|invoice|transfer funds)", re.I)
MESSAGING_TITLE = re.compile(
    r"(messenger|whatsapp|telegram|discord|slack|gmail|outlook|compose|inbox|"
    r"mail|viber|signal|teams|chat|reply|new message|facebook|instagram|twitter|\bx\b)", re.I)

# words on the thing being clicked (EN + Tagalog)
RISKY_LABEL = re.compile(
    r"\b(send|submit|post|publish|reply|tweet|share|forward|broadcast|"
    r"pay|buy|purchase|checkout|check out|place order|order now|confirm order|"
    r"subscribe|donate|transfer|withdraw|deposit|cash ?out|"
    r"delete|remove|erase|empty|format|wipe|reset|uninstall|install|setup|"
    r"allow|grant|authorize|run as|sign out|log ?out|unfollow|unfriend|block|"
    r"deactivate|close account|change password|"
    r"ipadala|padala|i-?send|i-?post|i-?share|bayad|bayaran|bumili|bilhin|"
    r"burahin|i-?delete|tanggalin|alisin|i-?uninstall|i-?install|"
    r"confirm|yes, ?delete|permanently)\b", re.I)
SECRET_LABEL = re.compile(
    r"(password|passcode|passphrase|\bpin\b|\botp\b|one[- ]time|verification code|"
    r"security code|\bcvv\b|\bcvc\b|card number|account number|\bssn\b|secret|"
    r"recovery (key|code)|private key|api key|token)", re.I)
RISKY_KEYS = [
    ({"win", "r"}, "opens the Run dialog"),
    ({"alt", "f4"}, "closes the active window"),
    ({"ctrl", "shift", "delete"}, "clears browser data"),
    ({"shift", "delete"}, "permanently deletes"),
    ({"win", "x"}, "opens the power-user menu"),
    ({"ctrl", "shift", "esc"}, "opens Task Manager"),
    ({"delete"}, "deletes the selection"),
]
BLOCKED_KEYS = [
    ({"ctrl", "alt", "delete"}, "Ctrl+Alt+Del is off limits"),
    ({"win", "l"}, "locking the PC is off limits"),
]
OTP_TEXT = re.compile(r"^\s*\d{4,8}\s*$")
INSTALLER_APP = re.compile(r"(setup|install|update|uninstall|msi)", re.I)


def _in_rects(pt, rects) -> bool:
    return bool(pt) and any(r[0] <= pt[0] <= r[2] and r[1] <= pt[1] <= r[3] for r in rects)


def assess(ctx: RiskContext) -> Assessment:
    a = ctx.action
    app = (ctx.app or "").lower()
    title = ctx.title or ""
    label = ctx.label or ""
    block: list[str] = []
    confirm: list[str] = []

    # ---- hard blocks ------------------------------------------------------
    if app in NOGO_APPS or NOGO_TITLE.search(title):
        block.append(f"'{app}' / '{title[:40]}' is a sensitive window (passwords, security, admin)")
    if _in_rects(ctx.pt, ctx.hud_rects):
        block.append("target is Arrow's own control panel")
    if a.kind == "type":
        text = a.text or ""
        if ctx.target_is_password or SECRET_LABEL.search(label) or SECRET_LABEL.search(title):
            block.append("typing into a password / code / card field is never allowed")
        elif OTP_TEXT.match(text) and (a.risk_hint != "low" or SECRET_LABEL.search(label)):
            block.append("typing a one-time code is never allowed")
        if FINANCE_TITLE.search(title):
            block.append("typing inside a banking/payment window is never allowed")
    if a.kind == "key":
        ks = set(a.keys)
        for combo, why in BLOCKED_KEYS:
            if combo <= ks:
                block.append(why)
    if app in TERMINAL_APPS and a.kind in ("type", "key") and a.kind == "type":
        confirm.append(f"typing a command into {app}")
    if block:
        return Assessment("block", tuple(block))

    # ---- always confirm (even in auto mode) -----------------------------------
    if a.risk_hint == "high":
        confirm.append("the model itself marked this as high risk")
    elif a.risk_hint == "medium" and a.kind != "wait":
        confirm.append("the model marked this as medium risk")
    if a.kind in POINTER_KINDS or a.kind == "drag":
        if RISKY_LABEL.search(label):
            confirm.append(f"touches '{label}' (send/pay/delete/install-type control)")
        if FINANCE_TITLE.search(title):
            confirm.append("inside a banking/payment window")
    if a.kind == "key":
        ks = set(a.keys)
        for combo, why in RISKY_KEYS:
            if combo <= ks:
                confirm.append(why)
        if ks & {"enter"} and not (ks - {"enter"}) and (
                MESSAGING_TITLE.search(title) or FINANCE_TITLE.search(title)):
            confirm.append("Enter may send or submit here")
        if ks >= {"ctrl", "enter"}:
            confirm.append("Ctrl+Enter usually sends")
    if a.kind == "type" and a.text and "\n" in a.text and (
            MESSAGING_TITLE.search(title) or FINANCE_TITLE.search(title)):
        confirm.append("text with a newline may send in this window")
    if a.kind == "open_app":
        if INSTALLER_APP.search(a.app or ""):
            confirm.append("launching an installer/updater")
        if (a.app or "").lower().replace(".exe", "") in {"cmd", "powershell", "pwsh", "terminal", "windows terminal", "wsl"}:
            confirm.append("opening a command line")
    if app in TERMINAL_APPS and a.kind != "wait":
        confirm.append("command-line window: any input can run a command")
    if ctx.scope_apps and app and app not in ctx.scope_apps and a.is_physical:
        confirm.append(f"'{app}' is outside the apps this task was limited to")
    if confirm:
        return Assessment("confirm", tuple(dict.fromkeys(confirm)))
    return SAFE


def is_sensitive_window(app: str, title: str) -> bool:
    """True when the screenshot itself must not leave the PC."""
    return (app or "").lower() in NOGO_APPS or bool(NOGO_TITLE.search(title or ""))
