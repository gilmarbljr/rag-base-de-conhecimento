import re
from collections.abc import Sequence
from functools import cached_property
from typing import Protocol

import httpx

from kb.errors import KbError


class Reranker(Protocol):
    @property
    def model(self) -> str: ...

    def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        """Um score de relevância por texto, na mesma ordem recebida."""
        ...


class TeiReranker:
    """Cross-encoder servido pelo Text Embeddings Inference (endpoints /info e /rerank)."""

    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        self.base_url = base_url
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    @cached_property
    def model(self) -> str:
        return self._request("GET", "/info").json()["model_id"]

    def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        response = self._request(
            "POST", "/rerank", json={"query": query, "texts": list(texts), "truncate": True}
        )
        scores = [0.0] * len(texts)
        for item in response.json():
            scores[item["index"]] = item["score"]
        return scores

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise KbError(
                f"Reranker indisponível em {self.base_url}. "
                "Suba o ambiente com: docker compose --profile cpu up -d"
            ) from exc
        if response.is_error:
            raise KbError(f"Erro do reranker ({response.status_code}): {response.text}")
        return response


class FakeReranker:
    """Reranker determinístico para testes: proporção de palavras da pergunta presentes no texto."""

    model = "fake-reranker"

    def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        terms = set(re.findall(r"\w+", query.lower()))
        return [
            len(terms & set(re.findall(r"\w+", text.lower()))) / (len(terms) or 1) for text in texts
        ]
