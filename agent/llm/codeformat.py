"""Helpers for pulling structured content (code or JSON) out of raw LLM text
output — models routinely wrap answers in markdown fences despite being told
not to, so every node that parses an LLM response goes through here.
"""

import json
from typing import Any, Optional


def extract_code(text: str) -> str:
    text = text.strip()
    if "```python" in text:
        text = text.split("```python", 1)[1]
        text = text.split("```", 1)[0]
    elif text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
    return text.strip() + "\n"


def extract_json(text: str) -> Optional[dict[str, Any]]:
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None
