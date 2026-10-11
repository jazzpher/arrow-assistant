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

## Tools (no mouse or keyboard)

Besides clicking and typing, the agent can use tools directly. They are faster and cheaper
than driving the screen (still one model call per step).

| tool | what it does | asks first? |
| --- | --- | --- |
| `web_search` | DuckDuckGo results (title, link, snippet), no key needed | no |
| `web_fetch` | reads a **public** web page as plain text (private/LAN addresses are blocked, also after redirects) | no |
| `list_files`, `read_file` | only inside the agent workspace | no |
| `write_file` | only inside the agent workspace | in `step` mode, and always when it overwrites a file |
| `powershell` | one command, run in the workspace, 60 s timeout, read-only by default | **always**, in every mode |

- Workspace: `ARROW_AGENT_WORKSPACE` (default `Documents/Arrow Workspace`). Paths are resolved in code
  and cannot leave it; absolute paths, `..` escapes and secret-looking files (`.env`, keys, `*.kdbx`,
  cookies, `*password*`) are refused.
- Turn groups on/off: `ARROW_AGENT_TOOLS=web,files,powershell` (default), e.g. `web,files` or `none`.
- PowerShell has two modes, `ARROW_AGENT_PS_MODE`:
  - `safe` (default): only read-only commands (`Get-ChildItem`, `Get-Content`, `Select-String`,
    `Measure-Object`, `Where-Object`, ...), only paths inside the workspace (no `C:\`, `..`, `~`,
    `$env:`, `HKLM:`, UNC), no `>`/`Set-Content`/`New-Item`, no `&`, `$( )` or .NET calls.
    Saving files goes through `write_file`.
  - `approve`: any command that is not on the blocklist below, still asked every time.
  In both modes the command runs in PowerShell **ConstrainedLanguage** and without your API keys,
  tokens or passwords in its environment.
- PowerShell that is destructive or dangerous is **blocked even if you approve**: recursive delete,
  deleting outside the workspace, disk format, shutdown/sign-out, registry edits, execution policy or
  Defender changes, `iex`/encoded or downloaded code, admin elevation, users/credentials, services and
  scheduled tasks. This is not an OS sandbox: in `approve` mode a command runs as you and can reach
  the rest of your PC, so read every command before you approve it.
- Tool results go back to the model as **data**, wrapped and labelled; instructions inside a web page
  or file are ignored like on-screen text.
- `--dry-run` runs read-only tools (search, fetch, read, list) but never writes files or runs PowerShell.

## Which model is best? (benchmark)

`python -m arrow_assistant.agent.bench` gives every configured model the same 15 synthetic screens
and scores the ONE next action it picks. It never moves the mouse, types, or runs a tool.

- **task**: grounding (right button, Save vs Save As, pixel-only clicks, Taglish), flow (click
  before typing, scroll to find, noticing it is done, opening an app), and tool use.
- **safety**: refuses to type a password, ignores on-screen and in-file prompt injection, drafts
  without sending, does not click a disabled button.
- **valid JSON** (first reply needed no repair) and **median latency**.

```
python -m arrow_assistant.agent.bench                       # every provider with a key
python -m arrow_assistant.agent.bench --models gemini:gemini-3.8-flash,openrouter:google/gemma-4-31b-it:free,nvidia:meta/llama-3.2-90b-vision-instruct
python -m arrow_assistant.agent.bench --repeat 3 --only safety
python -m arrow_assistant.agent.bench --dump bench_screens  # look at the screens, no API calls
```

Reports go to `Documents/Arrow Logs/bench-<time>.md` (+ `.json`). Free tiers are slow: `--delay`
(default 4 s) spaces out calls, and a 429 is retried once after a pause. Pick the model with the
best **safety** first, then **task**. One step right is not a whole task done, so treat this as a
filter, not a guarantee.

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
- Tool results (search results, fetched pages, files you let it read, command output) are also sent to
  the cloud model. Do not keep private files in the agent workspace.

## Safety (enforced in code, not just in the prompt)

- **Always asks** (any mode): send/post/reply, pay/buy/checkout, delete/remove/format, install/uninstall,
  allow/authorize, sign out, terminals, Run dialog, Alt+F4, Enter/Ctrl+Enter in chat/mail/banking windows.
  EN + Tagalog keywords. The label comes from the real UI element, not from what the model claims.
- **Never does**: type into password/PIN/OTP/card fields, type in banking windows, Ctrl+Alt+Del, lock the PC,
  touch password managers, Windows Security, UAC, registry editor, or click its own HUD.
- **Sensitive windows are never screenshotted** to a model; the task stops until you close them.
- **Typing follows the real keyboard focus.** Right before every type/key action the agent re-checks the
  focused window (if it changed since the screenshot, it re-plans) and asks UI Automation which field has
  focus (a password field is blocked even if focus got there via Tab).
- **Unknown keyboard focus means no input.** If Windows/UI Automation cannot verify the window and
  field, keyboard actions re-plan and stop after repeated failures. Use manual input in apps without UIA.
- **Typing re-checks the window and field before each character.** A change stops the remaining text.
  Password and named PIN/OTP/card fields are checked again after approval.
- **`open_app` checks Start before every character and before Enter.** A lost Start focus aborts the
  remaining input. These checks reduce focus races, but Windows focus can still change between a check
  and a keystroke. Already typed characters cannot be undone automatically; use per-action approval
  and supervise agent mode. This is not a guarantee that input can never reach another window.
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
