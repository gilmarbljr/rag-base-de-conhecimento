"""
### kb_sync

Sincroniza a base de conhecimento com a pasta da fonte.

1. **planejar**: classifica cada documento (novo, nova versão, metadados, inalterado) e
   lista os que saíram da fonte. **listar_pendentes** extrai os que precisam de processamento.
2. **processar_documento**: uma tarefa por documento pendente (dynamic task mapping), com
   retry próprio. A falha de um documento não interrompe os outros.
3. **aposentar_removidos**: tombstone dos documentos que saíram da fonte.
4. **publicar_indice**: registra o resumo e emite o asset `kb_indice`, que dispara as
   DAGs que dependem do índice atualizado.

Parâmetros: `pasta` (opcional; padrão = pasta registrada para a fonte) e `data_efetiva`
(opcional; padrão = data de referência do manifest).
"""

from datetime import date, datetime, timedelta

from airflow.sdk import Param, dag, task
from kb_common import FONTE, KB_INDICE, PASTA_INICIAL, components, kb_path, kb_session, parse_date

from kb.errors import KbError
from kb.maintenance import plan_sync, retire_documents, sync_document
from kb.store import catalog


@dag(
    dag_id="kb_sync",
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    params={
        "pasta": Param(None, type=["null", "string"], description="Pasta da fonte, relativa ao projeto."),
        "data_efetiva": Param(None, type=["null", "string"], description="AAAA-MM-DD"),
    },
    tags=["kb", "ingestao"],
    doc_md=__doc__,
)
def kb_sync():
    @task
    def planejar(**context) -> dict:
        params = context["params"]
        with kb_session() as (settings, conn, _):
            pasta = params.get("pasta")
            if not pasta:
                try:
                    pasta = catalog.get_source(conn, FONTE)["caminho"]
                except KbError:
                    pasta = PASTA_INICIAL  # primeira carga
            plan = plan_sync(
                conn,
                kb_path(pasta),
                source_name=FONTE,
                data_efetiva=parse_date(params.get("data_efetiva")),
                hoje=settings.hoje(),
            )
        print(f"Plano para {plan.pasta} (data efetiva {plan.data_efetiva}): {plan.acoes}")
        print(f"A aposentar: {plan.aposentar or 'nenhum'}")
        return plan.to_dict()

    @task
    def listar_pendentes(plano: dict) -> list[str]:
        # O mapeamento dinâmico precisa expandir sobre o valor de retorno inteiro de uma tarefa.
        return plano["pendentes"]

    @task(retries=2, retry_delay=timedelta(seconds=20), max_active_tis_per_dag=2)
    def processar_documento(doc_key: str, plano: dict) -> dict:
        with kb_session() as (settings, conn, config):
            chunker, embedder = components(settings, config)
            result = sync_document(
                conn,
                kb_path(plano["pasta"]),
                doc_key,
                source_name=plano["fonte"],
                data_efetiva=date.fromisoformat(plano["data_efetiva"]),
                chunker=chunker,
                embedder=embedder,
                config=config,
            )
        print(result)
        return result.to_dict()

    @task
    def aposentar_removidos(plano: dict) -> list[dict]:
        if not plano["aposentar"]:
            return []
        with kb_session() as (_, conn, config):
            data_efetiva = date.fromisoformat(plano["data_efetiva"])
            results = retire_documents(conn, plano["aposentar"], data_efetiva, config)
        for result in results:
            print(result)
        return [r.to_dict() for r in results]

    @task(outlets=[KB_INDICE], trigger_rule="none_failed")
    def publicar_indice(processados: list[dict], aposentados: list[dict]) -> dict:
        resumo: dict[str, int] = {}
        for result in [*processados, *aposentados]:
            resumo[result["status"]] = resumo.get(result["status"], 0) + 1
        print(f"Resumo da sincronização: {resumo or 'nenhuma mudança'}")
        return resumo

    plano = planejar()
    processados = processar_documento.partial(plano=plano).expand(doc_key=listar_pendentes(plano))
    aposentados = aposentar_removidos(plano)
    publicar_indice(processados, aposentados)


kb_sync()
