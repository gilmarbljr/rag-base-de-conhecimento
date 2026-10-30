"""Gera o corpus de demonstração a partir de data/facts.yaml e data/templates/.

Para cada release, escreve em data/corpus/release_<n>/ os documentos e o manifest.yaml
(registro das fontes). A saída é determinística e fica versionada no repositório, para
que ninguém precise rodar este script para usar o projeto.

Uso:
    uv run python scripts/generate_corpus.py            # todas as releases
    uv run python scripts/generate_corpus.py --release 2
"""

import argparse
import copy
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
FACTS = ROOT / "data" / "facts.yaml"
TEMPLATES = ROOT / "data" / "templates"
OUTPUT = ROOT / "data" / "corpus"
GOLDEN_OUTPUT = ROOT / "data" / "golden_set"

MANIFEST_HEADER = (
    "# Gerado por scripts/generate_corpus.py a partir de data/facts.yaml. Não edite à mão.\n"
)


def brl(value: float) -> str:
    text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {text}"


def pct(value: float) -> str:
    return f"{value:g}".replace(".", ",") + "%"


def num(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def data_br(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def deep_merge(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def generate(
    release: int,
    output_root: Path = OUTPUT,
    facts_path: Path = FACTS,
    templates: Path = TEMPLATES,
    golden_root: Path = GOLDEN_OUTPUT,
) -> Path:
    """Gera os documentos e o manifest da release, e o golden set correspondente."""
    raw = yaml.safe_load(facts_path.read_text(encoding="utf-8"))
    if release not in raw["releases"]:
        raise SystemExit(f"Release {release} não está definida em {facts_path}")
    # As mudanças se acumulam: a release N é a base mais as releases 1..N, em ordem.
    previous = sorted(r for r in raw["releases"] if r <= release)
    facts = raw["base"]
    for r in previous:
        changes = {k: v for k, v in raw["releases"][r].items() if k != "data_referencia"}
        facts = deep_merge(facts, changes)
    facts = _list_versions(facts)
    data_referencia = raw["releases"][release]["data_referencia"]

    env = Environment(
        loader=FileSystemLoader(templates),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters.update(brl=brl, pct=pct, num=num, data=data_br)

    output = output_root / f"release_{release}"
    output.mkdir(parents=True, exist_ok=True)
    for old in [*output.glob("*.md"), output / "manifest.yaml"]:
        old.unlink(missing_ok=True)

    entries = []
    for registered in raw["documentos"]:
        if release not in registered["releases"]:
            continue
        doc = registered
        for r in previous:
            doc = deep_merge(doc, registered.get("por_release", {}).get(r, {}))
        context: dict[str, Any] = {**facts, **doc.get("variaveis", {}), "doc": doc}
        # contexto: {marca: altis} expõe `marca` = marcas.altis e `marca_id` = "altis".
        for key, value in doc.get("contexto", {}).items():
            context[key] = facts[f"{key}s"][value]
            context[f"{key}_id"] = value

        arquivo = f"{doc['id']}.md"
        text = env.get_template(doc["template"]).render(**context)
        (output / arquivo).write_text(text.strip() + "\n", encoding="utf-8", newline="\n")
        entries.append(_manifest_entry(doc, arquivo))

    manifest = {"release": release, "data_referencia": data_referencia, "documentos": entries}
    (output / "manifest.yaml").write_text(
        MANIFEST_HEADER + yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )

    # O golden set usa os mesmos fatos: respostas esperadas mudam junto com os documentos.
    golden_root.mkdir(parents=True, exist_ok=True)
    golden = env.get_template("golden_set.yaml.j2").render(
        **facts, release=release, data_referencia=data_referencia
    )
    yaml.safe_load(golden)  # falha cedo se o template gerar YAML inválido
    (golden_root / f"release_{release}.yaml").write_text(
        golden.strip() + "\n", encoding="utf-8", newline="\n"
    )
    return output


def _list_versions(facts: dict) -> dict:
    """Nos fatos, as versões de cada marca são um mapa por código; os templates recebem uma lista."""
    for marca in facts["marcas"].values():
        marca["versoes"] = [{"codigo": codigo, **dados} for codigo, dados in marca["versoes"].items()]
    return facts


def _manifest_entry(doc: dict, arquivo: str) -> dict:
    entry = {
        "id": doc["id"],
        "titulo": doc["titulo"],
        "tipo": doc["tipo"],
        "arquivo": arquivo,
        "formato_final": doc["formato_final"],
        "escopo": doc["escopo"],
        "acesso": doc["acesso"],
        "vigencia": doc["vigencia"],
    }
    for optional in ("revisar_ate", "responsavel"):
        if doc.get(optional) is not None:
            entry[optional] = doc[optional]
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--release", type=int, help="Gera só esta release (padrão: todas).")
    args = parser.parse_args()
    releases = [args.release] if args.release else sorted(available_releases())
    for release in releases:
        output = generate(release)
        print(f"Corpus da release {release} gerado em {output.relative_to(ROOT)}")


def available_releases(facts_path: Path = FACTS) -> list[int]:
    return list(yaml.safe_load(facts_path.read_text(encoding="utf-8"))["releases"])


if __name__ == "__main__":
    main()
