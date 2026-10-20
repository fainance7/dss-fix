import os
"""
dag_gdrive_sync.py

Реализует Задачу 3.3 плана: интеграция с Google Drive.

Механика:
  - Service Account (см. etl/gdrive_service_account.json) должен иметь
    права "Читатель" (Viewer) на каждую папку, перечисленную в
    GDRIVE_FOLDER_IDS — это делает ВЛАДЕЛЕЦ папки на стороне Google Drive
    UI ("Поделиться" → email бота), не этот код.
  - files().list(q="'{folder_id}' in parents") находит файлы в каждой
    папке (только прямые файлы, не рекурсивно по подпапкам — см.
    _list_files_in_folder).
  - files().get_media() скачивает бинарное содержимое — РАБОТАЕТ для
    обычных файлов (.xlsx, .docx, .pdf, загруженных как есть). Google
    Docs/Sheets/Slides (нативные форматы Google, не файлы в привычном
    смысле) требуют другого метода (files().export()) — см. обработку
    googleDoc-типов ниже, такие файлы экспортируются в xlsx/docx перед
    обработкой.
  - Каждый скачанный файл прогоняется через universal_detector +
    document_adapters — тот же движок, что и везде в проекте (план
    прямо требует переиспользовать парсеры из Задачи 1.1).
  - schedule_interval="@daily" — проверяет папки раз в сутки, как и
    требует план. Запоминает уже обработанные file_id (см. gdrive_synced_files),
    чтобы повторно не загружать один и тот же файл при каждом дневном
    запуске — обрабатываются только НОВЫЕ файлы (по modifiedTime), либо
    изменённые с прошлой синхронизации.

conf, ожидаемый при ручном триггере (опционально — при автоматическом
запуске по расписанию conf пуст, используются значения по умолчанию):
{
    "folder_ids": ["1qjft3YiO6gfkz3rXBeltGcHcZ1rWDh-P", ...],  # опционально,
                                                                  # иначе все
                                                                  # из GDRIVE_FOLDER_IDS
    "direction_code": "labor",   # fallback для несматченных показателей
    "force_resync": false         # true = игнорировать историю синхронизации,
}                                  # перечитать все файлы заново
"""
import io
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from sqlalchemy import create_engine

DB_CONN = os.environ["REPORTS_DB_URL"]  # M-3: required, no insecure default
GDRIVE_KEY_PATH = "/opt/airflow/etl/gdrive_service_account.json"

# Папки, переданные владельцем (см. "Перечень собранных первичных
# материалов") — имя группы используется как fallback direction_code,
# если показатель внутри файла не сматчится с каталогом и не передан
# явный direction_code через conf.
GDRIVE_FOLDERS = {
    "1qjft3YiO6gfkz3rXBeltGcHcZ1rWDh-P": ("Трудовые ресурсы", "labor"),
    "17PrME0b00munZzKVhcbgsXEguZAAQ85U": ("Водные ресурсы", "water"),
    "17ArrQxD-cXGLnzHwGX4l1JuIZAkA4EXA": ("Земельные ресурсы ОК", "land"),
    "1aAxwkpS8g5slT0qUfLVyDD3LxI-P2H_-": ("Экология, туризм и недра", "ecology"),
    "1t_Jr2dxbYFcgjZOxVrLYLqiMLZaJKNa0": ("Агрометеорология", "climate"),
    "1QJSmWW9LbqghsVcFQk_wNSYwFSGkhsnH": ("ДЗЗ и Картография", "land"),
}

# Google Docs/Sheets/Slides не имеют бинарного содержимого, скачиваемого
# через get_media — их нужно ЭКСПОРТИРОВАТЬ в обычный формат сначала.
_GOOGLE_EXPORT_MIME = {
    "application/vnd.google-apps.spreadsheet":
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "application/vnd.google-apps.document":
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
}

ALLOWED_EXTENSIONS = {"xlsx", "xls", "csv", "doc", "docx", "pdf"}


def _get_drive_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        GDRIVE_KEY_PATH,
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    return build("drive", "v3", credentials=creds)


