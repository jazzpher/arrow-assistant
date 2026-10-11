"""The brain: send screenshot + transcript + memory + KB to a vision LLM
and stream the answer back sentence by sentence.

Providers (all free tiers, picked in config.select_llm order):
  - Gemini via AI Studio (generativelanguage.googleapis.com)
  - OpenRouter (OpenAI-compatible, free models)
  - NVIDIA Build (OpenAI-compatible, free)

The system prompt asks the model to end with [POINT:x,y:label] tags in
screenshot pixel coordinates; points.py turns those into overlay arrows.
"""
from __future__ import annotations

import json
import re
from typing import Iterator

import requests

from . import usage
from .config import LLMProvider

SYSTEM_PROMPT = """You are Arrow, a friendly screen-aware assistant on Windows.
The user is looking at an app and asks you a question out loud. You get:
a screenshot, their question, recent memory of this app, and maybe docs.

Rules:
- Answer briefly, out-loud style: 1-4 short sentences, plain words.
- Be concrete: name the exact button, menu, tab, or field to click.
- NEVER claim you clicked anything. You only point; the user clicks.
- Pointing is rendered ONLY from your tags, never from your words. Saying
  "here" or "I am pointing" without a tag draws nothing.
- When asked to point/show/locate a visible word, button, menu, or field
  (including "ituro", "ipoint", "saan"), include a tag for that target.
  Put the tag FIRST, before the short spoken explanation, so it appears early.
- Emit literal tags (max 3) using INTEGER pixel coordinates in the provided
  screenshot, not percentages, normalized 0-1/0-1000 coordinates, or desktop
  coordinates: [POINT:x,y:short label]
  Example for a target at pixel (120,80): [POINT:120,80:File] This is File.
- Locate the actual target in the screenshot; never invent a location.
  If the requested target is not visible or cannot be located, say so and
  ask the user to bring it into view. Emit no tags in that case.
- General questions with no on-screen target need no tags.
"""


# Gemini is trained to locate things on a 0-1000 grid and keeps doing so even
# when told to use pixels: on a real 1024x768 Notepad shot (e2e run
# 38111522159) every point it gave matched the target only when read as
# 0-1000 (y ~24 px too low as pixels; x off by up to 57% on a 1568-px-wide
# image). So for Gemini we ask for that grid on purpose and convert in code.
_PIXEL_RULE = """- Emit literal tags (max 3) using INTEGER pixel coordinates in the provided
  screenshot, not percentages, normalized 0-1/0-1000 coordinates, or desktop
  coordinates: [POINT:x,y:short label]
  Example for a target at pixel (120,80): [POINT:120,80:File] This is File.
"""
_NORM_RULE = """- Emit literal tags (max 3) using INTEGER coordinates normalized to 0-1000
  (x = 0 left edge .. 1000 right edge, y = 0 top .. 1000 bottom of the
  screenshot), x first, then y: [POINT:x,y:short label]
  Example for a target 6% from the left and 10% from the top:
  [POINT:60,100:File] This is File.
"""
GEMINI_SYSTEM_PROMPT = SYSTEM_PROMPT.replace(_PIXEL_RULE, _NORM_RULE)
assert GEMINI_SYSTEM_PROMPT != SYSTEM_PROMPT


def point_space(provider: LLMProvider | None) -> str:
    """'norm1000' when POINT tags from this provider are on a 0-1000 grid,
    else 'pixels' (screenshot pixels)."""
    return "norm1000" if getattr(provider, "name", None) == "gemini" else "pixels"


def system_prompt(provider: LLMProvider | None) -> str:
    return GEMINI_SYSTEM_PROMPT if point_space(provider) == "norm1000" else SYSTEM_PROMPT


def build_user_content(question: str, app: str, memory: str,
                       kb_text: str | None, include_image: bool,
                       image_b64: str | None,
                       image_size: tuple[int, int] | None = None,
                       space: str = "pixels") -> list[dict]:
    """Assemble the multimodal user message (OpenAI content-part shape)."""
    ctx = [f"The focused app is: {app}", f"The user asks: {question}"]
    if include_image and space == "norm1000":
        ctx.append("POINT coordinates are normalized 0-1000 over the screenshot.")
    elif image_size and include_image:
        ctx.append(f"The screenshot is {image_size[0]}x{image_size[1]} px. "
                   f"POINT coordinates must be inside it.")
    if memory:
        ctx.append(f"Recent history with this app:\n{memory}")
    if kb_text:
        ctx.append(f"Documentation for this app:\n{kb_text}")
    parts: list[dict] = [{"type": "text", "text": "\n\n".join(ctx)}]
    if include_image and image_b64:
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
        })
    return parts


# Gemini 3.x thinks by default (3.8 Flash: "medium"), and thinking tokens
# count toward maxOutputTokens. A 400-token cap was being spent on
# reasoning, so answers and [POINT] tags came back cut short or empty.
# Gemini 3 also deprecates temperature/top_p/top_k.
GEMINI3_THINKING_LEVEL = "low"        # 3.8 Flash rejects "minimal"
GEMINI3_THINKING_HEADROOM = 4096      # cap only; you pay for tokens used


def is_gemini3_or_newer(model: str) -> bool:
    """True for gemini-3.x and later; False for gemini-2.x / 1.x."""
    m = re.match(r"(?:models/)?gemini-(\d+)", (model or "").strip().lower())
    return bool(m) and int(m.group(1)) >= 3


