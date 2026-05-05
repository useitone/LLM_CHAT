from __future__ import annotations

import json
import urllib.request
from abc import ABC, abstractmethod
from typing import Any


class ModelProvider(ABC):
    @abstractmethod
    def ask(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError


def _post_json(url: str, obj: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read()
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("Response is not JSON object")
    return data


class CloudProvider(ModelProvider):
    """OpenAI-compatible /chat/completions provider."""

    def __init__(self, *, base_url: str, model: str, api_key: str, timeout_s: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s

    def ask(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            raw = resp.read().decode("utf-8")
        obj = json.loads(raw)
        choices = obj.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("No choices in cloud response")
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, str):
            raise RuntimeError("No message.content in cloud response")
        return content


class LocalProvider(ModelProvider):
    """Ollama /api/chat provider."""

    def __init__(self, *, base_url: str, model: str, timeout_s: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s

    def ask(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {"temperature": 0.2},
        }
        obj = _post_json(f"{self.base_url}/api/chat", payload, timeout_s=self.timeout_s)
        msg = obj.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, str):
            raise RuntimeError("No message.content in local response")
        return content
