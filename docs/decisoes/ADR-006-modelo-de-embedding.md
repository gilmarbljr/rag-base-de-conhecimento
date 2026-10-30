# ADR-006: Troca do modelo de embedding para nomic-embed-text-v2-moe

- **Status:** Aceito
- **Data:** 2026-10-25

## Contexto

O índice inicial usava o `bge-m3` (1024 dimensões, até 8.192 tokens). O cenário de demonstração previa, no mês 3, a troca do modelo de embedding pela reindexação blue/green. O candidato é o `nomic-embed-text-v2-moe`: multilíngue, 768 dimensões, até 512 tokens e treinado com prefixos de consulta e documento (`search_query:` / `search_document:`), que o projeto aplica automaticamente por modelo.

## Execução

`kb reindex --modelo nomic-embed-text-v2-moe` construiu o índice candidato (22 versões, 101 trechos, 92 embeddings em 44 s) e avaliou candidato e ativo com o golden set das três releases na pipeline de produção (`hibrida-rerank`). O resultado foi um empate, e a regra "promove se não piorar" fez a troca.

## O rerank mascara a diferença entre embeddings

O cross-encoder reordena os mesmos candidatos, então diferenças na qualidade dos embeddings quase não aparecem na pipeline com rerank. Para avaliar o modelo em si, os dois índices foram comparados também sem rerank:

| Modelo | Vetorial: recall@1 / MRR | Híbrida: recall@1 / MRR | Com rerank: recall@1 / MRR | Latência (vetorial) |
|---|---|---|---|---|
| `bge-m3` | 0,792 / 0,889 | 0,729 / 0,861 | 0,917 / 0,958 | 183 ms |
| `nomic-embed-text-v2-moe` | **0,875 / 0,929** | **0,812 / 0,901** | 0,917 / 0,958 | **164 ms** |

## Decisão

Manter o `nomic-embed-text-v2-moe` como modelo do índice ativo:

- melhor busca vetorial (+8 pontos de recall@1), o que deixa o sistema menos dependente do rerank;
- vetores menores (768 contra 1024 dimensões) e consultas um pouco mais rápidas;
- qualidade igual na pipeline de produção.

O índice com `bge-m3` continua no banco: `kb ativar hierarquico-bge-m3-d658cc` faz o rollback.

## Consequências

- **Limite de 512 tokens:** trechos muito longos são truncados no embedding. O maior trecho atual (a ata, com 1.851 caracteres) fica perto do limite. Se o corpus passar a ter seções longas, o `max_chars` do chunker deve ser reduzido.
- **O modelo do índice vem do catálogo, não do ambiente.** Depois da troca, `KB_EMBEDDING_MODEL` só define o modelo do primeiro índice (`kb init`). Consultas, sincronização e DAGs usam o modelo do índice ativo.
- **Achado para a próxima iteração:** com os dois modelos, a busca híbrida ficou pior que a só vetorial. A consulta textual com "OU" entre os termos da pergunta traz trechos que só repetem palavras comuns. Opções: dar peso menor ao ranking textual no RRF ou usar a busca textual só quando a pergunta tem códigos (como "CMP-0126").
- **A avaliação de troca de modelo deve olhar mais de uma pipeline.** A regra de promoção continua sendo "não piorar na pipeline de produção", mas a comparação sem rerank passa a fazer parte da análise registrada.
