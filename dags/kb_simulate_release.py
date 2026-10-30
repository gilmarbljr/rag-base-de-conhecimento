"""
### kb_simulate_release

Demonstração da linha do tempo: aponta a fonte para a pasta de uma release do corpus
(`data/corpus/release_<n>`) e dispara a `kb_sync`, esperando o fim da sincronização.

- Release 1 (15/01/2026): criação da base.
- Release 2 (10/02/2026): reajuste da tabela Altis, nova exigência no PCD Veltra e circular de fevereiro.
- Release 3 (10/03/2026): encerramento do Programa Rota Limpa e cópia encaminhada da circular.
"""

from datetime import datetime

from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.sdk import Param, dag, task
from kb_common import kb_path

from kb.errors import KbError


@dag(
    dag_id="kb_simulate_release",
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    params={"release": Param(1, type="integer", enum=[1, 2, 3], description="Release do corpus")},
    tags=["kb", "demonstracao"],
    doc_md=__doc__,
)
def kb_simulate_release():
    @task
    def localizar_release(**context) -> str:
        pasta = f"data/corpus/release_{context['params']['release']}"
        if not (kb_path(pasta) / "manifest.yaml").exists():
            raise KbError(f"Release não encontrada: {pasta}. Rode scripts/generate_corpus.py")
        return pasta

    pasta = localizar_release()
    disparar = TriggerDagRunOperator(
        task_id="disparar_kb_sync",
        trigger_dag_id="kb_sync",
        conf={"pasta": "{{ ti.xcom_pull(task_ids='localizar_release') }}"},
        wait_for_completion=True,
        poke_interval=10,
    )
    pasta >> disparar


kb_simulate_release()
