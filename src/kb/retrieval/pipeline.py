"""Configuração de pipeline de consulta (`pipeline_config`), versionada em YAML.

Diferente da configuração de índice, trocar a pipeline não exige reindexar: é só escolher
outro arquivo. Toda consulta e toda avaliação registram qual pipeline foi usada.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from kb.errors import KbError


class RecuperacaoConfig(BaseModel):
    modo: Literal["vetorial", "textual", "hibrida"] = "hibrida"
    candidatos: int = Field(30, ge=1, description="Quantos trechos cada recuperador devolve.")
    rrf_k: int = Field(60, ge=1, description="Constante do Reciprocal Rank Fusion.")


class RerankConfig(BaseModel):
    ativo: bool = False
    candidatos: int = Field(15, ge=1, description="Quantos trechos fundidos vão para o reranker.")
    modelo: str | None = Field(
        None, description="Modelo que o serviço de rerank deve estar servindo (conferido na busca)."
    )


class PipelineConfig(BaseModel):
    nome: str
    versao: int
    descricao: str = ""
    recuperacao: RecuperacaoConfig = RecuperacaoConfig()
    rerank: RerankConfig = RerankConfig()

    @property
    def id(self) -> str:
        return f"{self.nome}@v{self.versao}"


def load_pipeline(directory: Path, nome: str) -> PipelineConfig:
    path = directory / f"{nome}.yaml"
    if not path.exists():
        disponiveis = ", ".join(list_pipelines(directory)) or "nenhuma"
        raise KbError(f"Pipeline '{nome}' não encontrada em {directory}. Disponíveis: {disponiveis}")
    try:
        config = PipelineConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except ValidationError as exc:
        raise KbError(f"Pipeline inválida em {path}:\n{exc}") from exc
    if config.nome != nome:
        raise KbError(f"{path.name} declara o nome '{config.nome}'; o nome deve ser igual ao do arquivo.")
    return config


def list_pipelines(directory: Path) -> list[str]:
    return sorted(path.stem for path in directory.glob("*.yaml"))
