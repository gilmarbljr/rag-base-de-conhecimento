# ADR-001: Orquestração dos pipelines com Apache Airflow

- **Status:** Aceito
- **Data:** 2026-10-24

## Contexto

Manter a base de conhecimento não é uma operação única. Cada ingestão ou atualização dispara uma cadeia de processos:

1. detectar quais documentos mudaram;
2. normalizar cada documento para Markdown com metadados;
3. dividir em chunks e calcular os hashes;
4. gerar embeddings dos chunks novos ou alterados;
5. gravar no índice e marcar como removido (tombstone) o que saiu;
6. avaliar a base com o golden set;
7. gerar o relatório de saúde.

Além da ingestão, existem processos periódicos (relatório de saúde semanal), processos disparados por outros (avaliação após cada atualização do índice) e processos manuais com parâmetros (reindexar com outro modelo de embedding, aplicar uma release da simulação).

Executar tudo isso com scripts encadeados funciona no começo, mas deixa sem resposta perguntas como: o que rodou ontem? Em que etapa falhou? Dá para repetir só o documento que falhou? A avaliação rodou depois da última atualização?

## Decisão

Usar o **Apache Airflow** para orquestrar todos os processos em lote: ingestão, sincronização, reindexação, avaliação, relatórios e simulação de releases.

A consulta do usuário (caminho online) **não** passa pelo Airflow. Ela é atendida pela API, que lê o mesmo banco.

## Justificativas

Só o encadeamento de etapas não justifica um orquestrador, porque uma função pode chamar a outra. O que justifica são as necessidades em volta do encadeamento, e o projeto tem todas estas:

| Necessidade | Exemplo no projeto | Como o Airflow atende |
|---|---|---|
| Execução agendada | Sincronização diária, relatório de saúde semanal | Agendamento nativo por DAG |
| Retry por etapa | O embedding falha em 1 de 20 documentos | Retry configurável por tarefa: só a tarefa que falhou é repetida |
| Paralelismo por item | Processar cada documento alterado de forma independente | Dynamic task mapping: uma tarefa por documento, criada em tempo de execução |
| Dependência entre pipelines | O índice atualizado deve disparar a avaliação | Agendamento por dados (assets): `kb_eval` roda quando `kb_sync` publica o índice |
| Decisão no fluxo | Promover o índice novo só se a avaliação melhorar | Branching entre tarefas |
| Execução manual com parâmetros | Reindexar com outro modelo, aplicar a release 2 | Parâmetros de execução na interface e na API |
| Histórico e observabilidade | Saber o que rodou, quando, quanto tempo levou e onde falhou | Interface com grafo, histórico de execuções e logs por tarefa |

## Benefícios

- **Confiabilidade:** falhas ficam isoladas por tarefa e podem ser repetidas sem reprocessar a base inteira.
- **Rastreabilidade:** todas as execuções ficam registradas, com duração, status e logs, o que complementa o histórico de versões do catálogo.
- **Automação da qualidade:** a avaliação roda sozinha após cada atualização e bloqueia regressões.
- **Visibilidade:** a interface mostra o pipeline como um grafo, o que torna o processo compreensível para quem não leu o código.
- **Operação manual segura:** reindexação e simulação são disparadas com parâmetros validados, sem acesso direto ao servidor.
- **Ferramenta padrão de mercado:** amplamente adotada em engenharia de dados, com documentação extensa, grande comunidade e muitos provedores gerenciados.
- **Separação clara entre lote e online:** o caminho de consulta continua simples e rápido, enquanto o trabalho pesado fica no orquestrador.

## Alternativas consideradas

| Alternativa | Pontos fortes | Por que não foi escolhida |
|---|---|---|
| Scripts encadeados (`simulate_timeline`) | Zero infraestrutura extra | Sem agendamento, sem retry por etapa, sem histórico nem visibilidade. Continua existindo como caminho leve, sem orquestrador |
| Cron + APScheduler | Leve e simples | Resolve só o agendamento. Retry, dependências, histórico e interface teriam de ser construídos à mão |
| Prefect | Fácil de subir, API Python moderna | Menor adoção em empresas, e a demonstração perde a referência da ferramenta mais usada no mercado |
| Dagster | O modelo de assets (corpus → chunks → índice → avaliação) combina muito bem com RAG | Menor adoção no mercado. A vantagem dos assets é parcialmente coberta pelo agendamento por dados do Airflow |

## Consequências

### Positivas
- Os processos em lote ficam observáveis, agendáveis e repetíveis.
- A avaliação automática transforma regressões de qualidade em falhas visíveis.
- O projeto demonstra a manutenção de um RAG como um pipeline de dados completo.

### Negativas
- **Mais containers e mais memória:** a stack completa pede cerca de 8 GB de RAM livres.
- **Mais tempo de setup** para quem clona o projeto.
- **Windows:** o Airflow não roda nativamente, só via Docker ou WSL.
- **Risco de acoplamento:** se a lógica for escrita dentro das DAGs, o projeto fica preso ao Airflow e difícil de testar.

### Mitigações
- **Toda a lógica fica no pacote `kb`.** As DAGs são finas e só chamam as funções, que também são usadas pela CLI, pela API e pelos testes.
- **Configuração mínima:** LocalExecutor, sem Celery nem Redis, e o banco de metadados do Airflow fica no mesmo servidor Postgres do projeto, em um banco separado.
- **Caminho sem orquestrador:** o script `simulate_timeline` reproduz a demonstração chamando as mesmas funções em sequência.
- **Testes sem Airflow:** a CI testa o pacote `kb` diretamente, e as DAGs passam só por um teste de carregamento (import sem erros).

## Regras de uso

1. Uma DAG nunca contém regra de negócio, só chamadas ao pacote `kb`.
2. Toda tarefa é idempotente: reexecutá-la produz o mesmo resultado, garantido pelos IDs determinísticos e pelo hash de conteúdo.
3. Uma tarefa por documento sempre que o processamento for independente, para isolar falhas.
4. O caminho online (consulta) nunca depende do Airflow estar no ar.
