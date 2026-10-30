from kb.llm import FakeChat
from kb.maintenance import conflicts


def _chunk(doc_key, conteudo, marca=None):
    return {
        "doc_key": doc_key,
        "titulo": doc_key,
        "caminho_secao": f"{doc_key} > Seção",
        "conteudo": conteudo,
        "acesso": {"visibilidade": "publico", "marca": marca},
    }


def test_frases_de_regra_e_polaridade():
    sentences = conflicts.rule_sentences(
        [
            _chunk(
                "pcd",
                "O desconto PCD pode ser combinado com o bônus de troca. "
                "Não pode ser combinado com venda direta.\n| Tabela | Sim |",
            )
        ]
    )
    assert [(s.polaridade, s.frase) for s in sentences] == [
        ("positiva", "O desconto PCD pode ser combinado com o bônus de troca."),
        ("negativa", "Não pode ser combinado com venda direta."),
    ]


def test_candidatos_de_polaridade_oposta_com_termos_em_comum():
    sentences = conflicts.rule_sentences(
        [
            _chunk("manual", "O desconto PCD pode ser combinado com o bônus de troca da campanha."),
            _chunk("circular", "Nesta campanha, o bônus de troca não acumula com desconto PCD."),
            _chunk("loja", "O horário de sábado vale até as 13h."),
        ]
    )
    [candidate] = conflicts.candidates(sentences, min_common_terms=3)
    assert {candidate.doc_a, candidate.doc_b} == {"manual", "circular"}
    assert {"bônus", "troca", "pcd", "desconto"} <= set(candidate.termos_comuns)


def test_candidatos_ignoram_duplicatas_e_marcas_diferentes():
    positive = "O desconto PCD pode ser combinado com o bônus de troca da campanha."
    negative = "Nesta campanha, o bônus de troca não acumula com desconto PCD."
    sentences = conflicts.rule_sentences(
        [
            _chunk("manual-altis", positive, marca="altis"),
            _chunk("circular-veltra", negative, marca="veltra"),
            _chunk("circular-altis", negative, marca="altis"),
        ]
    )
    assert conflicts.candidates(
        sentences, min_common_terms=3, ignore_pairs={frozenset(("manual-altis", "circular-altis"))}
    ) == []


def test_frases_identicas_em_documentos_diferentes_contam_uma_vez():
    positive = "O desconto PCD pode ser combinado com o bônus de troca da campanha."
    negative = "Nesta campanha, o bônus de troca não acumula com desconto PCD."
    sentences = conflicts.rule_sentences(
        [_chunk("manual-a", positive), _chunk("manual-b", positive), _chunk("circular", negative)]
    )
    assert len(conflicts.candidates(sentences, min_common_terms=3)) == 1


def _candidate():
    return conflicts.ConflictCandidate("a", "seção a", "frase a", "b", "seção b", "frase b", ["pcd"])


def test_juiz_confirma_so_com_contradicao_nas_duas_ordens():
    yes = {"contradicao": True, "explicacao": "contradiz"}
    no = {"contradicao": False, "explicacao": "não contradiz"}

    model = FakeChat([yes, yes])
    assert conflicts.judge(_candidate(), model).confirmado is True
    assert model.prompts[0].index('"frase a"') < model.prompts[0].index('"frase b"')
    assert model.prompts[1].index('"frase b"') < model.prompts[1].index('"frase a"')

    assert conflicts.judge(_candidate(), FakeChat([yes, no])).confirmado is False


def test_juiz_nao_faz_a_segunda_chamada_quando_a_primeira_descarta():
    model = FakeChat([{"contradicao": False, "explicacao": "não"}])
    assert conflicts.judge(_candidate(), model).confirmado is False
    assert len(model.prompts) == 1
