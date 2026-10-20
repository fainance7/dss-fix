-- +goose Up
-- Long-format (tidy) values unpivoted from a vector_with_kato dataset's
-- attribute table: one row per (KATO code, date). value holds numeric cells,
-- value_text holds non-numeric ones.
CREATE TABLE dataset_observations (
    id         UUID PRIMARY KEY      DEFAULT gen_random_uuid(),
    dataset_id UUID NOT NULL         REFERENCES datasets (id) ON DELETE CASCADE,
    kato_code  TEXT NOT NULL,
    date       DATE NOT NULL,
    value      DOUBLE PRECISION,
    value_text TEXT,
    created_at TIMESTAMPTZ NOT NULL  DEFAULT now()
);

CREATE UNIQUE INDEX uq_dataset_observations ON dataset_observations (dataset_id, kato_code, date);
CREATE INDEX idx_dataset_observations_dataset ON dataset_observations (dataset_id);
CREATE INDEX idx_dataset_observations_kato_date ON dataset_observations (kato_code, date);

-- +goose Down
DROP TABLE dataset_observations;
