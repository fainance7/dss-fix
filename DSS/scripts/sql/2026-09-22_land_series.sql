-- Дополнение областных рядов земель по исправленным статданным заказчика (22.09.2026).
-- Сгенерировано scripts/import_corrected_oblast_series.py --sql
-- Пишет только отсутствующие значения: каждый INSERT защищён NOT EXISTS,
-- поэтому повторный прогон ничего не меняет и ничего не перезаписывает.
BEGIN;

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2014-01-01', DATE '2014-12-31', 7168.461, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь земель сельскохозяйственного назначения' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2014-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2015-01-01', DATE '2015-12-31', 7228.526, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь земель сельскохозяйственного назначения' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2015-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2016-01-01', DATE '2016-12-31', 6916.927, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь земель сельскохозяйственного назначения' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2016-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2017-01-01', DATE '2017-12-31', 7014.433, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь земель сельскохозяйственного назначения' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2017-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2018-01-01', DATE '2018-12-31', 7154.833, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь земель сельскохозяйственного назначения' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2018-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2019-01-01', DATE '2019-12-31', 7198.599, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь земель сельскохозяйственного назначения' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2019-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2020-01-01', DATE '2020-12-31', 7342.322, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь земель сельскохозяйственного назначения' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2020-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2021-01-01', DATE '2021-12-31', 7287.552, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь земель сельскохозяйственного назначения' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2021-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2022-01-01', DATE '2022-12-31', 7271.201, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь земель сельскохозяйственного назначения' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2022-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2023-01-01', DATE '2023-12-31', 7232.512, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь земель сельскохозяйственного назначения' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2023-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2024-01-01', DATE '2024-12-31', 7163.1, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь земель сельскохозяйственного назначения' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2024-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2014-01-01', DATE '2014-12-31', 4855.11, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь пашни' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2014-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2015-01-01', DATE '2015-12-31', 4815.875, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь пашни' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2015-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2016-01-01', DATE '2016-12-31', 4862.119, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь пашни' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2016-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2017-01-01', DATE '2017-12-31', 4891.761, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь пашни' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2017-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2018-01-01', DATE '2018-12-31', 4917.983, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь пашни' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2018-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2019-01-01', DATE '2019-12-31', 4926.954, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь пашни' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2019-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2020-01-01', DATE '2020-12-31', 4944.119, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь пашни' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2020-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2021-01-01', DATE '2021-12-31', 4944.119, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь пашни' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2021-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2022-01-01', DATE '2022-12-31', 4959.478, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь пашни' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2022-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2023-01-01', DATE '2023-12-31', 4987.501, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь пашни' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2023-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2024-01-01', DATE '2024-12-31', 4982.2, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Площадь пашни' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2024-01-01');

-- показателя не было в справочнике, хотя он входит в расчёт УР
INSERT INTO indicators (id, direction_id, unit_id, name, sort_order, is_active, created_at)
SELECT gen_random_uuid(), d.id, u.id, 'Посевная площадь на орошении', 999, true, now()
FROM directions d, units u
WHERE d.code = 'land' AND u.name = 'тыс.га'
  AND NOT EXISTS (SELECT 1 FROM indicators WHERE name = 'Посевная площадь на орошении');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2014-01-01', DATE '2014-12-31', 9.961, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Посевная площадь на орошении' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2014-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2015-01-01', DATE '2015-12-31', 9.964, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Посевная площадь на орошении' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2015-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2016-01-01', DATE '2016-12-31', 11.317, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Посевная площадь на орошении' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2016-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2017-01-01', DATE '2017-12-31', 11.324, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Посевная площадь на орошении' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2017-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2018-01-01', DATE '2018-12-31', 11.324, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Посевная площадь на орошении' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2018-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2019-01-01', DATE '2019-12-31', 11.324, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Посевная площадь на орошении' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2019-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2020-01-01', DATE '2020-12-31', 11.324, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Посевная площадь на орошении' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2020-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2021-01-01', DATE '2021-12-31', 11.324, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Посевная площадь на орошении' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2021-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2022-01-01', DATE '2022-12-31', 13.364, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Посевная площадь на орошении' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2022-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2023-01-01', DATE '2023-12-31', 17.329, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Посевная площадь на орошении' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2023-01-01');

INSERT INTO indicator_values (id, indicator_id, region_id, period_type, period_start, period_end, value, validation_status, created_at)
SELECT gen_random_uuid(), i.id, r.id, 'year', DATE '2024-01-01', DATE '2024-12-31', 17.329, 'approved', now()
FROM indicators i, regions r
WHERE i.name = 'Посевная площадь на орошении' AND r.name = 'Северо-Казахстанская область' AND r.level = 1
  AND NOT EXISTS (
    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id
      AND v.region_id = r.id AND v.period_start = DATE '2024-01-01');

COMMIT;
