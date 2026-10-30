from kb.parsing import normalize_text, parse_sections


def test_normalize_text_remove_bom_crlf_e_espacos_finais():
    assert normalize_text("﻿# Título  \r\n\r\nTexto \r\n\r\n") == "# Título\n\nTexto\n"


def test_secoes_seguem_a_hierarquia_de_titulos():
    text = "# Doc\n\nIntro\n\n## A\n\nTexto A\n\n### A.1\n\nTexto A1\n\n## B\n\nTexto B\n"
    paths = [section.path for section in parse_sections(text)]
    assert paths == [("Doc",), ("Doc", "A"), ("Doc", "A", "A.1"), ("Doc", "B")]


def test_titulo_sem_conteudo_nao_gera_secao():
    text = "# Doc\n\n## Vazia\n\n### Filha\n\nConteúdo\n"
    assert [s.path for s in parse_sections(text)] == [("Doc", "Vazia", "Filha")]


def test_tabelas_e_paragrafos_viram_blocos_separados():
    text = "## Preços\n\nTabela abaixo:\n| A | B |\n|---|---|\n| 1 | 2 |\nNota final.\n"
    [section] = parse_sections(text)
    assert [block.kind for block in section.blocks] == ["texto", "tabela", "texto"]
    assert section.blocks[1].text.splitlines()[0] == "| A | B |"


def test_cerquilha_dentro_de_bloco_de_codigo_nao_e_titulo():
    text = "## Exemplo\n\n```\n# comentário\n```\n"
    [section] = parse_sections(text)
    assert section.path == ("Exemplo",)
    assert "# comentário" in section.blocks[0].text
