"""Wrapper around freellmapi (https://github.com/tashfeenahmed/freellmapi).

Nothing else in the codebase should import an LLM SDK directly — this is the
single choke point, which makes token accounting and provider-swapping
trivial (CLAUDE.md §3).
"""

import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from config import settings

RETRYABLE_STATUS = {429, 502, 503, 504}


@dataclass
class LLMResponse:
    content: str
    tokens_prompt: int
    tokens_completion: int
    raw: dict[str, Any]


class LLMClient:
    def __init__(
        self,
        base_url: str = settings.freellmapi_base_url,
        api_key: str = settings.freellmapi_api_key,
        model: str = settings.freellmapi_model,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=120.0,
        )
        self.total_tokens_prompt = 0
        self.total_tokens_completion = 0

    def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        response_schema: Optional[dict] = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_schema is not None:
            payload["response_format"] = {"type": "json_schema", "json_schema": response_schema}

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                resp = self._client.post("/chat/completions", json=payload)
                if resp.status_code in RETRYABLE_STATUS and attempt < max_attempts:
                    time.sleep(2**attempt)
                    continue
                resp.raise_for_status()
                data = resp.json()
                break
            except httpx.TransportError:
                if attempt == max_attempts:
                    raise
                time.sleep(2**attempt)

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        tokens_prompt = usage.get("prompt_tokens", 0)
        tokens_completion = usage.get("completion_tokens", 0)
        self.total_tokens_prompt += tokens_prompt
        self.total_tokens_completion += tokens_completion

        return LLMResponse(
            content=content,
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
            raw=data,
        )

    def close(self) -> None:
        self._client.close()


llm_client = LLMClient()
