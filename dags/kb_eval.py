"""
### kb_eval

Avalia a base com o golden set sempre que o índice é atualizado. É disparada pelo asset
`kb_indice`, emitido pela `kb_sync`, e também pode ser rodada manualmente.

1. **listar_pipelines**: as pipelines de consulta em `configs/pipelines/`.
2. **avaliar**: uma tarefa por pipeline (dynamic task mapping). Roda o golden set da release
   atual, registra a execução em `eval_runs` e compara com a anterior.
3. **verificar**: falha a DAG se houver vazamento de acesso, documento proibido ou queda de
   métrica em relação à execução anterior.
"""

from datetime import datetime, timedelta

from airflow.sdk import dag, task
from kb_common import FONTE, KB_INDICE, kb_path

from kb.config import Settings
from kb.errors import KbError
from kb.evaluation.service import evaluate
from kb.retrieval import list_pipelines
from kb.store.db import connect


@dag(
    dag_id="kb_eval",
    schedule=[KB_INDICE],
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["kb", "qualidade"],
    doc_md=__doc__,
)
def kb_eval():
    @task
    def listar_pipelines() -> list[str]:
        kb_path(".")
        return list_pipelines(Settings().pipelines_dir)

    # Uma pipeline por vez: as avaliações disputariam a mesma CPU do Ollama e do reranker.
    @task(
        retries=1,
        retry_delay=timedelta(seconds=30),
        execution_timeout=timedelta(minutes=30),
        max_active_tis_per_dag=1,
    )
    def avaliar(pipeline: str) -> dict:
        kb_path(".")
        settings = Settings()
        with connect(settings.database_url) as conn:
            [outcome] = evaluate(conn, settings, [pipeline], fonte=FONTE)
        report = outcome.report
        print(f"{report.pipeline} | release {report.release} | {report.metricas}")
        for item in report.itens:
            if item.tem_esperado and item.posicao != 1:
                print(f"  {item.id}: posição {item.posicao}; 1º recuperado: {item.recuperados[:1]}")
        return {
            "pipeline": report.pipeline,
            "release": report.release,
            "metricas": report.metricas,
            "problemas": outcome.problemas,
        }

    @task
    def verificar(resultados: list[dict]) -> None:
        problemas = [f"{r['pipeline']}: {p}" for r in resultados for p in r["problemas"]]
        for r in resultados:
            m = r["metricas"]
            print(f"{r['pipeline']}: recall@k={m['recall@k']} mrr={m['mrr']} vazamentos={m['vazamentos']}")
        if problemas:
            raise KbError("Avaliação reprovada:\n" + "\n".join(problemas))

    verificar(avaliar.expand(pipeline=listar_pipelines()))


kb_eval()