def _list_files_in_folder(service, folder_id: str) -> list[dict]:
    """
    Возвращает список файлов (только прямые, не рекурсивно по подпапкам)
    внутри указанной папки. Каждый элемент содержит id, name, mimeType,
    modifiedTime.

    Не углубляется в подпапки намеренно — план описывает "проверять папку",
    не "проверять всю иерархию"; если в реальных папках есть вложенные
    подпапки с файлами, это легко расширить, рекурсивно вызывая эту же
    функцию для каждого найденного элемента с mimeType
    'application/vnd.google-apps.folder'.
    """
    results = []
    page_token = None
    query = f"'{folder_id}' in parents and trashed=false"

    while True:
        response = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
            pageToken=page_token,
        ).execute()
        results.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return results


def _download_file_content(service, file_meta: dict) -> tuple[bytes, str]:
    """
    Скачивает содержимое файла. Для обычных файлов — через get_media.
    Для Google Docs/Sheets — через export (см. _GOOGLE_EXPORT_MIME).

    Возвращает (content_bytes, effective_filename) — effective_filename
    включает правильное расширение даже для экспортированных Google-файлов
    (у которых исходное name в Drive обычно без расширения вообще).
    """
    file_id = file_meta["id"]
    name = file_meta["name"]
    mime_type = file_meta.get("mimeType", "")

    if mime_type in _GOOGLE_EXPORT_MIME:
        export_mime, ext = _GOOGLE_EXPORT_MIME[mime_type]
        request = service.files().export_media(fileId=file_id, mimeType=export_mime)
        filename = f"{name}.{ext}"
    else:
        request = service.files().get_media(fileId=file_id)
        filename = name

    content = request.execute()
    return content, filename


def _already_synced(cur, file_id: str, modified_time: str, force_resync: bool) -> bool:
    """
    Проверяет, был ли этот файл (по file_id) уже обработан с тем же
    modifiedTime — если да и force_resync=False, пропускаем повторную
    обработку. Это и даёт "раз в сутки проверять, появились ли новые
    файлы" вместо повторной загрузки всего содержимого папок каждый день.
    """
    if force_resync:
        return False
    cur.execute(
        "SELECT 1 FROM gdrive_synced_files WHERE file_id = %s AND modified_time = %s",
        (file_id, modified_time),
    )
    return cur.fetchone() is not None


def _mark_synced(cur, file_id: str, name: str, modified_time: str, rows_count: int):
    cur.execute(
        """
        INSERT INTO gdrive_synced_files (file_id, file_name, modified_time, rows_extracted, synced_at)
        VALUES (%s, %s, %s, %s, now())
        ON CONFLICT (file_id) DO UPDATE SET
            file_name = EXCLUDED.file_name,
            modified_time = EXCLUDED.modified_time,
            rows_extracted = EXCLUDED.rows_extracted,
            synced_at = now()
        """,
        (file_id, name, modified_time, rows_count),
    )


