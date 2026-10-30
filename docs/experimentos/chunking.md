# Experimento de chunking

Gerado por `scripts/experimento_chunking.py`. Mesmo corpus (3 releases), mesmo modelo de embedding (bge-m3) e mesmo golden set (60 perguntas, 3 releases). Trechos vigentes em 2026-03-10.

## Qualidade da busca

| Estratégia | Recall@1 (híbrida) | MRR (híbrida) | Recall@1 (com rerank) | MRR (com rerank) |
|---|---|---|---|---|
| Tamanho fixo (500, sobreposição 75) | 0.896 | 0.938 | 0.938 | 0.969 |
| Recursivo (1.800) | 0.812 | 0.880 | 0.854 | 0.904 |
| Semântico (percentil 85, máx. 1.800) | 0.875 | 0.938 | 0.896 | 0.948 |
| Hierárquico (1.800) | 0.771 | 0.882 | 0.917 | 0.958 |
| Hierárquico + fusão de fragmentos (mín. 200) | 0.729 | 0.861 | 0.917 | 0.958 |
| Hierárquico + agrupamento de seções (até 600) | 0.688 | 0.833 | 0.917 | 0.958 |
| Hierárquico + agrupamento de seções (até 1.200) | 0.792 | 0.856 | 0.875 | 0.930 |

## Tamanho e custo de manutenção

| Estratégia | Trechos vigentes | Tamanho mediano | Trechos < 120 caracteres | Embeddings no índice | Trechos novos na release 2 | Trechos novos na release 3 |
|---|---|---|---|---|---|---|
| Tamanho fixo (500, sobreposição 75) | 58 | 500 | 3 | 77 | 8 de 69 (12%) | 3 de 69 (4%) |
| Recursivo (1.800) | 20 | 1237 | 1 | 27 | 3 de 24 (12%) | 1 de 24 (4%) |
| Semântico (percentil 85, máx. 1.800) | 81 | 195 | 29 | 104 | 7 de 97 (7%) | 4 de 96 (4%) |
| Hierárquico (1.800) | 80 | 176 | 26 | 93 | 4 de 89 (4%) | 1 de 84 (1%) |
| Hierárquico + fusão de fragmentos (mín. 200) | 79 | 177 | 25 | 92 | 4 de 88 (5%) | 1 de 83 (1%) |
| Hierárquico + agrupamento de seções (até 600) | 45 | 481 | 3 | 55 | 4 de 51 (8%) | 1 de 49 (2%) |
| Hierárquico + agrupamento de seções (até 1.200) | 26 | 884 | 1 | 35 | 4 de 31 (13%) | 1 de 30 (3%) |
