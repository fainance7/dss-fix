"""Подекадные климатические данные по метеостанциям → месяц станции → месяц района.

Первичные данные — декады по станциям (meteo_station_values, period_type='dekad'):
1-я декада — дни 1–10, 2-я — 11–20, 3-я — с 21-го до конца месяца.

Месяц станции (period_type='month', is_derived) считается, только когда есть все три
декады и ни одна не отклонена:
- температура — среднее, взвешенное по дням: (d1·10 + d2·10 + d3·(N−20)) / N;
- осадки — сумма трёх декад.
Статус месяца: approved, если все три декады утверждены, иначе pending.

Месяц района пишется в indicator_values (period_type='month', источник «Расчёт DSS»)
как среднее месячных значений станций района по справочнику
climate_region_station_map (для г. Петропавловска — ближайшая станция). Статус:
approved, если все участвующие станционные месяцы утверждены, иначе pending.

Если условия расчёта перестали выполняться (декаду отклонили или удалили), расчётный
месяц станции и района удаляется — устаревшее значение не остаётся.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TEMPERATURE = "Средняя температура воздуха (по метеостанциям)"
PRECIPITATION = "Сумма осадков (по метеостанциям)"
# Способ получения месяца из декад — явно по показателю, без эвристик по имени.
AGGREGATION = {TEMPERATURE: "mean_weighted", PRECIPITATION: "sum"}
CALC_SOURCE = "Расчёт DSS"
DEKAD_SOURCE = "РГП «Казгидромет» — декадные данные метеостанций"

# (station_id, indicator_id, первое число месяца)
MonthKey = tuple[UUID, UUID, date]


# ── чистые функции ───────────────────────────────────────────────────────────

def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def dekad_bounds(year: int, month: int, dekad: int) -> tuple[date, date]:
    """Начало и конец декады (включительно)."""
    if dekad not in (1, 2, 3):
        raise ValueError(f"Декада должна быть 1, 2 или 3: {dekad}")
    start = date(year, month, 1 + (dekad - 1) * 10)
    end = start + timedelta(days=9) if dekad < 3 else date(year, month, days_in_month(year, month))
    return start, end


def dekad_of(start: date) -> Optional[int]:
    """Номер декады по дате начала или None, если дата не 1, 11 или 21 число."""
    return {1: 1, 11: 2, 21: 3}.get(start.day)


def expected_dekad_end(start: date) -> Optional[date]:
    d = dekad_of(start)
    return dekad_bounds(start.year, start.month, d)[1] if d else None


def month_temperature(d1: float, d2: float, d3: float, year: int, month: int) -> float:
    n = days_in_month(year, month)
    return (d1 * 10 + d2 * 10 + d3 * (n - 20)) / n


def month_precip(d1: float, d2: float, d3: float) -> float:
    return d1 + d2 + d3


def month_value(kind: str, d1: float, d2: float, d3: float, year: int, month: int) -> float:
    if kind == "mean_weighted":
        return month_temperature(d1, d2, d3, year, month)
    if kind == "sum":
        return month_precip(d1, d2, d3)
    raise ValueError(f"Неизвестный способ агрегации: {kind}")


# ── пересчёт в БД ────────────────────────────────────────────────────────────

async def _indicator_kinds(db: AsyncSession) -> dict[UUID, str]:
    rows = (await db.execute(
        text("SELECT id, name FROM indicators WHERE name = ANY(:names)"),
        {"names": list(AGGREGATION)},
    )).all()
    return {r.id: AGGREGATION[r.name] for r in rows}


async def _source_id(db: AsyncSession, name: str) -> Optional[UUID]:
    return (await db.execute(
        text("SELECT id FROM data_sources WHERE name = :n ORDER BY created_at LIMIT 1"), {"n": name},
    )).scalar_one_or_none()


def month_keys_from_values(rows: Iterable[tuple[UUID, UUID, date]]) -> set[MonthKey]:
    """Ключи месяцев, затронутых изменением декад (station_id, indicator_id, period_start)."""
    return {(st, ind, d.replace(day=1)) for st, ind, d in rows}


_STATION_MONTHS_SQL = """
WITH k AS (
    SELECT * FROM unnest(CAST(:st AS uuid[]), CAST(:ind AS uuid[]), CAST(:m AS date[]))
        AS k(station_id, indicator_id, m)
),
d AS (
    SELECT k.station_id, k.indicator_id, k.m,
           count(v.id) FILTER (WHERE v.validation_status <> 'rejected') AS n_ok,
           bool_and(v.validation_status = 'approved') AS all_approved,
           max(v.value) FILTER (WHERE v.dekad = 1 AND v.validation_status <> 'rejected') AS d1,
           max(v.value) FILTER (WHERE v.dekad = 2 AND v.validation_status <> 'rejected') AS d2,
           max(v.value) FILTER (WHERE v.dekad = 3 AND v.validation_status <> 'rejected') AS d3
    FROM k LEFT JOIN meteo_station_values v
      ON v.station_id = k.station_id AND v.indicator_id = k.indicator_id
     AND v.period_type = 'dekad' AND date_trunc('month', v.period_start)::date = k.m
    GROUP BY k.station_id, k.indicator_id, k.m
)
SELECT station_id, indicator_id, m, n_ok, all_approved, d1, d2, d3 FROM d
"""


async def recompute(db: AsyncSession, keys: set[MonthKey]) -> dict[str, int]:
    """Пересчитать месяцы станций и районов, затем годовые и сезонные показатели районов."""
    stats = await recompute_months(db, keys)
    if stats["region_months"] or stats["region_months_deleted"]:
        stats.update(await recompute_yearly(db, years_from_month_keys(keys)))
    return stats


async def recompute_months(db: AsyncSession, keys: set[MonthKey]) -> dict[str, int]:
    """Пересчитать месяцы станций по ключам и месяцы затронутых районов.

    Выполняется в транзакции вызывающего кода; параллельные пересчёты
    сериализуются advisory-блокировкой.
    """
    stats = {"station_months": 0, "station_months_deleted": 0,
             "region_months": 0, "region_months_deleted": 0}
    if not keys:
        return stats
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('climate_dekads'))"))
    kinds = await _indicator_kinds(db)
    keys = {k for k in keys if k[1] in kinds}
    if not keys:
        return stats
    calc_source = await _source_id(db, CALC_SOURCE)

    st, ind, ms = zip(*sorted(keys))
    rows = (await db.execute(text(_STATION_MONTHS_SQL),
                             {"st": list(st), "ind": list(ind), "m": list(ms)})).all()

    upserts, deletes = [], []
    for r in rows:
        if r.n_ok == 3 and None not in (r.d1, r.d2, r.d3):
            value = month_value(kinds[r.indicator_id], r.d1, r.d2, r.d3, r.m.year, r.m.month)
            end = date(r.m.year, r.m.month, days_in_month(r.m.year, r.m.month))
            upserts.append({"st": r.station_id, "ind": r.indicator_id, "ps": r.m, "pe": end, "v": value,
                            "status": "approved" if r.all_approved else "pending", "src": calc_source})
        else:
            deletes.append({"st": r.station_id, "ind": r.indicator_id, "ps": r.m})

    if upserts:
        await db.execute(text("""
            INSERT INTO meteo_station_values
                (id, station_id, indicator_id, period_type, period_start, period_end, value,
                 is_derived, derivation, source_id, validation_status, validated_at)
            VALUES (gen_random_uuid(), :st, :ind, 'month', :ps, :pe, :v, TRUE, 'month_from_dekads', :src, CAST(:status AS varchar),
                    CASE WHEN CAST(:status AS varchar) = 'approved' THEN now() END)
            ON CONFLICT ON CONSTRAINT msv_unique DO UPDATE SET
                value = EXCLUDED.value, period_end = EXCLUDED.period_end,
                validation_status = EXCLUDED.validation_status, validated_at = EXCLUDED.validated_at,
                validated_by = NULL, source_id = EXCLUDED.source_id
            WHERE meteo_station_values.is_derived
        """), upserts)
        stats["station_months"] = len(upserts)
    if deletes:
        await db.execute(text("""
            DELETE FROM meteo_station_values
            WHERE station_id = :st AND indicator_id = :ind AND period_type = 'month'
              AND period_start = :ps AND is_derived
        """), deletes)
        stats["station_months_deleted"] = len(deletes)  # проверено ключей (executemany не даёт rowcount)

    # ── районы, в которые входят затронутые станции ──
    region_keys = (await db.execute(text("""
        SELECT DISTINCT m.region_id, k.indicator_id, k.m
        FROM unnest(CAST(:st AS uuid[]), CAST(:ind AS uuid[]), CAST(:m AS date[])) AS k(station_id, indicator_id, m)
        JOIN climate_region_station_map m ON m.station_id = k.station_id
    """), {"st": list(st), "ind": list(ind), "m": list(ms)})).all()
    if not region_keys:
        return stats
    rg, rind, rm = zip(*[(r.region_id, r.indicator_id, r.m) for r in region_keys])
    agg = (await db.execute(text("""
        WITH k AS (
            SELECT * FROM unnest(CAST(:rg AS uuid[]), CAST(:ind AS uuid[]), CAST(:m AS date[]))
                AS k(region_id, indicator_id, m)
        )
        SELECT k.region_id, k.indicator_id, k.m,
               avg(v.value) AS value, count(v.id) AS n,
               bool_and(v.validation_status = 'approved') AS all_approved
        FROM k
        JOIN climate_region_station_map map ON map.region_id = k.region_id
        LEFT JOIN meteo_station_values v
          ON v.station_id = map.station_id AND v.indicator_id = k.indicator_id
         AND v.period_type = 'month' AND v.period_start = k.m AND v.validation_status <> 'rejected'
        GROUP BY k.region_id, k.indicator_id, k.m
    """), {"rg": list(rg), "ind": list(rind), "m": list(rm)})).all()

    iv_up, iv_del = [], []
    for r in agg:
        if r.n:
            end = date(r.m.year, r.m.month, days_in_month(r.m.year, r.m.month))
            iv_up.append({"ind": r.indicator_id, "rg": r.region_id, "ps": r.m, "pe": end, "v": r.value,
                          "status": "approved" if r.all_approved else "pending", "src": calc_source})
        else:
            iv_del.append({"ind": r.indicator_id, "rg": r.region_id, "ps": r.m})
    if iv_up:
        await db.execute(text("""
            INSERT INTO indicator_values
                (id, indicator_id, region_id, period_type, period_start, period_end, value,
                 source_id, validation_status, validated_at)
            VALUES (gen_random_uuid(), :ind, :rg, 'month', :ps, :pe, :v, :src, CAST(:status AS varchar),
                    CASE WHEN CAST(:status AS varchar) = 'approved' THEN now() END)
            ON CONFLICT ON CONSTRAINT iv_unique_value DO UPDATE SET
                value = EXCLUDED.value, period_end = EXCLUDED.period_end, source_id = EXCLUDED.source_id,
                validation_status = EXCLUDED.validation_status, validated_at = EXCLUDED.validated_at,
                validated_by = NULL
        """), iv_up)
        stats["region_months"] = len(iv_up)
    if iv_del:
        await db.execute(text("""
            DELETE FROM indicator_values
            WHERE indicator_id = :ind AND region_id = :rg AND period_type = 'month' AND period_start = :ps
        """), iv_del)
        stats["region_months_deleted"] = len(iv_del)  # проверено ключей
    return stats


async def recompute_all(db: AsyncSession, year_from: Optional[int] = None,
                        year_to: Optional[int] = None) -> dict[str, int]:
    """Полный пересчёт: все месяцы, где есть декады или уже есть расчётный месяц."""
    cond, params = "", {}
    if year_from:
        cond += " AND period_start >= :df"
        params["df"] = date(year_from, 1, 1)
    if year_to:
        cond += " AND period_start <= :dt"
        params["dt"] = date(year_to, 12, 31)
    rows = (await db.execute(text(f"""
        SELECT DISTINCT station_id, indicator_id, date_trunc('month', period_start)::date
        FROM meteo_station_values WHERE TRUE {cond}
    """), params)).all()
    stats = await recompute_months(db, {(r[0], r[1], r[2]) for r in rows})
    years = None
    if year_from or year_to:
        years = set(range(year_from or 1900, (year_to or date.today().year) + 2))
    stats.update(await recompute_yearly(db, years))
    return stats


# ── годовые и сезонные показатели района из месяцев района ───────────────────
#
# Значение за год y считается только при полном наборе месяцев. Сезон задаётся
# списком (сдвиг года, месяц): холодный период года y — ноябрь–декабрь y−1 и
# январь–март y. Прежние значения из других источников (реанализ Open-Meteo)
# переносятся в indicator_values_archive и удаляются.

ANNUAL_TEMPERATURE = "Среднегодовая температура воздуха"
YEARLY: dict[str, tuple[str, str, tuple[tuple[int, int], ...]]] = {
    ANNUAL_TEMPERATURE: (TEMPERATURE, "mean", tuple((0, m) for m in range(1, 13))),
    "Годовая сумма осадков": (PRECIPITATION, "sum", tuple((0, m) for m in range(1, 13))),
    "Сумма осадков за вегетационный период": (PRECIPITATION, "sum", ((0, 5), (0, 6), (0, 7), (0, 8))),
    "Сумма осадков за весенний период (апрель–май)": (PRECIPITATION, "sum", ((0, 4), (0, 5))),
    "Сумма осадков за осенний период (сентябрь–октябрь)": (PRECIPITATION, "sum", ((0, 9), (0, 10))),
    "Сумма осадков за холодный период": (PRECIPITATION, "sum", ((-1, 11), (-1, 12), (0, 1), (0, 2), (0, 3))),
}
ARCHIVE_REASON = "Замена расчётом по метеостанциям (месяцы из декад)"


def season_value(how: str, months: tuple[tuple[int, int], ...], year: int,
                 series: dict[tuple[int, int], tuple[float, str]]) -> Optional[tuple[float, str]]:
    """Значение сезона и статус из месячного ряда {(год, месяц): (значение, статус)}."""
    vals = [series.get((year + dy, m)) for dy, m in months]
    if any(v is None for v in vals):
        return None
    nums = [v[0] for v in vals]
    value = sum(nums) / len(nums) if how == "mean" else sum(nums)
    status = "approved" if all(v[1] == "approved" for v in vals) else "pending"
    return value, status


def years_from_month_keys(keys: Iterable[MonthKey]) -> set[int]:
    """Годы сезонов, которые затрагивает изменение месяцев (ноябрь–декабрь → и следующий год)."""
    out: set[int] = set()
    for _, _, m in keys:
        out.add(m.year)
        if m.month >= 11:
            out.add(m.year + 1)
    return out


async def recompute_yearly(db: AsyncSession, years: Optional[set[int]] = None) -> dict[str, int]:
    """Пересчитать годовые и сезонные показатели районов за годы (None — все годы с данными)."""
    stats = {"region_years": 0, "region_years_deleted": 0, "archived": 0}
    ids = {r.name: r.id for r in (await db.execute(
        text("SELECT id, name FROM indicators WHERE name = ANY(:n)"),
        {"n": [*YEARLY, TEMPERATURE, PRECIPITATION]})).all()}
    calc_source = await _source_id(db, CALC_SOURCE)

    series: dict[str, dict] = {}
    for src in (TEMPERATURE, PRECIPITATION):
        if src not in ids:
            continue
        by_region: dict = {}
        for r in (await db.execute(text("""
            SELECT region_id, period_start, value, validation_status FROM indicator_values
            WHERE indicator_id = :i AND period_type = 'month' AND validation_status <> 'rejected'
        """), {"i": ids[src]})).all():
            by_region.setdefault(r.region_id, {})[(r.period_start.year, r.period_start.month)] = \
                (r.value, r.validation_status)
        series[src] = by_region
    all_years = {y for s in series.values() for reg in s.values() for (y, _) in reg}
    if years is None:
        years = all_years | {y + 1 for y in all_years}
    if not years:
        return stats
    year_list = sorted(years)

    for name, (src, how, months) in YEARLY.items():
        if name not in ids or src not in series:
            continue
        ind = ids[name]
        rows = []
        for region_id, ser in series[src].items():
            for y in year_list:
                res = season_value(how, months, y, ser)
                if res:
                    rows.append({"ind": ind, "rg": region_id, "ps": date(y, 1, 1), "pe": date(y, 12, 31),
                                 "v": res[0], "status": res[1], "src": calc_source})
        params = {"i": ind, "src": calc_source, "years": year_list}
        res = await db.execute(text("""
            INSERT INTO indicator_values_archive
                (id, indicator_id, region_id, period_type, period_start, period_end, value, source_id,
                 validation_status, created_at, reason)
            SELECT id, indicator_id, region_id, period_type, period_start, period_end, value, source_id,
                   validation_status, created_at, :reason
            FROM indicator_values
            WHERE indicator_id = :i AND period_type = 'year' AND source_id IS DISTINCT FROM :src
              AND extract(year FROM period_start)::int = ANY(:years)
        """), {**params, "reason": ARCHIVE_REASON})
        stats["archived"] += res.rowcount or 0
        # Удаляются чужие значения и устаревшие расчётные (для них нет полного набора месяцев).
        keep = {(r["rg"], r["ps"]) for r in rows}
        old = (await db.execute(text("""
            SELECT id, region_id, period_start, source_id FROM indicator_values
            WHERE indicator_id = :i AND period_type = 'year'
              AND extract(year FROM period_start)::int = ANY(:years)
        """), params)).all()
        stale = [o.id for o in old if o.source_id != calc_source or (o.region_id, o.period_start) not in keep]
        if stale:
            await db.execute(text("DELETE FROM indicator_values WHERE id = ANY(:ids)"), {"ids": stale})
            stats["region_years_deleted"] += len(stale)
        if rows:
            await db.execute(text("""
                INSERT INTO indicator_values
                    (id, indicator_id, region_id, period_type, period_start, period_end, value,
                     source_id, validation_status, validated_at)
                VALUES (gen_random_uuid(), :ind, :rg, 'year', :ps, :pe, :v, :src, CAST(:status AS varchar),
                        CASE WHEN CAST(:status AS varchar) = 'approved' THEN now() END)
                ON CONFLICT ON CONSTRAINT iv_unique_value DO UPDATE SET
                    value = EXCLUDED.value, source_id = EXCLUDED.source_id,
                    validation_status = EXCLUDED.validation_status, validated_at = EXCLUDED.validated_at,
                    validated_by = NULL
            """), rows)
            stats["region_years"] += len(rows)
    return stats
