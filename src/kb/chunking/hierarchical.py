"""Chunking hierárquico: um chunk por seção, com fallback recursivo para seções grandes.

- Cada seção do documento (definida pelos títulos) vira um chunk, se couber em `max_chars`.
- Seções maiores são divididas por blocos: parágrafos são agrupados até o limite.
- Tabelas que não cabem são divididas por linhas, repetindo o cabeçalho em cada parte.
- Parágrafos que não cabem são divididos recursivamente (linha, frase, palavra).
- Opcional (`agrupar_ate`): seções pequenas e inteiras, vizinhas, são agrupadas num só trecho
  até o tamanho-alvo. Um trecho continua sendo feito de seções completas, nunca cortadas.
"""

import hashlib
from dataclasses import dataclass
from typing import Any

from kb.chunking.base import Chunk, chunk_id
from kb.parsing import Block, parse_sections

_TEXT_SEPARATORS = ("\n", ". ", " ")


@dataclass
class _Unit:
    contexts: list[tuple[str, ...]]  # caminho completo (com o título) de cada seção da unidade
    raw_path: tuple[str, ...]  # caminho sem o título; base do ID quando a unidade tem uma seção
    kind: str
    content: str
    whole_section: bool  # a seção inteira coube neste pedaço


class HierarchicalChunker:
    name = "hierarquico"

    def __init__(self, max_chars: int = 1800, min_chars: int = 0, agrupar_ate: int = 0) -> None:
        """`min_chars`: o último pedaço de uma seção dividida, se menor que isso, é fundido ao
        anterior (que pode então passar de `max_chars` em até `min_chars`).
        `agrupar_ate`: tamanho-alvo para agrupar seções pequenas vizinhas (0 = não agrupa)."""
        if max_chars < 200:
            raise ValueError("max_chars deve ser pelo menos 200")
        self.max_chars = max_chars
        self.min_chars = min_chars
        self.agrupar_ate = agrupar_ate

    @property
    def params(self) -> dict[str, Any]:
        # Parâmetros opcionais só entram quando usados, para não mudar o ID de configurações
        # existentes.
        optional = {"min_chars": self.min_chars, "agrupar_ate": self.agrupar_ate}
        return {"max_chars": self.max_chars, **{k: v for k, v in optional.items() if v}}

    def chunk(self, doc_key: str, title: str, text: str) -> list[Chunk]:
        units: list[_Unit] = []
        for section in parse_sections(text):
            context = context_path(title, section.path)
            groups = self._pack(section.blocks)
            for kind, content in groups:
                units.append(_Unit([context], section.path, kind, content, len(groups) == 1))
        if self.agrupar_ate:
            units = self._group_sections(units)

        chunks: list[Chunk] = []
        seen: set[str] = set()
        for unit in units:
            if len(unit.contexts) == 1:
                [context] = unit.contexts
                prefix, section_path, id_path = context, context, unit.raw_path
            else:
                label = " | ".join(" > ".join(c) for c in unit.contexts)
                prefix, section_path, id_path = _common_prefix(unit.contexts), (label,), (label,)
            embed_text = f"[{' > '.join(prefix)}]\n{unit.content}"
            content_hash = hashlib.sha256(embed_text.encode("utf-8")).hexdigest()
            cid = chunk_id(doc_key, id_path, content_hash)
            if cid in seen:  # trecho idêntico repetido na mesma seção
                continue
            seen.add(cid)
            chunks.append(
                Chunk(
                    id=cid,
                    section_path=section_path,
                    ordinal=len(chunks),
                    kind=unit.kind,
                    content=unit.content,
                    embed_text=embed_text,
                    content_hash=content_hash,
                )
            )
        return chunks

    def _group_sections(self, units: list[_Unit]) -> list[_Unit]:
        """Agrupa unidades de seções inteiras, em ordem, até `agrupar_ate` caracteres."""
        result: list[_Unit] = []
        buffer: list[_Unit] = []

        def size(items: list[_Unit]) -> int:
            return sum(len(_with_heading(u)) + 2 for u in items)

        def flush() -> None:
            if len(buffer) == 1:
                result.append(buffer[0])
            elif buffer:
                kinds = {u.kind for u in buffer}
                result.append(
                    _Unit(
                        contexts=[u.contexts[0] for u in buffer],
                        raw_path=(),
                        kind=kinds.pop() if len(kinds) == 1 else "misto",
                        content="\n\n".join(_with_heading(u) for u in buffer),
                        whole_section=True,
                    )
                )
            buffer.clear()

        for unit in units:
            if not unit.whole_section or len(unit.content) >= self.agrupar_ate:
                flush()
                result.append(unit)
            elif buffer and size([*buffer, unit]) > self.agrupar_ate:
                flush()
                buffer.append(unit)
            else:
                buffer.append(unit)
        flush()
        return result

    def _pack(self, blocks: tuple[Block, ...]) -> list[tuple[str, str]]:
        units: list[tuple[str, str]] = []
        for block in blocks:
            if len(block.text) <= self.max_chars:
                units.append((block.kind, block.text))
            elif block.kind == "tabela":
                units.extend(("tabela", part) for part in split_table(block.text, self.max_chars))
            else:
                units.extend(("texto", part) for part in split_text(block.text, self.max_chars))

        groups: list[tuple[str, str]] = []
        texts: list[str] = []
        kinds: set[str] = set()
        size = 0
        for kind, text in units:
            added = len(text) + (2 if texts else 0)
            if texts and size + added > self.max_chars:
                groups.append((_kind_of(kinds), "\n\n".join(texts)))
                texts, kinds, size = [], set(), 0
                added = len(text)
            texts.append(text)
            kinds.add(kind)
            size += added
        if texts:
            groups.append((_kind_of(kinds), "\n\n".join(texts)))
        if self.min_chars and len(groups) > 1 and len(groups[-1][1]) < self.min_chars:
            (kind_a, text_a), (kind_b, text_b) = groups[-2], groups[-1]
            groups[-2:] = [(kind_a if kind_a == kind_b else "misto", f"{text_a}\n\n{text_b}")]
        return groups


