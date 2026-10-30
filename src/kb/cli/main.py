from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Annotated

import psycopg
import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from kb import components
from kb.chunking import build_chunker
from kb.config import Settings
from kb.consultas import consultar
from kb.embedding import OllamaEmbedder
from kb.errors import KbError
from kb.evaluation.service import EvalOutcome, evaluate
from kb.maintenance import SyncPlan, SyncResult, blue_green, sync_folder
from kb.maintenance import reindex as reindex_ops
from kb.maintenance.health import SEVERIDADES, HealthReport, build_report, load_health_config
from kb.retrieval import list_pipelines, load_pipeline
from kb.store import catalog, query_logs, vectors
from kb.store import health as health_store
from kb.store.db import apply_migrations, connect

app = typer.Typer(
    help="Base de conhecimento gerenciável para RAG.", no_args_is_help=True, add_completion=False
)
console = Console()

AsOf = Annotated[
    str | None,
    typer.Option("--as-of", help="Data de referência (AAAA-MM-DD). Padrão: KB_DATA_SIMULADA ou hoje."),
]
DataEfetiva = Annotated[
    str | None,
    typer.Option(
        "--data-efetiva",
        help="Data em que as mudanças passam a valer (AAAA-MM-DD). "
        "Padrão: data de referência do manifest, KB_DATA_SIMULADA ou hoje.",
    ),
]
Fonte = Annotated[str, typer.Option(help="Nome da fonte no catálogo.")]

_STATUS_COLORS = {
    "novo": "green",
    "nova_versao": "cyan",
    "reativado": "green",
    "metadados": "yellow",
    "aposentado": "red",
    "inalterado": "dim",
}


@contextmanager
def _session() -> Iterator[tuple[Settings, psycopg.Connection]]:
    """Abre configuração e conexão, e transforma erros esperados em mensagens legíveis."""
    try:
        settings = Settings()
        with connect(settings.database_url) as conn:
            yield settings, conn
    except KbError as exc:
        console.print(f"[bold red]Erro:[/] {exc}")
        raise typer.Exit(1) from None


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        console.print(f"[bold red]Erro:[/] data inválida '{value}'. Use AAAA-MM-DD.")
        raise typer.Exit(1) from None


@app.command()
def init() -> None:
    """Cria as tabelas e registra a configuração de índice (chunker + modelo de embedding)."""
    with _session() as (settings, conn):
        applied = apply_migrations(conn)
        console.print(f"Migrações aplicadas: {', '.join(applied) if applied else 'nenhuma nova'}")

        embedder = OllamaEmbedder(settings.ollama_url, settings.embedding_model)
        dimensao = len(embedder.embed(["teste de dimensão"])[0])
        chunker = build_chunker("hierarquico", {"max_chars": settings.chunk_max_chars})
        config, activated = catalog.ensure_index_config(
            conn, chunker.name, chunker.params, settings.embedding_model, dimensao
        )

        console.print(
            f"Configuração de índice: [bold]{config.id}[/] "
            f"({config.embedding_model}, {config.dimensao} dimensões, tabela {config.tabela})"
        )
        active = catalog.active_index_config(conn)
        if activated:
            console.print("[green]Configuração ativada.[/]")
        elif active.id != config.id:
            console.print(
                f"[yellow]Atenção:[/] a configuração ativa continua sendo [bold]{active.id}[/]. "
                "A troca de configuração (reindexação blue/green) é feita por kb reindex."
            )


@app.command()
def demo(
    completo: Annotated[
        bool,
        typer.Option(
            "--completo",
            help="Também avalia todas as pipelines e confirma conflitos com o LLM (mais lento).",
        ),
    ] = False,
) -> None:
    """Prepara a demonstração do zero: tabelas, as três releases, consultas, avaliação e saúde."""
    steps: list[tuple[str, list[str]]] = [
        ("Criando tabelas e o índice inicial", ["init"]),
        *[(f"Sincronizando a release {r}", ["ingest", f"data/corpus/release_{r}"]) for r in (1, 2, 3)],
        ("Registrando consultas de demonstração", ["simular-consultas", "data/consultas_demo.yaml"]),
        ("Avaliando com o golden set", ["eval"] if completo else ["eval", "--pipeline", "hibrida-rerank"]),
        ("Gerando o relatório de saúde", ["health"] if completo else ["health", "--sem-juiz"]),
    ]
    for title, args in steps:
        console.rule(f"[bold]{title}[/]")
        # Fora do modo standalone, o Click devolve o código de saída em vez de encerrar.
        exit_code = app(args, standalone_mode=False)
        if exit_code:
            console.print(f"[bold red]A etapa \"{title}\" falhou; demonstração interrompida.[/]")
            raise typer.Exit(exit_code)
    console.rule("[bold green]Demonstração pronta[/]")
    console.print(
        "Experimente: kb query \"O bônus de troca do Trilha acumula com o desconto PCD?\" "
        "--as-of 2026-02-15 --perfil cliente-recife"
    )


