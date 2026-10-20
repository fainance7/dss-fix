-- Migration 024: слияние дублирующихся единиц измерения «шт» и «шт.»
--
-- В units существуют две идентичные по смыслу строки: «шт» (description «шт»)
-- и «шт.» (description «Штука»). Каноническая — «шт.» (из сидов 007). Дубликат
-- «шт» переназначаем на «шт.» во всех ссылках (indicators.unit_id — единственный
-- FK на units, ON DELETE RESTRICT) и удаляем.
--
-- Идемпотентно, ссылки резолвятся по name (UUID на проде отличаются от dev).
-- Применять на dev (dss-postgres :5433) и на проде (dss_db).

BEGIN;

-- Перепривязываем показатели с дубликата «шт» на каноническую «шт.».
-- Делаем только если обе строки существуют, иначе UPDATE пропускается.
UPDATE indicators
   SET unit_id = (SELECT id FROM units WHERE name = 'шт.')
 WHERE unit_id = (SELECT id FROM units WHERE name = 'шт')
   AND EXISTS (SELECT 1 FROM units WHERE name = 'шт.');

-- Удаляем дубликат (после перепривязки RESTRICT-FK уже не держит строку).
DELETE FROM units
 WHERE name = 'шт'
   AND EXISTS (SELECT 1 FROM units WHERE name = 'шт.');

COMMIT;
