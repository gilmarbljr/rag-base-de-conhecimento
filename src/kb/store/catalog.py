"""Operações sobre o catálogo: fontes, documentos, versões, chunks e configurações de índice."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from kb.chunking import Chunk
from kb.errors import KbError
from kb.sources import SourceDocument
from kb.store import vectors


@dataclass(frozen=True)
class IndexConfig:
    id: str
    chunker: str
    chunker_params: dict[str, Any]
    embedding_model: str
    dimensao: int
    tabela: str
    ativo: bool


_INDEX_CONFIG_SELECT = """
    SELECT ic.id, ic.chunker, ic.chunker_params, em.nome AS embedding_model, em.dimensao,
           ic.tabela_embeddings AS tabela, ic.ativo
    FROM index_configs ic
    JOIN embedding_models em ON em.id = ic.embedding_model_id
"""


# --- Fontes -----------------------------------------------------------------------------


def ensure_source(conn: psycopg.Connection, nome: str, caminho: str) -> int:
    row = conn.execute(
        "INSERT INTO sources (nome, caminho) VALUES (%s, %s)"
        " ON CONFLICT (nome) DO UPDATE SET caminho = EXCLUDED.caminho RETURNING id",
        (nome, caminho),
    ).fetchone()
    return row["id"]


def get_source(conn: psycopg.Connection, nome: str) -> dict:
    row = conn.execute("SELECT * FROM sources WHERE nome = %s", (nome,)).fetchone()
    if row is None:
        raise KbError(f"Fonte '{nome}' não registrada. Rode: kb ingest <pasta> --fonte {nome}")
    return row


def active_documents_of_source(conn: psycopg.Connection, source_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT doc_key FROM documents WHERE source_id = %s AND status = 'active' ORDER BY doc_key",
        (source_id,),
    ).fetchall()
    return [row["doc_key"] for row in rows]


# --- Documentos e versões ---------------------------------------------------------------


def get_document(conn: psycopg.Connection, doc_key: str) -> dict | None:
    return conn.execute("SELECT * FROM documents WHERE doc_key = %s", (doc_key,)).fetchone()


def latest_version(conn: psycopg.Connection, document_id: int) -> dict | None:
    return conn.execute(
        "SELECT * FROM document_versions WHERE document_id = %s ORDER BY versao DESC LIMIT 1",
        (document_id,),
    ).fetchone()


def document_fields(doc: SourceDocument) -> dict[str, Any]:
    """Campos do documento que vêm do registro de fontes (comparados para detectar mudanças)."""
    entry = doc.entry
    return {
        "titulo": entry.titulo,
        "tipo_documento": entry.tipo,
        "escopo": entry.escopo,
        "acesso": entry.acesso,
        "metadados": {
            "arquivo": entry.arquivo,
            "formato_final": entry.formato_final,
            "responsavel": entry.responsavel,
        },
    }


def insert_document(conn: psycopg.Connection, source_id: int, doc: SourceDocument) -> int:
    fields = document_fields(doc)
    row = conn.execute(
        "INSERT INTO documents (doc_key, source_id, titulo, tipo_documento, escopo, acesso, metadados)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            doc.doc_key,
            source_id,
            fields["titulo"],
            fields["tipo_documento"],
            fields["escopo"],
            Jsonb(fields["acesso"]),
            Jsonb(fields["metadados"]),
        ),
    ).fetchone()
    return row["id"]


def update_document(
    conn: psycopg.Connection, document_id: int, source_id: int, doc: SourceDocument
) -> None:
    """Atualiza os metadados e reativa o documento, caso estivesse aposentado."""
    fields = document_fields(doc)
    conn.execute(
        "UPDATE documents SET source_id = %s, titulo = %s, tipo_documento = %s, escopo = %s,"
        " acesso = %s, metadados = %s, status = 'active', aposentado_em = NULL WHERE id = %s",
        (
            source_id,
            fields["titulo"],
            fields["tipo_documento"],
            fields["escopo"],
            Jsonb(fields["acesso"]),
            Jsonb(fields["metadados"]),
            document_id,
        ),
    )


def retire_document(conn: psycopg.Connection, document_id: int, retired_on: date) -> None:
    conn.execute(
        "UPDATE documents SET status = 'retired', aposentado_em = %s WHERE id = %s",
        (retired_on, document_id),
    )


def insert_version(
    conn: psycopg.Connection,
    document_id: int,
    versao: int,
    doc: SourceDocument,
    valid_from: date,
) -> int:
    row = conn.execute(
        "INSERT INTO document_versions"
        " (document_id, versao, content_hash, conteudo, valid_from, valid_to, review_by)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            document_id,
            versao,
            doc.content_hash,
            doc.content,
            valid_from,
            doc.entry.vigencia.fim,
            doc.entry.revisar_ate,
        ),
    ).fetchone()
    return row["id"]


def update_version_dates(
    conn: psycopg.Connection,
    version_id: int,
    *,
    valid_from: date,
    valid_to: date | None,
    review_by: date | None,
) -> None:
    conn.execute(
        "UPDATE document_versions SET valid_from = %s, valid_to = %s, review_by = %s WHERE id = %s",
        (valid_from, valid_to, review_by, version_id),
    )


def close_version(conn: psycopg.Connection, version_id: int, valid_to: date) -> None:
    conn.execute(
        "UPDATE document_versions SET valid_to = %s WHERE id = %s", (valid_to, version_id)
    )


def document_versions(conn: psycopg.Connection, index_config_id: str, doc_key: str) -> list[dict]:
    return conn.execute(
        """
        SELECT v.versao, v.valid_from, v.valid_to, v.review_by, v.content_hash, v.ingerido_em,
               (SELECT count(*) FROM version_chunks vc
                 WHERE vc.version_id = v.id AND vc.index_config_id = %s) AS chunks
        FROM document_versions v
        JOIN documents d ON d.id = v.document_id
        WHERE d.doc_key = %s
        ORDER BY v.versao
        """,
        (index_config_id, doc_key),
    ).fetchall()


# --- Chunks -----------------------------------------------------------------------------


def upsert_chunks(conn: psycopg.Connection, document_id: int, chunks: list[Chunk]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO chunks"
            " (id, document_id, caminho_secao, tipo, conteudo, texto_embedding, content_hash)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            [
                (c.id, document_id, c.path_label, c.kind, c.content, c.embed_text, c.content_hash)
                for c in chunks
            ],
        )


def link_version_chunks(
    conn: psycopg.Connection, index_config_id: str, version_id: int, chunks: list[Chunk]
) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO version_chunks (index_config_id, version_id, chunk_id, ordem)"
            " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            [(index_config_id, version_id, c.id, c.ordinal) for c in chunks],
        )


def version_chunk_ids(conn: psycopg.Connection, index_config_id: str, version_id: int) -> set[str]:
    rows = conn.execute(
        "SELECT chunk_id FROM version_chunks WHERE index_config_id = %s AND version_id = %s",
        (index_config_id, version_id),
    ).fetchall()
    return {row["chunk_id"] for row in rows}


def document_chunks(
    conn: psycopg.Connection, index_config_id: str, doc_key: str, as_of: date
) -> list[dict]:
    """Chunks da versão do documento vigente na data `as_of`."""
    return conn.execute(
        """
        SELECT c.id, vc.ordem, c.caminho_secao, c.tipo, c.conteudo, v.versao
        FROM documents d
        JOIN document_versions v ON v.document_id = d.id
        JOIN version_chunks vc ON vc.version_id = v.id AND vc.index_config_id = %s
        JOIN chunks c ON c.id = vc.chunk_id
        WHERE d.doc_key = %s
          AND v.valid_from <= %s AND (v.valid_to IS NULL OR v.valid_to >= %s)
        ORDER BY vc.ordem
        """,
        (index_config_id, doc_key, as_of, as_of),
    ).fetchall()


def current_chunks(conn: psycopg.Connection, index_config_id: str, as_of: date) -> list[dict]:
    """Todos os trechos das versões vigentes em `as_of`, com os dados do documento."""
    return conn.execute(
        """
        SELECT c.id, c.conteudo, c.caminho_secao, d.doc_key, d.titulo, d.acesso, v.versao
        FROM chunks c
        JOIN version_chunks vc ON vc.chunk_id = c.id AND vc.index_config_id = %s
        JOIN document_versions v ON v.id = vc.version_id
        JOIN documents d ON d.id = v.document_id
        WHERE v.valid_from <= %s AND (v.valid_to IS NULL OR v.valid_to >= %s)
        ORDER BY d.doc_key, vc.ordem
        """,
        (index_config_id, as_of, as_of),
    ).fetchall()


def list_documents(conn: psycopg.Connection, as_of: date) -> list[dict]:
    """Documentos com a versão mais recente e a versão vigente em `as_of` (se houver)."""
    return conn.execute(
        """
        SELECT d.doc_key, d.titulo, d.tipo_documento, d.escopo, d.acesso, d.status,
               d.aposentado_em, max(v.versao) AS ultima_versao,
               max(v.versao) FILTER (
                   WHERE v.valid_from <= %(as_of)s AND (v.valid_to IS NULL OR v.valid_to >= %(as_of)s)
               ) AS versao_vigente
        FROM documents d
        JOIN document_versions v ON v.document_id = d.id
        GROUP BY d.id
        ORDER BY d.doc_key
        """,
        {"as_of": as_of},
    ).fetchall()


def version_content(conn: psycopg.Connection, doc_key: str, versao: int) -> str | None:
    row = conn.execute(
        "SELECT v.conteudo FROM document_versions v JOIN documents d ON d.id = v.document_id"
        " WHERE d.doc_key = %s AND v.versao = %s",
        (doc_key, versao),
    ).fetchone()
    return row["conteudo"] if row else None


# --- Configurações de índice ------------------------------------------------------------


def ensure_embedding_model(conn: psycopg.Connection, nome: str, dimensao: int, provedor: str) -> int:
    row = conn.execute("SELECT id, dimensao FROM embedding_models WHERE nome = %s", (nome,)).fetchone()
    if row:
        if row["dimensao"] != dimensao:
            raise KbError(
                f"O modelo '{nome}' já está registrado com dimensão {row['dimensao']}, "
                f"mas o Ollama retornou {dimensao}."
            )
        return row["id"]
    row = conn.execute(
        "INSERT INTO embedding_models (nome, dimensao, provedor) VALUES (%s, %s, %s) RETURNING id",
        (nome, dimensao, provedor),
    ).fetchone()
    return row["id"]


def ensure_index_config(
    conn: psycopg.Connection,
    chunker: str,
    chunker_params: dict[str, Any],
    embedding_model: str,
    dimensao: int,
) -> tuple[IndexConfig, bool]:
    """Garante a configuração de índice e a sua tabela de embeddings.

    Retorna a configuração e se ela foi ativada agora. Só é ativada automaticamente
    quando não existe nenhuma configuração ativa.
    """
    params_hash = hashlib.sha256(
        json.dumps(chunker_params, sort_keys=True).encode("utf-8")
    ).hexdigest()[:6]
    config_id = f"{chunker}-{_slug(embedding_model)}-{params_hash}"
    tabela = "emb_" + _slug(config_id).replace("-", "_")

    with conn.transaction():
        model_id = ensure_embedding_model(conn, embedding_model, dimensao, "ollama")
        conn.execute(
            "INSERT INTO index_configs (id, chunker, chunker_params, embedding_model_id, tabela_embeddings)"
            " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (config_id, chunker, Jsonb(chunker_params), model_id, tabela),
        )
        vectors.create_table(conn, tabela, dimensao)
        activated = False
        if conn.execute("SELECT 1 FROM index_configs WHERE ativo").fetchone() is None:
            conn.execute("UPDATE index_configs SET ativo = true WHERE id = %s", (config_id,))
            activated = True

    row = conn.execute(_INDEX_CONFIG_SELECT + " WHERE ic.id = %s", (config_id,)).fetchone()
    return IndexConfig(**row), activated


def get_index_config(conn: psycopg.Connection, config_id: str) -> IndexConfig:
    row = conn.execute(_INDEX_CONFIG_SELECT + " WHERE ic.id = %s", (config_id,)).fetchone()
    if row is None:
        raise KbError(f"Configuração de índice '{config_id}' não existe. Veja: kb indices")
    return IndexConfig(**row)


def list_index_configs(conn: psycopg.Connection) -> list[IndexConfig]:
    rows = conn.execute(_INDEX_CONFIG_SELECT + " ORDER BY ic.criado_em").fetchall()
    return [IndexConfig(**row) for row in rows]


def index_coverage(conn: psycopg.Connection, config: IndexConfig) -> dict[str, int]:
    """Quantas versões o índice cobre e quantos trechos vinculados ainda não têm embedding."""
    row = conn.execute(
        sql.SQL(
            """
            SELECT (SELECT count(*) FROM document_versions) AS versoes_total,
                   (SELECT count(DISTINCT version_id) FROM version_chunks
                     WHERE index_config_id = %(config)s) AS versoes_indexadas,
                   (SELECT count(DISTINCT vc.chunk_id) FROM version_chunks vc
                     LEFT JOIN {tabela} e ON e.chunk_id = vc.chunk_id
                     WHERE vc.index_config_id = %(config)s AND e.chunk_id IS NULL
                   ) AS trechos_sem_embedding,
                   (SELECT count(*) FROM {tabela}) AS embeddings
            """
        ).format(tabela=sql.Identifier(config.tabela)),
        {"config": config.id},
    ).fetchone()
    return dict(row)


def all_versions(conn: psycopg.Connection, doc_keys: list[str] | None = None) -> list[dict]:
    """Todas as versões de todos os documentos (inclusive aposentados), em ordem."""
    return conn.execute(
        """
        SELECT v.id AS version_id, v.document_id, d.doc_key, d.titulo, v.versao, v.conteudo
        FROM document_versions v JOIN documents d ON d.id = v.document_id
        WHERE %(todos)s OR d.doc_key = ANY(%(doc_keys)s)
        ORDER BY d.doc_key, v.versao
        """,
        {"todos": doc_keys is None, "doc_keys": doc_keys or []},
    ).fetchall()


def active_index_config(conn: psycopg.Connection) -> IndexConfig:
    row = conn.execute(_INDEX_CONFIG_SELECT + " WHERE ic.ativo").fetchone()
    if row is None:
        raise KbError("Nenhuma configuração de índice ativa. Rode: kb init")
    return IndexConfig(**row)


def stats(conn: psycopg.Connection) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT (SELECT count(*) FROM documents WHERE status = 'active') AS documentos_ativos,
               (SELECT count(*) FROM documents WHERE status = 'retired') AS documentos_aposentados,
               (SELECT count(*) FROM document_versions) AS versoes,
               (SELECT count(*) FROM chunks) AS chunks
        """
    ).fetchone()
    return dict(row)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
