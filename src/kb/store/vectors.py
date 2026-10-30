"""Vetores no pgvector: uma tabela por configuração de índice.

Os vetores são enviados como literal de texto (`'[0.1,0.2,...]'::vector`), o que evita
depender de adaptadores extras no cliente Python.
"""

from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING

import psycopg
from psycopg import sql

from kb.store.candidates import COLUMNS, JOINS, Candidate, eligibility

if TYPE_CHECKING:
    from kb.store.catalog import IndexConfig


def create_table(conn: psycopg.Connection, tabela: str, dimensao: int) -> None:
    conn.execute(
        sql.SQL(
            "CREATE TABLE IF NOT EXISTS {tabela} ("
            " chunk_id text PRIMARY KEY REFERENCES chunks (id),"
            " embedding vector({dimensao}) NOT NULL)"
        ).format(tabela=sql.Identifier(tabela), dimensao=sql.Literal(dimensao))
    )
    conn.execute(
        sql.SQL(
            "CREATE INDEX IF NOT EXISTS {indice} ON {tabela} USING hnsw (embedding vector_cosine_ops)"
        ).format(indice=sql.Identifier(f"{tabela}_hnsw"), tabela=sql.Identifier(tabela))
    )


def missing_ids(conn: psycopg.Connection, tabela: str, chunk_ids: Sequence[str]) -> list[str]:
    """IDs que ainda não têm embedding nesta tabela, na ordem recebida."""
    found = {
        row["chunk_id"]
        for row in conn.execute(
            sql.SQL("SELECT chunk_id FROM {tabela} WHERE chunk_id = ANY(%s)").format(
                tabela=sql.Identifier(tabela)
            ),
            (list(chunk_ids),),
        )
    }
    return [chunk_id for chunk_id in chunk_ids if chunk_id not in found]


def insert(
    conn: psycopg.Connection, tabela: str, items: Sequence[tuple[str, Sequence[float]]]
) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            sql.SQL(
                "INSERT INTO {tabela} (chunk_id, embedding) VALUES (%s, %s::vector)"
                " ON CONFLICT (chunk_id) DO NOTHING"
            ).format(tabela=sql.Identifier(tabela)),
            [(chunk_id, to_literal(vector)) for chunk_id, vector in items],
        )


def count(conn: psycopg.Connection, tabela: str) -> int:
    row = conn.execute(
        sql.SQL("SELECT count(*) AS n FROM {tabela}").format(tabela=sql.Identifier(tabela))
    ).fetchone()
    return row["n"]


def dense_search(
    conn: psycopg.Connection,
    config: "IndexConfig",
    query_vector: Sequence[float],
    *,
    limit: int,
    as_of: date,
    access_filter: sql.Composable,
) -> list[Candidate]:
    """Similaridade de cosseno entre os trechos elegíveis (vigentes em `as_of` e visíveis ao perfil).

    Só a vigência decide o que é atual: documentos aposentados têm a última versão encerrada,
    então somem da busca atual e continuam aparecendo em consultas históricas.
    """
    # Com filtros restritivos, o índice HNSW continua buscando até encontrar `limit` trechos
    # elegíveis, em vez de devolver menos resultados (ver ADR-002).
    conn.execute("SET hnsw.iterative_scan = strict_order")
    rows = conn.execute(
        sql.SQL(
            """
            SELECT {columns}, 1 - (e.embedding <=> %(vetor)s::vector) AS score
            FROM {tabela} e
            JOIN chunks c ON c.id = e.chunk_id
            {joins}
            WHERE {eligibility}
            ORDER BY e.embedding <=> %(vetor)s::vector
            LIMIT %(limit)s
            """
        ).format(
            columns=COLUMNS,
            tabela=sql.Identifier(config.tabela),
            joins=JOINS,
            eligibility=eligibility(access_filter),
        ),
        {"vetor": to_literal(query_vector), "config": config.id, "as_of": as_of, "limit": limit},
    ).fetchall()
    return [Candidate(**row) for row in rows]


def to_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{value:.7g}" for value in vector) + "]"
