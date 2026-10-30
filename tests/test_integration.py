"""Pipeline completo contra um Postgres real, com embeddings falsos (sem Ollama).

Roda só quando KB_TEST_DATABASE_URL está definida. O teste cria um banco temporário,
usa e remove ao final, então pode apontar para o mesmo servidor do projeto.
"""

import os
import uuid
from datetime import date
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from kb.access import load_policy
from kb.chunking import HierarchicalChunker
from kb.embedding import FakeEmbedder
from kb.errors import KbError
from kb.evaluation import load_golden_set, run_eval
from kb.maintenance import reindex, sync_folder
from kb.maintenance.health import build_report, load_health_config
from kb.retrieval import FakeReranker, PipelineConfig, Retriever, list_pipelines, load_pipeline
from kb.store import catalog, evals, query_logs, vectors
from kb.store.db import apply_migrations, connect

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "corpus"
PIPELINES = ROOT / "configs" / "pipelines"
VETORIAL = PipelineConfig(nome="vetorial", versao=1, recuperacao={"modo": "vetorial"})


@pytest.fixture
def conn():
    admin_url = os.environ.get("KB_TEST_DATABASE_URL")
    if not admin_url:
        pytest.skip("KB_TEST_DATABASE_URL não definida")
    name = f"kb_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    connection = connect(make_conninfo(admin_url, dbname=name))
    try:
        yield connection
    finally:
        connection.close()
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def kb(conn):
    """Banco migrado com uma configuração de índice ativa, e atalhos para sincronizar e buscar."""
    assert apply_migrations(conn) == [
        "001_catalogo.sql", "002_aposentadoria.sql", "003_eval_runs.sql", "004_operacao.sql"
    ]
    assert apply_migrations(conn) == []
    embedder = FakeEmbedder()
    chunker = HierarchicalChunker(max_chars=1800)
    config, activated = catalog.ensure_index_config(
        conn, chunker.name, chunker.params, embedder.model, embedder.dimension
    )
    assert activated
    policy = load_policy(ROOT / "data" / "perfis.yaml")
    retriever = Retriever(conn, config, embedder, FakeReranker(), policy)

    class Kb:
        def sync(self, release: int, data_efetiva: date | None = None):
            _, results = sync_folder(
                conn, CORPUS / f"release_{release}", source_name="teste", chunker=chunker,
                embedder=embedder, config=config, hoje=date(2026, 1, 1), data_efetiva=data_efetiva,
            )
            return {r.doc_key: r for r in results}

        def search(self, question: str, as_of: date, k: int = 5, pipeline=VETORIAL, profile=None):
            return retriever.search(question, pipeline=pipeline, as_of=as_of, k=k, profile=profile)

        def versions(self, doc_key: str):
            return catalog.document_versions(conn, config.id, doc_key)

    Kb.config = config
    Kb.policy = policy
    Kb.retriever = retriever
    return Kb()


def test_linha_do_tempo_completa(kb, conn):
    # Release 1: tudo novo; reprocessar não muda nada.
    r1 = kb.sync(1)
    assert {r.status for r in r1.values()} == {"novo"}
    assert vectors.count(conn, kb.config.tabela) == sum(r.chunks for r in r1.values())
    assert {r.status for r in kb.sync(1).values()} == {"inalterado"}

    # Release 2: uma circular nova e duas novas versões; o resto inalterado.
    r2 = kb.sync(2)
    changed = {key: r.status for key, r in r2.items() if r.status != "inalterado"}
    assert changed == {
        "circular-altis-2026-02": "novo",
        "pcd-veltra": "nova_versao",
        "tabela-precos-altis": "nova_versao",
    }
    pcd = r2["pcd-veltra"]
    assert (pcd.novos_embeddings, pcd.removidos) == (1, 1)  # só a seção de documentos mudou
    assert pcd.reaproveitados == pcd.chunks - 1

    tabela = kb.versions("tabela-precos-altis")
    assert [(v["valid_from"], v["valid_to"]) for v in tabela] == [
        (date(2026, 1, 1), date(2026, 1, 31)),
        (date(2026, 2, 1), None),
    ]

    # Consulta no tempo: o preço vigente depende da data.
    def price_version(as_of):
        hits = kb.search("tabela de preços Altis Nexa preço público", as_of, k=10)
        return next(h.versao for h in hits if h.doc_key == "tabela-precos-altis")

    assert price_version(date(2026, 1, 28)) == 1
    assert price_version(date(2026, 2, 15)) == 2

    # Release 3: o programa sai da fonte (tombstone) e entra a cópia encaminhada.
    r3 = kb.sync(3)
    changed = {key: r.status for key, r in r3.items() if r.status != "inalterado"}
    assert changed == {
        "programa-rota-limpa": "aposentado",
        "circular-altis-2026-02-encaminhada": "novo",
    }
    [programa] = kb.versions("programa-rota-limpa")
    assert programa["valid_to"] == date(2026, 3, 9)  # véspera da data efetiva (10/03)

    def finds_program(as_of):
        hits = kb.search("Programa Rota Limpa bônus reciclagem", as_of, k=10)
        return any(h.doc_key == "programa-rota-limpa" for h in hits)

    assert finds_program(date(2026, 2, 20))
    assert not finds_program(date(2026, 3, 15))
    assert {r.status for r in kb.sync(3).values()} == {"inalterado"}

    # O programa volta à fonte: é reativado com uma nova versão; a cópia encaminhada sai.
    back = kb.sync(2, data_efetiva=date(2026, 3, 20))
    assert back["programa-rota-limpa"].status == "reativado"
    assert back["circular-altis-2026-02-encaminhada"].status == "aposentado"
    assert [v["valid_from"] for v in kb.versions("programa-rota-limpa")] == [
        date(2026, 1, 1),
        date(2026, 3, 20),
    ]
    assert finds_program(date(2026, 3, 25))


