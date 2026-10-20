-- 031: статус задачи «accepted» (Принята).
--
-- Приёмка выполнения задачи акиматом области (POST /tasks/{id}/accept) ставит
-- статус accepted, но ограничение таблицы из 001 его не допускало — приёмка
-- падала бы на уровне БД. Миграция идемпотентна (CI применяет все миграции).

BEGIN;

ALTER TABLE regional_tasks DROP CONSTRAINT IF EXISTS regional_tasks_status_check;
ALTER TABLE regional_tasks ADD CONSTRAINT regional_tasks_status_check
    CHECK (status IN ('pending', 'in_progress', 'completed', 'cancelled', 'accepted'));

COMMIT;
