-- 025_gis_links.sql
-- Персистентная связь каталога GIS (подсистема dssgis.dwh.kz) с доменом DSS.
--
-- Категории GIS (10 шт.) семантически соответствуют нашим НАПРАВЛЕНИЯМ (directions),
-- датасеты GIS (238) — нашим ПОКАЗАТЕЛЯМ (indicators). Каталог GIS живёт во внешней
-- системе, поэтому здесь храним только сопоставление: внешний UUID GIS-объекта →
-- наш direction/indicator. Имя/код GIS кэшируем, чтобы показывать связь в UI без
-- обращения к GIS-сервису.
--
-- Кардинальность: каждый GIS-объект привязывается максимум к одному объекту DSS
-- (UNIQUE на gis_*_id), но одно направление/показатель может иметь много GIS-объектов
-- (например, датасеты по разным годам одного показателя).

CREATE TABLE IF NOT EXISTS gis_category_links (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    gis_category_id    TEXT         NOT NULL UNIQUE,          -- UUID категории в GIS-сервисе
    gis_category_code  TEXT,                                  -- code категории (water/land/…), кэш
    gis_category_name  TEXT,                                  -- имя категории, кэш для UI
    direction_id       UUID         NOT NULL
                       REFERENCES directions(id) ON DELETE CASCADE,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gis_category_links_direction
    ON gis_category_links (direction_id);

CREATE TABLE IF NOT EXISTS gis_dataset_links (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    gis_dataset_id   TEXT         NOT NULL UNIQUE,            -- UUID датасета в GIS-сервисе
    gis_dataset_name TEXT,                                    -- имя датасета, кэш для UI
    gis_category_id  TEXT,                                    -- категория датасета в GIS, кэш
    indicator_id     UUID         NOT NULL
                     REFERENCES indicators(id) ON DELETE CASCADE,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gis_dataset_links_indicator
    ON gis_dataset_links (indicator_id);
CREATE INDEX IF NOT EXISTS idx_gis_dataset_links_gis_category
    ON gis_dataset_links (gis_category_id);
