"""Помесячные климатические данные по метеостанциям (из декад) для дашбордов.

Месяцы считает app/services/climate_dekads.py; здесь — чтение:
- станция: месячные значения станции + декады, из которых они получены;
- район: расчётный месяц района (среднее станций района; для района без станций —
  ближайшая станция);
- область: среднее месячных значений всех станций.
К каждому месяцу — многолетнее среднее того же месяца за базовый период 2003–2025 гг.
(норма, выбранный год входит в период) и отклонение от неё.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path
from statistics import mean
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.rbac import require_roles
from app.database import get_db
from app.deps import get_current_active_user
from app.models.user import User
from app.services import climate_dekads as cd

router = APIRouter(prefix="/climate", tags=["climate"])

INDICATORS = {"temperature": cd.TEMPERATURE, "precipitation": cd.PRECIPITATION}
# Базовый период климатической нормы дашбордов (решение заказчика).
NORM_PERIOD = (2003, 2025)
REFERENCE_FILE = Path(__file__).resolve().parents[2] / "data" / "climate_reference.json"


async def _indicator(db: AsyncSession, key: str) -> dict:
    name = INDICATORS.get(key)
    if name is None:
        raise HTTPException(status_code=422, detail="indicator: temperature или precipitation")
    row = (await db.execute(text("""
        SELECT i.id, i.name, u.name AS unit FROM indicators i LEFT JOIN units u ON u.id = i.unit_id
        WHERE i.name = :n ORDER BY i.created_at LIMIT 1
    """), {"n": name})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Показатель «{name}» не найден")
    return {"id": row["id"], "name": row["name"], "unit": row["unit"], "key": key,
            "aggregation": cd.AGGREGATION[name]}


def _status_clause(only_approved: bool, alias: str) -> str:
    return f"AND {alias}.validation_status = 'approved'" if only_approved else \
           f"AND {alias}.validation_status <> 'rejected'"


@router.get("/monthly")
async def monthly(
    indicator: Literal["temperature", "precipitation"] = Query(...),
    station_id: Optional[UUID] = Query(None),
    region_id: Optional[UUID] = Query(None),
    year: Optional[int] = Query(None, description="По умолчанию — последний год с данными"),
    base_from: Optional[int] = Query(None, description="Начало периода нормы"),
    base_to: Optional[int] = Query(None, description="Конец периода нормы"),
    only_approved: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    """12 месяцев года по станции, району или области (без station_id и region_id)."""
    if station_id and region_id:
        raise HTTPException(status_code=422, detail="Укажите станцию или район, не оба")
    ind = await _indicator(db, indicator)
    params: dict = {"ind": ind["id"]}

    scope: dict
    if station_id:
        st = (await db.execute(text("""
            SELECT s.id, s.name, r.name AS region FROM meteo_stations s JOIN regions r ON r.id = s.region_id
            WHERE s.id = :sid
        """), {"sid": station_id})).mappings().first()
        if st is None:
            raise HTTPException(status_code=404, detail="Станция не найдена")
        scope = {"type": "station", "id": str(st["id"]), "name": st["name"], "region": st["region"],
                 "basis": "station", "stations": [st["name"]]}
        params["sid"] = station_id
        series_sql = f"""
            SELECT v.period_start AS ps, v.value, v.validation_status AS status, 1 AS n
            FROM meteo_station_values v
            WHERE v.indicator_id = :ind AND v.station_id = :sid AND v.period_type = 'month'
              {_status_clause(only_approved, 'v')}
        """
    elif region_id:
        rg = (await db.execute(text("SELECT id, name FROM regions WHERE id = :rid"), {"rid": region_id})).mappings().first()
        if rg is None:
            raise HTTPException(status_code=404, detail="Район не найден")
        mapped = (await db.execute(text("""
            SELECT s.name, m.mode FROM climate_region_station_map m JOIN meteo_stations s ON s.id = m.station_id
            WHERE m.region_id = :rid ORDER BY s.name
        """), {"rid": region_id})).mappings().all()
        basis = "nearest_station" if mapped and all(m["mode"] == "nearest" for m in mapped) else "district_mean"
        scope = {"type": "region", "id": str(rg["id"]), "name": rg["name"], "basis": basis,
                 "stations": [m["name"] for m in mapped]}
        params["rid"] = region_id
        series_sql = f"""
            SELECT iv.period_start AS ps, iv.value, iv.validation_status AS status,
                   (SELECT count(*) FROM climate_region_station_map m
                      JOIN meteo_station_values v ON v.station_id = m.station_id AND v.indicator_id = iv.indicator_id
                       AND v.period_type = 'month' AND v.period_start = iv.period_start
                       AND v.validation_status <> 'rejected'
                     WHERE m.region_id = iv.region_id) AS n
            FROM indicator_values iv
            WHERE iv.indicator_id = :ind AND iv.region_id = :rid AND iv.period_type = 'month'
              {_status_clause(only_approved, 'iv')}
        """
    else:
        scope = {"type": "oblast", "id": None, "name": "Северо-Казахстанская область",
                 "basis": "stations_mean", "stations": []}
        series_sql = f"""
            SELECT v.period_start AS ps, avg(v.value) AS value,
                   CASE WHEN bool_and(v.validation_status = 'approved') THEN 'approved' ELSE 'pending' END AS status,
                   count(*) AS n
            FROM meteo_station_values v
            WHERE v.indicator_id = :ind AND v.period_type = 'month'
              {_status_clause(only_approved, 'v')}
            GROUP BY v.period_start
        """

    rows = (await db.execute(text(series_sql), params)).mappings().all()
    by_ym = {(r["ps"].year, r["ps"].month): r for r in rows}
    years = sorted({y for y, _ in by_ym})
    if not years:
        return {"indicator": ind | {"id": str(ind["id"])}, "scope": scope, "year": year,
                "years": [], "months": [], "base_period": None}
    sel = year if year is not None else years[-1]
    b_from = base_from or NORM_PERIOD[0]
    b_to = base_to or NORM_PERIOD[1]

    clim: dict[int, list[float]] = defaultdict(list)
    for (y, m), r in by_ym.items():
        if b_from <= y <= b_to:
            clim[m].append(r["value"])

    dekads: dict[int, list[dict]] = defaultdict(list)
    if station_id:
        for d in (await db.execute(text(f"""
            SELECT v.period_start, v.dekad, v.value, v.validation_status, v.derivation
            FROM meteo_station_values v
            WHERE v.indicator_id = :ind AND v.station_id = :sid AND v.period_type = 'dekad'
              AND v.period_start BETWEEN :d0 AND :d1
            ORDER BY v.period_start
        """), {**params, "d0": date(sel, 1, 1), "d1": date(sel, 12, 31)})).mappings().all():
            dekads[d["period_start"].month].append(
                {"dekad": d["dekad"], "value": d["value"], "status": d["validation_status"],
                 "derivation": d["derivation"]})

    # Декады, восполненные по соседней станции (пометка из файла заказчика), — по месяцам
    sub_cond = "v.station_id = :sid" if station_id else (
        "v.station_id IN (SELECT station_id FROM climate_region_station_map WHERE region_id = :rid)"
        if region_id else "TRUE")
    substitutes: dict[int, list[str]] = defaultdict(list)
    for r in (await db.execute(text(f"""
        SELECT extract(month FROM v.period_start)::int AS m, s.name, v.derivation, count(*) AS n
        FROM meteo_station_values v JOIN meteo_stations s ON s.id = v.station_id
        WHERE v.indicator_id = :ind AND v.period_type = 'dekad' AND v.derivation IS NOT NULL
          AND v.period_start BETWEEN :d0 AND :d1 AND {sub_cond}
        GROUP BY 1, 2, 3 ORDER BY 2
    """), {**params, "d0": date(sel, 1, 1), "d1": date(sel, 12, 31)})).mappings().all():
        substitutes[r["m"]].append(f"{r['name']} — {r['derivation']} ({r['n']} дек.)")

    months = []
    for m in range(1, 13):
        r = by_ym.get((sel, m))
        norm = mean(clim[m]) if clim[m] else None
        value = r["value"] if r else None
        item = {
            "month": m,
            "value": value,
            "status": r["status"] if r else None,
            "n_stations": int(r["n"]) if r and r["n"] is not None else 0,
            "norm": norm,
            "norm_years": len(clim[m]),
            "anomaly": (value - norm) if value is not None and norm is not None else None,
            "substitutes": substitutes.get(m, []),
        }
        if station_id:
            item["dekads"] = dekads.get(m, [])
            item["complete"] = r is not None
        months.append(item)

    return {
        "indicator": {**ind, "id": str(ind["id"])},
        "scope": scope,
        "year": sel,
        "years": years,
        "base_period": [b_from, b_to],
        "months": months,
    }


@lru_cache(maxsize=1)
def _reference() -> dict:
    return json.loads(REFERENCE_FILE.read_text(encoding="utf-8"))


@router.get("/reference")
async def reference(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    """Справочные многолетние таблицы заказчика (даты перехода, ΣT>5/10/15, обеспеченность P%)
    с идентификаторами районов для подсветки на дашборде."""
    data = _reference()
    names = {r for t in ("transitions", "active_sums", "probability") for r in
             (row["region"] for row in data[t]["rows"])}
    ids = {r.name: str(r.id) for r in (await db.execute(
        text("SELECT id, name FROM regions WHERE name = ANY(:n)"), {"n": list(names)})).all()}
    out = {**data}
    for t in ("transitions", "active_sums", "probability"):
        out[t] = {**data[t], "rows": [{**row, "region_id": ids.get(row["region"])} for row in data[t]["rows"]]}
    return out


@router.get("/stations")
async def stations(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    """Станции и районы для переключателей дашборда."""
    st = (await db.execute(text("""
        SELECT s.id, s.name, s.lat, s.lon, r.id AS region_id, r.name AS region
        FROM meteo_stations s JOIN regions r ON r.id = s.region_id
        WHERE s.is_active ORDER BY s.name
    """))).mappings().all()
    rg = (await db.execute(text("""
        SELECT r.id, r.name, bool_and(m.mode = 'nearest') AS nearest_only,
               array_agg(s.name ORDER BY s.name) AS stations
        FROM climate_region_station_map m JOIN regions r ON r.id = m.region_id
        JOIN meteo_stations s ON s.id = m.station_id
        GROUP BY r.id, r.name ORDER BY r.name
    """))).mappings().all()
    return {
        "stations": [{**dict(s), "id": str(s["id"]), "region_id": str(s["region_id"])} for s in st],
        "regions": [{"id": str(r["id"]), "name": r["name"], "nearest_only": r["nearest_only"],
                     "stations": list(r["stations"])} for r in rg],
    }


@router.get("/station-values")
async def station_values(
    indicator: Optional[Literal["temperature", "precipitation"]] = Query(None),
    station_id: Optional[UUID] = Query(None),
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None, ge=1, le=12),
    status: Optional[Literal["pending", "approved", "rejected"]] = Query(None),
    period_type: Literal["dekad", "month"] = Query("dekad"),
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader")),
):
    """Значения метеостанций для проверки экспертом (декады или расчётные месяцы)."""
    cond = ["v.period_type = :pt"]
    params: dict = {"pt": period_type}
    if indicator:
        cond.append("i.name = :iname")
        params["iname"] = INDICATORS[indicator]
    if station_id:
        cond.append("v.station_id = :sid")
        params["sid"] = station_id
    if year:
        cond.append("extract(year FROM v.period_start) = :y")
        params["y"] = year
    if month:
        cond.append("extract(month FROM v.period_start) = :mo")
        params["mo"] = month
    if status:
        cond.append("v.validation_status = :st")
        params["st"] = status
    where = " AND ".join(cond)
    total = (await db.execute(text(f"""
        SELECT count(*) FROM meteo_station_values v JOIN indicators i ON i.id = v.indicator_id WHERE {where}
    """), params)).scalar_one()
    rows = (await db.execute(text(f"""
        SELECT v.id, v.period_type, v.period_start, v.period_end, v.dekad, v.value, v.validation_status,
               v.is_derived, v.validated_at, v.job_id, s.name AS station, r.name AS region,
               i.name AS indicator, u.name AS unit, j.source_ref
        FROM meteo_station_values v
        JOIN indicators i ON i.id = v.indicator_id
        LEFT JOIN units u ON u.id = i.unit_id
        JOIN meteo_stations s ON s.id = v.station_id
        JOIN regions r ON r.id = s.region_id
        LEFT JOIN dataset_intake_jobs j ON j.id = v.job_id
        WHERE {where}
        ORDER BY v.period_start DESC, s.name, i.name
        LIMIT :lim OFFSET :off
    """), {**params, "lim": page_size, "off": (page - 1) * page_size})).mappings().all()
    return {
        "items": [{**dict(r), "id": str(r["id"]), "job_id": str(r["job_id"]) if r["job_id"] else None} for r in rows],
        "total": total, "page": page, "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
    }


class StationValidateRequest(BaseModel):
    ids: list[UUID] = Field(..., min_length=1, max_length=10000)
    status: Literal["approved", "rejected", "pending"]


@router.post("/station-values/validate-bulk")
async def validate_station_values(
    body: StationValidateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles("admin", "exp_ver")),
):
    """Утверждение/отклонение декад станций; месяцы станций и районов пересчитываются."""
    rows = (await db.execute(text("""
        UPDATE meteo_station_values
        SET validation_status = :st, validated_by = :uid,
            validated_at = CASE WHEN :st = 'pending' THEN NULL ELSE now() END
        WHERE id = ANY(:ids) AND period_type = 'dekad'
        RETURNING station_id, indicator_id, period_start
    """), {"st": body.status, "uid": user.id, "ids": body.ids})).all()
    stats = await cd.recompute(db, cd.month_keys_from_values(rows)) if rows else {}
    await log_action(db, user_id=user.id, action="validate", entity_type="meteo_station_values",
                     entity_id=None, details={"status": body.status, "updated": len(rows), **stats})
    return {"updated": len(rows), "not_found": len(body.ids) - len(rows), "recompute": stats}


@router.post("/recompute")
async def recompute_all(
    year_from: Optional[int] = Query(None),
    year_to: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles("admin")),
):
    """Полный пересчёт месяцев станций и районов из декад."""
    stats = await cd.recompute_all(db, year_from, year_to)
    await log_action(db, user_id=user.id, action="climate_recompute", entity_type="climate",
                     entity_id=None, details={"year_from": year_from, "year_to": year_to, **stats})
    return stats
