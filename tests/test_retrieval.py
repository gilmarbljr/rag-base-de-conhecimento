from pathlib import Path

import pytest

from kb.errors import KbError
from kb.retrieval import FakeReranker, list_pipelines, load_pipeline
from kb.retrieval.fusion import reciprocal_rank_fusion

PIPELINES = Path(__file__).resolve().parents[1] / "configs" / "pipelines"


def test_rrf_favorece_itens_bem_colocados_nos_dois_rankings():
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "a"]], k=60)
    assert [item for item, _ in fused] == ["b", "a", "c"]


def test_rrf_inclui_itens_de_um_ranking_so():
    fused = dict(reciprocal_rank_fusion([["a"], ["b"]], k=60))
    assert fused["a"] == pytest.approx(1 / 61)
    assert fused["b"] == pytest.approx(1 / 61)


def test_rrf_desempata_pelo_id():
    fused = reciprocal_rank_fusion([["z", "a"], ["a", "z"]], k=60)
    assert [item for item, _ in fused] == ["a", "z"]


def test_todas_as_pipelines_do_projeto_sao_validas():
    names = list_pipelines(PIPELINES)
    assert {"vetorial", "hibrida", "hibrida-rerank"} <= set(names)
    for name in names:
        assert load_pipeline(PIPELINES, name).nome == name


def test_pipeline_inexistente():
    with pytest.raises(KbError, match="não encontrada"):
        load_pipeline(PIPELINES, "nao-existe")


def test_nome_da_pipeline_deve_ser_o_do_arquivo(tmp_path):
    (tmp_path / "a.yaml").write_text("nome: b\nversao: 1\n", encoding="utf-8")
    with pytest.raises(KbError, match="nome deve ser igual"):
        load_pipeline(tmp_path, "a")


def test_fake_reranker_mede_sobreposicao_de_palavras():
    scores = FakeReranker().rerank("bônus de troca", ["bônus de troca do Trilha", "horário da loja"])
    assert scores[0] > scores[1]
