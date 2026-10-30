from datetime import date

import pytest

from kb.errors import KbError
from kb.sources import load_folder


def test_carrega_a_release_1(corpus_release_1):
    manifest, documents = load_folder(corpus_release_1)

    assert manifest.release == 1
    assert len(documents) == 18
    circular = next(d for d in documents if d.doc_key == "circular-altis-2026-01")
    assert circular.entry.vigencia.fim == date(2026, 1, 31)
    assert circular.entry.acesso == {"visibilidade": "publico", "marca": "altis"}


def test_hash_ignora_diferencas_de_quebra_de_linha(tmp_path):
    _write_manifest(tmp_path, ["doc-a"])
    (tmp_path / "doc-a.md").write_bytes(b"# A\r\n\r\nTexto\r\n")
    _, [crlf] = load_folder(tmp_path)

    (tmp_path / "doc-a.md").write_bytes(b"# A\n\nTexto\n")
    _, [lf] = load_folder(tmp_path)

    assert crlf.content_hash == lf.content_hash


def test_pasta_sem_manifest(tmp_path):
    with pytest.raises(KbError, match="manifest.yaml"):
        load_folder(tmp_path)


def test_ids_duplicados(tmp_path):
    _write_manifest(tmp_path, ["doc-a", "doc-a"])
    (tmp_path / "doc-a.md").write_text("# A\n", encoding="utf-8")
    with pytest.raises(KbError, match="duplicados"):
        load_folder(tmp_path)


def test_vigencia_invertida(tmp_path):
    (tmp_path / "manifest.yaml").write_text(
        "release: 1\ndocumentos:\n"
        "- {id: doc-a, titulo: A, tipo: t, arquivo: doc-a.md, formato_final: md, escopo: grupo,"
        " vigencia: {inicio: 2026-02-01, fim: 2026-01-01}}\n",
        encoding="utf-8",
    )
    with pytest.raises(KbError, match="inválido"):
        load_folder(tmp_path)


def _write_manifest(folder, ids):
    entries = "".join(
        f"- {{id: {doc_id}, titulo: T, tipo: t, arquivo: {doc_id}.md, formato_final: md,"
        f" escopo: grupo, vigencia: {{inicio: 2026-01-01}}}}\n"
        for doc_id in ids
    )
    (folder / "manifest.yaml").write_text(f"release: 1\ndocumentos:\n{entries}", encoding="utf-8")
