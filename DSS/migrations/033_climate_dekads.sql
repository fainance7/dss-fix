-- Migration 033: подекадные климатические данные по метеостанциям.
--
-- Средняя температура воздуха и осадки хранятся по станциям подекадно
-- (1–10, 11–20, 21–конец месяца) в meteo_station_values. Месяц станции система
-- считает сама (backend/app/services/climate_dekads.py):
--   температура = (d1·10 + d2·10 + d3·(N−20)) / N, N — дней в месяце;
--   осадки      = d1 + d2 + d3;
-- только при наличии всех трёх декад. Месяц района (среднее станций района,
-- для г. Петропавловска — ближайшая станция) пишется в indicator_values
-- (period_type='month') под отдельными расчётными показателями «… (по метеостанциям)»;
-- ряды Open-Meteo не затрагиваются.
-- Идемпотентно — применяется на каждом деплое.

BEGIN;

-- ── Показатели ────────────────────────────────────────────────────────────
ALTER TABLE indicators ADD COLUMN IF NOT EXISTS period_kind VARCHAR(10) NOT NULL DEFAULT 'year';
ALTER TABLE indicators ADD COLUMN IF NOT EXISTS is_computed BOOLEAN NOT NULL DEFAULT FALSE;

-- Пустые показатели «… (по 26 метеостанциям)» становятся расчётными станционными.
UPDATE indicators SET name = 'Средняя температура воздуха (по метеостанциям)'
 WHERE name = 'Среднемесячная температура (по 26 метеостанциям)';
UPDATE indicators SET name = 'Сумма осадков (по метеостанциям)'
 WHERE name = 'Среднемесячные осадки (по 26 метеостанциям)';

INSERT INTO indicators (direction_id, unit_id, name, sort_order, is_active)
SELECT d.id, u.id, v.name, 0, TRUE
FROM (VALUES ('Средняя температура воздуха (по метеостанциям)', '°C'),
             ('Сумма осадков (по метеостанциям)', 'мм')) AS v(name, unit)
JOIN directions d ON d.code = 'climate'
JOIN units u ON u.name = v.unit
ON CONFLICT (direction_id, name) DO NOTHING;

UPDATE indicators SET period_kind = 'month', is_computed = TRUE
 WHERE name IN ('Средняя температура воздуха (по метеостанциям)', 'Сумма осадков (по метеостанциям)');
UPDATE indicators SET period_kind = 'month'
 WHERE name IN ('Среднемесячная температура воздуха', 'Сумма месячных осадков');

-- ── Источники ─────────────────────────────────────────────────────────────
INSERT INTO data_sources (name, description)
SELECT v.name, v.description
FROM (VALUES
  ('РГП «Казгидромет» — декадные данные метеостанций',
   'Средняя температура воздуха и сумма осадков по метеостанциям СКО за декаду'),
  ('Расчёт DSS',
   'Значения, рассчитанные Системой из первичных данных (например, месяц из декад)')
) AS v(name, description)
WHERE NOT EXISTS (SELECT 1 FROM data_sources s WHERE s.name = v.name);

-- ── Станции ───────────────────────────────────────────────────────────────
-- Координаты Благовещенки — из файла заказчика «Клим_данные_по_метеостанциям_СКО.xlsx».
UPDATE meteo_stations SET lat = 54.4, lon = 67.0 WHERE name = 'Благовещенка' AND lat IS NULL;

-- Какие станции дают значение района: свои (own) или ближайшая (nearest),
-- если в районе станций нет (г. Петропавловск → станция «Петропавловск»).
CREATE TABLE IF NOT EXISTS climate_region_station_map (
    region_id  UUID NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    station_id UUID NOT NULL REFERENCES meteo_stations(id) ON DELETE CASCADE,
    mode       VARCHAR(10) NOT NULL CHECK (mode IN ('own', 'nearest')),
    PRIMARY KEY (region_id, station_id)
);

