-- Migration 017: bind forecasts and AI models to a specific indicator
-- Прогнозы теперь строятся по реальному ряду из indicator_values для
-- конкретного показателя (см. ai-service forecasting). Добавляем явную
-- привязку forecasts.indicator_id и ai_models.indicator_id.
-- Идемпотентно (IF NOT EXISTS) — применяется на каждом деплое.

BEGIN;

ALTER TABLE forecasts
    ADD COLUMN IF NOT EXISTS indicator_id UUID
    REFERENCES indicators(id) ON DELETE SET NULL;

ALTER TABLE ai_models
    ADD COLUMN IF NOT EXISTS indicator_id UUID
    REFERENCES indicators(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_forecasts_indicator_id
    ON forecasts (indicator_id);

CREATE INDEX IF NOT EXISTS idx_ai_models_indicator_id
    ON ai_models (indicator_id);

COMMIT;