@app.command()
def ingest(
    pasta: Annotated[Path, typer.Argument(help="Pasta com manifest.yaml e os documentos.")],
    fonte: Fonte = "corpus-demo",
    data_efetiva: DataEfetiva = None,
) -> None:
    """Aponta a fonte para uma pasta e sincroniza o catálogo com ela."""
    with _session() as (settings, conn):
        _run_sync(settings, conn, pasta, fonte, _parse_date(data_efetiva))


@app.command()
def sync(fonte: Fonte = "corpus-demo", data_efetiva: DataEfetiva = None) -> None:
    """Sincroniza o catálogo com a pasta já registrada para a fonte."""
    with _session() as (settings, conn):
        pasta = Path(catalog.get_source(conn, fonte)["caminho"])
        _run_sync(settings, conn, pasta, fonte, _parse_date(data_efetiva))


def _run_sync(
    settings: Settings, conn: psycopg.Connection, pasta: Path, fonte: str, data_efetiva: date | None
) -> None:
    config = catalog.active_index_config(conn)
    embedder = components.embedder_for(settings, config)
    chunker = components.chunker_for(config, embedder)
    with console.status("Sincronizando..."):
        plan, results = sync_folder(
            conn,
            pasta,
            source_name=fonte,
            chunker=chunker,
            embedder=embedder,
            config=config,
            hoje=settings.hoje(),
            data_efetiva=data_efetiva,
        )
    _print_sync(plan, results)


def _print_sync(plan: SyncPlan, results: list[SyncResult]) -> None:
    table = Table(title=f"Sincronização de {plan.pasta} (data efetiva {plan.data_efetiva.isoformat()})")
    for column in ("Documento", "Status", "Versão", "Chunks", "Reaproveitados", "Novos embeddings", "Removidos"):
        table.add_column(column)
    for r in results:
        table.add_row(
            r.doc_key,
            f"[{_STATUS_COLORS[r.status]}]{r.status}[/]",
            str(r.versao or "-"),
            str(r.chunks),
            str(r.reaproveitados),
            str(r.novos_embeddings),
            str(r.removidos),
        )
    console.print(table)
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    console.print(" | ".join(f"{status}: {n}" for status, n in sorted(counts.items())))


@app.command()
def query(
    pergunta: Annotated[str, typer.Argument(help="Pergunta em linguagem natural.")],
    k: Annotated[int, typer.Option("-k", help="Quantidade de trechos retornados.")] = 5,
    as_of: AsOf = None,
    perfil: Annotated[
        str | None, typer.Option(help="Perfil de acesso (kb perfis). Sem perfil: sem filtro de acesso.")
    ] = None,
    pipeline: Annotated[
        str | None, typer.Option(help="Pipeline de consulta (padrão: KB_PIPELINE).")
    ] = None,
) -> None:
    """Busca os trechos mais relevantes para uma pergunta (e registra no log de consultas)."""
    with _session() as (settings, conn):
        reference = _parse_date(as_of) or components.hoje(settings, conn)
        config = load_pipeline(settings.pipelines_dir, pipeline or settings.pipeline)
        retriever = components.retriever(settings, conn, with_reranker=config.rerank.ativo)
        profile = retriever.policy.profile(perfil) if perfil else None
        hits = consultar(
            conn, retriever, pergunta, pipeline=config, as_of=reference, k=k, profile=profile,
            origem="cli",
        )

        console.print(
            f"[dim]Data de referência: {reference.isoformat()} | pipeline: {config.id} | "
            f"índice: {retriever.index.id} | perfil: {perfil or 'sem filtro de acesso'}[/]"
        )
        if not hits:
            console.print("Nenhum trecho encontrado.")
        for position, hit in enumerate(hits, start=1):
            origem = []
            if hit.rank_vetorial:
                origem.append(f"vetorial #{hit.rank_vetorial}")
            if hit.rank_textual:
                origem.append(f"textual #{hit.rank_textual}")
            console.print(
                Panel(
                    hit.conteudo,
                    title=f"{position}. {hit.caminho_secao}",
                    subtitle=f"{hit.doc_key} v{hit.versao} | {', '.join(origem)} | score {hit.score:.3f}",
                    title_align="left",
                    subtitle_align="right",
                )
            )


