-- Migration 010: Data intake staging
-- Систематизация исторических данных: партии загрузок (jobs) и пред-валидированные строки (rows).
-- После commit оператором — переезжают в indicator_values через UPSERT.

BEGIN;

CREATE TABLE IF NOT EXISTS dataset_intake_jobs (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_kind        VARCHAR(30) NOT NULL,
    source_ref         TEXT        NOT NULL,
    uploaded_by        UUID        REFERENCES users(id) ON DELETE SET NULL,
    uploaded_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status             VARCHAR(20) NOT NULL DEFAULT 'pending',
    rows_total         INTEGER     NOT NULL DEFAULT 0,
    rows_ok            INTEGER     NOT NULL DEFAULT 0,
    rows_invalid       INTEGER     NOT NULL DEFAULT 0,
    rows_imported      INTEGER     NOT NULL DEFAULT 0,
    validation_summary JSONB,
    data_source_id     UUID        REFERENCES data_sources(id) ON DELETE SET NULL,
    committed_at       TIMESTAMPTZ,
    committed_by       UUID        REFERENCES users(id) ON DELETE SET NULL,

    CONSTRAINT dij_source_kind_chk
        CHECK (source_kind IN ('excel_template', 'remote_reports_db')),
    CONSTRAINT dij_status_chk
        CHECK (status IN ('pending', 'validated', 'imported', 'failed', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS idx_dij_status      ON dataset_intake_jobs(status);
CREATE INDEX IF NOT EXISTS idx_dij_uploaded_by ON dataset_intake_jobs(uploaded_by);
CREATE INDEX IF NOT EXISTS idx_dij_uploaded_at ON dataset_intake_jobs(uploaded_at DESC);

CREATE TABLE IF NOT EXISTS dataset_intake_rows (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id        UUID NOT NULL REFERENCES dataset_intake_jobs(id) ON DELETE CASCADE,
    row_no        INTEGER NOT NULL,
    raw_payload   JSONB   NOT NULL,
    indicator_id  UUID    REFERENCES indicators(id) ON DELETE SET NULL,
    region_id     UUID    REFERENCES regions(id)    ON DELETE SET NULL,
    period_type   VARCHAR(20),
    period_start  DATE,
    period_end    DATE,
    value         DOUBLE PRECISION,
    error_message TEXT,
    status        VARCHAR(20) NOT NULL DEFAULT 'invalid',

    CONSTRAINT dir_status_chk
        CHECK (status IN ('ok', 'invalid', 'imported', 'skipped'))
);

CREATE INDEX IF NOT EXISTS idx_dir_job_status        ON dataset_intake_rows(job_id, status);
CREATE INDEX IF NOT EXISTS idx_dir_indicator_region  ON dataset_intake_rows(indicator_id, region_id);

COMMIT;
