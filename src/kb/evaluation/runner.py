"""Avaliação da recuperação com o golden set.

Para cada pergunta, roda a pipeline com o perfil e a data da pergunta e registra:
- a posição do primeiro trecho esperado (recall@n e MRR);
- vazamentos: trechos que o perfil não pode ver, conferidos em Python, independentemente
  do filtro SQL que a busca aplicou;
- proibidos: documentos que não podem aparecer (por exemplo, com a vigência encerrada).

As métricas de recuperação consideram só as perguntas que têm trechos esperados. Perguntas
sem resposta na base continuam valendo para vazamentos e proibidos.
"""

import time
from dataclasses import asdict, dataclass, field

from kb.access import AccessPolicy
from kb.evaluation.golden import GoldenItem, GoldenSet
from kb.retrieval import PipelineConfig, Retriever, SearchHit

# Uma piora menor que isto é ruído de arredondamento, não regressão.
_TOLERANCE = 1e-6
_TRACKED = ("recall@1", "recall@3", "recall@k", "mrr")


@dataclass
class ItemResult:
    id: str
    tipo: str
    perfil: str | None
    tem_esperado: bool
    posicao: int | None
    recuperados: list[str]
    vazamentos: list[str] = field(default_factory=list)
    proibidos: list[str] = field(default_factory=list)
    latencia_ms: float = 0.0


@dataclass
class EvalReport:
    pipeline: str
    index_config_id: str
    release: int
    k: int
    itens: list[ItemResult]
    metricas: dict[str, float]

    def to_dict(self) -> dict:
        return asdict(self)


def run_eval(
    retriever: Retriever,
    golden: GoldenSet,
    pipeline: PipelineConfig,
    policy: AccessPolicy,
    *,
    k: int = 5,
) -> EvalReport:
    itens = [_evaluate_item(retriever, golden, item, pipeline, policy, k) for item in golden.perguntas]
    return EvalReport(
        pipeline=pipeline.id,
        index_config_id=retriever.index.id,
        release=golden.release,
        k=k,
        itens=itens,
        metricas=compute_metrics(itens, k),
    )


def _evaluate_item(
    retriever: Retriever,
    golden: GoldenSet,
    item: GoldenItem,
    pipeline: PipelineConfig,
    policy: AccessPolicy,
    k: int,
) -> ItemResult:
    profile = policy.profile(item.perfil) if item.perfil else None
    started = time.perf_counter()
    hits = retriever.search(
        item.pergunta,
        pipeline=pipeline,
        as_of=item.as_of or golden.data_referencia,
        k=k,
        profile=profile,
    )
    latency = (time.perf_counter() - started) * 1000

    position = next(
        (
            n
            for n, hit in enumerate(hits, start=1)
            if any(e.matches(hit.doc_key, hit.caminho_secao, hit.versao) for e in item.esperado)
        ),
        None,
    )
    return ItemResult(
        id=item.id,
        tipo=item.tipo,
        perfil=item.perfil,
        tem_esperado=bool(item.esperado),
        posicao=position,
        recuperados=[_label(hit) for hit in hits],
        vazamentos=[_label(h) for h in hits if not policy.allows(profile, h.candidate.acesso)],
        proibidos=[_label(h) for h in hits if h.doc_key in item.proibido],
        latencia_ms=round(latency, 1),
    )


def compute_metrics(itens: list[ItemResult], k: int) -> dict[str, float]:
    graded = [item for item in itens if item.tem_esperado]

    def recall_at(n: int) -> float:
        hits = sum(1 for i in graded if i.posicao is not None and i.posicao <= n)
        return hits / len(graded) if graded else 0.0

    mrr = sum(1 / i.posicao for i in graded if i.posicao) / len(graded) if graded else 0.0
    latencies = sorted(i.latencia_ms for i in itens)
    return {
        "perguntas": len(itens),
        "perguntas_avaliadas": len(graded),
        "recall@1": round(recall_at(1), 4),
        "recall@3": round(recall_at(3), 4),
        "recall@k": round(recall_at(k), 4),
        "mrr": round(mrr, 4),
        "vazamentos": sum(len(i.vazamentos) for i in itens),
        "proibidos": sum(len(i.proibidos) for i in itens),
        "latencia_media_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        "latencia_p95_ms": latencies[int(0.95 * (len(latencies) - 1))] if latencies else 0.0,
    }


def find_regressions(current: dict[str, float], previous: dict[str, float] | None) -> list[str]:
    """Problemas que devem falhar a avaliação: vazamentos, proibidos e pioras de métrica."""
    problems = []
    if current["vazamentos"]:
        problems.append(f"{current['vazamentos']} trecho(s) vazado(s) para perfis sem acesso")
    if current["proibidos"]:
        problems.append(f"{current['proibidos']} trecho(s) proibido(s) recuperado(s)")
    if previous:
        for metric in _TRACKED:
            if current[metric] < previous[metric] - _TOLERANCE:
                problems.append(f"{metric} caiu de {previous[metric]:.3f} para {current[metric]:.3f}")
    return problems


def _label(hit: SearchHit) -> str:
    return f"{hit.doc_key} v{hit.versao} | {hit.caminho_secao}"
