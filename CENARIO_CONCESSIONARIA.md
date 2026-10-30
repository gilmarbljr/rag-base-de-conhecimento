# Cenário de demonstração: Assistente de Vendas de Concessionárias

> **Status:** cenário escolhido. A release 1 está implementada em `data/` (fatos, templates e corpus gerado).

## 1. Contexto

**Grupo fictício:** Grupo Horizonte (nome provisório), com três lojas:

| Loja | Cidade | O que vende |
|---|---|---|
| Loja Altis | Recife | Veículos novos da marca Altis |
| Loja Veltra | São Paulo | Veículos novos da marca Veltra |
| Loja Seminovos | Campinas | Seminovos multimarca |

**Problema:** as regras comerciais vêm de várias origens: políticas do grupo, manuais e circulares das montadoras, tabelas de preço, campanhas mensais, regras de PCD, venda direta e programas governamentais. Elas mudam todo mês, valem por períodos curtos e se combinam de formas que nem os vendedores dominam. Um cliente recebe condições de outra marca, uma campanha encerrada é oferecida ou um desconto que não pode ser somado é prometido.

**Solução:** uma base de conhecimento para o assistente de vendas. Ela atende clientes e equipe comercial, respeita a vigência de cada regra e mostra a cada pessoa apenas o conteúdo da sua loja, marca e perfil.

**Por que este domínio:**
- A dinâmica temporal é forte: campanhas mensais, reajustes de tabela, programas que encerram.
- O controle de acesso tem várias dimensões: perfil, marca e loja, com precedência entre regras.
- Há muitas perguntas que cruzam documentos (cumulatividade de condições, elegibilidade).

## 2. Decisões tomadas

| Tema | Decisão |
|---|---|
| Controle de acesso | **Sim**, para demonstrar o filtro de metadados na busca |
| Imagens | **Não**. Apenas documentos com texto e tabelas |
| Tamanho do corpus | **O mínimo suficiente** para demonstrar cada etapa (18 documentos na release 1) |
| Formatos | Markdown, PDF, TXT e e-mail (.eml) |

## 3. Cuidados com o conteúdo

- **Marcas fictícias** (Altis, Veltra). Regras inventadas não podem ser atribuídas a montadoras reais.
- **Regras de PCD, venda direta e programas governamentais são fictícias**, apenas inspiradas nas reais. O README deve avisar que os valores e critérios não são oficiais, porque a legislação real muda e não pode parecer fonte de consulta.

## 4. Controle de acesso

Cada documento recebe três metadados:

| Metadado | Valores | Exemplo |
|---|---|---|
| `escopo` | `grupo` > `marca` > `loja` | Política de seminovos (grupo), circular da Altis (marca), horário da loja de Recife (loja) |
| `marca` / `loja` | Altis, Veltra; Recife, São Paulo, Campinas | — |
| `visibilidade` | `publico` < `vendedor` < `gerente` | Campanha (público), roteiro de negociação (vendedor), alçada de desconto (gerente) |

**Regra de exemplo:** o cliente da Loja Altis vê o conteúdo público do grupo, o conteúdo público da marca Altis e o conteúdo público da loja de Recife. Ele nunca vê conteúdo da Veltra nem informações de margem e desconto.

**Personas da demonstração:**

| Persona | Perfil | Escopo |
|---|---|---|
| Cliente | Público | Loja Altis (Recife) |
| Vendedor | Vendedor | Loja Veltra (São Paulo) |
| Gerente | Gerente | Loja Seminovos (Campinas) |

**Princípios:**
- O filtro é aplicado **na busca**, antes de qualquer trecho chegar ao LLM. Nunca depois da geração.
- **Precedência:** a regra mais específica complementa a mais geral (loja > marca > grupo), mas não pode contradizê-la. Uma contradição é tratada como conflito e aparece no relatório de saúde.
- O golden set inclui **testes de vazamento** por marca e por perfil. A métrica esperada é zero trechos restritos no contexto.

## 5. Tipos de negócio e restrições

| Tipo | Regras representadas no corpus |
|---|---|
| Novos (varejo) | Tabela de preços por versão, campanhas mensais, bônus de troca |
| Seminovos | Garantia por idade e quilometragem, critérios de avaliação do usado, laudo cautelar |
| Venda direta (CNPJ) | Elegibilidade (tempo de CNPJ, atividade, frota mínima), documentos exigidos, restrição de revenda |
| PCD | Elegibilidade, documentação, teto de valor do veículo, versões elegíveis, prazo mínimo para revenda |
| Programa governamental de incentivo | Vigência, verba limitada, modelos participantes |
| Cumulatividade | Quais condições podem ser somadas (por exemplo, campanha + bônus de troca sim, campanha + venda direta não) |

## 6. Corpus mínimo

