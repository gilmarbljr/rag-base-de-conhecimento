from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

from kb.errors import KbError


class Expected(BaseModel):
    documento: str
    secao: str | None = None  # trecho do caminho da seção
    versao: int | None = None

    def matches(self, doc_key: str, caminho_secao: str, versao: int) -> bool:
        return (
            doc_key == self.documento
            and (self.secao is None or self.secao in caminho_secao)
            and (self.versao is None or versao == self.versao)
        )


class GoldenItem(BaseModel):
    id: str
    tipo: str
    pergunta: str
    perfil: str | None = None
    as_of: date | None = None
    esperado: list[Expected] = Field(default_factory=list)
    proibido: list[str] = Field(default_factory=list)
    resposta_referencia: str | None = None


class GoldenSet(BaseModel):
    release: int
    data_referencia: date
    perguntas: list[GoldenItem]


def load_golden_set(path: Path) -> GoldenSet:
    if not path.exists():
        raise KbError(f"Golden set não encontrado: {path}")
    try:
        golden = GoldenSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except ValidationError as exc:
        raise KbError(f"Golden set inválido em {path}:\n{exc}") from exc
    ids = [item.id for item in golden.perguntas]
    if len(ids) != len(set(ids)):
        raise KbError(f"IDs de pergunta duplicados em {path}")
    return golden
