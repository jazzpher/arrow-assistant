# Arrow agent mode

Teach mode (`Ctrl+Alt+Space`) only points. **Agent mode** can click and type for you.
It is separate, opt-in, and asks before it acts.

## Use it

- Hold `Ctrl+Alt+A`, say the task, release. Or say "Arrow agent, <task>" in teach mode.
- A red HUD (top right) shows each step. `Ctrl+Alt+Y` approves, `Ctrl+Alt+N` skips.
- **Panic: `Ctrl+Alt+Esc`** stops everything at once (also the HUD STOP button, or slam the mouse into a screen corner).
- Moving the mouse yourself pauses the agent; it asks before resuming.
- Console: `python -m arrow_assistant.agent "open notepad and type hello" --dry-run`

## Modes (`ARROW_AGENT_MODE`)

| mode | behavior |
| --- | --- |
| `step` (default) | every click/type/key waits for your approval |
| `task` | you approve the plan once; only risky actions interrupt |
| `auto` | no routine prompts; risky actions still ask |

Limits: `ARROW_AGENT_MAX_STEPS` (default 25; say "Arrow agent ituloy" to continue).
`ARROW_AGENT_SCOPE=excel.exe,chrome.exe` makes any action outside those apps ask first.

## Privacy (read before the first run)

Every step sends a **screenshot of your screen to a cloud AI provider** (Gemini, NVIDIA, OpenRouter).
Free tiers may use what you send to improve their products (Gemini free tier: "Used to improve our
products: Yes"; NVIDIA free trial logs inputs). So screenshots are **not private**.
Blocking password/OTP/card fields and sensitive windows is **not** full screenshot privacy: chats,
emails, documents and notifications that are visible still get uploaded.

- First run shows this notice and needs a YES (`python -m arrow_assistant.agent.privacy`); the app refuses tasks until accepted.
- Close private windows/tabs before starting. Prefer `ARROW_AGENT_SCOPE=notepad.exe,excel.exe` (app allowlist) or `--scope` on the console.
- The first `--live` selftest runs on Notepad only (scope-limited) and asks you to close everything else. Do your first tests on synthetic, non-sensitive content.
- `--dry-run` still sends screenshots to the model; it only skips clicking.

## Safety (enforced in code, not just in the prompt)

- **Always asks** (any mode): send/post/reply, pay/buy/checkout, delete/remove/format, install/uninstall,
  allow/authorize, sign out, terminals, Run dialog, Alt+F4, Enter/Ctrl+Enter in chat/mail/banking windows.
  EN + Tagalog keywords. The label comes from the real UI element, not from what the model claims.
- **Never does**: type into password/PIN/OTP/card fields, type in banking windows, Ctrl+Alt+Del, lock the PC,
  touch password managers, Windows Security, UAC, registry editor, or click its own HUD.
- **Sensitive windows are never screenshotted** to a model; the task stops until you close them.
- On-screen text is treated as data, not instructions (prompt-injection defense), and a send/delete
  button still needs your approval even if a web page tells the agent to press it.
- If the screen changes while you are deciding, the agent re-plans instead of clicking a stale spot.
- Every step is saved in `Documents/Arrow Logs/`.

## Free-tier reality

One vision call per step. Providers are tried in order (Gemini, NVIDIA, OpenRouter) and a provider that
hits a quota is rested while the next one takes over. If all are exhausted the task is saved and can be
resumed. Expect short tasks (5-20 steps) with approval, not long unattended runs.
The agent grounds clicks on real UI Automation elements (numbered boxes) and falls back to pixel coordinates.

## Check your PC (send the output back)

```
python -m arrow_assistant.agent.selftest            # environment, screenshot, UIA, provider ping
python -m arrow_assistant.agent.selftest --bench    # also scores your free models on 10 synthetic screens
python -m arrow_assistant.agent.selftest --live     # tiny real Notepad task, you approve each step
```
Reports land in `Documents/Arrow Logs/` (`selftest.md`, `bench.md`).
