from collections.abc import Sequence

import httpx

from kb.embedding.base import PrefixMixin
from kb.errors import KbError


class OllamaEmbedder(PrefixMixin):
    def __init__(
        self, base_url: str, model: str, batch_size: int = 16, timeout: float = 300.0
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.batch_size = batch_size
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            embeddings.extend(self._embed_batch(batch))
        return embeddings

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        try:
            response = self._client.post("/api/embed", json={"model": self.model, "input": batch})
        except httpx.ConnectError as exc:
            raise KbError(
                f"Ollama indisponível em {self.base_url}. "
                "Suba o ambiente com: docker compose --profile cpu up -d"
            ) from exc
        if response.status_code == 404:
            raise KbError(
                f"Modelo '{self.model}' não encontrado no Ollama. Aguarde o download do serviço "
                f"ollama-init ou rode: docker exec kb-ollama ollama pull {self.model}"
            )
        if response.is_error:
            raise KbError(f"Erro do Ollama ({response.status_code}): {response.text}")
        return response.json()["embeddings"]

    def close(self) -> None:
        self._client.close()