@app.command("simular-consultas")
def simular_consultas(
    arquivo: Annotated[Path, typer.Argument(help="YAML com a lista de consultas (perfil, pergunta).")],
    pipeline: Annotated[str | None, typer.Option(help="Pipeline (padrão: KB_PIPELINE).")] = None,
) -> None:
    """Roda uma lista de perguntas como se fossem de usuários, para alimentar o log de consultas."""
    with _session() as (settings, conn):
        consultas = yaml.safe_load(arquivo.read_text(encoding="utf-8"))["consultas"]
        config = load_pipeline(settings.pipelines_dir, pipeline or settings.pipeline)
        retriever = components.retriever(settings, conn, with_reranker=config.rerank.ativo)
        reference = components.hoje(settings, conn)
        with console.status(f"Rodando {len(consultas)} consultas em {reference.isoformat()}..."):
            for item in consultas:
                profile = retriever.policy.profile(item["perfil"]) if item.get("perfil") else None
                consultar(
                    conn, retriever, item["pergunta"], pipeline=config, as_of=reference,
                    profile=profile, origem="simulacao",
                )
        console.print(f"{len(consultas)} consultas registradas (pipeline {config.id}).")


@app.command("consultas")
def consultas_command(
    limite: Annotated[int, typer.Option(help="Quantidade de consultas mostradas.")] = 20,
) -> None:
    """Últimas consultas registradas, com o melhor trecho de cada uma."""
    with _session() as (_, conn):
        table = Table(title="Consultas recentes")
        for column in ("Quando", "Origem", "Perfil", "Pergunta", "Melhor trecho", "Score"):
            table.add_column(column)
        for row in query_logs.recent(conn, limite):
            first = row["resultados"][0] if row["resultados"] else None
            table.add_row(
                row["criado_em"].strftime("%d/%m %H:%M"),
                row["origem"],
                row["perfil"] or "-",
                row["pergunta"],
                f"{first['doc_key']} | {first['caminho_secao']}" if first else "-",
                f"{row['melhor_score']:.3f}" if row["melhor_score"] is not None else "-",
            )
        console.print(table)


@app.command()
def health(
    data: Annotated[
        str | None, typer.Option(help="Data de referência (AAAA-MM-DD). Padrão: relógio configurado.")
    ] = None,
    sem_juiz: Annotated[
        bool, typer.Option("--sem-juiz", help="Lista candidatos a conflito sem confirmar com o LLM.")
    ] = False,
    salvar: Annotated[
        bool,
        typer.Option("--salvar/--nao-salvar", help="Registra o relatório e grava em Markdown."),
    ] = True,
) -> None:
    """Relatório de saúde: revisão, vigência, duplicatas, conflitos, consistência, lacunas."""
    with _session() as (settings, conn):
        reference = _parse_date(data) or components.hoje(settings, conn)
        cfg = load_health_config(settings.saude_path)
        model = None if sem_juiz else components.chat_model(settings)
        index = catalog.active_index_config(conn)
        with console.status("Gerando relatório de saúde (o juiz de conflitos pode levar minutos)..."):
            report = build_report(conn, index, reference, cfg, model)
        _print_health(report)
        if salvar:
            health_store.save_report(conn, report.to_dict())
            path = settings.reports_dir / "saude" / f"{reference.isoformat()}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(report.to_markdown(), encoding="utf-8")
            console.print(f"[dim]Relatório registrado e gravado em {path.as_posix()}[/]")


def _print_health(report: HealthReport) -> None:
    colors = {"erro": "red", "alerta": "yellow", "info": "cyan"}
    resumo = report.resumo
    console.print(
        f"[bold]Saúde da base em {report.data_referencia.isoformat()}[/] | índice "
        f"{report.index_config_id} | "
        + " | ".join(f"[{colors[s]}]{resumo[s]} {s}[/]" for s in SEVERIDADES)
    )
    for secao in report.secoes:
        console.print(f"\n[bold]{secao.titulo}[/] [dim]{secao.descricao}[/]")
        if not secao.achados:
            console.print("  [green]Nada a apontar.[/]")
        for achado in secao.achados:
            console.print(f"  [{colors[achado.severidade]}]{achado.severidade:>6}[/] {achado.titulo}")
            if achado.detalhe:
                console.print(f"         [dim]{achado.detalhe}[/]")


