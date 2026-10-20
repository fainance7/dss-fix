-- Migration 019: async narrative generation status
-- Генерация нарратива сценария вынесена в фон (на CPU занимает до ~6 мин).
-- Храним статус и ошибку, чтобы фронт поллил готовность, а не висел синхронно.
-- Идемпотентно (IF NOT EXISTS) — применяется на каждом деплое.

BEGIN;

ALTER TABLE development_scenarios
    ADD COLUMN IF NOT EXISTS narrative_status VARCHAR(20);

ALTER TABLE development_scenarios
    ADD COLUMN IF NOT EXISTS narrative_error TEXT;

COMMENT ON COLUMN development_scenarios.narrative_status IS 'pending|running|done|failed — статус фоновой генерации нарратива';

COMMIT;
