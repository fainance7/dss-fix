-- Migration 013: intake_mappings — UI-редактируемая конфигурация ETL для remote-таблиц reports_db.
-- Заменяет/дополняет yaml-файлы в data/intake_mapping (yaml остаётся для bootstrap/CI).
-- Каждая запись описывает, как одну таблицу reports_db развернуть в indicator_values.

BEGIN;

CREATE TABLE IF NOT EXISTS intake_mappings (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    remote_table             TEXT        NOT NULL,
    indicator_id             UUID        NOT NULL REFERENCES indicators(id) ON DELETE RESTRICT,
    -- Способ резолва региона:
    --   'constant' — все строки → один регион (kato_constant);
    --   'by_name'  — по имени из колонки region_column_idx, lookup в region_name_to_kato JSONB.
    region_resolution_kind   VARCHAR(20) NOT NULL DEFAULT 'constant',
    kato_constant            VARCHAR(9)  REFERENCES kato_reference(kato_code) ON DELETE RESTRICT,
    region_column_idx        INTEGER,
    region_name_to_kato      JSONB,

    -- Период: 'monthly' | 'yearly' | 'quarterly' | 'daily'
    period_kind              VARCHAR(20) NOT NULL,
    year_column_idx          INTEGER,
    -- Список {index: int, marker: str}; для monthly marker — '01'..'12', для quarterly — '1'..'4'
    period_columns_idx       JSONB       NOT NULL,
    -- Значения первой колонки, при которых строку пропускаем (агрегаты «среднее», «норма», ...)
    skip_if_first_col_in     JSONB       NOT NULL DEFAULT '[]'::jsonb,

    source_name              TEXT,
    source_url               TEXT,
    source_description       TEXT,
    notes                    TEXT,

    created_by               UUID        REFERENCES users(id) ON DELETE SET NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_run_at              TIMESTAMPTZ,
    last_run_status          VARCHAR(20),
    last_run_summary         JSONB,

    CONSTRAINT im_region_kind_chk
        CHECK (region_resolution_kind IN ('constant', 'by_name')),
    CONSTRAINT im_period_kind_chk
        CHECK (period_kind IN ('yearly', 'quarterly', 'monthly', 'daily')),
    CONSTRAINT im_constant_xor_by_name CHECK (
        (region_resolution_kind = 'constant' AND kato_constant IS NOT NULL)
        OR (region_resolution_kind = 'by_name' AND region_column_idx IS NOT NULL)
    ),
    CONSTRAINT im_remote_table_unique UNIQUE (remote_table, indicator_id)
);

CREATE INDEX IF NOT EXISTS idx_im_indicator       ON intake_mappings(indicator_id);
CREATE INDEX IF NOT EXISTS idx_im_remote_table    ON intake_mappings(remote_table);
CREATE INDEX IF NOT EXISTS idx_im_last_run_status ON intake_mappings(last_run_status);

COMMIT;
