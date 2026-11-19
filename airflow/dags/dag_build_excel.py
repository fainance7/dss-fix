import os
import json, sys
import pandas as pd
from sqlalchemy import create_engine
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator

DB_CONN      = os.environ.get("REPORTS_DB_URL", "postgresql+psycopg2://aldis:CHANGE_ME@postgres/reports_db")
ARTIFACT_DIR = "/opt/airflow/artifacts"

sys.path.insert(0, "/opt/airflow/etl")

def transform_weather():
    engine = create_engine(DB_CONN)
    try:
        raw = pd.read_sql("SELECT * FROM raw_weather", engine)
    except Exception as e:
        print(f"Таблица raw_weather недоступна: {e}")
        return

    from weather_calculator import calculate_all
    rows = []
    for _, rec in raw.iterrows():
        data = json.loads(rec["raw_json"])
        rows.extend(calculate_all(data, int(rec["kato_code"]), int(rec["year"])))

    if rows:
        df = pd.DataFrame(rows)
        # ---- 4 показателя "Доля метеостанций района с ..." (эталон DSS) ----
        # Считаются ДО усреднения, пока значения ещё по каждой станции.
        # доля = станций района ниже порога / всего станций района * 100.
        # Интерполяция (geopandas) НЕ нужна: DSS требует долю СТАНЦИЙ, не площади.
        # ГТК и K берём из ФАКТА Казгидромета (station_climate_fact), не из Open-Meteo:
        # реанализ расходился с наблюдениями у порога 0,80 и переворачивал доли.
        # Из Open-Meteo остаются Σt / вегетация / дата перехода (их в файле нет).
        df = df[df["indicator_name"] != "Гидротермический коэффициент (ГТК)"]
        try:
            _fact = pd.read_sql("SELECT * FROM station_climate_fact", engine)
        except Exception as _e:
            _fact = pd.DataFrame()
            print(f"[warn] station_climate_fact недоступна: {_e}")
        if not _fact.empty:
            _u = {"Гидротермический коэффициент (ГТК)": "безразм.",
                  "Коэффициент увлажнения (K)": "безразм."}
            _fr = []
            # 1) значения по районам = среднее по станциям района
            _g = _fact.groupby(["indicator_name", "kato_code", "year"], as_index=False)["value"].mean()
            for _, r in _g.iterrows():
                _fr.append({"indicator_name": r["indicator_name"], "direction_code": "climate",
                            "unit_name": _u[r["indicator_name"]], "kato_code": int(r["kato_code"]),
                            "period_type": "year", "period_start": f"{int(r['year'])}-01-01",
                            "period_end": f"{int(r['year'])}-12-31",
                            "value": round(float(r["value"]), 2), "source_file": "kazhydromet_fact"})
            # 2) город Петропавловск (591000000) = станция Петропавловск
            _p = _fact[_fact["station"] == "Петропавловск"]
            for _, r in _p.iterrows():
                _fr.append({"indicator_name": r["indicator_name"], "direction_code": "climate",
                            "unit_name": _u[r["indicator_name"]], "kato_code": 591000000,
                            "period_type": "year", "period_start": f"{int(r['year'])}-01-01",
                            "period_end": f"{int(r['year'])}-12-31",
                            "value": round(float(r["value"]), 2), "source_file": "kazhydromet_fact"})
            # 3) доли метеостанций района (ГТК<0,80 и K<0,80) — по факту
            _names = {"Гидротермический коэффициент (ГТК)": "Доля метеостанций района с ГТК<0,80",
                      "Коэффициент увлажнения (K)": "Доля метеостанций района с K<0,80 (риск дефицита влаги)"}
            for _ind, _out in _names.items():
                _sub = _fact[_fact["indicator_name"] == _ind]
                for (_k, _y), _grp in _sub.groupby(["kato_code", "year"]):
                    _share = (_grp["value"] < 0.80).sum() / len(_grp) * 100
                    _fr.append({"indicator_name": _out, "direction_code": "climate",
                                "unit_name": "%", "kato_code": int(_k), "period_type": "year",
                                "period_start": f"{int(_y)}-01-01", "period_end": f"{int(_y)}-12-31",
                                "value": round(float(_share), 1), "source_file": "kazhydromet_fact"})
            df = pd.concat([df, pd.DataFrame(_fr)], ignore_index=True)
            print(f"climate: из факта Казгидромета добавлено {len(_fr)} строк")

        _share_defs = [
            ("Сумма активных температур >10°C (Σt)", 2300.0,
             "Доля метеостанций района с суммой активных температур <2300 °C·сут"),
            ("Продолжительность вегетационного периода", 140.0,
             "Доля метеостанций района с продолжительностью вегетационного периода <140 сут."),
        ]
        _share_rows = []
        _st = df.copy()
        _st["_v"] = pd.to_numeric(_st["value"], errors="coerce")
        for _src_name, _thr, _out_name in _share_defs:
            _sub = _st[(_st["indicator_name"] == _src_name) & _st["_v"].notna()]
            if _sub.empty:
                continue
            _g = _sub.groupby(["kato_code", "period_start", "period_end"])
            for (_kato, _ps, _pe), _grp in _g:
                _total = len(_grp)
                _below = int((_grp["_v"] < _thr).sum())
                _share_rows.append({
                    "indicator_name": _out_name,
                    "direction_code": "climate",
                    "unit_name":      "%",
                    "kato_code":      _kato,
                    "period_type":    "year",
                    "period_start":   _ps,
                    "period_end":     _pe,
                    "value":          round(_below / _total * 100, 1),
                    "source_file":    "open_meteo",
                })
        if _share_rows:
            df = pd.concat([df, pd.DataFrame(_share_rows)], ignore_index=True)
            print(f"climate: добавлено долей метеостанций -> {len(_share_rows)} строк")

        # У 5 районов СКО по 2 метеостанции (Жамбылский, М.Жумабаева, Тайыншинский,
        # Г.Мусрепова + Кызылжарский). Значение района = СРЕДНЕЕ по его станциям,
        # иначе при дедубликации терялись бы данные второй станции.
        # Дата перехода через 10°C — не число, усредняется отдельно (берём минимум =
        # самый ранний переход по району).
        _num = df[df["value"].apply(lambda v: isinstance(v, (int, float)))]
        _txt = df[~df["value"].apply(lambda v: isinstance(v, (int, float)))]
        if not _num.empty:
            _num = (_num.groupby(
                        ["indicator_name", "direction_code", "unit_name",
                         "kato_code", "period_type", "period_start", "period_end"],
                        as_index=False)["value"].mean())
            _num["value"] = pd.to_numeric(_num["value"], errors="coerce").round(2)
        if not _txt.empty:
            _txt = _txt.drop_duplicates(
                subset=["indicator_name", "kato_code", "period_start"])
        df = pd.concat([_num, _txt], ignore_index=True)
        print(f"climate: усреднено по станциям района -> {len(df)} строк, "
              f"{df['kato_code'].nunique()} районов")
        df.to_sql("transformed_weather", engine, if_exists="replace", index=False)
        print(f"Трансформировано {len(df)} строк метео")