def split_table(table: str, max_chars: int) -> list[str]:
    lines = table.split("\n")
    has_separator = len(lines) >= 2 and set(lines[1].replace("|", "").strip()) <= set("-: ")
    header = lines[:2] if has_separator else lines[:1]
    rows = lines[len(header):]
    if not rows:
        return [table]

    parts: list[str] = []
    current: list[str] = []
    header_size = sum(len(line) + 1 for line in header)
    size = header_size
    for row in rows:
        if current and size + len(row) + 1 > max_chars:
            parts.append("\n".join(header + current))
            current, size = [], header_size
        current.append(row)
        size += len(row) + 1
    parts.append("\n".join(header + current))
    return parts


def split_text(text: str, max_chars: int, separators: tuple[str, ...] = _TEXT_SEPARATORS) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    if not separators:
        return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]

    separator, remaining = separators[0], separators[1:]
    pieces = text.split(separator)
    # Mantém o separador no fim de cada pedaço para não perder pontuação.
    pieces = [piece + separator for piece in pieces[:-1]] + [pieces[-1]]

    parts: list[str] = []
    buffer = ""
    for piece in pieces:
        if len(buffer) + len(piece) <= max_chars:
            buffer += piece
            continue
        if buffer:
            parts.append(buffer)
            buffer = ""
        if len(piece) > max_chars:
            parts.extend(split_text(piece, max_chars, remaining))
        else:
            buffer = piece
    if buffer:
        parts.append(buffer)
    return [part.strip() for part in parts if part.strip()]


def _with_heading(unit: _Unit) -> str:
    """Conteúdo da seção precedido do seu título (num trecho agrupado, o título não está no prefixo)."""
    context = unit.contexts[0]
    return f"{context[-1]}\n\n{unit.content}" if len(context) > 1 else unit.content


def _common_prefix(paths: list[tuple[str, ...]]) -> tuple[str, ...]:
    prefix: list[str] = []
    for parts in zip(*paths):
        if len(set(parts)) != 1:
            break
        prefix.append(parts[0])
    return tuple(prefix)


def context_path(title: str, path: tuple[str, ...]) -> tuple[str, ...]:
    if path and path[0] == title:
        return path
    return (title, *path)


def _kind_of(kinds: set[str]) -> str:
    return next(iter(kinds)) if len(kinds) == 1 else "misto"
