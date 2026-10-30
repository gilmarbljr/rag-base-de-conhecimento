-- Histórico das avaliações: cada execução do golden set, por pipeline, índice e release.
CREATE TABLE eval_runs (
    id               serial PRIMARY KEY,
    pipeline         text NOT NULL,          -- nome@vN da pipeline de consulta
    index_config_id  text NOT NULL REFERENCES index_configs (id),
    release          int NOT NULL,
    k                int NOT NULL,
    metricas         jsonb NOT NULL,
    itens            jsonb NOT NULL,         -- resultado de cada pergunta
    criado_em        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX eval_runs_comparacao_idx ON eval_runs (pipeline, index_config_id, release, id DESC);
