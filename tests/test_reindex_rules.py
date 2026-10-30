from kb.embedding import FakeEmbedder
from kb.embedding.base import MODEL_PREFIXES
from kb.maintenance.reindex import decide

ATUAL = {"recall@1": 0.90, "mrr": 0.95, "vazamentos": 0, "proibidos": 0}


def test_promove_quando_nao_piora():
    assert decide({**ATUAL}, ATUAL).promover
    assert decide({**ATUAL, "recall@1": 0.95}, ATUAL).promover


def test_nao_promove_quando_piora_ou_vaza():
    worse = decide({**ATUAL, "mrr": 0.90}, ATUAL)
    assert not worse.promover
    assert worse.motivos == ["mrr cairia de 0.950 para 0.900"]
    assert not decide({**ATUAL, "vazamentos": 1}, ATUAL).promover


def test_sem_indice_ativo_avaliado_promove_se_nao_vaza():
    assert decide({**ATUAL}, None).promover


def test_prefixos_de_consulta_e_documento_por_modelo():
    embedder = FakeEmbedder()
    embedder.model = "nomic-embed-text-v2-moe"
    query, document = MODEL_PREFIXES["nomic-embed-text-v2-moe"]
    # O prefixo muda o texto embedado, então o vetor muda.
    assert embedder.embed_query("preço") == embedder.embed([query + "preço"])[0]
    assert embedder.embed_documents(["preço"]) == embedder.embed([document + "preço"])
    assert embedder.embed_query("preço") != embedder.embed(["preço"])[0]
