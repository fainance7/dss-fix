-- Migration 006: Directions (Направления)
-- 8 тематических направлений, ограничение exp_dir, привязка к data_layers

BEGIN;

-- 1. Таблица направлений
CREATE TABLE IF NOT EXISTS directions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code        VARCHAR(50)  NOT NULL,
    name        VARCHAR(200) NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT directions_code_unique UNIQUE (code),
    CONSTRAINT directions_name_unique UNIQUE (name)
);

-- 8 фиксированных направлений
INSERT INTO directions (code, name) VALUES
    ('labor',        'Трудовые ресурсы'),
    ('social_demog', 'Социально-демографическое'),
    ('water',        'Водные ресурсы'),
    ('economy',      'Экономическое'),
    ('ecology',      'Экология'),
    ('tourism',      'Туризм'),
    ('transport',    'Транспорт'),
    ('climate',      'Климат')
ON CONFLICT (code) DO NOTHING;

-- 2. Связь пользователь ↔ направления (ограничение для exp_dir)
--    Пустой набор = доступ ко всем направлениям
CREATE TABLE IF NOT EXISTS user_directions (
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    direction_id UUID NOT NULL REFERENCES directions(id) ON DELETE CASCADE,
    assigned_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, direction_id)
);

CREATE INDEX IF NOT EXISTS idx_user_directions_user ON user_directions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_directions_dir  ON user_directions(direction_id);

-- 3. Добавить direction_id в data_layers (nullable для бэкфила)
ALTER TABLE data_layers ADD COLUMN IF NOT EXISTS direction_id UUID REFERENCES directions(id) ON DELETE RESTRICT;

-- 4. Бэкфил существующих слоёв по категориям
UPDATE data_layers SET direction_id = (SELECT id FROM directions WHERE code = 'economy')
    WHERE direction_id IS NULL
      AND category_id IN (SELECT id FROM data_categories WHERE name IN ('Экономика', 'Сельское хозяйство'));

UPDATE data_layers SET direction_id = (SELECT id FROM directions WHERE code = 'social_demog')
    WHERE direction_id IS NULL
      AND category_id IN (SELECT id FROM data_categories WHERE name = 'Демография');

UPDATE data_layers SET direction_id = (SELECT id FROM directions WHERE code = 'labor')
    WHERE direction_id IS NULL
      AND category_id IN (SELECT id FROM data_categories WHERE name = 'Занятость');

-- Catch-all для оставшихся без привязки
UPDATE data_layers SET direction_id = (SELECT id FROM directions WHERE code = 'economy')
    WHERE direction_id IS NULL;

-- 5. Сделать NOT NULL после бэкфила
ALTER TABLE data_layers ALTER COLUMN direction_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_data_layers_direction ON data_layers(direction_id);

COMMIT;
