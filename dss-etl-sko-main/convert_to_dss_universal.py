import os
"""
Универсальная конвертация Excel файлов → raw_historical для последующего ETL.

ВАЖНО: эта версия заменяет старые захардкоженные блоки (Файл 1, Файл 2, ...)
на автоматическое распознавание структуры через etl/universal_detector.py.
Больше не нужно дописывать конфиг для каждого нового файла — достаточно
положить .xlsx в INPUT_DIR.

Запускать внутри контейнера airflow-worker (там есть pandas, rapidfuzz):

    docker cp /opt/aldispetro/convert_to_dss.py aldispetro-airflow-worker-1:/tmp/
    docker cp /opt/aldispetro/indicator_catalog.csv aldispetro-airflow-worker-1:/tmp/
    docker cp /opt/aldispetro/indicator_aliases.csv aldispetro-airflow-worker-1:/tmp/
    docker exec -w /tmp aldispetro-airflow-worker-1 bash -c \
      "sed 's/host=localhost/host=postgres/g' convert_to_dss.py > conv_fixed.py && python3 conv_fixed.py"

Что нужно класть в /tmp вместе со скриптом:
  - все .xlsx файлы, которые нужно сконвертировать (через docker cp)
  - indicator_catalog.csv  — канонические показатели DSS
  - indicator_aliases.csv  — альтернативные названия → канонический показатель

Если показатель не находится ни в каталоге, ни в алиасах — он всё равно
сохраняется (чтобы не терять данные молча), но с direction_code='unmapped'
и пометкой в comment. После прогона смотри секцию "ТРЕБУЕТ ВНИМАНИЯ" в выводе
и при необходимости дополни indicator_aliases.csv новой строкой.

Если регион (район/город) не резолвится в KATO ни точно, ни нечётко —
строка пропускается с явным [ПРОПУСК] в логе (так было и в старой версии);
данные не подменяются произвольным кодом.
"""
import sys
import json
from pathlib import Path

import pandas as pd
import psycopg2

sys.path.insert(0, "/opt/aldispetro/etl")
sys.path.insert(0, "/opt/airflow/etl")
sys.path.insert(0, str(Path(__file__).parent / "etl"))

from universal_detector import IndicatorCatalog, convert_file  # noqa: E402

DB_CONN = os.environ["REPORTS_DB_DSN"]  # M-3: required, no insecure default

# Папка с исходными xlsx-файлами для конвертации.
# По умолчанию — рядом со скриптом (т.е. /tmp при запуске по инструкции выше).
INPUT_DIR = Path(__file__).parent

# Где искать каталог показателей и алиасы (тоже рядом со скриптом)
CATALOG_PATH = INPUT_DIR / "indicator_catalog.csv"
ALIASES_PATH = INPUT_DIR / "indicator_aliases.csv"


def insert_rows(cur, conn, rows, source_file):
    for rec in rows:
        cur.execute(
            "INSERT INTO raw_historical (source_file, data) VALUES (%s, %s)",
            (source_file, json.dumps(rec, ensure_ascii=False)),
        )
    conn.commit()


def main():
    if not CATALOG_PATH.exists():
        print(f"[FATAL] Не найден каталог показателей: {CATALOG_PATH}")
        print("        Скопируй indicator_catalog.csv в ту же папку, что и этот скрипт.")
        return

    catalog = IndicatorCatalog.from_csv(
        CATALOG_PATH,
        ALIASES_PATH if ALIASES_PATH.exists() else None,
    )
    print(f"Каталог загружен: {len(catalog.entries)} показателей, "
          f"{len(catalog.alias_map)} алиасов")

    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()

    xlsx_files = sorted(INPUT_DIR.glob("*.xlsx")) + sorted(INPUT_DIR.glob("*.xls"))
    if not xlsx_files:
        print(f"[WARN] Нет .xlsx/.xls файлов в {INPUT_DIR}")
        return

    total = 0
    unmapped_summary = {}  # indicator_title -> count, для финальной сводки

    for path in xlsx_files:
        print(f"\n=== Обрабатываю: {path.name} ===")
        rows, stats = convert_file(path, catalog)

        if not rows:
            print("  [WARN] Нет данных для загрузки из этого файла")
            continue

        # собираем сводку по unmapped для финального отчёта
        for r in rows:
            if r["direction_code"] == "unmapped":
                unmapped_summary[r["indicator_name"]] = (
                    unmapped_summary.get(r["indicator_name"], 0) + 1
                )

        insert_rows(cur, conn, rows, path.name)
        total += len(rows)
        print(f"  Загружено: {len(rows)} записей")

    cur.close()
    conn.close()

    print(f"\n✅ Итого загружено в raw_historical: {total} записей")

    if unmapped_summary:
        print("\n⚠️  ТРЕБУЕТ ВНИМАНИЯ — показатели без канонического соответствия "
              "(сохранены с direction_code='unmapped'):")
        for name, cnt in sorted(unmapped_summary.items(), key=lambda x: -x[1]):
            print(f"    {cnt:>5} строк — {name}")
        print("\n   Добавь нужные строки в indicator_aliases.csv "
              "(alias_name,canonical_name) и перезапусти конвертацию,")
        print("   либо вручную поправь direction_code/unit_name прямо в DSS-каталоге, "
              "если это новый показатель.")

    print("\nТеперь запусти dag_extract_historical в Airflow")


if __name__ == "__main__":
    main()
