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
from typing import Iterator

import requests

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


def build_user_content(question: str, app: str, memory: str,
                       kb_text: str | None, include_image: bool,
                       image_b64: str | None,
                       image_size: tuple[int, int] | None = None) -> list[dict]:
    """Assemble the multimodal user message (OpenAI content-part shape)."""
    ctx = [f"The focused app is: {app}", f"The user asks: {question}"]
    if image_size and include_image:
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


def _check(resp, name: str) -> None:
    """Raise on HTTP errors WITHOUT echoing the request URL or headers
    (requests' own message contains the full URL), so a 429/500 can
    never put an API key into the logs."""
    if resp.status_code >= 400:
        raise RuntimeError(f"{name} request failed: HTTP {resp.status_code}")


def stream_answer(provider: LLMProvider, question: str, app: str,
                  memory: str, kb_text: str | None,
                  image_b64: str | None,
                  timeout: int = 60,
                  image_size: tuple[int, int] | None = None) -> Iterator[str]:
    """Yield streamed text chunks of the model's answer."""
    parts = build_user_content(question, app, memory, kb_text,
                               bool(image_b64), image_b64, image_size)
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
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": _to_gemini_contents(parts),
        "generationConfig": {"maxOutputTokens": 400, "temperature": 0.4},
    }
    with requests.post(url, json=body, headers=headers, stream=True,
                       timeout=timeout) as resp:
        _check(resp, provider.name)
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                data = json.loads(payload)
                for cand in data.get("candidates", []):
                    for part in cand.get("content", {}).get("parts", []):
                        if "text" in part:
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
    with requests.post(url, json=body, headers=headers,
                       stream=True, timeout=timeout) as resp:
        _check(resp, provider.name)
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                data = json.loads(payload)
                for choice in data.get("choices", []):
                    delta = choice.get("delta", {})
                    if delta.get("content"):
                        yield delta["content"]
            except json.JSONDecodeError:
                continue
