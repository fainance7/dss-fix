-- Migration 026: narratives per direction (нарративы по направлениям)
-- Раньше нарратив сценария хранился в одной колонке narrative — генерация по
-- другому направлению (экономика → климат) перезатирала предыдущий текст.
-- Теперь храним карту direction_code → {text, status, generated_at, error},
-- чтобы нарратив каждого направления сохранялся отдельно.
-- Идемпотентно (IF NOT EXISTS) — применяется на каждом деплое.

BEGIN;

ALTER TABLE development_scenarios
    ADD COLUMN IF NOT EXISTS narratives JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN development_scenarios.narratives IS
    'Нарративы по направлениям: {direction_code: {text, status, generated_at, error}}. '
    'Легаси-колонки narrative/narrative_status/narrative_error отражают последнюю генерацию для обратной совместимости.';

COMMIT;
