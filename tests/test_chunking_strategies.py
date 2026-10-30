import pytest

from kb.chunking import HierarchicalChunker, build_chunker
from kb.chunking.flat import FixedSizeChunker, RecursiveChunker, SemanticChunker, flatten
from kb.embedding import FakeEmbedder

DOC = (
    "# Manual\n\nIntrodução do manual.\n\n"
    "## Prazos\n\n" + "O prazo de entrega é de cinco dias úteis. " * 12 + "\n\n"
    "## Pagamento\n\n" + "O pagamento pode ser à vista ou financiado. " * 12 + "\n"
)


def test_texto_corrido_registra_a_posicao_de_cada_secao():
    flat, spans = flatten("Manual", DOC)
    paths = [path for _, _, path in spans]
    assert paths == [("Manual",), ("Manual", "Prazos"), ("Manual", "Pagamento")]
    start, end, _ = spans[1]
    assert flat[start:end].startswith("Prazos\n\nO prazo de entrega")


def test_tamanho_fixo_com_sobreposicao():
    chunks = FixedSizeChunker(tamanho=200, sobreposicao=50).chunk("doc", "Manual", DOC)
    assert len(chunks) > 3
    assert all(len(c.content) <= 200 for c in chunks)
    # A sobreposição repete o fim de uma janela no começo da seguinte.
    assert chunks[0].content[-30:].strip() in chunks[1].content


def test_trechos_planos_registram_as_secoes_que_cobrem():
    chunks = FixedSizeChunker(tamanho=600, sobreposicao=0).chunk("doc", "Manual", DOC)
    assert "Manual > Prazos" in chunks[0].path_label
    assert any("Manual > Pagamento" in c.path_label for c in chunks)
    assert all(c.embed_text.startswith("[Manual]\n") for c in chunks)


def test_recursivo_respeita_o_limite():
    chunks = RecursiveChunker(max_chars=300).chunk("doc", "Manual", DOC)
    assert len(chunks) > 1
    assert all(len(c.content) <= 300 for c in chunks)


def test_semantico_e_deterministico_e_respeita_o_limite():
    chunker = SemanticChunker(FakeEmbedder(), percentil=50, max_chars=400)
    first = chunker.chunk("doc", "Manual", DOC)
    second = chunker.chunk("doc", "Manual", DOC)
    assert [c.id for c in first] == [c.id for c in second]
    assert len(first) > 1
    assert all(len(c.content) <= 450 for c in first)  # uma frase pode passar um pouco do limite


def test_semantico_precisa_de_embedder():
    with pytest.raises(ValueError, match="embedder"):
        build_chunker("semantico", {"percentil": 85})


def test_hierarquico_funde_o_fragmento_final_da_secao():
    # Um parágrafo que quase enche o limite e um fragmento que não cabe junto dele.
    text = "# Ata\n\n" + ("Frase da reunião. " * 16).strip() + "\n\nPróxima reunião."
    without = HierarchicalChunker(max_chars=300).chunk("ata", "Ata", text)
    merged = HierarchicalChunker(max_chars=300, min_chars=100).chunk("ata", "Ata", text)
    assert len(without[-1].content) < 100
    assert len(merged) == len(without) - 1
    assert merged[-1].content.endswith("Próxima reunião.")


def test_agrupamento_junta_secoes_pequenas_inteiras():
    text = (
        "# Loja\n\nConcessionária do grupo.\n\n## Endereço\n\nAv. das Palmeiras, 1200.\n\n"
        "## Horário\n\nSegunda a sexta, 8h às 18h.\n\n## Política longa\n\n" + "Regra extensa. " * 60
    )
    plain = HierarchicalChunker(max_chars=1800).chunk("loja", "Loja", text)
    grouped = HierarchicalChunker(max_chars=1800, agrupar_ate=300).chunk("loja", "Loja", text)

    assert len(plain) == 4
    assert len(grouped) == 2
    first = grouped[0]
    assert first.path_label == "Loja | Loja > Endereço | Loja > Horário"
    assert "Endereço\n\nAv. das Palmeiras" in first.content  # cada seção leva o seu título
    assert first.embed_text.startswith("[Loja]\n")
    # A seção grande não entra no grupo e mantém o mesmo ID da versão sem agrupamento.
    assert grouped[1].id == plain[3].id


def test_parametros_do_hierarquico_mantem_o_id_das_configuracoes_antigas():
    assert HierarchicalChunker(max_chars=1800).params == {"max_chars": 1800}
    assert HierarchicalChunker(max_chars=1800, min_chars=200).params == {
        "max_chars": 1800, "min_chars": 200
    }
