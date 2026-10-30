"""Partes comuns das buscas no banco: colunas retornadas e filtros de elegibilidade.

Os recuperadores (vetorial e textual) usam exatamente o mesmo filtro: só entram trechos de
versões vigentes na data de referência e de documentos que o perfil pode ver.
"""

from dataclasses import dataclass
from typing import Any

from psycopg import sql


@dataclass(frozen=True)
class Candidate:
    chunk_id: str
    doc_key: str
    titulo: str
    acesso: dict[str, Any]
    versao: int
    caminho_secao: str
    conteudo: str
    texto_embedding: str
    score: float


COLUMNS = sql.SQL(
    "c.id AS chunk_id, d.doc_key, d.titulo, d.acesso, v.versao, c.caminho_secao, c.conteudo,"
    " c.texto_embedding"
)

JOINS = sql.SQL(
    "JOIN version_chunks vc ON vc.chunk_id = c.id AND vc.index_config_id = %(config)s"
    " JOIN document_versions v ON v.id = vc.version_id"
    " JOIN documents d ON d.id = v.document_id"
)


def eligibility(access_filter: sql.Composable) -> sql.Composable:
    """Vigência na data `%(as_of)s` e filtro de acesso do perfil."""
    return sql.SQL(
        "v.valid_from <= %(as_of)s AND (v.valid_to IS NULL OR v.valid_to >= %(as_of)s) AND ({})"
    ).format(access_filter)
