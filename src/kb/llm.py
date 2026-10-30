"""Modelo de geração, usado como juiz (conflitos) e, nas próximas fases, para respostas."""

import json
from typing import Any, Protocol

import httpx

from kb.errors import KbError


class ChatModel(Protocol):
    model: str

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Resposta estruturada, validada pelo JSON Schema informado."""
        ...


class OllamaChat:
    def __init__(self, base_url: str, model: str, timeout: float = 300.0) -> None:
        self.base_url = base_url
        self.model = model
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "format": schema,  # saída estruturada: o Ollama restringe a geração ao schema
            "stream": False,
            "think": False,  # sem raciocínio estendido: a tarefa é curta e a latência importa
            "options": {"temperature": 0},
        }
        try:
            response = self._client.post("/api/chat", json=payload)
        except httpx.ConnectError as exc:
            raise KbError(f"Ollama indisponível em {self.base_url}.") from exc
        if response.status_code == 404:
            raise KbError(
                f"Modelo '{self.model}' não encontrado no Ollama. "
                f"Rode: docker exec kb-ollama ollama pull {self.model}"
            )
        if response.is_error:
            raise KbError(f"Erro do Ollama ({response.status_code}): {response.text}")
        content = response.json()["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise KbError(f"O modelo não devolveu JSON válido: {content[:200]}") from exc


class FakeChat:
    """Modelo falso para testes: devolve respostas prontas, em ordem, e guarda os prompts."""

    model = "fake-chat"

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        return self.responses.pop(0)
