import json
from typing import Generator, Optional

import httpx

from config.settings import settings

CLOUD_MODELS: dict[str, list[str]] = {
    "openai": [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "gpt-3.5-turbo",
    ],
    "anthropic": [
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ],
    "google": [
        "gemini-2.0-flash",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ],
}

POPULAR_OLLAMA_MODELS = [
    "qwen3:8b",
    "qwen3:14b",
    "llama3.2:3b",
    "llama3.1:8b",
    "gemma3:12b",
    "gemma3:4b",
    "mistral:7b",
    "deepseek-r1:8b",
    "phi4:14b",
    "nomic-embed-text",
]


class ModelManager:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or settings.OLLAMA_BASE_URL

    def is_available(self) -> bool:
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=3.0)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[dict]:
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=5.0)
            r.raise_for_status()
            return r.json().get("models", [])
        except Exception:
            return []

    def list_running(self) -> list[dict]:
        try:
            r = httpx.get(f"{self.base_url}/api/ps", timeout=5.0)
            r.raise_for_status()
            return r.json().get("models", [])
        except Exception:
            return []

    def pull(self, model_name: str) -> Generator[dict, None, None]:
        with httpx.stream(
            "POST",
            f"{self.base_url}/api/pull",
            json={"name": model_name},
            timeout=None,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line:
                    yield json.loads(line)

    def delete(self, model_name: str) -> bool:
        try:
            r = httpx.request(
                "DELETE",
                f"{self.base_url}/api/delete",
                json={"name": model_name},
                timeout=10.0,
            )
            return r.status_code in {200, 204}
        except Exception:
            return False

    def get_info(self, model_name: str) -> Optional[dict]:
        try:
            r = httpx.post(
                f"{self.base_url}/api/show",
                json={"name": model_name},
                timeout=5.0,
            )
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    @staticmethod
    def cloud_models(provider: str) -> list[str]:
        return CLOUD_MODELS.get(provider, [])

    @staticmethod
    def all_providers() -> list[str]:
        return ["ollama"] + list(CLOUD_MODELS.keys())


model_manager = ModelManager()
