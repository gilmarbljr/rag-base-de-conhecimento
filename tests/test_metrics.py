import pytest

from kb.evaluation import ItemResult, compute_metrics, find_regressions


def _item(posicao, tem_esperado=True, vazamentos=(), proibidos=(), latencia=10.0):
    return ItemResult(
        id="x", tipo="t", perfil=None, tem_esperado=tem_esperado, posicao=posicao,
        recuperados=[], vazamentos=list(vazamentos), proibidos=list(proibidos),
        latencia_ms=latencia,
    )


def test_recall_e_mrr_consideram_so_perguntas_com_esperado():
    itens = [_item(1), _item(3), _item(None), _item(None, tem_esperado=False)]
    m = compute_metrics(itens, k=5)
    assert m["perguntas"] == 4
    assert m["perguntas_avaliadas"] == 3
    assert m["recall@1"] == pytest.approx(1 / 3, abs=1e-4)
    assert m["recall@3"] == pytest.approx(2 / 3, abs=1e-4)
    assert m["mrr"] == pytest.approx((1 + 1 / 3) / 3, abs=1e-4)


def test_vazamentos_e_proibidos_contam_em_qualquer_pergunta():
    itens = [_item(None, tem_esperado=False, vazamentos=["a", "b"], proibidos=["c"])]
    m = compute_metrics(itens, k=5)
    assert (m["vazamentos"], m["proibidos"]) == (2, 1)


def test_regressoes():
    previous = {"recall@1": 0.8, "recall@3": 0.9, "recall@k": 0.9, "mrr": 0.85,
                "vazamentos": 0, "proibidos": 0}
    same = dict(previous)
    assert find_regressions(same, previous) == []

    worse = {**previous, "recall@1": 0.7}
    assert find_regressions(worse, previous) == ["recall@1 caiu de 0.800 para 0.700"]

    leaking = {**previous, "vazamentos": 1}
    assert "vazado" in find_regressions(leaking, None)[0]
