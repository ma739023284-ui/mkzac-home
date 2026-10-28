"""可选的大模型接入层（OpenAI 兼容协议）。未配置 Key 时整体走规则引擎，功能不受影响。"""
from __future__ import annotations

import os

import requests


class LLMClient:
    def __init__(self) -> None:
        self.api_key = os.environ.get("LLM_API_KEY", "").strip()
        self.base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
        self.model = os.environ.get("LLM_MODEL", "deepseek-chat")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def complete(self, system: str, user: str, temperature: float = 0.2, timeout: int = 20) -> str | None:
        if not self.enabled:
            return None
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "temperature": temperature,
                    "max_tokens": 700,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=timeout,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            return (data["choices"][0]["message"]["content"] or "").strip() or None
        except Exception:
            return None
