# ADR-002: Postgres + pgvector como banco de catálogo e vetores

- **Status:** Aceito
- **Data:** 2026-10-24

## Contexto

O projeto guarda dois tipos de dado:

- **Catálogo (relacional):** fontes, documentos, versões, vigência (`valid_from`/`valid_to`), metadados de acesso, chunks, configurações de índice, logs de consulta e histórico de avaliações.
- **Vetores:** os embeddings de cada chunk, por modelo de embedding.

A maior parte do trabalho do projeto é **gerenciar o catálogo**: versionar, aposentar conteúdo, aplicar vigência e permissões, auditar mudanças e gerar relatórios de saúde. Os vetores são necessários para a busca, mas o volume é pequeno (algumas centenas a poucos milhares de chunks na demonstração).

Uma atualização de documento altera os dois tipos de dado ao mesmo tempo:

1. cria a nova versão do documento;
2. grava os chunks novos e seus embeddings;
3. fecha o `valid_to` da versão anterior;
4. aposenta (tombstone) os chunks que saíram.

A questão é se catálogo e vetores ficam no mesmo banco ou em sistemas separados.

## Decisão

Usar **um único Postgres com a extensão pgvector** para o catálogo e os vetores. A busca textual usa o full-text nativo do Postgres.

O mesmo servidor Postgres também hospeda, em um banco separado, os metadados do Airflow ([ADR-001](ADR-001-orquestracao-airflow.md)).

## Justificativas

| Necessidade | Com pgvector (um banco) | Com banco vetorial dedicado (dois sistemas) |
|---|---|---|
| Atualização consistente | Versão, chunks, embeddings e tombstones numa **única transação**: ou tudo entra, ou nada | Gravações em dois sistemas sem transação comum. Uma falha no meio deixa catálogo e busca divergentes, e exige reconciliação |
| Metadados de filtro | Ficam uma vez, na tabela `documents`. A busca faz JOIN. Mudar o acesso de um documento altera **uma linha** | Precisam ser copiados no payload de cada vetor. Mudar o acesso exige atualizar o payload de **todos os chunks** do documento |
| Consulta no tempo (`as_of`) | Filtro por data sobre `document_versions` | Datas copiadas no payload e mantidas em sincronia |
| Busca híbrida | Full-text (`tsvector` com dicionário `portuguese`) + vetor, fundidos por RRF numa única consulta SQL | Depende dos recursos de busca esparsa do banco escolhido |
| Relatório de saúde | Vencidos, inconsistências, lacunas nos logs e quase-duplicatas (vetor × vetor) em SQL sobre o mesmo banco | Cruzar dados entre dois sistemas no código da aplicação |
| Histórico de mudanças | Consulta direta sobre as versões | Igual, mas sem garantia de que os vetores refletem o mesmo estado |
| Infraestrutura | Um container de banco, compartilhado com o Airflow | Um container a mais, numa stack que já pede cerca de 8 GB de RAM |

## Benefícios

- **Consistência por construção:** catálogo e busca nunca divergem, porque são atualizados na mesma transação.
- **Uma única fonte de verdade** para metadados, vigência e permissões.
- **Controle de acesso simples e auditável:** o filtro é uma cláusula SQL sobre dados relacionais.
- **Relatórios e auditoria em SQL**, uma linguagem conhecida por qualquer pessoa de dados.
- **Menos infraestrutura:** um banco para catálogo, vetores e Airflow, com backup e operação únicos.
- **Ecossistema maduro:** migrações, ferramentas de administração e provedores gerenciados do Postgres valem também para os vetores.

## Alternativas consideradas

| Alternativa | Pontos fortes | Por que não foi escolhida |
|---|---|---|
| Postgres (catálogo) + Qdrant (vetores) | Busca filtrada muito eficiente em grande volume, quantização, sharding, aliases de coleção, vetores esparsos | Dois sistemas sem transação comum e metadados duplicados no payload. O volume do projeto não aproveita as vantagens |
| Postgres + Weaviate ou Milvus | Bancos vetoriais completos e escaláveis | Mesmo problema de consistência entre dois sistemas, com infraestrutura mais pesada |
| Elasticsearch ou OpenSearch | BM25 de referência e busca kNN no mesmo motor | Pesado (JVM) e ainda separado do catálogo relacional |
| Chroma | Muito simples de usar e embutível | Recursos limitados de filtro e operação, e também separado do catálogo |

## Consequências

### Positivas
- As estratégias de atualização (versão, tombstone, vigência, blue/green) são implementadas com garantias transacionais.
- Mudanças de permissão e vigência têm efeito imediato na busca.
- O relatório de saúde e a avaliação consultam um único banco.

### Negativas
- **Escala:** o pgvector não foi feito para dezenas de milhões de vetores com sharding horizontal.
- **Filtros muito restritivos** sobre o índice HNSW podem reduzir o recall, porque o índice devolve candidatos antes do filtro ser aplicado. As versões recentes têm *iterative index scans* (`hnsw.iterative_scan`), que diminuem esse efeito.
- **O ranking do full-text** (`ts_rank`) não é BM25 exato. Para a demonstração isso é suficiente, e existem extensões com BM25 se for necessário.
- **Dimensão fixa por coluna:** vetores de modelos com dimensões diferentes não podem compartilhar a mesma coluna indexada.
- **Sem alias de coleção nativo:** a troca do índice ativo no blue/green precisa ser implementada.

### Mitigações
- **Uma tabela de embeddings por `index_config`**, cada uma com a dimensão do seu modelo e o seu índice HNSW. A configuração ativa é marcada em `index_configs.ativo`, e a troca (e o rollback) é uma atualização transacional dessa marcação.
- **Iterative index scans** habilitados nas consultas com filtro, e a avaliação mede o recall com os filtros de acesso aplicados.
- **Interface `VectorStore`** no pacote `kb`. O resto do código não depende do pgvector, e um banco vetorial dedicado pode ser adicionado como novo adapter.

## Quando revisitar esta decisão

Migrar a busca vetorial para um banco dedicado passa a fazer sentido se ocorrer pelo menos um destes sinais:

- o volume passar de alguns milhões de vetores;
- a latência da busca filtrada (p95) ultrapassar o limite aceitável mesmo com ajuste de índice;
- for necessário escalar a busca horizontalmente, separada do banco transacional;
- o recall com filtros restritivos cair de forma consistente na avaliação.

Nesse caso, o catálogo continua no Postgres como fonte de verdade, e a sincronização com o banco vetorial passa a usar o padrão *transactional outbox*: as mudanças são registradas numa tabela dentro da mesma transação e propagadas por uma DAG do Airflow, com reconciliação periódica.

## Regras de uso

1. O catálogo é sempre a fonte de verdade. Nenhum metadado de filtro existe só no índice vetorial.
2. Toda atualização de documento (versão, chunks, embeddings, tombstones) acontece numa única transação.
3. Cada `index_config` tem a sua tabela de embeddings e o seu índice HNSW.
4. A busca aplica os filtros de acesso, status e vigência **na mesma consulta** que faz a similaridade, nunca depois.
5. O código fora do pacote `kb.store` acessa os vetores apenas pela interface `VectorStore`.
