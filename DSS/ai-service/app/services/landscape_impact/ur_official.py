"""
Официальный индекс устойчивого развития СКО — расчёт по методике заказчика.

Отличие от `sd_index.py`: там считается **индекс DSS** — собственная
конструкция разработчика с нормативными границами и геометрической свёрткой,
построенная в отсутствие методики. Здесь — воспроизведение **официального**
индекса из расчётной книги заказчика.

Оба индекса сосуществуют намеренно. Ответ на наше ключевое замечание (файл
`data/research/new_stats/ответы на Замечания…docx`, вопрос 5) закрепил
разделение ролей: официальный индекс остаётся инструментом мониторинга
территорий, а варианты ландшафтного планирования оценивает отдельный модуль,
потому что землепользование в официальный состав показателей не входит и
эффект любого варианта оказывается в шестнадцать раз ниже порога различимости.

## Что важно знать о методике

**Границы нормирования фиксированы**, а не пересчитываются по выборке. Это
подтверждено ответом на замечание 1 и видно в формулах книги: коэффициент
рождаемости делится на 20 ‰ при фактическом максимуме 16,67 ‰. Прежний наш
вывод о сквозном нормировании по 154 наблюдениям снят.

Практическое следствие (ответ на замечание 4): добавление нового года **не**
меняет ранее опубликованные значения. Границы образуют редакцию
(`bounds_edition`), их пересмотр — отдельное методическое решение с полным
ретроспективным перерасчётом.

**Значения вне границ усекаются** в [0; 1]. Правило закреплено в методике
ответом на замечание 12 (30.09.2026) вместе с порядком сценарного расчёта;
усечение по-прежнему фиксируется в отчёте расчёта.

**Областное значение** — невзвешенное среднее по 14 территориям. Вклад
Петропавловска равен вкладу любого сельского района, хотя в городе живёт 42 %
населения области. Ответ на замечание 10 (30.09.2026): способ сохраняется,
значение — территориально усреднённая оценка, а не показатель для среднего
жителя области. Взвешенный по населению ряд считается дополнительно и
помечается как справочный.

**Шкала классов** 0,500 / 0,650 / 0,750 / 0,900 по ответу на замечание 6 —
сравнительная, а не нормативная: «Высокий / Средний / Ниже среднего». Здесь она
не используется, но при выводе классов на экран брать надо её формулировки.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

_REF_FILE = Path(__file__).parent / "reference" / "ur_official.json"

BLOCKS = ("social", "economic", "ecological")

# Приведение сырого значения к величине, к которой применяется нормирование.
# Спецификации выписаны по формулам книги (поле `transform_formula` справочника)
# и проверяются сверкой с сохранёнными промежуточными значениями — см.
# `verify_transforms`. Ручная запись без проверки была бы допущением.
#   per_mille     — на 1000 жителей (делим на численность в тысячах)
#   per_10k       — на 10 000 жителей
#   per_capita_k  — на душу, результат в тысячах (× 1000 к численности в тысячах)
#   per_capita    — на душу без масштабирования
#   per_area      — на квадратный километр
#   per_area_k    — на тысячу квадратных километров
#   ratio_pair    — отношение двух рядов одного листа (зарплата к прожиточному минимуму)
#   identity      — сырое значение уже в нужной размерности
TRANSFORMS: dict[str, str] = {
    "birth_rate": "identity",
    "death_rate": "identity",
    "migration_balance": "per_mille",
    "unemployment": "identity",
    "ageing_index": "identity",
    "crime_rate": "per_10k",
    "investment_pc": "per_capita_k",
    "industry_pc": "per_capita_k",
    "agri_pc": "per_capita",
    "retail_pc": "per_capita_k",
    "wage_to_subsistence": "ratio_pair",
    # Делится на площадь в ТЫСЯЧАХ км², тогда как выбросы — на площадь в км².
    # Две разные размерности на одном справочнике площадей; при этом площадь в
    # тысячах округлена в книге до сотых (Петропавловск: 0,22 вместо 0,221), что
    # смещает результат на 0,45 %. Берём величину из книги как есть —
    # воспроизводимость опубликованных значений важнее точности деления.
    "emission_sources": "per_area_k",
    "emissions_pc": "identity",
    "emissions_per_area": "per_area",
}


@dataclass
class UROfficialModel:
    """Справочник официального индекса: данные, веса и границы нормирования."""

    years: list[int]
    territories: list[str]
    bounds_edition: str
    blocks: dict[str, dict]
    population_thousands: dict[str, list[float | None]]
    area_km2: dict[str, list[float | None]]
    area_thousand_km2: dict[str, list[float | None]]
    published: dict[str, dict[str, list[float | None]]]
    source_file: str = ""
    clipped: list[str] = field(default_factory=list)

    def weight(self, block: str, indicator: str) -> float:
        return self.blocks[block]["indicators"][indicator]["weight"]


def load_model(path: Path | None = None) -> UROfficialModel:
    """Загрузка справочника, собранного `scripts/import_ur_workbook.py`."""
    ref = json.loads((path or _REF_FILE).read_text(encoding="utf-8"))
    aux_pop = ref["aux"]["population"]
    aux_area = ref["aux"]["area"]
    return UROfficialModel(
        years=ref["years_window"],
        territories=ref["territories"],
        bounds_edition=ref["bounds_edition"],
        blocks=ref["blocks"],
        population_thousands=aux_pop.get("series_scaled") or aux_pop["series"],
        area_km2=aux_area["series"],
        area_thousand_km2=aux_area.get("series_scaled") or {},
        published=ref["published_indices"],
        source_file=ref.get("source_file", ""),
    )


def _transform(
    kind: str,
    raw: float | None,
    population_k: float | None,
    area: float | None,
    second: float | None,
    area_k: float | None = None,
) -> float | None:
    """Приведение сырого значения к величине, к которой применяется нормирование."""
    if raw is None:
        return None
    if kind == "identity":
        return raw
    if kind == "per_mille":
        return raw / population_k if population_k else None
    if kind == "per_10k":
        return raw / population_k / 10 if population_k else None
    if kind == "per_capita_k":
        return raw / population_k * 1000 if population_k else None
    if kind == "per_capita":
        return raw / population_k if population_k else None
    if kind == "per_area":
        return raw / area if area else None
    if kind == "per_area_k":
        return raw / area_k if area_k else None
    if kind == "ratio_pair":
        return raw / second if second else None
    raise ValueError(f"Неизвестное приведение: {kind}")


def normalize(value: float | None, bounds: dict) -> tuple[float | None, bool]:
    """Нормирование по фиксированным границам. Возвращает (значение, усечено ли)."""
    if value is None:
        return None, False
    low, high = bounds["low"], bounds["high"]
    if high == low:
        return None, False
    x = (value - low) / (high - low)
    clipped = x < 0.0 or x > 1.0
    x = min(1.0, max(0.0, x))
    return (x if bounds["polarity"] > 0 else 1.0 - x), clipped


def compute_indicator(
    model: UROfficialModel, block: str, indicator: str, territory: str,
) -> dict:
    """Ряд одного показателя: сырое → приведённое → нормированное."""
    spec = model.blocks[block]["indicators"][indicator]
    kind = TRANSFORMS[indicator]
    raw = spec["raw"].get(territory, [])
    # Для отношения двух рядов одного листа второй ряд лежит в промежуточном
    # блоке той же таблицы (зарплата в первом, прожиточный минимум во втором).
    second_series = spec.get("second_series", {}).get(territory)
    pop = model.population_thousands.get(territory, [])
    area = model.area_km2.get(territory, [])
    area_k = model.area_thousand_km2.get(territory, [])

    transformed, normalized, clipped_years = [], [], []
    for i, _year in enumerate(model.years):
        r = raw[i] if i < len(raw) else None
        p = pop[i] if i < len(pop) else None
        a = area[i] if i < len(area) else None
        ak = area_k[i] if i < len(area_k) else None
        s = second_series[i] if second_series and i < len(second_series) else None
        if kind == "ratio_pair":
            # Готовое отношение уже посчитано в книге — берём его из
            # промежуточного блока, чтобы не подменять источник.
            value = spec["pre_normalized"].get(territory, [None] * len(model.years))[i]
        else:
            value = _transform(kind, r, p, a, s, ak)
        transformed.append(value)
        norm, was_clipped = normalize(value, spec["bounds"])
        normalized.append(norm)
        if was_clipped:
            clipped_years.append(model.years[i])

    return {
        "raw": raw,
        "transformed": transformed,
        "normalized": normalized,
        "clipped_years": clipped_years,
    }


def compute_blocks(model: UROfficialModel, territory: str) -> dict[str, list[float | None]]:
    """Индексы трёх блоков и итоговый по одной территории."""
    per_block: dict[str, list[float | None]] = {}
    for block in BLOCKS:
        acc = [0.0] * len(model.years)
        valid = [True] * len(model.years)
        for indicator in model.blocks[block]["indicators"]:
            series = compute_indicator(model, block, indicator, territory)["normalized"]
            weight = model.weight(block, indicator)
            for i, v in enumerate(series):
                if v is None:
                    valid[i] = False
                else:
                    acc[i] += v * weight
        per_block[block] = [acc[i] if valid[i] else None for i in range(len(model.years))]

    total = []
    for i in range(len(model.years)):
        values = [per_block[b][i] for b in BLOCKS]
        total.append(sum(values) / len(BLOCKS) if all(v is not None for v in values) else None)
    per_block["total"] = total
    return per_block


def compute_all(model: UROfficialModel) -> dict[str, dict[str, list[float | None]]]:
    """Индексы по всем территориям: {территория: {блок: ряд}}."""
    return {t: compute_blocks(model, t) for t in model.territories}


def region_value(
    model: UROfficialModel,
    per_territory: dict[str, dict[str, list[float | None]]],
    block: str,
    weighted_by_population: bool = False,
) -> list[float | None]:
    """Областное значение блока.

    По методике — невзвешенное среднее по территориям. Взвешенный по населению
    ряд считается по запросу и в официальные значения не входит: он справочный
    (замечание 10: способ подтверждён заказчиком 30.09.2026, оговорка — в модуле).
    """
    out: list[float | None] = []
    for i in range(len(model.years)):
        values, weights = [], []
        for territory in model.territories:
            v = per_territory[territory][block][i]
            if v is None:
                continue
            values.append(v)
            pop = model.population_thousands.get(territory, [])
            weights.append(pop[i] if weighted_by_population and i < len(pop) else 1.0)
        if not values or (weighted_by_population and not any(weights)):
            out.append(None)
        else:
            out.append(sum(v * w for v, w in zip(values, weights)) / sum(weights))
    return out


def verify_against_published(model: UROfficialModel) -> dict:
    """Сверка расчёта с опубликованными значениями книги.

    Критерий приёмки задан в плане работ: средняя абсолютная ошибка не выше
    0,001 и максимальное отклонение не выше 0,005 по каждому блоку. Расхождение
    больше означает, что методика воспроизведена неверно, и разбираться нужно с
    реализацией, а не подгонять пороги.
    """
    computed = compute_all(model)
    report: dict[str, dict] = {}
    for block in (*BLOCKS, "total"):
        published = model.published.get(block, {})
        errors: list[float] = []
        worst = {"abs_error": 0.0}
        for territory in model.territories:
            pub = published.get(territory, [])
            calc = computed[territory][block]
            for i, year in enumerate(model.years):
                p = pub[i] if i < len(pub) else None
                c = calc[i] if i < len(calc) else None
                if p is None or c is None:
                    continue
                err = abs(p - c)
                errors.append(err)
                if err > worst["abs_error"]:
                    worst = {
                        "abs_error": err, "territory": territory,
                        "year": year, "published": p, "computed": c,
                    }
        report[block] = {
            "n": len(errors),
            "mae": sum(errors) / len(errors) if errors else None,
            "max_abs_error": max(errors) if errors else None,
            "worst": worst if errors else None,
        }
    return report


def verify_transforms(model: UROfficialModel, rel_tol: float = 1e-6) -> dict:
    """Сверка наших приведений с промежуточными значениями книги.

    Спецификации приведений (`TRANSFORMS`) выписаны по формулам вручную, и без
    этой проверки они остались бы непроверенным допущением: ошибка в множителе
    прошла бы незамеченной, потому что итоговый индекс всё равно посчитался бы.
    """
    report: dict[str, dict] = {}
    for block in BLOCKS:
        for indicator, spec in model.blocks[block]["indicators"].items():
            if TRANSFORMS[indicator] == "ratio_pair":
                report[indicator] = {"skipped": "значение берётся из книги"}
                continue
            mismatches, checked = [], 0
            for territory in model.territories:
                expected = spec["pre_normalized"].get(territory, [])
                got = compute_indicator(model, block, indicator, territory)["transformed"]
                for i, year in enumerate(model.years):
                    e = expected[i] if i < len(expected) else None
                    g = got[i] if i < len(got) else None
                    if e is None or g is None:
                        continue
                    checked += 1
                    if abs(e - g) > max(abs(e) * rel_tol, 1e-9):
                        mismatches.append(
                            {"territory": territory, "year": year, "expected": e, "got": g}
                        )
            report[indicator] = {
                "checked": checked,
                "mismatches": len(mismatches),
                "examples": mismatches[:3],
            }
    return report


def clipping_report(model: UROfficialModel) -> list[dict]:
    """Где значения выходили за границы нормирования и были усечены."""
    out = []
    for block in BLOCKS:
        for indicator in model.blocks[block]["indicators"]:
            for territory in model.territories:
                years = compute_indicator(model, block, indicator, territory)["clipped_years"]
                if years:
                    out.append(
                        {"block": block, "indicator": indicator,
                         "territory": territory, "years": years}
                    )
    return out


# ── Сценарный расчёт ──────────────────────────────────────────────────

# Показатель, через который землепользование вообще воздействует на официальный
# индекс. Он единственный: остальные тринадцать к структуре угодий отношения не
# имеют. Отсюда и величина эффекта — вес 0,11 внутри экономического блока даёт
# 3,7 % итогового индекса, что было содержанием нашего ключевого замечания.
LAND_LINKED_INDICATOR = ("economic", "agri_pc")


def scenario_delta(
    model: UROfficialModel,
    base_year: int,
    agri_output_rel: float,
    uniform_across_territories: bool = True,
) -> dict:
    """Изменение официального индекса при относительном изменении продукции СХ.

    Пересчёт точный: валовая продукция на душу умножается на (1 + изменение),
    величина заново нормируется по фиксированным границам, пересобираются
    экономический блок и итоговый индекс, затем берётся среднее по территориям.
    Прежняя реализация линеаризовала эффект вокруг базы — теперь в этом нет
    нужды, потому что конструкция индекса известна.

    **Порядок расчёта закреплён методикой** (ответ на замечание 12, отчёт НИР
    в редакции 30.09.2026): те же фиксированные границы, усечение в [0; 1],
    расчёт по каждой территории, затем изменение среднего по 14 территориям
    относительно базы. Реализация ему соответствует.

    **Остаётся одно допущение.** Методика требует сценарных значений по каждой
    территории, а вход алгоритма ЗБ-УР — областной. Здесь относительное
    изменение продукции одинаково для всех территорий. Для эффекта,
    сосредоточенного в одном районе, итог из-за невзвешенного усреднения будет
    примерно в четырнадцать раз меньше. Допущение помечается полем `assumption`
    и снимается, когда варианты будут заданы по районам.
    """
    block, indicator = LAND_LINKED_INDICATOR
    spec = model.blocks[block]["indicators"][indicator]
    bounds = spec["bounds"]
    weight = spec["weight"]
    year_idx = model.years.index(base_year)

    base_totals, scen_totals = [], []
    base_econ, scen_econ = [], []
    clipped_territories: list[str] = []

    for territory in model.territories:
        blocks_base = compute_blocks(model, territory)
        econ_base = blocks_base["economic"][year_idx]
        total_base = blocks_base["total"][year_idx]
        if econ_base is None or total_base is None:
            continue

        current = compute_indicator(model, block, indicator, territory)
        value = current["transformed"][year_idx]
        norm_base = current["normalized"][year_idx]
        if value is None or norm_base is None:
            continue

        norm_scen, clipped = normalize(value * (1.0 + agri_output_rel), bounds)
        if clipped:
            clipped_territories.append(territory)
        if norm_scen is None:
            continue

        econ_scen = econ_base + (norm_scen - norm_base) * weight
        total_scen = total_base + (econ_scen - econ_base) / len(BLOCKS)

        base_econ.append(econ_base)
        scen_econ.append(econ_scen)
        base_totals.append(total_base)
        scen_totals.append(total_scen)

    if not base_totals:
        return {"applicable": 0.0, "reason": "нет данных за базовый год"}

    n = len(base_totals)
    d_econ = sum(scen_econ) / n - sum(base_econ) / n
    d_total = sum(scen_totals) / n - sum(base_totals) / n

    return {
        "applicable": 1.0,
        "social": 0.0,
        "economic": d_econ,
        "ecological": 0.0,
        "total": d_total,
        "base_total": sum(base_totals) / n,
        "scenario_total": sum(scen_totals) / n,
        "territories_used": n,
        "clipped_territories": clipped_territories,
        "assumption": (
            "Порядок расчёта — по методике (ответ на замечание 12): по каждой "
            "территории, затем среднее по 14. Допущение: областное изменение "
            "продукции СХ задано одинаковым для всех территорий; для эффекта, "
            "сосредоточенного в одном районе, итог будет примерно в 14 раз меньше."
        ) if uniform_across_territories else None,
    }


def mde_official(model: UROfficialModel, sigma_fraction: float = 0.5) -> dict[str, float]:
    """Порог различимости по опубликованным рядам официального индекса.

    Половина стандартного отклонения годовых приращений областного значения —
    то же правило, что и для индекса DSS, чтобы пороги были сопоставимы.
    """
    import statistics

    per_territory = compute_all(model)
    out: dict[str, float] = {}
    for block in (*BLOCKS, "total"):
        series = [v for v in region_value(model, per_territory, block) if v is not None]
        if len(series) < 3:
            continue
        diffs = [series[i] - series[i - 1] for i in range(1, len(series))]
        sigma = statistics.stdev(diffs)
        out[block] = sigma_fraction * sigma
        out[f"{block}_sigma"] = sigma
    return out
