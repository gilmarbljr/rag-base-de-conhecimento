-- Catálogo da base de conhecimento.
-- Os vetores ficam em uma tabela por configuração de índice (ver index_configs),
-- criada pela aplicação, porque cada modelo de embedding tem a sua dimensão.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE sources (
    id         serial PRIMARY KEY,
    nome       text NOT NULL UNIQUE,
    caminho    text NOT NULL,
    criado_em  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE documents (
    id              serial PRIMARY KEY,
    doc_key         text NOT NULL UNIQUE,  -- identidade estável, vinda do registro de fontes
    source_id       int NOT NULL REFERENCES sources (id),
    titulo          text NOT NULL,
    tipo_documento  text NOT NULL,
    escopo          text NOT NULL,
    acesso          jsonb NOT NULL DEFAULT '{}',
    metadados       jsonb NOT NULL DEFAULT '{}',
    status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    criado_em       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE document_versions (
    id            serial PRIMARY KEY,
    document_id   int NOT NULL REFERENCES documents (id),
    versao        int NOT NULL,
    content_hash  text NOT NULL,
    conteudo      text NOT NULL,
    -- Vigência de negócio, inclusiva nas duas pontas. valid_to NULL = sem data de término.
    valid_from    date NOT NULL,
    valid_to      date,
    review_by     date,
    ingerido_em   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, versao),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE TABLE embedding_models (
    id         serial PRIMARY KEY,
    nome       text NOT NULL UNIQUE,
    dimensao   int NOT NULL,
    provedor   text NOT NULL,
    criado_em  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE index_configs (
    id                  text PRIMARY KEY,
    chunker             text NOT NULL,
    chunker_params      jsonb NOT NULL,
    embedding_model_id  int NOT NULL REFERENCES embedding_models (id),
    tabela_embeddings   text NOT NULL UNIQUE,
    ativo               boolean NOT NULL DEFAULT false,
    criado_em           timestamptz NOT NULL DEFAULT now()
);

-- No máximo uma configuração ativa por vez.
CREATE UNIQUE INDEX index_configs_uma_ativa ON index_configs (ativo) WHERE ativo;

CREATE TABLE chunks (
    id               text PRIMARY KEY,  -- determinístico: documento + seção + hash do conteúdo
    document_id      int NOT NULL REFERENCES documents (id),
    caminho_secao    text NOT NULL,
    tipo             text NOT NULL CHECK (tipo IN ('texto', 'tabela', 'misto')),
    conteudo         text NOT NULL,
    texto_embedding  text NOT NULL,
    content_hash     text NOT NULL,
    tsv              tsvector GENERATED ALWAYS AS (to_tsvector('portuguese', texto_embedding)) STORED,
    criado_em        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv);

-- Quais chunks compõem cada versão de documento, em cada configuração de índice.
-- Um chunk que não muda entre versões é reaproveitado (mesmo ID, mesmo embedding).
CREATE TABLE version_chunks (
    index_config_id  text NOT NULL REFERENCES index_configs (id),
    version_id       int NOT NULL REFERENCES document_versions (id),
    chunk_id         text NOT NULL REFERENCES chunks (id),
    ordem            int NOT NULL,
    PRIMARY KEY (index_config_id, version_id, chunk_id)
);

CREATE INDEX version_chunks_chunk_idx ON version_chunks (chunk_id);
