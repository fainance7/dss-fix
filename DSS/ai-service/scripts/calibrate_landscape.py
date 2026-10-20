#!/usr/bin/env python3
"""Калибровка алгоритма ЗБ-УР на историческом окне 2014-2024 и печать протокола.

Это то, что заказчик описал как «на этом примере, с 2014 по 2024 год, вырабатываем
алгоритмы»: скрипт прогоняет шаг 1 алгоритма и печатает, что именно извлечено из
истории, какие связи прошли проверку, а какие понижены до экспертных.

Запуск:
    docker exec dss-ai-service python scripts/calibrate_landscape.py
    docker exec dss-ai-service python scripts/calibrate_landscape.py --json > calib.json
    docker exec dss-ai-service python scripts/calibrate_landscape.py --with-db

`--with-db` дополнительно сверяет ряды витрины с `indicator_values` и печатает
расхождения. Сверка ничего не подменяет: на этих рядах уже зафиксированы
расхождения источников до 48 %, и выбор источника — решение заказчика, не скрипта.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.landscape_impact.assumptions import (  # noqa: E402
    assumptions_version, list_assumptions, unverified_keys,
)
from app.services.landscape_impact.calibration import calibrate  # noqa: E402
from app.services.landscape_impact.datamart import (  # noqa: E402
    load_datamart, load_reference_datamart, reference_version,
)
from app.services.landscape_impact.pipeline import ALGORITHM_VERSION  # noqa: E402
from app.services.landscape_impact.sd_index import build_model, index_version  # noqa: E402

LINE = "─" * 78


def _print_report(mart, calib, sd_model) -> None:
    print(LINE)
    print("КАЛИБРОВКА АЛГОРИТМА ЗБ-УР НА ОКНЕ "
          f"{calib.window[0]}-{calib.window[1]} ({mart.n_years} годовых наблюдений)")
    print(f"версии: алгоритм {ALGORITHM_VERSION} · витрина {reference_version()} · "
          f"коэффициенты {assumptions_version()} · индекс DSS {index_version()}")
    print(LINE)

    print("\n1. БАЛАНСОВЫЕ ТОЖДЕСТВА  [Б]")
    if calib.balance_ok:
        print(f"   Сумма семи категорий земель постоянна во все {mart.n_years} лет "
              f"и равна {mart.total_land} тыс. га — отклонение ноль.")
        print("   Следствие: любой сценарий землепользования есть игра с нулевой суммой;")
        print("   прирост одной категории обязан быть покрыт убылью других.")
    else:
        print(f"   НАРУШЕНО в {len(calib.balance_problems)} годах:")
        for p in calib.balance_problems:
            print(f"     {p['year']}: сумма {p['sum']} против {p['expected']}")

    print("\n2. СТРУКТУРНЫЕ ДОЛИ «категория → угодье → посев»  [Б]")
    print(f"   {'коэффициент':34s} {'среднее':>9s} {'σ':>8s} {'диапазон':>20s}")
    for key, v in calib.structural_shares.items():
        print(f"   {key:34s} {v['mean']:9.4f} {v['sd']:8.4f} "
              f"  [{v['low']:.3f}; {v['high']:.3f}]")

    print("\n3. УРАВНЕНИЕ УРОЖАЙНОСТИ ЗЕРНОВЫХ  (первые разности логарифмов, n = 10)")
    print(f"   {'регрессор':12s} {'b':>8s} {'r':>7s} {'R²':>6s} {'p':>7s} {'p_adj':>7s} "
          f"{'MASE':>6s}  гейты G4/G5/G6/G7")
    for c in calib.yield_candidates:
        g = c.gates
        marks = "".join("+" if g[k] else "−" for k in
                        ("G4_skill", "G5_jackknife", "G6_significance", "G7_prior_sign"))
        print(f"   {c.regressor:12s} {c.slope:+8.3f} {c.r:+7.3f} {c.r2:6.3f} "
              f"{c.p_permutation:7.4f} {c.p_adjusted:7.4f} {c.mase:6.3f}  {marks}")
    link = calib.yield_link
    if link:
        print(f"\n   Принято: {link.regressor} → метка связи [{link.link_type}]")
        lo, base, hi = link.expert_range
        print(f"   Диапазон коэффициента: [{lo:+.3f}; {base:+.3f}; {hi:+.3f}] (jackknife)")

    print("\n4. ПРЯМАЯ СВЯЗЬ «земельный баланс → показатель»  (отрицательный контроль)")
    dl = calib.direct_land_links
    print(f"   Проверено пар: {dl['n_tests']}; минимальное p = {dl['min_p']}; "
          f"порог Бонферрони = {dl['bonferroni_threshold']}")
    print(f"   Прошло поправку FDR: {dl['survived_fdr']}")
    for line in _wrap(dl["conclusion"], 74):
        print(f"   {line}")

    print("\n5. ИНДЕКСЫ УСТОЙЧИВОГО РАЗВИТИЯ")
    agg = calib.sd_aggregation
    print(f"   Межблочная свёртка: {agg['rule']}, "
          f"максимальное отклонение {agg['max_abs_deviation_arithmetic']}")
    for line in _wrap(agg["note"], 74):
        print(f"   {line}")
    print(f"\n   Режим индекса: {sd_model.mode}")
    for r in sd_model.reconstruction:
        mark = "прошла" if r.passed else "не прошла"
        print(f"     {mark:9s} MAE {r.mae:7.4f} MaxAE {r.max_ae:7.4f} "
              f"знак {r.sign_matches}/{r.sign_total}  {r.hypothesis}")
    for line in _wrap(sd_model.note, 74):
        print(f"   {line}")

    print("\n6. МИНИМАЛЬНЫЙ СОДЕРЖАТЕЛЬНЫЙ ЭФФЕКТ (MDE)")
    for name in ("total", "social", "economic", "ecological"):
        print(f"   {name:12s} σ приращений {calib.mde[name + '_sigma']:.5f} → "
              f"MDE {calib.mde[name]:.5f}")
    print("   Эффект меньше MDE неотличим от исторического шума независимо от p-значений.")

    print("\n7. ЭКОНОМИЧЕСКИЙ КОНТУР")
    v = calib.vds_share_observed
    print(f"   Отношение ВП СХ / ВРП за окно: среднее {v['mean']}, σ {v['sd']}, "
          f"диапазон [{v['min']}; {v['max']}]")

    print("\n8. КАЧЕСТВО ВХОДНЫХ ДАННЫХ")
    if not mart.issues:
        print("   Замечаний нет.")
    for issue in mart.issues:
        head = f"[{issue['severity']}] {issue['kind']}"
        detail = issue.get("reason") or issue.get("message") or ""
        extra = issue.get("series") or issue.get("year") or ""
        print(f"   {head}: {extra} {detail}".rstrip())
        for point in issue.get("points", [])[:3]:
            print(f"       {point['year']}: витрина {point['mart']} против БД {point['db']} "
                  f"({point['rel']:.1%})")

    print("\n9. ЭКСПЕРТНЫЕ КОЭФФИЦИЕНТЫ")
    unverified = unverified_keys()
    print(f"   Всего {len(list_assumptions())}, без подписи экспертов заказчика: {len(unverified)}")
    for key in unverified:
        print(f"     • {key}")

    print("\n" + LINE)
    print("ЗАМЕТКИ КАЛИБРОВКИ")
    for note in calib.notes:
        for line in _wrap(note, 76):
            print(f"  {line}")
    print(LINE)


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for w in words:
        if len(current) + len(w) + 1 > width:
            lines.append(current)
            current = w
        else:
            current = f"{current} {w}".strip()
    if current:
        lines.append(current)
    return lines


async def _load(with_db: bool):
    if not with_db:
        return load_reference_datamart()
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        return await load_datamart(session)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Вывести результат в JSON")
    parser.add_argument("--with-db", action="store_true",
                        help="Сверить ряды витрины с indicator_values")
    args = parser.parse_args()

    mart = asyncio.run(_load(args.with_db))
    calib = calibrate(mart)
    sd_model = build_model(mart, mart.years[-1])

    if args.json:
        json.dump({
            "versions": {
                "algorithm": ALGORITHM_VERSION, "datamart": reference_version(),
                "assumptions": assumptions_version(), "dss_index": index_version(),
            },
            "calibration": calib.as_dict(),
            "sd_index_model": sd_model.as_dict(),
            "data_issues": mart.issues,
        }, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        _print_report(mart, calib, sd_model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
