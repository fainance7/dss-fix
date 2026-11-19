# app/routers/agromap.py
"""
Эндпоинты для карты земельного фонда СКО (agromap.gishagi.kz → agromap_geo).

Подключение к БД — по тому же паттерну, что в etl/agromap_sync/sync_agromap.py
(get_db_config/get_db_conn): переменные окружения ОБЯЗАТЕЛЬНЫ, без захардкоженного
пароля-дефолта в коде — сознательно не повторяем паттерн DB_CONN из
upload_parser.py, т.к. это новый код и лишний риск утечки пароля через git
не нужен, когда его легко избежать.
"""
import os
import sys
import json
import csv
import io
from datetime import datetime
from typing import Optional
from decimal import Decimal

import psycopg2
import psycopg2.extras
import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

router = APIRouter()

REQUIRED_ENV = ["GEO_DB_HOST", "GEO_DB_PORT", "GEO_DB_NAME", "GEO_DB_USER", "GEO_DB_PASSWORD"]


def get_geo_db_conn():
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        raise HTTPException(
            500,
            f"Не заданы переменные окружения для agromap_geo: {missing}. "
            f"Проверь geo/.env и env_file контейнера fastapi.",
        )
    return psycopg2.connect(
        host=os.environ["GEO_DB_HOST"],
        port=os.environ["GEO_DB_PORT"],
        dbname=os.environ["GEO_DB_NAME"],
        user=os.environ["GEO_DB_USER"],
        password=os.environ["GEO_DB_PASSWORD"],
    )


