import pytest

from arrow_assistant.agent.actions import parse_action
from arrow_assistant.agent.risk import RiskContext, assess


def ctx(raw, app="notepad.exe", title="Untitled", label="", **kw):
    a = parse_action(raw)
    return RiskContext(a, app, title, label or a.label, "task", **kw)


def level(raw, **kw):
    return assess(ctx(raw, **kw)).level


# ---- hard blocks --------------------------------------------------------------
@pytest.mark.parametrize("app,title", [
    ("keepassxc.exe", "Database"), ("1password.exe", "Vault"),
    ("consent.exe", "User Account Control"), ("explorer.exe", "Windows Security"),
    ("chrome.exe", "Bitwarden - Password Manager"), ("regedit.exe", "Registry Editor"),
    ("chrome.exe", "Enter your one-time code"),
])
def test_sensitive_windows_block_everything(app, title):
    assert level('{"action":"click","x":1,"y":1,"label":"OK"}', app=app, title=title) == "block"
    assert level('{"action":"key","keys":["a"]}', app=app, title=title) == "block"


@pytest.mark.parametrize("label", ["Password", "Enter PIN", "OTP code", "CVV", "Card number",
                                   "Verification code", "Recovery key", "API key"])
def test_typing_into_secret_fields_blocked(label):
    assert level('{"action":"type","text":"hunter2"}', label=label) == "block"


def test_typing_into_uia_password_field_blocked_even_with_innocent_label():
    assert level('{"action":"type","text":"x","label":"Box"}', target_is_password=True) == "block"


def test_typing_six_digits_with_flagged_risk_blocked_but_plain_numbers_ok():
    assert level('{"action":"type","text":"123456","risk":"high"}') == "block" or \
        level('{"action":"type","text":"123456","risk":"high"}') == "block"
    assert level('{"action":"type","text":"123456"}') == "safe"


def test_typing_in_banking_window_blocked_clicking_needs_confirm():
    assert level('{"action":"type","text":"5000"}', title="BDO Online Banking") == "block"
    assert level('{"action":"click","x":1,"y":1,"label":"Next"}', title="GCash - Payment") == "confirm"


def test_blocked_hotkeys():
    assert level('{"action":"key","keys":["ctrl","alt","delete"]}') == "block"
    assert level('{"action":"key","keys":["win","l"]}') == "block"


def test_own_hud_blocked():
    assert level('{"action":"click","x":1,"y":1}', pt=(10, 10), hud_rects=[(0, 0, 50, 50)]) == "block"


# ---- confirm ---------------------------------------------------------------------
@pytest.mark.parametrize("label", [
    "Send", "Submit", "Post", "Publish", "Reply", "Pay now", "Buy now", "Place order",
    "Checkout", "Delete", "Remove", "Empty Recycle Bin", "Format", "Uninstall", "Install",
    "Allow", "Sign out", "Change password", "Transfer", "Ipadala", "Burahin", "Bayaran",
    "I-post", "Confirm", "Permanently delete",
])
def test_risky_click_labels_need_confirm(label):
    assert level('{"action":"click","x":1,"y":1}', label=label) == "confirm"


@pytest.mark.parametrize("label", ["Save", "Open", "Search", "File", "Next page", "Bold",
                                   "Copy", "Settings", "Close tab", "Reading list"])
def test_ordinary_labels_are_safe(label):
    assert level('{"action":"click","x":1,"y":1}', label=label) == "safe"


def test_word_boundaries_do_not_false_positive():
    assert level('{"action":"click","x":1,"y":1}', label="Resend count") == "safe"
    assert level('{"action":"click","x":1,"y":1}', label="Postal code") == "safe"
    assert level('{"action":"click","x":1,"y":1}', label="Postcard") == "safe"


def test_model_risk_hint_triggers_confirm_but_cannot_lower_risk():
    assert level('{"action":"click","x":1,"y":1,"risk":"high"}', label="Save") == "confirm"
    # model says low, label says delete: label wins
    assert level('{"action":"click","x":1,"y":1,"risk":"low"}', label="Delete") == "confirm"


def test_risky_keys_confirm():
    assert level('{"action":"key","keys":["win","r"]}') == "confirm"
    assert level('{"action":"key","keys":["alt","f4"]}') == "confirm"
    assert level('{"action":"key","keys":["delete"]}') == "confirm"
    assert level('{"action":"key","keys":["ctrl","s"]}') == "safe"


def test_enter_in_messaging_windows_confirm_but_not_in_notepad():
    assert level('{"action":"key","keys":["enter"]}', title="Messenger - Maria") == "confirm"
    assert level('{"action":"key","keys":["enter"]}', title="Untitled - Notepad") == "safe"
    assert level('{"action":"key","keys":["ctrl","enter"]}', title="Gmail") == "confirm"


def test_newline_text_in_chat_confirm():
    assert level('{"action":"type","text":"hi\\nthere"}', title="WhatsApp") == "confirm"
    assert level('{"action":"type","text":"hi\\nthere"}', title="Notepad") == "safe"


def test_terminals_always_confirm():
    assert level('{"action":"type","text":"dir"}', app="cmd.exe", title="Command Prompt") == "confirm"
    assert level('{"action":"click","x":1,"y":1}', app="powershell.exe") == "confirm"
    assert level('{"action":"open_app","app":"PowerShell"}') == "confirm"


def test_installers_confirm():
    assert level('{"action":"open_app","app":"setup.exe"}') == "confirm"
    assert level('{"action":"open_app","app":"notepad"}') == "safe"


def test_scope_limit_confirms_outside_apps():
    r = assess(ctx('{"action":"click","x":1,"y":1,"label":"OK"}', app="chrome.exe",
                   scope_apps=frozenset({"excel.exe"})))
    assert r.level == "confirm" and "outside" in r.reasons[0]
    assert assess(ctx('{"action":"click","x":1,"y":1,"label":"OK"}', app="excel.exe",
                      scope_apps=frozenset({"excel.exe"}))).level == "safe"


def test_passive_and_terminal_actions_safe():
    assert level('{"action":"scroll","amount":3}') == "safe"
    assert level('{"action":"wait","seconds":1}') == "safe"
    assert level('{"action":"done","message":"ok"}') == "safe"


def test_reasons_are_deduplicated_and_explained():
    r = assess(ctx('{"action":"click","x":1,"y":1,"risk":"high"}', label="Delete",
                   title="Bank transfer"))
    assert r.level == "confirm" and len(r.reasons) >= 2
    assert len(set(r.reasons)) == len(r.reasons)
