#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_agromap.py — синк агрополигонов agromap.gishagi.kz в PostGIS (agromap_geo).

Запуск (внутри aldispetro-airflow-worker-1):
    python3 /opt/airflow/etl/agromap_sync/sync_agromap.py --region sko

Окружение (из geo/.env, уже подключён к airflow-worker):
    GEO_DB_HOST, GEO_DB_PORT, GEO_DB_NAME, GEO_DB_USER, GEO_DB_PASSWORD

Логика (см. agromap_etap3_kontekst.md, разделы 3.1-3.6 + решение по staging):
  1. Пагинация по источнику -> нормализация -> батчевая вставка ТОЛЬКО в land_polygons_staging
     (staging — per-region буфер; land_polygons не трогается во время выгрузки).
  2. Только после успешной выгрузки ВСЕГО региона (все страницы пройдены):
     одна транзакция: upsert staging->land_polygons, soft-delete пропавших (is_active=false),
     очистка staging для этого региона.
  3. Если скрипт обрывается на любом шаге пагинации — land_polygons для региона не тронута.
  4. sync_log — статус прогона. sync_errors — отдельные битые полигоны (не валят весь батч).
"""

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
import requests

# ------------------------------------------------------------------
# Конфигурация по регионам (заполняется по факту разведки, Этап 1)
# ------------------------------------------------------------------
REGION_REGISTRY = {
    "sko": {
        # Кириллическая "е" в agricultur*е*_polygon — скопировано из каталога API,
        # НЕ набирать руками (README, раздел "Грабли").
        "service_path": "sko/sko_qtrf_agriculturе_polygon_mil",
        "server_type": "MapServer",
        "layer_id": 0,
        "expected_count": 290374,  # Этап 1, для информационной сверки
    },
    # Другие 17 регионов — добавлять сюда по мере тестирования.
    # Layer id НЕ хардкодить как 0 по умолчанию — проверять через ?f=json
    # (README: Туркестан=28, Жетісу=2).
}

BASE_URL = "https://agromap.gishagi.kz/server/rest/services"
PAGE_SIZE = 2000          # maxRecordCount источника
REQUEST_PAUSE_SEC = 0.4   # 300-500 мс между запросами, госсервер
BATCH_SIZE = 750          # multi-row insert batch

# Поля, которые ожидаемо "служебные"/внутренние у ArcGIS и не должны считаться
# "непокрытым" неизвестным полем при логировании (см. ниже).
KNOWN_INTERNAL_KEYS = {
    "OBJECTID", "OBJECTID_1", "objectid", "FID",
    "Shape__Area", "Shape__Length", "SHAPE__Area", "SHAPE__Length",
    "shape_Area", "shape_Length",  # тот же смысл, другое соглашение об именовании
                                   # у sko_qtrf_agriculturе_polygon_mil (см. README:
                                   # имена полей плавают между сервисами agromap)
    "GlobalID", "globalid",
}

KNOWN_UNMAPPED_KEYS = {
    "description",
}

# Канонические поля в БД -> варианты имён ключей в properties источника.
# Порядок внутри списка = приоритет (первое найденное непустое значение берётся).
FIELD_ALIASES = {
    "object_ru":       ["object_ru", "object1"],
    "object_kz":       ["object_kz", "object2"],
    "ground_ru":       ["ground_ru", "ground1"],
    "ground_kz":       ["ground_kz", "ground2"],
    "vegetation_ru":   ["vegetation_ru", "veg_ru"],
    "vegetation_kz":   ["vegetation_kz", "veg_kz"],
    "water_ru":        ["water_ru"],
    "water_kz":        ["water_kz"],
    "description_ru":  ["description_ru", "descr_ru"],
    "description_kz":  ["description_kz", "descr_kz"],
    "code":            ["code"],
    "code_esri":       ["code_esri", "esri_code"],
    "area_ha":         ["area_ha", "area_he"],  # приоритет area_ha, COALESCE-логика
    "location_name":   ["name_ru", "name1", "name"],
}


def get_db_config():
    required = ["GEO_DB_HOST", "GEO_DB_PORT", "GEO_DB_NAME", "GEO_DB_USER", "GEO_DB_PASSWORD"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"FATAL: отсутствуют переменные окружения: {missing}", file=sys.stderr)
        sys.exit(1)
    return {
        "host": os.environ["GEO_DB_HOST"],
        "port": os.environ["GEO_DB_PORT"],
        "dbname": os.environ["GEO_DB_NAME"],
        "user": os.environ["GEO_DB_USER"],
        "password": os.environ["GEO_DB_PASSWORD"],
    }


def get_db_conn():
    return psycopg2.connect(**get_db_config())


# ------------------------------------------------------------------
# Работа с источником (agromap REST API)
# ------------------------------------------------------------------

def fetch_page(service_path, server_type, layer_id, offset):
    """Один запрос страницы GeoJSON. Возвращает (features, exceeded_transfer_limit)."""
    url = f"{BASE_URL}/{service_path}/{server_type}/{layer_id}/query"
    params = {
        "where": "1=1",
        "outFields": "*",
        "outSR": 4326,
        "f": "geojson",
        "orderByFields": "objectid",
        "resultOffset": offset,
        "resultRecordCount": PAGE_SIZE,
    }
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Источник вернул ошибку: {data['error']}")
    features = data.get("features", [])
    # ArcGIS для geojson кладёт признак пагинации либо в properties последней страницы,
    # либо отдельно — проверяем оба варианта, надёжнее ориентироваться на len(features) < PAGE_SIZE.
    exceeded = len(features) >= PAGE_SIZE
    return features, exceeded


def fetch_all_features(service_path, server_type, layer_id, region_label):
    """Генератор: отдаёт фичи страницами, с паузой между запросами."""
    offset = 0
    total = 0
    while True:
        features, exceeded = fetch_page(service_path, server_type, layer_id, offset)
        if not features:
            break
        total += len(features)
        print(f"[{region_label}] страница offset={offset}: {len(features)} объектов (всего забрано {total})")
        yield features
        if not exceeded:
            break
        offset += PAGE_SIZE
        time.sleep(REQUEST_PAUSE_SEC)


def fetch_source_stats(service_path, server_type, layer_id, group_field="object_ru"):
    """Серверная агрегация источника: count + sum(area_ha) по группам, для сверки после синка."""
    url = f"{BASE_URL}/{service_path}/{server_type}/{layer_id}/query"
    out_stats = json.dumps([
        {"statisticType": "count", "onStatisticField": "OBJECTID_1", "outStatisticFieldName": "cnt"},
        {"statisticType": "sum", "onStatisticField": "area_ha", "outStatisticFieldName": "ga"},
    ])
    params = {
        "where": "1=1",
        "groupByFieldsForStatistics": group_field,
        "outStatistics": out_stats,
        "f": "json",
    }
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data.get("features", [])


# ------------------------------------------------------------------
# Нормализация properties
# ------------------------------------------------------------------

def normalize_value(v):
    """"" -> None, обрезка пробелов у строк (README: грязные значения object_ru)."""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        return v if v != "" else None
    return v


def map_properties(props, seen_uncovered_keys):
    """Возвращает (typed_fields: dict, raw_attributes: dict)."""
    normalized = {k: normalize_value(v) for k, v in props.items()}
    typed = {}
    consumed_keys = set()
    for canonical, variants in FIELD_ALIASES.items():
        for key in variants:
            if key in normalized:
                # Ключ считается "покрытым" (не уходит в лог uncovered-key), даже
                # если значение None — это распознанное поле, просто пустое в этой
                # записи (ожидаемо: README фиксирует, что ground_ru/water_ru и т.п.
                # заполнены лишь в 10-50% объектов). Раньше None-значение не
                # добавлялось в consumed_keys, и такие поля ложно попадали в
                # uncovered-key на каждой строке, где они просто пустые.
                consumed_keys.add(key)
                if normalized[key] is not None:
                    typed[canonical] = normalized[key]
                    break
    raw = {k: v for k, v in normalized.items() if k not in consumed_keys}

    # Логируем непокрытые ключи (кроме известных служебных) — один раз на процесс,
    # чтобы явно увидеть на первом прогоне региона, не потерялось ли что-то важное.
    for k in raw.keys():
        if k not in KNOWN_INTERNAL_KEYS and k not in KNOWN_UNMAPPED_KEYS and k not in seen_uncovered_keys:
            seen_uncovered_keys.add(k)
            print(f"  [uncovered-key] '{k}' встретился в properties и ушёл в raw_attributes "
                  f"(пример значения: {raw[k]!r})")

    return typed, raw


def feature_to_row(feature, source_region, source_layer, seen_uncovered_keys):
    """Готовит tuple для вставки. Может бросить исключение — тогда объект идёт на fallback."""
    props = feature.get("properties", {}) or {}
    geometry = feature.get("geometry")
    if geometry is None:
        raise ValueError("geometry отсутствует у фичи")

    objectid_raw = (
        props.get("OBJECTID_1")
        or props.get("OBJECTID")
        or props.get("objectid")
    )
    if objectid_raw is None:
        raise ValueError("ни OBJECTID_1, ни objectid не найдены в properties")
    source_objectid = int(objectid_raw)

    typed, raw = map_properties(props, seen_uncovered_keys)

    area_ha = typed.get("area_ha")
    if area_ha is not None:
        try:
            area_ha = float(area_ha)
        except (TypeError, ValueError):
            area_ha = None

    row = (
        json.dumps(geometry),                      # -> ST_GeomFromGeoJSON
        source_objectid,
        source_region,
        source_layer,
        typed.get("object_ru"),
        typed.get("object_kz"),
        typed.get("ground_ru"),
        typed.get("ground_kz"),
        typed.get("vegetation_ru"),
        typed.get("vegetation_kz"),
        typed.get("water_ru"),
        typed.get("water_kz"),
        typed.get("description_ru"),
        typed.get("description_kz"),
        typed.get("code"),
        typed.get("code_esri"),
        area_ha,
        typed.get("location_name"),
        json.dumps(raw, ensure_ascii=False),
    )
    return row, feature


# ------------------------------------------------------------------
# Вставка в staging
# ------------------------------------------------------------------

STAGING_INSERT_TEMPLATE = """(
    ST_Multi(
        ST_CollectionExtract(
            ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)),
            3
        )
    ),
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
)"""

STAGING_INSERT_SQL = """
INSERT INTO land_polygons_staging (
    geom, source_objectid, source_region, source_layer,
    object_ru, object_kz, ground_ru, ground_kz,
    vegetation_ru, vegetation_kz, water_ru, water_kz,
    description_ru, description_kz, code, code_esri,
    area_ha, location_name, raw_attributes
) VALUES %s
ON CONFLICT (source_region, source_objectid) DO UPDATE SET
    geom = EXCLUDED.geom,
    source_layer = EXCLUDED.source_layer,
    object_ru = EXCLUDED.object_ru,
    object_kz = EXCLUDED.object_kz,
    ground_ru = EXCLUDED.ground_ru,
    ground_kz = EXCLUDED.ground_kz,
    vegetation_ru = EXCLUDED.vegetation_ru,
    vegetation_kz = EXCLUDED.vegetation_kz,
    water_ru = EXCLUDED.water_ru,
    water_kz = EXCLUDED.water_kz,
    description_ru = EXCLUDED.description_ru,
    description_kz = EXCLUDED.description_kz,
    code = EXCLUDED.code,
    code_esri = EXCLUDED.code_esri,
    area_ha = EXCLUDED.area_ha,
    location_name = EXCLUDED.location_name,
    raw_attributes = EXCLUDED.raw_attributes,
    synced_at = now()
