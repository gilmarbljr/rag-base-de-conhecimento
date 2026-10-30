import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from kb.embedding import Embedder


@dataclass(frozen=True)
class Chunk:
    id: str
    section_path: tuple[str, ...]
    ordinal: int
    kind: str  # texto | tabela | misto
    content: str
    # Texto que vai para o embedding: o conteúdo prefixado com título e caminho da seção.
    embed_text: str
    content_hash: str

    @property
    def path_label(self) -> str:
        return " > ".join(self.section_path)


class Chunker(Protocol):
    name: str

    @property
    def params(self) -> dict[str, Any]: ...

    def chunk(self, doc_key: str, title: str, text: str) -> list[Chunk]: ...


def chunk_id(doc_key: str, section_path: tuple[str, ...], content_hash: str) -> str:
    """ID determinístico: o mesmo trecho, na mesma seção do mesmo documento, tem sempre o mesmo ID.

    Por isso reprocessar um arquivo não duplica chunks, e uma seção que não mudou
    mantém o ID (e o embedding) de uma versão para a outra.
    """
    raw = "\x1f".join([doc_key, " > ".join(section_path), content_hash])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def build_chunker(name: str, params: dict[str, Any], embedder: "Embedder | None" = None) -> Chunker:
    """Recria um chunker a partir do nome e dos parâmetros guardados na configuração do índice.

    O chunker semântico precisa de um embedder para medir a mudança de assunto entre frases.
    """
    from kb.chunking.flat import FixedSizeChunker, RecursiveChunker, SemanticChunker
    from kb.chunking.hierarchical import HierarchicalChunker

    if name == SemanticChunker.name:
        if embedder is None:
            raise ValueError("O chunker semântico precisa de um embedder.")
        return SemanticChunker(embedder, **params)
    chunkers = {c.name: c for c in (HierarchicalChunker, FixedSizeChunker, RecursiveChunker)}
    if name not in chunkers:
        raise ValueError(f"Chunker desconhecido: {name}")
    return chunkers[name](**params)
