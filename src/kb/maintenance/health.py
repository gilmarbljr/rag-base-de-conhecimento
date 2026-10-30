"""Relatório de saúde da base de conhecimento.

Cada verificação devolve uma seção independente, para que o orquestrador possa rodá-las
em paralelo e consolidar no final. As severidades:

- erro:   a base está errada e precisa de correção (ex.: trecho vigente sem embedding);
- alerta: precisa de revisão humana (ex.: documento vencido, conflito, duplicata);
- info:   acompanhamento (ex.: documento a vencer, distribuição dos trechos).
"""

import statistics
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

import psycopg
import yaml
from pydantic import BaseModel, ValidationError

from kb.errors import KbError
from kb.evaluation.runner import find_regressions
from kb.llm import ChatModel
from kb.maintenance import conflicts
from kb.store import catalog, evals, query_logs
from kb.store import health as queries
from kb.store.catalog import IndexConfig

SEVERIDADES = ("erro", "alerta", "info")


class ConflitosConfig(BaseModel):
    termos_comuns: int = 3
    usar_juiz: bool = True


class LacunasConfig(BaseModel):
    pipeline: str = "hibrida-rerank"
    score_maximo: float = 0.2
    dias: int = 30


class HealthConfig(BaseModel):
    vencimento_alerta_dias: int = 15
    duplicata_similaridade: float = 0.97
    conflitos: ConflitosConfig = ConflitosConfig()
    chunk_minimo_caracteres: int = 120
    lacunas: LacunasConfig = LacunasConfig()


def load_health_config(path: Path) -> HealthConfig:
    if not path.exists():
        return HealthConfig()
    try:
        return HealthConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    except ValidationError as exc:
        raise KbError(f"Configuração de saúde inválida em {path}:\n{exc}") from exc


@dataclass
class Finding:
    severidade: str
    titulo: str
    detalhe: str = ""
    documentos: list[str] = field(default_factory=list)


@dataclass
class Section:
    id: str
    titulo: str
    descricao: str
    achados: list[Finding] = field(default_factory=list)
    metricas: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Section":
        return cls(**{**data, "achados": [Finding(**f) for f in data["achados"]]})


@dataclass
class HealthReport:
    data_referencia: date
    index_config_id: str
    secoes: list[Section]

    @property
    def resumo(self) -> dict:
        total = {s: 0 for s in SEVERIDADES}
        for secao in self.secoes:
            for achado in secao.achados:
                total[achado.severidade] += 1
        return total

    def to_dict(self) -> dict:
        return {
            "data_referencia": self.data_referencia.isoformat(),
            "index_config_id": self.index_config_id,
            "resumo": self.resumo,
            "secoes": [s.to_dict() for s in self.secoes],
        }

    def to_markdown(self) -> str:
        lines = [
            f"# Relatório de saúde da base: {self.data_referencia.isoformat()}",
            "",
            f"Índice ativo: `{self.index_config_id}`",
            "",
            "| Seção | Erros | Alertas | Informações |",
            "|---|---|---|---|",
        ]
        for secao in self.secoes:
            counts = {s: sum(1 for a in secao.achados if a.severidade == s) for s in SEVERIDADES}
            lines.append(f"| {secao.titulo} | {counts['erro']} | {counts['alerta']} | {counts['info']} |")
        for secao in self.secoes:
            lines += ["", f"## {secao.titulo}", "", secao.descricao, ""]
            if not secao.achados:
                lines.append("Nada a apontar.")
            for achado in secao.achados:
                lines.append(f"- **[{achado.severidade}]** {achado.titulo}")
                if achado.detalhe:
                    lines.append(f"  {achado.detalhe}")
        return "\n".join(lines) + "\n"


# --- Verificações -----------------------------------------------------------------------


def check_review_dates(conn: psycopg.Connection, hoje: date, cfg: HealthConfig) -> Section:
    limite = hoje + timedelta(days=cfg.vencimento_alerta_dias)
    section = Section(
        "revisao",
        "Revisão de documentos",
        f"Versões vigentes com revisão vencida ou prevista para os próximos "
        f"{cfg.vencimento_alerta_dias} dias.",
    )
    for row in queries.review_due(conn, hoje, limite):
        vencido = row["review_by"] < hoje
        section.achados.append(
            Finding(
                "alerta" if vencido else "info",
                f"{row['titulo']} (v{row['versao']}): revisão "
                f"{'vencida em' if vencido else 'prevista para'} {row['review_by'].isoformat()}",
                documentos=[row["doc_key"]],
            )
        )
    return section