| ID | Documento | Escopo / visibilidade | Formato | Linha do tempo | Demonstra |
|---|---|---|---|---|---|
| C01 | Política comercial do grupo | Grupo / público | PDF | Mês 1 | Chunking hierárquico, parsing de PDF |
| C02 | Manual de vendas PCD da Altis | Marca / público | PDF | Mês 1 | Regras com restrições |
| C03 | Manual de vendas PCD da Veltra | Marca / público | PDF | Mês 1; uma seção muda no mês 2 | Mesma pergunta com resposta diferente por marca, atualização incremental |
| C04 | Regras de venda direta para CNPJ (uma seção por marca) | Grupo / vendedor | Markdown | Mês 1 | Elegibilidade, acesso por perfil |
| C05 | Programa Rota Limpa (programa governamental fictício) | Grupo / público | Markdown | Mês 1; encerrado no mês 3 | Vigência, tombstone |
| C06 | Tabela de preços da Altis | Marca / público | PDF (tabela) | Mês 1; reajuste no mês 2 | Tabelas, versionamento, `as_of` |
| C07 | Tabela de preços da Veltra | Marca / público | PDF (tabela) | Mês 1; vence no mês 3 | Tabelas, filtro por marca, vencimento |
| C08 | Circular de campanhas da Altis (mês 1) | Marca / público | E-mail (.eml) | Mês 1; expira no mês 2 | Vigência curta |
| C09 | Circular de campanhas da Veltra (mês 1) | Marca / público | E-mail (.eml) | Mês 1; expira no mês 2 | Vigência curta |
| C10 | Circular de campanhas da Altis CMP-0226 (mês 2): desconto à vista no Nexa 1.0 Manual e bônus de troca no Trilha que **não acumula com PCD** | Marca / público | E-mail (.eml) | Mês 2 | Documento novo, conflito com a matriz de cumulatividade |
| C11 | Matriz de cumulatividade de condições | Grupo / vendedor | Markdown (tabela) | Mês 1 | Pergunta que cruza documentos |
| C12 | Política de seminovos e garantia | Loja Seminovos / público | Markdown | Mês 1 | Hierárquico, tabela de garantia |
| C13 | Critérios de avaliação de usados | Grupo / vendedor | Markdown (tabela) | Mês 1 | Tabelas, acesso por perfil |
| C14 | Alçadas de desconto e margem mínima | Grupo / gerente | PDF | Mês 1 | Controle de acesso (teste de vazamento) |
| C15 | Informações das lojas: um documento por loja (Recife, São Paulo, Campinas) | Loja / público | Markdown | Mês 1 | Filtro por loja |
| C16 | Ata da reunião comercial | Loja / vendedor | TXT | Mês 1 | Texto corrido |
| C17 | Glossário do setor (PCD, VD, F&I, CRLV, faturamento direto) | Grupo / público | Markdown | Mês 1 | Reescrita da consulta |
| C18 | Cópia da circular da Altis (mês 2) encaminhada como PDF | Marca / público | PDF | Mês 3 | Quase-duplicata |

## 7. Linha do tempo

| Release | Eventos | Etapa demonstrada |
|---|---|---|
| **Mês 1**: criação | Ingestão de C01–C09, C11–C17 | Pipeline completo, primeira avaliação |
| **Mês 2** (data efetiva 10/02/2026): virada do mês | C08 e C09 **expiram sozinhas** pelo `valid_to`, sem reingestão | Vigência por metadado |
| | C10 entra e muda uma regra de cumulatividade | Documento novo, conflito com C11 |
| | C06: reajuste da tabela de preços, vigente a partir de 01/02 (os demais documentos remetem à "tabela vigente", então só a tabela muda) | Versionamento de tabelas, `as_of` |
| | C03: passa a exigir laudo emitido há no máximo 12 meses (uma seção) | Atualização incremental: 7 de 8 chunks reaproveitados |
| **Mês 3** (data efetiva 10/03/2026): manutenção | C05: programa encerrado ("verba esgotada") | Tombstone, resposta "não está mais disponível" |
| | C07 passa da data de revisão sem atualização | Relatório de saúde |
| | C18 chega com conteúdo quase igual a C10 | Quase-duplicata |
| | Troca do modelo de embedding | Reindexação blue/green, comparação de métricas |

## 8. Golden set

