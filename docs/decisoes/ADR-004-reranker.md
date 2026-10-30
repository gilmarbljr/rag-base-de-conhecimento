# ADR-004: Reranker servido pelo Text Embeddings Inference, com um cross-encoder pequeno

- **Status:** Aceito
- **Data:** 2026-10-24

## Contexto

A avaliação da busca (golden set da release 3, 21 perguntas, 16 com trecho esperado) mostrou que a busca vetorial e a híbrida acertam o trecho entre os 5 primeiros em todas as perguntas, mas erram o 1º lugar em cerca de 1 a cada 5. Os casos típicos:

- "Quais documentos preciso para comprar um carro PCD?": o glossário (que define "PCD") vem antes da seção "Documentos necessários" do manual.
- "Qual o preço do Altis Nexa 1.0 Turbo?": o manual PCD (que lista a versão) vem antes da tabela de preços.
- "O bônus de troca do Trilha acumula com o desconto PCD?" (fevereiro): a regra geral da matriz e dos manuais vem antes da circular de fevereiro, que é a regra vigente e diz o contrário.

Para um agente, o 1º trecho pesa muito: é o que mais influencia a resposta. Um cross-encoder lê pergunta e trecho juntos e mede a relevância com mais precisão que a comparação de embeddings.

O Ollama, usado para os embeddings, não oferece reranking. O serviço precisa rodar em CPU, numa máquina comum.

## Decisão

- Servir o reranker com o **Text Embeddings Inference (TEI)**, num container próprio (`reranker`), pelo endpoint `/rerank`.
- Usar o modelo **`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`** (118M parâmetros, multilíngue, treinado no mMARCO, que inclui português).
- A pipeline `hibrida-rerank` declara o modelo esperado. A busca confere o modelo servido (`/info`) e recusa se for outro, para que avaliações de modelos diferentes nunca sejam comparadas por engano.

## Medições

Golden set da release 3, k = 5, CPU (12 núcleos), 15 candidatos por pergunta enviados ao reranker:

| Pipeline | Recall@1 | Recall@5 | MRR | Latência média |
|---|---|---|---|---|
| vetorial | 0,812 | 1,000 | 0,896 | 172 ms |
| hibrida | 0,812 | 1,000 | 0,906 | 179 ms |
| hibrida + `mmarco-mMiniLMv2-L12-H384-v1` | **0,938** | 1,000 | **0,969** | 3,0 s |
| hibrida + `bge-reranker-v2-m3` | 0,938 | 1,000 | 0,969 | 41,8 s |

Os dois rerankers chegaram à mesma qualidade neste golden set. Cada um errou o 1º lugar em uma pergunta diferente: o mMiniLM na pergunta temporal (a tabela de preços veio em 2º) e o m3 numa paráfrase (o glossário veio em 1º).

## Alternativas consideradas

| Alternativa | Por que não foi escolhida |
|---|---|
| `BAAI/bge-reranker-v2-m3` no TEI | Modelo maior (568M) e mais forte em benchmarks, mas cerca de 14 vezes mais lento em CPU (41,8 s contra 3,0 s por pergunta), sem ganho de qualidade neste golden set. Continua sendo a opção indicada para GPU: basta trocar `KB_RERANKER_MODEL` e criar uma nova versão da pipeline |
| Reranker em processo (sentence-transformers ou fastembed) | Traria PyTorch ou ONNX Runtime para o pacote e para a imagem do Airflow. Como serviço, o reranker é trocado sem mexer no código |
| LLM como reranker (via Ollama) | Uma chamada de geração por trecho ou por lote: muito mais lento em CPU e menos estável que um cross-encoder |
| Sem rerank | Mantém o 1º lugar errado nos casos acima, incluindo o conflito em que a regra vigente perde para a regra geral |

## Consequências

- **Positivas:** o 1º trecho passa a ser o certo em 15 de 16 perguntas, e os conflitos entre regra geral e regra vigente da circular se resolvem a favor da circular.
- **Negativas:** a consulta passa de cerca de 0,2 s para cerca de 3 s em CPU, e há um container a mais.
- **Mitigações:** o rerank é uma opção da pipeline. A pipeline `hibrida` continua disponível para quando a latência importa mais que o 1º lugar, e a avaliação compara as duas a cada atualização do índice.
