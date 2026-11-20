-- +goose Up
-- Dataset groups: "одноимённые" datasets versioned by period. A group may carry a
-- shared SLD used as a fallback for its datasets.
CREATE TABLE dataset_groups (
    id               UUID PRIMARY KEY      DEFAULT gen_random_uuid(),
    code             VARCHAR(255) NOT NULL UNIQUE,
    name             VARCHAR(255) NOT NULL,
    description      TEXT         NOT NULL DEFAULT '',
    sld_storage_key  TEXT,
    sld_filename     VARCHAR(255),
    sld_content_type VARCHAR(255),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

ALTER TABLE datasets ADD COLUMN dg_id      UUID REFERENCES dataset_groups (id) ON DELETE RESTRICT;
ALTER TABLE datasets ADD COLUMN start_date DATE;
ALTER TABLE datasets ADD COLUMN end_date   DATE;
ALTER TABLE datasets ADD CONSTRAINT chk_datasets_period
    CHECK (start_date IS NULL OR end_date IS NULL OR end_date >= start_date);
CREATE INDEX idx_datasets_dg_id_end_date ON datasets (dg_id, end_date DESC NULLS LAST, created_at DESC);
CREATE INDEX idx_datasets_start_date ON datasets (start_date);
CREATE INDEX idx_datasets_end_date   ON datasets (end_date);

-- Period columns are renamed everywhere: observations, and the year_columns
-- mapping JSON ({column, date, date_to} -> {column, start_date, end_date}).
ALTER TABLE dataset_observations RENAME COLUMN date    TO start_date;
ALTER TABLE dataset_observations RENAME COLUMN date_to TO end_date;
DROP INDEX uq_dataset_observations;
DROP INDEX idx_dataset_observations_kato_date;
CREATE UNIQUE INDEX uq_dataset_observations ON dataset_observations (dataset_id, kato_code, start_date, COALESCE(end_date, start_date));
CREATE INDEX idx_dataset_observations_kato_date ON dataset_observations (kato_code, start_date);

UPDATE datasets
SET year_columns = (
    SELECT jsonb_agg(
        (elem - 'date' - 'date_to')
        || jsonb_build_object('start_date', elem->'date')
        || CASE WHEN elem ? 'date_to' AND elem->'date_to' IS NOT NULL
                THEN jsonb_build_object('end_date', elem->'date_to') ELSE '{}'::jsonb END)
    FROM jsonb_array_elements(year_columns) AS elem)
WHERE year_columns IS NOT NULL AND jsonb_typeof(year_columns) = 'array' AND jsonb_array_length(year_columns) > 0;

-- +goose Down
UPDATE datasets
SET year_columns = (
    SELECT jsonb_agg(
        (elem - 'start_date' - 'end_date')
        || jsonb_build_object('date', elem->'start_date')
        || CASE WHEN elem ? 'end_date' AND elem->'end_date' IS NOT NULL
                THEN jsonb_build_object('date_to', elem->'end_date') ELSE '{}'::jsonb END)
    FROM jsonb_array_elements(year_columns) AS elem)
WHERE year_columns IS NOT NULL AND jsonb_typeof(year_columns) = 'array' AND jsonb_array_length(year_columns) > 0;
DROP INDEX uq_dataset_observations;
DROP INDEX idx_dataset_observations_kato_date;
ALTER TABLE dataset_observations RENAME COLUMN start_date TO date;
ALTER TABLE dataset_observations RENAME COLUMN end_date   TO date_to;
CREATE UNIQUE INDEX uq_dataset_observations ON dataset_observations (dataset_id, kato_code, date, COALESCE(date_to, date));
CREATE INDEX idx_dataset_observations_kato_date ON dataset_observations (kato_code, date);
DROP INDEX idx_datasets_dg_id_end_date;
DROP INDEX idx_datasets_start_date;
DROP INDEX idx_datasets_end_date;
ALTER TABLE datasets DROP CONSTRAINT chk_datasets_period;
ALTER TABLE datasets DROP COLUMN dg_id;
ALTER TABLE datasets DROP COLUMN start_date;
ALTER TABLE datasets DROP COLUMN end_date;
DROP TABLE dataset_groups;
