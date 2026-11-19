-- Создание схемы для landing zone (сырые данные)
CREATE TABLE IF NOT EXISTS raw_historical (
    id          SERIAL PRIMARY KEY,
    source_file TEXT,
    data        JSONB,
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw_taldau (
    id              SERIAL PRIMARY KEY,
    indicator_code  TEXT,
    indicator_name  TEXT,
    raw_json        TEXT,
    fetched_at      TEXT,
    source_file     TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw_weather (
    id           SERIAL PRIMARY KEY,
    station_name TEXT,
    kato_code    BIGINT,
    year         INT,
    raw_json     TEXT,
    fetched_at   TEXT,
    source_file  TEXT,
    created_at   TIMESTAMP DEFAULT NOW()
);

-- Трансформированные данные (результат DAG-ов)
CREATE TABLE IF NOT EXISTS transformed_taldau (
    id             SERIAL PRIMARY KEY,
    indicator_name TEXT,
    direction_code TEXT,
    unit_name      TEXT,
    kato_code      BIGINT,
    period_type    TEXT,
    period_start   TEXT,
    period_end     TEXT,
    value          TEXT,
    created_at     TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS transformed_weather (
    id             SERIAL PRIMARY KEY,
    indicator_name TEXT,
    direction_code TEXT,
    unit_name      TEXT,
    kato_code      BIGINT,
    period_type    TEXT,
    period_start   TEXT,
    period_end     TEXT,
    value          TEXT,
    created_at     TIMESTAMP DEFAULT NOW()
);

-- Кэш справочников DSS (синхронизируется из aldis.kz)
CREATE TABLE IF NOT EXISTS kato_cache (
    kato_code  BIGINT PRIMARY KEY,
    name_ru    TEXT,
    name_kz    TEXT,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS indicator_cache (
    indicator_id    SERIAL PRIMARY KEY,
    indicator_name  TEXT UNIQUE,
    direction_code  TEXT,
    unit_name       TEXT,
    updated_at      TIMESTAMP DEFAULT NOW()
);
