"""Загрузка файла заказчика «Клим_данные_по_метеостанциям_СКО_T_R_decade (Н4)»:
декады температуры и осадков по метеостанциям, ГТК(5-8) и K(5-8) по районам.

1. Декады (листы T_R_YYYY, разбор — app/services/climate_xlsx.py) → meteo_station_values
   (UPSERT). Строки «Станция (по Донор)» под таблицей года означают, что данные станции
   за год восполнены по соседней станции: такие декады (значение совпадает с донором;
   нулевые осадки не считаются признаком) помечаются derivation = «по станции <донор>».
   Затем пересчитываются месяцы станций и районов (app/services/climate_dekads.py).
2. ΣT(5-8) станции — столбец ΣT_05_08 файла (сумма активных температур выше 10 °C
   за май–август); значение района — среднее станций района.
   ГТК(5-8) станции — столбец ГТК_5-8 файла (= ΣR(5-8) / (0,1·ΣT(5-8)), проверено).
   K(5-8) станции = (0,5·ΣR(11–4) + ΣR(5-8)) / (0,12·ΣT(5-8)), где ΣR(11–4) — осадки
   ноября–декабря прошлого года и января–апреля текущего, ΣT(5-8) — сумма активных
   температур выше 10 °C за май–август (столбец ΣT_05_08 файла). Месячные суммы осадков —
   из декад. Значение района — среднее станций района (climate_region_station_map).
   Прежние значения показателей переносятся в indicator_values_archive; K за 2003 год
   (нет осадков ноября–декабря 2002 г.) не заменяется.

Запуск в контейнере бэкенда:
    docker cp "<файл>.xlsx" dss-backend:/tmp/climate.xlsx
    docker exec dss-backend python scripts/import_climate_workbook.py --file /tmp/climate.xlsx --dry-run
    docker exec dss-backend python scripts/import_climate_workbook.py --file /tmp/climate.xlsx
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import load_workbook  # noqa: E402
from sqlalchemy import text  # noqa: E402

import app.main  # noqa: E402,F401
from app.database import AsyncSessionLocal  # noqa: E402
from app.services import climate_dekads as cd  # noqa: E402
from app.services.climate_xlsx import SHEET_RE, norm_station, parse_substitutions, parse_workbook  # noqa: E402

KIND_TO_INDICATOR = {"T": cd.TEMPERATURE, "R": cd.PRECIPITATION}
GTK_INDICATOR = "Гидротермический коэффициент (ГТК)"
K_INDICATOR = "Коэффициент увлажнения (K)"
T58_INDICATOR = "Сумма активных температур воздуха выше 10 °C за май–август, ΣT(5-8)"
K_KEEP_YEARS = {2003}  # K не считается без осадков ноября–декабря прошлого года


def _num(v):
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def read_indices(wb) -> dict[tuple[int, str], dict]:
    """{(год, станция): {"gtk": …, "t58": …}} из годовых листов."""
    out = {}
    for name in wb.sheetnames:
        m = SHEET_RE.match(name)
        if not m:
            continue
        year = int(m.group(1))
        rows = list(wb[name].iter_rows(values_only=True))
        if not rows:
            continue
        hdr = [str(h).strip() if h is not None else "" for h in rows[0]]
        st_i = hdr.index("station") if "station" in hdr else 1
        gtk_i = hdr.index("ГТК_5-8") if "ГТК_5-8" in hdr else None
        t58_i = next((i for i, h in enumerate(hdr) if re.match(r"^ΣT_05_08", h)), None)
        for r in rows[1:]:
            if st_i >= len(r) or not r[st_i]:
                continue
            key = (year, norm_station(str(r[st_i])))
            if key in out:
                continue  # основная таблица — первое вхождение станции
            out[key] = {
                "gtk": _num(r[gtk_i]) if gtk_i is not None and gtk_i < len(r) else None,
                "t58": _num(r[t58_i]) if t58_i is not None and t58_i < len(r) else None,
            }
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    wb = load_workbook(args.file, read_only=True, data_only=True)
    cells = parse_workbook(wb)

    async with AsyncSessionLocal() as db:
        st_rows = (await db.execute(text("SELECT id, name FROM meteo_stations"))).all()
        stations = {norm_station(r.name): (r.id, r.name) for r in st_rows}
        indicators = {r.name: r.id for r in (await db.execute(
            text("SELECT id, name FROM indicators WHERE name = ANY(:n)"),
            {"n": [*KIND_TO_INDICATOR.values(), GTK_INDICATOR, K_INDICATOR, T58_INDICATOR]})).all()}
        missing = [n for n in (*KIND_TO_INDICATOR.values(), GTK_INDICATOR, K_INDICATOR, T58_INDICATOR)
                   if n not in indicators]
        if missing:
            print(f"Нет показателей: {missing}", file=sys.stderr)
            return 1
        src_dekad = (await db.execute(text("SELECT id FROM data_sources WHERE name = :n LIMIT 1"),
                                      {"n": cd.DEKAD_SOURCE})).scalar_one_or_none()
        src_calc = (await db.execute(text("SELECT id FROM data_sources WHERE name = :n LIMIT 1"),
                                     {"n": cd.CALC_SOURCE})).scalar_one_or_none()

        # ── 1. декады ────────────────────────────────────────────────────
        subs = parse_substitutions(wb, stations.keys())
        values = {}
        skipped = Counter()
        for c in cells:
            st = norm_station(c.station)
            if st not in stations:
                skipped[c.station] += 1
                continue
            values[(c.year, st, c.kind, c.month, c.dekad)] = c.value
        rows, marked = [], Counter()
        for (y, st, kind, mo, dk), v in values.items():
            derivation = None
            donor = subs.get((y, st))
            if donor:
                dv = values.get((y, donor, kind, mo, dk))
                if dv is not None and dv == v and not (kind == "R" and v == 0):
                    derivation = f"по станции {stations[donor][1]}"[:30]
                    marked[(y, stations[st][1], stations[donor][1])] += 1
            start, end = cd.dekad_bounds(y, mo, dk)
            rows.append({"st": stations[st][0], "ind": indicators[KIND_TO_INDICATOR[kind]], "ps": start,
                         "pe": end, "dk": dk, "v": v, "src": src_dekad, "der": derivation})
        print(f"Декад к загрузке: {len(rows)}")
        if skipped:
            print("Пропущены строки вне справочника станций: "
                  + ", ".join(f"{k} ({v})" for k, v in sorted(skipped.items())))
        for (y, st, donor), n in sorted(marked.items()):
            print(f"  {y}: {st} — {n} декад помечены «по станции {donor}»")

        before = {(r.station_id, r.indicator_id, r.period_start): r.value for r in (await db.execute(text(
            "SELECT station_id, indicator_id, period_start, value FROM meteo_station_values WHERE period_type='dekad'"
        ))).all()}
        changed = sum(1 for r in rows if (r["st"], r["ind"], r["ps"]) in before
                      and abs(before[(r["st"], r["ind"], r["ps"])] - r["v"]) > 1e-9)
        added = sum(1 for r in rows if (r["st"], r["ind"], r["ps"]) not in before)
        print(f"  из них новых: {added}, изменённых: {changed}")

        await db.execute(text("""
            INSERT INTO meteo_station_values
                (id, station_id, indicator_id, period_type, period_start, period_end, dekad, value,
                 derivation, source_id, validation_status, validated_at)
            VALUES (gen_random_uuid(), :st, :ind, 'dekad', :ps, :pe, :dk, :v, :der, :src, 'approved', now())
            ON CONFLICT ON CONSTRAINT msv_unique DO UPDATE SET
                value = EXCLUDED.value, derivation = EXCLUDED.derivation, source_id = EXCLUDED.source_id,
                validation_status = 'approved', validated_at = now(), validated_by = NULL
        """), rows)
        stats = await cd.recompute(db, cd.month_keys_from_values((r["st"], r["ind"], r["ps"]) for r in rows))
        print(f"Пересчёт месяцев: {stats}")

        # ── 2. ГТК(5-8) и K(5-8) ─────────────────────────────────────────
        idx = read_indices(wb)
        rsum = defaultdict(dict)  # (год, станция) → {месяц: сумма осадков}
        for (y, st, kind, mo, dk), v in values.items():
            if kind == "R":
                rsum[(y, st)].setdefault(mo, []).append(v)
        def month_r(y, st, mo):
            vals = rsum.get((y, st), {}).get(mo)
            return sum(vals) if vals and len(vals) == 3 else None

        gtk_st, k_st, t58_st = {}, {}, {}
        for (y, st), d in idx.items():
            if st not in stations:
                continue
            if d["gtk"] is not None:
                gtk_st[(y, st)] = d["gtk"]
            t58 = d["t58"]
            if t58:
                t58_st[(y, st)] = t58
            parts = [month_r(y - 1, st, 11), month_r(y - 1, st, 12)] + [month_r(y, st, m) for m in (1, 2, 3, 4)]
            r58 = [month_r(y, st, m) for m in (5, 6, 7, 8)]
            if t58 and None not in parts and None not in r58:
                k_st[(y, st)] = (0.5 * sum(parts) + sum(r58)) / (0.12 * t58)

        region_of = defaultdict(list)
        for r in (await db.execute(text("""
            SELECT s.name, m.region_id FROM climate_region_station_map m JOIN meteo_stations s ON s.id = m.station_id
        """))).all():
            region_of[norm_station(r.name)].append(r.region_id)

        def by_region(st_vals):
            acc = defaultdict(list)
            for (y, st), v in st_vals.items():
                for rid in region_of.get(st, []):
                    acc[(y, rid)].append(v)
            return {k: sum(v) / len(v) for k, v in acc.items()}

        for name, st_vals, src, keep in ((GTK_INDICATOR, gtk_st, src_dekad, set()),
                                          (K_INDICATOR, k_st, src_calc, K_KEEP_YEARS),
                                          (T58_INDICATOR, t58_st, src_dekad, set())):
            ind = indicators[name]
            reg = {k: v for k, v in by_region(st_vals).items() if k[0] not in keep}
            old = (await db.execute(text("""
                SELECT avg(value) FROM indicator_values WHERE indicator_id = :i AND period_type = 'year'
                  AND NOT (extract(year FROM period_start)::int = ANY(:keep))
            """), {"i": ind, "keep": list(keep) or [0]})).scalar()
            await db.execute(text("""
                INSERT INTO indicator_values_archive
                    (id, indicator_id, region_id, period_type, period_start, period_end, value, source_id,
                     validation_status, created_at, reason)
                SELECT id, indicator_id, region_id, period_type, period_start, period_end, value, source_id,
                       validation_status, created_at, :reason
                FROM indicator_values
                WHERE indicator_id = :i AND period_type = 'year'
                  AND NOT (extract(year FROM period_start)::int = ANY(:keep))
            """), {"i": ind, "keep": list(keep) or [0],
                   "reason": "Замена расчётом по метеостанциям (файл T_R_decade Н4)"})
            await db.execute(text("""
                DELETE FROM indicator_values WHERE indicator_id = :i AND period_type = 'year'
                  AND NOT (extract(year FROM period_start)::int = ANY(:keep))
            """), {"i": ind, "keep": list(keep) or [0]})
            await db.execute(text("""
                INSERT INTO indicator_values
                    (id, indicator_id, region_id, period_type, period_start, period_end, value, source_id,
                     validation_status, validated_at)
                VALUES (gen_random_uuid(), :i, :rid, 'year', :ps, :pe, :v, :src, 'approved', now())
            """), [{"i": ind, "rid": rid, "ps": date(y, 1, 1), "pe": date(y, 12, 31), "v": v, "src": src}
                   for (y, rid), v in sorted(reg.items())])
            years = sorted({y for y, _ in reg})
            new_mean = sum(reg.values()) / len(reg) if reg else None
            print(f"{name}: станций-лет {len(st_vals)}, районов-лет {len(reg)}, годы {years[0]}–{years[-1]}; "
                  f"среднее было {old:.3f}, стало {new_mean:.3f}" if reg and old else f"{name}: {len(reg)} районов-лет")

        if args.dry_run:
            await db.rollback()
            print("dry-run: изменения откатены")
        else:
            await db.commit()
            print("Записано.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
