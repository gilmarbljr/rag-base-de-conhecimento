# Cenário de demonstração: Assistente de RH e TI

> **Status:** cenário candidato, registrado para avaliação. Nada foi implementado ainda.

## 1. Contexto

**Empresa fictícia:** Grupo Aurora (nome provisório), com 1.200 colaboradores em três unidades (São Paulo, Recife e Curitiba) e trabalho híbrido.

**Problema:** as regras de benefícios, reembolsos, viagens, home office e TI estão espalhadas em PDFs, páginas da intranet, comunicados por e-mail e atas de comitê. Quando uma regra muda, o comunicado novo convive com a política antiga. O RH responde as mesmas perguntas toda semana, e às vezes com a regra errada.

**Solução:** uma base de conhecimento para o assistente interno "Pergunte ao RH e à TI", mantida ao longo do ano: atualizada, auditável e com acesso restrito por perfil.

**Por que este domínio:**
- Qualquer pessoa entende o assunto sem conhecimento técnico.
- As regras dependem da data ("vale o limite da data da despesa"), o que dá uso real à consulta `as_of`.
- O conteúdo mistura texto estruturado, tabelas e texto corrido.
- Não envolve dado sensível real.

## 2. Decisões tomadas

| Tema | Decisão |
|---|---|
| Controle de acesso | **Sim**, para demonstrar o filtro de metadados na busca |
| Imagens | **Não**. Apenas documentos com texto e tabelas |
| Tamanho do corpus | **O mínimo suficiente** para demonstrar cada etapa (cerca de 15 documentos) |
| Formatos | Markdown, PDF, TXT, HTML e e-mail (.eml) |

## 3. Controle de acesso

Cada documento recebe dois metadados de acesso:

| Metadado | Valores | Regra |
|---|---|---|
| `perfil_minimo` | `colaborador` < `gestor` < `rh` | O usuário vê documentos com perfil igual ou inferior ao seu |
| `unidades` | `todas`, `sp`, `rec`, `cwb` (e `bh` a partir de julho) | O usuário vê documentos de `todas` e da própria unidade |

**Personas da demonstração:**

| Persona | Perfil | Unidade |
|---|---|---|
| Ana | Colaboradora | São Paulo |
| Bruno | Gestor | Recife |
| Carla | RH | Curitiba |

**Princípios:**
- O filtro é aplicado **na busca**, antes de qualquer trecho chegar ao LLM. Nunca depois da geração.
- O golden set inclui **testes de vazamento**: perguntas sobre conteúdo restrito feitas por personas sem acesso. A métrica esperada é zero trechos restritos no contexto.

## 4. Corpus mínimo

| ID | Documento | Formato | Acesso | Linha do tempo | Demonstra |
|---|---|---|---|---|---|
| D01 | Política de Home Office | Markdown | Todos | Jan; uma seção muda em abril | Chunking hierárquico, atualização incremental |
| D02 | Política de Viagens (com tabela de diárias por cargo e cidade) | PDF | Todos | Jan; tabela muda em abril; vence em julho | Parsing de PDF, tabelas, vencimento |
| D03 | Política de Reembolso de Despesas (com tabela de limites) | PDF | Todos | Jan | Tabelas, consulta `as_of` |
| D04 | Comunicado: novo limite de hospedagem | E-mail (.eml) | Todos | Abril | Conflito com D03, vigência |
| D05 | Política de Escritórios e Convivência | Markdown | Todos | Jan | Step-back (regra geral) |
| D06 | Calendário de feriados por unidade | Markdown (tabela) | Por unidade | Jan; unidade BH entra em julho | Tabelas, filtro por unidade |
| D07 | Manual de VPN e acesso remoto (com códigos de erro) | Markdown | Todos | Jan | Busca híbrida (palavra-chave) |
| D08 | Glossário de siglas internas | Markdown | Todos | Jan | Reescrita da consulta |
| D09 | FAQ de RH (versão anterior) | Markdown | Todos | Jan; removida em abril | Chunk por par pergunta-resposta, tombstone |
| D10 | FAQ de RH (versão nova) | Markdown | Todos | Abril | Documento novo |
| D11 | Política de Uso de IA Generativa | Markdown | Todos | Abril | Documento novo |
| D12 | Ata do comitê de benefícios | TXT | Gestores | Jan | Texto corrido, controle de acesso |
| D13 | Tabela de cargos e faixas salariais | PDF | RH | Jan | Controle de acesso |
| D14 | Diretrizes de avaliação de desempenho | Markdown | Gestores | Jan | Controle de acesso |
| D15 | Cópia da Política de Home Office na intranet | HTML | Todos | Julho | Quase-duplicata |

## 5. Linha do tempo

