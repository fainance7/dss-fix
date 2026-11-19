"""
dag_monthly_update.py — ежемесячное обновление данных ETL -> DSS.

Запуск: 1-го числа каждого месяца в 03:00 (Алматы). Можно запустить и вручную
(Trigger DAG); для проверки без отправки в DSS — conf {"send": false}.

Что делает (по шагам):
  1. Талдау — запускает dag_extract_taldau по всем активным показателям
     каталога taldau_indicators_catalog за все годы с 2000 (Талдау отдаёт их
     одним ответом): сначала по районам (split_by_kato=true), затем по области.
     Задача Талдау при каждом запуске стирает предыдущий результат по тем же
     показателям, поэтому результат каждого прогона сохраняется здесь и потом
     обе части (районы + область) записываются обратно в raw_taldau и
     transformed_taldau. Перед запуском сохраняется копия строк Талдау: если
     какой-то прогон не завершился, таблицы возвращаются к состоянию до запуска.
  2. Климат — запускает dag_extract_weather за ПРОШЕДШИЙ полный год, если его
     ещё нет в raw_weather (не раньше февраля: архив Open-Meteo отстаёт на
     несколько дней). Текущий год не берётся: годовые показатели (Σt, ГТК,
     вегетация) по неполному году были бы неверными. Затем dag_build_excel
     пересчитывает transformed_weather.
  3. Agromap — запускает dag_sync_agromap (геослои; в DSS-значения не идут),
     если он не на паузе. Пока он на паузе (запуск только вручную, см. его
     файл), шаг пропускается без ошибки. Ошибка agromap не останавливает
     отбор данных для DSS.
  4. Отбор новых значений для DSS — в партию попадают ТОЛЬКО НОВЫЕ значения:
       - показатель есть в справочнике DSS (название + направление + единица);
         если в ETL он назван иначе или с другой подписью/множителем единицы,
         сопоставление берётся из etl/dss_monthly_map.csv (туда вносятся только
         пары, у которых значения ETL и DSS на общих годах совпали);
       - ряд (показатель + территория + тип периода) уже есть в DSS — то есть
         ряд только продлевается новыми годами; новые ряды не отправляются;
       - период идёт ПОСЛЕ последнего периода ряда в DSS (пропуски внутри ряда
         и годы до его начала не дописываются: их могли убрать намеренно);
       - значение не отличается от последнего значения ряда в DSS более
         чем в 2,5 раза (как порог «обрыв ряда» контроля качества DSS);
       - в последних 5 общих с DSS годах значения ETL совпадают с DSS не меньше
         чем в 80 % случаев (защита от другой единицы или сдвига года в ряду);
       - значение не было раньше в партии, которую DSS отклонил или удалил.
     Изменившиеся старые значения, новые ряды, пропуски, скачки и показатели
     вне справочника DSS не отправляются, а пишутся (за текущий год и 3
     предыдущих) в отчёт review_ГГГГ-ММ.csv для проверки человеком.
     Партия сохраняется файлом в формате загрузки DSS и копируется в общий
     список файлов сайта ETL (artifacts/*.xlsx) — её можно скачать и загрузить
     в DSS вручную.
  5. Отправка в DSS (по умолчанию ВЫКЛЮЧЕНА) — партия уходит в портал приёма
     данных как ЧЕРНОВИК (как кнопка «Проверить в DSS»; в базу DSS данные
     попадают только после подтверждения в портале), если одновременно:
       - включена Airflow Variable DSS_MONTHLY_SEND=true (отдельно от
         DSS_DRY_RUN кнопки сайта: кнопка и эта задача включаются независимо);
       - заданы DSS_INTAKE_URL и DSS_INTAKE_KEY и портал доступен из Airflow;
       - снимок DSS не старше 3 дней;
       - запуск не с conf {"send": false}.

Что уже есть в DSS, задача узнаёт из снимка значений DSS
artifacts/dss_snapshot/dss_values.csv.gz (выгрузка из базы DSS, колонки dir, name,
unit, is_active, kato, period_type, period_start, value, ...; снимок каждую ночь
обновляет scripts/dss_snapshot.sh по cron на сервере — только чтение базы DSS)
и из собственных прошлых отправок (artifacts/monthly/sent_batches.json, статус
каждой партии перепроверяется в DSS: imported — строки уже в DSS; pending/
validated — ждут подтверждения; failed/cancelled/удалена — строки больше не
предлагаются автоматически, а уходят в отчёт на проверку).
Без снимка задача ничего не готовит и не отправляет.

Файлы результата (artifacts/monthly): dss_monthly_ГГГГ-ММ.xlsx (партия новых
значений, копия — artifacts/dss_monthly_ГГГГ-ММ.xlsx), report_ГГГГ-ММ.json
(сводка), indicators_ГГГГ-ММ.csv (по каждому показателю: обновляется ли он в DSS
автоматически, а если нет — почему), review_ГГГГ-ММ.csv (значения на проверку),
classified_ГГГГ-ММ.csv.gz (все значения ETL с итогом сверки).

Подключения: база reports_db — REPORTS_DB_URL из окружения, иначе строка
DB_CONN из dag_extract_taldau.py / dag_build_excel.py (та же база, куда пишут
эти задачи), иначе Airflow Variable REPORTS_DB_CONN_SQLALCHEMY; портал DSS —
DSS_INTAKE_URL и DSS_INTAKE_KEY из .env (как у кнопки «Проверить в DSS»).
"""
import gzip
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import pendulum
import requests
from sqlalchemy import create_engine, text

