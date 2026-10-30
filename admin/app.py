"""Painel de administração da base de conhecimento.

Rode com: uv run streamlit run admin/app.py
Só lê o catálogo e chama as mesmas funções do pacote `kb` usadas pela CLI e pelas DAGs.
"""

import difflib

import altair as alt
import pandas as pd
import streamlit as st

from kb import components
from kb.config import Settings
from kb.consultas import consultar
from kb.errors import KbError
from kb.maintenance.health import SEVERIDADES, load_health_config
from kb.retrieval import list_pipelines, load_pipeline
from kb.store import catalog, evals, query_logs, vectors
from kb.store import health as health_store
from kb.store.db import connect

st.set_page_config(page_title="Base de Conhecimento", page_icon="📚", layout="wide")

# Ordem fixa das pipelines e das cores (paleta categórica validada para daltonismo).
PIPELINE_ORDER = ["vetorial", "hibrida", "hibrida-rerank"]
PIPELINE_COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]
SEVERITY_ICON = {"erro": "🔴", "alerta": "🟠", "info": "🔵"}


@st.cache_resource
def settings() -> Settings:
    return Settings()


@st.cache_resource
def connection():
    return connect(settings().database_url)


def conn():
    connection_ = connection()
    if connection_.closed:
        st.cache_resource.clear()
        connection_ = connection()
    return connection_


def page_overview() -> None:
    st.header("Visão geral")
    hoje = components.hoje(settings(), conn())
    index = catalog.active_index_config(conn())
    totals = catalog.stats(conn())
    cols = st.columns(4)
    cols[0].metric("Documentos ativos", totals["documentos_ativos"])
    cols[1].metric("Aposentados", totals["documentos_aposentados"])
    cols[2].metric("Versões", totals["versoes"])
    cols[3].metric("Trechos", totals["chunks"])
    st.caption(
        f"Data de referência: **{hoje.isoformat()}** · índice ativo: `{index.id}` "
        f"({index.embedding_model}, {vectors.count(conn(), index.tabela)} embeddings)"
    )

    reports = health_store.list_reports(conn(), limit=1)
    st.subheader("Último relatório de saúde")
    if reports:
        resumo = reports[0]["resumo"]
        cols = st.columns(3)
        for col, severidade in zip(cols, SEVERIDADES):
            col.metric(f"{SEVERITY_ICON[severidade]} {severidade.capitalize()}", resumo[severidade])
        st.caption(f"Gerado em {reports[0]['criado_em']:%d/%m/%Y %H:%M}, referência {reports[0]['data_referencia']}")
    else:
        st.info("Nenhum relatório gerado ainda. Rode `kb health` ou a DAG kb_health_report.")

    runs = evals.history(conn())
    st.subheader("Última avaliação por pipeline")
    if runs:
        latest = {}
        for run in runs:
            latest[run["pipeline"]] = run
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Pipeline": r["pipeline"],
                        "Release": r["release"],
                        "Recall@1": r["metricas"]["recall@1"],
                        "MRR": r["metricas"]["mrr"],
                        "Vazamentos": r["metricas"]["vazamentos"],
                        "Latência média (ms)": r["metricas"]["latencia_media_ms"],
                    }
                    for r in latest.values()
                ]
            ),
            hide_index=True,
        )
    else:
        st.info("Nenhuma avaliação registrada. Rode `kb eval` ou a DAG kb_eval.")


