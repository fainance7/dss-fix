-- Migration 011: Extension of units + indicators for weather / water / population / agriculture.
-- Минимальный набор показателей для первого ETL-импорта из reports_db (метео, демография).

BEGIN;

-- 1. Дополнительные единицы измерения
INSERT INTO units (name, description) VALUES
    ('°C',     'Градус Цельсия'),
    ('мм',     'Миллиметр (осадки)'),
    ('м³/с',   'Кубический метр в секунду (расход воды)'),
    ('мг/л',   'Миллиграмм на литр (концентрация)'),
    ('кВт·ч',  'Киловатт-час'),
    ('гол.',   'Голова (поголовье скота)'),
    ('тыс.га', 'Тысяча гектаров')
ON CONFLICT (name) DO NOTHING;

-- 2. Базовые показатели для climate
INSERT INTO indicators (direction_id, unit_id, name, sort_order)
SELECT d.id, u.id, v.name, v.sort_order
FROM (VALUES
    ('Среднемесячная температура воздуха',                   '°C', 10),
    ('Сумма месячных осадков',                                'мм', 20),
    ('Среднегодовая температура воздуха',                    '°C', 30),
    ('Годовая сумма осадков',                                 'мм', 40),
    ('Сумма активных температур выше 10°C',                  '°C', 50)
) AS v(name, unit_name, sort_order)
JOIN directions d ON d.code = 'climate'
JOIN units      u ON u.name = v.unit_name
ON CONFLICT (direction_id, name) DO NOTHING;

-- 3. Базовые показатели для water
INSERT INTO indicators (direction_id, unit_id, name, sort_order)
SELECT d.id, u.id, v.name, v.sort_order
FROM (VALUES
    ('Среднемесячный расход воды',                            'м³/с', 10),
    ('Среднегодовой расход воды',                             'м³/с', 20),
    ('Концентрация загрязняющих веществ',                     'мг/л', 30)
) AS v(name, unit_name, sort_order)
JOIN directions d ON d.code = 'water'
JOIN units      u ON u.name = v.unit_name
ON CONFLICT (direction_id, name) DO NOTHING;

-- 4. Базовые показатели для social_demog
INSERT INTO indicators (direction_id, unit_id, name, sort_order)
SELECT d.id, u.id, v.name, v.sort_order
FROM (VALUES
    ('Численность населения',                                 'человек', 10),
    ('Численность городского населения',                      'человек', 20),
    ('Численность сельского населения',                       'человек', 30)
) AS v(name, unit_name, sort_order)
JOIN directions d ON d.code = 'social_demog'
JOIN units      u ON u.name = v.unit_name
ON CONFLICT (direction_id, name) DO NOTHING;

-- 5. Базовые показатели для economy (расширение по сельхоз/животноводству)
INSERT INTO indicators (direction_id, unit_id, name, sort_order)
SELECT d.id, u.id, v.name, v.sort_order
FROM (VALUES
    ('Поголовье крупного рогатого скота',                     'гол.',    200),
    ('Поголовье овец и коз',                                  'гол.',    210),
    ('Поголовье свиней',                                      'гол.',    220),
    ('Поголовье лошадей',                                     'гол.',    230),
    ('Поголовье птиц',                                        'гол.',    240),
    ('Площадь сельскохозяйственных угодий',                   'тыс.га',  250)
) AS v(name, unit_name, sort_order)
JOIN directions d ON d.code = 'economy'
JOIN units      u ON u.name = v.unit_name
ON CONFLICT (direction_id, name) DO NOTHING;

COMMIT;