from airflow import DAG
from airflow.api.common.trigger_dag import trigger_dag
from airflow.models import DagModel, DagRun, Variable
from airflow.operators.python import PythonOperator
from airflow.utils.trigger_rule import TriggerRule

TZ = pendulum.timezone("Asia/Almaty")
ARTIFACTS = Path(os.environ.get("ARTIFACTS_DIR_AIRFLOW", "/opt/airflow/artifacts"))
WORK = ARTIFACTS / "monthly"
SNAPSHOT = ARTIFACTS / "dss_snapshot" / "dss_values.csv.gz"  # пишет scripts/dss_snapshot.sh (cron)
BATCHES = WORK / "sent_batches.json"
DAGS_DIR = Path(__file__).resolve().parent
ETL_DIR = "/opt/airflow/etl"

SEND_VAR = "DSS_MONTHLY_SEND"  # Airflow Variable: "true" — отправлять партию в DSS черновиком

# Талдау отдаёт все годы показателя одним ответом, а задача Талдау при каждом запуске
# стирает прежние строки своих показателей — поэтому запрашиваем всю историю, чтобы
# ETL её не потерял. В DSS при этом уходят только новые годы в конце ряда.
TALDAU_YEAR_FROM = 2000
REVIEW_YEARS_BACK = 3          # в отчёт на проверку — текущий год и 3 предыдущих
JUMP_RATIO = 2.5               # как «обрыв ряда» в контроле качества DSS
MATCH_SHARE = 0.8              # доля совпадений ETL и DSS на общих годах, чтобы доверять ряду
AGREE_LAST_N = 5               # совпадение проверяем по последним 5 общим годам ряда
SNAPSHOT_MAX_AGE_DAYS = 3      # со снимком DSS старше этого партия не отправляется
WAIT_POLL_SEC = 60
WAIT_TIMEOUT_SEC = 8 * 3600
KEY_COLS = ["direction_code", "indicator_name", "kato_code", "period_type", "period_start"]
COLS = ["indicator_name", "direction_code", "unit_name", "kato_code", "period_type",
        "period_start", "period_end", "value", "source_file"]


# ---------------------------------------------------------------- подключения
def _dsn() -> str:
    v = os.environ.get("REPORTS_DB_URL")
    if v:
        return v
    # та же база, куда пишут Талдау и сборка (на сервере строка задана в самих DAG)
    for name in ("dag_extract_taldau.py", "dag_build_excel.py"):
        try:
            m = re.search(r'^DB_CONN\s*=\s*["\'](postgresql[^"\']+)["\']',
                          (DAGS_DIR / name).read_text(encoding="utf-8"), re.M)
        except OSError:
            m = None
        if m:
            return m.group(1)
    try:
        return Variable.get("REPORTS_DB_CONN_SQLALCHEMY")
    except KeyError:
        pass
    raise RuntimeError("Нет подключения к reports_db: задайте REPORTS_DB_URL в .env "
                       "или Airflow Variable REPORTS_DB_CONN_SQLALCHEMY")


def _engine():
    return create_engine(_dsn())


def _intake():
    url = os.environ.get("DSS_INTAKE_URL", "").rstrip("/")
    key = os.environ.get("DSS_INTAKE_KEY", "")
    return url, key


def _send_switch() -> bool:
    try:
        v = Variable.get(SEND_VAR, default_var="false")
    except Exception:
        v = "false"
    return str(v).strip().lower() in ("1", "true", "yes")


def _snapshot_age_days():
    if not SNAPSHOT.exists():
        return None
    return round((time.time() - SNAPSHOT.stat().st_mtime) / 86400, 1)


def _conf(ctx) -> dict:
    dr = ctx.get("dag_run")
    return (dr.conf or {}) if dr else {}


