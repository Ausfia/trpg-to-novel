"""OpenAI-compatible LLM 客户端封装（薄层，不过度封装）。

适用于所有 OpenAI 兼容接口：DeepSeek、SiliconFlow、OpenRouter、自建中转站等。
模型名称由调用方按各平台规范传入（如 OpenRouter 用 "anthropic/claude-opus-4-7"）。
"""

from __future__ import annotations

import json
import time
from typing import Any

from openai import OpenAI, APIStatusError


def make_client(api_key: str, base_url: str) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=base_url)


def chat(
    client: OpenAI,
    model: str,
    messages: list[dict],
    *,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    response_format: dict | None = None,
    retries: int = 3,
    retry_delay: float = 5.0,
) -> str:
    """发起 Chat Completion，返回内容字符串。失败后重试 retries 次。"""
    kwargs: dict[str, Any] = dict(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if response_format is not None:
        kwargs["response_format"] = response_format

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except APIStatusError as e:
            last_err = e
            if e.status_code in (429, 500, 502, 503, 504):
                time.sleep(retry_delay * (attempt + 1))
            else:
                raise
    raise RuntimeError(f"LLM 调用失败（已重试 {retries} 次）: {last_err}") from last_err


def chat_json(
    client: OpenAI,
    model: str,
    messages: list[dict],
    **kwargs,
) -> dict:
    """JSON 模式调用，返回解析后的 dict。"""
    kwargs.setdefault("response_format", {"type": "json_object"})
    kwargs.setdefault("temperature", 0.3)
    raw = chat(client, model, messages, **kwargs)
    # 部分模型返回 markdown 代码块包裹的 JSON，剥掉包裹
    stripped = raw.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(stripped)
