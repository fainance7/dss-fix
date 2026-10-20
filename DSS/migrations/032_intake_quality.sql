-- Контроль качества партий приёма данных: отчёт по партии и замечания по ключу значения.
-- Отчёт считается до импорта (backend/app/services/intake_quality.py) и хранится в партии;
-- замечания по ключу (показатель, регион, период) показываются эксперту по проверке
-- на странице «Импортированные значения» рядом со значениями «на проверке».
BEGIN;

ALTER TABLE dataset_intake_jobs ADD COLUMN IF NOT EXISTS quality_report JSONB;

CREATE TABLE IF NOT EXISTS intake_quality_flags (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id       UUID NOT NULL REFERENCES dataset_intake_jobs(id) ON DELETE CASCADE,
    indicator_id UUID NOT NULL,
    region_id    UUID NOT NULL,
    period_type  VARCHAR(20) NOT NULL,
    period_start DATE NOT NULL,
    code         VARCHAR(40) NOT NULL,
    severity     VARCHAR(10) NOT NULL CHECK (severity IN ('blocker', 'warning', 'info')),
    message      TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_iqf_key
    ON intake_quality_flags (indicator_id, region_id, period_type, period_start);
CREATE INDEX IF NOT EXISTS idx_iqf_job ON intake_quality_flags (job_id);

COMMIT;
