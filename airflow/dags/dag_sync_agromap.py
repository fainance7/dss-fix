#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dag_sync_agromap.py — обёртка над etl/agromap_sync/sync_agromap.py.

На старте — БЕЗ расписания (schedule_interval=None), запуск только вручную
через Airflow UI/CLI, пока не пройдено несколько уверенных ручных прогонов
и не согласована реальная частота изменений с источником agromap.
После — предполагаемый переход на schedule_interval='@weekly' (решение
на будущее, требует отдельного явного изменения этого файла, не подразумевается
само собой).
"""

import sys
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.exceptions import AirflowException

sys.path.insert(0, "/opt/airflow/etl/agromap_sync")

REGION = "sko"
STALE_RUNNING_HOURS = 2  # sync_log.status='running' дольше этого — считаем зомби
                          # (процесс убит извне, finally не отработал; см.
                          # agromap_etap3_kontekst.md, разбор 20-21.07.2026)


def mark_stale_running(**context):
    """Гасит зависшие 'running'-записи sync_log перед новым прогоном региона."""
    from sync_agromap import get_db_conn

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE sync_log
                SET status = 'failed',
                    finished_at = now(),
                    error_message = %s
                WHERE source_region = %s
                  AND status = 'running'
                  AND started_at < now() - interval '%s hours'
                """,
                (
                    f"помечено watchdog-ом DAG как зомби (running дольше {STALE_RUNNING_HOURS}ч, "
                    f"процесс, вероятно, был прерван извне)",
                    REGION,
                    STALE_RUNNING_HOURS,
                ),
            )
            marked = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    print(f"[{REGION}] помечено зомби running-записей: {marked}")


def run_sync(**context):
    """Запускает полный синк региона. sys.exit() из sync_region перехватывается
    явно и переводится в AirflowException — чтобы поведение таска не зависело
    от того, как конкретный executor Airflow обрабатывает SystemExit."""
    from sync_agromap import sync_region

    try:
        sync_region(REGION)
    except SystemExit as e:
        raise AirflowException(
            f"sync_region('{REGION}') завершился с sys.exit({e.code}) — "
            f"подробности в логах таска выше и в таблице sync_log"
        )


with DAG(
    "dag_sync_agromap",
    default_args={
        "owner": "airflow",
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
    },
    schedule_interval=None,  # РЕШЕНИЕ: без расписания на старте, см. докстринг файла
    start_date=datetime(2026, 7, 1),
    catchup=False,
    max_active_runs=1,  # запрещает параллельные раны одного DAG — исключает гонку
                        # retry vs ручной триггер, которая реально произошла 21.07.2026
    tags=["agromap", "geo"],
) as dag:

    t0 = PythonOperator(
        task_id="mark_stale_running",
        python_callable=mark_stale_running,
    )

    t1 = PythonOperator(
        task_id="sync_sko",
        python_callable=run_sync,
        execution_timeout=timedelta(hours=1),
    )

    t0 >> t1
