"""Consulta com registro: toda busca feita por um usuário entra no log de consultas.

O log alimenta o relatório de saúde (lacunas: perguntas sem boa resposta na base).
As buscas da avaliação não passam por aqui, para não misturar perguntas de teste com reais.
"""

import time
from datetime import date

import psycopg

from kb.access import AccessProfile
from kb.retrieval import PipelineConfig, Retriever, SearchHit
from kb.store import query_logs


def consultar(
    conn: psycopg.Connection,
    retriever: Retriever,
    pergunta: str,
    *,
    pipeline: PipelineConfig,
    as_of: date,
    k: int = 5,
    profile: AccessProfile | None = None,
    origem: str,
) -> list[SearchHit]:
    started = time.perf_counter()
    hits = retriever.search(pergunta, pipeline=pipeline, as_of=as_of, k=k, profile=profile)
    latency = (time.perf_counter() - started) * 1000
    query_logs.insert(
        conn,
        origem=origem,
        pergunta=pergunta,
        perfil=profile.nome if profile else None,
        pipeline=pipeline.id,
        index_config_id=retriever.index.id,
        as_of=as_of,
        k=k,
        latencia_ms=round(latency, 1),
        resultados=[
            {
                "chunk_id": h.chunk_id,
                "doc_key": h.doc_key,
                "versao": h.versao,
                "caminho_secao": h.caminho_secao,
                "score": round(h.score, 4),
            }
            for h in hits
        ],
    )
    return hits
