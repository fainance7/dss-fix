-- Migration 035: архив заменяемых значений показателей + исправление названия станции.
--
-- K(5-8) и ГТК(5-8) по районам пересчитываются из файла заказчика
-- «Клим_данные_по_метеостанциям_СКО_T_R_decade (Н4)» (scripts/import_climate_workbook.py):
-- прежние значения (другая методика K, ошибки в ГТК) сохраняются здесь для сравнения.
-- Станция «Новоишинский» в справочнике — опечатка: верно «Новоишимский» (с. Новоишимское).
-- Идемпотентно.

BEGIN;

CREATE TABLE IF NOT EXISTS indicator_values_archive (
    id                UUID NOT NULL,
    indicator_id      UUID NOT NULL,
    region_id         UUID NOT NULL,
    period_type       VARCHAR(20) NOT NULL,
    period_start      DATE NOT NULL,
    period_end        DATE NOT NULL,
    value             DOUBLE PRECISION NOT NULL,
    source_id         UUID,
    validation_status VARCHAR(20),
    created_at        TIMESTAMPTZ,
    archived_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason            TEXT NOT NULL,
    PRIMARY KEY (id, archived_at)
);
CREATE INDEX IF NOT EXISTS idx_iva_ind_region ON indicator_values_archive (indicator_id, region_id, period_start);

UPDATE meteo_stations SET name = 'Новоишимский'
 WHERE name = 'Новоишинский' AND NOT EXISTS (SELECT 1 FROM meteo_stations WHERE name = 'Новоишимский');

COMMIT;