def _tag(ctx) -> str:
    """ГГГГ-ММ месяца запуска (по Алматы) — по времени старта запуска, одинаково для всех шагов."""
    dr = ctx.get("dag_run")
    start = getattr(dr, "start_date", None) if dr else None
    t = pendulum.instance(start).in_timezone(TZ) if start else pendulum.now(TZ)
    return t.strftime("%Y-%m")


# ---------------------------------------------------------------- запуск других DAG
def _trigger_and_wait(dag_id: str, conf: dict, label: str, ctx) -> str:
    dm = DagModel.get_dagmodel(dag_id)
    if dm is None:
        raise RuntimeError(f"DAG {dag_id} не найден")
    if dm.is_paused:
        raise RuntimeError(f"DAG {dag_id} на паузе — включите его в Airflow, иначе запуск не выполнится")
    run_id = f"monthly__{label}__{ctx['ts_nodash']}__{int(time.time())}"
    trigger_dag(dag_id=dag_id, run_id=run_id, conf=conf)
    print(f"[{label}] запущен {dag_id}, run_id={run_id}")
    waited = 0
    while waited < WAIT_TIMEOUT_SEC:
        time.sleep(WAIT_POLL_SEC)
        waited += WAIT_POLL_SEC
        runs = DagRun.find(dag_id=dag_id, run_id=run_id)
        state = str(runs[0].state).lower() if runs else "none"
        if state.endswith("success"):
            print(f"[{label}] {dag_id}: успешно за {waited // 60} мин")
            return "success"
        if state.endswith("failed"):
            raise RuntimeError(f"[{label}] {dag_id} завершился с ошибкой (run_id={run_id})")
    raise RuntimeError(f"[{label}] {dag_id} не завершился за {WAIT_TIMEOUT_SEC // 3600} ч")


# ---------------------------------------------------------------- шаги
def prepare(**ctx):
    WORK.mkdir(parents=True, exist_ok=True)
    tag = _tag(ctx)
    for p in WORK.glob(f"taldau_part_{tag}_*.csv"):
        p.unlink()
    if _conf(ctx).get("fetch") is not False:
        # копия строк Талдау до запуска: при сбое Талдау таблицы ETL вернутся к ней
        _save_taldau(_engine(), tag, "before")
    age = _snapshot_age_days()
    if age is None:
        print(f"[ВНИМАНИЕ] нет снимка DSS {SNAPSHOT} — данные будут собраны, но партия для DSS не готовится")
    elif age > SNAPSHOT_MAX_AGE_DAYS:
        print(f"[ВНИМАНИЕ] снимку DSS {age} дн. — проверьте cron dss_snapshot.sh; партия не будет отправлена")
    url, key = _intake()
    print(f"месяц {tag}; снимок DSS: {age} дн.; DSS: url={'есть' if url else 'нет'}, "
          f"ключ={'есть' if key else 'нет'}, отправка ({SEND_VAR})={'вкл' if _send_switch() else 'выкл'}")


def _taldau_catalog(engine) -> dict:
    cat = pd.read_sql("SELECT taldau_index_id, name_ru FROM taldau_indicators_catalog WHERE is_active = TRUE", engine)
    return {int(i): str(n) for i, n in zip(cat["taldau_index_id"], cat["name_ru"]) if pd.notna(i)}


TALDAU_TABLES = ("raw_taldau", "transformed_taldau")


def _part_path(tag: str, part: str, table: str) -> Path:
    return WORK / f"taldau_part_{tag}_{part}_{table}.csv"


def _taldau_where(engine, table: str) -> str:
    """Отбор строк Талдау: по source_file, если такая колонка есть в таблице."""
    cols = pd.read_sql(f"SELECT * FROM {table} LIMIT 0", engine).columns
    return " WHERE source_file = 'taldau_api'" if "source_file" in cols else " WHERE TRUE"


def _read_taldau(engine, table: str) -> pd.DataFrame:
    return pd.read_sql(f"SELECT * FROM {table}{_taldau_where(engine, table)}", engine)


def _save_taldau(engine, tag: str, part: str):
    for table in TALDAU_TABLES:
        df = _read_taldau(engine, table)
        if "kato_code" in df:  # без «.0» у кодов, если в колонке есть пустые значения
            df["kato_code"] = pd.to_numeric(df["kato_code"], errors="coerce").round().astype("Int64")
        df.to_csv(_part_path(tag, part, table), index=False)
        print(f"Талдау ({part}): {table}, сохранено {len(df)} строк")


