"""
Geographic context builder for the LLM. Produces a compact JSON
payload combining a *simplified* GeoJSON of the relevant administrative
units (regions of СКО) with aggregated indicator values per region for
a given direction.

The output is fed both to the local LLM (as text) and to the frontend
(for inspection / overlays). All geometry is reduced via PostGIS
`ST_Simplify` to keep the prompt under ~5 KB.
"""
from __future__ import annotations

import json
import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.climate_reference import DISTRICT_POSITION, gtk_class, render_climate_reference

logger = logging.getLogger(__name__)


# Tolerance in degrees (~0.005° ≈ 500 m at СКО latitude). Enough resolution
# for an LLM to understand district shapes without exploding token count.
DEFAULT_SIMPLIFY_TOLERANCE = 0.005

# Показатели, которые не передаются модели (шаблоны LIKE): по замечаниям эксперта
# по климату (06.10.2026) — «доли метеостанций» с непонятной методикой, испорченный
# ряд Σt, даты перехода (хранятся числом ДД.ММ) и продолжительность вегетационного
# периода по реанализу Open-Meteo, расходящаяся с данными метеостанций; площадь
# «благоприятных для богарного земледелия» — методика неизвестна (0 в Тайыншинском районе).
EXCLUDED_INDICATORS: dict[str, list[str]] = {
    "climate": [
        "Доля метеостанций%",
        "Сумма активных температур >10°C (Σt)",
        "Дата перехода%",
        "Продолжительность вегетационного периода",
        "Площадь благоприятных для богарного земледелия",
    ],
}

# Климатические нормы: среднее района за базовый период и значение последнего года.
CLIMATE_NORM_PERIOD = (2003, 2025)
CLIMATE_NORM_INDICATORS = [
    ("Гидротермический коэффициент (ГТК)", "ГТК(5-8), май–август", 2),
    ("Коэффициент увлажнения (K)", "Коэффициент увлажнения K(5-8)", 2),
    ("Сумма активных температур воздуха выше 10 °C за май–август, ΣT(5-8)",
     "Сумма активных температур выше 10 °C за май–август ΣT(5-8)", 0),
    ("Сумма осадков за вегетационный период", "Сумма осадков за май–август", 1),
    ("Годовая сумма осадков", "Годовая сумма осадков", 1),
    ("Сумма осадков за холодный период", "Сумма осадков за ноябрь–март", 1),
    ("Среднегодовая температура воздуха", "Среднегодовая температура воздуха", 1),
]