"""


def insert_error(conn, source_region, source_objectid, error_message, raw_geojson):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO sync_errors (source_region, source_objectid, error_message, raw_geojson)
               VALUES (%s, %s, %s, %s::jsonb)""",
            (source_region, source_objectid, error_message,
             json.dumps(raw_geojson, ensure_ascii=False) if raw_geojson is not None else None),
        )
    conn.commit()


def insert_batch_to_staging(conn, rows_with_features, source_region):
    """Пытается вставить батч целиком; при сбое — построчный fallback с логированием в sync_errors."""
    rows = [r for r, _ in rows_with_features]
    try:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur, STAGING_INSERT_SQL, rows, template=STAGING_INSERT_TEMPLATE, page_size=len(rows)
            )
        conn.commit()
        return len(rows), 0
    except psycopg2.Error as e:
        conn.rollback()
        print(f"  [batch-fail] батч из {len(rows)} упал целиком ({e.__class__.__name__}), "
              f"переходим на построчную вставку для изоляции сбойных объектов")
        ok_count = 0
        fail_count = 0
        for row, feature in rows_with_features:
            try:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(
                        cur, STAGING_INSERT_SQL, [row], template=STAGING_INSERT_TEMPLATE, page_size=1
                    )
                conn.commit()
                ok_count += 1
            except psycopg2.Error as row_err:
                conn.rollback()
                source_objectid = row[1]
                insert_error(conn, source_region, source_objectid, str(row_err), feature)
                fail_count += 1
        return ok_count, fail_count


