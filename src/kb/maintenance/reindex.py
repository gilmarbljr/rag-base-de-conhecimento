"""Reindexação blue/green: constrói um índice novo ao lado do ativo, avalia e só então troca.

1. preparar: registra a nova configuração (chunker + modelo) e cria a sua tabela de vetores;
2. construir: indexa TODAS as versões de todos os documentos (inclusive as antigas, para que
   a consulta no tempo continue funcionando), reaproveitando trechos e embeddings já feitos;
3. avaliar: roda o golden set no índice novo e no ativo, com a mesma pipeline;
4. decidir: promove só se o novo não for pior em recall@1 e MRR, sem vazamentos;
5. promover: troca o índice ativo numa transação. O anterior fica intacto para rollback.

`construir` é idempotente: rodar de novo só indexa o que falta. Por isso ele é chamado
outra vez logo antes da promoção, para incluir versões sincronizadas durante a construção.
"""

import time
from dataclasses import dataclass, field

import psycopg
from psycopg import sql

from kb.chunking import Chunker
from kb.embedding import Embedder
from kb.errors import KbError
from kb.store import catalog, vectors
from kb.store.catalog import IndexConfig

# Uma piora menor que isto é ruído de arredondamento.
_TOLERANCE = 1e-6


@dataclass
class BuildResult:
    index_config_id: str
    versoes: int = 0
    trechos: int = 0
    novos_embeddings: int = 0
    segundos: float = 0.0


@dataclass
class Decision:
    promover: bool
    motivos: list[str] = field(default_factory=list)


def prepare(
    conn: psycopg.Connection, *, chunker: Chunker, embedder: Embedder
) -> tuple[IndexConfig, bool]:
    """Registra a configuração (sem ativar, se já houver uma ativa)."""
    dimensao = len(embedder.embed(["teste de dimensão"])[0])
    return catalog.ensure_index_config(conn, chunker.name, chunker.params, embedder.model, dimensao)


def build(
    conn: psycopg.Connection,
    config: IndexConfig,
    *,
    chunker: Chunker,
    embedder: Embedder,
    doc_keys: list[str] | None = None,
) -> BuildResult:
    """Indexa as versões dos documentos (padrão: todos) na configuração informada."""
    if embedder.model != config.embedding_model:
        raise KbError(
            f"O índice {config.id} usa o modelo '{config.embedding_model}', "
            f"mas o embedder é '{embedder.model}'."
        )
    started = time.perf_counter()
    result = BuildResult(config.id)
    for version in catalog.all_versions(conn, doc_keys):
        chunks = chunker.chunk(version["doc_key"], version["titulo"], version["conteudo"])
        pending_ids = set(vectors.missing_ids(conn, config.tabela, [c.id for c in chunks]))
        pending = [c for c in chunks if c.id in pending_ids]
        embeddings = embedder.embed_documents([c.embed_text for c in pending]) if pending else []
        with conn.transaction():
            catalog.upsert_chunks(conn, version["document_id"], chunks)
            catalog.link_version_chunks(conn, config.id, version["version_id"], chunks)
            vectors.insert(conn, config.tabela, list(zip([c.id for c in pending], embeddings)))
        result.versoes += 1
        result.trechos += len(chunks)
        result.novos_embeddings += len(pending)
    result.segundos = round(time.perf_counter() - started, 1)
    return result


def reset(conn: psycopg.Connection, config: IndexConfig) -> None:
    """Esvazia um índice candidato (vínculos e vetores) para reconstruí-lo do zero.

    Necessário quando o código do chunker mudou sem mudar os parâmetros: os trechos novos
    teriam outros IDs e se somariam aos antigos. O índice ativo nunca é esvaziado.
    """
    if config.ativo:
        raise KbError(f"O índice {config.id} está ativo e não pode ser esvaziado.")
    with conn.transaction():
        conn.execute("DELETE FROM version_chunks WHERE index_config_id = %s", (config.id,))
        conn.execute(sql.SQL("TRUNCATE {}").format(sql.Identifier(config.tabela)))


def decide(candidate: dict, current: dict | None) -> Decision:
    """Promove se o candidato não vaza e não piora recall@1 nem MRR em relação ao ativo."""
    motivos = []
    if candidate["vazamentos"] or candidate["proibidos"]:
        motivos.append("o índice novo tem vazamentos ou documentos proibidos na avaliação")
    if current:
        for metric in ("recall@1", "mrr"):
            if candidate[metric] < current[metric] - _TOLERANCE:
                motivos.append(f"{metric} cairia de {current[metric]:.3f} para {candidate[metric]:.3f}")
    return Decision(promover=not motivos, motivos=motivos)


def promote(conn: psycopg.Connection, config_id: str) -> None:
    """Troca o índice ativo numa transação. Recusa um índice incompleto."""
    coverage = catalog.index_coverage(conn, catalog.get_index_config(conn, config_id))
    if coverage["versoes_indexadas"] < coverage["versoes_total"] or coverage["trechos_sem_embedding"]:
        raise KbError(
            f"O índice {config_id} está incompleto: {coverage['versoes_indexadas']} de "
            f"{coverage['versoes_total']} versões indexadas, {coverage['trechos_sem_embedding']} "
            "trecho(s) sem embedding. Rode a construção (kb reindex) antes de ativá-lo."
        )
    with conn.transaction():
        # Duas instruções: o índice único parcial não permite dois ativos nem por um instante.
        conn.execute("UPDATE index_configs SET ativo = false WHERE ativo")
        conn.execute("UPDATE index_configs SET ativo = true WHERE id = %s", (config_id,))
