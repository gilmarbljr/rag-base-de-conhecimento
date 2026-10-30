"""Experimento de chunking (ADR-003): compara estratégias no mesmo corpus e no mesmo golden set.

Para cada estratégia:
- constrói um índice candidato (sem ativar) com todas as versões de todos os documentos;
- avalia com o golden set das três releases, nas pipelines `hibrida` e `hibrida-rerank`;
- mede o custo de manutenção: quantos trechos novos (embeddings a calcular) cada release
  exige, comparando os IDs determinísticos dos trechos entre releases consecutivas.

Pré-requisitos: a base sincronizada até a release 3 e os serviços no ar (Ollama e reranker).

Uso:   uv run python scripts/experimento_chunking.py
Saída: docs/experimentos/chunking.md e docs/experimentos/chunking.json
"""

import json
import statistics
import time
from pathlib import Path

from kb import components
from kb.config import Settings
from kb.maintenance import blue_green, reindex
from kb.sources import load_folder
from kb.store import catalog
from kb.store.db import connect

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "experimentos"
MODEL = "bge-m3"
PEQUENO = 120

ESTRATEGIAS = [
    ("Tamanho fixo (500, sobreposição 75)", "fixo", {"tamanho": 500, "sobreposicao": 75}),
    ("Recursivo (1.800)", "recursivo", {"max_chars": 1800}),
    ("Semântico (percentil 85, máx. 1.800)", "semantico", {"percentil": 85, "max_chars": 1800}),
    ("Hierárquico (1.800)", "hierarquico", {"max_chars": 1800}),
    ("Hierárquico + fusão de fragmentos (mín. 200)", "hierarquico", {"max_chars": 1800, "min_chars": 200}),
    ("Hierárquico + agrupamento de seções (até 600)", "hierarquico", {"max_chars": 1800, "agrupar_ate": 600}),
    ("Hierárquico + agrupamento de seções (até 1.200)", "hierarquico", {"max_chars": 1800, "agrupar_ate": 1200}),
]


def churn(chunker) -> dict[str, dict[str, int]]:
    """Trechos novos que cada release exige em relação à anterior (IDs determinísticos)."""
    ids_by_release = {}
    for release in (1, 2, 3):
        _, documents = load_folder(ROOT / "data" / "corpus" / f"release_{release}")
        ids_by_release[release] = {
            c.id for doc in documents for c in chunker.chunk(doc.doc_key, doc.titulo, doc.content)
        }
    return {
        f"release_{r}": {"novos": len(ids_by_release[r] - ids_by_release[r - 1]), "total": len(ids_by_release[r])}
        for r in (2, 3)
    }


def main() -> None:
    settings = Settings()
    results = []
    with connect(settings.database_url) as conn:
        hoje = components.hoje(settings, conn)
        for label, name, params in ESTRATEGIAS:
            print(f"\n=== {label}")
            chunker, embedder = blue_green.candidate_components(
                settings, chunker_name=name, chunker_params=params, model=MODEL
            )
            config, _ = reindex.prepare(conn, chunker=chunker, embedder=embedder)
            if not config.ativo:
                reindex.reset(conn, config)  # reconstrói do zero, com o código atual do chunker
            started = time.perf_counter()
            build = reindex.build(conn, config, chunker=chunker, embedder=embedder)
            print(f"índice {config.id}: {build.trechos} trechos, {build.novos_embeddings} embeddings novos "
                  f"({time.perf_counter() - started:.0f} s)")

            sizes = [len(r["conteudo"]) for r in catalog.current_chunks(conn, config.id, hoje)]
            evaluation = {
                pipeline: blue_green.evaluate_index(conn, settings, config, pipeline, fonte=components.FONTE_PADRAO)
                for pipeline in ("hibrida", "hibrida-rerank")
            }
            rotatividade = churn(chunker)
            result = {
                "estrategia": label,
                "index_config_id": config.id,
                "trechos_vigentes": len(sizes),
                "tamanho_mediano": int(statistics.median(sizes)),
                "trechos_pequenos": sum(1 for s in sizes if s < PEQUENO),
                "embeddings_totais": catalog.index_coverage(conn, config)["embeddings"],
                "avaliacao": evaluation,
                "rotatividade": rotatividade,
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            results.append(result)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "chunking.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTPUT / "chunking.md").write_text(to_markdown(results, hoje), encoding="utf-8")
    print(f"\nResultados em {OUTPUT.relative_to(ROOT)}")


def to_markdown(results: list[dict], hoje) -> str:
    def pct(churn_release: dict) -> str:
        return f"{churn_release['novos']} de {churn_release['total']} ({churn_release['novos'] / churn_release['total']:.0%})"

    lines = [
        "# Experimento de chunking",
        "",
        (
            "Gerado por `scripts/experimento_chunking.py`. Mesmo corpus (3 releases), mesmo modelo "
            f"de embedding ({MODEL}) e mesmo golden set (60 perguntas, 3 releases). Trechos "
            f"vigentes em {hoje}."
        ),
        "",
        "## Qualidade da busca",
        "",
        "| Estratégia | Recall@1 (híbrida) | MRR (híbrida) | Recall@1 (com rerank) | MRR (com rerank) |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        h, rr = r["avaliacao"]["hibrida"], r["avaliacao"]["hibrida-rerank"]
        lines.append(
            f"| {r['estrategia']} | {h['recall@1']:.3f} | {h['mrr']:.3f} | {rr['recall@1']:.3f} | {rr['mrr']:.3f} |"
        )
    lines += [
        "",
        "## Tamanho e custo de manutenção",
        "",
        f"| Estratégia | Trechos vigentes | Tamanho mediano | Trechos < {PEQUENO} caracteres | Embeddings no índice | Trechos novos na release 2 | Trechos novos na release 3 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        rot = r["rotatividade"]
        lines.append(
            f"| {r['estrategia']} | {r['trechos_vigentes']} | {r['tamanho_mediano']} | {r['trechos_pequenos']} | "
            f"{r['embeddings_totais']} | {pct(rot['release_2'])} | {pct(rot['release_3'])} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