async def build_geo_context(
    db: AsyncSession,
    *,
    direction_code: str,
    region_id: UUID,
    simplify_tolerance: float = DEFAULT_SIMPLIFY_TOLERANCE,
) -> dict:
    """Returns:
        {
          "direction": {"code", "name"},
          "region": {"id", "code", "name", "level"},
          "child_regions_geojson": <FeatureCollection>,
          "aggregates": [
              {"region_id", "region_name", "indicator_id",
               "indicator_name", "unit", "avg", "min", "max", "n"}, ...
          ],
          "meta": {"simplify_tolerance", "child_count", "indicator_count"}
        }

    `region_id` should normally be the СКО oblast (level 1). The function
    pulls its direct children (level 2 districts) and computes per-district
    aggregates of approved indicator_values for the given direction.
    """
    # 1. Direction
    dir_row = (await db.execute(text(
        "SELECT id, code, name FROM directions WHERE code = :code"
    ), {"code": direction_code})).mappings().first()
    if not dir_row:
        raise ValueError(f"direction {direction_code!r} not found")

    # 2. Parent region
    parent_row = (await db.execute(text(
        "SELECT id, code, name, level FROM regions WHERE id = :rid"
    ), {"rid": str(region_id)})).mappings().first()
    if not parent_row:
        raise ValueError(f"region {region_id} not found")

    # 3. Children with simplified geometry (via spatial_data → data_layers).
    # Some deployments store admin polygons in spatial_data with layer linked
    # to a 'boundaries' category; we attempt that join, falling back to an
    # empty FeatureCollection if no spatial data is published.
    children_rows = (await db.execute(text("""
        SELECT r.id, r.code, r.name, r.level
        FROM regions r
        WHERE r.parent_id = :rid
        ORDER BY r.name
    """), {"rid": str(parent_row["id"])})).mappings().all()

    child_ids = [str(r["id"]) for r in children_rows]

    # Try to fetch a polygon for each child from spatial_data — the layer is
    # the most recent approved boundary record linked to the region.
    geojson_features = []
    if child_ids:
        sd_rows = (await db.execute(text(f"""
            SELECT DISTINCT ON (sd.region_id)
                sd.region_id,
                ST_AsGeoJSON(ST_Simplify(sd.geom, :tol)) AS geom_json
            FROM spatial_data sd
            WHERE sd.region_id = ANY(:ids)
              AND sd.geom IS NOT NULL
              AND sd.validation_status = 'approved'
            ORDER BY sd.region_id, sd.data_date DESC
        """), {"tol": simplify_tolerance, "ids": child_ids})).mappings().all()
        sd_by_region = {str(r["region_id"]): r["geom_json"] for r in sd_rows}
        for r in children_rows:
            geom_json = sd_by_region.get(str(r["id"]))
            if not geom_json:
                continue
            try:
                geometry = json.loads(geom_json)
            except (TypeError, ValueError):
                continue
            geojson_features.append({
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "region_id": str(r["id"]),
                    "region_name": r["name"],
                    "region_code": r["code"],
                },
            })

    # 4. Aggregates of approved indicator_values for this direction.
    agg_rows = (await db.execute(text("""
        SELECT
            iv.region_id,
            r.name           AS region_name,
            i.id             AS indicator_id,
            i.name           AS indicator_name,
            u.name           AS unit,
            AVG(iv.value)    AS avg_value,
            MIN(iv.value)    AS min_value,
            MAX(iv.value)    AS max_value,
            COUNT(*)         AS n_values,
            MAX(iv.period_end) AS latest_period
        FROM indicator_values iv
        JOIN indicators i ON i.id = iv.indicator_id
        LEFT JOIN units u ON u.id = i.unit_id
        JOIN regions r    ON r.id = iv.region_id
        WHERE i.direction_id = :did
          AND iv.validation_status = 'approved'
          AND iv.period_type = i.period_kind
          AND (iv.region_id = :rid OR r.parent_id = :rid)
        GROUP BY iv.region_id, r.name, i.id, i.name, u.name
        ORDER BY r.name, i.name
    """), {"did": str(dir_row["id"]), "rid": str(parent_row["id"])})).mappings().all()

    aggregates = [
        {
            "region_id": str(a["region_id"]),
            "region_name": a["region_name"],
            "indicator_id": str(a["indicator_id"]),
            "indicator_name": a["indicator_name"],
            "unit": a["unit"],
            "avg": float(a["avg_value"]) if a["avg_value"] is not None else None,
            "min": float(a["min_value"]) if a["min_value"] is not None else None,
            "max": float(a["max_value"]) if a["max_value"] is not None else None,
            "n": int(a["n_values"]),
            "latest_period": a["latest_period"].isoformat() if a["latest_period"] else None,
        }
        for a in agg_rows
    ]

    # 5. Срез «последний год»: по каждому показателю — значение области и разброс
    # по районам за последний опубликованный год. Именно его получает LLM:
    # средние/min/max «за все годы» смешивали 1990-е с текущими данными.
    latest_rows = (await db.execute(text("""
        SELECT DISTINCT ON (i.id, iv.region_id)
            i.id AS indicator_id, i.name AS indicator_name, u.name AS unit,
            iv.region_id, r.name AS region_name, r.level,
            EXTRACT(YEAR FROM iv.period_start)::int AS year, iv.value
        FROM indicator_values iv
        JOIN indicators i ON i.id = iv.indicator_id
        LEFT JOIN units u ON u.id = i.unit_id
        JOIN regions r    ON r.id = iv.region_id
        WHERE i.direction_id = :did
          AND iv.validation_status = 'approved'
          AND iv.period_type = 'year'
          AND i.name NOT LIKE '% — %'
          AND (iv.region_id = :rid OR r.parent_id = :rid)
        ORDER BY i.id, iv.region_id, iv.period_start DESC
    """), {"did": str(dir_row["id"]), "rid": str(parent_row["id"])})).mappings().all()
    excluded = EXCLUDED_INDICATORS.get(direction_code, [])
    if excluded:
        import fnmatch
        pats = [x.replace("%", "*") for x in excluded]
        latest_rows = [r for r in latest_rows
                       if not any(fnmatch.fnmatchcase(r["indicator_name"], p) for p in pats)]
    latest = _latest_by_indicator(latest_rows)
    norms = (await _climate_norms(db, str(parent_row["id"]))) if direction_code == "climate" else []

    return {
        "direction": {"code": dir_row["code"], "name": dir_row["name"]},
        "region": {
            "id": str(parent_row["id"]),
            "code": parent_row["code"],
            "name": parent_row["name"],
            "level": parent_row["level"],
        },
        "child_regions_geojson": {
            "type": "FeatureCollection",
            "features": geojson_features,
        },
        "aggregates": aggregates,
        "latest": latest,
        "norms": norms,
        "meta": {
            "simplify_tolerance": simplify_tolerance,
            "child_count": len(children_rows),
            "geojson_feature_count": len(geojson_features),
            "indicator_aggregate_count": len(aggregates),
        },
    }


