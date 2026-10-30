"""O golden set precisa ser coerente com o corpus e com a política de acesso.

Uma pergunta cujo trecho esperado não existe, ou que o perfil não pode ver, mediria um
problema do golden set, não da busca.
"""

from pathlib import Path

import pytest

from kb.access import load_policy
from kb.chunking import HierarchicalChunker
from kb.evaluation import load_golden_set
from kb.sources import load_folder

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("release", [1, 2, 3])
def test_golden_set_coerente_com_corpus_e_perfis(release):
    golden = load_golden_set(ROOT / "data" / "golden_set" / f"release_{release}.yaml")
    policy = load_policy(ROOT / "data" / "perfis.yaml")
    manifest, documents = load_folder(ROOT / "data" / "corpus" / f"release_{release}")
    chunker = HierarchicalChunker(max_chars=1800)
    sections = {
        doc.doc_key: [c.path_label for c in chunker.chunk(doc.doc_key, doc.titulo, doc.content)]
        for doc in documents
    }
    acesso = {entry.id: entry.acesso for entry in manifest.documentos}

    assert golden.release == release
    assert golden.data_referencia == manifest.data_referencia
    for item in golden.perguntas:
        profile = policy.profile(item.perfil) if item.perfil else None
        for expected in item.esperado:
            assert expected.documento in sections, f"{item.id}: documento inexistente"
            if expected.secao:
                assert any(expected.secao in path for path in sections[expected.documento]), (
                    f"{item.id}: seção '{expected.secao}' não existe em {expected.documento}"
                )
            assert policy.allows(profile, acesso[expected.documento]), (
                f"{item.id}: o perfil {item.perfil} não pode ver {expected.documento}"
            )


def test_perguntas_de_vazamento_existem_em_todas_as_releases():
    for release in (1, 2, 3):
        golden = load_golden_set(ROOT / "data" / "golden_set" / f"release_{release}.yaml")
        assert {"vazamento-alcadas", "vazamento-outra-marca"} <= {i.id for i in golden.perguntas}
