"""OpenAI-compatible LLM 客户端封装（薄层，不过度封装）。

适用于所有 OpenAI 兼容接口：DeepSeek、SiliconFlow、OpenRouter、自建中转站等。
模型名称由调用方按各平台规范传入（如 OpenRouter 用 "anthropic/claude-opus-4-7"）。
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from openai import OpenAI, APIConnectionError, APIStatusError

_THINK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)


def _strip_think(text: str) -> str:
    """Remove `<think>...</think>` blocks leaked by reasoning models (e.g. DeepSeek-R1)."""
    return _THINK_RE.sub("", text).strip()


def make_client(
    api_key: str,
    base_url: str,
) -> OpenAI:
    """构造 OpenAI 兼容客户端。"""
    return OpenAI(api_key=api_key, base_url=base_url)


def chat(
    client: OpenAI,
    model: str,
    messages: list[dict],
    *,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    response_format: dict | None = None,
    extra_body: dict | None = None,
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
    merged_extra_body = _default_extra_body(client, model)
    if extra_body:
        merged_extra_body.update(extra_body)
    if merged_extra_body:
        kwargs["extra_body"] = merged_extra_body

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(**kwargs)
            return _strip_think(resp.choices[0].message.content or "")
        except APIConnectionError as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(retry_delay * (attempt + 1))
                continue
            base_url = str(getattr(client, "base_url", "") or "").rstrip("/")
            raise RuntimeError(
                "LLM 连接失败：无法连接到模型服务。"
                "这通常不是模型生成问题，而是 Base URL 域名无法解析、网络/DNS 不通、代理不可用，"
                "或服务商地址填写错误。\n"
                f"当前 Base URL：{base_url or '未设置'}\n"
                "请到「LLM 配置」页检查对应阶段的 Base URL、API Key、代理/网络后重试。\n"
                f"底层错误：{e}"
            ) from e
        except APIStatusError as e:
            last_err = e
            if e.status_code in (429, 500, 502, 503, 504):
                time.sleep(retry_delay * (attempt + 1))
            else:
                raise
    raise RuntimeError(f"LLM 调用失败（已重试 {retries} 次）: {last_err}") from last_err


def _default_extra_body(client: OpenAI, model: str) -> dict[str, Any]:
    """Provider-specific defaults for OpenAI-compatible APIs."""
    base_url = str(getattr(client, "base_url", "") or "").lower()
    model_l = (model or "").lower()
    if "api.deepseek.com" in base_url and model_l.startswith("deepseek-v4"):
        mode = os.environ.get("LLM_DEEPSEEK_THINKING", "disabled").strip().lower()
        if mode in {"enabled", "disabled"}:
            return {"thinking": {"type": mode}}
    return {}


def chat_json(
    client: OpenAI,
    model: str,
    messages: list[dict],
    **kwargs,
) -> dict:
    """JSON 模式调用，返回解析后的 dict。

    先尝试带 response_format=json_object；若解析失败会把原始响应附在错误里便于排查。
    """
    kwargs.setdefault("response_format", {"type": "json_object"})
    kwargs.setdefault("temperature", 0.3)
    raw = chat(client, model, messages, **kwargs)
    stripped = raw.strip()
    if not stripped:
        raise ValueError(f"模型 {model} 返回了空响应（期望 JSON）")
    # 部分模型返回 markdown 代码块包裹的 JSON，剥掉包裹
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        inner = "\n".join(lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:]).strip()
        if inner:
            stripped = inner
    if not stripped:
        raise ValueError(f"模型 {model} 返回空 JSON 内容，原始响应：{raw!r}")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        first200 = stripped[:200].replace("\n", "\\n")
        raise ValueError(
            f"模型 {model} 返回了非 JSON 内容（{exc}）\n"
            f"响应前 200 字：{first200!r}"
        ) from exc


def chat_vision(
    client: OpenAI,
    model: str,
    image_bytes: bytes,
    prompt: str,
    mime_type: str = "image/jpeg",
    *,
    max_tokens: int = 1200,
) -> str:
    """Vision call: send image (base64) + text prompt, return description string."""
    import base64
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
            ],
        }
    ]
    return chat(client, model, messages, temperature=0.5, max_tokens=max_tokens)