def test_filtro_sql_de_acesso_equivale_a_regra_em_python(kb, conn):
    kb.sync(1)
    documents = conn.execute("SELECT doc_key, acesso FROM documents").fetchall()
    for name, profile in kb.policy.perfis.items():
        visible_sql = {
            row["doc_key"]
            for row in conn.execute(
                sql.SQL("SELECT doc_key FROM documents d WHERE {}").format(
                    kb.policy.sql_filter(profile)
                )
            )
        }
        visible_python = {d["doc_key"] for d in documents if kb.policy.allows(profile, d["acesso"])}
        assert visible_sql == visible_python, name
        assert visible_sql, f"{name} não vê nenhum documento"


def _without_model_check(pipeline):
    """Nos testes o reranker é falso; a conferência do modelo servido não se aplica."""
    rerank = pipeline.rerank.model_copy(update={"modelo": None})
    return pipeline.model_copy(update={"rerank": rerank})


def test_busca_hibrida_com_perfil_nao_vaza(kb):
    kb.sync(1)
    hibrida = _without_model_check(load_pipeline(PIPELINES, "hibrida-rerank"))
    cliente = kb.policy.profile("cliente-recife")
    hits = kb.search(
        "desconto máximo do vendedor e alçada do gerente", date(2026, 1, 15), k=10,
        pipeline=hibrida, profile=cliente,
    )
    assert hits
    assert all(kb.policy.allows(cliente, h.candidate.acesso) for h in hits)
    assert "alcadas-desconto" not in {h.doc_key for h in hits}

    sem_perfil = kb.search("alçada de desconto do gerente", date(2026, 1, 15), k=10, pipeline=hibrida)
    assert "alcadas-desconto" in {h.doc_key for h in sem_perfil}


def test_pipeline_recusa_reranker_com_outro_modelo(kb):
    kb.sync(1)
    with pytest.raises(KbError, match="espera o reranker"):
        kb.search("bônus de troca", date(2026, 1, 15), pipeline=load_pipeline(PIPELINES, "hibrida-rerank"))


class _JudgeOnlyCircularVsManual:
    """Juiz falso: só confirma conflito entre a circular de fevereiro e o manual PCD Altis."""

    model = "fake-judge"

    def __init__(self):
        self.calls = 0

    def complete_json(self, system, prompt, schema):
        self.calls += 1
        real = "nesta campanha" in prompt.lower() and "pode ser combinado com a campanha" in prompt
        return {"contradicao": real, "explicacao": "teste"}