# ------------------------------------------------------------------
# Перенос staging -> land_polygons (одна транзакция, только после полной выгрузки региона)
# ------------------------------------------------------------------

TRANSFER_SQL = """
WITH normalized AS (
    -- Города-акиматы с историческим/созвучным несовпадением района: Булаево
    -- относится к р-ну Магжана Жумабаева (не к Мамлютскому, несмотря на
    -- созвучие имён), Мамлютка — к Мамлютскому. Подтверждено внешним поиском
    -- 22.07.2026 (агрегированный kato_catalog в reports_db НЕ содержит
    -- подрайонного уровня и не может быть источником истины для этого).
    SELECT
        id AS staging_id,
        replace(
            replace(location_name, 'город Булаево', 'р-н Магжана Жумабаева'),
            'город Мамлютка', 'Мамлютский р-н'
        ) AS location_name_fixed
    FROM land_polygons_staging
    WHERE source_region = %s
),
parsed AS (
    SELECT
        n.staging_id,
        trim((string_to_array(n.location_name_fixed, ','))[2]) AS district_name,
        trim((string_to_array(n.location_name_fixed, ','))[3]) AS village_name
    FROM normalized n
    WHERE n.location_name_fixed IS NOT NULL
      AND array_length(string_to_array(n.location_name_fixed, ','), 1) = 3
),
resolved AS (
    SELECT p.staging_id, d.id AS district_id, v.id AS village_id
    FROM parsed p
    JOIN agromap_districts d ON d.source_region = %s AND d.name = p.district_name
    JOIN agromap_villages v ON v.district_id = d.id AND v.name = p.village_name
)
INSERT INTO land_polygons (
    geom, source_objectid, source_region, source_layer,
    object_ru, object_kz, ground_ru, ground_kz,
    vegetation_ru, vegetation_kz, water_ru, water_kz,
    description_ru, description_kz, code, code_esri,
    area_ha, location_name, raw_attributes,
    district_id, village_id
)
SELECT
    s.geom, s.source_objectid, s.source_region, s.source_layer,
    s.object_ru, s.object_kz, s.ground_ru, s.ground_kz,
    s.vegetation_ru, s.vegetation_kz, s.water_ru, s.water_kz,
    s.description_ru, s.description_kz, s.code, s.code_esri,
    s.area_ha, s.location_name, s.raw_attributes,
    r.district_id, r.village_id
FROM land_polygons_staging s
LEFT JOIN resolved r ON r.staging_id = s.id
WHERE s.source_region = %s
ON CONFLICT (source_region, source_objectid) DO UPDATE SET
    geom = EXCLUDED.geom,
    source_layer = EXCLUDED.source_layer,
    object_ru = EXCLUDED.object_ru,
    object_kz = EXCLUDED.object_kz,
    ground_ru = EXCLUDED.ground_ru,
    ground_kz = EXCLUDED.ground_kz,
    vegetation_ru = EXCLUDED.vegetation_ru,
    vegetation_kz = EXCLUDED.vegetation_kz,
    water_ru = EXCLUDED.water_ru,
    water_kz = EXCLUDED.water_kz,
    description_ru = EXCLUDED.description_ru,
    description_kz = EXCLUDED.description_kz,
    code = EXCLUDED.code,
    code_esri = EXCLUDED.code_esri,
    area_ha = EXCLUDED.area_ha,
    location_name = EXCLUDED.location_name,
    raw_attributes = EXCLUDED.raw_attributes,
    district_id = EXCLUDED.district_id,
    village_id = EXCLUDED.village_id,
    synced_at = now(),
    is_active = true,
    retired_at = NULL
"""