def check_expired(conn: psycopg.Connection, hoje: date) -> Section:
    section = Section(
        "vigencia",
        "Vigência encerrada",
        "Documentos que continuam na fonte, mas cuja vigência já terminou. Eles não aparecem na "
        "busca atual; podem ser arquivados.",
    )
    for row in queries.expired_in_source(conn, hoje):
        section.achados.append(
            Finding(
                "info",
                f"{row['titulo']} ({row['doc_key']}): vigência encerrada em {row['fim'].isoformat()}",
                documentos=[row["doc_key"]],
            )
        )
    return section


def check_duplicates(
    conn: psycopg.Connection, index: IndexConfig, hoje: date, cfg: HealthConfig
) -> Section:
    section = Section(
        "duplicatas",
        "Quase-duplicatas",
        f"Documentos vigentes diferentes com trechos de similaridade ≥ {cfg.duplicata_similaridade}. "
        "Cópias do mesmo conteúdo ocupam espaço no top-k e divergem quando só uma é atualizada.",
    )
    pares = queries.near_duplicate_pairs(
        conn, index.id, index.tabela, hoje, cfg.duplicata_similaridade
    )
    for row in pares:
        menor = min(row["trechos_a"], row["trechos_b"])
        share = row["trechos_iguais"] / menor if menor else 0
        section.achados.append(
            Finding(
                "alerta" if share >= 0.5 else "info",
                f"{row['doc_a']} e {row['doc_b']}: {row['trechos_iguais']} trecho(s) quase "
                f"idêntico(s) ({share:.0%} do menor documento)",
                documentos=[row["doc_a"], row["doc_b"]],
            )
        )
    section.metricas["pares"] = [[r["doc_a"], r["doc_b"]] for r in pares if r["trechos_iguais"]]
    return section


def check_conflicts(
    conn: psycopg.Connection,
    index: IndexConfig,
    hoje: date,
    cfg: HealthConfig,
    model: ChatModel | None,
    duplicate_pairs: list[list[str]] = (),
) -> Section:
    juiz = model if cfg.conflitos.usar_juiz else None
    section = Section(
        "conflitos",
        "Possíveis conflitos",
        "Regras de documentos vigentes diferentes que se contradizem. Candidatos encontrados por "
        "heurística (frases de polaridade oposta sobre os mesmos termos)"
        + (f" e confirmados pelo modelo {juiz.model}." if juiz else ", sem confirmação por LLM."),
    )
    sentences = conflicts.rule_sentences(catalog.current_chunks(conn, index.id, hoje))
    found = conflicts.candidates(
        sentences,
        min_common_terms=cfg.conflitos.termos_comuns,
        ignore_pairs={frozenset(pair) for pair in duplicate_pairs},
    )
    for candidate in found:
        if juiz:
            conflicts.judge(candidate, juiz)
            if not candidate.confirmado:
                continue
        section.achados.append(
            Finding(
                # Sem o juiz, o candidato não foi verificado: fica como informação para revisão.
                "alerta" if juiz else "info",
                f"{candidate.doc_a} x {candidate.doc_b}"
                + (f": {candidate.explicacao}" if candidate.explicacao else " (não verificado)"),
                detalhe=f'"{candidate.frase_a}" x "{candidate.frase_b}"',
                documentos=[candidate.doc_a, candidate.doc_b],
            )
        )
    section.metricas = {
        "frases_de_regra": len(sentences),
        "candidatos": len(found),
        "confirmados": len(section.achados) if juiz else None,
        "candidatos_detalhados": [c.to_dict() for c in found],
    }
    return section


def check_consistency(conn: psycopg.Connection, index: IndexConfig, hoje: date) -> Section:
    section = Section(
        "consistencia",
        "Consistência do índice",
        "Trechos vigentes sem embedding (não aparecem na busca vetorial) e registros órfãos.",
    )
    for row in queries.chunks_without_embedding(conn, index.id, index.tabela, hoje):
        section.achados.append(
            Finding(
                "erro",
                f"{row['doc_key']}: {row['trechos']} trecho(s) vigente(s) sem embedding",
                documentos=[row["doc_key"]],
            )
        )
    orphans = queries.orphan_counts(conn, index.id, index.tabela)
    if orphans["embeddings_orfaos"]:
        section.achados.append(
            Finding("info", f"{orphans['embeddings_orfaos']} embedding(s) sem trecho no índice ativo")
        )
    if orphans["trechos_orfaos"]:
        section.achados.append(
            Finding("info", f"{orphans['trechos_orfaos']} trecho(s) sem nenhuma versão vinculada")
        )
    section.metricas = orphans
    return section


