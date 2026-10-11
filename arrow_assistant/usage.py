"""Daily API usage tracker + "warn me before it costs money" guard.

Counts requests and tokens per provider/model per LOCAL calendar day and
keeps them in %APPDATA%\\Arrow\\usage.json (config.user_dir()). When a
provider crosses 80% and again 100% of its daily *warn* budget, the
notifier (tray + one short TTS line, wired in app.py) fires once each.
An optional *hard* limit refuses new calls for that provider for the
rest of the day.

Budgets are Arrow's own conservative guesses, NOT the providers' limits:
free-tier limits change often, so check each provider's rate-limit page
and set the env vars (see .env.example / README "Will this cost me money?").

    ARROW_DAILY_REQUEST_WARN[_<PROVIDER>]   warn budget, requests/day
    ARROW_DAILY_TOKEN_WARN[_<PROVIDER>]     warn budget, tokens/day
    ARROW_DAILY_REQUEST_LIMIT[_<PROVIDER>]  hard stop, requests/day (0/off = none)
    ARROW_DAILY_TOKEN_LIMIT[_<PROVIDER>]    next-call token threshold, may overshoot (0/off = none)
    ARROW_USAGE_ALERTS=0                    no tray/voice alerts (still counts)

Reservations fail closed on lock, persistence or corrupt counter failures.
Token thresholds are not dollar or token caps.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from functools import wraps

PROVIDERS = ("gemini", "openrouter", "nvidia", "groq")
DISPLAY = {"gemini": "Gemini", "openrouter": "OpenRouter", "nvidia": "NVIDIA",
           "groq": "Groq"}

# Conservative defaults (requests per local day). Deliberately low so the
# warning comes early. Not provider facts: limits differ per model/project
# and change often. Check aistudio.google.com/rate-limit and raise
# ARROW_DAILY_REQUEST_WARN_GEMINI if your limit is higher.
DEFAULT_REQUEST_WARN = {"gemini": 50, "openrouter": 40, "nvidia": 300, "groq": 500}
DEFAULT_TOKEN_WARN = 1_000_000     # LLM providers; Groq STT counts audio, not tokens
WARN_LEVELS = (80, 100)            # percent of the warn budget
KEEP_DAYS = 14


class UsageLimitReached(RuntimeError):
    """A hard daily limit (ARROW_DAILY_*_LIMIT) refused the call."""

    def __init__(self, provider: str, message: str):
        super().__init__(message)
        self.provider = provider


def _int_env(env, name: str) -> int | None:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return None
    s = str(raw).strip().lower()
    if s in ("0", "off", "none", "no", "false"):
        return 0
    try:
        return max(0, int(float(s)))
    except ValueError:
        return None


def budget(kind: str, provider: str, env=None) -> int:
    """kind: request_warn | token_warn | request_limit | token_limit.
    Per-provider var wins over the global one; 0 means off."""
    env = os.environ if env is None else env
    base = f"ARROW_DAILY_{kind.upper()}"
    for name in (f"{base}_{provider.upper()}", base):
        v = _int_env(env, name)
        if v is not None:
            return v
    if kind == "request_warn":
        return DEFAULT_REQUEST_WARN.get(provider, 100)
    if kind == "token_warn":
        return 0 if provider == "groq" else DEFAULT_TOKEN_WARN
    return 0  # hard limits are off by default


def _empty_counts() -> dict:
    return {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens": 0, "audio_seconds": 0.0, "errors_429": 0}


def tokens_from_gemini(data: dict | None) -> tuple[int, int, int]:
    """(prompt, completion, total) from a Gemini response's usageMetadata."""
    um = (data or {}).get("usageMetadata") or {}
    p = int(um.get("promptTokenCount") or 0)
    c = int(um.get("candidatesTokenCount") or 0) + int(um.get("thoughtsTokenCount") or 0)
    t = int(um.get("totalTokenCount") or 0) or p + c
    return p, c, t


def tokens_from_openai(data: dict | None) -> tuple[int, int, int]:
    """(prompt, completion, total) from an OpenAI-compatible `usage` object."""
    u = (data or {}).get("usage") or {}
    p = int(u.get("prompt_tokens") or 0)
    c = int(u.get("completion_tokens") or 0)
    t = int(u.get("total_tokens") or 0) or p + c
    return p, c, t