| Tipo | Persona | Pergunta | Etapa ou estratégia |
|---|---|---|---|
| Direta | Cliente | "Qual o preço do Altis Nexa 1.0 Turbo?" | Chunking de tabelas |
| Código | Vendedor | "A campanha CMP-0126 vale para a versão NX-T AT?" | Busca híbrida |
| Paráfrase | Cliente | "Tenho deficiência, consigo desconto no carro?" | Busca vetorial |
| Continuação | Cliente | "E quais documentos preciso levar?" | Reescrita com histórico |
| Comparativa | Vendedor | "Para frota, compensa mais o modelo A ou o B na venda direta?" | Decomposição |
| Regra geral | Cliente | "Posso comprar como PCD no nome do meu filho, se eu for o condutor?" | Step-back (elegibilidade) |
| Multi-documento | Vendedor | "Cliente PJ pode somar a campanha do mês com a venda direta?" | Matriz de cumulatividade + circular |
| Temporal | Vendedor | "Fechei o pedido no dia 28 do mês passado, vale o preço antigo?" | `as_of` pela data do pedido |
| Expirado | Cliente | "Ainda tem a campanha de taxa zero?" (no mês 2) | Vigência: "encerrou em…" |
| Encerrado | Cliente | "Posso entrar no programa do governo?" (no mês 3) | Tombstone com explicação |
| Precedência | Vendedor | "Qual o bônus de troca?" (a marca define, a loja complementa) | Regra mais específica, citando as duas fontes |
| Mudanças | Gerente | "O que mudou nas condições deste mês?" | Histórico de versões |
| Vencido | Vendedor | "Qual o preço do Veltra Sport?" (no mês 3) | Aviso de documento vencido |
| Vazamento por marca | Cliente | "Quanto custa o Veltra Sport?" | Nenhum trecho da Veltra no contexto |
| Vazamento por perfil | Cliente | "Qual o desconto máximo que o vendedor pode dar?" | Nenhum trecho de C14 no contexto |
| Acesso permitido | Gerente | "Qual minha alçada de desconto para seminovos acima de 5 anos?" | Acesso gerencial |
| Sem resposta | Cliente | "Vocês vendem motos?" | Corrective RAG: "não encontrei" |

## 9. Como o cenário exercita cada etapa do pipeline

| Etapa | Onde aparece no cenário |
|---|---|
| Fontes e normalização | Quatro formatos (MD, PDF, TXT, EML) normalizados para Markdown com metadados |
| Chunking | Hierárquico (manuais e políticas), tabelas (preços, cumulatividade, garantia, avaliação), recursivo ou semântico (ata) |
| Enriquecimento | Prefixo com marca, documento e seção; siglas do glossário |
| Embedding e indexação | Troca de modelo no mês 3, com reindexação blue/green |
| Transformação da consulta | Continuação, siglas, step-back, decomposição |
| Recuperação | Híbrida (códigos de campanha e versão), filtros de escopo, visibilidade e vigência |
| Pós-recuperação | Rerank entre manual, circular e matriz sobre o mesmo tema |
| Geração | Citação de documento, seção e versão; precedência entre escopos |
| Controle de fluxo | "Não encontrei" para perguntas fora da base |
| Avaliação | Golden set por release, incluindo testes de vazamento por marca e perfil |
| Manutenção | Expiração, encerramento, vencimento, quase-duplicata, conflito, resumo de mudanças |

## 10. Ideias de demonstração

1. **Painel "por que este trecho foi excluído?"** Para cada consulta, todos os candidatos com o motivo da exclusão: sem permissão, vencido, substituído por versão nova ou baixa relevância.
2. **Mesma pergunta, três personas lado a lado.** "Quais as condições para PCD?" feita pelo cliente, pelo vendedor e pelo gerente.
3. **Seletor de data (`as_of`)** na interface, mostrando campanhas e preços entrando e saindo de vigência.
4. **Virada de mês sem reingestão.** Avançar a data simulada e mostrar as campanhas expirando só pelo `valid_to`.
5. **Precedência explícita de regras.** Um conflito real (loja contrariando a marca) aparece no relatório de saúde.
6. **Testes de vazamento como bloqueio na CI.** O build falha se um trecho restrito aparecer no contexto de uma persona sem acesso.
7. **Resumo "o que mudou este mês"** para a equipe de vendas, gerado a partir do histórico de versões.
8. **Lacunas a partir das perguntas reais.** Perguntas frequentes sem boa resposta indicam qual documento falta na base.

## 11. Geração dos dados

1. **Fatos canônicos** em `data/facts.yaml`: os valores da base mais o que muda em cada release. Por exemplo, o preço de cada versão fica em `marcas.altis.versoes`, e o reajuste do mês 2 entra em `releases.2`.
2. **Documentos e golden set gerados a partir dos fatos.** A resposta esperada de cada pergunta vem do mesmo fato que gerou o texto, e os dois mudam juntos entre releases.
3. **Saída versionada no repositório** em `data/corpus/release_1..3/`. O visitante não precisa rodar nenhum LLM para ter os dados.

## 12. Pontos em aberto

- [ ] Nomes definitivos do grupo, das marcas e dos modelos.
- [ ] Regra de precedência final entre grupo, marca e loja.
- [ ] Ferramenta para gerar os PDFs a partir dos fatos.
- [x] Linha do tempo: janeiro (mês 1), fevereiro (mês 2) e março (mês 3) de 2026.
