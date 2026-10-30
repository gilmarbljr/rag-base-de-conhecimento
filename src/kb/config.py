from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração lida das variáveis de ambiente com prefixo KB_ (ou do arquivo .env)."""

    model_config = SettingsConfigDict(env_prefix="KB_", env_file=".env", extra="ignore")

    database_url: str = "postgresql://kb:kb@localhost:5432/kb"
    ollama_url: str = "http://localhost:11434"
    embedding_model: str = "bge-m3"
    generation_model: str = "qwen3:4b"
    chunk_max_chars: int = 1800
    reranker_url: str = "http://localhost:8081"
    # Pipeline de consulta padrão (arquivo em pipelines_dir) e caminhos relativos ao projeto.
    pipeline: str = "hibrida-rerank"
    pipelines_dir: Path = Path("configs/pipelines")
    saude_path: Path = Path("configs/saude.yaml")
    perfis_path: Path = Path("data/perfis.yaml")
    golden_set_dir: Path = Path("data/golden_set")
    reports_dir: Path = Path("reports")
    # Relógio simulado: uma data (AAAA-MM-DD), "release" (a data de referência da release para
    # a qual a fonte aponta) ou vazio (data real). Permite demonstrar vigência e viradas de mês.
    data_simulada: date | Literal["release"] | None = None

    @field_validator("data_simulada", mode="before")
    @classmethod
    def _vazio_e_none(cls, value: object) -> object:
        return None if value == "" else value

    def hoje(self, release_date: date | None = None) -> date:
        if self.data_simulada == "release":
            return release_date or date.today()
        return self.data_simulada or date.today()