@router.get("/meta")
def agromap_meta(region: str = "sko"):
    """
    Метаданные последней успешной синхронизации региона — для блока
    "Об источнике данных" на карте (план, раздел 4.9).
    """
    conn = get_geo_db_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT started_at, finished_at, rows_count, status
                FROM sync_log
                WHERE source_region = %s AND status = 'success'
                ORDER BY finished_at DESC
                LIMIT 1
                """,
                (region,),
            )
            last_sync = cur.fetchone()

            cur.execute(
                """
                SELECT count(*) AS total, sum(area_ha) AS total_area_ha
                FROM land_polygons
                WHERE source_region = %s AND is_active = true
                """,
                (region,),
            )
            totals = cur.fetchone()
    finally:
        conn.close()

    if last_sync is None:
        raise HTTPException(404, f"Нет успешных синхронизаций для региона '{region}'")

    return {
        "region": region,
        "last_sync_started_at": last_sync["started_at"],
        "last_sync_finished_at": last_sync["finished_at"],
        "last_sync_rows_count": last_sync["rows_count"],
        "total_active_polygons": totals["total"],
        "total_area_ha": float(totals["total_area_ha"]) if totals["total_area_ha"] else None,
        "note": (
            "Данные: РГП «ГИСХАГИ», agromap.gishagi.kz. Классификация земельного "
            "фонда — тип угодья, площадь, почва, орошение. В данных отсутствуют: "
            "сведения о посевных культурах, урожайности, землепользователях и "
            "кадастровых номерах."
        ),
    }

@router.get("/stats")
def agromap_stats(
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    region: str = "sko",
    object_ru: Optional[str] = None,
    district_id: Optional[int] = None,
    village_id: Optional[int] = None,
    area_min: Optional[float] = None,
    area_max: Optional[float] = None,
):
    """
    Статистика по текущему вьюпорту карты (план, раздел 4.6): count + сумма
    area_ha по типам угодий, с опциональным фильтром по object_ru (список
    через запятую — соответствует галочкам на панели слоёв, раздел 4.3).
    """
    conn = get_geo_db_conn()
    try:
        filters = ["source_region = %s", "is_active = true"]
        params = [region]

        # Bounding-box через && — использует GIST-индекс на geom (idx_land_polygons_geom),
        # не тянет геометрию для полного ST_Intersects — быстрее для первой прикидки
        # по вьюпорту; для точной обрезки на границе экрана этого достаточно, т.к.
        # сводка и так приблизительная по формулировке README ("в границах экрана").
        filters.append("geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)")
        params.extend([minx, miny, maxx, maxy])

        if object_ru:
            types = [t.strip() for t in object_ru.split(",") if t.strip()]
            if types:
                filters.append("object_ru = ANY(%s)")
                params.append(types)
        if district_id is not None:
            filters.append("district_id = %s")
            params.append(district_id)
        if village_id is not None:
            filters.append("village_id = %s")
            params.append(village_id)
        if area_min is not None:
            filters.append("area_ha >= %s")
            params.append(area_min)
        if area_max is not None:
            filters.append("area_ha <= %s")
            params.append(area_max)

        where_clause = " AND ".join(filters)

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    COALESCE(object_ru, '(пусто)') AS object_ru,
                    count(*) AS count,
                    sum(area_ha) AS area_ha
                FROM land_polygons
                WHERE {where_clause}
                GROUP BY object_ru
                ORDER BY area_ha DESC NULLS LAST
                """,
                params,
            )
            by_type = cur.fetchall()

            cur.execute(
                f"""
                SELECT count(*) AS total_count, sum(area_ha) AS total_area_ha
                FROM land_polygons
                WHERE {where_clause}
                """,
                params,
            )
            totals = cur.fetchone()
    finally:
        conn.close()

    return {
        "region": region,
        "bbox": {"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy},
        "filters_applied": object_ru.split(",") if object_ru else None,
        "total_count": totals["total_count"],
        "total_area_ha": float(totals["total_area_ha"]) if totals["total_area_ha"] else 0,
        "by_type": [
            {
                "object_ru": row["object_ru"],
                "count": row["count"],
                "area_ha": float(row["area_ha"]) if row["area_ha"] else 0,
            }
            for row in by_type
        ],
    }

@router.get("/polygon/{polygon_id}")
def agromap_polygon(polygon_id: int):
    """
    Атрибуты одного полигона по его внутреннему id (не source_objectid!) —
    для попапа по клику на карте (план, раздел 4.4). Пустые поля возвращаются
    как None — фронтенд показывает "—", не скрывает (раздел 4.9: "честность
    интерфейса").
    """
    conn = get_geo_db_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    id, source_objectid, source_region,
                    object_ru, object_kz,
                    ground_ru, ground_kz,
                    vegetation_ru, vegetation_kz,
                    water_ru, water_kz,
                    description_ru, description_kz,
                    code, code_esri,
                    area_ha, location_name,
                    is_active, synced_at,
                    ST_AsGeoJSON(geom) AS geometry
                FROM land_polygons
                WHERE id = %s
                """,
                (polygon_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(404, f"Полигон с id={polygon_id} не найден")

    import json
    geometry = json.loads(row.pop("geometry"))

    return {
        **row,
        "area_ha": float(row["area_ha"]) if row["area_ha"] is not None else None,
        "geometry": geometry,
    }

@router.get("/tiles/{z}/{x}/{y}.pbf")
def agromap_tile(z: int, x: int, y: int, region: str = "sko", object_ru: Optional[str] = None, district_id: Optional[int] = None, village_id: Optional[int] = None):
    """
    MVT-тайл с полигонами угодий (план, раздел 4.4). Без деления по зумам —
    один и тот же слой land_polygons на любом z; масштабирование по объёму
    данных при необходимости добавим отдельно, когда увидим реальную
    производительность на фронтенде.
    """
    conn = get_geo_db_conn()
    try:
        filters = ["source_region = %s", "is_active = true"]
        params = [region]

        if object_ru:
            types = [t.strip() for t in object_ru.split(",") if t.strip()]
            if types:
                filters.append("object_ru = ANY(%s)")
                params.append(types)

        if district_id is not None:
            filters.append("district_id = %s")
            params.append(district_id)

        if village_id is not None:
            filters.append("village_id = %s")
            params.append(village_id)

        where_clause = " AND ".join(filters)
        
        # Допуск упрощения геометрии в метрах (проекция 3857) — на широких зумах
        # огромное число детальных полигонов иначе перегружает память при сборке
        # тайла (подтверждено на практике: OOM-килл контейнера БД 23.07.2026).
        # На z=14+ упрощение практически не заметно глазу, на z<8 контуры и так
        # мелкие на экране — точность там не нужна.
        simplify_tolerance = max(1, (16 - z) * 15)

        # ST_TileEnvelope(z,x,y) — границы тайла в EPSG:3857 (стандартная веб-меркаторская схема).
        # ST_AsMVTGeom клиппит и переводит геометрию в координаты тайла (0..4096).
        # Передаём id как свойство — фронтенд использует его для popup-запроса (4.3),
        # раз мы решили маршрутизировать по внутреннему id, не по source_objectid.
        with conn.cursor() as cur:
            cur.execute(
                f"""
                WITH tile_bounds AS (
                    SELECT
                        ST_TileEnvelope(%s, %s, %s) AS geom_3857,
                        ST_Transform(ST_TileEnvelope(%s, %s, %s), 4326) AS geom_4326
                ),
                clipped AS (
                    SELECT
                        id,
                        object_ru,
                        area_ha,
			district_id,
			village_id,
                        ST_AsMVTGeom(
                            ST_Simplify(ST_Transform(lp.geom, 3857), %s),
                            tile_bounds.geom_3857,
                            4096, 64, true
                        ) AS geom
                    FROM land_polygons lp, tile_bounds
                    WHERE {where_clause}
                      AND lp.geom && tile_bounds.geom_4326
                )
                SELECT ST_AsMVT(clipped, 'land_polygons', 4096, 'geom')
                FROM clipped
                WHERE geom IS NOT NULL
                """,
                [z, x, y, z, x, y, simplify_tolerance] + params,
            )
            result = cur.fetchone()
    finally:
        conn.close()

    mvt_data = bytes(result[0]) if result and result[0] else b""
    return Response(
        content=mvt_data,
        media_type="application/vnd.mapbox-vector-tile",
    )

EXPORT_LIMIT = 300_000

RUSSIAN_HEADERS = {
    "object_ru": "Тип угодья",
    "area_ha": "Площадь (га)",
    "ground_ru": "Тип почвы",
    "vegetation_ru": "Растительность",
    "water_ru": "Орошение",
    "location_name": "Расположение",
    "code": "Код",
    "lon": "Долгота",
    "lat": "Широта",
}


def _build_export_filters(region: str, object_ru: Optional[str], bbox: Optional[str]):
    filters = ["source_region = %s", "is_active = true"]
    params = [region]

    if object_ru:
        types = [t.strip() for t in object_ru.split(",") if t.strip()]
        if types:
            filters.append("object_ru = ANY(%s)")
            params.append(types)

    if bbox:
        try:
            minx, miny, maxx, maxy = [float(v) for v in bbox.split(",")]
        except ValueError:
            raise HTTPException(400, "bbox должен быть в формате minx,miny,maxx,maxy")
        filters.append("geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)")
        params.extend([minx, miny, maxx, maxy])

    return " AND ".join(filters), params

@router.get("/export")
def agromap_export(
    format: str,
    region: str = "sko",
    object_ru: Optional[str] = None,
    bbox: Optional[str] = None,
    group_by: Optional[str] = None,
):
    """
    Экспорт выборки (план, раздел 4.8): GeoJSON / CSV / Excel.
    bbox ОПЦИОНАЛЕН — если не передан, выгружается весь регион с учётом
    остальных фильтров. Это осознанное решение: /stats и /export решают
    разные задачи (сводка по экрану vs выгрузка для внешней работы), и
    порог EXPORT_LIMIT рассчитан именно на случай выгрузки без bbox
    (иначе он почти никогда не срабатывал бы на практике).

    group_by="location" — агрегированная сводка по району/округу/типу угодья
    (кол-во участков + суммарная площадь на комбинацию), без геометрии и без
    построчной детализации по каждому участку. Доступна только для csv/xlsx.
    Не подчиняется EXPORT_LIMIT — результат агрегата всегда компактен
    (максимум несколько тысяч строк — район×округ×тип угодья, не 290К).
    """
    if format not in ("geojson", "csv", "xlsx"):
        raise HTTPException(400, "format должен быть одним из: geojson, csv, xlsx")

    if group_by == "location" and format == "geojson":
        raise HTTPException(400, "group_by=location недоступен для geojson — в агрегате нет геометрии")

    where_clause, params = _build_export_filters(region, object_ru, bbox)

    conn = get_geo_db_conn()
    try:
        if group_by == "location":
            qualified_where = where_clause.replace("source_region", "lp.source_region")
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT
                        COALESCE(d.name, '(район не определён)') AS district_name,
                        COALESCE(v.name, '(округ не определён)') AS village_name,
                        lp.object_ru,
                        count(*) AS parcel_count,
                        sum(lp.area_ha) AS area_ha
                    FROM land_polygons lp
                    LEFT JOIN agromap_districts d ON d.id = lp.district_id
                    LEFT JOIN agromap_villages v ON v.id = lp.village_id
                    WHERE {qualified_where}
                    GROUP BY d.name, v.name, lp.object_ru
                    ORDER BY d.name NULLS LAST, v.name NULLS LAST, lp.object_ru
                    """,
                    params,
                )
                rows = cur.fetchall()
            if not rows:
                raise HTTPException(404, "По заданным фильтрам ничего не найдено")
        else:
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM land_polygons WHERE {where_clause}", params)
                total = cur.fetchone()[0]

            if total > EXPORT_LIMIT:
                raise HTTPException(
                    413,
                    f"Выборка содержит {total} объектов — больше лимита {EXPORT_LIMIT}. "
                    f"Сузьте фильтр (тип угодья, область карты).",
                )
            if total == 0:
                raise HTTPException(404, "По заданным фильтрам ничего не найдено")

            geometry_select = "ST_AsGeoJSON(geom) AS geometry," if format == "geojson" else ""

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT
                        id, object_ru, object_kz, ground_ru, vegetation_ru, water_ru,
                        code, area_ha, location_name,
                        ST_X(ST_Centroid(geom)) AS lon,
                        ST_Y(ST_Centroid(geom)) AS lat,
                        {geometry_select}
                        1 AS _dummy
                    FROM land_polygons
                    WHERE {where_clause}
                    ORDER BY id
                    """,
                    params,
                )
                rows = cur.fetchall()
    finally:
        conn.close()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "_by_location" if group_by == "location" else ""

    if format == "geojson":
        features = []
        for row in rows:
            geometry = json.loads(row["geometry"])
            props = {
                k: (float(v) if isinstance(v, Decimal) else v)
                for k, v in row.items()
                if k not in ("geometry", "lon", "lat")
            }
            features.append({"type": "Feature", "geometry": geometry, "properties": props})
        body = json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False)
        return Response(
            content=body,
            media_type="application/geo+json",
            headers={"Content-Disposition": f'attachment; filename="agromap_{region}_{timestamp}.geojson"'},
        )

    if format == "csv":
        output = io.StringIO()
        output.write("\ufeff")  # BOM — чтобы Excel корректно определил UTF-8, кириллица не превратилась в кракозябры
        writer = csv.writer(output, delimiter=";")

        if group_by == "location":
            writer.writerow(["Район", "Округ", "Тип угодья", "Кол-во участков", "Площадь (га)"])
            for row in rows:
                writer.writerow([
                    row["district_name"], row["village_name"], row["object_ru"],
                    row["parcel_count"], row["area_ha"],
                ])
        else:
            cols = ["object_ru", "area_ha", "ground_ru", "vegetation_ru", "water_ru", "location_name", "code", "lon", "lat"]
            writer.writerow([RUSSIAN_HEADERS.get(c, c) for c in cols])
            for row in rows:
                writer.writerow([row[c] for c in cols])

        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="agromap_{region}{suffix}_{timestamp}.csv"'},
        )

    # xlsx
    if group_by == "location":
        df = pd.DataFrame(rows)
        df = df.rename(columns={
            "district_name": "Район",
            "village_name": "Округ",
            "object_ru": "Тип угодья",
            "parcel_count": "Кол-во участков",
            "area_ha": "Площадь (га)",
        })
    else:
        df = pd.DataFrame(rows)
        if "geometry" in df.columns:
            df = df.drop(columns=["geometry"])
        if "_dummy" in df.columns:
            df = df.drop(columns=["_dummy"])
        df = df.rename(columns=RUSSIAN_HEADERS)

    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, engine="openpyxl")
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="agromap_{region}{suffix}_{timestamp}.xlsx"'},
    )

@router.get("/locations")
def agromap_locations(region: str = "sko"):
    """
    Справочник район/округ для фильтра (план, раздел 5.9). Читает из
    agromap_districts/agromap_villages — таблиц с постоянными числовыми ID,
    используемыми и в MVT-тайлах (district_id), и здесь, чтобы фронтенд мог
    фильтровать саму карту через map.setFilter, а не только сводку.
    """
    conn = get_geo_db_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT d.id AS district_id, d.name AS district_name,
                       v.id AS village_id, v.name AS village_name
                FROM agromap_districts d
                LEFT JOIN agromap_villages v ON v.district_id = d.id
                WHERE d.source_region = %s
                ORDER BY d.name, v.name
                """,
                (region,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    districts = {}
    for row in rows:
        d_id = row["district_id"]
        if d_id not in districts:
            districts[d_id] = {"id": d_id, "name": row["district_name"], "villages": []}
        if row["village_id"] is not None:
            districts[d_id]["villages"].append({"id": row["village_id"], "name": row["village_name"]})

    return {
        "region": region,
        "districts": sorted(districts.values(), key=lambda d: d["name"]),
    }