def page_documents() -> None:
    st.header("Documentos")
    hoje = components.hoje(settings(), conn())
    docs = catalog.list_documents(conn(), hoje)
    table = pd.DataFrame(
        [
            {
                "Documento": d["doc_key"],
                "Título": d["titulo"],
                "Escopo": d["escopo"],
                "Acesso": ", ".join(f"{k}={v}" for k, v in d["acesso"].items() if v),
                "Status": d["status"] + (f" ({d['aposentado_em']})" if d["aposentado_em"] else ""),
                "Versão vigente": str(d["versao_vigente"] or "-"),
                "Última versão": str(d["ultima_versao"]),
            }
            for d in docs
        ]
    )
    st.dataframe(table, hide_index=True)

    doc_key = st.selectbox("Histórico de um documento", [d["doc_key"] for d in docs])
    index = catalog.active_index_config(conn())
    versions = catalog.document_versions(conn(), index.id, doc_key)
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Versão": v["versao"],
                    "Vigente de": v["valid_from"].isoformat(),
                    "Até": v["valid_to"].isoformat() if v["valid_to"] else "-",
                    "Revisar até": v["review_by"].isoformat() if v["review_by"] else "-",
                    "Trechos": v["chunks"],
                    "Hash": v["content_hash"][:8],
                }
                for v in versions
            ]
        ),
        hide_index=True,
    )
    if len(versions) > 1:
        numbers = [v["versao"] for v in versions]
        col_a, col_b = st.columns(2)
        old = col_a.selectbox("Comparar a versão", numbers, index=len(numbers) - 2)
        new = col_b.selectbox("com a versão", numbers, index=len(numbers) - 1)
        diff = difflib.unified_diff(
            (catalog.version_content(conn(), doc_key, old) or "").splitlines(),
            (catalog.version_content(conn(), doc_key, new) or "").splitlines(),
            fromfile=f"v{old}",
            tofile=f"v{new}",
            lineterm="",
        )
        st.code("\n".join(diff) or "Sem diferenças.", language="diff")


def page_search() -> None:
    st.header("Busca")
    s = settings()
    policy = components.access_policy(s)
    with st.form("busca"):
        pergunta = st.text_input("Pergunta", "O bônus de troca do Trilha acumula com o desconto PCD?")
        cols = st.columns(4)
        perfil = cols[0].selectbox("Perfil", ["(sem filtro de acesso)", *policy.perfis])
        pipelines = list_pipelines(s.pipelines_dir)
        pipeline = cols[1].selectbox("Pipeline", pipelines, index=pipelines.index(s.pipeline))
        as_of = cols[2].date_input("Data de referência", components.hoje(s, conn()))
        k = cols[3].number_input("Trechos", 1, 20, 5)
        submitted = st.form_submit_button("Buscar")
    if not submitted:
        return
    config = load_pipeline(s.pipelines_dir, pipeline)
    retriever = components.retriever(s, conn(), with_reranker=config.rerank.ativo)
    profile = policy.profile(perfil) if perfil in policy.perfis else None
    hits = consultar(
        conn(), retriever, pergunta, pipeline=config, as_of=as_of, k=int(k), profile=profile,
        origem="painel",
    )
    if not hits:
        st.warning("Nenhum trecho encontrado.")
    for position, hit in enumerate(hits, start=1):
        ranks = [("vetorial", hit.rank_vetorial), ("textual", hit.rank_textual)]
        origem = ", ".join(f"{name} #{rank}" for name, rank in ranks if rank)
        with st.container(border=True):
            st.markdown(f"**{position}. {hit.caminho_secao}**")
            st.caption(f"`{hit.doc_key}` v{hit.versao} · {origem} · score {hit.score:.3f}")
            st.markdown(hit.conteudo)


def page_health() -> None:
    st.header("Saúde da base")
    reports = health_store.list_reports(conn())
    if not reports:
        st.info("Nenhum relatório gerado ainda. Rode `kb health` ou a DAG kb_health_report.")
        return
    labels = {
        f"#{r['id']} · referência {r['data_referencia']} · gerado {r['criado_em']:%d/%m %H:%M}": r
        for r in reports
    }
    report = labels[st.selectbox("Relatório", list(labels))]
    cols = st.columns(3)
    for col, severidade in zip(cols, SEVERIDADES):
        col.metric(f"{SEVERITY_ICON[severidade]} {severidade.capitalize()}", report["resumo"][severidade])
    for secao in report["secoes"]:
        count = len(secao["achados"])
        with st.expander(f"{secao['titulo']} ({count})", expanded=count > 0):
            st.caption(secao["descricao"])
            if not count:
                st.success("Nada a apontar.")
            for achado in secao["achados"]:
                st.markdown(f"{SEVERITY_ICON[achado['severidade']]} {achado['titulo']}")
                if achado["detalhe"]:
                    st.caption(achado["detalhe"])


