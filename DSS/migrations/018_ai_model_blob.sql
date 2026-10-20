-- Migration 018: persist trained model artifacts
-- Offline-тренировка (ai-service/scripts/train.py) сохраняет сериализованную
-- модель в ai_models.model_blob вместе с метриками бэктеста. До этого модели
-- фитились только на лету и не персистились.
-- Идемпотентно (IF NOT EXISTS) — применяется на каждом деплое.

BEGIN;

ALTER TABLE ai_models
    ADD COLUMN IF NOT EXISTS model_blob BYTEA;

COMMENT ON COLUMN ai_models.model_blob IS 'Сериализованный (pickle) артефакт обученной модели для offline-инференса';

COMMIT;