async def _climate_norms(db: AsyncSession, parent_id: str) -> list[dict]:
    """Норма 2003–2025 гг. и последний год по районам; область — среднее районов."""
    names = [n for n, _, _ in CLIMATE_NORM_INDICATORS]
    rows = (await db.execute(text("""
        SELECT i.name AS indicator, u.name AS unit, r.name AS region,
               EXTRACT(YEAR FROM iv.period_start)::int AS year, iv.value
        FROM indicator_values iv
        JOIN indicators i ON i.id = iv.indicator_id
        LEFT JOIN units u ON u.id = i.unit_id
        JOIN regions r ON r.id = iv.region_id
        WHERE i.name = ANY(:names) AND iv.period_type = 'year'
          AND iv.validation_status = 'approved' AND r.parent_id = :pid
    """), {"names": names, "pid": parent_id})).mappings().all()
    out = []
    lo_y, hi_y = CLIMATE_NORM_PERIOD
    for name, label, digits in CLIMATE_NORM_INDICATORS:
        rs = [r for r in rows if r["indicator"] == name]
        if not rs:
            continue
        year = max(r["year"] for r in rs if r["year"] <= hi_y)
        by_region: dict[str, dict] = {}
        for r in rs:
            d = by_region.setdefault(r["region"], {"base": [], "last": None})
            if lo_y <= r["year"] <= hi_y:
                d["base"].append(float(r["value"]))
            if r["year"] == year:
                d["last"] = float(r["value"])
        districts = [
            {"name": k, "norm": sum(v["base"]) / len(v["base"]), "last": v["last"]}
            for k, v in by_region.items() if v["base"]
        ]
        with_last = [d for d in districts if d["last"] is not None]
        if not districts:
            continue
        out.append({
            "label": label, "unit": rs[0]["unit"], "digits": digits, "year": year,
            "oblast_norm": sum(d["norm"] for d in districts) / len(districts),
            "oblast_last": (sum(d["last"] for d in with_last) / len(with_last)) if with_last else None,
            "norm_min": min(districts, key=lambda d: d["norm"]),
            "norm_max": max(districts, key=lambda d: d["norm"]),
            "last_min": min(with_last, key=lambda d: d["last"]) if with_last else None,
            "last_max": max(with_last, key=lambda d: d["last"]) if with_last else None,
            "n_last": len(with_last),
            "indicator": name,
            "districts": sorted(districts, key=lambda d: d["norm"]),
        })
    return out


def _num(v: float, digits: int) -> str:
    return f"{v:,.{digits}f}".replace(",", " ").replace(".", ",").replace("-", "−")


def render_climate_norms(norms: list[dict]) -> str:
    lo_y, hi_y = CLIMATE_NORM_PERIOD
    lines = [f"## Климатические нормы ({lo_y}–{hi_y} гг.) и последний год, по данным метеостанций "
             "(значение района — среднее станций района; область — среднее 14 территорий)"]
    for n in norms:
        unit = f" {n['unit']}" if n["unit"] and n["unit"] != "безразмерная величина" else ""
        d = n["digits"]
        parts = [f"норма по области {_num(n['oblast_norm'], d)}{unit}"]
        if n["oblast_last"] is not None:
            diff = n["oblast_last"] - n["oblast_norm"]
            sign = "+" if diff >= 0 else "−"
            parts.append(f"{n['year']} г. — {_num(n['oblast_last'], d)}{unit} ({sign}{_num(abs(diff), d)} к норме)")
        parts.append(f"норма по районам от {_num(n['norm_min']['norm'], d)} ({n['norm_min']['name']}) "
                     f"до {_num(n['norm_max']['norm'], d)} ({n['norm_max']['name']})")
        if n["last_min"]:
            parts.append(f"{n['year']} г. по районам от {_num(n['last_min']['last'], d)} ({n['last_min']['name']}) "
                         f"до {_num(n['last_max']['last'], d)} ({n['last_max']['name']})")
        lines.append(f"- {n['label']}: " + "; ".join(parts))
    by = {n.get("indicator"): n for n in norms}
    gtk = by.get("Гидротермический коэффициент (ГТК)")
    if gtk:
        precip = {d["name"]: d for d in (by.get("Сумма осадков за вегетационный период") or {}).get("districts", [])}
        year = gtk["year"]
        lines.append("")
        lines.append(f"## Районы: положение и увлажнение по ГТК(5-8) (от самых засушливых по норме {lo_y}–{hi_y} гг.)")
        for d in gtk["districts"]:
            pos = DISTRICT_POSITION.get(d["name"])
            row = f"- {d['name']}" + (f" ({pos})" if pos else "")
            row += f": норма ГТК {_num(d['norm'], 2)} — {gtk_class(d['norm'])}"
            if d["last"] is not None:
                row += f"; {year} г. — {_num(d['last'], 2)} — {gtk_class(d['last'])}"
            p_ = precip.get(d["name"])
            if p_:
                row += f"; осадки май–август: норма {_num(p_['norm'], 1)} мм"
                if p_["last"] is not None:
                    row += f", {year} г. {_num(p_['last'], 1)} мм"
            lines.append(row)
    return "\n".join(lines)


