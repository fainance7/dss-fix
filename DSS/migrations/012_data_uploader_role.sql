-- Migration 012: роль data_uploader для смежной команды.
-- Доступ только к /api/v1/data-intake (загрузка xlsx-партий).
-- Не имеет права коммитить — только загружать и просматривать свои партии.

BEGIN;

INSERT INTO roles (id, name, description) VALUES
    (gen_random_uuid(), 'data_uploader',
     'Внешняя команда загрузки данных: только импорт через шаблон, без commit и без доступа к остальным разделам DSS')
ON CONFLICT (name) DO NOTHING;

COMMIT;
