"""Разбор файла заказчика с декадными данными метеостанций.

Файл «Клим_данные_по_метеостанциям_СКО.xlsx», листы T_R_YYYY: строка — станция
(колонка station), колонки декад:
- температура — ``T_d{декада}_{ММ}``  (T_d1_01 — январь, 1-я декада);
- осадки      — ``R_d{М}_{0Д}``        (R_d1_01 — январь, 1-я декада; R_d12_03 — декабрь, 3-я).
Порядок индексов у температуры и осадков разный — это свойство исходного файла.

Колонки *_mean_* не используются: R_mean — среднее трёх декад, а не сумма за месяц,
и месяц Система считает сама (app/services/climate_dekads.py). Пустые ячейки
пропускаются, станции других областей отбрасывает вызывающий код.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

SHEET_RE = re.compile(r"^T_R_(\d{4})$")
TEMP_RE = re.compile(r"^T_d([1-3])_(\d{2})$")   # декада, месяц
PRECIP_RE = re.compile(r"^R_d(\d{1,2})_0([1-3])$")  # месяц, декада


@dataclass(frozen=True)
class DekadCell:
    station: str
    kind: str      # 'T' — температура, 'R' — осадки
    year: int
    month: int
    dekad: int
    value: float


def parse_columns(header: Iterable[Optional[str]]) -> dict[int, tuple[str, int, int]]:
    """Номер колонки → (вид, месяц, декада) для декадных колонок заголовка."""
    out: dict[int, tuple[str, int, int]] = {}
    for idx, raw in enumerate(header):
        name = str(raw).strip() if raw is not None else ""
        if m := TEMP_RE.match(name):
            out[idx] = ("T", int(m.group(2)), int(m.group(1)))
        elif m := PRECIP_RE.match(name):
            out[idx] = ("R", int(m.group(1)), int(m.group(2)))
    return {i: v for i, v in out.items() if 1 <= v[1] <= 12}


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ".").replace("−", "-"))
    except ValueError:
        return None


def parse_sheet(year: int, rows: list[tuple]) -> list[DekadCell]:
    if not rows:
        return []
    header = rows[0]
    try:
        st_idx = [str(h).strip() if h is not None else "" for h in header].index("station")
    except ValueError:
        return []
    cols = parse_columns(header)
    cells: list[DekadCell] = []
    for row in rows[1:]:
        if st_idx >= len(row) or not row[st_idx]:
            continue
        station = str(row[st_idx]).strip()
        for idx, (kind, month, dekad) in cols.items():
            if idx >= len(row):
                continue
            value = _num(row[idx])
            if value is not None:
                cells.append(DekadCell(station, kind, year, month, dekad, value))
    return cells


def parse_workbook(wb, years: Optional[range] = None) -> list[DekadCell]:
    """Все декадные значения книги openpyxl (листы T_R_YYYY)."""
    cells: list[DekadCell] = []
    for name in wb.sheetnames:
        m = SHEET_RE.match(name)
        if not m:
            continue
        year = int(m.group(1))
        if years is not None and year not in years:
            continue
        cells.extend(parse_sheet(year, list(wb[name].iter_rows(values_only=True))))
    return cells


# Варианты написания станций в файлах заказчика → название в справочнике meteo_stations.
STATION_ALIASES = {"новоишинский": "новоишимский", "талшик": "талшык"}


def norm_station(name: str) -> str:
    key = name.strip().lower().replace("ё", "е")
    return STATION_ALIASES.get(key, key)


SUBSTITUTE_RE = re.compile(r"^\s*(.+?)\s*\(\s*по\s+(.+?)\s*\)\s*$")


def parse_substitutions(wb, known: Iterable[str]) -> dict[tuple[int, str], str]:
    """Строки вида «Пресновка (по Благовещенке)» под таблицей года: данные станции
    за этот год восполнены по соседней станции. Возвращает {(год, станция): донор}
    в нормализованных названиях справочника (склонения сопоставляются по основе)."""
    known = list(known)

    def match(raw: str) -> Optional[str]:
        n = norm_station(raw)
        if n in known:
            return n
        stem = n[:6]
        hits = [k for k in known if k.startswith(stem)]
        return hits[0] if len(hits) == 1 else None

    out: dict[tuple[int, str], str] = {}
    for name in wb.sheetnames:
        m = SHEET_RE.match(name)
        if not m:
            continue
        year = int(m.group(1))
        for row in wb[name].iter_rows(values_only=True):
            cell = row[1] if len(row) > 1 else None
            sm = SUBSTITUTE_RE.match(str(cell)) if isinstance(cell, str) else None
            if not sm:
                continue
            base, donor = match(sm.group(1)), match(sm.group(2))
            if base and donor:
                out[(year, base)] = donor
    return out