@app.command()
def indices() -> None:
    """Configurações de índice (chunker + modelo), cobertura e última avaliação de cada uma."""
    with _session() as (settings, conn):
        table = Table(title="Índices")
        for column in ("Ativo", "ID", "Chunker", "Parâmetros", "Modelo", "Versões", "Embeddings",
                       "Última avaliação", "Recall@1", "MRR"):
            table.add_column(column)
        for config in catalog.list_index_configs(conn):
            coverage = catalog.index_coverage(conn, config)
            runs = health_store.latest_evals(conn, config.id)
            run = next((r for r in runs if r["pipeline"].startswith(settings.pipeline + "@")), None)
            run = run or (runs[0] if runs else None)
            table.add_row(
                "[green]●[/]" if config.ativo else "",
                config.id,
                config.chunker,
                ", ".join(f"{k}={v}" for k, v in config.chunker_params.items()),
                config.embedding_model,
                f"{coverage['versoes_indexadas']}/{coverage['versoes_total']}",
                str(coverage["embeddings"]),
                f"{run['pipeline']}, release {run['release']}" if run else "-",
                f"{run['metricas']['recall@1']:.3f}" if run else "-",
                f"{run['metricas']['mrr']:.3f}" if run else "-",
            )
        console.print(table)


@app.command()
def reindex(
    modelo: Annotated[
        str | None, typer.Option(help="Modelo de embedding (padrão: o do índice ativo).")
    ] = None,
    chunker: Annotated[
        str | None, typer.Option(help="hierarquico | recursivo | fixo | semantico (padrão: o do ativo).")
    ] = None,
    parametro: Annotated[
        list[str] | None,
        typer.Option("--parametro", "-p", help="Parâmetro do chunker, chave=valor (pode repetir)."),
    ] = None,
    pipeline: Annotated[
        str | None, typer.Option(help="Pipeline usada na avaliação (padrão: KB_PIPELINE).")
    ] = None,
    promover: Annotated[
        str, typer.Option(help="auto (se não piorar) | sim | nao")
    ] = "auto",
) -> None:
    """Reindexação blue/green: constrói um índice novo, avalia contra o ativo e decide a troca."""
    if promover not in ("auto", "sim", "nao"):
        console.print("[bold red]Erro:[/] --promover deve ser auto, sim ou nao.")
        raise typer.Exit(1)
    with _session() as (settings, conn):
        active = catalog.active_index_config(conn)
        params = dict(active.chunker_params) if chunker in (None, active.chunker) else {}
        for item in parametro or []:
            key, _, value = item.partition("=")
            params[key] = int(value) if value.lstrip("-").isdigit() else value
        with console.status("Construindo e avaliando o índice candidato (pode levar minutos)..."):
            outcome = blue_green.run(
                conn,
                settings,
                chunker_name=chunker or active.chunker,
                chunker_params=params,
                model=modelo or active.embedding_model,
                pipeline=pipeline or settings.pipeline,
                promover=promover,  # type: ignore[arg-type]
            )
        b = outcome.build
        console.print(
            f"Índice candidato [bold]{outcome.config.id}[/]: {b.versoes} versões, {b.trechos} trechos, "
            f"{b.novos_embeddings} embeddings novos em {b.segundos:.0f} s"
        )
        table = Table(title=f"Avaliação (releases {', '.join(map(str, outcome.candidato['releases']))})")
        for column in ("Índice", "Recall@1", "MRR", "Vazamentos", "Latência média"):
            table.add_column(column)
        for label, m in (("ativo: " + active.id, outcome.atual), ("candidato: " + outcome.config.id, outcome.candidato)):
            table.add_row(label, f"{m['recall@1']:.3f}", f"{m['mrr']:.3f}", str(m["vazamentos"]),
                          f"{m['latencia_media_ms']:.0f} ms")
        console.print(table)
        if outcome.promovido:
            console.print(f"[green]Promovido:[/] {outcome.config.id} é o novo índice ativo. "
                          f"Rollback: kb ativar {active.id}")
        else:
            motivos = "; ".join(outcome.decisao.motivos) or "promoção desligada (--promover nao)"
            console.print(f"[yellow]Não promovido:[/] {motivos}. O índice ativo continua {active.id}.")


