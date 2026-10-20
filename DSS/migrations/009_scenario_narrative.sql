-- Migration 009: Narrative text for scenarios + xai summary for forecasts.
-- Used by ai-service POST /scenarios/{id}/narrative — stores LLM-generated
-- explanation of the development scenario (autonomous local LLM, e.g.
-- Qwen2.5-7B via Ollama). Also adds optional human-readable summary on
-- forecasts.

BEGIN;

ALTER TABLE development_scenarios
    ADD COLUMN IF NOT EXISTS narrative TEXT,
    ADD COLUMN IF NOT EXISTS narrative_generated_at TIMESTAMPTZ;

ALTER TABLE forecasts
    ADD COLUMN IF NOT EXISTS xai_summary TEXT;

COMMIT;