def test_relatorio_de_saude(kb, conn):
    for release in (1, 2, 3):
        kb.sync(release)
    cfg = load_health_config(ROOT / "configs" / "saude.yaml")

    # Em março: tabelas com revisão vencida, circulares de janeiro e fevereiro expiradas,
    # a cópia encaminhada ainda na fonte e a ata com um trecho minúsculo.
    report = build_report(conn, kb.config, date(2026, 3, 10), cfg, model=None)
    sections = {s.id: s for s in report.secoes}
    titles = lambda sid: " | ".join(a.titulo for a in sections[sid].achados)

    assert "Tabela de Preços Veltra" in titles("revisao")
    assert "circular-altis-2026-01" in {d for a in sections["vigencia"].achados for d in a.documentos}
    # Só o fragmento que sobrou da divisão da ata; seções pequenas e completas não entram.
    assert [a.documentos for a in sections["chunks"].achados] == [["ata-reuniao-sao-paulo-2026-01-12"]]
    assert sections["consistencia"].achados == []
    assert sections["avaliacao"].achados[0].titulo.startswith("Nenhuma avaliação")

    # Em fevereiro, a circular contradiz o manual PCD: candidato confirmado pelo juiz.
    judge = _JudgeOnlyCircularVsManual()
    february = build_report(conn, kb.config, date(2026, 2, 15), cfg, model=judge)
    conflicts_section = next(s for s in february.secoes if s.id == "conflitos")
    # Uma chamada por candidato, mais a segunda ordem para cada conflito confirmado.
    expected_calls = conflicts_section.metricas["candidatos"] + len(conflicts_section.achados)
    assert judge.calls == expected_calls
    confirmed = {frozenset(a.documentos) for a in conflicts_section.achados}
    assert frozenset(("circular-altis-2026-02", "pcd-altis")) in confirmed

    # Lacunas vêm do log de consultas, na escala da pipeline configurada.
    for score in (0.05, 0.03):
        query_logs.insert(
            conn, origem="teste", pergunta="Vocês vendem motos?", perfil="cliente-recife",
            pipeline="hibrida-rerank@v1", index_config_id=kb.config.id, as_of=date(2026, 3, 10),
            k=5, latencia_ms=1.0, resultados=[{"doc_key": "glossario", "score": score}],
        )
    gaps = next(s for s in build_report(conn, kb.config, date(2026, 3, 10), cfg, None).secoes
                if s.id == "lacunas")
    assert [a.severidade for a in gaps.achados] == ["alerta"]
    assert "2 vez(es)" in gaps.achados[0].titulo

    markdown = report.to_markdown()
    assert markdown.startswith("# Relatório de saúde da base: 2026-03-10")
    assert "## Possíveis conflitos" in markdown


def test_reindexacao_blue_green(kb, conn):
    for release in (1, 2, 3):
        kb.sync(release)
    embedder = FakeEmbedder()
    candidate_chunker = HierarchicalChunker(max_chars=1800, min_chars=200)
    candidate, activated = reindex.prepare(conn, chunker=candidate_chunker, embedder=embedder)
    assert not activated and candidate.id != kb.config.id

    # Um índice incompleto não pode ser ativado.
    with pytest.raises(KbError, match="incompleto"):
        reindex.promote(conn, candidate.id)

    result = reindex.build(conn, candidate, chunker=candidate_chunker, embedder=embedder)
    assert result.versoes == catalog.index_coverage(conn, candidate)["versoes_total"]
    # Trechos iguais nos dois índices (mesmo ID) não pedem embedding de novo nesta tabela,
    # mas a tabela do candidato é nova: todos os trechos são embedados uma vez.
    assert result.novos_embeddings == catalog.index_coverage(conn, candidate)["embeddings"]
    again = reindex.build(conn, candidate, chunker=candidate_chunker, embedder=embedder)
    assert again.novos_embeddings == 0  # idempotente

    # A ata não tem mais o fragmento de 55 caracteres no índice candidato.
    ata = catalog.document_chunks(conn, candidate.id, "ata-reuniao-sao-paulo-2026-01-12", date(2026, 3, 10))
    assert len(ata) == 1

    # Promove, consulta no índice novo (inclusive no passado) e faz rollback.
    reindex.promote(conn, candidate.id)
    assert catalog.active_index_config(conn).id == candidate.id
    retriever = Retriever(conn, candidate, embedder, FakeReranker(), kb.policy)
    hits = retriever.search("Programa Rota Limpa requisitos", pipeline=VETORIAL, as_of=date(2026, 2, 20), k=10)
    assert "programa-rota-limpa" in {h.doc_key for h in hits}
    reindex.promote(conn, kb.config.id)
    assert catalog.active_index_config(conn).id == kb.config.id


def test_avaliacao_registra_e_compara_execucoes(kb, conn):
    for release in (1, 2):
        kb.sync(release)
    golden = load_golden_set(ROOT / "data" / "golden_set" / "release_2.yaml")

    for name in list_pipelines(PIPELINES):
        pipeline = _without_model_check(load_pipeline(PIPELINES, name))
        report = run_eval(kb.retriever, golden, pipeline, kb.policy, k=5)
        assert report.metricas["perguntas"] == len(golden.perguntas)
        assert report.metricas["vazamentos"] == 0, name
        assert report.metricas["proibidos"] == 0, name

        assert evals.previous_metrics(
            conn, pipeline=report.pipeline, index_config_id=report.index_config_id,
            release=2, k=5,
        ) is None
        run_id = evals.save_run(conn, report)
        assert evals.previous_metrics(
            conn, pipeline=report.pipeline, index_config_id=report.index_config_id,
            release=2, k=5,
        ) == report.metricas
        assert evals.previous_metrics(
            conn, pipeline=report.pipeline, index_config_id=report.index_config_id,
            release=2, k=5, before_id=run_id,
        ) is None
