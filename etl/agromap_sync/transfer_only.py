#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
transfer_only.py — довершает перенос staging -> land_polygons для региона,
у которого staging УЖЕ полностью выгружен (пагинация завершилась успешно),
но сам перенос не случился (например, оборвался на soft-delete).

НЕ качает ничего у источника — только работает с уже имеющимся staging.

Запуск (внутри aldispetro-airflow-worker-1):
    python3 /opt/airflow/etl/agromap_sync/transfer_only.py --region sko
"""

import argparse
import sys
from datetime import datetime, timezone

# Переиспользуем всё уже написанное и проверенное в sync_agromap.py
sys.path.insert(0, "/opt/airflow/etl/agromap_sync")
from sync_agromap import (
    get_db_conn,
    transfer_region_to_production,
    verify_region,
    sync_log_start,
    sync_log_finish,
)


def main():
    parser = argparse.ArgumentParser(description="Довершить перенос staging -> land_polygons без повторного скачивания")
    parser.add_argument("--region", required=True)
    args = parser.parse_args()
    region = args.region

    conn = get_db_conn()

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM land_polygons_staging WHERE source_region = %s", (region,))
        staging_count = cur.fetchone()[0]

    if staging_count == 0:
        print(f"FATAL: land_polygons_staging пуст для региона '{region}' — нечего переносить. "
              f"Нужен полный прогон sync_agromap.py, а не transfer_only.py.", file=sys.stderr)
        sys.exit(1)

    print(f"=== Перенос региона '{region}': {staging_count} объектов в staging, начинаю перенос ===")

    log_id = sync_log_start(conn, region)

    try:
        transferred, retired = transfer_region_to_production(conn, region)
        print(f"[{region}] перенос в land_polygons: upsert={transferred}, retired(is_active=false)={retired}")
        sync_log_finish(conn, log_id, "success", staging_count, error_message=None)
        print(f"=== Перенос региона '{region}' завершён успешно ===")
    except Exception as e:
        conn.rollback()
        sync_log_finish(conn, log_id, "failed", 0, error_message=str(e))
        print(f"=== Перенос региона '{region}' ПРОВАЛЕН: {e} ===", file=sys.stderr)
        conn.close()
        sys.exit(1)

    conn.close()
    verify_region(region)


if __name__ == "__main__":
    main()
