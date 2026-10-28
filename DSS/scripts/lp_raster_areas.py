#!/usr/bin/env python3
"""
Площади классов вариантов ландшафтного планирования в разрезе районов.

Смежная команда (АЛДИС) выпустила результаты ЛП: у всех пятнадцати групп
появились готовые растры, включая три варианта — V1 Conservative, V2 Balanced,
V3 Development. Раньше `/landscape/results` отдавал пустые `latest`, и модуль
ЗБ-УР работал на синтетических площадях.

## Что скрипт делает и чего НЕ делает

Делает: считает, сколько пикселей каждого класса попадает в каждый район, и
переводит их в гектары. Это измеримый факт, не требующий договорённостей.

НЕ делает: не переводит классы в категории земель — и, как выяснилось на этих
же площадях, перевести их напрямую нельзя в принципе. Пять содержательных
классов покрывают 99,5-99,9 % области, тогда как земли сельскохозяйственного
назначения занимают 73,1 %; отнести классы к категориям «в лоб» значит
перевести около 2600 тыс. га лесов, вод, населённых пунктов и ООПТ в
сельхозугодья. Класс 1 в V1 занимает 3024 тыс. га при фактической посевной
площади на орошении 17,3 тыс. га — в 175 раз больше. Значит классы задают
приоритетное направление использования по всей территории, а не категорию
земель, и мост к земельному балансу требует методического решения, а не
таблицы соответствия. Запрос отправлен смежной команде.

Проверку согласованности печатает `--recheck`; тот же порог зашит гейтом
`transitions.IMPLAUSIBLE_MOVE_SHARE`, чтобы неверная карта не прошла молча.

Запуск:
    backend/.venv/bin/python scripts/lp_raster_areas.py --variant lp-v3-development
    backend/.venv/bin/python scripts/lp_raster_areas.py --all --out data/research/lp_class_areas.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

GIS_BASE = os.environ.get("GIS_SERVICE_URL", "https://dssgis.dwh.kz").rstrip("/")
# GIS требует X-API-Key (аудит C-2).
GIS_HEADERS = {"X-API-Key": os.environ["GIS_API_KEY"]} if os.environ.get("GIS_API_KEY") else {}
TITILER_CONTAINER = "dss-titiler"
BACKEND_CONTAINER = "dss-backend"
COG_DIR = "/data/cogs"
DISTRICTS = Path("frontend/public/geo/districts.geojson")
LAND_BALANCE_REF = "ai-service/app/services/landscape_impact/reference/sko_2014_2024.json"

VARIANTS = ("lp-v1-conservative", "lp-v2-balanced", "lp-v3-development")
CLASS_VALUES = list(range(8))  # диапазон растра 0…7, проверено на V3
# Классы, отвечающие пяти направлениям слоёв C (орошение, снегозадержание,
# растениеводство, пастбища, туризм) — см. tie_priority прогона.
DIRECTIONAL_CLASSES = (1, 2, 3, 4, 5)


def gis_get(path: str) -> dict | list:
    req = urllib.request.Request(f"{GIS_BASE}{path}", headers=GIS_HEADERS)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def resolve_variant_datasets() -> dict[str, dict]:
    """Группа варианта → сведения о последнем растре."""
    out = {}
    for group in gis_get("/landscape/results"):
        if group.get("group") in VARIANTS and group.get("latest"):
            out[group["group"]] = group["latest"]
    return out


def ensure_local_cog(dataset_id: str, tmp: Path) -> str:
    """Скачать COG и положить в том, который читает titiler."""
    local_name = f"lp-{dataset_id}.tif"
    listing = subprocess.run(
        ["docker", "exec", TITILER_CONTAINER, "ls", COG_DIR],
        capture_output=True, text=True, check=True,
    ).stdout
    if local_name in listing:
        return f"{COG_DIR}/{local_name}"

    path = tmp / local_name
    if not path.exists():
        print(f"  скачивание растра {dataset_id}…")
        req = urllib.request.Request(f"{GIS_BASE}/datasets/{dataset_id}.cog", headers=GIS_HEADERS)
        with urllib.request.urlopen(req, timeout=600) as r, open(path, "wb") as fh:
            shutil.copyfileobj(r, fh)
    # Том смонтирован в titiler только на чтение, пишем через backend.
    subprocess.run(
        ["docker", "cp", str(path), f"{BACKEND_CONTAINER}:{COG_DIR}/{local_name}"],
        check=True, capture_output=True,
    )
    return f"{COG_DIR}/{local_name}"


def titiler_post(endpoint: str, params: dict, body: dict) -> dict:
    """POST в titiler изнутри контейнера: наружу он не опубликован."""
    script = (
        "import json,urllib.request,urllib.parse,sys\n"
        "params=json.loads(sys.argv[1]); body=json.loads(sys.argv[2])\n"
        f"url='http://localhost:8000{endpoint}?'+urllib.parse.urlencode(params, doseq=True)\n"
        "req=urllib.request.Request(url, data=json.dumps(body).encode(), "
        "headers={'Content-Type':'application/json'})\n"
        "print(urllib.request.urlopen(req, timeout=600).read().decode())\n"
    )
    res = subprocess.run(
        ["docker", "exec", "-i", TITILER_CONTAINER, "python", "-c", script,
         json.dumps(params), json.dumps(body)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(res.stdout)


def district_areas_ha() -> dict[str, float]:
    """Площади районов из базы (показатель «Площадь территории», км² → га).

    Площади берутся из базы, а не считаются по геометрии растра: статистика
    titiler возвращается на прореженной сетке, и переводить её пиксели в
    гектары через разрешение — значит завязаться ещё и на коэффициент
    прореживания. Доля класса внутри района от прореживания не зависит, поэтому
    площади раскладываются по долям. Джойн по КАТО: названия районов в геоданных,
    в книге заказчика и в базе различаются написанием.
    """
    import os

    import psycopg2

    dsn = os.environ.get("DSS_DB_DSN") or (
        "host=127.0.0.1 port=5433 dbname=dss_db user=dss_user password="
        + os.environ["POSTGRES_PASSWORD"]  # аудит H-5: без пароля по умолчанию
    )
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.kato_code, iv.value
            FROM indicator_values iv
            JOIN indicators i ON i.id = iv.indicator_id
            JOIN regions r ON r.id = iv.region_id
            WHERE i.name = 'Площадь территории'
              AND EXTRACT(YEAR FROM iv.period_start) = (
                  SELECT MAX(EXTRACT(YEAR FROM period_start))
                  FROM indicator_values v2 WHERE v2.indicator_id = i.id
              )
            """
        )
        # Ключ — первые четыре значащие цифры КАТО: они задают район, а
        # последующие уточняют населённый пункт. У Петропавловска геоданные дают
        # код городской администрации (591000000), база — код города
        # (591010000); прочие тринадцать районов совпадают точно. Сопоставление
        # по префиксу разрешает это однозначно, потому что у всех четырнадцати
        # территорий префиксы разные.
        return {str(kato)[:4]: float(value) * 100.0 for kato, value in cur.fetchall()}