def _latest_by_indicator(rows) -> list[dict]:
    """Группирует последние значения по показателю: область + районы за их общий
    последний год (район с более старыми данными в разброс не попадает)."""
    by_ind: dict[str, dict] = {}
    for r in rows:
        item = by_ind.setdefault(str(r["indicator_id"]), {
            "indicator_name": r["indicator_name"], "unit": r["unit"],
            "oblast": None, "districts": [],
        })
        value = float(r["value"])
        if r["level"] == 1:
            item["oblast"] = {"year": int(r["year"]), "value": value}
        else:
            item["districts"].append({"name": r["region_name"], "year": int(r["year"]), "value": value})
    out = []
    for item in by_ind.values():
        ds = item.pop("districts")
        if ds:
            year = max(d["year"] for d in ds)
            cur = [d for d in ds if d["year"] == year]
            lo = min(cur, key=lambda d: d["value"])
            hi = max(cur, key=lambda d: d["value"])
            item["districts"] = {
                "year": year, "n": len(cur),
                "min": {"name": lo["name"], "value": lo["value"]},
                "max": {"name": hi["name"], "value": hi["value"]},
            }
        else:
            item["districts"] = None
        out.append(item)
    out.sort(key=lambda x: x["indicator_name"])
    return out


def _fmt(v: float) -> str:
    return f"{v:,.2f}".rstrip("0").rstrip(".").replace(",", " ").replace(".", ",")


def render_context_as_text(ctx: dict, max_chars: int | None = None) -> str:
    """Render the context as a markdown-ish text block for LLM consumption.
    Compact, no escaping; truncated if it grows beyond `max_chars`."""
    lines: list[str] = []
    lines.append(f"# Контекст: {ctx['direction']['name']} — {ctx['region']['name']}")
    lines.append("")
    lines.append(f"Направление: {ctx['direction']['code']} ({ctx['direction']['name']})")
    lines.append(f"Регион: {ctx['region']['name']} (level {ctx['region']['level']})")
    lines.append(f"Подчинённых районов: {ctx['meta']['child_count']}")
    lines.append("")
    latest = ctx.get("latest") or []
    if latest:
        lines.append("## Показатели за последний опубликованный год (область и разброс по районам)")
        for a in latest:
            unit = f" {a['unit']}" if a["unit"] else ""
            parts = []
            if a["oblast"]:
                parts.append(f"область {_fmt(a['oblast']['value'])}{unit} ({a['oblast']['year']})")
            d = a["districts"]
            if d and d["n"] > 1:
                parts.append(
                    f"районы {d['year']}: от {_fmt(d['min']['value'])} ({d['min']['name']}) "
                    f"до {_fmt(d['max']['value'])} ({d['max']['name']}), {d['n']} районов"
                )
            if parts:
                lines.append(f"- {a['indicator_name']}: " + "; ".join(parts))
    else:
        lines.append("## Показатели: данных нет в indicator_values")
    lines.append("")
    if ctx.get("norms"):
        lines.append(render_climate_norms(ctx["norms"]))
        lines.append("")
    if ctx["direction"]["code"] == "climate":
        lines.append(render_climate_reference())
        lines.append("")
    # NB: full GeoJSON inflates the prompt to >5K tokens which is too slow on
    # CPU (>10 min). For text rendering we only mention which districts have
    # geometry — the LLM doesn't need raw coordinates to write a narrative.
    if ctx["child_regions_geojson"]["features"]:
        names = [
            f["properties"].get("region_name", "?")
            for f in ctx["child_regions_geojson"]["features"]
        ]
        lines.append(
            f"## Геометрия покрытых районов ({len(names)} полигонов): "
            + ", ".join(names)
        )
    text_out = "\n".join(lines)
    # Климатический контекст длиннее (нормы, таблица районов, справочные блоки); для прочих
    # направлений лимит прежний — иначе изменился бы их промпт и сохранённые заключения устарели.
    if max_chars is None:
        max_chars = 14000 if ctx["direction"]["code"] == "climate" else 6000
    if len(text_out) > max_chars:
        text_out = text_out[: max_chars - 30] + "\n…[усечено]"
    return text_out
