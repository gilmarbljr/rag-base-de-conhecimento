# ADR-005: Detecção de conflitos com heurística + juiz LLM (qwen3:4b)

- **Status:** Aceito
- **Data:** 2026-10-24

## Contexto

O relatório de saúde precisa apontar regras de documentos vigentes que se contradizem. O caso real do corpus: em fevereiro de 2026, a circular CMP-0226 diz que "o bônus de troca do Altis Trilha **não acumula** com desconto PCD", enquanto o manual PCD e um exemplo da matriz de cumulatividade dizem que o desconto PCD **pode ser combinado** com o bônus de troca.

Esse tipo de conflito é o que mais prejudica um agente: as duas regras são recuperadas, e o modelo de geração precisa adivinhar qual vale.

## Experimentos

Corpus de fevereiro (release 2, data 15/02/2026). Há 2 conflitos reais: circular × manual PCD Altis e circular × exemplo da matriz.

| Abordagem | Candidatos | Conflitos reais encontrados | Falsos positivos | Observação |
|---|---|---|---|---|
| Heurística: frases de polaridade oposta ("pode ser combinado" × "não acumula") com 3 ou mais termos em comum | 15 | 2 de 2 | 13 | Regras sobre sujeitos diferentes usam as mesmas palavras ("venda direta não acumula com PCD" × "PCD acumula com a campanha") |
| Heurística + similaridade de embeddings (bge-m3) entre as frases | 15 | — | — | Não separa: um conflito real teve 0,692 e um falso, 0,766 |
| Heurística + juiz LLM com prompt de decisão direta | 15 | 2 de 2 | 2 | Cerca de 20 s por candidato em CPU |
| Heurística + juiz LLM extraindo condições antes de decidir | 15 | **0 de 2** | 0 | Mais lento (40 a 240 s) e perdeu os conflitos reais |
| **Heurística + juiz LLM com verificação dupla (A × B e B × A)** | 15 | **2 de 2** | **0** | 8,5 min para os 15 candidatos. Só os aprovados na primeira ordem pagam a segunda chamada |

Em março (release 3, 10/03/2026), a circular de fevereiro já não está vigente: a heurística gera 7 candidatos e o juiz descarta todos, corretamente, em cerca de 1,5 min.

## Decisão

1. **Candidatos por heurística**, barata e com bom recall: frases de regra com polaridade oposta, em documentos vigentes diferentes, com 3 ou mais termos em comum. A heurística descarta pares de documentos quase duplicados e de marcas diferentes, e conta uma vez só frases idênticas repetidas em documentos diferentes.
2. **Confirmação por um juiz LLM** (`qwen3:4b` via Ollama, saída JSON estruturada, temperatura 0, sem raciocínio estendido), com o prompt de decisão direta e **verificação dupla**: o conflito só é confirmado se o modelo apontar contradição nas duas ordens das regras.
3. O juiz é opcional (`configs/saude.yaml`, `kb health --sem-juiz`). Sem ele, o relatório lista os candidatos como "não verificados".

## Por que um LLM pequeno local

- **qwen3:4b** roda em CPU (cerca de 2,6 GB), tem bom desempenho em português e é o mesmo modelo que vai gerar respostas nas próximas fases.
- A verificação é semanal e só sobre candidatos, poucas dezenas de chamadas. A latência de 20 a 40 s por candidato é aceitável numa DAG.

## Consequências

- **Positivas:** o conflito real de fevereiro aparece no relatório com a explicação do modelo, e a lista para revisão humana é curta.
- **Negativas:** o juiz pode errar nos dois sentidos. O relatório é uma fila de revisão, não uma decisão automática.
- **Mitigações:** cada conflito traz as duas frases e a explicação, para a revisão ser rápida. O teste de integração usa um juiz falso determinístico, então o fluxo é testado sem depender do modelo.
