-- Data em que o documento saiu da fonte (tombstone). O histórico continua consultável por as_of.
ALTER TABLE documents ADD COLUMN aposentado_em date;
