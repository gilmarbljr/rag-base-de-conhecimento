"""Detecção de possíveis conflitos entre documentos vigentes.

Duas etapas:
1. Candidatos (heurística barata): frases de regra com polaridade oposta ("pode ser
   combinado" x "não acumula") em documentos diferentes, com termos em comum.
2. Confirmação (juiz LLM): o modelo decide se as duas regras se contradizem para a mesma
   situação. A heurística sozinha gera muitos falsos positivos: regras sobre sujeitos
   diferentes ("venda direta não acumula com PCD" x "PCD acumula com a campanha") usam as
   mesmas palavras, e nem a similaridade de embeddings separa esses casos.
"""

import itertools
import re
from dataclasses import asdict, dataclass

from kb.llm import ChatModel

_RULE_VERB = (
    r"(?:acumula[mr]?|combinad[oa]s?|somad[oa]s?|vale[m]?|aceit[ao]s?|permitid[oa]s?"
    r"|eleg[ií]ve(?:l|is))"
)
_NEGATIVE = re.compile(rf"\b(?:não|nem)\b(?:\W+\w+){{0,3}}?\W+{_RULE_VERB}", re.IGNORECASE)
_POSITIVE = re.compile(rf"\b{_RULE_VERB}\b", re.IGNORECASE)
_STOPWORDS = frozenset(
    {
        "a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "é", "em", "no", "na", "nos",
        "nas", "com", "por", "para", "um", "uma", "uns", "umas", "que", "se", "ao", "à", "não",
        "nem", "pode", "podem", "ser", "ou", "mais", "mês", "seu", "sua", "seus", "suas", "este",
        "esta", "isso", "nesta", "neste", "qualquer", "todos", "todas", "cada", "ela", "ele",
        "foi", "são", "está", "estão", "também", "apenas",
    }
)

_JUDGE_SYSTEM = (
    "Você revisa a base de conhecimento comercial de um grupo de concessionárias. "
    "Responda somente com o JSON pedido."
)
_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"contradicao": {"type": "boolean"}, "explicacao": {"type": "string"}},
    "required": ["contradicao", "explicacao"],
}


@dataclass(frozen=True)
class RuleSentence:
    doc_key: str
    titulo: str
    caminho_secao: str
    frase: str
    polaridade: str  # "positiva" | "negativa"
    termos: frozenset[str]
    acesso: dict


@dataclass
class ConflictCandidate:
    doc_a: str
    secao_a: str
    frase_a: str
    doc_b: str
    secao_b: str
    frase_b: str
    termos_comuns: list[str]
    confirmado: bool | None = None  # None: não verificado pelo juiz
    explicacao: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def rule_sentences(chunks: list[dict]) -> list[RuleSentence]:
    """Frases que afirmam ou negam uma regra, extraídas dos trechos vigentes."""
    result = []
    for chunk in chunks:
        for frase in _sentences(chunk["conteudo"]):
            polaridade = _polarity(frase)
            if polaridade:
                result.append(
                    RuleSentence(
                        doc_key=chunk["doc_key"],
                        titulo=chunk["titulo"],
                        caminho_secao=chunk["caminho_secao"],
                        frase=frase,
                        polaridade=polaridade,
                        termos=frozenset(_terms(frase)),
                        acesso=chunk["acesso"],
                    )
                )
    return result


def candidates(
    sentences: list[RuleSentence],
    *,
    min_common_terms: int,
    ignore_pairs: set[frozenset[str]] = frozenset(),
) -> list[ConflictCandidate]:
    """Pares de frases de polaridade oposta, em documentos diferentes, sobre os mesmos termos.

    Descarta pares de documentos quase duplicados (`ignore_pairs`) e de documentos de marcas
    diferentes (valem para públicos diferentes). Frases idênticas em documentos diferentes
    (por exemplo, o mesmo parágrafo nos manuais de duas marcas) contam uma vez só.
    """
    found: dict[tuple[str, str], ConflictCandidate] = {}
    for a, b in itertools.combinations(sentences, 2):
        if a.doc_key == b.doc_key or a.polaridade == b.polaridade:
            continue
        if frozenset((a.doc_key, b.doc_key)) in ignore_pairs:
            continue
        marca_a, marca_b = a.acesso.get("marca"), b.acesso.get("marca")
        if marca_a and marca_b and marca_a != marca_b:
            continue
        common = a.termos & b.termos
        if len(common) < min_common_terms:
            continue
        key = tuple(sorted((a.frase, b.frase)))
        if key in found:
            continue
        found[key] = ConflictCandidate(
            doc_a=a.doc_key,
            secao_a=a.caminho_secao,
            frase_a=a.frase,
            doc_b=b.doc_key,
            secao_b=b.caminho_secao,
            frase_b=b.frase,
            termos_comuns=sorted(common),
        )
    return list(found.values())


def judge(candidate: ConflictCandidate, model: ChatModel) -> ConflictCandidate:
    """Confirma o candidato só se o modelo apontar contradição nas duas ordens (A x B e B x A).

    Modelos pequenos são sensíveis à ordem das regras no prompt. Exigir a mesma resposta nas
    duas ordens corta falsos positivos; candidatos descartados na primeira chamada não pagam
    a segunda. (Pedir ao modelo para extrair as condições antes de decidir foi testado e
    piorou: ele perdeu os conflitos reais.)
    """
    first = _ask(model, (candidate.secao_a, candidate.frase_a), (candidate.secao_b, candidate.frase_b))
    candidate.explicacao = str(first.get("explicacao", "")).strip()
    if not first["contradicao"]:
        candidate.confirmado = False
        return candidate
    second = _ask(model, (candidate.secao_b, candidate.frase_b), (candidate.secao_a, candidate.frase_a))
    candidate.confirmado = bool(second["contradicao"])
    return candidate


def _ask(model: ChatModel, rule_a: tuple[str, str], rule_b: tuple[str, str]) -> dict:
    prompt = (
        "Duas regras de documentos diferentes estão vigentes ao mesmo tempo.\n\n"
        f'Regra A ({rule_a[0]}): "{rule_a[1]}"\n'
        f'Regra B ({rule_b[0]}): "{rule_b[1]}"\n\n'
        "Elas se contradizem para a MESMA situação comercial? Regras sobre situações "
        "diferentes (outra modalidade de venda, outro programa, outro público) não são "
        "contradição. Uma exceção explícita em um documento (por exemplo, \"nesta campanha\") "
        "contrariando uma regra geral de outro documento É uma contradição a revisar.\n"
        'Responda em JSON: {"contradicao": true ou false, "explicacao": "uma frase curta"}'
    )
    return model.complete_json(_JUDGE_SYSTEM, prompt, _JUDGE_SCHEMA)


def _sentences(text: str):
    for part in re.split(r"(?<=[.!?])\s+|\n+", text):
        part = part.strip(" -*")
        if part and not part.startswith("|"):  # linhas de tabela não são frases
            yield part


def _polarity(sentence: str) -> str | None:
    if _NEGATIVE.search(sentence):
        return "negativa"
    if _POSITIVE.search(sentence):
        return "positiva"
    return None


def _terms(sentence: str) -> set[str]:
    words = re.findall(r"[a-zà-ú0-9]+", sentence.lower())
    return {
        w for w in words if w not in _STOPWORDS and len(w) > 2 and not re.fullmatch(_RULE_VERB, w)
    }