@app.command()
def ativar(config_id: Annotated[str, typer.Argument(help="ID da configuração (kb indices).")]) -> None:
    """Ativa outra configuração de índice (troca manual ou rollback)."""
    with _session() as (_, conn):
        previous = catalog.active_index_config(conn)
        reindex_ops.promote(conn, config_id)
        console.print(f"Índice ativo: [bold]{config_id}[/] (antes: {previous.id})")


@app.command()
def perfis() -> None:
    """Perfis de acesso disponíveis para consultas e avaliação."""
    try:
        policy = components.access_policy(Settings())
    except KbError as exc:
        console.print(f"[bold red]Erro:[/] {exc}")
        raise typer.Exit(1) from None
    table = Table(title=f"Perfis (níveis: {' < '.join(policy.nivel.ordem)})")
    for column in ("Perfil", "Nível", "Restrições", "Descrição"):
        table.add_column(column)
    for nome, perfil in policy.perfis.items():
        restricoes = "; ".join(f"{campo}: {', '.join(v)}" for campo, v in perfil.restricoes.items())
        table.add_row(nome, perfil.nivel, restricoes or "-", perfil.descricao)
    console.print(table)


@app.command("eval")
def eval_command(
    pipeline: Annotated[
        list[str] | None,
        typer.Option(help="Pipeline a avaliar (pode repetir). Padrão: todas as pipelines."),
    ] = None,
    release: Annotated[
        int | None, typer.Option(help="Golden set da release (padrão: release atual da fonte).")
    ] = None,
    k: Annotated[int, typer.Option("-k", help="Trechos recuperados por pergunta.")] = 5,
    fonte: Fonte = "corpus-demo",
    detalhes: Annotated[bool, typer.Option(help="Mostra o resultado de cada pergunta.")] = False,
    salvar: Annotated[
        bool, typer.Option("--salvar/--nao-salvar", help="Registra a execução em eval_runs.")
    ] = True,
    estrito: Annotated[
        bool, typer.Option(help="Termina com erro se houver vazamento, proibido ou regressão.")
    ] = False,
) -> None:
    """Avalia as pipelines com o golden set e compara com a execução anterior."""
    with _session() as (settings, conn):
        names = pipeline or list_pipelines(settings.pipelines_dir)
        with console.status(f"Avaliando {', '.join(names)}..."):
            outcomes = evaluate(
                conn, settings, names, fonte=fonte, release=release, k=k, save=salvar
            )
        _print_eval(outcomes, detalhes)
        if estrito and any(o.problemas for o in outcomes):
            raise typer.Exit(1)


def _print_eval(outcomes: list[EvalOutcome], detalhes: bool) -> None:
    first = outcomes[0].report
    table = Table(
        title=f"Avaliação | release {first.release} | índice {first.index_config_id} | k={first.k}"
    )
    for column in ("Pipeline", "Recall@1", "Recall@3", f"Recall@{first.k}", "MRR",
                   "Vazamentos", "Proibidos", "Latência média"):
        table.add_column(column, justify="right" if column != "Pipeline" else "left")

    def fmt(outcome: EvalOutcome, metric: str) -> str:
        value = outcome.report.metricas[metric]
        text = f"{value:.3f}"
        if outcome.previous and metric in outcome.previous:
            delta = value - outcome.previous[metric]
            if abs(delta) > 1e-6:
                color = "green" if delta > 0 else "red"
                text += f" [{color}]({delta:+.3f})[/]"
        return text

    for outcome in outcomes:
        m = outcome.report.metricas
        table.add_row(
            outcome.report.pipeline,
            fmt(outcome, "recall@1"),
            fmt(outcome, "recall@3"),
            fmt(outcome, "recall@k"),
            fmt(outcome, "mrr"),
            f"[{'red' if m['vazamentos'] else 'green'}]{m['vazamentos']}[/]",
            f"[{'red' if m['proibidos'] else 'green'}]{m['proibidos']}[/]",
            f"{m['latencia_media_ms']:.0f} ms",
        )
    console.print(table)
    console.print(
        f"[dim]{first.metricas['perguntas']} perguntas; recall e MRR sobre as "
        f"{first.metricas['perguntas_avaliadas']} que têm trecho esperado.[/]"
    )
    for outcome in outcomes:
        for problema in outcome.problemas:
            console.print(f"[red]✗ {outcome.report.pipeline}:[/] {problema}")

    if not detalhes:
        return
    for outcome in outcomes:
        detail = Table(title=f"Detalhes: {outcome.report.pipeline}")
        for column in ("Pergunta", "Tipo", "Perfil", "Posição", "1º recuperado", "Alertas"):
            detail.add_column(column)
        for item in outcome.report.itens:
            if not item.tem_esperado:
                posicao = "-"
            elif item.posicao is None:
                posicao = "[red]não achou[/]"
            else:
                posicao = f"[{'green' if item.posicao == 1 else 'yellow'}]{item.posicao}[/]"
            alertas = [f"vazou: {v}" for v in item.vazamentos] + [f"proibido: {p}" for p in item.proibidos]
            detail.add_row(
                item.id,
                item.tipo,
                item.perfil or "-",
                posicao,
                item.recuperados[0] if item.recuperados else "-",
                "[red]" + "; ".join(alertas) + "[/]" if alertas else "",
            )
        console.print(detail)


