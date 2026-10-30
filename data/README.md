# Dados de demonstração

> **Todo o conteúdo é fictício.** O grupo de concessionárias, as lojas, as marcas, os modelos, os preços, o Programa Rota Limpa e as regras de PCD e venda direta foram inventados para a demonstração. As regras apenas se inspiram em práticas reais do setor e **não devem ser usadas como referência**.

## Como os dados são gerados

```
data/facts.yaml ──┐
                  ├──> scripts/generate_corpus.py ──> data/corpus/release_<n>/
data/templates/ ──┘                                    ├── manifest.yaml
                                                       └── <documento>.md
```

- **`facts.yaml`** guarda os fatos canônicos (preços, percentuais, prazos, campanhas) e o registro dos documentos, com metadados de vigência e acesso.
- **`templates/`** contém o texto de cada documento, com os fatos inseridos por variáveis.
- **`corpus/release_<n>/`** é a saída gerada, versionada no repositório. Não edite à mão: altere o fato ou o template e gere de novo.

Assim, quando um fato muda, todos os documentos que o citam mudam juntos, e também as respostas esperadas do golden set.

## Golden set

`golden_set/release_<n>.yaml` é gerado pelo mesmo script, a partir de `templates/golden_set.yaml.j2` e dos fatos da release. Cada pergunta declara:

- o **perfil** de acesso com que a busca roda;
- os trechos **esperados** (documento, e opcionalmente seção e versão);
- os documentos **proibidos** (por exemplo, com a vigência encerrada);
- uma **resposta de referência**, para a avaliação de respostas geradas.

Um teste confere que todo trecho esperado existe no corpus da release e é visível ao perfil da pergunta.

## Perfis de acesso

`perfis.yaml` define os níveis de visibilidade e os perfis de demonstração (cliente, vendedor e gerente), com as restrições de marca e loja.

## Releases

As mudanças se acumulam: a release N é a `base` mais as mudanças das releases 1 a N, em ordem. Cada documento pode ter metadados que mudam numa release (`por_release`), como a vigência de uma nova versão.

| Release | Data de referência | Mudanças |
|---|---|---|
| 1 | 15/01/2026 | Criação da base |
| 2 | 10/02/2026 | Reajuste da tabela Altis, nova exigência no PCD Veltra, circular de fevereiro |
| 3 | 10/03/2026 | Programa Rota Limpa sai da fonte, cópia encaminhada da circular de fevereiro |

Os documentos remetem à "tabela de preços vigente" em vez de repetir preços. Assim, um reajuste altera só a tabela de preços, como numa base real.

```bash
uv run python scripts/generate_corpus.py             # todas as releases
uv run python scripts/generate_corpus.py --release 2
```

Um teste confere se o corpus versionado está igual ao que o gerador produz.

## Formato final dos documentos

Cada documento tem um `formato_final` no manifest (PDF, e-mail, TXT ou Markdown). Na fase 1, todos são gerados em Markdown. A conversão para os formatos finais entra junto com a etapa de normalização de outros formatos.
