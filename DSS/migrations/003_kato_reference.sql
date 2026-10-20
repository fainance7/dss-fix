-- =============================================================================
-- DSS — Migration 003: КАТО — Классификатор административно-территориальных объектов
-- Version: 003
-- Date: 2026-03-27
-- =============================================================================

-- ---------------------------------------------------------------------------
-- kato_reference — справочник КАТО
-- ---------------------------------------------------------------------------
CREATE TABLE kato_reference (
    kato_code    VARCHAR(9)   NOT NULL,
    name_ru      VARCHAR(250) NOT NULL,
    name_kz      VARCHAR(250),
    type_code    VARCHAR(10),
    type_name_ru VARCHAR(100),
    parent_code  VARCHAR(9)   REFERENCES kato_reference(kato_code) ON DELETE RESTRICT,
    level        SMALLINT     NOT NULL,
    is_active    BOOLEAN      NOT NULL DEFAULT TRUE,
    region_id    UUID         REFERENCES regions(id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT kato_reference_pkey PRIMARY KEY (kato_code),
    CONSTRAINT kato_level_check CHECK (level BETWEEN 1 AND 5)
);

COMMENT ON TABLE  kato_reference             IS 'Классификатор административно-территориальных объектов Республики Казахстан';
COMMENT ON COLUMN kato_reference.kato_code   IS '9-значный код КАТО';
COMMENT ON COLUMN kato_reference.name_ru     IS 'Наименование на русском языке';
COMMENT ON COLUMN kato_reference.name_kz     IS 'Наименование на казахском языке';
COMMENT ON COLUMN kato_reference.type_code   IS 'Тип объекта: oblys, rayon, qala, awyl и т.д.';
COMMENT ON COLUMN kato_reference.type_name_ru IS 'Тип объекта на русском: Область, Район, Город и т.д.';
COMMENT ON COLUMN kato_reference.parent_code IS 'Ссылка на родительский объект КАТО';
COMMENT ON COLUMN kato_reference.level       IS '1=область, 2=район/город обл. знач., 3=город/посёлок районного знач., 4=сельский округ, 5=н.п.';
COMMENT ON COLUMN kato_reference.region_id   IS 'Связь с таблицей regions DSS-системы';

CREATE INDEX idx_kato_parent_code ON kato_reference(parent_code);
CREATE INDEX idx_kato_level       ON kato_reference(level);
CREATE INDEX idx_kato_region_id   ON kato_reference(region_id);
CREATE INDEX idx_kato_name_ru     ON kato_reference USING gin (name_ru gin_trgm_ops);


-- ---------------------------------------------------------------------------
-- Добавляем kato_code в таблицу regions
-- ---------------------------------------------------------------------------
ALTER TABLE regions
    ADD COLUMN IF NOT EXISTS kato_code VARCHAR(9) REFERENCES kato_reference(kato_code) ON DELETE SET NULL;

COMMENT ON COLUMN regions.kato_code IS 'Официальный код КАТО для данного региона';

CREATE INDEX IF NOT EXISTS idx_regions_kato_code ON regions(kato_code);
