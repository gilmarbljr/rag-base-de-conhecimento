"""Normalização de Markdown e divisão em seções pela hierarquia de títulos."""

import re
from dataclasses import dataclass
from typing import Literal

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^(```|~~~)")


@dataclass(frozen=True)
class Block:
    """Parágrafo ou tabela dentro de uma seção."""

    kind: Literal["texto", "tabela"]
    text: str


@dataclass(frozen=True)
class Section:
    """Conteúdo sob um título. `path` são os títulos do nível mais alto até o atual."""

    path: tuple[str, ...]
    blocks: tuple[Block, ...]


def normalize_text(text: str) -> str:
    """Remove BOM, padroniza quebras de linha e espaços finais.

    O hash de conteúdo é calculado sobre o texto normalizado, para que diferenças
    invisíveis (CRLF, espaços no fim da linha) não gerem uma versão nova.
    """
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip() + "\n"


def parse_sections(text: str) -> list[Section]:
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    buffer: list[str] = []
    in_fence = False

    def flush() -> None:
        blocks = _split_blocks(buffer)
        if blocks:
            sections.append(Section(tuple(title for _, title in stack), tuple(blocks)))
        buffer.clear()

    for line in text.split("\n"):
        if _FENCE.match(line.strip()):
            in_fence = not in_fence
            buffer.append(line)
            continue
        match = None if in_fence else _HEADING.match(line)
        if match:
            flush()
            level = len(match.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, match.group(2).strip()))
        else:
            buffer.append(line)
    flush()
    return sections


def _split_blocks(lines: list[str]) -> list[Block]:
    """Separa as linhas de uma seção em parágrafos (por linha em branco) e tabelas."""
    blocks: list[Block] = []
    current: list[str] = []
    kind: Literal["texto", "tabela"] | None = None
    in_fence = False

    def close() -> None:
        nonlocal current, kind
        content = "\n".join(current).strip()
        if content and kind is not None:
            blocks.append(Block(kind, content))
        current, kind = [], None

    for line in lines:
        stripped = line.strip()
        if _FENCE.match(stripped):
            in_fence = not in_fence
        if not in_fence and not stripped:
            close()
            continue
        line_kind: Literal["texto", "tabela"] = (
            "tabela" if not in_fence and stripped.startswith("|") else "texto"
        )
        if kind is not None and line_kind != kind:
            close()
        kind = line_kind
        current.append(line)
    close()
    return blocks
