"""
### kb_reindex

Reindexação blue/green: constrói um índice candidato ao lado do ativo, avalia os dois com o
golden set de todas as releases e só troca se o candidato não for pior.

1. **preparar**: registra a configuração candidata (chunker + parâmetros + modelo).
2. **construir**: uma tarefa por documento (dynamic task mapping), indexando todas as versões.
3. **avaliar_candidato** e **avaliar_ativo**: mesma pipeline, mesmas perguntas.
4. **decidir** (branch): `promover` se não piorar recall@1 nem MRR e não vazar; senão `manter`.
5. **promover**: completa versões que chegaram durante a construção e troca o índice ativo.
   O índice anterior fica intacto: rollback com `kb ativar <id>`.

Parâmetros: `modelo` e `chunker` (padrão: os do índice ativo), `parametros` do chunker,
`pipeline` da avaliação e `promover` (auto, sim, nao).
"""

from datetime import datetime, timedelta

from airflow.sdk import Param, dag, task
from kb_common import FONTE, kb_path

from kb.config import Settings
from kb.maintenance import blue_green, reindex
from kb.store import catalog
from kb.store.db import connect


def _session():
    kb_path(".")
    settings = Settings()
    return settings, connect(settings.database_url)


@dag(
    dag_id="kb_reindex",
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    params={
        "modelo": Param(None, type=["null", "string"], description="Modelo de embedding no Ollama"),
        "chunker": Param(None, type=["null", "string"], enum=[None, "hierarquico", "recursivo", "fixo", "semantico"]),
        "parametros": Param({}, type="object", description='Ex.: {"max_chars": 1800, "min_chars": 200}'),
        "pipeline": Param(None, type=["null", "string"], description="Pipeline da avaliação"),
        "promover": Param("auto", type="string", enum=["auto", "sim", "nao"]),
    },
    tags=["kb", "manutencao"],
    doc_md=__doc__,
)
def kb_reindex():
    @task
    def preparar(**context) -> dict:
        params = context["params"]
        settings, conn = _session()
        with conn:
            active = catalog.active_index_config(conn)
            chunker_name = params.get("chunker") or active.chunker
            chunker_params = dict(active.chunker_params) if chunker_name == active.chunker else {}
            chunker_params.update(params.get("parametros") or {})
            model = params.get("modelo") or active.embedding_model
            chunker, embedder = blue_green.candidate_components(
                settings, chunker_name=chunker_name, chunker_params=chunker_params, model=model
            )
            config, _ = reindex.prepare(conn, chunker=chunker, embedder=embedder)
        if config.id == active.id:
            raise ValueError(f"A configuração {config.id} já é o índice ativo.")
        print(f"Candidato: {config.id} | ativo: {active.id}")
        return {
            "candidato": config.id,
            "ativo": active.id,
            "chunker": chunker_name,
            "parametros": chunker_params,
            "modelo": model,
            "pipeline": params.get("pipeline") or settings.pipeline,
            "promover": params.get("promover", "auto"),
        }

    @task
    def listar_documentos() -> list[str]:
        _, conn = _session()
        with conn:
            return sorted({v["doc_key"] for v in catalog.all_versions(conn)})

    @task(retries=2, retry_delay=timedelta(seconds=20), max_active_tis_per_dag=2)
    def construir(doc_key: str, ctx: dict) -> dict:
        settings, conn = _session()
        chunker, embedder = blue_green.candidate_components(
            settings, chunker_name=ctx["chunker"], chunker_params=ctx["parametros"], model=ctx["modelo"]
        )
        with conn:
            config = catalog.get_index_config(conn, ctx["candidato"])
            result = reindex.build(conn, config, chunker=chunker, embedder=embedder, doc_keys=[doc_key])
        print(f"{doc_key}: {result}")
        return {"versoes": result.versoes, "novos_embeddings": result.novos_embeddings}

    def _evaluate(ctx: dict, which: str) -> dict:
        settings, conn = _session()
        with conn:
            config = catalog.get_index_config(conn, ctx[which])
            metrics = blue_green.evaluate_index(conn, settings, config, ctx["pipeline"], fonte=FONTE)
        print(f"{which} {config.id}: {metrics}")
        return metrics

    @task(execution_timeout=timedelta(hours=1))
    def avaliar_candidato(ctx: dict, _construidos: list[dict]) -> dict:
        return _evaluate(ctx, "candidato")

    @task(execution_timeout=timedelta(hours=1))
    def avaliar_ativo(ctx: dict) -> dict:
        return _evaluate(ctx, "ativo")

    @task.branch
    def decidir(ctx: dict, candidato: dict, ativo: dict) -> str:
        decision = reindex.decide(candidato, ativo)
        print(f"Decisão: {'promover' if decision.promover else 'manter'} {decision.motivos}")
        if ctx["promover"] == "sim" or (ctx["promover"] == "auto" and decision.promover):
            return "promover"
        return "manter"

    @task
    def promover(ctx: dict) -> str:
        settings, conn = _session()
        chunker, embedder = blue_green.candidate_components(
            settings, chunker_name=ctx["chunker"], chunker_params=ctx["parametros"], model=ctx["modelo"]
        )
        with conn:
            config = catalog.get_index_config(conn, ctx["candidato"])
            complemento = reindex.build(conn, config, chunker=chunker, embedder=embedder)
            reindex.promote(conn, config.id)
        print(f"Promovido {config.id} (complemento: {complemento}). Rollback: kb ativar {ctx['ativo']}")
        return config.id

    @task
    def manter(ctx: dict, candidato: dict, ativo: dict) -> str:
        motivos = reindex.decide(candidato, ativo).motivos or ["promoção desligada (promover=nao)"]
        print(f"Índice ativo mantido: {ctx['ativo']}. Motivos: {'; '.join(motivos)}")
        return ctx["ativo"]

    ctx = preparar()
    construidos = construir.partial(ctx=ctx).expand(doc_key=listar_documentos())
    candidato = avaliar_candidato(ctx, construidos)
    ativo = avaliar_ativo(ctx)
    decidir(ctx, candidato, ativo) >> [promover(ctx), manter(ctx, candidato, ativo)]


kb_reindex()
