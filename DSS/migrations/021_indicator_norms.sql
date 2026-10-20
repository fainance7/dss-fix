-- Migration 021: среднемноголетние нормы показателей (indicator_norms).
-- Климатические графики показывают горизонтальную «среднемноголетнюю» линию
-- (норму) рядом с рядом «по годам» и линией тренда — см. образцы заказчика
-- (docs/dashboards/climate/Примеры графиков.docx). Норма — это ОДНА константа
-- на (показатель, район), не временной ряд, поэтому хранится отдельно от
-- indicator_values (без validation-workflow). Источник — ручной CC-столбец из
-- Клим_данные_по_метеостанциям_СКО.xlsx (+ производные за весну/осень).
-- Загружается скриптом backend/scripts/import_climate_norms.py (идемпотентно).

BEGIN;

CREATE TABLE IF NOT EXISTS indicator_norms (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    indicator_id        UUID NOT NULL REFERENCES indicators(id) ON DELETE CASCADE,
    region_id           UUID NOT NULL REFERENCES regions(id)    ON DELETE RESTRICT,
    norm_value          DOUBLE PRECISION NOT NULL,
    -- метка нормы (период/способ агрегации), напр. 'multiyear_2003_2024'
    period_label        VARCHAR(60) NOT NULL DEFAULT 'multiyear',
    -- способ расчёта: 'manual_cc' (ручной CC-столбец) | 'derived_mean' (выведено)
    method              VARCHAR(30) NOT NULL DEFAULT 'manual_cc',
    ref_period_start    DATE,
    ref_period_end      DATE,
    note                TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT indicator_norms_unique UNIQUE (indicator_id, region_id, period_label),
    CONSTRAINT indicator_norms_period_chk
        CHECK (ref_period_end IS NULL OR ref_period_start IS NULL
               OR ref_period_end >= ref_period_start)
);

CREATE INDEX IF NOT EXISTS idx_indicator_norms_indicator
    ON indicator_norms (indicator_id);
CREATE INDEX IF NOT EXISTS idx_indicator_norms_region
    ON indicator_norms (indicator_id, region_id);

COMMENT ON TABLE indicator_norms IS
    'Среднемноголетние нормы показателей (одна константа на показатель/район) для линии нормы на графиках';

COMMIT;
