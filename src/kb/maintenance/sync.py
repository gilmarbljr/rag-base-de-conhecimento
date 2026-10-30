"""Sincronização incremental de uma pasta de fontes com o catálogo.

Cada documento da pasta é classificado e tratado:

- novo         -> versão 1.
- nova_versao  -> o texto indexado (título + conteúdo) mudou: cria uma versão e encerra a anterior.
                  Chunks que não mudaram são reaproveitados, com o mesmo embedding.
- reativado    -> estava aposentado e voltou à fonte: cria uma versão.
- metadados    -> só mudaram metadados (acesso, vigência, revisão): atualizados no lugar.
- inalterado   -> nada a fazer.

Documentos do catálogo que sumiram da fonte são aposentados (tombstone): a última versão
é encerrada na véspera da data efetiva e o histórico continua consultável por `as_of`.

As etapas ficam separadas (planejar, sincronizar um documento, aposentar) para que o
orquestrador rode uma tarefa por documento. A CLI usa as mesmas funções em sequência.
Cada documento é gravado numa única transação; os embeddings são calculados antes dela.
"""

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

import psycopg

from kb.chunking import Chunker
from kb.embedding import Embedder
from kb.errors import KbError
from kb.sources import SourceDocument, load_folder
from kb.store import catalog, vectors
from kb.store.catalog import IndexConfig

TO_PROCESS = {"novo", "nova_versao", "reativado", "metadados"}


@dataclass(frozen=True)
class SyncPlan:
    fonte: str
    pasta: str
    data_efetiva: date
    acoes: dict[str, str]  # doc_key -> classificação
    aposentar: list[str] = field(default_factory=list)

    @property
    def pendentes(self) -> list[str]:
        return [doc_key for doc_key, acao in self.acoes.items() if acao in TO_PROCESS]

    def to_dict(self) -> dict:
        """Formato serializável (JSON), usado para passar o plano entre tarefas do Airflow."""
        return {**asdict(self), "data_efetiva": self.data_efetiva.isoformat(), "pendentes": self.pendentes}


@dataclass(frozen=True)
class SyncResult:
    doc_key: str
    status: str
    versao: int | None
    chunks: int = 0
    reaproveitados: int = 0
    novos_embeddings: int = 0
    removidos: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def plan_sync(
    conn: psycopg.Connection,
    folder: Path,
    *,
    source_name: str,
    data_efetiva: date | None = None,
    hoje: date,
) -> SyncPlan:
    """Registra a fonte e classifica cada documento, sem alterar o conteúdo do catálogo.

    A data efetiva é a informada, ou a data de referência do manifest, ou `hoje`.
    """
    manifest, documents = load_folder(folder)
    efetiva = data_efetiva or manifest.data_referencia or hoje
    source_id = catalog.ensure_source(conn, source_name, folder.as_posix())
    acoes = {doc.doc_key: _classify(conn, doc) for doc in documents}
    aposentar = [
        doc_key
        for doc_key in catalog.active_documents_of_source(conn, source_id)
        if doc_key not in acoes
    ]
    return SyncPlan(source_name, folder.as_posix(), efetiva, acoes, aposentar)


def sync_document(
    conn: psycopg.Connection,
    folder: Path,
    doc_key: str,
    *,
    source_name: str,
    data_efetiva: date,
    chunker: Chunker,
    embedder: Embedder,
    config: IndexConfig,
) -> SyncResult:
    _check_model(embedder, config)
    _, documents = load_folder(folder)
    doc = next((d for d in documents if d.doc_key == doc_key), None)
    if doc is None:
        raise KbError(f"Documento '{doc_key}' não está em {folder}")
    source_id = catalog.get_source(conn, source_name)["id"]
    return _sync_one(conn, doc, source_id, data_efetiva, chunker, embedder, config)


def retire_documents(
    conn: psycopg.Connection, doc_keys: list[str], data_efetiva: date, config: IndexConfig
) -> list[SyncResult]:
    results = []
    for doc_key in doc_keys:
        existing = catalog.get_document(conn, doc_key)
        if existing is None or existing["status"] == "retired":
            continue
        latest = catalog.latest_version(conn, existing["id"])
        end = max(latest["valid_from"], data_efetiva - timedelta(days=1))
        with conn.transaction():
            if latest["valid_to"] is None or latest["valid_to"] > end:
                catalog.close_version(conn, latest["id"], end)
            catalog.retire_document(conn, existing["id"], data_efetiva)
        removed = len(catalog.version_chunk_ids(conn, config.id, latest["id"]))
        results.append(SyncResult(doc_key, "aposentado", latest["versao"], removidos=removed))
    return results


def sync_folder(
    conn: psycopg.Connection,
    folder: Path,
    *,
    source_name: str,
    chunker: Chunker,
    embedder: Embedder,
    config: IndexConfig,
    hoje: date,
    data_efetiva: date | None = None,
) -> tuple[SyncPlan, list[SyncResult]]:
    """Sincronização completa em sequência: planejar, processar cada documento, aposentar."""
    _check_model(embedder, config)
    plan = plan_sync(conn, folder, source_name=source_name, data_efetiva=data_efetiva, hoje=hoje)
    _, documents = load_folder(folder)
    source_id = catalog.get_source(conn, source_name)["id"]
    results = [
        _sync_one(conn, doc, source_id, plan.data_efetiva, chunker, embedder, config)
        for doc in documents
    ]
    results += retire_documents(conn, plan.aposentar, plan.data_efetiva, config)
    return plan, results