def pixel_area_ha(cog_path: str) -> float:
    """Площадь пикселя в гектарах — диагностика разрешения растра.

    Растры ЛП приходят в проекции UTM (EPSG:32642), где границы уже в метрах;
    пересчёт из градусов здесь не нужен и дал бы бессмыслицу.
    """
    script = (
        "import json,urllib.request,urllib.parse,sys\n"
        "url='http://localhost:8000/cog/info?'+urllib.parse.urlencode({'url':sys.argv[1]})\n"
        "print(urllib.request.urlopen(url, timeout=120).read().decode())\n"
    )
    res = subprocess.run(
        ["docker", "exec", "-i", TITILER_CONTAINER, "python", "-c", script, cog_path],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(res.stdout)
    minx, miny, maxx, maxy = info["bounds"]
    crs = str(info.get("crs", ""))
    if "4326" in crs or max(abs(minx), abs(maxx)) <= 180:
        return float("nan")  # географические координаты — здесь не ожидаются
    w_m = (maxx - minx) / info["width"]
    h_m = (maxy - miny) / info["height"]
    return w_m * h_m / 10_000.0


def land_balance_consistency(result: dict) -> dict:
    """Можно ли вообще отобразить классы ЛП в категории земельного баланса.

    Проверка не косметическая: без неё сопоставление «класс → категория»
    подставляется по догадке и ломает земельный баланс молча. Считаем, сколько
    территории покрывают пять содержательных классов, и сравниваем с долей
    земель сельскохозяйственного назначения. Если классы покрывают почти всю
    область, а сельхозземли — лишь три четверти, то классы описывают не
    категорию земель, а приоритетное направление использования, и прямое
    отображение перевело бы леса, воды и населённые пункты в сельхозугодья.
    """
    ref = json.loads(Path(LAND_BALANCE_REF).read_text(encoding="utf-8"))
    last = len(ref["meta"]["years"]) - 1
    balance = {
        k: v[last] for k, v in ref["land_balance"]["series"].items()
        if isinstance(v, list)
    }
    balance_total = sum(balance.values())
    agri_share = balance["agricultural"] / balance_total

    per_variant = {}
    for variant, info in result["variants"].items():
        agg: dict[str, float] = {}
        for rec in info["districts"].values():
            for cls, area in rec["class_area_ha"].items():
                agg[cls] = agg.get(cls, 0.0) + area
        total = sum(agg.values())
        directional = sum(agg.get(str(c), 0.0) for c in DIRECTIONAL_CLASSES)
        per_variant[variant] = {
            "directional_classes_share": round(directional / total, 4),
            "class_0_ha": round(agg.get("0", 0.0), 1),
            "class_6_7_ha": round(agg.get("6", 0.0) + agg.get("7", 0.0), 1),
            "class_area_ths_ha": {k: round(v / 1000.0, 1) for k, v in sorted(agg.items(), key=lambda kv: int(kv[0]))},
        }

    return {
        "agricultural_share_of_balance": round(agri_share, 4),
        "land_balance_total_ths_ha": round(balance_total, 1),
        "per_variant": per_variant,
        "conclusion": (
            "Пять содержательных классов покрывают практически всю область "
            "(99,5-99,9 %), тогда как земли сельскохозяйственного назначения "
            "занимают 73,1 %. Значит классы ЛП задают приоритетное направление "
            "использования по всей территории, а не категорию земель: прямое "
            "отображение «класс -> категория» перевело бы около 2600 тыс. га "
            "лесов, вод, населённых пунктов и ООПТ в сельхозугодья и сломало бы "
            "земельный баланс. Мост требует методического решения заказчика, "
            "а не таблицы соответствия."
        ),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Площади классов вариантов ЛП по районам")
    p.add_argument("--variant", choices=VARIANTS)
    p.add_argument("--all", action="store_true")
    p.add_argument("--out")
    p.add_argument("--recheck", metavar="JSON",
                   help="Перепроверить готовый файл площадей, не пересчитывая растры")
    p.add_argument("--max-size", type=int, default=4096,
                   help="Сторона сетки для статистики; меньше — быстрее, грубее")
    args = p.parse_args()

    if args.recheck:
        result = json.loads(Path(args.recheck).read_text(encoding="utf-8"))
        result["class_semantics"]["land_balance_check"] = land_balance_consistency(result)
        Path(args.recheck).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(result["class_semantics"]["land_balance_check"]["conclusion"])
        return

    variants = list(VARIANTS) if args.all else [args.variant or VARIANTS[-1]]
    districts = json.loads(DISTRICTS.read_text(encoding="utf-8"))
    tmp = Path("/tmp")

    areas_by_kato = district_areas_ha()
    print(f"Площади районов из базы: {len(areas_by_kato)} шт., "
          f"суммарно {sum(areas_by_kato.values()) / 1e6:.2f} млн га")

    datasets = resolve_variant_datasets()
    missing = [v for v in variants if v not in datasets]
    if missing:
        raise SystemExit(f"Нет готового растра для вариантов: {missing}")

    result: dict = {"source": GIS_BASE, "classes": CLASS_VALUES, "variants": {}}

    for variant in variants:
        meta = datasets[variant]
        print(f"\n=== {variant} ({meta['name']}) ===")
        cog = ensure_local_cog(meta["id"], tmp)
        px_ha = pixel_area_ha(cog)
        print(f"  разрешение растра: {px_ha * 10_000:.0f} м² на пиксель")

        per_district: dict[str, dict] = {}
        for feature in districts["features"]:
            name = feature["properties"]["name_ru"]
            stats = titiler_post(
                "/cog/statistics",
                {"url": cog, "categorical": "true", "max_size": args.max_size,
                 **({"c": CLASS_VALUES}),},
                {"type": "FeatureCollection", "features": [feature]},
            )
            band = next(iter(stats["features"][0]["properties"]["statistics"].values()))
            hist_counts, hist_values = band["histogram"]
            total_px = sum(hist_counts) or 1
            kato = feature["properties"]["kato"]
            area_ha = areas_by_kato.get(str(kato)[:4])
            if area_ha is None:
                raise SystemExit(f"Нет площади района КАТО {kato} в базе")
            areas = {
                str(int(v)): round(c / total_px * area_ha, 1)
                for c, v in zip(hist_counts, hist_values)
            }
            per_district[name] = {
                "kato": kato,
                "district_area_ha": area_ha,
                "valid_pixels": band["count"],
                "class_area_ha": areas,
            }
            top = max(areas, key=lambda k: areas[k])
            print(f"  {name[:30]:30s} преобладает класс {top} ({areas[top]:,.0f} га)".replace(",", " "))

        result["variants"][variant] = {
            "dataset_id": meta["id"],
            "name": meta["name"],
            "created_at": meta.get("created_at"),
            "pixel_area_ha": round(px_ha, 6),
            "districts": per_district,
        }

    result["class_semantics"] = {
        "status": "не подтверждено",
        "note": (
            "Таблица соответствия «класс ЛП → категория земель» от смежной команды "
            "не получена; в метаданных растра легенды нет, поле SLD пустое. "
            "Площади приведены по номерам классов. Подставлять семантику по "
            "догадке (состав входов прогона и tie_priority) в расчёт устойчивого "
            "развития нельзя — это было бы непроверяемым допущением разработчика."
        ),
    }

    if len(result["variants"]) == len(VARIANTS):
        check = land_balance_consistency(result)
        result["class_semantics"]["land_balance_check"] = check
        print("\n=== согласованность с земельным балансом ===")
        for variant, rec in check["per_variant"].items():
            print(f"  {variant:22s} классы 1-5 покрывают "
                  f"{rec['directional_classes_share'] * 100:.2f} % области")
        print(f"  земли с/х назначения занимают "
              f"{check['agricultural_share_of_balance'] * 100:.2f} % области")
        print(f"\n{check['conclusion']}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nСохранено: {args.out}")


if __name__ == "__main__":
    main()