def gemini_generation_config(model: str, max_tokens: int, temperature: float,
                             disable_legacy_thinking: bool = False) -> dict:
    """generationConfig for a Gemini call, correct for 2.x and 3.x models.

    Gemini 3+: low thinking level, extra headroom so thinking cannot eat
    the answer, and no sampling params. Gemini 2.x: unchanged behaviour
    (temperature; thinkingBudget=0 on 2.5 when disable_legacy_thinking).
    """
    if is_gemini3_or_newer(model):
        return {"maxOutputTokens": max_tokens + GEMINI3_THINKING_HEADROOM,
                "thinkingConfig": {"thinkingLevel": GEMINI3_THINKING_LEVEL}}
    gen = {"maxOutputTokens": max_tokens, "temperature": temperature}
    if disable_legacy_thinking and "2.5" in (model or ""):
        gen["thinkingConfig"] = {"thinkingBudget": 0}
    return gen


def _check(resp, name: str) -> None:
    """Raise on HTTP errors WITHOUT echoing the request URL or headers
    (requests' own message contains the full URL), so a 429/500 can
    never put an API key into the logs."""
    if resp.status_code >= 400:
        if resp.status_code in (402, 429):
            body = ""
            try:
                body = (resp.text or "")[:300]
            except Exception:  # noqa: BLE001
                pass
            usage.tracker().quota_hit(name, body, resp.status_code)
            if resp.status_code == 429:
                raise RuntimeError(f"{name} free-tier quota reached (HTTP 429); "
                                   "try again later")
        raise RuntimeError(f"{name} request failed: HTTP {resp.status_code}")


class _Tokens:
    """Collects the usage numbers a streamed response reports (usually in
    its last chunk) and records one request when the stream ends."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider
        self.counts = (0, 0, 0)
        self.sent = False   # True once the provider answered (counts toward quota)

    def see_gemini(self, data: dict) -> None:
        if isinstance(data, dict) and data.get("usageMetadata"):
            self.counts = usage.tokens_from_gemini(data)

    def see_openai(self, data: dict) -> None:
        if isinstance(data, dict) and data.get("usage"):
            self.counts = usage.tokens_from_openai(data)

    def record(self) -> None:
        if not self.sent:
            return
        p, c, t = self.counts
        usage.tracker().record(self.provider.name, self.provider.model, p, c, t)


def stream_answer(provider: LLMProvider, question: str, app: str,
                  memory: str, kb_text: str | None,
                  image_b64: str | None,
                  timeout: int = 60,
                  image_size: tuple[int, int] | None = None) -> Iterator[str]:
    """Yield streamed text chunks of the model's answer."""
    parts = build_user_content(question, app, memory, kb_text,
                               bool(image_b64), image_b64, image_size,
                               space=point_space(provider))
    if provider.name == "gemini":
        yield from _stream_gemini(provider, parts, timeout)
    else:
        yield from _stream_openai_compat(provider, parts, timeout)


def _to_gemini_contents(parts: list[dict]) -> list[dict]:
    out = []
    for p in parts:
        if p["type"] == "text":
            out.append({"text": p["text"]})
        else:
            b64 = p["image_url"]["url"].split(",", 1)[1]
            out.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
    return [{"role": "user", "parts": out}]


def _stream_gemini(provider: LLMProvider, parts: list[dict],
                   timeout: int) -> Iterator[str]:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{provider.model}:streamGenerateContent?alt=sse")
    headers = {"x-goog-api-key": provider.api_key}  # never put the key in the URL
    body = {
        "system_instruction": {"parts": [{"text": system_prompt(provider)}]},
        "contents": _to_gemini_contents(parts),
        "generationConfig": gemini_generation_config(provider.model, 400, 0.4),
    }
    usage.tracker().check_allowed(provider.name)   # hard daily limit, if set
    tok = _Tokens(provider)
    try:
        with requests.post(url, json=body, headers=headers, stream=True,
                           timeout=timeout) as resp:
            tok.sent = True
            _check(resp, provider.name)
            yield from _gemini_sse(resp, tok)
    finally:
        tok.record()


def _gemini_sse(resp, tok: _Tokens) -> Iterator[str]:
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            data = json.loads(payload)
            tok.see_gemini(data)
            for cand in data.get("candidates", []):
                for part in cand.get("content", {}).get("parts", []):
                    if "text" in part and not part.get("thought"):
                        yield part["text"]
        except json.JSONDecodeError:
            continue


def _stream_openai_compat(provider: LLMProvider, parts: list[dict],
                          timeout: int) -> Iterator[str]:
    url = f"{provider.base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {provider.api_key}"}
    if provider.name == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/jazzpher/arrow-assistant"
    body = {
        "model": provider.model,
        "stream": True,
        "max_tokens": 400,
        "temperature": 0.4,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": parts},
        ],
    }
    usage.tracker().check_allowed(provider.name)   # hard daily limit, if set
    tok = _Tokens(provider)
    try:
        with requests.post(url, json=body, headers=headers,
                           stream=True, timeout=timeout) as resp:
            tok.sent = True
            _check(resp, provider.name)
            yield from _openai_sse(resp, tok)
    finally:
        tok.record()


def _openai_sse(resp, tok: _Tokens) -> Iterator[str]:
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            data = json.loads(payload)
            tok.see_openai(data)
            err = data.get("error") if isinstance(data, dict) else None
            if isinstance(err, dict) and err.get("code") == 429:   # mid-stream (OpenRouter)
                usage.tracker().quota_hit(tok.provider.name, str(err.get("message", "")))
            for choice in data.get("choices", []):
                delta = choice.get("delta", {})
                if delta.get("content"):
                    yield delta["content"]
        except json.JSONDecodeError:
            continue
