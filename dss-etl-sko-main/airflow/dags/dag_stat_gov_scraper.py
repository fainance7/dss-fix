import os
"""
dag_stat_gov_scraper.py

Реализует Задачу 3.2 плана: скрапинг файлов с stat.gov.kz и автоматическая
загрузка их в стандартный ETL-конвейер.

Механика (на основе реальной структуры страницы, проверенной curl+bs4):
  - Страницы региона (например https://stat.gov.kz/ru/region/sko/) либо
    раздел "Электронные таблицы" построены из элементов
    <a class="doc-item" href="/api/iblock/element/region/{ID}/file/ru/">
    с .doc-item-title (название показателя) и .doc-item-info (дата/размер).
  - Эндпоинт /api/iblock/element/region/{ID}/file/ru/ отдаёт СРАЗУ бинарный
    файл (подтверждено: `file` показал "Microsoft Excel 2007+" без какого-
    либо промежуточного JSON) — поэтому скрапер скачивает по этому href
    напрямую, без двухступенчатой логики "получить метаданные → скачать".
  - Реальное расширение файла определяется ПОСЛЕ скачивания по магическим
    байтам/Content-Type заголовку, а не по тексту .doc-item-info — для
    нескольких файлов на реальной странице info содержит только дату без
    указания формата (см. примеры "22.06.2026" без "xlsx").

ВАЖНО — отличие от requests.get() "в один шаг": сайт отдаёт 404 без
заголовка User-Agent (проверено и на этом сервере, и через внешний fetch).
Все запросы здесь ОБЯЗАТЕЛЬНО идут с реалистичным User-Agent.

conf, ожидаемый при триггере:
{
    "source_url": "https://stat.gov.kz/ru/region/sko/",  # обязательно
    "direction_code": "economy",   # fallback для несматченных показателей
    "max_files": 30                 # опционально, защита от случайного
}                                    # скрапинга огромного раздела целиком
"""
import io
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from sqlalchemy import create_engine
import json

DB_CONN = os.environ["REPORTS_DB_URL"]  # M-3: required, no insecure default

# Без этого заголовка stat.gov.kz отдаёт 404 на все страницы — подтверждено
# эмпирически (curl без -A падал, curl с -A "Mozilla/5.0..." работал).
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}

ALLOWED_EXTENSIONS = {"xlsx", "xls", "docx", "doc", "pdf"}

# Магические байты для определения реального формата файла по содержимому,
# независимо от того, что написано в тексте ссылки на странице (см.
# docstring — не все элементы страницы указывают формат в .doc-item-info).
_MAGIC_BYTES = {
    b"PK\x03\x04": "xlsx",          # xlsx/docx — оба ZIP-контейнеры,
                                     # различаются дальше по внутренней структуре
    b"\xd0\xcf\x11\xe0": "xls",     # старый формат Office (xls/doc)
    b"%PDF": "pdf",
}


def _detect_extension(content: bytes, fallback_hint: str = "") -> str:
    """
    Определяет реальное расширение файла по магическим байтам. Для ZIP-based
    форматов (xlsx/docx, отличить друг от друга по первым байтам нельзя)
    использует подсказку из текста ссылки на странице, либо ищет внутри
    архива характерные имена файлов ([Content_Types].xml совпадает для
    обоих, но docx содержит word/, xlsx содержит xl/).
    """
    if content[:4] == b"PK\x03\x04":
        if b"word/" in content[:2000] or "doc" in fallback_hint.lower():
            return "docx"
        return "xlsx"
    if content[:4] == b"\xd0\xcf\x11\xe0":
        return "doc" if "doc" in fallback_hint.lower() else "xls"
    if content[:4] == b"%PDF":
        return "pdf"
    return ""


