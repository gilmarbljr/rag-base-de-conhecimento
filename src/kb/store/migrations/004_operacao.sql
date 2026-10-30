-- Operação da base: log das consultas reais e histórico dos relatórios de saúde.

CREATE TABLE query_logs (
    id               bigserial PRIMARY KEY,
    criado_em        timestamptz NOT NULL DEFAULT now(),
    origem           text NOT NULL,          -- cli | painel | simulacao
    pergunta         text NOT NULL,
    perfil           text,
    pipeline         text NOT NULL,          -- nome@vN
    index_config_id  text NOT NULL REFERENCES index_configs (id),
    as_of            date NOT NULL,
    k                int NOT NULL,
    latencia_ms      real NOT NULL,
    melhor_score     real,                   -- score do 1º trecho (escala da pipeline)
    resultados       jsonb NOT NULL          -- trechos devolvidos, na ordem
);

CREATE INDEX query_logs_criado_idx ON query_logs (criado_em DESC);

CREATE TABLE health_reports (
    id               serial PRIMARY KEY,
    criado_em        timestamptz NOT NULL DEFAULT now(),
    data_referencia  date NOT NULL,
    index_config_id  text NOT NULL REFERENCES index_configs (id),
    resumo           jsonb NOT NULL,
    secoes           jsonb NOT NULL
);
