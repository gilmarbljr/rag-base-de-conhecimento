"""
### kb_health_report

Relatório de saúde semanal da base de conhecimento. As verificações rodam em paralelo e
são consolidadas no final:

- **revisao**: documentos com revisão vencida ou próxima;
- **vigencia**: documentos ainda na fonte com a vigência encerrada;
- **duplicatas**: documentos com trechos quase idênticos;
- **conflitos**: regras contraditórias entre documentos vigentes (heurística + juiz LLM);
- **consistencia**: trechos vigentes sem embedding e registros órfãos;
- **chunks**: distribuição do tamanho dos trechos;
- **lacunas**: perguntas reais sem boa resposta (log de consultas);
- **avaliacao**: última avaliação de cada pipeline e regressões.

**consolidar** registra o relatório em `health_reports` e grava o Markdown em `reports/saude/`.
Parâmetros: `data` (padrão: relógio configurado) e `usar_juiz` (confirmar conflitos com o LLM).
"""

from collections.abc import Callable
from datetime import date, datetime, timedelta

from airflow.sdk import Param, dag, task
from kb_common import kb_path

from kb import components
from kb.config import Settings
from kb.maintenance import health
from kb.maintenance.health import HealthReport, Section
from kb.store import catalog
from kb.store import health as health_store
from kb.store.db import connect


def _run_check(contexto: dict, check: Callable[..., Section], *extra) -> dict:
    """Abre a sessão, roda uma verificação e devolve a seção serializável (XCom)."""
    kb_path(".")
    settings = Settings()
    cfg = health.load_health_config(settings.saude_path)
    hoje = date.fromisoformat(contexto["hoje"])
    with connect(settings.database_url) as conn:
        index = catalog.active_index_config(conn)
        section = check(conn, index, hoje, cfg, settings, *extra)
    print(f"{section.titulo}: {len(section.achados)} achado(s)")
    for achado in section.achados:
        print(f"  [{achado.severidade}] {achado.titulo}")
    return section.to_dict()


@dag(
    dag_id="kb_health_report",
    schedule="@weekly",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    params={
        "data": Param(None, type=["null", "string"], description="AAAA-MM-DD"),
        "usar_juiz": Param(True, type="boolean", description="Confirmar conflitos com o LLM"),
    },
    tags=["kb", "manutencao"],
    doc_md=__doc__,
)
def kb_health_report():
    @task
    def contexto(**context) -> dict:
        kb_path(".")
        settings = Settings()
        params = context["params"]
        with connect(settings.database_url) as conn:
            hoje = params.get("data") or components.hoje(settings, conn).isoformat()
            index = catalog.active_index_config(conn)
        return {"hoje": hoje, "index": index.id, "usar_juiz": params.get("usar_juiz", True)}

    @task
    def revisao(ctx: dict) -> dict:
        return _run_check(ctx, lambda conn, index, hoje, cfg, s: health.check_review_dates(conn, hoje, cfg))

    @task
    def vigencia(ctx: dict) -> dict:
        return _run_check(ctx, lambda conn, index, hoje, cfg, s: health.check_expired(conn, hoje))

    @task
    def duplicatas(ctx: dict) -> dict:
        return _run_check(ctx, lambda conn, index, hoje, cfg, s: health.check_duplicates(conn, index, hoje, cfg))

    @task(execution_timeout=timedelta(hours=1))
    def conflitos(ctx: dict, secao_duplicatas: dict) -> dict:
        def check(conn, index, hoje, cfg, settings):
            model = components.chat_model(settings) if ctx["usar_juiz"] else None
            return health.check_conflicts(
                conn, index, hoje, cfg, model, secao_duplicatas["metricas"].get("pares", [])
            )

        return _run_check(ctx, check)

    @task
    def consistencia(ctx: dict) -> dict:
        return _run_check(ctx, lambda conn, index, hoje, cfg, s: health.check_consistency(conn, index, hoje))

    @task
    def chunks(ctx: dict) -> dict:
        return _run_check(ctx, lambda conn, index, hoje, cfg, s: health.check_chunk_sizes(conn, index, hoje, cfg))

    @task
    def lacunas(ctx: dict) -> dict:
        return _run_check(ctx, lambda conn, index, hoje, cfg, s: health.check_gaps(conn, cfg))

    @task
    def avaliacao(ctx: dict) -> dict:
        return _run_check(ctx, lambda conn, index, hoje, cfg, s: health.check_evaluations(conn, index))

    @task
    def consolidar(ctx: dict, secoes: list[dict]) -> dict:
        kb_path(".")
        settings = Settings()
        report = HealthReport(
            data_referencia=date.fromisoformat(ctx["hoje"]),
            index_config_id=ctx["index"],
            secoes=[Section.from_dict(s) for s in secoes],
        )
        with connect(settings.database_url) as conn:
            report_id = health_store.save_report(conn, report.to_dict())
        path = settings.reports_dir / "saude" / f"{ctx['hoje']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report.to_markdown(), encoding="utf-8")
        print(f"Relatório {report_id} gravado em {path}: {report.resumo}")
        return report.resumo

    ctx = contexto()
    secao_duplicatas = duplicatas(ctx)
    consolidar(
        ctx,
        [
            revisao(ctx),
            vigencia(ctx),
            secao_duplicatas,
            conflitos(ctx, secao_duplicatas),
            consistencia(ctx),
            chunks(ctx),
            lacunas(ctx),
            avaliacao(ctx),
        ],
    )


kb_health_report()
