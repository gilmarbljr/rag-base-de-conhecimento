"""Consultas usadas pelo relatório de saúde e o histórico de relatórios."""

from datetime import date

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

_CURRENT = "v.valid_from <= %(hoje)s AND (v.valid_to IS NULL OR v.valid_to >= %(hoje)s)"


def review_due(conn: psycopg.Connection, hoje: date, limite: date) -> list[dict]:
    """Versões vigentes com data de revisão até `limite`."""
    return conn.execute(
        f"""
        SELECT d.doc_key, d.titulo, v.versao, v.review_by
        FROM documents d JOIN document_versions v ON v.document_id = d.id
        WHERE d.status = 'active' AND {_CURRENT}
          AND v.review_by IS NOT NULL AND v.review_by <= %(limite)s
        ORDER BY v.review_by, d.doc_key
        """,
        {"hoje": hoje, "limite": limite},
    ).fetchall()


def expired_in_source(conn: psycopg.Connection, hoje: date) -> list[dict]:
    """Documentos ainda na fonte cuja vigência já terminou (nenhuma versão vale de hoje em diante)."""
    return conn.execute(
        """
        SELECT d.doc_key, d.titulo, max(v.valid_to) AS fim
        FROM documents d JOIN document_versions v ON v.document_id = d.id
        WHERE d.status = 'active'
        GROUP BY d.id
        HAVING bool_and(v.valid_to IS NOT NULL AND v.valid_to < %(hoje)s)
        ORDER BY fim, d.doc_key
        """,
        {"hoje": hoje},
    ).fetchall()


def near_duplicate_pairs(
    conn: psycopg.Connection, index_config_id: str, tabela: str, hoje: date, similaridade: float
) -> list[dict]:
    """Pares de documentos vigentes com trechos quase idênticos, e o tamanho de cada um."""
    return conn.execute(
        sql.SQL(
            f"""
            WITH atuais AS (
                SELECT c.id, d.doc_key
                FROM chunks c
                JOIN version_chunks vc ON vc.chunk_id = c.id AND vc.index_config_id = %(config)s
                JOIN document_versions v ON v.id = vc.version_id
                JOIN documents d ON d.id = v.document_id
                WHERE {_CURRENT}
            ),
            tamanhos AS (SELECT doc_key, count(*) AS trechos FROM atuais GROUP BY doc_key),
            pares AS (
                SELECT a.doc_key AS doc_a, b.doc_key AS doc_b, count(*) AS trechos_iguais
                FROM atuais a
                JOIN {{tabela}} ea ON ea.chunk_id = a.id
                JOIN atuais b ON a.doc_key < b.doc_key
                JOIN {{tabela}} eb ON eb.chunk_id = b.id
                WHERE 1 - (ea.embedding <=> eb.embedding) >= %(similaridade)s
                GROUP BY 1, 2
            )
            SELECT p.doc_a, p.doc_b, p.trechos_iguais, ta.trechos AS trechos_a, tb.trechos AS trechos_b
            FROM pares p
            JOIN tamanhos ta ON ta.doc_key = p.doc_a
            JOIN tamanhos tb ON tb.doc_key = p.doc_b
            ORDER BY p.trechos_iguais DESC, p.doc_a
            """
        ).format(tabela=sql.Identifier(tabela)),
        {"config": index_config_id, "hoje": hoje, "similaridade": similaridade},
    ).fetchall()


def chunks_without_embedding(
    conn: psycopg.Connection, index_config_id: str, tabela: str, hoje: date
) -> list[dict]:
    return conn.execute(
        sql.SQL(
            f"""
            SELECT d.doc_key, count(*) AS trechos
            FROM version_chunks vc
            JOIN document_versions v ON v.id = vc.version_id
            JOIN documents d ON d.id = v.document_id
            LEFT JOIN {{tabela}} e ON e.chunk_id = vc.chunk_id
            WHERE vc.index_config_id = %(config)s AND {_CURRENT} AND e.chunk_id IS NULL
            GROUP BY d.doc_key
            """
        ).format(tabela=sql.Identifier(tabela)),
        {"config": index_config_id, "hoje": hoje},
    ).fetchall()


def orphan_counts(conn: psycopg.Connection, index_config_id: str, tabela: str) -> dict[str, int]:
    """Embeddings sem trecho vinculado no índice, e trechos sem nenhuma versão."""
    row = conn.execute(
        sql.SQL(
            """
            SELECT
              (SELECT count(*) FROM {tabela} e WHERE NOT EXISTS (
                  SELECT 1 FROM version_chunks vc
                  WHERE vc.chunk_id = e.chunk_id AND vc.index_config_id = %(config)s
              )) AS embeddings_orfaos,
              (SELECT count(*) FROM chunks c WHERE NOT EXISTS (
                  SELECT 1 FROM version_chunks vc WHERE vc.chunk_id = c.id
              )) AS trechos_orfaos
            """
        ).format(tabela=sql.Identifier(tabela)),
        {"config": index_config_id},
    ).fetchone()
    return dict(row)


def latest_evals(conn: psycopg.Connection, index_config_id: str) -> list[dict]:
    """Última avaliação de cada pipeline no índice ativo."""
    return conn.execute(
        """
        SELECT DISTINCT ON (pipeline) id, pipeline, release, k, metricas, criado_em
        FROM eval_runs WHERE index_config_id = %s
        ORDER BY pipeline, id DESC
        """,
        (index_config_id,),
    ).fetchall()


def save_report(conn: psycopg.Connection, report: dict) -> int:
    row = conn.execute(
        "INSERT INTO health_reports (data_referencia, index_config_id, resumo, secoes)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (
            report["data_referencia"],
            report["index_config_id"],
            Jsonb(report["resumo"]),
            Jsonb(report["secoes"]),
        ),
    ).fetchone()
    return row["id"]


def list_reports(conn: psycopg.Connection, limit: int = 20) -> list[dict]:
    return conn.execute(
        "SELECT id, criado_em, data_referencia, index_config_id, resumo, secoes"
        " FROM health_reports ORDER BY id DESC LIMIT %s",
        (limit,),
    ).fetchall()
