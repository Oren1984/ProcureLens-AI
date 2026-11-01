"""Optional local text generation via Ollama. The model only drafts text; it has no tools and
cannot change data."""

from __future__ import annotations

import json
import logging

import httpx

log = logging.getLogger(__name__)

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "next_steps": {"type": "array", "items": {"type": "string"}},
        "clarification_draft": {"type": "string"},
        "cited_refs": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "next_steps", "clarification_draft", "cited_refs"],
}


class GenerationError(RuntimeError):
    pass


class OllamaGenerator:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self._client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            transport=transport,
        )

    def status(self) -> tuple[bool, str]:
        try:
            resp = self._client.get("/api/tags", timeout=5.0)
            resp.raise_for_status()
            names = {m.get("name") for m in resp.json().get("models", [])}
        except (httpx.HTTPError, ValueError) as exc:
            return False, f"Ollama unreachable ({type(exc).__name__})."
        if self.model not in names and f"{self.model}:latest" not in names:
            return False, f"Ollama is running but model '{self.model}' is not pulled."
        return True, f"Ollama ready with model {self.model}."

    def generate_json(self, system: str, user: str) -> dict:
        try:
            resp = self._client.post(
                "/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "format": OUTPUT_SCHEMA,
                    "options": {"temperature": 0.1, "num_predict": 700},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
        except httpx.TimeoutException as exc:
            raise GenerationError("Generation timed out.") from exc
        except httpx.HTTPError as exc:
            raise GenerationError(f"Ollama unavailable ({type(exc).__name__}).") from exc
        if resp.status_code != 200:
            raise GenerationError(f"Ollama returned HTTP {resp.status_code}.")
        try:
            content = resp.json()["message"]["content"]
            data = json.loads(content)
        except (KeyError, TypeError, ValueError) as exc:
            raise GenerationError("Model output was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise GenerationError("Model output was not a JSON object.")
        return data