SOFT_DELETE_SQL = """
UPDATE land_polygons lp
SET is_active = false, retired_at = now()
WHERE lp.source_region = %s
  AND lp.is_active = true
  AND NOT EXISTS (
      SELECT 1 FROM land_polygons_staging s
      WHERE s.source_region = %s AND s.source_objectid = lp.source_objectid
  )
"""

CLEANUP_STAGING_SQL = "DELETE FROM land_polygons_staging WHERE source_region = %s"
CLEANUP_SYNC_ERRORS_SQL = "DELETE FROM sync_errors WHERE source_region = %s"


def transfer_region_to_production(conn, source_region):
    """Одна транзакция: upsert staging->prod, soft-delete пропавших, очистка staging."""
    with conn.cursor() as cur:
        cur.execute(TRANSFER_SQL, (source_region, source_region, source_region))
        transferred = cur.rowcount
        cur.execute(SOFT_DELETE_SQL, (source_region, source_region))
        retired = cur.rowcount
        cur.execute(CLEANUP_STAGING_SQL, (source_region,))
    conn.commit()
    return transferred, retired


# ------------------------------------------------------------------
# sync_log
# ------------------------------------------------------------------

def sync_log_start(conn, source_region):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sync_log (source_region, started_at, status) VALUES (%s, %s, 'running') RETURNING id",
            (source_region, datetime.now(timezone.utc)),
        )
        log_id = cur.fetchone()[0]
    conn.commit()
    return log_id


