-- Migration 038: аннотации к значениям показателей в таблицах (ТЗ ФFE.1).
--
-- Эксперт по проверке данных отмечает подозрительные значения не только на карте,
-- но и в таблице значений показателей: аннотация ссылается на indicator_values.id,
-- точка на карте для такой аннотации не нужна.

ALTER TABLE data_annotations
    ADD COLUMN IF NOT EXISTS indicator_value_id uuid REFERENCES indicator_values(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_data_annotations_indicator_value_id ON data_annotations (indicator_value_id);

-- Прежнее ограничение требовало spatial_data_id или layer_id, а карта сохраняет
-- аннотацию к точке без объекта — такие записи отклонялись. Целью аннотации может
-- быть объект, слой, значение показателя или точка на карте.
ALTER TABLE data_annotations DROP CONSTRAINT IF EXISTS data_annotations_target_check;
ALTER TABLE data_annotations ADD CONSTRAINT data_annotations_target_check CHECK (
    spatial_data_id IS NOT NULL OR layer_id IS NOT NULL
    OR indicator_value_id IS NOT NULL OR annotation_point IS NOT NULL
);
