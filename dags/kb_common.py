"""Peças compartilhadas pelas DAGs: sessão com o banco e componentes do pacote kb.

As DAGs não contêm regra de negócio: tudo o que fazem é chamar funções do pacote `kb`,
as mesmas usadas pela CLI e pelos testes (ver ADR-001).
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import psycopg
from airflow.sdk import Asset

from kb import components as kb_components
from kb.chunking import Chunker
from kb.config import Settings
from kb.embedding import OllamaEmbedder
from kb.store import catalog
from kb.store.catalog import IndexConfig
from kb.store.db import connect

KB_HOME = Path(os.environ.get("KB_HOME", "/opt/kb"))
FONTE = "corpus-demo"
PASTA_INICIAL = "data/corpus/release_1"

# Emitido pela kb_sync ao publicar o índice; dispara as DAGs que dependem dele (kb_eval).
KB_INDICE = Asset(name="kb_indice", uri="kb://indice/ativo")


def kb_path(caminho: str) -> Path:
    """Caminhos de fontes são relativos à raiz do projeto, no host e no container."""
    os.chdir(KB_HOME)
    return Path(caminho)


def parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


@contextmanager
def kb_session() -> Iterator[tuple[Settings, psycopg.Connection, IndexConfig]]:
    settings = Settings()
    with connect(settings.database_url) as conn:
        yield settings, conn, catalog.active_index_config(conn)


def components(settings: Settings, config: IndexConfig) -> tuple[Chunker, OllamaEmbedder]:
    """Chunker e embedder do índice informado (o modelo vem do índice, não do ambiente)."""
    embedder = kb_components.embedder_for(settings, config)
    return kb_components.chunker_for(config, embedder), embedder
