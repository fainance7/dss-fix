-- Migration 007: Units of measurement + Indicators per direction

BEGIN;

-- 1. Справочник единиц измерения
CREATE TABLE IF NOT EXISTS units (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(100) NOT NULL,
    description VARCHAR(200),
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT units_name_unique UNIQUE (name)
);

INSERT INTO units (name, description) VALUES
    ('%',           'Процент'),
    ('млрд.тенге',  'Миллиард тенге'),
    ('млн.тенге',   'Миллион тенге'),
    ('тенге',       'Тенге'),
    ('кг',          'Килограмм'),
    ('тонна',       'Тонна'),
    ('человек',     'Человек'),
    ('шт.',         'Штука'),
    ('га',          'Гектар'),
    ('км',          'Километр'),
    ('км²',         'Квадратный километр'),
    ('м³',          'Кубический метр')
ON CONFLICT (name) DO NOTHING;

-- 2. Показатели направлений
CREATE TABLE IF NOT EXISTS indicators (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    direction_id UUID NOT NULL REFERENCES directions(id) ON DELETE CASCADE,
    unit_id      UUID NOT NULL REFERENCES units(id)      ON DELETE RESTRICT,
    name         VARCHAR(300) NOT NULL,
    sort_order   INTEGER      NOT NULL DEFAULT 0,
    is_active    BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT indicators_direction_name_unique UNIQUE (direction_id, name)
);

CREATE INDEX IF NOT EXISTS idx_indicators_direction ON indicators(direction_id);
CREATE INDEX IF NOT EXISTS idx_indicators_unit      ON indicators(unit_id);

-- 3. Сидинг показателей для направления "Экономическое"
INSERT INTO indicators (direction_id, unit_id, name, sort_order)
SELECT d.id, u.id, v.name, v.sort_order
FROM (VALUES
    ('Индекс физического объема ВРП',                                                         '%',          10),
    ('Объем инвестиций в основной капитал',                                                   'млрд.тенге', 20),
    ('Объем розничной торговли',                                                              'млрд.тенге', 30),
    ('Среднемесячная номинальная заработная плата',                                           'тенге',      40),
    ('Потребление продуктов питания на душу населения',                                       'кг',         50),
    ('Доля населения, имеющие доходы ниже величины прожиточного уровня',                      '%',          60),
    ('Индекс производительности труда',                                                       '%',          70),
    ('Среднегодовая доля занятых в экономике в общей численности активного населения',        '%',          80),
    ('Валовая продукция сельского хозяйства',                                                 'млрд.тенге', 90),
    ('Валовая продукция промышленности',                                                      'млрд.тенге', 100),
    ('Величина прожиточного минимума',                                                        'тенге',      110)
) AS v(name, unit_name, sort_order)
JOIN directions d ON d.code = 'economy'
JOIN units      u ON u.name = v.unit_name
ON CONFLICT (direction_id, name) DO NOTHING;

COMMIT;
