"""Leitura de uma pasta de fontes descrita por um `manifest.yaml`.

O manifest é o registro das fontes: identifica cada documento e guarda os metadados
que não estão no arquivo (vigência, acesso, responsável). Assim o mesmo registro serve
para qualquer formato de arquivo.
"""

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from kb.errors import KbError
from kb.parsing import normalize_text

SUPPORTED_SUFFIXES = {".md"}


class Vigencia(BaseModel):
    inicio: date
    fim: date | None = None  # inclusivo; None = sem data de término

    @model_validator(mode="after")
    def _fim_depois_do_inicio(self) -> "Vigencia":
        if self.fim is not None and self.fim < self.inicio:
            raise ValueError("vigencia.fim é anterior a vigencia.inicio")
        return self


class ManifestEntry(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    titulo: str
    tipo: str
    arquivo: str
    formato_final: str
    escopo: str
    acesso: dict[str, str | None] = Field(default_factory=dict)
    vigencia: Vigencia
    revisar_ate: date | None = None
    responsavel: str | None = None


class Manifest(BaseModel):
    release: int
    data_referencia: date | None = None
    documentos: list[ManifestEntry]


@dataclass(frozen=True)
class SourceDocument:
    entry: ManifestEntry
    content: str
    # Hash do título + conteúdo: tudo o que entra no texto indexado. Mudou, é uma nova versão.
    content_hash: str

    @property
    def doc_key(self) -> str:
        return self.entry.id

    @property
    def titulo(self) -> str:
        return self.entry.titulo


def load_manifest(folder: Path) -> Manifest:
    manifest_path = folder / "manifest.yaml"
    if not manifest_path.exists():
        raise KbError(f"Pasta sem manifest.yaml: {folder}")
    try:
        return Manifest.model_validate(yaml.safe_load(manifest_path.read_text(encoding="utf-8")))
    except ValidationError as exc:
        raise KbError(f"manifest.yaml inválido em {folder}:\n{exc}") from exc


def load_folder(folder: Path) -> tuple[Manifest, list[SourceDocument]]:
    manifest = load_manifest(folder)

    ids = [entry.id for entry in manifest.documentos]
    duplicated = sorted({doc_id for doc_id in ids if ids.count(doc_id) > 1})
    if duplicated:
        raise KbError(f"IDs duplicados no manifest: {', '.join(duplicated)}")

    documents = []
    for entry in manifest.documentos:
        path = folder / entry.arquivo
        if not path.exists():
            raise KbError(f"Arquivo do documento '{entry.id}' não encontrado: {path}")
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise KbError(f"Formato ainda não suportado: {path.name}")
        content = normalize_text(path.read_text(encoding="utf-8"))
        content_hash = hashlib.sha256(f"{entry.titulo}\n{content}".encode()).hexdigest()
        documents.append(SourceDocument(entry, content, content_hash))
    return manifest, documents
