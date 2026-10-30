"""Fluxo completo de reindexação blue/green, usado pela CLI (`kb reindex`) e pela DAG `kb_reindex`.

A avaliação usa o golden set de todas as releases disponíveis, com as datas de cada uma:
como o índice guarda todas as versões, um índice novo precisa responder bem em janeiro,
fevereiro e março, não só na data atual.
"""

from dataclasses import dataclass, field
from typing import Literal

import psycopg

from kb import components
from kb.chunking import build_chunker
from kb.config import Settings
from kb.embedding import OllamaEmbedder
from kb.errors import KbError
from kb.evaluation.service import evaluate
from kb.maintenance import reindex
from kb.maintenance.reindex import BuildResult, Decision
from kb.store import catalog
from kb.store.catalog import IndexConfig

Promocao = Literal["auto", "sim", "nao"]


@dataclass
class ReindexOutcome:
    config: IndexConfig
    build: BuildResult
    candidato: dict
    atual: dict
    decisao: Decision
    promovido: bool = False
    complemento: BuildResult | None = field(default=None)


def golden_releases(settings: Settings) -> list[int]:
    return sorted(
        int(path.stem.removeprefix("release_"))
        for path in settings.golden_set_dir.glob("release_*.yaml")
    )


def evaluate_index(
    conn: psycopg.Connection, settings: Settings, config: IndexConfig, pipeline: str, *, fonte: str
) -> dict:
    """Métricas do índice somadas sobre os golden sets de todas as releases.

    Recall e MRR são médias ponderadas pelo número de perguntas avaliadas em cada release.
    """
    totals = {"perguntas_avaliadas": 0, "recall@1": 0.0, "mrr": 0.0, "vazamentos": 0,
              "proibidos": 0, "latencia_media_ms": 0.0, "releases": []}
    for release in golden_releases(settings):
        [outcome] = evaluate(conn, settings, [pipeline], fonte=fonte, release=release, index=config)
        m = outcome.report.metricas
        n = m["perguntas_avaliadas"]
        totals["perguntas_avaliadas"] += n
        totals["recall@1"] += m["recall@1"] * n
        totals["mrr"] += m["mrr"] * n
        totals["vazamentos"] += m["vazamentos"]
        totals["proibidos"] += m["proibidos"]
        totals["latencia_media_ms"] += m["latencia_media_ms"]
        totals["releases"].append(release)
    n = totals["perguntas_avaliadas"] or 1
    totals["recall@1"] = round(totals["recall@1"] / n, 4)
    totals["mrr"] = round(totals["mrr"] / n, 4)
    totals["latencia_media_ms"] = round(totals["latencia_media_ms"] / max(len(totals["releases"]), 1), 1)
    return totals


def candidate_components(
    settings: Settings, *, chunker_name: str, chunker_params: dict, model: str
):
    embedder = OllamaEmbedder(settings.ollama_url, model)
    return build_chunker(chunker_name, chunker_params, embedder=embedder), embedder


def run(
    conn: psycopg.Connection,
    settings: Settings,
    *,
    chunker_name: str,
    chunker_params: dict,
    model: str,
    pipeline: str,
    fonte: str = components.FONTE_PADRAO,
    promover: Promocao = "auto",
) -> ReindexOutcome:
    chunker, embedder = candidate_components(
        settings, chunker_name=chunker_name, chunker_params=chunker_params, model=model
    )
    active = catalog.active_index_config(conn)
    config, _ = reindex.prepare(conn, chunker=chunker, embedder=embedder)
    if config.id == active.id:
        raise KbError(f"A configuração {config.id} já é o índice ativo.")

    build = reindex.build(conn, config, chunker=chunker, embedder=embedder)
    candidato = evaluate_index(conn, settings, config, pipeline, fonte=fonte)
    atual = evaluate_index(conn, settings, active, pipeline, fonte=fonte)
    decisao = reindex.decide(candidato, atual)
    outcome = ReindexOutcome(config, build, candidato, atual, decisao)

    if promover == "sim" or (promover == "auto" and decisao.promover):
        # Inclui versões que chegaram durante a construção, e só então troca.
        outcome.complemento = reindex.build(conn, config, chunker=chunker, embedder=embedder)
        reindex.promote(conn, config.id)
        outcome.promovido = True
    return outcome