def build_excel(**context):
    conf = (context.get("dag_run").conf or {}) if context.get("dag_run") else {}
    taldau_only = bool(conf.get("taldau_only", False))
    indicator_names = conf.get("indicator_names") or []

    engine = create_engine(DB_CONN)
    dfs = []

    if taldau_only and indicator_names:
        placeholders = ", ".join(["%s"] * len(indicator_names))
        try:
            df_t = pd.read_sql(
                f"SELECT * FROM transformed_taldau WHERE indicator_name IN ({placeholders})",
                engine, params=tuple(indicator_names),
            )
            dfs.append(df_t)
            print(f"Режим taldau_only: {len(df_t)} строк по {len(indicator_names)} показателям")
        except Exception as e:
            print(f"Таблица transformed_taldau недоступна: {e}")
    else:
        for table in ["transformed_weather", "transformed_historical", "transformed_taldau"]:
            try:
                dfs.append(pd.read_sql(f"SELECT * FROM {table}", engine))
            except Exception as e:
                print(f"Таблица {table} недоступна: {e}")

    if not dfs:
        raise ValueError("Нет данных для сборки Excel")

    df = pd.concat(dfs, ignore_index=True)

    # Принудительно конвертируем value в число
    df["value"] = pd.to_numeric(df["value"], errors="coerce")

    # Убираем строки где value не число (даты перехода температуры)
    df = df[df["value"].notna()].copy()
    # comment — служебная колонка, требуется форматом DSS (Values, 9 колонок)
    if "comment" not in df.columns:
        df["comment"] = ""


    print(f"Строк после фильтрации: {len(df)}")

    from excel_builder import build_dss_excel

    ts       = datetime.now().strftime("%Y%m%d_%H%M")
    prefix   = "dss_taldau" if taldau_only else "dss_upload"
    filename = f"{ARTIFACT_DIR}/{prefix}_{ts}.xlsx"
    build_dss_excel(df, filename)
    print(f"Файл создан: {filename}")

with DAG("dag_build_excel",
         default_args={"owner": "airflow", "retries": 1,
                       "retry_delay": timedelta(minutes=5)},
         schedule_interval=None,
         start_date=datetime(2024, 1, 1),
         catchup=False,
         tags=["load"]) as dag:

    t1 = PythonOperator(task_id="transform_weather", python_callable=transform_weather)
    t2 = PythonOperator(task_id="build_excel",       python_callable=build_excel)
    t1 >> t2