| Release | Eventos | Etapa demonstrada |
|---|---|---|
| **Janeiro**: criação | Ingestão de D01–D03, D05–D09, D12–D14 | Pipeline completo, primeira avaliação |
| **Abril**: mudanças | D01: dias presenciais passam de 2 para 3 | Atualização incremental (só a seção alterada) |
| | D04: comunicado altera o limite de hospedagem sem alterar D03 | Detecção de conflito, vigência |
| | D02: tabela de diárias atualizada | Versionamento de tabelas |
| | D09 removida; D10 e D11 entram | Tombstone, documentos novos |
| **Julho**: manutenção | D02 passa da data de revisão sem ser revisada | Relatório de saúde |
| | D15 aparece com conteúdo quase igual a D01 | Quase-duplicatas |
| | D06: unidade BH entra no calendário | Mudança em tabela, nova unidade no controle de acesso |
| | Troca do modelo de embedding | Reindexação blue/green, comparação de métricas |

## 6. Golden set

| Tipo | Persona | Pergunta | Etapa ou estratégia |
|---|---|---|---|
| Direta | Ana | "Quantos dias por semana preciso ir ao escritório?" | Baseline. A resposta muda em abril |
| Código | Ana | "O que fazer com o erro ERR-VPN-809?" | Busca híbrida |
| Paráfrase | Ana | "Posso trabalhar de outra cidade por um mês?" | Busca vetorial |
| Tabela | Bruno | "Qual a diária de um gerente em Recife?" | Chunking de tabelas |
| Continuação | Bruno | "E para analista?" | Reescrita com histórico |
| Comparativa | Ana | "Qual a diferença entre VR e VA?" | Decomposição, glossário |
| Regra geral | Carla | "Posso levar meu cachorro ao escritório de Curitiba?" | Step-back |
| Temporal | Ana | "Gastei R$ 380 de hotel em março, recebo tudo?" | Consulta `as_of`: vale a regra anterior ao comunicado |
| Mudanças | Carla | "O que mudou nas políticas este ano?" | Histórico de versões |
| Conflito | Bruno | "Qual o limite de hospedagem?" | Vigência: regra do comunicado, com fonte citada |
| Sem resposta | Ana | "Qual é a política de bônus do ano que vem?" | Corrective RAG: "não encontrei" |
| Vencido | Ana | "Qual a diária para viagem a Curitiba?" (após julho) | Aviso de documento vencido |
| Unidade | Ana | "Quais os feriados municipais deste ano?" | Filtro por unidade: só São Paulo |
| Vazamento | Ana | "Qual a faixa salarial de Analista III?" | Controle de acesso: nenhum trecho de D13 no contexto |
| Acesso permitido | Carla | "Qual a faixa salarial de Analista III?" | Controle de acesso: responde com D13 |
| Vazamento | Ana | "O que foi decidido no último comitê de benefícios?" | Controle de acesso: nenhum trecho de D12 no contexto |

## 7. Como o cenário exercita cada etapa do pipeline

| Etapa | Onde aparece no cenário |
|---|---|
| Fontes e normalização | Cinco formatos (MD, PDF, TXT, HTML, EML) normalizados para Markdown com metadados |
| Chunking | Hierárquico (políticas), tabelas (diárias, limites, feriados, faixas), por par (FAQ), recursivo ou semântico (ata) |
| Enriquecimento | Prefixo com título e caminho da seção; siglas do glossário |
| Embedding e indexação | Troca de modelo em julho, com reindexação blue/green |
| Transformação da consulta | Continuação ("E para analista?"), siglas, step-back, decomposição |
| Recuperação | Híbrida (códigos de erro), filtros de acesso, unidade e vigência |
| Pós-recuperação | Rerank entre política, comunicado e FAQ sobre o mesmo tema |
| Geração | Citação de documento, seção e versão |
| Controle de fluxo | "Não encontrei" para perguntas sem resposta na base |
| Avaliação | Golden set por release, incluindo testes de vazamento |
| Manutenção | Vencimento, quase-duplicata, conflito, histórico de mudanças |

## 8. Geração dos dados

1. **Fatos canônicos** em `facts.yaml`: cada regra com seu valor por release. Por exemplo: `limite_hospedagem: {jan: 350, abr: 420}`.
2. **Documentos e golden set gerados a partir dos fatos.** A resposta esperada de cada pergunta vem do mesmo fato que gerou o texto, e os dois mudam juntos entre releases.
3. **Saída versionada no repositório** em `data/corpus/release_1..3/`. O visitante não precisa rodar nenhum LLM para ter os dados.

## 9. Pontos em aberto

- [ ] Nome definitivo da empresa fictícia.
- [ ] Ferramenta para gerar os PDFs a partir dos fatos.
- [ ] Ano fictício da linha do tempo.
