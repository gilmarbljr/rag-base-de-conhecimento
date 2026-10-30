from kb.chunking import HierarchicalChunker, build_chunker
from kb.chunking.hierarchical import split_table, split_text
from kb.sources import load_folder

DOC = """# Política

Introdução da política.

## Prazos

O prazo é de 7 dias.

## Pagamento

Pagamento à vista ou financiado.
"""


def test_um_chunk_por_secao_com_caminho_completo():
    chunks = HierarchicalChunker().chunk("politica", "Política", DOC)
    assert [c.path_label for c in chunks] == [
        "Política",
        "Política > Prazos",
        "Política > Pagamento",
    ]
    assert chunks[1].embed_text == "[Política > Prazos]\nO prazo é de 7 dias."


def test_ids_sao_deterministicos():
    first = HierarchicalChunker().chunk("politica", "Política", DOC)
    second = HierarchicalChunker().chunk("politica", "Política", DOC)
    assert [c.id for c in first] == [c.id for c in second]


def test_alterar_uma_secao_nao_muda_o_id_das_outras():
    before = {c.path_label: c.id for c in HierarchicalChunker().chunk("politica", "Política", DOC)}
    changed = DOC.replace("7 dias", "30 dias")
    after = {c.path_label: c.id for c in HierarchicalChunker().chunk("politica", "Política", changed)}

    assert after["Política > Prazos"] != before["Política > Prazos"]
    assert after["Política"] == before["Política"]
    assert after["Política > Pagamento"] == before["Política > Pagamento"]


def test_inserir_texto_no_inicio_nao_desloca_as_outras_secoes():
    """O efeito cascata do chunking de tamanho fixo não acontece no hierárquico."""
    before = {c.path_label: c.id for c in HierarchicalChunker().chunk("politica", "Política", DOC)}
    changed = DOC.replace("Introdução da política.", "Introdução da política, revisada e ampliada.")
    after = {c.path_label: c.id for c in HierarchicalChunker().chunk("politica", "Política", changed)}

    assert after["Política > Prazos"] == before["Política > Prazos"]
    assert after["Política > Pagamento"] == before["Política > Pagamento"]


def test_tabela_grande_e_dividida_repetindo_o_cabecalho():
    header = "| Código | Preço |\n|---|---|"
    rows = "\n".join(f"| COD-{i:03d} | R$ {i * 1000},00 |" for i in range(60))
    parts = split_table(f"{header}\n{rows}", max_chars=300)

    assert len(parts) > 1
    assert all(part.startswith(header) for part in parts)
    assert all(len(part) <= 300 for part in parts)
    body = [line for part in parts for line in part.splitlines()[2:]]
    assert body == rows.splitlines()


def test_texto_longo_sem_titulos_e_dividido_em_frases():
    text = " ".join(f"Esta é a frase número {i} do parágrafo." for i in range(80))
    parts = split_text(text, max_chars=400)

    assert len(parts) > 1
    assert all(len(part) <= 400 for part in parts)
    assert all(part.endswith(".") for part in parts)


def test_secao_grande_agrupa_paragrafos_ate_o_limite():
    paragraphs = "\n\n".join(f"Parágrafo {i}. " + "texto " * 40 for i in range(10))
    chunker = HierarchicalChunker(max_chars=600)
    chunks = chunker.chunk("doc", "Doc", f"# Doc\n\n{paragraphs}\n")

    assert len(chunks) > 1
    assert all(len(c.content) <= 600 for c in chunks)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_build_chunker_recria_a_partir_dos_parametros():
    chunker = build_chunker("hierarquico", {"max_chars": 900})
    assert chunker.params == {"max_chars": 900}


def test_corpus_inteiro_respeita_o_limite_e_tem_ids_unicos(corpus_release_1):
    _, documents = load_folder(corpus_release_1)
    chunker = HierarchicalChunker(max_chars=1800)
    chunks = [c for doc in documents for c in chunker.chunk(doc.doc_key, doc.titulo, doc.content)]

    assert all(len(c.content) <= 1800 for c in chunks)
    assert len({c.id for c in chunks}) == len(chunks)
    tabelas = [c for c in chunks if c.kind == "tabela"]
    assert tabelas, "o corpus deve gerar chunks de tabela"