def scrape_and_download(**context):
    """
    Скачивает страницу source_url, находит все элементы a.doc-item,
    скачивает каждый файл, прогоняет через universal_detector +
    document_adapters (тот же движок, что у /upload/file и dag_auto_convert
    — единый источник истины для распознавания структуры файлов, как и
    требует план в Задаче 3.2: "передает их парсерам, используемым для
    ручной загрузки файлов").
    """
    conf = context["dag_run"].conf or {}
    source_url = conf.get("source_url")
    fallback_direction = conf.get("direction_code", "economy")
    max_files = int(conf.get("max_files", 30))

    if not source_url:
        raise ValueError("conf['source_url'] не передан — нечего скрапить")

    sys.path.insert(0, "/opt/airflow/etl")
    from universal_detector import IndicatorCatalog, convert_sheets
    from document_adapters import extract_sheets

    resp = requests.get(source_url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    items = soup.select("a.doc-item")
    print(f"Найдено элементов doc-item на странице: {len(items)}")

    if len(items) > max_files:
        print(f"[WARN] Найдено {len(items)} файлов, ограничиваю до max_files={max_files}")
        items = items[:max_files]

    catalog = IndicatorCatalog.from_csv(
        "/opt/airflow/etl/indicator_catalog.csv",
        "/opt/airflow/etl/indicator_aliases.csv",
    )

    base_url = re.match(r"https?://[^/]+", source_url).group(0)
    snapshot_year = datetime.now().year

    all_rows = []
    succeeded, failed = [], []

    for item in items:
        href = item.get("href", "")
        title_el = item.select_one(".doc-item-title")
        info_el = item.select_one(".doc-item-info")
        title = title_el.get_text(strip=True) if title_el else "Без названия"
        info_hint = info_el.get_text(strip=True) if info_el else ""

        if not href:
            continue
        file_url = href if href.startswith("http") else base_url + href

        try:
            file_resp = requests.get(file_url, headers=HEADERS, timeout=30)
            file_resp.raise_for_status()
            content = file_resp.content

            ext = _detect_extension(content, info_hint)
            if ext not in ALLOWED_EXTENSIONS:
                failed.append({"title": title, "url": file_url,
                              "reason": f"Не удалось определить поддерживаемый формат "
                                        f"(подсказка страницы: {info_hint!r})"})
                continue

            filename = re.sub(r"[^\w\s.-]", "", title)[:100] + f".{ext}"

            xl, categorical_rows = extract_sheets(
                filename, content, catalog=catalog, snapshot_year=snapshot_year
            )
            rows, _ = convert_sheets(xl, catalog, source_file=filename, log_fn=lambda m: None)
            rows.extend(categorical_rows)

            if not rows:
                failed.append({"title": title, "url": file_url,
                              "reason": "0 строк — структура не распознана детектором"})
                continue

            for r in rows:
                if r["direction_code"] == "unmapped":
                    r["direction_code"] = fallback_direction

            all_rows.extend(rows)
            succeeded.append({"title": title, "url": file_url, "rows": len(rows)})
            print(f"[OK] {title[:60]}: {len(rows)} строк")

        except Exception as e:
            failed.append({"title": title, "url": file_url, "reason": str(e)})
            print(f"[ERROR] {title[:60]}: {e}")

    if not all_rows:
        raise ValueError(
            f"Не удалось извлечь данные ни из одного файла. "
            f"Найдено {len(items)} файлов, успешно обработано 0. "
            f"Ошибки: {failed[:5]}"
        )

    engine = create_engine(DB_CONN)
    conn = engine.raw_connection()
    cur = conn.cursor()
    for rec in all_rows:
        cur.execute(
            "INSERT INTO raw_historical (source_file, data) VALUES (%s, %s)",
            (f"scraper:{source_url}", json.dumps(rec, ensure_ascii=False)),
        )
    conn.commit()
    cur.close()
    conn.close()

    print(f"✅ Скрапинг завершён: {len(succeeded)}/{len(items)} файлов обработано, "
          f"{len(all_rows)} строк сохранено в raw_historical")
    if failed:
        print(f"⚠️ Не обработано: {len(failed)} файлов — {[f['title'][:40] for f in failed]}")

    context["ti"].xcom_push(key="files_succeeded", value=len(succeeded))
    context["ti"].xcom_push(key="files_failed", value=len(failed))
    context["ti"].xcom_push(key="rows_loaded", value=len(all_rows))


with DAG(
    "dag_stat_gov_scraper",
    default_args={
        "owner": "airflow",
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
    },
    schedule_interval=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["extract", "scraper"],
) as dag:

    t1 = PythonOperator(
        task_id="scrape_and_download",
        python_callable=scrape_and_download,
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
