"""Busca textual com o full-text do Postgres (dicionário português).

A pergunta vira uma consulta OU entre os seus lexemas: perguntas em linguagem natural
raramente têm todas as palavras no trecho certo, e o ranking (`ts_rank_cd`) favorece os
trechos que cobrem mais termos. Códigos como "CMP-0126" e "NX-T AT" são o ponto forte
desta busca em relação à vetorial.
"""

from datetime import date
from typing import TYPE_CHECKING

import psycopg
from psycopg import sql

from kb.store.candidates import COLUMNS, JOINS, Candidate, eligibility

if TYPE_CHECKING:
    from kb.store.catalog import IndexConfig


def sparse_search(
    conn: psycopg.Connection,
    config: "IndexConfig",
    question: str,
    *,
    limit: int,
    as_of: date,
    access_filter: sql.Composable,
) -> list[Candidate]:
    rows = conn.execute(
        sql.SQL(
            """
            WITH q AS (
                SELECT to_tsquery('simple', string_agg(quote_literal(lexema), ' | ')) AS consulta
                FROM unnest(tsvector_to_array(to_tsvector('portuguese', %(texto)s))) AS lexema
            )
            SELECT {columns}, ts_rank_cd(c.tsv, q.consulta) AS score
            FROM q CROSS JOIN chunks c
            {joins}
            WHERE q.consulta IS NOT NULL AND c.tsv @@ q.consulta AND {eligibility}
            ORDER BY score DESC, c.id
            LIMIT %(limit)s
            """
        ).format(columns=COLUMNS, joins=JOINS, eligibility=eligibility(access_filter)),
        {"texto": question, "config": config.id, "as_of": as_of, "limit": limit},
    ).fetchall()
    return [Candidate(**row) for row in rows]