def check_chunk_sizes(
    conn: psycopg.Connection, index: IndexConfig, hoje: date, cfg: HealthConfig
) -> Section:
    rows = catalog.current_chunks(conn, index.id, hoje)
    sizes = [len(r["conteudo"]) for r in rows]
    section = Section(
        "chunks",
        "Fragmentos de trechos",
        f"Pedaços com menos de {cfg.chunk_minimo_caracteres} caracteres que sobraram da divisão "
        "de uma seção grande: sozinhos, quase nunca respondem a uma pergunta. Seções pequenas "
        "e completas (um horário, um endereço) não entram aqui.",
    )
    chunks_per_section: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row["doc_key"], row["caminho_secao"])
        chunks_per_section[key] = chunks_per_section.get(key, 0) + 1
    small = 0
    for row in rows:
        if len(row["conteudo"]) >= cfg.chunk_minimo_caracteres:
            continue
        small += 1
        if chunks_per_section[(row["doc_key"], row["caminho_secao"])] > 1:
            section.achados.append(
                Finding(
                    "alerta",
                    f"{row['caminho_secao']} ({row['doc_key']}): fragmento de "
                    f"{len(row['conteudo'])} caracteres",
                    detalhe=row["conteudo"][:120],
                    documentos=[row["doc_key"]],
                )
            )
    if sizes:
        section.metricas = {
            "trechos": len(sizes),
            "minimo": min(sizes),
            "mediana": int(statistics.median(sizes)),
            "maximo": max(sizes),
            "pequenos": small,
            "fragmentos": len(section.achados),
        }
    return section


def check_gaps(conn: psycopg.Connection, cfg: HealthConfig) -> Section:
    lac = cfg.lacunas
    section = Section(
        "lacunas",
        "Lacunas de conteúdo",
        f"Perguntas reais dos últimos {lac.dias} dias cujo melhor trecho na pipeline "
        f"{lac.pipeline} teve score abaixo de {lac.score_maximo}. Ou a base não tem a resposta "
        "(conteúdo a criar), ou tem e a busca não a encontrou (caso para o golden set).",
    )
    for row in query_logs.gaps(
        conn, pipeline_nome=lac.pipeline, score_maximo=lac.score_maximo, dias=lac.dias
    ):
        score = "sem resultado" if row["melhor_score"] is None else f"melhor score {row['melhor_score']:.2f}"
        perfis = ", ".join(p for p in row["perfis"] if p) or "sem perfil"
        section.achados.append(
            Finding(
                "alerta" if row["ocorrencias"] > 1 else "info",
                f"\"{row['pergunta']}\": {row['ocorrencias']} vez(es), {score}",
                detalhe=f"Perfis: {perfis}",
            )
        )
    return section


def check_evaluations(conn: psycopg.Connection, index: IndexConfig) -> Section:
    section = Section(
        "avaliacao",
        "Avaliação",
        "Última avaliação de cada pipeline com o golden set, comparada com a execução anterior.",
    )
    latest = queries.latest_evals(conn, index.id)
    if not latest:
        section.achados.append(Finding("alerta", "Nenhuma avaliação registrada para o índice ativo"))
    for row in latest:
        previous = evals.previous_metrics(
            conn, pipeline=row["pipeline"], index_config_id=index.id,
            release=row["release"], k=row["k"], before_id=row["id"],
        )
        for problema in find_regressions(row["metricas"], previous):
            severidade = "erro" if "vazado" in problema or "proibido" in problema else "alerta"
            section.achados.append(Finding(severidade, f"{row['pipeline']}: {problema}"))
        section.metricas[row["pipeline"]] = {
            "release": row["release"],
            "recall@1": row["metricas"]["recall@1"],
            "mrr": row["metricas"]["mrr"],
            "vazamentos": row["metricas"]["vazamentos"],
        }
    return section


def build_report(
    conn: psycopg.Connection,
    index: IndexConfig,
    hoje: date,
    cfg: HealthConfig,
    model: ChatModel | None,
) -> HealthReport:
    duplicates = check_duplicates(conn, index, hoje, cfg)
    return HealthReport(
        data_referencia=hoje,
        index_config_id=index.id,
        secoes=[
            check_review_dates(conn, hoje, cfg),
            check_expired(conn, hoje),
            duplicates,
            check_conflicts(conn, index, hoje, cfg, model, duplicates.metricas["pares"]),
            check_consistency(conn, index, hoje),
            check_chunk_sizes(conn, index, hoje, cfg),
            check_gaps(conn, cfg),
            check_evaluations(conn, index),
        ],
    )