INSERT INTO climate_region_station_map (region_id, station_id, mode)
SELECT s.region_id, s.id, 'own' FROM meteo_stations s WHERE s.is_active
ON CONFLICT DO NOTHING;

INSERT INTO climate_region_station_map (region_id, station_id, mode)
SELECT r.id, s.id, 'nearest'
FROM regions r JOIN meteo_stations s ON s.name = 'Петропавловск'
WHERE r.name = 'г. Петропавловск'
  AND NOT EXISTS (SELECT 1 FROM climate_region_station_map m WHERE m.region_id = r.id)
ON CONFLICT DO NOTHING;

-- ── Значения по станциям ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS meteo_station_values (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    station_id        UUID NOT NULL REFERENCES meteo_stations(id) ON DELETE RESTRICT,
    indicator_id      UUID NOT NULL REFERENCES indicators(id) ON DELETE CASCADE,
    period_type       VARCHAR(10) NOT NULL,
    period_start      DATE NOT NULL,
    period_end        DATE NOT NULL,
    dekad             SMALLINT,
    value             DOUBLE PRECISION NOT NULL,
    is_derived        BOOLEAN NOT NULL DEFAULT FALSE,
    derivation        VARCHAR(30),
    source_id         UUID REFERENCES data_sources(id) ON DELETE SET NULL,
    job_id            UUID REFERENCES dataset_intake_jobs(id) ON DELETE SET NULL,
    validation_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    validated_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    validated_at      TIMESTAMPTZ,
    created_by        UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT msv_unique UNIQUE (station_id, indicator_id, period_type, period_start),
    CONSTRAINT msv_status_chk CHECK (validation_status IN ('pending', 'approved', 'rejected')),
    CONSTRAINT msv_shape_chk CHECK (
        (period_type = 'dekad' AND dekad BETWEEN 1 AND 3
         AND EXTRACT(DAY FROM period_start) = CASE dekad WHEN 1 THEN 1 WHEN 2 THEN 11 ELSE 21 END
         AND period_end = CASE WHEN dekad < 3 THEN period_start + 9
                               ELSE (date_trunc('month', period_start) + interval '1 month - 1 day')::date END)
        OR
        (period_type = 'month' AND dekad IS NULL AND EXTRACT(DAY FROM period_start) = 1
         AND period_end = (date_trunc('month', period_start) + interval '1 month - 1 day')::date)
    )
);
CREATE INDEX IF NOT EXISTS idx_msv_ind_period ON meteo_station_values (indicator_id, period_type, period_start);
CREATE INDEX IF NOT EXISTS idx_msv_station ON meteo_station_values (station_id);
CREATE INDEX IF NOT EXISTS idx_msv_status ON meteo_station_values (validation_status);

-- Архив районных декад осадков, ранее ошибочно записанных в месячный ряд
-- «Сумма месячных осадков» (period_type='month', начало 11-го/21-го числа).
-- Переносит backend/scripts/migrate_legacy_precip_dekads.py; на дашборды не выводится.
CREATE TABLE IF NOT EXISTS legacy_district_precip_dekads (
    region_id      UUID NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    period_start   DATE NOT NULL,
    period_end     DATE NOT NULL,
    dekad          SMALLINT NOT NULL CHECK (dekad BETWEEN 1 AND 3),
    value          DOUBLE PRECISION NOT NULL,
    source_id      UUID,
    reconstructed  BOOLEAN NOT NULL DEFAULT FALSE,  -- 1-я декада = месяц − d2 − d3 (расчётное)
    stations_mean  DOUBLE PRECISION,                -- среднее станционных декад района для сверки
    archived_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (region_id, period_start)
);

-- Строки приёма данных по станциям (канал декад — фаза приёма).
ALTER TABLE dataset_intake_rows ADD COLUMN IF NOT EXISTS station_id UUID REFERENCES meteo_stations(id) ON DELETE SET NULL;

COMMIT;
