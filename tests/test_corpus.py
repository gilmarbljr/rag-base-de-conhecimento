"""O corpus versionado precisa ser exatamente o que o gerador produz a partir dos fatos."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_corpus", ROOT / "scripts" / "generate_corpus.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("release", [1, 2, 3])
def test_corpus_versionado_esta_atualizado(tmp_path, release):
    golden_root = tmp_path / "golden_set"
    generated = _load_generator().generate(
        release, output_root=tmp_path / "corpus", golden_root=golden_root
    )
    committed = ROOT / "data" / "corpus" / f"release_{release}"

    generated_files = {p.name: p.read_bytes() for p in generated.iterdir()}
    committed_files = {p.name: p.read_bytes() for p in committed.iterdir() if p.name != ".gitkeep"}

    assert generated_files.keys() == committed_files.keys()
    stale = [name for name in generated_files if generated_files[name] != committed_files[name]]
    assert not stale, f"Corpus desatualizado, rode scripts/generate_corpus.py: {stale}"

    golden_name = f"release_{release}.yaml"
    committed_golden = (ROOT / "data" / "golden_set" / golden_name).read_bytes()
    assert (golden_root / golden_name).read_bytes() == committed_golden, (
        "Golden set desatualizado, rode scripts/generate_corpus.py"
    )


def test_formatacao_dos_valores():
    generator = _load_generator()
    assert generator.brl(112990) == "R$ 112.990,00"
    assert generator.brl(81110.4) == "R$ 81.110,40"
    assert generator.pct(1.5) == "1,5%"
    assert generator.num(15000) == "15.000"
