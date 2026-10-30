"""Execução de uma pipeline de consulta.

1. Recuperadores (vetorial e/ou textual) devolvem candidatos elegíveis: vigentes na data de
   referência e visíveis ao perfil. O filtro de acesso acontece aqui, antes de tudo.
2. Na busca híbrida, os rankings são fundidos por Reciprocal Rank Fusion.
3. Opcionalmente, um cross-encoder reordena os melhores candidatos.
"""

from dataclasses import dataclass, replace
from datetime import date

import psycopg

from kb.access import AccessPolicy, AccessProfile
from kb.embedding import Embedder
from kb.errors import KbError
from kb.retrieval.fusion import reciprocal_rank_fusion
from kb.retrieval.pipeline import PipelineConfig
from kb.retrieval.rerank import Reranker
from kb.store import fulltext, vectors
from kb.store.candidates import Candidate
from kb.store.catalog import IndexConfig


@dataclass(frozen=True)
class SearchHit:
    candidate: Candidate
    score: float  # score final: rerank, RRF ou o do recuperador, conforme a pipeline
    rank_vetorial: int | None = None
    rank_textual: int | None = None
    score_rerank: float | None = None

    @property
    def chunk_id(self) -> str:
        return self.candidate.chunk_id

    @property
    def doc_key(self) -> str:
        return self.candidate.doc_key

    @property
    def versao(self) -> int:
        return self.candidate.versao

    @property
    def caminho_secao(self) -> str:
        return self.candidate.caminho_secao

    @property
    def conteudo(self) -> str:
        return self.candidate.conteudo


@dataclass
class Retriever:
    conn: psycopg.Connection
    index: IndexConfig
    embedder: Embedder
    reranker: Reranker | None = None
    policy: AccessPolicy | None = None

    def search(
        self,
        question: str,
        *,
        pipeline: PipelineConfig,
        as_of: date,
        k: int = 5,
        profile: AccessProfile | None = None,
    ) -> list[SearchHit]:
        if self.embedder.model != self.index.embedding_model:
            raise KbError(
                f"A configuração ativa usa o modelo '{self.index.embedding_model}', "
                f"mas o embedder configurado é '{self.embedder.model}'."
            )
        if profile is not None and self.policy is None:
            raise KbError("Um perfil de acesso foi informado, mas não há política de acesso carregada.")
        access_filter = self.policy.sql_filter(profile) if self.policy else AccessPolicy.no_filter()

        rec = pipeline.recuperacao
        rankings: dict[str, list[Candidate]] = {}
        if rec.modo in ("vetorial", "hibrida"):
            vector = self.embedder.embed_query(question)
            rankings["vetorial"] = vectors.dense_search(
                self.conn, self.index, vector, limit=rec.candidatos, as_of=as_of,
                access_filter=access_filter,
            )
        if rec.modo in ("textual", "hibrida"):
            rankings["textual"] = fulltext.sparse_search(
                self.conn, self.index, question, limit=rec.candidatos, as_of=as_of,
                access_filter=access_filter,
            )

        hits = _fuse(rankings, rec.rrf_k)
        if pipeline.rerank.ativo:
            if self.reranker is None:
                raise KbError(f"A pipeline '{pipeline.id}' usa rerank, mas não há reranker configurado.")
            expected = pipeline.rerank.modelo
            if expected and self.reranker.model != expected:
                raise KbError(
                    f"A pipeline '{pipeline.id}' espera o reranker '{expected}', mas o serviço está "
                    f"com '{self.reranker.model}'. Ajuste KB_RERANKER_MODEL ou crie uma nova "
                    "versão da pipeline."
                )
            hits = _rerank(self.reranker, question, hits[: pipeline.rerank.candidatos])
        return hits[:k]


def _fuse(rankings: dict[str, list[Candidate]], rrf_k: int) -> list[SearchHit]:
    by_id = {c.chunk_id: c for ranking in rankings.values() for c in ranking}
    positions = {
        name: {c.chunk_id: position for position, c in enumerate(ranking, start=1)}
        for name, ranking in rankings.items()
    }
    if len(rankings) == 1:
        [ranking] = rankings.values()
        ordered = [(c.chunk_id, c.score) for c in ranking]
    else:
        ordered = reciprocal_rank_fusion(
            [[c.chunk_id for c in ranking] for ranking in rankings.values()], k=rrf_k
        )
    return [
        SearchHit(
            candidate=by_id[chunk_id],
            score=score,
            rank_vetorial=positions.get("vetorial", {}).get(chunk_id),
            rank_textual=positions.get("textual", {}).get(chunk_id),
        )
        for chunk_id, score in ordered
    ]


def _rerank(reranker: Reranker, question: str, hits: list[SearchHit]) -> list[SearchHit]:
    scores = reranker.rerank(question, [h.candidate.texto_embedding for h in hits])
    rescored = [replace(h, score=s, score_rerank=s) for h, s in zip(hits, scores)]
    return sorted(rescored, key=lambda h: (-h.score, h.candidate.chunk_id))