def _sync_one(
    conn: psycopg.Connection,
    doc: SourceDocument,
    source_id: int,
    data_efetiva: date,
    chunker: Chunker,
    embedder: Embedder,
    config: IndexConfig,
) -> SyncResult:
    status = _classify(conn, doc)
    existing = catalog.get_document(conn, doc.doc_key)
    latest = catalog.latest_version(conn, existing["id"]) if existing else None

    if status == "inalterado":
        return SyncResult(doc.doc_key, status, latest["versao"])

    if status == "metadados":
        entry = doc.entry
        with conn.transaction():
            catalog.update_document(conn, existing["id"], source_id, doc)
            catalog.update_version_dates(
                conn,
                latest["id"],
                valid_from=_expected_valid_from(conn, existing["id"], latest, doc),
                valid_to=entry.vigencia.fim,
                review_by=entry.revisar_ate,
            )
        return SyncResult(doc.doc_key, status, latest["versao"])

    previous_ids = catalog.version_chunk_ids(conn, config.id, latest["id"]) if latest else set()
    chunks = chunker.chunk(doc.doc_key, doc.titulo, doc.content)
    pending_ids = set(vectors.missing_ids(conn, config.tabela, [c.id for c in chunks]))
    pending = [c for c in chunks if c.id in pending_ids]
    embeddings = embedder.embed_documents([c.embed_text for c in pending]) if pending else []

    valid_from = _start_of_new_version(doc.entry.vigencia.inicio, latest, data_efetiva)
    if doc.entry.vigencia.fim is not None and doc.entry.vigencia.fim < valid_from:
        raise KbError(
            f"'{doc.doc_key}': a nova versão começaria em {valid_from.isoformat()}, "
            f"depois do fim da vigência declarada ({doc.entry.vigencia.fim.isoformat()})."
        )
    versao = latest["versao"] + 1 if latest else 1

    with conn.transaction():
        if existing is None:
            document_id = catalog.insert_document(conn, source_id, doc)
        else:
            document_id = existing["id"]
            catalog.update_document(conn, document_id, source_id, doc)
        if latest and (latest["valid_to"] is None or latest["valid_to"] >= valid_from):
            catalog.close_version(conn, latest["id"], valid_from - timedelta(days=1))
        version_id = catalog.insert_version(conn, document_id, versao, doc, valid_from)
        catalog.upsert_chunks(conn, document_id, chunks)
        catalog.link_version_chunks(conn, config.id, version_id, chunks)
        vectors.insert(conn, config.tabela, list(zip([c.id for c in pending], embeddings)))

    current_ids = {c.id for c in chunks}
    return SyncResult(
        doc.doc_key,
        status,
        versao,
        chunks=len(chunks),
        reaproveitados=len(current_ids & previous_ids),
        novos_embeddings=len(pending),
        removidos=len(previous_ids - current_ids),
    )


def _classify(conn: psycopg.Connection, doc: SourceDocument) -> str:
    existing = catalog.get_document(conn, doc.doc_key)
    if existing is None:
        return "novo"
    latest = catalog.latest_version(conn, existing["id"])
    if existing["status"] == "retired" or latest is None:
        return "reativado"
    if latest["content_hash"] != doc.content_hash:
        return "nova_versao"
    fields = catalog.document_fields(doc)
    if any(existing[name] != value for name, value in fields.items()):
        return "metadados"
    entry = doc.entry
    declared = (
        _expected_valid_from(conn, existing["id"], latest, doc),
        entry.vigencia.fim,
        entry.revisar_ate,
    )
    if declared != (latest["valid_from"], latest["valid_to"], latest["review_by"]):
        return "metadados"
    return "inalterado"


def _start_of_new_version(inicio: date, latest: dict | None, data_efetiva: date) -> date:
    """Uma nova versão começa na vigência declarada, se ela for posterior à versão atual.

    Se o documento mudou sem declarar uma nova vigência, a nova versão vale a partir da
    data efetiva da sincronização (e sempre depois do início da versão atual).
    """
    if latest is None or inicio > latest["valid_from"]:
        return inicio
    return max(data_efetiva, latest["valid_from"] + timedelta(days=1))


def _expected_valid_from(
    conn: psycopg.Connection, document_id: int, latest: dict, doc: SourceDocument
) -> date:
    """Início esperado da versão atual, considerando a regra de `_start_of_new_version`."""
    inicio = doc.entry.vigencia.inicio
    if latest["versao"] == 1:
        return inicio
    previous = conn.execute(
        "SELECT valid_from FROM document_versions WHERE document_id = %s AND versao = %s",
        (document_id, latest["versao"] - 1),
    ).fetchone()
    return inicio if inicio > previous["valid_from"] else latest["valid_from"]


def _check_model(embedder: Embedder, config: IndexConfig) -> None:
    if embedder.model != config.embedding_model:
        raise KbError(
            f"A configuração ativa usa o modelo '{config.embedding_model}', "
            f"mas o embedder configurado é '{embedder.model}'."
        )
