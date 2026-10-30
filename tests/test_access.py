from pathlib import Path

import pytest

from kb.access import AccessPolicy, load_policy
from kb.errors import KbError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def policy() -> AccessPolicy:
    return load_policy(ROOT / "data" / "perfis.yaml")


def test_nivel_superior_ve_documentos_de_niveis_inferiores(policy):
    gerente = policy.profile("gerente-campinas")
    assert policy.allows(gerente, {"visibilidade": "publico"})
    assert policy.allows(gerente, {"visibilidade": "vendedor"})
    assert policy.allows(gerente, {"visibilidade": "gerente"})


def test_cliente_nao_ve_conteudo_interno(policy):
    cliente = policy.profile("cliente-recife")
    assert policy.allows(cliente, {"visibilidade": "publico"})
    assert not policy.allows(cliente, {"visibilidade": "vendedor"})
    assert not policy.allows(cliente, {"visibilidade": "gerente"})


def test_restricoes_de_marca_e_loja(policy):
    cliente = policy.profile("cliente-recife")
    assert policy.allows(cliente, {"visibilidade": "publico", "marca": "altis"})
    assert not policy.allows(cliente, {"visibilidade": "publico", "marca": "veltra"})
    assert policy.allows(cliente, {"visibilidade": "publico", "loja": "recife"})
    assert not policy.allows(cliente, {"visibilidade": "publico", "loja": "sao-paulo"})
    # Campo vazio vale para todos: documentos do grupo, sem marca nem loja.
    assert policy.allows(cliente, {"visibilidade": "publico", "marca": None})


def test_campo_nao_restrito_no_perfil_nao_filtra(policy):
    gerente = policy.profile("gerente-campinas")  # restrito só por loja
    assert policy.allows(gerente, {"visibilidade": "publico", "marca": "veltra"})
    assert not policy.allows(gerente, {"visibilidade": "publico", "loja": "recife"})


def test_documento_sem_nivel_e_publico_e_nivel_desconhecido_e_bloqueado(policy):
    cliente = policy.profile("cliente-recife")
    gerente = policy.profile("gerente-campinas")
    assert policy.allows(cliente, {})
    assert not policy.allows(gerente, {"visibilidade": "diretoria"})


def test_sem_perfil_nao_ha_filtro(policy):
    assert policy.allows(None, {"visibilidade": "gerente", "marca": "veltra"})


def test_perfil_inexistente(policy):
    with pytest.raises(KbError, match="não existe"):
        policy.profile("diretor")


def test_perfil_com_nivel_desconhecido_e_invalido(tmp_path):
    path = tmp_path / "perfis.yaml"
    path.write_text(
        "nivel: {campo: visibilidade, ordem: [publico]}\n"
        "perfis: {x: {nivel: gerente}}\n",
        encoding="utf-8",
    )
    with pytest.raises(KbError, match="inválida"):
        load_policy(path)
