# ADR-003: Chunking hierárquico com fusão de fragmentos

- **Status:** Aceito
- **Data:** 2026-10-25

## Contexto

A estratégia de chunking define o que a busca encontra e quanto custa manter a base. Ela foi deixada em aberto até existirem números: um golden set, avaliação automática e a linha do tempo de releases para medir o custo de atualização.

As estratégias comparadas são as mais divulgadas (tamanho fixo, recursivo, semântico, hierárquico), mais duas variantes do hierárquico criadas a partir dos achados do relatório de saúde:

- **fusão de fragmentos:** o último pedaço de uma seção dividida, se pequeno, junta-se ao anterior;
- **agrupamento de seções:** seções pequenas e inteiras, vizinhas, viram um só trecho.

A estratégia "agêntica" (um LLM decide onde cortar) ficou de fora: ela não é determinística, e IDs estáveis são a base da atualização incremental.

## Método

`scripts/experimento_chunking.py` constrói um índice candidato por estratégia (todas as versões dos documentos, modelo `bge-m3`) e mede:

- **qualidade:** recall@1 e MRR no golden set das três releases (60 perguntas, 48 com trecho esperado), nas pipelines `hibrida` e `hibrida-rerank`;
- **custo de manutenção:** quantos trechos novos (embeddings a calcular) cada release exige em relação à anterior, comparando os IDs determinísticos.

**Critério de acerto justo entre estratégias:** um trecho de uma estratégia que ignora a estrutura (fixo, recursivo, semântico) conta como resposta a uma seção somente se contiver pelo menos metade dela (ou 400 caracteres dela). Na primeira rodada, bastava encostar na seção, e isso favorecia as janelas de tamanho fixo: o recall@1 sem rerank caiu de 0,958 para 0,896 quando o critério foi corrigido.

Resultados completos: [docs/experimentos/chunking.md](../experimentos/chunking.md).

## Resultados

| Estratégia | Recall@1 sem rerank | Recall@1 com rerank | MRR com rerank | Trechos novos (release 2 / 3) |
|---|---|---|---|---|
| Tamanho fixo (500, sobreposição 75) | **0,896** | **0,938** | **0,969** | 12% / 4% |
| Semântico (percentil 85) | 0,875 | 0,896 | 0,948 | 7% / 4% |
| Recursivo (1.800) | 0,812 | 0,854 | 0,904 | 12% / 4% |
| Hierárquico (1.800) | 0,771 | 0,917 | 0,958 | **4% / 1%** |
| Hierárquico + fusão de fragmentos | 0,729 | 0,917 | 0,958 | 5% / 1% |
| Hierárquico + agrupamento (até 600) | 0,688 | 0,917 | 0,958 | 8% / 2% |
| Hierárquico + agrupamento (até 1.200) | 0,792 | 0,875 | 0,930 | 13% / 3% |

## Leitura

1. **Sem rerank, o tamanho fixo é claramente melhor** (0,896 contra 0,771 do hierárquico). Janelas de 500 caracteres com sobreposição juntam o conteúdo de seções vizinhas, e cada seção aparece em duas janelas.
2. **Com rerank, que é a pipeline de produção, a diferença cai para uma pergunta em 48** (0,938 contra 0,917). O cross-encoder corrige a maior parte dos erros de ordenação do hierárquico.
3. **O hierárquico é o mais barato de manter:** uma mudança numa seção gera trechos novos só nessa seção. No fixo, o deslocamento das janelas propaga a mudança pelo resto do documento (3 vezes mais re-embeddings na release 2).
4. **A hipótese do agrupamento não se confirmou.** Juntar seções pequenas para chegar perto do tamanho das janelas piorou a busca sem rerank. O ganho do tamanho fixo não vem só do tamanho dos trechos.
5. **A fusão de fragmentos não muda a qualidade** e elimina o fragmento de 55 caracteres que o relatório de saúde apontava na ata.

## Decisão

Manter o **chunking hierárquico, agora com fusão de fragmentos** (`min_chars=200`), junto com a pipeline `hibrida-rerank`.

Motivos:

- qualidade equivalente na pipeline de produção (diferença de uma pergunta);
- custo de manutenção de 3 a 4 vezes menor por release;
- cada trecho corresponde a uma seção do documento, o que dá citações precisas ("Manual PCD > Documentos necessários") e permite a atualização seção a seção.

A troca para a fusão de fragmentos foi feita pelo próprio fluxo blue/green (`kb reindex -p min_chars=200`), que avaliou o candidato contra o ativo antes de promovê-lo.

## Consequências

- **Se o rerank for desligado** (por latência, por exemplo), esta decisão deve ser revista: sem ele, o tamanho fixo de 500 caracteres foi o melhor resultado. A troca é uma reindexação blue/green: `kb reindex --chunker fixo -p tamanho=500 -p sobreposicao=75`.
- O golden set é pequeno (48 perguntas com trecho esperado). Diferenças de uma ou duas perguntas estão dentro da margem de ruído, e o experimento deve ser repetido quando o corpus crescer.
