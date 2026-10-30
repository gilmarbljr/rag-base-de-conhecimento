import psycopg
from psycopg.types.json import Jsonb

from kb.evaluation.runner import EvalReport


def save_run(conn: psycopg.Connection, report: EvalReport) -> int:
    data = report.to_dict()
    row = conn.execute(
        "INSERT INTO eval_runs (pipeline, index_config_id, release, k, metricas, itens)"
        " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (
            report.pipeline,
            report.index_config_id,
            report.release,
            report.k,
            Jsonb(data["metricas"]),
            Jsonb(data["itens"]),
        ),
    ).fetchone()
    return row["id"]


def history(conn: psycopg.Connection, limit: int = 200) -> list[dict]:
    """Execuções registradas, da mais antiga para a mais recente."""
    return conn.execute(
        "SELECT * FROM (SELECT id, pipeline, index_config_id, release, k, metricas, itens, criado_em"
        " FROM eval_runs ORDER BY id DESC LIMIT %s) recentes ORDER BY id",
        (limit,),
    ).fetchall()


def previous_metrics(
    conn: psycopg.Connection,
    *,
    pipeline: str,
    index_config_id: str,
    release: int,
    k: int,
    before_id: int | None = None,
) -> dict | None:
    """Métricas da última execução comparável (mesma pipeline, índice, release e k)."""
    row = conn.execute(
        "SELECT metricas FROM eval_runs"
        " WHERE pipeline = %s AND index_config_id = %s AND release = %s AND k = %s"
        "   AND (%s::int IS NULL OR id < %s::int)"
        " ORDER BY id DESC LIMIT 1",
        (pipeline, index_config_id, release, k, before_id, before_id),
    ).fetchone()
    return row["metricas"] if row else None
