-- 027_gis_dataset_meta.sql
-- Локальный override текстового ОПИСАНИЯ карты (датасета GIS).
--
-- Внешний сервис dssgis.dwh.kz хранит description только на создании датасета
-- (POST /datasets) и не даёт его редактировать (на /datasets/{id} лишь GET/DELETE).
-- Чтобы можно было добавлять/править описание любой карты (в т.ч. уже загруженных),
-- храним его у себя, ключ — внешний UUID датасета. При показе локальный override
-- приоритетнее внешнего dataset.description.
--
-- Кардинальность: одно описание на GIS-датасет (UNIQUE на gis_dataset_id).
-- Таблица независима от gis_dataset_links (та требует привязки к indicator).

CREATE TABLE IF NOT EXISTS gis_dataset_meta (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    gis_dataset_id TEXT         NOT NULL UNIQUE,             -- UUID датасета в GIS-сервисе
    description    TEXT,                                     -- локальное описание карты
    updated_by     UUID         REFERENCES users(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
