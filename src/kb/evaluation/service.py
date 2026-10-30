from dataclasses import dataclass

import psycopg

from kb import components
from kb.config import Settings
from kb.evaluation.golden import load_golden_set
from kb.evaluation.runner import EvalReport, find_regressions, run_eval
from kb.retrieval import load_pipeline
from kb.store import evals
from kb.store.catalog import IndexConfig


@dataclass
class EvalOutcome:
    report: EvalReport
    previous: dict | None
    problemas: list[str]
    run_id: int | None


def evaluate(
    conn: psycopg.Connection,
    settings: Settings,
    pipeline_names: list[str],
    *,
    fonte: str,
    release: int | None = None,
    k: int = 5,
    save: bool = True,
    index: IndexConfig | None = None,
) -> list[EvalOutcome]:
    """Roda o golden set da release (padrão: a release atual da fonte) em cada pipeline.

    Avalia o índice informado (padrão: o ativo). Cada execução é comparada com a última
    execução comparável já registrada.
    """
    release = release or components.current_release(conn, fonte)
    golden = load_golden_set(settings.golden_set_dir / f"release_{release}.yaml")
    pipelines = [load_pipeline(settings.pipelines_dir, name) for name in pipeline_names]
    needs_reranker = any(p.rerank.ativo for p in pipelines)
    retriever = components.retriever(settings, conn, with_reranker=needs_reranker, index=index)

    outcomes = []
    for pipeline in pipelines:
        report = run_eval(retriever, golden, pipeline, retriever.policy, k=k)
        previous = evals.previous_metrics(
            conn, pipeline=report.pipeline, index_config_id=report.index_config_id,
            release=report.release, k=k,
        )
        run_id = evals.save_run(conn, report) if save else None
        outcomes.append(
            EvalOutcome(report, previous, find_regressions(report.metricas, previous), run_id)
        )
    return outcomes
