import os
import json
import sys
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

DB_CONN = os.environ["REPORTS_DB_URL"]  # M-3: required, no insecure default

sys.path.insert(0, "/opt/airflow/etl")


def check_raw_historical(**context):
    """
    Проверяет наличие данных в raw_historical.
    Если таблица пуста — логирует предупреждение но не падает,
    чтобы не блокировать остальные DAG-и.
    """
    engine = create_engine(DB_CONN)
    count = pd.read_sql(
        "SELECT COUNT(*) as cnt FROM raw_historical", engine
    ).iloc[0]["cnt"]

    print(f"raw_historical содержит {count} записей")

    if count == 0:
        print(
            "ВНИМАНИЕ: raw_historical пуст.\n"
            "Данные должны быть загружены вручную или через отдельный скрипт "
            "из reports_db (PDF/DOCX/XLSX источники).\n"
            "Трансформация будет пропущена."
        )
    # Передаём count следующей задаче через XCom
    return int(count)


def transform_historical(**context):
    """
    Трансформирует записи из raw_historical → transformed_historical
    и добавляет их в transformed_weather (общий поток для build_excel).

    raw_historical.data — JSONB со структурой:
    {
      "indicator_name": "...",
      "direction_code": "...",
      "unit_name": "...",
      "kato_code": 590000000,
      "period_type": "year",
      "period_start": "2020-01-01",
      "period_end": "2020-12-31",
      "value": 12345.6
    }
    Либо список таких объектов.
    """
    # Получаем count из предыдущей задачи
    ti = context["ti"]
    count = ti.xcom_pull(task_ids="check_raw_historical")

    if not count or count == 0:
        print("raw_historical пуст — трансформация пропускается")
        return

    engine = create_engine(DB_CONN)

    # Загружаем справочники
    try:
        ind_cache = pd.read_sql("SELECT * FROM indicator_cache", engine)
        ind_map = dict(zip(
            ind_cache["indicator_name"],
            zip(ind_cache["direction_code"], ind_cache["unit_name"])
        ))
    except Exception:
        ind_map = {}
        print("indicator_cache недоступен")

    raw = pd.read_sql(
        "SELECT id, source_file, data FROM raw_historical ORDER BY id",
        engine
    )

    rows = []
    skipped = 0

    for _, rec in raw.iterrows():
        source_file = rec["source_file"] or "historical"

        try:
            # data может быть строкой или уже dict (JSONB)
            if isinstance(rec["data"], str):
                payload = json.loads(rec["data"])
            else:
                payload = rec["data"]

            # Нормализуем: может быть один объект или список
            if isinstance(payload, dict):
                items = [payload]
            elif isinstance(payload, list):
                items = payload
            else:
                print(f"  [ПРОПУСК id={rec['id']}] Неожиданный тип data: {type(payload)}")
                skipped += 1
                continue

        except Exception as e:
            print(f"  [ПРОПУСК id={rec['id']}] Ошибка парсинга JSON: {e}")
            skipped += 1
            continue

        for item in items:
            try:
                # Базовые поля
                ind_name = str(item.get("indicator_name", "")).strip()
                kato_raw = item.get("kato_code")
                value_raw = item.get("value")

                if not ind_name or kato_raw is None or value_raw is None:
                    skipped += 1
                    continue

                # Конвертация value
                value = float(
                    str(value_raw).replace(" ", "").replace(",", ".")
                )
                if pd.isna(value):
                    skipped += 1
                    continue

                kato_code = int(kato_raw)

                # direction_code и unit_name: берём из item или из кэша
                direction_code = (
                    item.get("direction_code")
                    or ind_map.get(ind_name, ("other", "единиц"))[0]
                )
                unit_name = (
                    item.get("unit_name")
                    or ind_map.get(ind_name, ("other", "единиц"))[1]
                )

                period_type  = item.get("period_type", "year")
                period_start = str(item.get("period_start", ""))
                period_end   = str(item.get("period_end", ""))

                # Валидация дат
                if not period_start or not period_end:
                    skipped += 1
                    continue

                pd.to_datetime(period_start)  # бросит исключение если некорректна
                pd.to_datetime(period_end)

                rows.append({
                    "indicator_name": ind_name,
                    "direction_code": direction_code,
                    "unit_name":      unit_name,
                    "kato_code":      kato_code,
                    "period_type":    period_type,
                    "period_start":   period_start[:10],
                    "period_end":     period_end[:10],
                    "value":          value,
                    "source_file":    source_file,
                })

            except Exception as e:
                print(f"  [ПРОПУСК] item={item}: {e}")
                skipped += 1
                continue

    print(f"Обработано: {len(rows)} строк, пропущено: {skipped}")

    if not rows:
        print("Нет валидных данных после трансформации")
        return

    df_out = pd.DataFrame(rows)

    # Убираем дубли
    # direction_code ОБЯЗАТЕЛЕН в ключе: каталог DSS содержит одноимённые
    # показатели в разных направлениях с разными единицами (напр. "Валовая
    # продукция сельского хозяйства" — agriculture/млн.тенге и
    # economy/млрд.тенге). Без direction_code одна версия молча затирала
    # другую — замер показал 294 таких пары, и ВСЕ 294 расходились именно
    # по направлению, а не по значению внутри одного направления.
    #
    # keep="last": строки идут ORDER BY id, то есть последняя загрузка
    # побеждает. Раньше выбор зависел от порядка и был недетерминированным.
    _before = len(df_out)
    df_out = df_out.drop_duplicates(
        subset=["kato_code", "indicator_name", "direction_code", "period_start"],
        keep="last",
    ).reset_index(drop=True)
    print(f"Дедупликация: {_before} -> {len(df_out)} строк")

    # Нормализация единиц
    # "ед." НЕ переписывается в "единиц": в справочнике DSS это ДВЕ РАЗНЫЕ
    # единицы с разными id, и показатели туризма ("Количество средств
    # размещения", "Количество номеров", "Предоставлено койко-суток")
    # объявлены именно через "ед.". Замена ломала соответствие единицы
    # тому, что ждёт DSS у этого показателя.
    unit_norm = {"чел.": "человек"}
    df_out["unit_name"] = df_out["unit_name"].replace(unit_norm)

    # Сохраняем в отдельную таблицу transformed_historical
    df_out.to_sql(
        "transformed_historical", engine,
        if_exists="replace", index=False
    )
    print(f"transformed_historical: {len(df_out)} строк сохранено")

    # Также добавляем в transformed_weather чтобы build_excel подхватил
    # (build_excel читает из transformed_weather — общий поток данных)
    existing_cols = [
        "indicator_name", "direction_code", "unit_name",
        "kato_code", "period_type", "period_start", "period_end", "value"
    ]
    df_for_excel = df_out[existing_cols].copy()

    try:
        existing = pd.read_sql("SELECT * FROM transformed_weather", engine)
        combined = pd.concat([existing, df_for_excel], ignore_index=True)
        combined = combined.drop_duplicates(
            subset=["kato_code", "indicator_name", "direction_code", "period_start"],
            keep="last",
        ).reset_index(drop=True)
        combined.to_sql(
            "transformed_weather", engine,
            if_exists="replace", index=False
        )
        print(f"transformed_weather обновлён: {len(combined)} строк всего")
    except Exception as e:
        print(f"Не удалось обновить transformed_weather: {e}")


with DAG(
    "dag_extract_historical",
    default_args={
        "owner":       "airflow",
        "retries":     1,
        "retry_delay": timedelta(minutes=5),
    },
    schedule_interval=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["extract"],
) as dag:

    t1 = PythonOperator(
        task_id="check_raw_historical",
        python_callable=check_raw_historical,
    )

    t2 = PythonOperator(
        task_id="transform_historical",
        python_callable=transform_historical,
    )

    t3 = TriggerDagRunOperator(
        task_id="trigger_build_excel",
        trigger_dag_id="dag_build_excel",
    )

    t1 >> t2 >> t3
