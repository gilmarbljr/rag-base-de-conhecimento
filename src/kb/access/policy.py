"""Controle de acesso por metadados.

A mesma regra existe em duas formas:
- `sql_filter`: cláusula aplicada na busca, antes de qualquer trecho chegar ao LLM;
- `allows`: verificação em Python, usada pela avaliação para conferir se a busca vazou algo.
"""

from pathlib import Path
from typing import Any

import yaml
from psycopg import sql
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from kb.errors import KbError

_UNKNOWN_LEVEL = 999  # nível desconhecido no documento: ninguém vê


class LevelRule(BaseModel):
    campo: str
    ordem: list[str] = Field(min_length=1)


class AccessProfile(BaseModel):
    nome: str = ""
    descricao: str = ""
    nivel: str
    restricoes: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("restricoes")
    @classmethod
    def _sem_listas_vazias(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        empty = [campo for campo, valores in value.items() if not valores]
        if empty:
            raise ValueError(f"restrições sem valores permitidos: {', '.join(empty)}")
        return value


class AccessPolicy(BaseModel):
    nivel: LevelRule
    perfis: dict[str, AccessProfile]

    @model_validator(mode="after")
    def _niveis_conhecidos(self) -> "AccessPolicy":
        for nome, perfil in self.perfis.items():
            perfil.nome = nome
            if perfil.nivel not in self.nivel.ordem:
                raise ValueError(f"perfil '{nome}' usa o nível desconhecido '{perfil.nivel}'")
        return self

    def profile(self, nome: str) -> AccessProfile:
        if nome not in self.perfis:
            raise KbError(f"Perfil '{nome}' não existe. Perfis: {', '.join(self.perfis)}")
        return self.perfis[nome]

    def allows(self, profile: AccessProfile | None, acesso: dict[str, Any]) -> bool:
        if profile is None:
            return True
        ordem = self.nivel.ordem
        doc_level = acesso.get(self.nivel.campo) or ordem[0]
        doc_rank = ordem.index(doc_level) + 1 if doc_level in ordem else _UNKNOWN_LEVEL
        if doc_rank > ordem.index(profile.nivel) + 1:
            return False
        return all(
            acesso.get(campo) is None or acesso.get(campo) in valores
            for campo, valores in profile.restricoes.items()
        )

    @staticmethod
    def no_filter() -> sql.Composable:
        return sql.SQL("TRUE")

    def sql_filter(self, profile: AccessProfile | None, alias: str = "d") -> sql.Composable:
        """Cláusula equivalente a `allows`, sobre a coluna `acesso` (jsonb) de `documents`."""
        if profile is None:
            return self.no_filter()
        acesso = sql.SQL("{}.acesso").format(sql.Identifier(alias))
        ordem = self.nivel.ordem
        clauses = [
            sql.SQL(
                "coalesce(array_position({ordem}::text[], coalesce({acesso}->>{campo}, {primeiro})),"
                " {desconhecido}) <= {nivel}"
            ).format(
                ordem=sql.Literal(ordem),
                acesso=acesso,
                campo=sql.Literal(self.nivel.campo),
                primeiro=sql.Literal(ordem[0]),
                desconhecido=sql.Literal(_UNKNOWN_LEVEL),
                nivel=sql.Literal(ordem.index(profile.nivel) + 1),
            )
        ]
        for campo, valores in profile.restricoes.items():
            clauses.append(
                sql.SQL("({acesso}->>{campo} IS NULL OR {acesso}->>{campo} = ANY({valores}::text[]))").format(
                    acesso=acesso, campo=sql.Literal(campo), valores=sql.Literal(valores)
                )
            )
        return sql.SQL(" AND ").join(clauses)


def load_policy(path: Path) -> AccessPolicy:
    if not path.exists():
        raise KbError(f"Política de acesso não encontrada: {path}")
    try:
        return AccessPolicy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except ValidationError as exc:
        raise KbError(f"Política de acesso inválida em {path}:\n{exc}") from exc
