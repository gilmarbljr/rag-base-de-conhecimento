from datetime import date

from kb.maintenance.sync import SyncPlan, _start_of_new_version


def test_primeira_versao_comeca_na_vigencia_declarada():
    assert _start_of_new_version(date(2026, 1, 1), None, date(2026, 2, 10)) == date(2026, 1, 1)


def test_nova_vigencia_declarada_e_respeitada():
    latest = {"valid_from": date(2026, 1, 1)}
    assert _start_of_new_version(date(2026, 2, 1), latest, date(2026, 2, 10)) == date(2026, 2, 1)


def test_mudanca_sem_nova_vigencia_vale_a_partir_da_data_efetiva():
    latest = {"valid_from": date(2026, 1, 1)}
    assert _start_of_new_version(date(2026, 1, 1), latest, date(2026, 2, 10)) == date(2026, 2, 10)


def test_nova_versao_sempre_comeca_depois_da_atual():
    latest = {"valid_from": date(2026, 2, 10)}
    assert _start_of_new_version(date(2026, 1, 1), latest, date(2026, 2, 10)) == date(2026, 2, 11)


def test_plano_serializavel_para_o_orquestrador():
    plan = SyncPlan(
        fonte="corpus-demo",
        pasta="data/corpus/release_2",
        data_efetiva=date(2026, 2, 10),
        acoes={"a": "novo", "b": "inalterado", "c": "metadados"},
        aposentar=["d"],
    )
    assert plan.to_dict() == {
        "fonte": "corpus-demo",
        "pasta": "data/corpus/release_2",
        "data_efetiva": "2026-02-10",
        "acoes": {"a": "novo", "b": "inalterado", "c": "metadados"},
        "aposentar": ["d"],
        "pendentes": ["a", "c"],
    }
