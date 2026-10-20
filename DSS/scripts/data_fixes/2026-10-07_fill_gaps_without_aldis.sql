-- Закрытие пробелов дашбордов без запроса к смежной команде (отчёт о незаполненных данных, 07.10.2026).
-- Идемпотентно: повторный запуск ничего не меняет.
BEGIN;

CREATE TEMP TABLE fx_ind ON COMMIT DROP AS
SELECT i.id, d.code, i.name FROM indicators i JOIN directions d ON d.id = i.direction_id;

CREATE TEMP TABLE fx_const ON COMMIT DROP AS
SELECT (SELECT id FROM regions WHERE level = 1) AS oblast,
       '6e560760-1f39-4c7e-8680-d7e1d5ffe00e'::uuid AS src_calc,   -- «Расчёт ТОО GRAPH»
       '9fa8de87-ed30-4090-a65e-f015c1aa3b9e'::uuid AS src_land;   -- данные по земельным ресурсам СКО

-- 1. Сенокосы и Многолетние насаждения 2004–2023: строки партии dss_transport.xlsx (24.06),
--    не принятые из-за заголовка таблицы вместо имени показателя.
INSERT INTO indicator_values (indicator_id, region_id, period_type, period_start, period_end, value,
                              source_id, validation_status, validated_at)
SELECT t.id, c.oblast, 'year', (r.raw_payload->>'period_start')::date, (r.raw_payload->>'period_end')::date,
       (r.raw_payload->>'value')::float8, c.src_land, 'approved', now()
FROM dataset_intake_rows r
JOIN fx_ind t ON t.code = 'land'
             AND t.name = split_part(r.raw_payload->>'indicator_name', ' — ', 2)
             AND t.name IN ('Сенокосы', 'Многолетние насаждения')
CROSS JOIN fx_const c
WHERE r.status = 'invalid'
ON CONFLICT ON CONSTRAINT iv_unique_value DO NOTHING;

UPDATE dataset_intake_rows r SET status = 'imported', indicator_id = t.id,
       region_id = (SELECT oblast FROM fx_const)
FROM fx_ind t
WHERE r.status = 'invalid' AND t.code = 'land'
  AND t.name = split_part(r.raw_payload->>'indicator_name', ' — ', 2)
  AND t.name IN ('Сенокосы', 'Многолетние насаждения');

-- 2. Категории земельного фонда на дашборде «Земельные ресурсы»: те же ряды лежат в других направлениях.
INSERT INTO indicator_values (indicator_id, region_id, period_type, period_start, period_end, value,
                              source_id, validation_status, validated_at)
SELECT dst.id, v.region_id, v.period_type, v.period_start, v.period_end, v.value, v.source_id, 'approved', now()
FROM (VALUES
        ('transport', 'Площадь земель промышленности и транспорта',
         'Земли промышленности, транспорта, связи и иного несельскохозяйственного назначения'),
        ('ecology', 'Площадь особо охраняемых природных территорий',
         'Земли особо охраняемых природных территорий')
     ) m(src_code, src_name, dst_name)
JOIN fx_ind s ON s.code = m.src_code AND s.name = m.src_name
JOIN fx_ind dst ON dst.code = 'land' AND dst.name = m.dst_name
JOIN indicator_values v ON v.indicator_id = s.id AND v.validation_status = 'approved'
ON CONFLICT ON CONSTRAINT iv_unique_value DO NOTHING;

-- 3. «Среднемесячная номинальная заработная плата» (Экономика): отклонённый индекс роста (~100)
--    заменяется рядом в тенге из «Трудовых ресурсов».
INSERT INTO indicator_values (indicator_id, region_id, period_type, period_start, period_end, value,
                              source_id, validation_status, validated_at)
SELECT e.id, v.region_id, v.period_type, v.period_start, v.period_end, v.value, v.source_id, 'approved', now()
FROM fx_ind e
JOIN fx_ind l ON l.code = 'labor' AND l.name = e.name
JOIN indicator_values v ON v.indicator_id = l.id AND v.validation_status = 'approved' AND v.period_type = 'year'
WHERE e.code = 'economy' AND e.name = 'Среднемесячная номинальная заработная плата'
ON CONFLICT ON CONSTRAINT iv_unique_value DO UPDATE
   SET value = EXCLUDED.value, source_id = EXCLUDED.source_id,
       validation_status = 'approved', validated_at = now(), validated_by = NULL
 WHERE indicator_values.validation_status = 'rejected';

-- 4. Расчётные показатели на душу населения (область; население в людях).
WITH pop AS (
    SELECT v.period_start, v.period_end, v.value
    FROM indicator_values v JOIN fx_ind i ON i.id = v.indicator_id
    WHERE i.code = 'social_demog' AND i.name = 'Численность населения (всего)'
      AND v.validation_status = 'approved' AND v.region_id = (SELECT oblast FROM fx_const)
      AND v.value > 100000
), calc AS (
    -- выбросы, тыс. т → кг/чел
    SELECT 'ecology' AS code, 'Выбросы на 1 жителя' AS name, v.period_start, v.period_end,
           v.value * 1e6 / p.value AS value
    FROM indicator_values v JOIN fx_ind i ON i.id = v.indicator_id
    JOIN pop p USING (period_start)
    WHERE i.code = 'ecology' AND i.name = 'Выбросы загрязняющих веществ (всего)'
      AND v.validation_status = 'approved' AND v.region_id = (SELECT oblast FROM fx_const)
    UNION ALL
    -- валовая продукция СХ, млрд тенге → тенге/чел
    SELECT 'agriculture', 'Стоимость валовой продукции сельского хозяйства на душу населения',
           v.period_start, v.period_end, v.value * 1e9 / p.value
    FROM indicator_values v JOIN fx_ind i ON i.id = v.indicator_id
    JOIN pop p USING (period_start)
    WHERE i.code = 'economy' AND i.name = 'Валовая продукция сельского хозяйства'
      AND v.validation_status = 'approved' AND v.region_id = (SELECT oblast FROM fx_const)
)
INSERT INTO indicator_values (indicator_id, region_id, period_type, period_start, period_end, value,
                              source_id, validation_status, validated_at)
SELECT t.id, c.oblast, 'year', calc.period_start, calc.period_end, round(calc.value::numeric, 1)::float8,
       c.src_calc, 'approved', now()
FROM calc JOIN fx_ind t ON t.code = calc.code AND t.name = calc.name
CROSS JOIN fx_const c
ON CONFLICT ON CONSTRAINT iv_unique_value DO NOTHING;

INSERT INTO audit_log (action, entity_type, details)
VALUES ('data_fix', 'indicator_value',
        '{"script": "scripts/data_fixes/2026-10-07_fill_gaps_without_aldis.sql", "comment": "Закрытие пробелов дашбордов без запроса к смежной команде"}');

COMMIT;