def sync_gdrive(**context):
    conf = context["dag_run"].conf or {}
    folder_ids = conf.get("folder_ids") or list(GDRIVE_FOLDERS.keys())
    fallback_direction_override = conf.get("direction_code")
    force_resync = bool(conf.get("force_resync", False))

    if not Path(GDRIVE_KEY_PATH).exists():
        raise ValueError(
            f"Ключ Service Account не найден по пути {GDRIVE_KEY_PATH}. "
            "Скопируй JSON-ключ туда перед запуском этого DAG."
        )

    sys.path.insert(0, "/opt/airflow/etl")
    from universal_detector import IndicatorCatalog, convert_sheets
    from document_adapters import extract_sheets

    service = _get_drive_service()
    catalog = IndicatorCatalog.from_csv(
        "/opt/airflow/etl/indicator_catalog.csv",
        "/opt/airflow/etl/indicator_aliases.csv",
    )

    engine = create_engine(DB_CONN)
    conn = engine.raw_connection()
    cur = conn.cursor()

    snapshot_year = datetime.now().year
    all_rows = []
    succeeded, failed, skipped = [], [], []

    for folder_id in folder_ids:
        group_name, default_direction = GDRIVE_FOLDERS.get(folder_id, (folder_id, "economy"))
        fallback_direction = fallback_direction_override or default_direction

        try:
            files = _list_files_in_folder(service, folder_id)
        except Exception as e:
            print(f"[ERROR] Не удалось получить список файлов папки "
                  f"'{group_name}' ({folder_id}): {e}")
            print(f"  Вероятная причина: бот ещё не получил доступ "
                  f"'Читатель' к этой папке от владельца.")
            failed.append({"folder": group_name, "reason": str(e)})
            continue

        print(f"Папка '{group_name}' ({folder_id}): найдено {len(files)} файлов")

        for file_meta in files:
            name = file_meta["name"]
            mime_type = file_meta.get("mimeType", "")
            modified_time = file_meta.get("modifiedTime", "")

            if mime_type == "application/vnd.google-apps.folder":
                continue  # подпапки — не углубляемся, см. docstring _list_files_in_folder

            if _already_synced(cur, file_meta["id"], modified_time, force_resync):
                skipped.append(name)
                continue

            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            is_google_native = mime_type in _GOOGLE_EXPORT_MIME
            if not is_google_native and ext not in ALLOWED_EXTENSIONS:
                failed.append({"folder": group_name, "file": name,
                              "reason": f"Формат .{ext} не поддерживается"})
                continue

            try:
                content, filename = _download_file_content(service, file_meta)

                xl, categorical_rows = extract_sheets(
                    filename, content, catalog=catalog, snapshot_year=snapshot_year
                )
                rows, _ = convert_sheets(xl, catalog, source_file=filename, log_fn=lambda m: None)
                rows.extend(categorical_rows)

                if not rows:
                    failed.append({"folder": group_name, "file": name,
                                  "reason": "0 строк — структура не распознана детектором"})
                    _mark_synced(cur, file_meta["id"], name, modified_time, 0)
                    continue

                for r in rows:
                    if r["direction_code"] == "unmapped":
                        r["direction_code"] = fallback_direction

                all_rows.extend(rows)
                succeeded.append({"folder": group_name, "file": name, "rows": len(rows)})
                _mark_synced(cur, file_meta["id"], name, modified_time, len(rows))
                print(f"[OK] {group_name}/{name}: {len(rows)} строк")

            except Exception as e:
                failed.append({"folder": group_name, "file": name, "reason": str(e)})
                print(f"[ERROR] {group_name}/{name}: {e}")

    conn.commit()

    if all_rows:
        for rec in all_rows:
            cur.execute(
                "INSERT INTO raw_historical (source_file, data) VALUES (%s, %s)",
                (f"gdrive:{rec['indicator_name'][:50]}", json.dumps(rec, ensure_ascii=False)),
            )
        conn.commit()

    cur.close()
    conn.close()

    print(f"✅ Синхронизация завершена: {len(succeeded)} файлов обработано, "
          f"{len(skipped)} уже синхронизировано ранее (пропущено), "
          f"{len(failed)} с ошибками, {len(all_rows)} строк сохранено")

    if not all_rows and not skipped:
        # Ничего нового и ничего ранее обработанного — вероятно доступ
        # ещё не открыт ни к одной папке. Не роняем DAG как failed в этом
        # случае (это не ошибка кода), просто явно предупреждаем.
        print("[WARN] Не найдено ни одного файла ни в одной папке. "
              "Проверь, что Service Account получил права 'Читатель' "
              "хотя бы на одну из папок.")

    context["ti"].xcom_push(key="files_succeeded", value=len(succeeded))
    context["ti"].xcom_push(key="files_skipped", value=len(skipped))
    context["ti"].xcom_push(key="files_failed", value=len(failed))
    context["ti"].xcom_push(key="rows_loaded", value=len(all_rows))


with DAG(
    "dag_gdrive_sync",
    default_args={
        "owner": "airflow",
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
    },
    schedule_interval="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["extract", "gdrive"],
) as dag:

    t1 = PythonOperator(
        task_id="sync_gdrive",
        python_callable=sync_gdrive,
    )

    t2 = TriggerDagRunOperator(
        task_id="trigger_extract_historical",
        trigger_dag_id="dag_extract_historical",
        wait_for_completion=True,
        poke_interval=5,
    )

    t3 = TriggerDagRunOperator(
        task_id="trigger_build_excel",
        trigger_dag_id="dag_build_excel",
        wait_for_completion=True,
        poke_interval=5,
    )

    t1 >> t2 >> t3
