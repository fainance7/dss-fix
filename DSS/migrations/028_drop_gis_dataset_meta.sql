-- 028_drop_gis_dataset_meta.sql
-- Удаление локального override описания карт.
--
-- dssgis теперь поддерживает PATCH /datasets/{id} — описание правится напрямую в
-- источнике правды (см. gis.py-прокси + фронт catalog/page.tsx). Локальная таблица
-- gis_dataset_meta (migration 027) больше не нужна.
--
-- ВНИМАНИЕ: перед применением выполнить бэкфилл существующих описаний в dssgis:
--   scripts/migrate_gis_dataset_meta_to_dssgis.py
-- иначе локальные описания будут потеряны.

DROP TABLE IF EXISTS gis_dataset_meta;
