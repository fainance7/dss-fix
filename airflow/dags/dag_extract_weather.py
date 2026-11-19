import os
import json, requests, time
from datetime import datetime, timedelta
import pandas as pd
from sqlalchemy import create_engine
from airflow import DAG
from airflow.operators.python import PythonOperator

STATIONS = [
    # 19 реальных метеостанций СКО с ДОСТОВЕРНЫМИ координатами
    # (источник: Клим_данные_по_метеостанциям_СКО_T_R_decade.xlsx, колонки xdd/ydd).
    # Прежние 11 координат были приблизительными — часть смещена на 100-160 км.
    # У 5 районов по 2 станции — значения по району усредняются в transform.
    {"name": "Благовещенка",  "kato": 594600000, "lat": 54.4, "lon": 67.0},   # Жамбылский
    {"name": "Пресновка",     "kato": 594600000, "lat": 54.7, "lon": 67.1},   # Жамбылский
    {"name": "Булаево",       "kato": 593600000, "lat": 54.9, "lon": 70.5},   # М.Жумабаева
    {"name": "Возвышенка",    "kato": 593600000, "lat": 54.4, "lon": 71.0},   # М.Жумабаева
    {"name": "Чкалово",       "kato": 596000000, "lat": 53.6, "lon": 70.5},   # Тайыншинский
    {"name": "Тайынша",       "kato": 596000000, "lat": 53.9, "lon": 69.8},   # Тайыншинский
    {"name": "Налобино",      "kato": 595000000, "lat": 55.2, "lon": 68.8},   # Кызылжарский
    {"name": "Петропавловск", "kato": 591000000, "lat": 54.8, "lon": 69.1},   # г.Петропавловск
    {"name": "Новоишинский",  "kato": 596600000, "lat": 53.2, "lon": 66.8},   # Г.Мусрепова
    {"name": "Рузаевка",      "kato": 596600000, "lat": 52.8, "lon": 67.0},   # Г.Мусрепова
    {"name": "Корнеевка",     "kato": 594200000, "lat": 54.0, "lon": 68.4},   # Есильский
    {"name": "Явленка",       "kato": 594200000, "lat": 54.3, "lon": 68.3},   # Есильский
    {"name": "Кишкенеколь",   "kato": 596400000, "lat": 53.6, "lon": 72.3},   # Уалихановский
    {"name": "Мамлютка",      "kato": 595200000, "lat": 54.9, "lon": 68.5},   # Мамлютский
    {"name": "Саумалколь",    "kato": 593200000, "lat": 53.3, "lon": 68.1},   # Айыртауский
    {"name": "Сергеевка",     "kato": 595600000, "lat": 53.9, "lon": 67.4},   # Шал акына
    {"name": "Смирново",      "kato": 595800000, "lat": 54.5, "lon": 69.4},   # Аккайынский
    {"name": "Талшык",        "kato": 593400000, "lat": 53.6, "lon": 71.9},   # Акжарский
    {"name": "Тимирязево",    "kato": 596200000, "lat": 53.8, "lon": 66.5},   # Тимирязевский
]

OPENMETEO_URL = "https://archive-api.open-meteo.com/v1/archive"
DB_CONN = os.environ["REPORTS_DB_URL"]  # M-3: required, no insecure default

# Open-Meteo рубит запросы 429-кой почти сразу без паузы между вызовами —
# добавлена базовая задержка + повтор с нарастающей паузой именно для 429.
RETRY_ON_429 = 3
BACKOFF_SECONDS = 5
REQUEST_DELAY = 0.5

def fetch_weather(**context):
    # Диапазон лет из conf (по умолчанию 2003-2024 — как в климат-файле
    # по метеостанциям). Раньше год был захардкожен (2024) — собирался 1 год.
    conf = (context.get("dag_run").conf or {}) if context.get("dag_run") else {}
    year_from = int(conf.get("year_from", 2003))
    year_to   = int(conf.get("year_to", 2024))
    years = list(range(year_from, year_to + 1))
    engine = create_engine(DB_CONN)
    rows   = []

    for year in years:
        start = f"{year}-01-01"
        end   = f"{year}-12-31"
        for station in STATIONS:
            for attempt in range(RETRY_ON_429 + 1):
                try:
                    resp = requests.get(OPENMETEO_URL, params={
                        "latitude":   station["lat"],
                        "longitude":  station["lon"],
                        "start_date": start,
                        "end_date":   end,
                        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                        "timezone": "Asia/Almaty"
                    }, timeout=60)
                    if resp.status_code == 429 and attempt < RETRY_ON_429:
                        wait = BACKOFF_SECONDS * (attempt + 1)
                        print(f"[429] {station['name']} {year} — повтор через {wait}с "
                              f"(попытка {attempt + 1}/{RETRY_ON_429})")
                        time.sleep(wait)
                        continue
                    resp.raise_for_status()
                    rows.append({
                        "station_name": station["name"],
                        "kato_code":    station["kato"],
                        "year":         year,
                        "raw_json":     json.dumps(resp.json(), ensure_ascii=False),
                        "fetched_at":   datetime.now().isoformat(),
                        "source_file":  "open_meteo"
                    })
                    break
                except Exception as e:
                    print(f"ОШИБКА {station['name']} {year}: {e}")
                    break
            time.sleep(REQUEST_DELAY)
        print(f"Год {year}: собрано станций {len([r for r in rows if r['year']==year])}")

    if rows:
        pd.DataFrame(rows).to_sql("raw_weather", engine,
                                   if_exists="append", index=False)
        print(f"Сохранено {len(rows)} станций")

with DAG("dag_extract_weather",
         default_args={"owner": "airflow", "retries": 2,
                       "retry_delay": timedelta(minutes=10)},
         schedule_interval=None,
         start_date=datetime(2024, 1, 1),
         catchup=False,
         tags=["extract"]) as dag:

    PythonOperator(task_id="fetch_weather", python_callable=fetch_weather)
