-- Migration 034: месячное значение — строго с 1-го по последнее число месяца.
--
-- Раньше в indicator_values можно было записать «месяц» с любыми датами: так в ряд
-- «Сумма месячных осадков» попали районные декады (начало 11-го и 21-го числа), а первые
-- декады затёрли месячные строки по ключу. Декады теперь хранятся отдельно
-- (meteo_station_values, миграция 033), старые перенесены в архив
-- (scripts/migrate_legacy_precip_dekads.py). Ограничение не даёт повторить ошибку.
-- Идемпотентно: добавляется NOT VALID и проверяется, если ещё не существует.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'iv_month_shape_chk') THEN
        ALTER TABLE indicator_values ADD CONSTRAINT iv_month_shape_chk CHECK (
            period_type <> 'month'
            OR (EXTRACT(DAY FROM period_start) = 1
                AND period_end = (date_trunc('month', period_start) + interval '1 month - 1 day')::date)
        ) NOT VALID;
        ALTER TABLE indicator_values VALIDATE CONSTRAINT iv_month_shape_chk;
    END IF;
END $$;
