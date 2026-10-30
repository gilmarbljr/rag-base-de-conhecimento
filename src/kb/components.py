"""Monta os componentes a partir da configuração. Usado pela CLI, pelas DAGs e pelo painel."""

from datetime import date
from pathlib import Path

import psycopg

from kb.access import AccessPolicy, load_policy
from kb.chunking import Chunker, build_chunker
from kb.config import Settings
from kb.embedding import OllamaEmbedder
from kb.errors import KbError
from kb.llm import OllamaChat
from kb.retrieval import Retriever, TeiReranker
from kb.sources import Manifest, load_manifest
from kb.store import catalog
from kb.store.catalog import IndexConfig

FONTE_PADRAO = "corpus-demo"


def access_policy(settings: Settings) -> AccessPolicy:
    return load_policy(settings.perfis_path)


def embedder_for(settings: Settings, index: IndexConfig) -> OllamaEmbedder:
    """O modelo de embedding vem do índice (o catálogo manda), não da configuração.

    `KB_EMBEDDING_MODEL` só define o modelo do primeiro índice, no `kb init`. Depois de uma
    reindexação blue/green, o índice ativo pode usar outro modelo.
    """
    return OllamaEmbedder(settings.ollama_url, index.embedding_model)


def chunker_for(index: IndexConfig, embedder: OllamaEmbedder | None = None) -> Chunker:
    return build_chunker(index.chunker, index.chunker_params, embedder=embedder)


def retriever(
    settings: Settings,
    conn: psycopg.Connection,
    *,
    with_reranker: bool = True,
    index: IndexConfig | None = None,
) -> Retriever:
    """Retriever do índice informado (padrão: o índice ativo)."""
    index = index or catalog.active_index_config(conn)
    return Retriever(
        conn=conn,
        index=index,
        embedder=embedder_for(settings, index),
        reranker=TeiReranker(settings.reranker_url) if with_reranker else None,
        policy=access_policy(settings),
    )


def chat_model(settings: Settings) -> OllamaChat:
    return OllamaChat(settings.ollama_url, settings.generation_model)


def current_manifest(conn: psycopg.Connection, fonte: str = FONTE_PADRAO) -> Manifest:
    """Manifest da pasta para a qual a fonte aponta."""
    return load_manifest(Path(catalog.get_source(conn, fonte)["caminho"]))


def current_release(conn: psycopg.Connection, fonte: str = FONTE_PADRAO) -> int:
    return current_manifest(conn, fonte).release


def hoje(settings: Settings, conn: psycopg.Connection, fonte: str = FONTE_PADRAO) -> date:
    """A "data de hoje" das consultas e relatórios, conforme o relógio configurado."""
    release_date = None
    if settings.data_simulada == "release":
        try:
            release_date = current_manifest(conn, fonte).data_referencia
        except KbError:
            release_date = None  # fonte ainda não registrada: usa a data real
    return settings.hoje(release_date)
