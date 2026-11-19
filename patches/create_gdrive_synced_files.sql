-- Задача 3.3: таблица для отслеживания уже синхронизированных файлов
-- с Google Drive. dag_gdrive_sync.py использует её, чтобы при ежедневном
-- запуске обрабатывать только НОВЫЕ или ИЗМЕНЁННЫЕ файлы (по modifiedTime),
-- а не перечитывать всю папку с нуля каждый день.

CREATE TABLE IF NOT EXISTS gdrive_synced_files (
    file_id         TEXT PRIMARY KEY,
    file_name       TEXT NOT NULL,
    modified_time   TEXT NOT NULL,
    rows_extracted  INTEGER NOT NULL DEFAULT 0,
    synced_at       TIMESTAMP NOT NULL DEFAULT now()
);

COMMENT ON TABLE gdrive_synced_files IS
    'История синхронизации файлов с Google Drive (dag_gdrive_sync.py).';