@app.command()
def chunks(
    doc_id: Annotated[str, typer.Argument(help="ID do documento (doc_key).")],
    conteudo: Annotated[
        bool, typer.Option("--conteudo", help="Mostra o conteúdo de cada chunk.")
    ] = False,
    as_of: AsOf = None,
) -> None:
    """Mostra como um documento foi dividido em chunks."""
    with _session() as (settings, conn):
        reference = _parse_date(as_of) or components.hoje(settings, conn)
        config = catalog.active_index_config(conn)
        rows = catalog.document_chunks(conn, config.id, doc_id, reference)
        if not rows:
            console.print(f"Nenhum chunk vigente em {reference.isoformat()} para '{doc_id}'.")
            return
        if conteudo:
            for row in rows:
                console.print(
                    Panel(
                        row["conteudo"],
                        title=f"{row['ordem']}. {row['caminho_secao']}",
                        subtitle=f"{row['tipo']} | {len(row['conteudo'])} caracteres",
                        title_align="left",
                        subtitle_align="right",
                    )
                )
            return
        table = Table(title=f"{doc_id} (versão {rows[0]['versao']})")
        for column in ("#", "Seção", "Tipo", "Caracteres", "ID"):
            table.add_column(column)
        for row in rows:
            table.add_row(
                str(row["ordem"]),
                row["caminho_secao"],
                row["tipo"],
                str(len(row["conteudo"])),
                row["id"][:12],
            )
        console.print(table)


@app.command()
def versoes(doc_id: Annotated[str, typer.Argument(help="ID do documento (doc_key).")]) -> None:
    """Histórico de versões de um documento, com a vigência de cada uma."""
    with _session() as (_, conn):
        config = catalog.active_index_config(conn)
        rows = catalog.document_versions(conn, config.id, doc_id)
        if not rows:
            console.print(f"Documento '{doc_id}' não encontrado.")
            return
        document = catalog.get_document(conn, doc_id)
        status = document["status"]
        if document["aposentado_em"]:
            status += f" em {document['aposentado_em'].isoformat()}"
        table = Table(title=f"{doc_id} ({status})")
        for column in ("Versão", "Vigente de", "Até", "Revisar até", "Chunks", "Hash"):
            table.add_column(column, no_wrap=True)
        for row in rows:
            table.add_row(
                str(row["versao"]),
                row["valid_from"].isoformat(),
                row["valid_to"].isoformat() if row["valid_to"] else "-",
                row["review_by"].isoformat() if row["review_by"] else "-",
                str(row["chunks"]),
                row["content_hash"][:8],
            )
        console.print(table)


@app.command()
def stats() -> None:
    """Resumo do conteúdo do catálogo e do índice ativo."""
    with _session() as (_, conn):
        totals = catalog.stats(conn)
        config = catalog.active_index_config(conn)
        table = Table(title="Base de conhecimento", show_header=False)
        table.add_row("Documentos ativos", str(totals["documentos_ativos"]))
        table.add_row("Documentos aposentados", str(totals["documentos_aposentados"]))
        table.add_row("Versões", str(totals["versoes"]))
        table.add_row("Chunks", str(totals["chunks"]))
        table.add_row("Índice ativo", config.id)
        table.add_row("Embeddings no índice ativo", str(vectors.count(conn, config.tabela)))
        console.print(table)
