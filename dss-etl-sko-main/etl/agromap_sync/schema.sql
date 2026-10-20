-- ============================================================
-- Schema: agromap_geo — geo-data for agromap.gishagi.kz integration
-- Target: aldispetro-postgis-agromap-1 (port 5433), DB agromap_geo
-- Convention: run via psql, wrap in transaction, verify before commit
-- Синхронизировано с реальным состоянием БД: 26.07.2026
-- ============================================================

BEGIN;

-- Extension safety check (should already exist per Stage 2, but idempotent)
CREATE EXTENSION IF NOT EXISTS postgis;

-- ВАЖНО для скрипта синка (Этап 3): CHECK(ST_IsValid(geom)) на land_polygons
-- и land_polygons_staging означает, что вставка ОБЯЗАНА приводить геометрию цепочкой:
--   ST_Multi(
--       ST_CollectionExtract(
--           ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(<geojson_geometry>), 4326)),
--           3  -- 3 = polygon
--       )
--   )
-- ST_CollectionExtract обязателен: ST_MakeValid на самопересекающихся полигонах
-- источника иногда возвращает GeometryCollection (полигон + вырожденные
-- LineString/Point нулевой площади от места самопересечения), что не проходит
-- по типу колонки MultiPolygon. Подтверждено на практике на объектах СКО
-- 289053 и 289552 (21.07.2026) — ST_CollectionExtract(...,3) отбрасывает
-- линии/точки без потери значимой площади.

-- ------------------------------------------------------------
-- Справочники район/округ — заполняются парсингом location_name
-- (формат "область, {район} р-н, {округ} с.о." — подтверждён разведкой,
-- 22.07.2026: 186 уникальных комбинаций для СКО, строгий формат).
-- Два известных исключения ("город Булаево", "город Мамлютка" — де-факто
-- сёла со своим горакиматом внутри района, не отдельные районы) переносятся
-- в правильный район ДО парсинга — см. комментарий в sync_agromap.py
-- (TRANSFER_SQL), подтверждено внешним поиском 22.07.2026:
--   Булаево -> р-н Магжана Жумабаева (не Мамлютский, несмотря на созвучие)
--   Мамлютка -> Мамлютский р-н
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agromap_districts (
    id             serial PRIMARY KEY,
    source_region  text   NOT NULL,
    name           text   NOT NULL,
    CONSTRAINT uq_agromap_districts UNIQUE (source_region, name)
);

CREATE TABLE IF NOT EXISTS agromap_villages (
    id             serial PRIMARY KEY,
    district_id    integer NOT NULL REFERENCES agromap_districts(id),
    name           text    NOT NULL,
    CONSTRAINT uq_agromap_villages UNIQUE (district_id, name)
);

-- ------------------------------------------------------------
-- Boevaya (production) table
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS land_polygons (
    id               bigserial PRIMARY KEY,
    geom             geometry(MultiPolygon, 4326) NOT NULL,

    source_objectid  bigint      NOT NULL,   -- OBJECTID_1 источника.
                                              -- ВНИМАНИЕ: не гарантированно стабилен во
                                              -- времени — источник может переиспользовать
                                              -- значение для другого физического участка
                                              -- между прогонами (подтверждено на практике,
                                              -- объект 93238, 20-21.07.2026).
    source_region    text        NOT NULL,   -- 'sko', 'pavlodarskaya', ... (папка agromap)
    source_layer     text        NOT NULL,   -- полный путь сервиса/слоя (для трассировки)

    object_ru        text,
    object_kz        text,
    ground_ru        text,
    ground_kz        text,
    vegetation_ru    text,
    vegetation_kz    text,
    water_ru         text,
    water_kz         text,
    description_ru   text,
    description_kz   text,

    code             text,
    code_esri        text,

    area_ha          numeric,

    location_name    text,       -- сырое поле "name" (область+район+округ вместе)

    district_id      integer REFERENCES agromap_districts(id),  -- распарсено из
                                              -- location_name (см. agromap_districts ниже);
                                              -- NULL если location_name пуст или не имеет
                                              -- стандартного формата "область, район, округ"
                                              -- (~3516 объектов СКО, ~1.2% региона)
    village_id       integer REFERENCES agromap_villages(id),

    raw_attributes   jsonb,      -- полный снимок properties на случай нестандартных полей

    synced_at        timestamptz NOT NULL DEFAULT now(),

    -- Soft-delete: объект мог исчезнуть у источника (перепродан/объединён/снят с учёта).
    -- При частичном/повторном синке региона запись не удаляется физически, а гасится:
    -- is_active=false, retired_at=<когда обнаружено отсутствие в очередном полном batch>.
    is_active        boolean     NOT NULL DEFAULT true,
    retired_at       timestamptz,

    CONSTRAINT uq_land_polygons_region_objectid UNIQUE (source_region, source_objectid),
    CONSTRAINT chk_land_polygons_geom_valid CHECK (ST_IsValid(geom))
);