def run_taldau(split: bool, **ctx):
    conf = _conf(ctx)
    if conf.get("fetch") is False:
        print("fetch=false — Талдау не запрашиваем")
        return
    engine = _engine()
    cat = _taldau_catalog(engine)
    if not cat:
        raise RuntimeError("каталог taldau_indicators_catalog пуст")
    part = "districts" if split else "oblast"
    _trigger_and_wait("dag_extract_taldau", {
        "index_ids": sorted(cat),
        "indicator_names": {str(i): n for i, n in cat.items()},
        "split_by_kato": split,
        "year_from": TALDAU_YEAR_FROM,
        "year_to": pendulum.now(TZ).year,
    }, f"taldau_{part}", ctx)
    # результат прогона сохраняем: следующий прогон Талдау сотрёт его в обеих таблицах
    _save_taldau(engine, _tag(ctx), part)


def restore_taldau(**ctx):
    """Приводит raw_taldau и transformed_taldau к полному набору: оба прогона удались —
    районы + область вместе; какой-то прогон упал — состояние до запуска (копия «before»)."""
    tag = _tag(ctx)
    engine = _engine()
    for table in TALDAU_TABLES:
        d, o, b = (_part_path(tag, p, table) for p in ("districts", "oblast", "before"))
        if d.exists() and o.exists():
            df = pd.concat([pd.read_csv(d, dtype=str), pd.read_csv(o, dtype=str)], ignore_index=True)
            # raw: строки других показателей одинаковы в обеих частях (тот же id);
            # transformed: задача Талдау пересоздаёт все строки, поэтому сравниваем по смыслу
            if table == "raw_taldau":
                key = ["id"] if "id" in df else None
            else:
                key = ["indicator_name", "kato_code", "period_start"]
            df = df.drop_duplicates(subset=key, keep="last")
            what = "районы + область"
            if b.exists():
                # показатель/территория, которых Талдау в этот раз не вернул (сбой запроса,
                # пустой ответ), остаются в ETL с прежними данными, а не пропадают
                old = pd.read_csv(b, dtype=str)
                have = set(zip(df["indicator_name"], df["kato_code"]))
                keep = old[[k not in have for k in zip(old["indicator_name"], old["kato_code"])]]
                if len(keep):
                    df = pd.concat([df, keep], ignore_index=True)
                    what += f"; {len(keep)} строк оставлено из прежних данных (Талдау их не вернул)"
        elif b.exists():
            df = pd.read_csv(b, dtype=str)
            what = "прогон Талдау не завершился, возвращено состояние до запуска"
        else:
            print(f"{table}: Талдау не запускался, восстанавливать нечего")
            continue
        with engine.begin() as conn:
            conn.execute(text(f"DELETE FROM {table}{_taldau_where(engine, table)}"))
            df.to_sql(table, conn, if_exists="append", index=False)
        print(f"{table}: записано {len(df)} строк ({what})")


def run_weather(**ctx):
    conf = _conf(ctx)
    if conf.get("fetch") is False:
        print("fetch=false — климат не запрашиваем")
        return
    now = pendulum.now(TZ)
    prev = now.year - 1
    engine = _engine()
    try:
        have = set(pd.read_sql("SELECT DISTINCT year FROM raw_weather", engine)["year"].astype(int))
    except Exception:
        have = set()
    if prev in have:
        print(f"климат за {prev} уже загружен — пропуск")
        return
    if now.month < 2 and not conf.get("weather_force"):
        print(f"климат за {prev}: ждём февраля (архив Open-Meteo за декабрь ещё неполный)")
        return
    _trigger_and_wait("dag_extract_weather", {"year_from": prev, "year_to": prev}, "weather", ctx)
    _trigger_and_wait("dag_build_excel", {}, "weather_transform", ctx)


def run_agromap(**ctx):
    conf = _conf(ctx)
    if conf.get("fetch") is False or conf.get("agromap") is False:
        print("agromap пропущен (conf)")
        return
    dm = DagModel.get_dagmodel("dag_sync_agromap")
    if dm is not None and dm.is_paused:
        print("dag_sync_agromap на паузе (запускается вручную) — пропуск. Чтобы обновлять agromap "
              "ежемесячно: airflow dags unpause dag_sync_agromap")
        return
    _trigger_and_wait("dag_sync_agromap", {}, "agromap", ctx)


