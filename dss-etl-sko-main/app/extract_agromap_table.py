#!/usr/bin/env python3
"""
extract_agromap_table.py — потоково читает большой GeoJSON-выгруз с
agromap.gishagi.kz (formation: {layers:[{..., features:{features:[...]}}]})
и извлекает АТРИБУТЫ (properties) каждого объекта в плоскую CSV-таблицу.
Геометрию (координаты полигонов) НЕ читает и никуда не сохраняет.

КРИТИЧНО ДЛЯ ПАМЯТИ: стримит по одной ФИЧЕ (ijson path
'layers.item.features.features.item'), а не по слою целиком — иначе ijson
соберёт в памяти весь массив features (40000 полигонов с геометрией) перед
тем как отдать хоть один результат, что и убивает всю пользу от потокового
чтения. Метаданные слоя (service/layer_name/feature_count) читаются ДО
основного прохода лёгким текстовым скан-ом первых байт файла (эти поля
лежат в начале JSON, перед тяжёлым массивом features).

ВАЖНО: имена полей в properties различаются МЕЖДУ сервисами agromap (см.
README у sync_agromap.py). ALIASES ниже покрывают ОБЕ схемы, встреченные
в этом файле: "плоские" (Павлодар: object/ground/vegetation/water) и "_ru"
(другой сервис/регион дальше в том же файле: object_ru/ground_ru/...,
ближе к тому, что уже описано в sync_agromap.py FIELD_ALIASES).

Запуск:
    python3 extract_agromap_table.py <путь_к.json> <путь_к_выходному.csv>
"""
import csv
import re
import sys
from pathlib import Path

import ijson

ALIASES = {
    "object_ru":      ["object", "object_ru", "object1"],
    "object_kz":      ["object_kz", "object2"],
    "ground_ru":      ["ground", "ground_ru", "ground1"],
    "ground_kz":      ["ground_kz", "ground2"],
    "vegetation_ru":  ["vegetation", "vegetation_ru", "veg_ru"],
    "vegetation_kz":  ["vegetation_kz", "veg_kz"],
    "water_ru":       ["water", "water_ru"],
    "water_kz":       ["water_kz"],
    "description_ru": ["descriptio", "discriptio", "description_ru", "descr_ru", "description"],
    "description_kz": ["description_kz", "descr_kz"],
    "code":           ["code"],
    "code_esri":      ["code_esri", "esri_code"],
    "kato_raw":       ["kato"],
    "area":           ["area", "area_ha", "area_he"],
    "location_name":  ["name1", "name_ru", "name", "location"],
    "location_kz":    ["name_kz"],
    "annotation":     ["anno", "anno_ru"],
    "annotation_kz":  ["anno_kz"],
    "shape_length":   ["shape_leng", "SHAPE__Length"],
    "shape_area":     ["SHAPE__Area"],
}

KNOWN_INTERNAL_KEYS = {
    "OBJECTID", "OBJECTID_1", "objectid", "objectid_1", "FID",
    "SHAPE__Length", "SHAPE__Area", "shape_leng", "shape_Length", "shape_Area",
    "GlobalID", "globalid",
}

OUTPUT_COLUMNS = [
    "source_objectid", "code", "code_esri", "kato_raw", "location_name", "location_kz",
    "object_ru", "object_kz",
    "ground_ru", "ground_kz",
    "vegetation_ru", "vegetation_kz",
    "water_ru", "water_kz",
    "description_ru", "description_kz",
    "annotation", "annotation_kz",
    "area", "shape_length", "shape_area",
]


def normalize_value(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        return v if v != "" else None
    return v


def map_properties(props, seen_uncovered_keys):
    normalized = {k: normalize_value(v) for k, v in props.items()}
    typed = {}
    consumed_keys = set()
    for canonical, variants in ALIASES.items():
        for key in variants:
            if key in normalized:
                consumed_keys.add(key)
                if normalized[key] is not None:
                    typed[canonical] = normalized[key]
                    break

    for k, v in normalized.items():
        if k in consumed_keys or k in KNOWN_INTERNAL_KEYS:
            continue
        if k not in seen_uncovered_keys:
            seen_uncovered_keys.add(k)
            print(f"  [uncovered-key] '{k}' встретился и не попал в таблицу (пример: {v!r})")

    return typed


def get_objectid(props):
    for key in ("OBJECTID_1", "OBJECTID", "objectid_1", "objectid"):
        if key in props and props[key] is not None:
            return props[key]
    return None


def peek_layer_metadata(src_path: Path, max_bytes: int = 8192):
    with open(src_path, "rb") as f:
        head = f.read(max_bytes).decode("utf-8", errors="ignore")
    out = {}
    for key in ("service", "layer_name", "geometry_type"):
        m = re.search(rf'"{key}"\s*:\s*"([^"]*)"', head)
        if m:
            out[key] = m.group(1)
    m = re.search(r'"feature_count"\s*:\s*(\d+)', head)
    if m:
        out["feature_count"] = int(m.group(1))
    return out


def main():
    if len(sys.argv) < 3:
        print("Использование: python extract_agromap_table.py <вход.json> <выход.csv>")
        sys.exit(1)

    src_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    if not src_path.exists():
        print(f"Файл не найден: {src_path}")
        sys.exit(1)

    meta = peek_layer_metadata(src_path)
    print(f"=== Слой: {meta.get('service')} / {meta.get('layer_name')} "
          f"(заявлено объектов: {meta.get('feature_count')}) ===")

    seen_uncovered_keys = set()
    total = 0
    truncated = False
    truncation_error = None

    with open(src_path, "rb") as f_in, open(out_path, "w", newline="", encoding="utf-8-sig") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()

        try:
            for feature in ijson.items(f_in, "layers.item.features.features.item", use_float=True):
                props = feature.get("properties") or {}
                typed = map_properties(props, seen_uncovered_keys)
                row = {"source_objectid": get_objectid(props)}
                for col in OUTPUT_COLUMNS:
                    if col == "source_objectid":
                        continue
                    row[col] = typed.get(col)
                writer.writerow(row)
                total += 1
                if total % 5000 == 0:
                    print(f"  ...обработано {total} объектов")
        except ijson.JSONError as e:
            truncated = True
            truncation_error = str(e)

    if truncated:
        print(f"\n!!! Источник оборвался (premature EOF / некорректный JSON) после {total} объектов.")
        print(f"!!! Ошибка парсера: {truncation_error[:200]}")
        print("!!! Файл на диске физически неполный — нужен новый экспорт от источника,")
        print("!!! это не чинится на нашей стороне. Строки, прочитанные ДО обрыва, сохранены.")
    print(f"\nВсего строк записано: {total}")
    print(f"Непокрытых ключей встретилось: {len(seen_uncovered_keys)} -> {sorted(seen_uncovered_keys)}")
    print(f"Готово: {out_path}")


if __name__ == "__main__":
    main()