def is_quota_error(status: int, body: str = "") -> bool:
    b = (body or "").upper()
    return status == 429 or "RESOURCE_EXHAUSTED" in b


def _locked(fn):
    @wraps(fn)
    def wrapped(self, *args, **kwargs):
        with self._transaction():
            return fn(self, *args, **kwargs)
    return wrapped


class UsageTracker:
    def __init__(self, path: str | None = None,
                 today: Callable[[], _dt.date] = _dt.date.today,
                 env=None):
        if path is None:
            from .config import user_dir
            path = os.path.join(user_dir(), "usage.json")
        self.path = path
        self._today = today
        self._env = env
        self._lock = threading.RLock()
        self._notifier: Callable[[str, str], None] | None = None
        self._depth = 0
        self._corrupt = False
        self._data = self._load()

    @contextmanager
    def _transaction(self):
        """Reload under an OS file lock, shared by all threads/processes."""
        with self._lock:
            if self._depth:
                yield
                return
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                lock_file = open(self.path + ".lock", "a+b")
            except OSError:
                raise UsageLimitReached("unknown", "Usage counter unavailable; calls stopped.") from None
            with lock_file as lock:
                # Ensure one byte exists for the Windows byte-range lock.
                # In append mode tell() may be zero even on an existing file.
                lock.seek(0, os.SEEK_END)
                if lock.tell() == 0:
                    lock.write(b"0")
                    lock.flush()
                lock.seek(0)
                if os.name == "nt":
                    import msvcrt
                    deadline = time.monotonic() + 10
                    while True:
                        try:
                            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                            break
                        except OSError:
                            if time.monotonic() >= deadline:
                                raise UsageLimitReached("unknown", "Usage counter lock unavailable; calls stopped.")
                            time.sleep(0.05)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    self._data = self._load()
                    self._depth = 1
                    yield
                finally:
                    self._depth = 0
                    lock.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @_locked
    def reserve(self, provider: str, model: str = "", audio_seconds: float = 0.0):
        """Count an attempted call BEFORE network, atomically with limit check.

        Request caps are strict reservations. Token limits are next-call
        thresholds, not dollar or token caps; missing usage blocks the next
        call when a token threshold is enabled.
        """
        if self._corrupt:
            raise UsageLimitReached(provider, "Usage counter is corrupt; calls stopped. Restore or review/reset usage.json before continuing.")
        self.check_allowed(provider)
        day = self._day()
        counts = self._prov(day, provider)
        for target in (counts, counts["models"].setdefault(model, _empty_counts())):
            target["requests"] += 1
            target["audio_seconds"] += audio_seconds
        counts["unknown_usage"] = counts.get("unknown_usage", 0) + (provider != "groq")
        self._save(strict=True)
        alerts = self._budget_alerts(day, provider, counts)
        self._save(strict=True)
        for msg, spoken in alerts:
            self._fire(msg, spoken)

    @_locked
    def finish(self, provider, model, counts=None):
        """None means usage unknown, never silently zero."""
        if counts is not None:
            p = self._prov(self._day(), provider)
            p["unknown_usage"] = max(0, p.get("unknown_usage", 0) - 1)
            self.add_tokens(provider, model, *counts)
            self._save()

    # -- wiring -----------------------------------------------------------
    def set_notifier(self, fn: Callable[[str, str], None] | None) -> None:
        """fn(tray_message, short_spoken_line). Called at most once per alert."""
        self._notifier = fn

    @property
    def env(self):
        return os.environ if self._env is None else self._env

    # -- persistence ------------------------------------------------------
    def _load(self) -> dict:
        self._corrupt = False
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            assert isinstance(data, dict) and isinstance(data.get("days"), dict)
            for day in data["days"].values():
                assert isinstance(day, dict) and isinstance(day.get("providers"), dict)
                assert isinstance(day.get("alerts"), list)
                for p in day["providers"].values():
                    assert isinstance(p, dict) and isinstance(p.get("models", {}), dict)
                    for counts in [p] + list(p.get("models", {}).values()):
                        assert isinstance(counts, dict)
                        for k in _empty_counts():
                            assert isinstance(counts.get(k, 0), (int, float)) and counts.get(k, 0) >= 0
            return data
        except FileNotFoundError:
            return {"version": 1, "days": {}}
        except (OSError, ValueError, TypeError, AssertionError):
            self._corrupt = True
            return {"version": 1, "days": {}}

    def _save(self, strict=False) -> None:
        if self._corrupt:
            if strict:
                raise UsageLimitReached("unknown", "Corrupt usage counter; calls stopped.")
            return
        try:
            days = self._data["days"]
            for old in sorted(days)[:-KEEP_DAYS]:
                days.pop(old, None)
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = f"{self.path}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=1)
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001
            if strict:
                raise UsageLimitReached("unknown", "Usage counter could not be saved; calls stopped.") from None

    def _day(self) -> dict:
        key = self._today().isoformat()
        day = self._data["days"].get(key)
        if not isinstance(day, dict):
            day = {"providers": {}, "alerts": []}
            self._data["days"][key] = day
        day.setdefault("providers", {})
        day.setdefault("alerts", [])
        return day

    def _prov(self, day: dict, provider: str) -> dict:
        p = day["providers"].get(provider)
        if not isinstance(p, dict):
            p = {**_empty_counts(), "models": {}}
            day["providers"][provider] = p
        for k, v in _empty_counts().items():
            p.setdefault(k, v)
        p.setdefault("models", {})
        return p

    # -- recording --------------------------------------------------------
    @_locked
    def record(self, provider: str, model: str = "", prompt_tokens: int = 0,
               completion_tokens: int = 0, total_tokens: int = 0,
               audio_seconds: float = 0.0) -> None:
        """Count one request (successful or not: failed requests still count
        against provider quotas) and any tokens/audio it reported."""
        alerts: list[tuple[str, str]] = []
        try:
            with self._lock:
                day = self._day()
                p = self._prov(day, provider)
                total_tokens = total_tokens or (prompt_tokens + completion_tokens)
                targets = [p]
                if model:
                    m = p["models"].setdefault(model, _empty_counts())
                    for k, v in _empty_counts().items():
                        m.setdefault(k, v)
                    targets.append(m)
                for t in targets:
                    t["requests"] += 1
                    t["prompt_tokens"] += int(prompt_tokens or 0)
                    t["completion_tokens"] += int(completion_tokens or 0)
                    t["total_tokens"] += int(total_tokens or 0)
                    t["audio_seconds"] = round(t["audio_seconds"] + float(audio_seconds or 0), 2)
                alerts = self._budget_alerts(day, provider, p)
                self._save()
        except Exception:  # noqa: BLE001
            return
        for msg, spoken in alerts:
            self._fire(msg, spoken)

    @_locked
    def add_tokens(self, provider: str, model: str = "", prompt_tokens: int = 0,
                   completion_tokens: int = 0, total_tokens: int = 0) -> None:
        """Add tokens to a request already counted (streamed answers report
        usage only at the end)."""
        alerts: list[tuple[str, str]] = []
        try:
            with self._lock:
                day = self._day()
                p = self._prov(day, provider)
                total_tokens = total_tokens or (prompt_tokens + completion_tokens)
                targets = [p] + ([p["models"].setdefault(model, _empty_counts())] if model else [])
                for t in targets:
                    for k, v in (("prompt_tokens", prompt_tokens),
                                 ("completion_tokens", completion_tokens),
                                 ("total_tokens", total_tokens)):
                        t[k] = int(t.get(k, 0)) + int(v or 0)
                alerts = self._budget_alerts(day, provider, p)
                self._save()
        except Exception:  # noqa: BLE001
            return
        for msg, spoken in alerts:
            self._fire(msg, spoken)

    def _budget_alerts(self, day: dict, provider: str, p: dict) -> list[tuple[str, str]]:
        name = DISPLAY.get(provider, provider)
        out = []
        pct = 0.0
        parts = []
        rw = budget("request_warn", provider, self.env)
        tw = budget("token_warn", provider, self.env)
        if rw:
            pct = max(pct, 100.0 * p["requests"] / rw)
            parts.append(f"{p['requests']}/{rw} requests")
        if tw:
            pct = max(pct, 100.0 * p["total_tokens"] / tw)
            parts.append(f"{p['total_tokens']:,}/{tw:,} tokens")
        for level in WARN_LEVELS:
            key = f"{provider}:warn{level}"
            if pct >= level and key not in day["alerts"]:
                day["alerts"].append(key)
                if level < 100:
                    out.append((f"{name}: {level}% of today's usage budget "
                                f"({', '.join(parts)}).",
                                f"Heads up: malapit na sa Arrow warning budget ang {name}."))
                else:
                    out.append((f"{name}: today's usage budget reached "
                                f"({', '.join(parts)}). This is an Arrow warning budget, not a billing cap; "
                                f"see 'Usage today...' in the tray.",
                                f"Heads up: naabot na ang Arrow warning budget ng {name}."))
        # only the highest new level matters when one call jumps past both
        return out[-1:]

    def _fire(self, msg: str, spoken: str) -> None:
        if str(self.env.get("ARROW_USAGE_ALERTS", "1")).strip().lower() in ("0", "off", "false", "no"):
            return
        fn = self._notifier
        if fn is None:
            return
        try:
            fn(msg, spoken)
        except Exception:  # noqa: BLE001
            pass

    @_locked
    def alert_once(self, key: str, msg: str, spoken: str = "") -> bool:
        """Fire a one-off alert at most once per local day (per key)."""
        try:
            with self._lock:
                day = self._day()
                if key in day["alerts"]:
                    return False
                day["alerts"].append(key)
                self._save()
        except Exception:  # noqa: BLE001
            return False
        self._fire(msg, spoken)
        return True

    @_locked
    def quota_hit(self, provider: str, body: str = "", status: int = 429) -> None:
        """A provider said 429 / RESOURCE_EXHAUSTED / 402. Notify once per day."""
        name = DISPLAY.get(provider, provider)
        try:
            with self._lock:
                p = self._prov(self._day(), provider)
                if status == 429:
                    p["errors_429"] += 1
                self._save()
        except Exception:  # noqa: BLE001
            pass
        if status == 402:
            self.alert_once(f"{provider}:payment",
                            f"{name} says payment required (HTTP 402): check account balance, key limits and billing status. "
                            "This response alone does not establish the account plan.",
                            f"Heads up: humihingi ng bayad ang {name}.")
            return
        flat = (body or "").lower().replace(" ", "").replace("_", "")
        when = ("resets at midnight Pacific time" if provider == "gemini" and "perday" in flat
                else "resets later")
        self.alert_once(f"{provider}:quota",
                        f"Rate limit reached for {name} (HTTP 429), {when}. "
                        "This is a rate limit, not a charge.",
                        f"Heads up: na-rate limit ng {name} for now.")

    # -- hard limits ------------------------------------------------------
    @_locked
    def blocked_reason(self, provider: str) -> str | None:
        try:
            with self._lock:
                p = self._prov(self._day(), provider)
                rl = budget("request_limit", provider, self.env)
                tl = budget("token_limit", provider, self.env)
                name = DISPLAY.get(provider, provider)
                if self._corrupt:
                    return "Usage counter is corrupt; configured limits cannot be verified."
                if tl and p.get("unknown_usage", 0):
                    return f"{name}: token usage is unknown; token threshold cannot be verified."
                if rl and p["requests"] >= rl:
                    return (f"{name}: daily request limit reached ({p['requests']}/{rl}, "
                            f"ARROW_DAILY_REQUEST_LIMIT). Resets tomorrow.")
                if tl and p["total_tokens"] >= tl:
                    return (f"{name}: daily token threshold reached (may overshoot per request) ({p['total_tokens']:,}/{tl:,}, "
                            f"ARROW_DAILY_TOKEN_LIMIT). Resets tomorrow.")
        except Exception:  # noqa: BLE001
            return None
        return None

    def check_allowed(self, provider: str) -> None:
        """Raise UsageLimitReached when a hard daily limit is set and reached."""
        reason = self.blocked_reason(provider)
        if reason:
            self.alert_once(f"{provider}:hardlimit", reason,
                            f"Naabot na ang daily limit ng {DISPLAY.get(provider, provider)}; "
                            "hindi ko na siya gagamitin ngayong araw.")
            raise UsageLimitReached(provider, reason)

    # -- reporting --------------------------------------------------------
    @_locked
    def snapshot(self) -> dict:
        if self._corrupt:
            raise UsageLimitReached("unknown", "Usage counter is corrupt; counts unavailable.")
        with self._lock:
            day = self._day()
            return json.loads(json.dumps(day["providers"]))

    def summary_text(self, configured: list[str] | None = None) -> str:
        snap = self.snapshot()
        names = list(dict.fromkeys((configured or []) + list(snap)))
        lines = [f"Usage today ({self._today().isoformat()}, local time)", ""]
        if not names:
            lines.append("No API calls yet today.")
        for prov in names:
            p = snap.get(prov) or _empty_counts()
            if p.get("unknown_usage"):
                lines.append(f"{DISPLAY.get(prov, prov)}: {p['unknown_usage']} call(s) with unknown token usage.")
            rw, tw = budget("request_warn", prov, self.env), budget("token_warn", prov, self.env)
            rl, tl = budget("request_limit", prov, self.env), budget("token_limit", prov, self.env)
            req = f"{p.get('requests', 0)} requests (warn {rw or 'off'}" + (
                f", limit {rl})" if rl else ")")
            line = f"{DISPLAY.get(prov, prov)}: {req}"
            if prov == "groq":
                line += f", {p.get('audio_seconds', 0):.0f} s audio"
            else:
                line += (f", {p.get('total_tokens', 0):,} tokens (warn "
                         f"{f'{tw:,}' if tw else 'off'}" + (f", limit {tl:,})" if tl else ")"))
            if p.get("errors_429"):
                line += f", {p['errors_429']}x HTTP 429"
            lines.append(line)
            for model, m in sorted((p.get("models") or {}).items()):
                lines.append(f"    {model}: {m.get('requests', 0)} req, "
                             f"{m.get('total_tokens', 0):,} tok")
        lines += ["", "Budgets are Arrow's own conservative warnings, not the providers' "
                  "limits. Free tiers rate-limit (HTTP 429) instead of charging. "
                  "Change them with ARROW_DAILY_REQUEST_WARN / ARROW_DAILY_TOKEN_WARN in .env."]
        return "\n".join(lines)


