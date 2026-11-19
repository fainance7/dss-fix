import os
"""
dag_auto_convert.py

Принимает путь к загруженному через фронт файлу (xlsx/xls/csv/doc/docx/pdf,
передаётся через conf при триггере), прогоняет его через универсальный
детектор (etl/universal_detector.py + etl/document_adapters.py для
PDF/Word), кладёт результат в raw_historical, и автоматически запускает
следующий шаг конвейера (dag_extract_historical).

Это замена прямого вызова dag_build_excel из upload.py — теперь загруженный
через сайт файл реально парсится, а не просто архивируется в /artifacts/.

ВАЖНО: раньше здесь использовался convert_file() из universal_detector.py,
который читает файл напрямую через pd.read_excel() — это работает только
для .xlsx/.xls. Когда добавили поддержку .doc/.docx/.pdf через
document_adapters.extract_sheets(), этот DAG не обновили, из-за чего файлы
этих форматов, успешно загруженные через сайт, не обрабатывались бы
(или обрабатывались бы неверно, в зависимости от поведения pd.read_excel
на чужом формате). Теперь используется extract_sheets() — та же функция,
что и в upload_parser.py для прямого API — поэтому результат идентичен
независимо от того, через какой путь файл попал в систему.

conf, ожидаемый при триггере:
{
    "source_file": "/opt/airflow/artifacts/20260620_..._файл.xlsx",
    "fallback_direction_code": "economy",   # если показатель не сматчился
    "fallback_indicator_name": ""            # подсказка пользователя (опционально)
}
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from sqlalchemy import create_engine

DB_CONN = os.environ["REPORTS_DB_URL"]  # M-3: required, no insecure default

sys.path.insert(0, "/opt/airflow/etl")


def run_universal_convert(**context):
    """
    Запускает universal_detector + document_adapters на файле, указанном
    в conf['source_file']. Несматченные показатели получают
    fallback_direction_code из conf (вместо жёсткого 'unmapped'), чтобы
    ручной выбор пользователя на фронте не терялся, даже если автоопределение
    не справилось.
    """
    conf = context["dag_run"].conf or {}
    source_file = conf.get("source_file")
    fallback_direction = conf.get("fallback_direction_code", "unmapped")
    fallback_indicator_hint = conf.get("fallback_indicator_name", "")

    if not source_file:
        raise ValueError("conf['source_file'] не передан — нечего конвертировать")

    from universal_detector import IndicatorCatalog, convert_sheets
    from document_adapters import extract_sheets

    catalog_path = "/opt/airflow/etl/indicator_catalog.csv"
    aliases_path = "/opt/airflow/etl/indicator_aliases.csv"
    catalog = IndicatorCatalog.from_csv(catalog_path, aliases_path)

    print(f"Каталог: {len(catalog.entries)} показателей, {len(catalog.alias_map)} алиасов")
    print(f"Конвертирую: {source_file}")

    path = Path(source_file)
    content = path.read_bytes()

    try:
        xl, categorical_rows = extract_sheets(
            path.name, content, catalog=catalog, snapshot_year=datetime.now().year
        )
    except Exception as e:
        raise ValueError(f"Не удалось извлечь таблицы из файла {path.name}: {e}")

    rows, stats = convert_sheets(xl, catalog, source_file=path.name)
    rows.extend(categorical_rows)

    if not rows:
        print("[WARN] Файл не дал ни одной строки — структура не распознана "
              "универсальным детектором (ни 'район х года', ни 'год х показатели', "
              "ни 'район х категория'). Нужна ручная обработка.")
        context["ti"].xcom_push(key="rows_loaded", value=0)
        return

    # Несматченные показатели получают direction_code, выбранный пользователем
    # на фронте, вместо безусловного 'unmapped' — так ручной выбор не теряется.
    unmapped_count = 0
    for r in rows:
        if r["direction_code"] == "unmapped":
            r["direction_code"] = fallback_direction
            if fallback_indicator_hint:
                r["comment"] = (r.get("comment", "") + f" | подсказка пользователя: {fallback_indicator_hint}").strip(" |")
            unmapped_count += 1

    engine = create_engine(DB_CONN)
    conn = engine.raw_connection()
    cur = conn.cursor()
    for rec in rows:
        cur.execute(
            "INSERT INTO raw_historical (source_file, data) VALUES (%s, %s)",
            (path.name, json.dumps(rec, ensure_ascii=False)),
        )
    conn.commit()
    cur.close()
    conn.close()

    print(f"✅ Загружено в raw_historical: {len(rows)} записей "
          f"({unmapped_count} с fallback-direction='{fallback_direction}', "
          f"{len(categorical_rows)} из категориальных таблиц)")

    context["ti"].xcom_push(key="rows_loaded", value=len(rows))
    context["ti"].xcom_push(key="unmapped_count", value=unmapped_count)


with DAG(
    "dag_auto_convert",
    default_args={
        "owner": "airflow",
        "retries": 1,
        "retry_delay": timedelta(minutes=2),
    },
    schedule_interval=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["extract", "auto"],
) as dag:

    t1 = PythonOperator(
        task_id="run_universal_convert",
        python_callable=run_universal_convert,
    )

    # Цепочкой запускаем трансформацию и сборку финального Excel —
    # пользователю на фронте не нужно вручную дёргать каждый DAG отдельно.
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
