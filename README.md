<h1 align="center">Arrow Assistant</h1>

<p align="center">
  A voice-driven, screen-aware AI buddy for Windows. Hold a hotkey, ask anything about whatever app you are looking at, and Arrow talks back and points an arrow at the exact button to click. It points, you click.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT" />
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4" alt="Windows 10/11" />
</p>

Built as my own version of [Clicky Windows](https://github.com/AbhishekVulla/clicky-windows) (itself a Windows port of [Farza's Clicky](https://github.com/farzaa/clicky)) - same idea, rebuilt from scratch so the whole stack runs on **free tiers**. No paid APIs required.

## What it does

You are working in some app. You hit a wall. You hold `Ctrl+Alt+Space`, ask a question out loud, release. A couple of seconds later you hear the answer, and an arrow lands on the exact button or menu item you need.

In teach mode (the default), Arrow never touches your mouse or keyboard. It shows you where to go, you do the clicking, and you come out of it actually knowing the app. (The separate, opt-in [agent mode](docs/AGENT.md) can click and type for you, with approvals and a panic key.)

Extras on top of the basic loop:

- **Per-app memory.** Arrow remembers your recent questions in each app (SQLite + a readable markdown tail in `Documents/Arrow Memory`), so follow-ups like "and then?" work.
- **Knowledge folder.** Drop a markdown file with docs into `Documents/Arrow Wiki/<app>.exe.md` and Arrow becomes an expert in obscure or company-internal software the AI has never heard of.

## Why this version exists (free-tier stack)

The original Clicky Windows runs on paid APIs (Anthropic + AssemblyAI + Cartesia, about $0.016 per 30-second interaction). Arrow Assistant swaps each layer for a free alternative:

| Layer | Clicky Windows | Arrow Assistant |
| --- | --- | --- |
| Brain (vision LLM) | Claude Sonnet (paid) | **Gemini free tier** (AI Studio key), or OpenRouter free models, or NVIDIA Build free |
| Speech-to-text | AssemblyAI (paid) | **Groq Whisper free tier**, or local faster-whisper (no key, offline) |
| Text-to-speech | Cartesia / ElevenLabs (paid) | **edge-tts** (free neural voices, no key), or pyttsx3 offline |
| Keys storage | keyring | same (Windows Credential Manager) |

Local speech (local whisper + pyttsx3) needs **zero speech API keys** and is slower. Install the optional speech dependencies below first. The vision/LLM still needs a cloud provider key.

## Agent mode (new)

Arrow can also *do* short tasks for you, with approval and a panic key. See [docs/AGENT.md](docs/AGENT.md).

## Easy install (Windows, no Python needed)

1. Download **`ArrowSetup-<version>.exe`** from the latest [Release](../../releases) (or the
   `ArrowSetup` artifact of the newest *windows-installer* run in the Actions tab).
2. Run it. It installs for your user only, with no admin prompt, into
   `%LOCALAPPDATA%\Programs\Arrow Assistant`. It brings its own **Python 3.12** and every
   package, so you don't install Python or run pip.
3. On first start, a window asks for your free **Gemini** and **Groq** keys and saves them in
   Windows Credential Manager. You can change them later from the tray icon → *API keys...*
4. Hold `Ctrl+Alt+Space`, ask something, release.

Optional: a `%APPDATA%\Arrow\.env` file works for other settings (`ARROW_AGENT_*`, the
`ARROW_DAILY_*` usage budgets, ...). Logs
go to `%APPDATA%\Arrow\arrow.log`. Local speech (faster-whisper) is not in the installer; use the
developer setup below for that. Uninstall from *Settings → Apps*.

Windows SmartScreen may warn the first time because the installer is not code-signed yet: click
*More info → Run anyway*.

## Will this cost me money?

Short answer: **not with the default setup**, as long as you don't turn billing on yourself.
What each provider actually does (checked against their docs on 2026-10-11; providers change
their terms, so re-check the linked pages):

- **Gemini (Google AI Studio key).** A new key lives in a Google Cloud project on the **Free
  tier** until you click *Set up billing* and link a billing account. Google's billing page
  lists the Free tier with "N/A" spend cap and says paid tiers start only when you "link a
  billing account and Prepay"; you can "unlink a project from its billing account to return to
  the free tier". On the free tier, going over the limits gives **HTTP 429
  `RESOURCE_EXHAUSTED`** (requests per day reset at midnight Pacific time), not a bill.
  Charges only happen on a project with billing enabled. Check the *Plan* column on
  [aistudio.google.com/api-keys](https://aistudio.google.com/api-keys): if it says *Paid*, that
  key can cost money. (Trade-off: Google notes free-tier prompts may be used to improve its
  products; paid-tier ones are not.) Sources: [Billing](https://ai.google.dev/gemini-api/docs/billing),
  [Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) (your live per-model limits:
  [aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit)).
- **Groq (speech-to-text).** The Free plan is rate-limited (HTTP 429 when you go over). You are
  only billed after you upgrade to the Developer plan and add a payment method.
  Sources: [Rate limits](https://console.groq.com/docs/rate-limits),
  [Billing FAQs](https://console.groq.com/docs/billing-faqs).
- **OpenRouter.** Models whose id ends in `:free` (the default,
  `google/gemma-4-31b-it:free`) don't spend credits; they have per-day request caps and return
  429 when you hit them. Paid models spend prepaid credits you bought; with no credits they fail
  (HTTP 402) instead of charging a card (unless you turned on auto top-up). Source:
  [Limits](https://openrouter.ai/docs/api-reference/limits).
- **NVIDIA Build.** Hosted endpoints on build.nvidia.com are free for prototyping and
  rate-limited (about 40 requests/minute for most models, per their FAQ), with no per-token
  billing. Source: [build.nvidia.com](https://build.nvidia.com) FAQ.
- **edge-tts** and the local fallbacks need no key at all.

### The usage guard

Arrow counts its own API calls per provider and model, per day (your PC's local date), in
`%APPDATA%\Arrow\usage.json`: requests, prompt/answer tokens (from the provider's usage data),
HTTP 429s, and seconds of audio sent to Groq. Tray icon → **Usage today...** shows the numbers.

- At **80%** and again at **100%** of a provider's daily *warn* budget you get one tray
  notification and one short spoken line ("Heads up: malapit na sa daily limit ang Gemini").
  Each fires once per day.
- When a provider answers **429 / `RESOURCE_EXHAUSTED`** you get one "free-tier quota reached
  for <provider>, resets later" notice per day. A **402** (payment required) gets its own notice.
- At startup, with an OpenRouter key, Arrow asks `GET https://openrouter.ai/api/v1/key` and
  warns if the key spent credits today, if the model isn't a `:free` one, or if the account has
  bought credits. If the check fails, it stays quiet.
- Optional **hard limits** refuse new calls for that provider for the rest of the day. In agent
  mode the planner just moves on to the next provider (gemini → nvidia → openrouter).

The budgets are **Arrow's own conservative defaults, not the providers' limits.** Free-tier
numbers change often, so look up your real limits (links above) and set your own:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ARROW_DAILY_REQUEST_WARN` | gemini 100, openrouter 40, nvidia 300, groq 500 | requests/day before the 80%/100% warnings |
| `ARROW_DAILY_TOKEN_WARN` | 1,000,000 (LLMs; Groq off) | tokens/day before the warnings |
| `ARROW_DAILY_REQUEST_LIMIT` | off | hard stop, requests/day |
| `ARROW_DAILY_TOKEN_LIMIT` | off | hard stop, tokens/day |
| `ARROW_USAGE_ALERTS` | 1 | `0` keeps counting but shows/says nothing |

Add `_GEMINI`, `_OPENROUTER`, `_NVIDIA` or `_GROQ` to any of them for one provider, e.g.
`ARROW_DAILY_REQUEST_WARN_GEMINI=200`. `0`/`off` turns that budget off. Arrow's day starts at
local midnight, while Gemini's daily quota resets at midnight Pacific, so the two counters
won't line up exactly.

## Developer setup

1. Install **64-bit Python 3.12** on Windows (recommended). Do not use 32-bit Python for this setup. Python 3.15 is not supported: required native packages, including optional CTranslate2, do not all publish 3.15 wheels. Python 3.13/3.14 have default-install CI checks, but use 3.12 for this setup.
2. From the extracted project folder, create a clean environment:
   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install --upgrade pip
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and add what you have:
   - `GEMINI_API_KEY` - free key from [Google AI Studio](https://aistudio.google.com/apikey) (recommended), **or** `OPENROUTER_API_KEY`, **or** `NVIDIA_API_KEY`
   - `GROQ_API_KEY` - free key from [Groq](https://console.groq.com/keys) for the default lightweight install; without it you need the optional local speech install below
   - TTS needs no key.
4. `.\.venv\Scripts\python.exe -m arrow_assistant`
5. Hold `Ctrl+Alt+Space`, ask something, release.

Everything runs through your own keys. Nothing routes through a proxy server.

### Optional local speech

The normal install does **not** include faster-whisper/CTranslate2 or pyttsx3.
For local speech, in the same environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-local.txt
```

This option downloads a model on first use and takes extra disk space and RAM.
Use Groq speech on a low-memory PC. If pip reports no matching CTranslate2
build, check your Python version, architecture, and package index rather than
forcing incompatible versions. A missing local speech package gives an
installation hint instead of an unexplained import error.

For an existing failed install, you can use the default cloud speech path by
removing the `faster-whisper` and `pyttsx3` lines from the old requirements file,
then running `python -m pip install --upgrade pip` and
`python -m pip install -r requirements.txt`. This does not fix unsupported
Python/architecture combinations for the remaining native packages.

## How it works

```mermaid
graph TD
    USER[User holds Ctrl+Alt+Space]
    subgraph Local [Local pipeline - 4 things in parallel on release]
        STT[Groq Whisper / local whisper]
        CAP[mss multi-monitor capture]
        MEM[Per-app memory recall]
        KB[Arrow Wiki docs lookup]
    end
    subgraph Cloud [Your chosen free-tier provider]
        LLM[Gemini / OpenRouter / NVIDIA vision]
    end
    subgraph Out [Output]
        TTS[edge-tts sentence prefetch]
        OVL[PyQt6 click-through arrow overlay]
    end
    USER --> STT & CAP & MEM & KB
    STT & CAP & MEM & KB --> LLM
    LLM -->|streaming sentences| TTS
    LLM -->|POINT x y label| OVL
```

On hotkey release, four things kick off in parallel: speech-to-text, screen capture, memory recall, and KB lookup. The vision model receives the screenshot plus transcript plus memory plus docs, and streams the answer. Sentences flush to TTS the moment a `.!?` boundary lands, so you start hearing the answer while the rest is still generating. A `[POINT:x,y:label]` tag in the reply drives the overlay arrow.

Notable details (credit to Clicky Windows for pioneering these):

- **Observe-only hotkey.** `pynput.Listener(suppress=False)` watches keys without eating them, so your typing never breaks.
- **Click-through overlay.** One transparent QWidget per physical monitor (mixed-DPI safe), made click-through with Win32 layered-window flags applied after `show()`.
- **Prefetch double-buffer.** The next sentence's audio is synthesized while the current one is still playing, so gaps stay near zero.

## Pointing troubleshooting

The overlay is shown only when the model locates a visible target and emits
`[POINT:x,y:label]`. It clears after six seconds; voice-only answers may have
no target. Ask for a specific visible menu or word and watch during the reply.
The app captures the monitor containing your mouse pointer.

Teach-mode metadata in `Documents/Arrow Memory/arrow.log` now records
`teach points: parsed=... invalid=... incomplete=...` plus image size,
monitor origin, and scale. It does not record model text, screenshots, or keys.
`parsed=0 invalid=0 incomplete=0` means no usable point tag was found;
nonzero invalid/incomplete counts mean the model's tags were malformed or cut
short. A positive parsed count means points were sent to the overlay, not
proof they were visibly drawn. Check terminal errors and monitor/scaling if
points were parsed but nothing appeared. Prompt changes do not guarantee
that every model will locate every target correctly.

## Development

```bash
pip install -r requirements-dev.txt
pytest -q
```

The test suite covers the sentence streamer, point-tag parsing, per-monitor point routing, memory, KB, provider selection, the hotkey state machine, the STT/TTS pipeline (mocked), and the agent loop with its safety gates (all with fakes: no network, screen, or mouse). CI runs them on every push.

## Privacy

- Screenshots and audio go only to the provider whose key you configured.
- Memory and wiki files stay on your machine (`Documents/Arrow Memory`, `Documents/Arrow Wiki`).
- No analytics, no proxy, no account.

## License

MIT. Inspired by [Clicky Windows](https://github.com/AbhishekVulla/clicky-windows) - implementation here is original.
