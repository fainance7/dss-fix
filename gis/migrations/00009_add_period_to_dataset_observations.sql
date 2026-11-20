-- +goose Up
-- Optional period end date, so a year_column mapping can represent a date
-- range (date..date_to) instead of a single point in time. NULL preserves the
-- pre-existing point-in-time behavior; the unique index treats a NULL date_to
-- as equal to date itself so point observations keep deduplicating exactly as
-- before.
ALTER TABLE dataset_observations ADD COLUMN date_to DATE;

DROP INDEX uq_dataset_observations;
CREATE UNIQUE INDEX uq_dataset_observations ON dataset_observations (dataset_id, kato_code, date, COALESCE(date_to, date));

-- +goose Down
DROP INDEX uq_dataset_observations;
ALTER TABLE dataset_observations DROP COLUMN date_to;
CREATE UNIQUE INDEX uq_dataset_observations ON dataset_observations (dataset_id, kato_code, date);