# -- OpenRouter key check -----------------------------------------------------

def openrouter_key_warning(api_key: str, model: str, get=None, timeout: int = 10) -> str | None:
    """GET https://openrouter.ai/api/v1/key and return a warning when this
    key could be spending money, else None. Failures return an explicit unknown-status warning."""
    if not api_key:
        return None
    try:
        if get is None:
            import requests
            get = requests.get
        resp = get("https://openrouter.ai/api/v1/key",
                   headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
        if getattr(resp, "status_code", 0) != 200:
            return "OpenRouter account status could not be checked; billing status is unknown."
        d = (resp.json() or {}).get("data") or {}
    except Exception:  # noqa: BLE001
        return "OpenRouter account status could not be checked; billing status is unknown."
    free_model = (model or "").endswith(":free")
    spent_today = float(d.get("usage_daily") or 0)
    paid_before = d.get("is_free_tier") is False
    if spent_today > 0:
        return (f"OpenRouter: this key spent ${spent_today:.4f} in credits today. "
                "Use a ':free' model to stay free.")
    if not free_model:
        return (f"OpenRouter: model '{model}' is not a ':free' model, so it spends "
                "credits" + (" from your balance." if paid_before else
                             " (requests fail without credits)."))
    if paid_before:
        return ("OpenRouter: this account has bought credits. ':free' models still "
                "don't spend them, but paid models would.")
    return None


def require_free_model(provider):
    if provider.name == "openrouter" and not provider.model.endswith(":free"):
        raise UsageLimitReached("openrouter", "OpenRouter model is not :free; request stopped by the free-only policy.")


# -- process-wide tracker -------------------------------------------------------
_tracker: UsageTracker | None = None
_tracker_lock = threading.Lock()


def tracker() -> UsageTracker:
    global _tracker
    with _tracker_lock:
        if _tracker is None:
            _tracker = UsageTracker()
        return _tracker


def set_tracker(t: UsageTracker | None) -> None:
    global _tracker
    with _tracker_lock:
        _tracker = t