def page_evaluations() -> None:
    st.header("Avaliações")
    runs = evals.history(conn())
    if not runs:
        st.info("Nenhuma avaliação registrada. Rode `kb eval` ou a DAG kb_eval.")
        return
    data = pd.DataFrame(
        [
            {
                "Execução": r["id"],
                "Quando": r["criado_em"],
                "Pipeline": r["pipeline"].split("@")[0],
                "Versão": r["pipeline"],
                "Release": r["release"],
                "Recall@1": r["metricas"]["recall@1"],
                "MRR": r["metricas"]["mrr"],
                "Vazamentos": r["metricas"]["vazamentos"],
                "Latência média (ms)": r["metricas"]["latencia_media_ms"],
            }
            for r in runs
        ]
    )
    # Uma métrica por gráfico (sem eixo duplo); cor fixa por pipeline.
    domain = [p for p in PIPELINE_ORDER if p in set(data["Pipeline"])] + sorted(
        set(data["Pipeline"]) - set(PIPELINE_ORDER)
    )
    colors = PIPELINE_COLORS + ["#eda100", "#e87ba4", "#6250d6"]
    cols = st.columns(2)
    for col, metric in zip(cols, ("Recall@1", "MRR")):
        chart = (
            alt.Chart(data, title=metric)
            .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=64, filled=True))
            .encode(
                x=alt.X("Quando:T", title=None),
                y=alt.Y(f"{metric}:Q", scale=alt.Scale(domain=[0, 1]), title=None),
                color=alt.Color(
                    "Pipeline:N",
                    scale=alt.Scale(domain=domain, range=colors[: len(domain)]),
                    legend=alt.Legend(orient="bottom", title=None),
                ),
                tooltip=["Versão", "Release", alt.Tooltip(f"{metric}:Q", format=".3f"), "Quando:T"],
            )
        )
        col.altair_chart(chart, width="stretch")
    st.dataframe(data.sort_values("Execução", ascending=False), hide_index=True)

    run_id = st.selectbox("Detalhes da execução", data["Execução"].sort_values(ascending=False))
    run = next(r for r in runs if r["id"] == run_id)
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Pergunta": i["id"],
                    "Tipo": i["tipo"],
                    "Perfil": i["perfil"],
                    "Posição": (
                        "-" if not i["tem_esperado"]
                        else str(i["posicao"]) if i["posicao"] else "não achou"
                    ),
                    "1º recuperado": i["recuperados"][0] if i["recuperados"] else "-",
                    "Alertas": "; ".join(i["vazamentos"] + i["proibidos"]),
                }
                for i in run["itens"]
            ]
        ),
        hide_index=True,
    )


def page_queries() -> None:
    st.header("Consultas")
    cfg = load_health_config(settings().saude_path).lacunas
    gaps = query_logs.gaps(
        conn(), pipeline_nome=cfg.pipeline, score_maximo=cfg.score_maximo, dias=cfg.dias
    )
    st.subheader("Lacunas")
    st.caption(
        f"Perguntas dos últimos {cfg.dias} dias cujo melhor trecho na pipeline {cfg.pipeline} "
        f"teve score abaixo de {cfg.score_maximo}."
    )
    if gaps:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Pergunta": g["pergunta"],
                        "Ocorrências": g["ocorrencias"],
                        "Melhor score": g["melhor_score"],
                        "Perfis": ", ".join(p for p in g["perfis"] if p),
                    }
                    for g in gaps
                ]
            ),
            hide_index=True,
        )
    else:
        st.success("Nenhuma lacuna no período.")
    st.subheader("Consultas recentes")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Quando": r["criado_em"],
                    "Origem": r["origem"],
                    "Perfil": r["perfil"] or "-",
                    "Pergunta": r["pergunta"],
                    "Pipeline": r["pipeline"],
                    "Data de referência": r["as_of"],
                    "Melhor trecho": r["resultados"][0]["doc_key"] if r["resultados"] else "-",
                    "Score": r["melhor_score"],
                    "Latência (ms)": r["latencia_ms"],
                }
                for r in query_logs.recent(conn(), 100)
            ]
        ),
        hide_index=True,
    )


PAGES = {
    "Visão geral": page_overview,
    "Documentos": page_documents,
    "Busca": page_search,
    "Saúde": page_health,
    "Avaliações": page_evaluations,
    "Consultas": page_queries,
}

st.sidebar.title("📚 Base de Conhecimento")
page = st.sidebar.radio("Página", list(PAGES), label_visibility="collapsed")
try:
    st.sidebar.caption(f"Data de referência: {components.hoje(settings(), conn()).isoformat()}")
    PAGES[page]()
except KbError as exc:
    st.error(str(exc))