def sync_log_finish(conn, log_id, status, rows_count, error_message=None):
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE sync_log SET finished_at = %s, status = %s, rows_count = %s, error_message = %s
               WHERE id = %s""",
            (datetime.now(timezone.utc), status, rows_count, error_message, log_id),
        )
    conn.commit()


# ------------------------------------------------------------------
# Основной прогон одного региона
# ------------------------------------------------------------------

def sync_region(region):
    if region not in REGION_REGISTRY:
        print(f"FATAL: регион '{region}' не найден в REGION_REGISTRY. "
              f"Сначала разведать service_path/layer_id через ?f=json (README, раздел 2) "
              f"и добавить запись в реестр.", file=sys.stderr)
        sys.exit(1)

    cfg = REGION_REGISTRY[region]
    service_path = cfg["service_path"]
    server_type = cfg["server_type"]
    layer_id = cfg["layer_id"]
    source_layer_full = f"{service_path}/{server_type}/{layer_id}"

    conn = get_db_conn()
    log_id = sync_log_start(conn, region)
    print(f"=== Синк региона '{region}' начат (sync_log id={log_id}) ===")
    print(f"    источник: {BASE_URL}/{source_layer_full}/query")

    seen_uncovered_keys = set()
    total_ok = 0
    total_fail = 0

    try:
        # Идемпотентность повторного прогона: чистим staging для региона перед началом,
        # чтобы обрыв предыдущего прогона не оставил там "хвост" вперемешку с новым.
        # sync_errors тоже чистим по региону: OBJECTID_1 источника не гарантированно
        # стабилен во времени (подтверждено на практике — источник переиспользовал ID
        # для другого участка между двумя прогонами), поэтому история ошибок по старому
        # ID не несёт надёжного сигнала и не сохраняется — таблица отражает только
        # проблемы последнего прогона.
        with conn.cursor() as cur:
            cur.execute(CLEANUP_STAGING_SQL, (region,))
            cur.execute(CLEANUP_SYNC_ERRORS_SQL, (region,))
        conn.commit()

        for page_features in fetch_all_features(service_path, server_type, layer_id, region):
            rows_with_features = []
            for feature in page_features:
                try:
                    row, feat = feature_to_row(feature, region, source_layer_full, seen_uncovered_keys)
                    rows_with_features.append((row, feat))
                except (ValueError, TypeError) as e:
                    # Не смогли даже собрать row (нет objectid/geometry) — логируем сразу, без вставки.
                    total_fail += 1
                    insert_error(conn, region, None, f"feature_to_row failed: {e}", feature)

            for i in range(0, len(rows_with_features), BATCH_SIZE):
                batch = rows_with_features[i:i + BATCH_SIZE]
                ok, fail = insert_batch_to_staging(conn, batch, region)
                total_ok += ok
                total_fail += fail

        print(f"[{region}] выгрузка в staging завершена: ok={total_ok}, fail={total_fail}")

        if total_ok == 0:
            raise RuntimeError("0 объектов успешно выгружено — перенос в production отменён")

        transferred, retired = transfer_region_to_production(conn, region)
        print(f"[{region}] перенос в land_polygons: upsert={transferred}, retired(is_active=false)={retired}")

        status = "success" if total_fail == 0 else "partial"
        sync_log_finish(conn, log_id, status, total_ok,
                         error_message=(f"{total_fail} объектов не вставлено, см. sync_errors" if total_fail else None))
        print(f"=== Синк региона '{region}' завершён: status={status} ===")

    except Exception as e:
        conn.rollback()
        err_text = f"{e}\n{traceback.format_exc()}"
        sync_log_finish(conn, log_id, "failed", total_ok, error_message=str(e))
        print(f"=== Синк региона '{region}' ПРОВАЛЕН: {e} ===", file=sys.stderr)
        print(err_text, file=sys.stderr)
        conn.close()
        sys.exit(1)

    conn.close()


# ------------------------------------------------------------------
# Сверка с источником (README: не "скрипт отработал", а "числа совпали")
# ------------------------------------------------------------------

def verify_region(region):
    if region not in REGION_REGISTRY:
        print(f"FATAL: регион '{region}' не найден в REGION_REGISTRY.", file=sys.stderr)
        sys.exit(1)
    cfg = REGION_REGISTRY[region]

    print(f"=== Сверка региона '{region}': источник vs БД ===")
    source_stats = fetch_source_stats(cfg["service_path"], cfg["server_type"], cfg["layer_id"])
    source_by_type = {}
    for f in source_stats:
        attrs = f.get("attributes", {})
        obj_type = (attrs.get("object_ru") or "").strip() or "(пусто)"
        cnt = attrs.get("cnt") or 0
        ga = attrs.get("ga") or 0
        if obj_type in source_by_type:
            # источник группирует по СЫРОМУ значению (README: "пастбище" в 3 вариантах
            # из-за хвостовых пробелов) -- после .strip() несколько групп источника
            # схлопываются в один ключ, поэтому суммируем, а не перезаписываем
            source_by_type[obj_type]["count"] += cnt
            source_by_type[obj_type]["area_ha"] += ga
        else:
            source_by_type[obj_type] = {"count": cnt, "area_ha": ga}

    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(
            """SELECT COALESCE(object_ru, '(пусто)'), count(*), sum(area_ha)
               FROM land_polygons WHERE source_region = %s AND is_active = true
               GROUP BY object_ru""",
            (region,),
        )
        db_by_type = {row[0]: {"count": row[1], "area_ha": row[2]} for row in cur.fetchall()}

        cur.execute("SELECT count(*) FROM land_polygons WHERE source_region = %s AND is_active = true", (region,))
        db_total = cur.fetchone()[0]
    conn.close()

    print(f"\n{'Тип угодья':<25} {'Источник, шт':>15} {'БД, шт':>10} {'Источник, га':>15} {'БД, га':>15}")
    all_types = sorted(set(source_by_type) | set(db_by_type))
    for t in all_types:
        s = source_by_type.get(t, {"count": "-", "area_ha": "-"})
        d = db_by_type.get(t, {"count": "-", "area_ha": "-"})
        print(f"{t:<25} {s['count']!s:>15} {d['count']!s:>10} {s['area_ha']!s:>15} {d['area_ha']!s:>15}")

    print(f"\nВсего в БД (is_active=true): {db_total}")
    if "expected_count" in cfg:
        print(f"Ожидалось по Этапу 1 (README): {cfg['expected_count']}")
    print("Сравни числа глазами — совпадение количества и сумм по каждому типу угодья, не только общий факт запуска.")


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Синк agromap.gishagi.kz -> PostGIS (agromap_geo)")
    parser.add_argument("--region", required=True, help="Ключ региона из REGION_REGISTRY, напр. 'sko'")
    parser.add_argument("--verify-only", action="store_true",
                         help="Не синкать, только сверить текущие данные в БД с источником")
    args = parser.parse_args()

    if args.verify_only:
        verify_region(args.region)
    else:
        sync_region(args.region)
        verify_region(args.region)


if __name__ == "__main__":
    main()
