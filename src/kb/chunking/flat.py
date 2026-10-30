"""Chunkers que ignoram a estrutura do documento, usados na comparação de estratégias (ADR-003).

O documento vira um texto corrido (título de cada seção seguido do conteúdo). Cada trecho
registra as seções que cobre, para que a avaliação, que espera seções, seja justa com eles.
Um trecho só "cobre" uma seção se tiver a maior parte dela (ver `_covers`): encostar em
algumas palavras de uma seção não conta.
O prefixo do texto indexado leva só o título do documento: estes chunkers não conhecem
o caminho da seção.
"""

import hashlib
import itertools
import math
import re
from typing import Any

from kb.chunking.base import Chunk, chunk_id
from kb.chunking.hierarchical import context_path, split_text
from kb.embedding import Embedder
from kb.parsing import parse_sections

Span = tuple[int, int, tuple[str, ...]]


class FixedSizeChunker:
    """Janelas de tamanho fixo, com sobreposição entre janelas vizinhas."""

    name = "fixo"

    def __init__(self, tamanho: int = 500, sobreposicao: int = 75) -> None:
        if not 0 <= sobreposicao < tamanho:
            raise ValueError("a sobreposição precisa ser menor que o tamanho")
        self.tamanho = tamanho
        self.sobreposicao = sobreposicao

    @property
    def params(self) -> dict[str, Any]:
        return {"tamanho": self.tamanho, "sobreposicao": self.sobreposicao}

    def chunk(self, doc_key: str, title: str, text: str) -> list[Chunk]:
        flat, spans = flatten(title, text)
        step = self.tamanho - self.sobreposicao
        pieces = []
        for start in range(0, max(len(flat), 1), step):
            pieces.append((start, start + self.tamanho))
            if start + self.tamanho >= len(flat):
                break
        return _build(doc_key, title, flat, spans, pieces)


class RecursiveChunker:
    """Divisão recursiva por parágrafo, linha, frase e palavra, sem olhar os títulos."""

    name = "recursivo"

    def __init__(self, max_chars: int = 1800) -> None:
        self.max_chars = max_chars

    @property
    def params(self) -> dict[str, Any]:
        return {"max_chars": self.max_chars}

    def chunk(self, doc_key: str, title: str, text: str) -> list[Chunk]:
        flat, spans = flatten(title, text)
        parts = split_text(flat, self.max_chars, separators=("\n\n", "\n", ". ", " "))
        return _build(doc_key, title, flat, spans, _locate(flat, parts))


class SemanticChunker:
    """Corta onde o assunto muda: entre frases vizinhas com embeddings pouco parecidos.

    As fronteiras são os pontos em que a distância entre frases vizinhas passa do percentil
    configurado. Depende do modelo de embedding: trocar o modelo muda os próprios trechos.
    """

    name = "semantico"

    def __init__(self, embedder: Embedder, percentil: int = 85, max_chars: int = 1800) -> None:
        self.embedder = embedder
        self.percentil = percentil
        self.max_chars = max_chars

    @property
    def params(self) -> dict[str, Any]:
        return {"percentil": self.percentil, "max_chars": self.max_chars}

    def chunk(self, doc_key: str, title: str, text: str) -> list[Chunk]:
        flat, spans = flatten(title, text)
        sentences = [(m.start(), m.end()) for m in re.finditer(r"[^\n]+?(?:[.!?](?=\s)|$)", flat, re.MULTILINE)]
        sentences = [(s, e) for s, e in sentences if flat[s:e].strip()]
        if len(sentences) < 2:
            return _build(doc_key, title, flat, spans, [(0, len(flat))])

        vectors = self.embedder.embed([flat[s:e] for s, e in sentences])
        distances = [1 - _cosine(a, b) for a, b in itertools.pairwise(vectors)]
        threshold = sorted(distances)[min(len(distances) - 1, int(len(distances) * self.percentil / 100))]

        pieces, start = [], sentences[0][0]
        for i, (sentence_start, sentence_end) in enumerate(sentences[1:]):
            too_long = sentence_end - start > self.max_chars
            if distances[i] >= threshold or too_long:
                pieces.append((start, sentences[i][1]))
                start = sentence_start
        pieces.append((start, sentences[-1][1]))
        return _build(doc_key, title, flat, spans, pieces)


def flatten(title: str, text: str) -> tuple[str, list[Span]]:
    """Texto corrido do documento e a posição de cada seção nele."""
    parts: list[str] = []
    spans: list[Span] = []
    position = 0
    for section in parse_sections(text):
        body = "\n\n".join(block.text for block in section.blocks)
        if section.path:
            body = f"{section.path[-1]}\n\n{body}"
        if parts:
            position += 2
        spans.append((position, position + len(body), context_path(title, section.path)))
        parts.append(body)
        position += len(body)
    return "\n\n".join(parts), spans


def _locate(flat: str, parts: list[str]) -> list[tuple[int, int]]:
    positions, cursor = [], 0
    for part in parts:
        start = flat.find(part, cursor)
        if start < 0:  # não deveria acontecer: os pedaços saem do próprio texto
            start = cursor
        positions.append((start, start + len(part)))
        cursor = start + len(part)
    return positions


def _build(
    doc_key: str, title: str, flat: str, spans: list[Span], pieces: list[tuple[int, int]]
) -> list[Chunk]:
    chunks: list[Chunk] = []
    seen: set[str] = set()
    for start, end in pieces:
        content = flat[start:end].strip()
        if not content:
            continue
        covered = [" > ".join(path) for s, e, path in spans if _covers(start, end, s, e)]
        label = " | ".join(dict.fromkeys(covered)) or title
        embed_text = f"[{title}]\n{content}"
        content_hash = hashlib.sha256(embed_text.encode("utf-8")).hexdigest()
        cid = chunk_id(doc_key, (label,), content_hash)
        if cid in seen:
            continue
        seen.add(cid)
        chunks.append(
            Chunk(
                id=cid,
                section_path=(label,),
                ordinal=len(chunks),
                kind="texto",
                content=content,
                embed_text=embed_text,
                content_hash=content_hash,
            )
        )
    return chunks


def _covers(start: int, end: int, section_start: int, section_end: int) -> bool:
    """O trecho cobre a seção se tiver ao menos metade dela, ou 400 caracteres dela."""
    overlap = min(end, section_end) - max(start, section_start)
    return overlap > 0 and overlap >= min(0.5 * (section_end - section_start), 400)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0
