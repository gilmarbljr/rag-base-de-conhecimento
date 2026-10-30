from datetime import date

import psycopg
from psycopg.types.json import Jsonb


def insert(
    conn: psycopg.Connection,
    *,
    origem: str,
    pergunta: str,
    perfil: str | None,
    pipeline: str,
    index_config_id: str,
    as_of: date,
    k: int,
    latencia_ms: float,
    resultados: list[dict],
) -> int:
    row = conn.execute(
        "INSERT INTO query_logs (origem, pergunta, perfil, pipeline, index_config_id, as_of, k,"
        " latencia_ms, melhor_score, resultados)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            origem,
            pergunta,
            perfil,
            pipeline,
            index_config_id,
            as_of,
            k,
            latencia_ms,
            resultados[0]["score"] if resultados else None,
            Jsonb(resultados),
        ),
    ).fetchone()
    return row["id"]


def recent(conn: psycopg.Connection, limit: int = 50) -> list[dict]:
    return conn.execute(
        "SELECT id, criado_em, origem, pergunta, perfil, pipeline, as_of, latencia_ms, melhor_score,"
        " resultados FROM query_logs ORDER BY id DESC LIMIT %s",
        (limit,),
    ).fetchall()


def gaps(
    conn: psycopg.Connection, *, pipeline_nome: str, score_maximo: float, dias: int
) -> list[dict]:
    """Perguntas cuja melhor resposta ficou abaixo do limiar, agrupadas pelo texto.

    Só compara consultas da mesma pipeline, porque cada pipeline tem a sua escala de score.
    A janela usa a data real do log (quando a pergunta foi feita).
    """
    return conn.execute(
        """
        SELECT lower(trim(pergunta)) AS pergunta, count(*) AS ocorrencias,
               max(melhor_score) AS melhor_score, array_agg(DISTINCT perfil) AS perfis,
               max(criado_em) AS ultima_vez
        FROM query_logs
        WHERE pipeline LIKE %s
          AND criado_em >= now() - make_interval(days => %s)
          AND (melhor_score IS NULL OR melhor_score < %s)
        GROUP BY 1
        ORDER BY ocorrencias DESC, melhor_score NULLS FIRST
        """,
        (f"{pipeline_nome}@%", dias, score_maximo),
    ).fetchall()