# ---------------------------------------------------------------- отбор новых значений
def _apply_map(cand: pd.DataFrame) -> pd.DataFrame:
    """Сопоставление ETL -> DSS из etl/dss_monthly_map.csv: один и тот же показатель в ETL
    (как в Талдау) и в DSS иногда назван по-разному или с другой подписью единицы. В файле
    только пары, у которых значения на общих годах совпали; factor — множитель единицы
    (например 0.001 из человек в тыс. человек). Исходное название остаётся в etl_name."""
    cand = cand.copy()
    cand["etl_name"] = cand["indicator_name"]
    path = Path(ETL_DIR) / "dss_monthly_map.csv"
    if not path.exists():
        return cand
    orig_unit = cand["unit_name"].copy()
    for m in pd.read_csv(path, dtype=str).fillna("").itertuples(index=False):
        sel = cand["etl_name"].eq(m.etl_name)
        if m.etl_unit:
            sel &= orig_unit.eq(m.etl_unit)
        if not sel.any():
            continue
        cand.loc[sel, ["direction_code", "indicator_name", "unit_name"]] = [m.dss_direction, m.dss_name, m.dss_unit]
        if m.factor and float(m.factor) != 1:
            cand.loc[sel, "value"] = (cand.loc[sel, "value"] * float(m.factor)).round(6)
    return cand


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["kato_code"] = df["kato_code"].map(lambda v: str(int(float(v))) if pd.notna(v) else None)
    df["period_start"] = pd.to_datetime(df["period_start"], errors="coerce").dt.strftime("%Y-%m-%d")
    if "period_end" in df:
        df["period_end"] = pd.to_datetime(df["period_end"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df[df["value"].notna() & df["kato_code"].notna() & df["period_start"].notna()]


def load_snapshot(path: Path) -> pd.DataFrame:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        s = pd.read_csv(fh, dtype=str)
    s = s.rename(columns={"dir": "direction_code", "name": "indicator_name", "unit": "unit_name", "kato": "kato_code"})
    s["period_start"] = s["period_start"].str[:10]
    s["value"] = pd.to_numeric(s["value"], errors="coerce")
    return s


def classify(cand: pd.DataFrame, known: pd.DataFrame, catalog: dict) -> pd.DataFrame:
    """cand — значения ETL; known — что уже есть в DSS (снимок + свои отправки);
    catalog — {(direction_code, indicator_name): unit_name} справочника DSS.
    Возвращает cand с колонками status и note."""
    known = known.dropna(subset=["value"])
    kv = {tuple(r): v for r, v in zip(known[KEY_COLS].itertuples(index=False, name=None), known["value"])}
    series = {}
    for (d, n, k, pt, ps), v in kv.items():
        series.setdefault((d, n, k, pt), []).append((ps, v))
    for s in series.values():
        s.sort()
    out = []
    for r in cand.itertuples(index=False):
        key = (r.direction_code, r.indicator_name, r.kato_code, r.period_type, r.period_start)
        ser = series.get(key[:4])
        unit = catalog.get((r.direction_code, r.indicator_name))
        if unit is None:
            st, note = "нет в справочнике DSS", ""
        elif unit != r.unit_name:
            st, note = "единица не как в DSS", f"в DSS: {unit}"
        elif ser is None:
            st, note = "новый ряд", "ряда нет в DSS, нужна ручная проверка"
        elif key in kv:
            old = kv[key]
            same = abs(r.value - old) <= max(0.051, abs(old) * 0.002)
            st, note = ("без изменений", "") if same else ("изменилось", f"в DSS: {old}")
        elif r.period_start < ser[0][0]:
            # DSS ведёт ряд с более позднего года — историю сам не дописываем
            st, note = "раньше начала ряда в DSS", f"ряд в DSS начинается с {ser[0][0][:4]}"
        elif r.period_start < ser[-1][0]:
            # пропуск внутри ряда мог быть удалён в DSS намеренно — только на проверку
            st, note = "пропуск в ряду DSS", f"ряд в DSS: {ser[0][0][:4]}-{ser[-1][0][:4]}"
        else:
            ref = ser[-1][1]  # последнее значение ряда в DSS
            ratio = (max(abs(r.value), abs(ref)) / max(min(abs(r.value), abs(ref)), 1e-9)) if ref else 1.0
            if ref and (r.value == 0) != (ref == 0):
                ratio = float("inf")
            if ratio > JUMP_RATIO:
                st, note = "скачок", f"последнее значение в DSS ({ser[-1][0][:4]}): {ref}"
            else:
                st, note = "новое", ""
        out.append((st, note))
    cand = cand.copy()
    cand["status"] = [s for s, _ in out]
    cand["note"] = [n for _, n in out]
    # Защита от рядов, которые ETL ведёт иначе, чем DSS (другая единица, сдвиг года,
    # например «на 1 января Y» против «на конец Y»): новые годы ряда отправляются, только
    # если в последних общих с DSS годах значения ETL совпадают не меньше чем в 80 % случаев.
    ser_key = list(zip(cand["direction_code"], cand["indicator_name"], cand["kato_code"], cand["period_type"]))
    cand["_ser"] = ser_key
    for sk, g in cand.groupby("_ser"):
        is_new = g.index[g["status"] == "новое"]
        if len(is_new) == 0:
            continue
        ov = g[g["status"].isin(["без изменений", "изменилось"])].sort_values("period_start").tail(AGREE_LAST_N)
        overlap = len(ov)
        agree = int((ov["status"] == "без изменений").sum())
        if overlap == 0:
            cand.loc[is_new, "status"] = "не проверено"
            cand.loc[is_new, "note"] = "нет общих с DSS лет для сверки ряда"
        elif agree / overlap < MATCH_SHARE:
            cand.loc[is_new, "status"] = "ряд не сходится с DSS"
            cand.loc[is_new, "note"] = f"в последних общих годах совпало {agree} из {overlap}"
    return cand.drop(columns="_ser")


AUTO = "обновляется автоматически"


def _indicator_summary(res: pd.DataFrame) -> pd.DataFrame:
    """Итог по каждому показателю ETL: уходят ли его новые годы в DSS и если нет, то почему.
    Совпадение считается как при отправке: по последним общим с DSS годам каждого ряда."""
    keys = ["source_file", "etl_name", "direction_code", "indicator_name", "unit_name"]
    rows = []
    for k, g in res.groupby(keys, dropna=False):
        st = g["status"].value_counts()
        agree = overlap = 0
        for _, s in g.groupby(["kato_code", "period_type"]):
            ov = s[s["status"].isin(["без изменений", "изменилось"])].sort_values("period_start").tail(AGREE_LAST_N)
            overlap += len(ov)
            agree += int((ov["status"] == "без изменений").sum())
        if st.get("нет в справочнике DSS", 0):
            grp, why = "нет в справочнике DSS", "в DSS нет показателя с таким названием и направлением"
        elif st.get("единица не как в DSS", 0):
            grp, why = "единица не как в DSS", g.loc[g["status"] == "единица не как в DSS", "note"].iloc[0]
        elif overlap == 0:
            grp, why = "нет общих лет с DSS", "рядов нет в DSS или нет общих лет для сверки"
        elif agree / overlap >= MATCH_SHARE:
            grp, why = AUTO, f"совпало с DSS {agree} из {overlap}"
        else:
            grp, why = "расходится с DSS", f"совпало с DSS {agree} из {overlap}"
        rows.append({**dict(zip(keys, k)), "группа": grp, "пояснение": why, "новых сейчас": int(st.get("новое", 0)),
                     "территорий": g["kato_code"].nunique(),
                     "годы": f"{g['period_start'].min()[:4]}-{g['period_start'].max()[:4]}"})
    return pd.DataFrame(rows).sort_values(["группа", "direction_code", "indicator_name"]).reset_index(drop=True)


def _dss_catalog(url, key) -> dict:
    h = {"X-API-Key": key}
    dirs = requests.get(f"{url}/api/v1/directions", headers=h, timeout=60)
    dirs.raise_for_status()
    dmap = {d["id"]: d["code"] for d in dirs.json()}
    ind = requests.get(f"{url}/api/v1/indicators", headers=h, params={"is_active": "true"}, timeout=60)
    ind.raise_for_status()
    return {(dmap.get(i["direction_id"]), i["name"]): i.get("unit_name") for i in ind.json() if i.get("is_active", True)}


def _batches() -> list:
    return json.loads(BATCHES.read_text(encoding="utf-8")) if BATCHES.exists() else []


def _refresh_batches(url, key, batches: list) -> list:
    """Обновляет статусы прошлых отправок по данным портала DSS."""
    for b in batches:
        if b.get("final"):
            continue
        try:
            r = requests.get(f"{url}/api/v1/data-intake/jobs/{b['job_id']}", headers={"X-API-Key": key}, timeout=60)
            if r.status_code == 404:
                b["status"], b["final"] = "deleted", True
                continue
            r.raise_for_status()
            data = r.json()
            st = (data.get("job") or data).get("status")
            b["status"] = st
            b["final"] = st in ("imported", "failed", "cancelled")
        except Exception as e:
            print(f"[warn] статус партии {b.get('job_id')} не получен: {str(e)[:120]}")
    return batches


def build_and_send(**ctx):
    conf = _conf(ctx)
    tag = _tag(ctx)
    WORK.mkdir(parents=True, exist_ok=True)
    url, key = _intake()
    switch = _send_switch()
    age = _snapshot_age_days()
    report = {"month": tag, "started": pendulum.now(TZ).isoformat(), "send_switch": switch, "snapshot_days": age}
    if age is None:
        report["error"] = f"нет снимка DSS {SNAPSHOT.name} — партия не готовится"
        (WORK / f"report_{tag}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        raise RuntimeError(report["error"])

    # кандидаты: Талдау (обе части этого месяца, иначе текущая таблица) + климат
    engine = _engine()
    parts = [p for p in (_part_path(tag, s, "transformed_taldau") for s in ("districts", "oblast")) if p.exists()]
    frames = [pd.read_csv(p, dtype={"kato_code": str}) for p in parts]
    if not frames:
        frames.append(_read_taldau(engine, "transformed_taldau"))
    try:
        report["taldau_catalog_active"] = len(_taldau_catalog(engine))
    except Exception as e:
        print(f"[warn] каталог Талдау не прочитан: {str(e)[:120]}")
    try:
        wthr = pd.read_sql("SELECT * FROM transformed_weather", engine)
        # после усреднения по станциям у числовых строк нет source_file
        wthr["source_file"] = wthr["source_file"].fillna("open_meteo") if "source_file" in wthr else "open_meteo"
        frames.append(wthr)
    except Exception as e:
        print(f"[warn] transformed_weather недоступна: {e}")
    cand = _norm(pd.concat([f[[c for c in COLS if c in f.columns]] for f in frames], ignore_index=True))
    cand = _apply_map(cand)
    cand = cand.drop_duplicates(subset=KEY_COLS, keep="last")

    # справочник DSS: по API портала (заодно проверка, что портал доступен), иначе из снимка
    snap = load_snapshot(SNAPSHOT)
    portal_ok = False
    catalog = {}
    if url and key:
        try:
            catalog = _dss_catalog(url, key)
            portal_ok = True
        except Exception as e:
            print(f"[warn] портал DSS недоступен ({str(e)[:120]}) — справочник берём из снимка")
    if not catalog:
        act = snap
        if "is_active" in snap:
            act = snap[snap["is_active"].astype(str).str.lower().isin(["t", "true", "1"])]
        catalog = {(d, n): u for d, n, u in
                   act[["direction_code", "indicator_name", "unit_name"]].drop_duplicates().itertuples(index=False)}

    # что уже есть в DSS: снимок + свои отправки (imported и ещё не решённые);
    # строки отклонённых/удалённых партий повторно не предлагаются
    batches = _batches()
    if portal_ok:
        batches = _refresh_batches(url, key, batches)
    rejected_st = ("failed", "cancelled", "deleted")
    sent_rows = [r for b in batches if b.get("status") not in rejected_st for r in b.get("rows", [])]
    rejected = {tuple(r[:5]) for b in batches if b.get("status") in rejected_st for r in b.get("rows", [])}
    known = pd.concat([snap[KEY_COLS + ["value"]],
                       pd.DataFrame(sent_rows, columns=KEY_COLS + ["value"])], ignore_index=True)

    res = classify(cand, known, catalog)
    if rejected:
        was_rejected = pd.Series([k in rejected for k in res[KEY_COLS].itertuples(index=False, name=None)],
                                 index=res.index)
        mask = (res["status"] == "новое") & was_rejected
        res.loc[mask, "status"] = "отклонено в DSS ранее"
        res.loc[mask, "note"] = "это значение было в партии, которую DSS отклонил или удалил"
    counts = res["status"].value_counts().to_dict()
    new = res[res["status"] == "новое"].copy()
    # на проверку человеку — только последние годы (старые расхождения ETL и DSS известны)
    recent = str(pendulum.now(TZ).year - REVIEW_YEARS_BACK)
    review = res[~res["status"].isin(["новое", "без изменений", "раньше начала ряда в DSS"])
                 & (res["period_start"].str[:4] >= recent)]
    review.to_csv(WORK / f"review_{tag}.csv", index=False, encoding="utf-8-sig")
    res.to_csv(WORK / f"classified_{tag}.csv.gz", index=False, compression="gzip")
    summ = _indicator_summary(res)
    summ.to_csv(WORK / f"indicators_{tag}.csv", index=False, encoding="utf-8-sig")
    report.update({"candidates": len(res), "by_status": counts, "review_rows": len(review),
                   "portal_ok": portal_ok, "new_by_indicator": new["indicator_name"].value_counts().to_dict(),
                   "indicators": summ["группа"].value_counts().to_dict(),
                   "auto_update": sorted(summ.loc[summ["группа"] == AUTO, "indicator_name"].unique().tolist())})
    print("итог отбора:", counts)
    print("показатели:", report["indicators"])

    blocked = []
    if conf.get("send") is False:
        blocked.append('запуск с conf {"send": false}')
    if not switch:
        blocked.append(f"отправка выключена (Variable {SEND_VAR})")
    if not (url and key):
        blocked.append("нет DSS_INTAKE_URL / DSS_INTAKE_KEY")
    elif not portal_ok:
        blocked.append("портал DSS недоступен из Airflow")
    if age > SNAPSHOT_MAX_AGE_DAYS:
        blocked.append(f"снимку DSS {age} дн. (допустимо до {SNAPSHOT_MAX_AGE_DAYS})")
    send = not blocked
    report["send"] = send
    if blocked:
        report["send_blocked"] = blocked

    if new.empty:
        report["result"] = "новых значений нет — партия не нужна"
    else:
        src = {"taldau_api": "Talдау (БНС)", "open_meteo": "Open-Meteo", "kazhydromet_fact": "Казгидромет"}
        new["comment"] = [f"Ежемесячное обновление ETL {tag}: {src.get(s, s)}, загрузка {datetime.now():%d.%m.%Y}"
                          for s in new["source_file"].fillna("")]
        sys.path.insert(0, ETL_DIR)
        from excel_builder import build_dss_excel
        xlsx = WORK / f"dss_monthly_{tag}.xlsx"
        build_dss_excel(new, str(xlsx))
        shutil.copy2(xlsx, ARTIFACTS / xlsx.name)  # в общий список файлов сайта ETL
        report["file"] = xlsx.name
        if not send:
            report["result"] = (f"подготовлено {len(new)} новых значений: {xlsx.name} (есть в списке файлов "
                                f"сайта ETL); в DSS не отправлено: " + "; ".join(blocked))
        else:
            with open(xlsx, "rb") as fh:
                r = requests.post(f"{url}/api/v1/data-intake/excel", headers={"X-API-Key": key},
                                  files={"file": (xlsx.name, fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                                  timeout=300)
            if r.status_code != 200:
                report["result"] = f"DSS ответил HTTP {r.status_code}: {r.text[:300]}"
                (WORK / f"report_{tag}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                raise RuntimeError(report["result"])
            job = (r.json().get("job") or r.json())
            report.update({"job_id": job.get("id"), "job_status": job.get("status"), "rows_ok": job.get("rows_ok"),
                           "rows_invalid": job.get("rows_invalid"),
                           "result": f"отправлено {len(new)} новых значений, черновик в DSS ждёт подтверждения"})
            batches.append({"job_id": job.get("id"), "month": tag, "file": xlsx.name, "status": job.get("status"),
                            "final": False, "sent_at": pendulum.now(TZ).isoformat(),
                            "rows": new[KEY_COLS + ["value"]].values.tolist()})
    BATCHES.write_text(json.dumps(batches, ensure_ascii=False), encoding="utf-8")
    report["finished"] = pendulum.now(TZ).isoformat()
    (WORK / f"report_{tag}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "new_by_indicator"}, ensure_ascii=False, indent=1))


def finish(**ctx):
    """Итог запуска: если любой шаг упал — весь запуск помечается ошибкой (видно в Airflow)."""
    failed = [ti.task_id for ti in ctx["dag_run"].get_task_instances()
              if ti.task_id != "finish" and str(ti.state).lower().endswith(("failed", "upstream_failed"))]
    rep = WORK / f"report_{_tag(ctx)}.json"
    if rep.exists():
        print(rep.read_text(encoding="utf-8"))
    if failed:
        raise RuntimeError(f"шаги с ошибкой: {', '.join(failed)} — подробности в логах этих шагов")
    print("все шаги выполнены")


# ---------------------------------------------------------------- DAG
with DAG(
    "dag_monthly_update",
    description="Ежемесячное обновление: Талдау, климат, agromap -> новые значения черновиком в DSS",
    schedule="0 3 1 * *",
    start_date=pendulum.datetime(2026, 10, 1, tz=TZ),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "airflow", "retries": 0},
    tags=["monthly", "load"],
) as dag:
    t_prep = PythonOperator(task_id="prepare", python_callable=prepare)
    t_tal_d = PythonOperator(task_id="taldau_districts", python_callable=run_taldau, op_kwargs={"split": True})
    # по области — только если по районам удалось (иначе Талдау недоступен, а таблицы вернёт taldau_restore)
    t_tal_o = PythonOperator(task_id="taldau_oblast", python_callable=run_taldau, op_kwargs={"split": False})
    t_rest = PythonOperator(task_id="taldau_restore", python_callable=restore_taldau, trigger_rule=TriggerRule.ALL_DONE)
    t_wthr = PythonOperator(task_id="weather", python_callable=run_weather, trigger_rule=TriggerRule.ALL_DONE)
    t_agro = PythonOperator(task_id="agromap", python_callable=run_agromap, trigger_rule=TriggerRule.ALL_DONE)
    t_send = PythonOperator(task_id="build_and_send", python_callable=build_and_send, trigger_rule=TriggerRule.ALL_DONE)
    t_fin = PythonOperator(task_id="finish", python_callable=finish, trigger_rule=TriggerRule.ALL_DONE)

    t_prep >> t_tal_d >> t_tal_o >> t_rest >> t_wthr >> t_send >> t_fin
    t_prep >> t_agro >> t_fin
