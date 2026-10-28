"""
Загрузка среднемноголетних норм климатических показателей в indicator_norms.

Источник: docs/dashboards/climate/Клим_данные_по_метеостанциям_СКО.xlsx
  • 4 «mean»-листа (Tmean03_24, Rmean03_24, Kmean03_24, GTKmean03_24): ручной
    среднемноголетний CC-столбец «по Сакену Советовичу» (последний числовой
    столбец, суффикс _CC/_СС) — method='manual_cc'. Покрытие: лето (05–08) для
    T/R + годовые K и ГТК.
  • Декадные листы T_R_YYYY (2003–2025): выводим сезонные суммы за каждый год
    (месяц = сумма трёх декад R_d{М}_0{1..3}) и усредняем по годам — method='derived_mean'. Сейчас выводим: Σt>10 (годовая
    сумма активных T>10), осадки холодного периода (XI–III), весна (IV–V),
    осень (IX–X).

Норма считается по станции, затем агрегируется до района (среднее по станциям
района). Станция→район берётся из таблицы meteo_stations (миграция 020); по ней
же отбрасываются станции соседних областей (в meteo_stations только 19 СКО).

Показатель резолвится по подстроке имени среди индикаторов направления climate
(как на дашбордах). Несопоставленные серии/районы логируются, не падаем.

Запуск (идемпотентно, UPSERT по indicator_norms_unique):
    POSTGRES_PORT=5433 backend/.venv/bin/python backend/scripts/import_climate_norms.py [--dry-run] [--file PATH]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import psycopg2
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FILE = ROOT / "docs" / "dashboards" / "climate" / "Клим_данные_по_метеостанциям_СКО.xlsx"

# Метаданные «mean»-листов: лист → (имя CC-серии). CC-столбец = последний
# непустой числовой столбец строки станции (после авто-среднего).
MEAN_SHEETS = {
    "Kmean03_24": "K",
    "GTKmean03_24": "GTK",
    "Rmean03_24": "R_veg_05_08",
    # Tmean03_24 (ΣсрT 05–08) НЕ грузим как Σt>10 — это разные величины;
    # Σt>10 выводим из декадных листов (ΣT_10C) ниже.
}

# Серия нормы → кандидаты подстрок имени индикатора (climate), как resolve на фронте.
# Только сопоставленные серии попадут в indicator_norms.
# Σt>10 не выводим: precomputed-столбцы ΣT_10C/ΣT_5C в файле пусты, а порог по
# декадным T — это уже не «их данные», а наша реконструкция (фабриковать не будем).
SERIES_TO_INDICATOR: dict[str, list[str]] = {
    "K": ["коэффициент увлажнения"],
    "GTK": ["гидротермический коэффициент"],
    "R_veg_05_08": ["сумма осадков за вегетационный"],
    "R_cold_11_03": ["сумма осадков за холодный"],
    # производные без host-индикатора в каталоге — будут залогированы:
    "R_spring_04_05": ["осадки за весенний"],
    "R_autumn_09_10": ["осадки за осенний"],
}


def norm_str(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).lower().replace("ё", "е")).strip()


def db_connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5433")),
        user=os.environ.get("POSTGRES_USER", "dss_user"),
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "dss_db"),
    )


def header_index(ws) -> dict[str, int]:
    """Карта имя_колонки → индекс по первой строке листа."""
    hdr = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
    return {str(h): i for i, h in enumerate(hdr) if h is not None}


def find_cc_column(cols: dict[str, int]) -> int | None:
    """Индекс ручного среднемноголетнего столбца «по Сакену Советовичу».
    Заголовок оканчивается на CC*/СС* (латиница/кириллица), напр.
    'Rmean03_24_СС*'. ВНИМАНИЕ: в текущей версии файла этот столбец ПУСТ —
    значения «по Сакену» ещё не внесены, см. fallback на авто-среднее ниже."""
    for name, idx in cols.items():
        if re.search(r"(CC|СС)\*?$", name):
            return idx
    return None


def column_has_data(ws, idx: int) -> bool:
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[1] and idx < len(row) and isinstance(row[idx], (int, float)):
            return True
    return False


# ── Чтение per-station норм из xlsx ───────────────────────────

def read_mean_sheets(wb) -> tuple[dict[str, dict[str, float]], dict[str, str]]:
    """({series_key: {station: norm}}, {series_key: method}) из 4 mean-листов.

    Приоритет столбца: ручной CC/СС («по Сакену Советовичу») если заполнен →
    method='manual_cc'; иначе авто-средний столбец (имя = имя листа, напр.
    'Kmean03_24') → method='auto_mean'. На сейчас CC пуст во всех 4 листах.
    """
    out: dict[str, dict[str, float]] = defaultdict(dict)
    method_of: dict[str, str] = {}
    for sheet, series in MEAN_SHEETS.items():
        if sheet not in wb.sheetnames:
            print(f"  [warn] нет листа {sheet}")
            continue
        ws = wb[sheet]
        cols = header_index(ws)
        cc = find_cc_column(cols)
        auto = cols.get(sheet)  # авто-средний столбец называется как лист
        if cc is not None and column_has_data(ws, cc):
            use_col, method = cc, "manual_cc"
        elif auto is not None:
            use_col, method = auto, "auto_mean"
            print(f"  [info] {sheet}: CC-столбец пуст → беру авто-среднее '{sheet}' (col {auto})")
        else:
            print(f"  [warn] {sheet}: не найден ни CC, ни авто-средний столбец")
            continue
        method_of[series] = method
        for row in ws.iter_rows(min_row=2, values_only=True):
            station = row[1]
            if not station or use_col >= len(row):
                continue
            val = row[use_col]
            if isinstance(val, (int, float)):
                out[series][str(station).strip()] = float(val)
    return out, method_of


def read_decadal_norms(wb) -> dict[str, dict[str, float]]:
    """{series_key: {station: mean_over_years}} из листов T_R_YYYY (derived_mean).

    Для каждого года считаем сезонную сумму по станции, затем усредняем по годам.
    """
    # накопление: series -> station -> [годовые значения]
    acc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    year_sheets = sorted(s for s in wb.sheetnames if re.fullmatch(r"T_R_\d{4}", s))
    for sheet in year_sheets:
        ws = wb[sheet]
        cols = header_index(ws)

        # Месячная сумма осадков = сумма трёх декад R_d{М}_0{1..3}.
        # Столбцы R_mean_MM в файле — СРЕДНЕЕ трёх декад, а не сумма за месяц
        # (раньше суммировались они, и нормы осадков получались ≈3 раза ниже).
        rdek = {m: [cols.get(f"R_d{m}_0{d}") for d in (1, 2, 3)] for m in range(1, 13)}

        for row in ws.iter_rows(min_row=2, values_only=True):
            station = row[1]
            if not station:
                continue
            st = str(station).strip()

            def month_sum(m: int) -> float | None:
                vals = [row[i] if i is not None and i < len(row) else None for i in rdek[m]]
                if any(not isinstance(v, (int, float)) for v in vals):
                    return None  # месяц без всех трёх декад не считается
                return float(sum(vals))

            def rsum(months: list[int]) -> float | None:
                vals = [month_sum(m) for m in months]
                # сезон за год — только если есть все его месяцы
                return sum(vals) if all(v is not None for v in vals) else None

            for series, months in (
                ("R_cold_11_03", [11, 12, 1, 2, 3]),
                ("R_spring_04_05", [4, 5]),
                ("R_autumn_09_10", [9, 10]),
            ):
                v = rsum(months)
                if v is not None:
                    acc[series][st].append(v)

    out: dict[str, dict[str, float]] = defaultdict(dict)
    for series, by_st in acc.items():
        for st, years in by_st.items():
            if years:
                out[series][st] = sum(years) / len(years)
    return out


# ── Агрегация станция→район и загрузка ────────────────────────

def aggregate_to_region(
    per_station: dict[str, float], station_region: dict[str, str]
) -> dict[str, float]:
    """Среднее по станциям района. Ключ результата — region_id (str)."""
    by_region: dict[str, list[float]] = defaultdict(list)
    for station, val in per_station.items():
        rid = station_region.get(station)
        if rid is None:
            continue  # станция вне СКО / нет в meteo_stations
        by_region[rid].append(val)
    return {rid: sum(v) / len(v) for rid, v in by_region.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description="Импорт среднемноголетних норм климата")
    ap.add_argument("--file", type=Path, default=DEFAULT_FILE)
    ap.add_argument("--dry-run", action="store_true", help="не писать в БД")
    args = ap.parse_args()

    if not args.file.exists():
        print(f"Файл не найден: {args.file}", file=sys.stderr)
        return 1

    print(f"Читаю {args.file.name} …")
    wb = load_workbook(args.file, read_only=True, data_only=True)
    mean_norms, mean_methods = read_mean_sheets(wb)
    derived_norms = read_decadal_norms(wb)
    methods = {**mean_methods, **{k: "derived_mean" for k in derived_norms}}
    all_norms = {**mean_norms, **derived_norms}
    ref_years = {
        "manual_cc": (date(2003, 1, 1), date(2024, 12, 31)),
        "auto_mean": (date(2003, 1, 1), date(2024, 12, 31)),
        "derived_mean": (date(2003, 1, 1), date(2025, 12, 31)),
    }

    conn = db_connect()
    cur = conn.cursor()

    # станция → region_id (только СКО, из meteo_stations)
    cur.execute("SELECT name, region_id::text FROM meteo_stations")
    station_region = {name: rid for name, rid in cur.fetchall()}
    print(f"meteo_stations: {len(station_region)} станций")
    if not station_region:
        print("[error] meteo_stations пуста — примените миграцию 020", file=sys.stderr)
        return 2

    # climate-индикаторы: name → id (для резолва по подстроке)
    cur.execute(
        """SELECT i.id::text, i.name FROM indicators i
           JOIN directions d ON d.id = i.direction_id WHERE d.code = 'climate'"""
    )
    climate_inds = [(iid, name) for iid, name in cur.fetchall()]

    def resolve_indicator(candidates: list[str]) -> tuple[str, str] | None:
        for cand in candidates:
            needle = norm_str(cand)
            for iid, name in climate_inds:
                if needle in norm_str(name):
                    return iid, name
        return None

    upserts: list[tuple] = []
    for series, per_station in all_norms.items():
        method = methods[series]
        rp_start, rp_end = ref_years[method]
        period_label = f"multiyear_{rp_start.year}_{rp_end.year}"
        by_region = aggregate_to_region(per_station, station_region)

        candidates = SERIES_TO_INDICATOR.get(series, [])
        match = resolve_indicator(candidates) if candidates else None
        if match is None:
            sample = next(iter(by_region.values()), None)
            print(
                f"  [skip] серия '{series}' ({method}): нет индикатора в каталоге "
                f"(кандидаты {candidates}); районов={len(by_region)}, пример={sample}"
            )
            continue
        iid, iname = match
        print(f"  [ok]   '{series}' → «{iname}» ({method}): районов={len(by_region)}")
        for rid, val in by_region.items():
            upserts.append((iid, rid, val, period_label, method, rp_start, rp_end, series))

    if args.dry_run:
        print(f"\nDRY-RUN: подготовлено {len(upserts)} норм (район×показатель), запись пропущена.")
        return 0

    sql = """
        INSERT INTO indicator_norms
            (indicator_id, region_id, norm_value, period_label, method,
             ref_period_start, ref_period_end, note)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT ON CONSTRAINT indicator_norms_unique
        DO UPDATE SET norm_value = EXCLUDED.norm_value,
                      method = EXCLUDED.method,
                      ref_period_start = EXCLUDED.ref_period_start,
                      ref_period_end = EXCLUDED.ref_period_end,
                      note = EXCLUDED.note,
                      updated_at = NOW()
    """
    cur.executemany(sql, upserts)
    conn.commit()
    print(f"\nЗагружено/обновлено норм: {len(upserts)}")
    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
