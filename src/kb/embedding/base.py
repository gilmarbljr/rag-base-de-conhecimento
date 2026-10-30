import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol

# Alguns modelos foram treinados com prefixos que distinguem pergunta e documento.
# Sem eles, a qualidade da busca cai. (prefixo da consulta, prefixo do documento)
MODEL_PREFIXES: dict[str, tuple[str, str]] = {
    "nomic-embed-text-v2-moe": ("search_query: ", "search_document: "),
    "nomic-embed-text": ("search_query: ", "search_document: "),
}


class Embedder(Protocol):
    model: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embeddings do texto como está, sem prefixos."""
        ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class PrefixMixin:
    """Aplica os prefixos de consulta e documento do modelo, quando existem."""

    model: str

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        _, prefix = MODEL_PREFIXES.get(self.model, ("", ""))
        return self.embed([prefix + text for text in texts])

    def embed_query(self, text: str) -> list[float]:
        prefix, _ = MODEL_PREFIXES.get(self.model, ("", ""))
        return self.embed([prefix + text])[0]


class FakeEmbedder(PrefixMixin):
    """Embedder determinístico para testes: hashing de palavras, sem modelo e sem rede.

    Textos que compartilham palavras ficam próximos, o que basta para testar o pipeline.
    """

    def __init__(self, dimension: int = 256) -> None:
        self.dimension = dimension
        self.model = f"fake-{dimension}"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in re.findall(r"\w+", text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
            vector[int.from_bytes(digest, "little") % self.dimension] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]