CREATE INDEX IF NOT EXISTS idx_land_polygons_geom
    ON land_polygons USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_land_polygons_object_ru
    ON land_polygons (object_ru);

CREATE INDEX IF NOT EXISTS idx_land_polygons_source_region
    ON land_polygons (source_region);

CREATE INDEX IF NOT EXISTS idx_land_polygons_is_active
    ON land_polygons (is_active) WHERE is_active = true;

-- ------------------------------------------------------------
-- Staging table (identical structure, for atomic swap)
-- Same UNIQUE constraint: prevents duplicate accumulation
-- if a sync run is interrupted and restarted before swap.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS land_polygons_staging (
    id               bigserial PRIMARY KEY,
    geom             geometry(MultiPolygon, 4326) NOT NULL,

    source_objectid  bigint      NOT NULL,
    source_region    text        NOT NULL,
    source_layer     text        NOT NULL,

    object_ru        text,
    object_kz        text,
    ground_ru        text,
    ground_kz        text,
    vegetation_ru    text,
    vegetation_kz    text,
    water_ru         text,
    water_kz         text,
    description_ru   text,
    description_kz   text,

    code             text,
    code_esri        text,

    area_ha          numeric,

    location_name    text,

    district_id      integer,
    village_id       integer,

    raw_attributes   jsonb,

    synced_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_land_polygons_staging_region_objectid UNIQUE (source_region, source_objectid),
    CONSTRAINT chk_land_polygons_staging_geom_valid CHECK (ST_IsValid(geom))
);

CREATE INDEX IF NOT EXISTS idx_land_polygons_staging_geom
    ON land_polygons_staging USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_land_polygons_staging_object_ru
    ON land_polygons_staging (object_ru);

CREATE INDEX IF NOT EXISTS idx_land_polygons_staging_source_region
    ON land_polygons_staging (source_region);

-- ------------------------------------------------------------
-- Sync log — for "last sync date" (README 4.9) and integrity
-- checks (Stage 3.8: count/sum comparison against source)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sync_log (
    id               bigserial PRIMARY KEY,
    source_region    text        NOT NULL,
    started_at       timestamptz NOT NULL,
    finished_at      timestamptz,
    rows_count       integer,
    status           text        NOT NULL DEFAULT 'running',  -- 'running' | 'success' | 'failed' | 'partial'
    error_message    text
);

CREATE INDEX IF NOT EXISTS idx_sync_log_region_started
    ON sync_log (source_region, started_at DESC);

-- ------------------------------------------------------------
-- Sync errors — битые объекты одного региона за ПОСЛЕДНИЙ прогон.
-- Не FK на sync_log и не хранит историю по дизайну: source_objectid
-- источника не гарантированно стабилен во времени (см. комментарий
-- у land_polygons.source_objectid), поэтому история ошибок по одному
-- ID может относиться к разным физическим участкам и не даёт
-- надёжного сигнала. Таблица чистится по региону (DELETE) в начале
-- каждого прогона синка — симметрично land_polygons_staging.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sync_errors (
    id               bigserial PRIMARY KEY,
    source_region    text        NOT NULL,
    source_objectid  bigint,                 -- NULL допустим: объект мог не иметь
                                              -- ни OBJECTID_1, ни objectid вовсе
    error_message    text        NOT NULL,
    raw_geojson      jsonb,                  -- исходная фича с источника целиком
                                              -- (properties + geometry), для разбора/backfill
    failed_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sync_errors_region_failed
    ON sync_errors (source_region, failed_at DESC);

COMMIT;

-- ------------------------------------------------------------
-- Verification queries (run manually after commit)
-- ------------------------------------------------------------
-- \dt
-- \d land_polygons
--    В выводе, в блоке Indexes:, проверить что строка
--    uq_land_polygons_region_objectid помечена как "UNIQUE CONSTRAINT",
--    а не просто индекс. И в блоке Check constraints: наличие
--    chk_land_polygons_geom_valid.
-- \d land_polygons_staging
-- \d sync_log
-- \d sync_errors
-- SELECT postgis_version();
