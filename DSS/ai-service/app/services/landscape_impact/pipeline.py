"""Оркестрация алгоритма ЗБ-УР: от варианта землепользования до вердикта.

Два режима работы:
  * `estimate_express` — быстрая оценка (< 2 с): аналитическая свёртка диапазонов
    без полного Монте-Карло. Отвечает на вопрос «как это скажется на росте
    экономики» — дельты ВРП и валовой продукции сельского хозяйства с интервалом.
  * `run_full_assessment` — полный расчёт: вся цепь, Монте-Карло, индексы УР,
    гейты, вердикт и трассировка каждого числа.

Трассировка обязательна: рядом с каждым числом идёт цепочка блоков и меток
[Б]/[С]/[Э], его породивших. Это не украшение отчёта — это единственный способ
показать пользователю, что «эффект варианта +2,3 % ВРП» получен из экспертного
коэффициента, а не измерен.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import numpy as np

from app.services.landscape_impact import agro, economy, sd_index, transitions, uncertainty, verdict
from app.services.landscape_impact.assumptions import (
    assumptions_version,
    get_assumption,
    list_assumptions,
    unverified_keys,
)
from app.services.landscape_impact.calibration import Calibration, calibrate
from app.services.landscape_impact.datamart import (
    LAND_CATEGORIES,
    DataMart,
    load_reference_datamart,
    reference_version,
)
from app.services.landscape_impact.district_panel import (
    DistrictPanel,
    load_district_panel,
    panel_version,
)
from app.services.landscape_impact.gates import EXTRAPOLATION_MARGIN, evaluate_gates

logger = logging.getLogger(__name__)

ALGORITHM_VERSION = "1.1.0"

# Параметры цепи, разыгрываемые в Монте-Карло. Ключ — имя параметра в расчёте,
# значение — источник тройки [min, base, max].
MONTE_CARLO_PARAMETERS: tuple[str, ...] = (
    "arable_share_of_agricultural",
    "sown_share_of_arable",
    "grain_share_of_sown",
    "yield_climate_elasticity_grain",
    "k_suitability_grain",
    "vds_share_agri",
    "multiplier_m",
    "crop_share_of_agri_output",
    "price_ratio_oil_to_grain",
    "price_ratio_fodder_to_grain",
    "livestock_pasture_elasticity",
)


@dataclass
class ScenarioInput:
    """Вход расчёта: вариант землепользования и горизонт."""

    name: str
    target_areas: Optional[dict[str, float]] = None      # тыс. га по категориям
    class_shares: Optional[dict[str, float]] = None      # доли классов растра ЛП
    class_map: Optional[dict[str, str]] = None           # класс ЛП → категория земель
    horizon_years: int = 10
    suitability_shift: float = 0.0                       # сдвиг класса пригодности пашни
    coverage: Optional[float] = None                     # доля валидных пикселей
    base_year: Optional[int] = None
    # [Э] сдвиг угодий внутри земель с/х назначения при полной реализации, тыс. га:
    # {"arable": Δпашни, "pasture": Δпастбищ}. Значения по умолчанию для V1–V3 —
    # из наложения растров вариантов на маски категорий земель (variant_presets).
    land_use_shift: Optional[dict[str, float]] = None
    # Код варианта ЛП (ключ variant_presets). По нему сдвиг пашни раскладывается
    # по районам для гейта G3; прогон варианта ЛП получает неблокирующий G3.
    variant_code: Optional[str] = None

    @property
    def is_lp_variant(self) -> bool:
        return bool(self.variant_code and self.variant_code.startswith("lp-"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "variant_code": self.variant_code,
            "target_areas": self.target_areas,
            "class_shares": self.class_shares, "horizon_years": self.horizon_years,
            "suitability_shift": self.suitability_shift, "coverage": self.coverage,
            "base_year": self.base_year, "land_use_shift": self.land_use_shift,
        }


@dataclass
class AssessmentResult:
    """Результат полного прогона со всей обязательной обвязкой."""

    scenario: dict[str, Any]
    versions: dict[str, str]
    calibration: dict[str, Any]
    transition: dict[str, Any]
    land_balance: dict[str, Any]
    economy: dict[str, Any]
    sd_index: dict[str, Any]
    distributions: dict[str, Any]
    sensitivity: dict[str, float]
    gates: dict[str, Any]
    verdict: dict[str, Any]
    trace: list[dict[str, Any]]
    assumptions: list[dict[str, Any]]
    issues: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario, "versions": self.versions,
            "calibration": self.calibration, "transition": self.transition,
            "land_balance": self.land_balance, "economy": self.economy,
            "sd_index": self.sd_index, "distributions": self.distributions,
            "sensitivity": self.sensitivity, "gates": self.gates,
            "verdict": self.verdict, "trace": self.trace,
            "assumptions": self.assumptions, "issues": self.issues,
            "created_at": self.created_at,
        }


def _assumption_ranges(calib: Calibration) -> dict[str, tuple[float, float, float]]:
    """Диапазоны параметров для Монте-Карло.

    Структурные доли берутся из фактического окна (калибровка), а не из файла
    допущений: это данные, а не мнение. Эластичность урожайности берётся из
    калибровки, если связь прошла гейты, иначе — из размаха jackknife.
    """
    ranges: dict[str, tuple[float, float, float]] = {}
    for key in MONTE_CARLO_PARAMETERS:
        a = get_assumption(key)
        ranges[key] = (a.low, a.base, a.high)

    for key in ("arable_share_of_agricultural", "sown_share_of_arable", "grain_share_of_sown"):
        observed = calib.structural_shares.get(key)
        if observed:
            ranges[key] = (observed["low"], observed["mean"], observed["high"])

    for name, key in (("oil_share_of_sown", "oil_share_of_sown"),
                      ("fodder_share_of_sown", "fodder_share_of_sown"),
                      ("pasture_share_of_agricultural", "pasture_share_of_agricultural")):
        observed = calib.structural_shares.get(name)
        if observed:
            ranges[key] = (observed["low"], observed["mean"], observed["high"])

    if calib.yield_link is not None:
        lo, base, hi = calib.yield_link.expert_range
        ranges["yield_climate_elasticity_grain"] = (min(lo, hi), base, max(lo, hi))
    return ranges


def _lag_factor(horizon_years: int) -> float:
    """Доля эффекта, наступившая к горизонту, с учётом лага по пашне.

    Пашня — угодье с нулевым лагом, поэтому на горизонтах от года эффект
    считается наступившим полностью. Лаги пастбищ, орошения и лесополос войдут
    в расчёт, когда появятся варианты, затрагивающие эти угодья.
    """
    lag = get_assumption("lag_years").value["arable"][1]
    return 1.0 if horizon_years > lag else max(0.0, horizon_years / max(lag, 1))


def _build_transition(mart: DataMart, scenario: ScenarioInput,
                      base_year: int) -> transitions.TransitionMatrix:
    base = mart.land_vector(base_year)
    if scenario.class_shares is not None:
        if not scenario.class_map:
            raise ValueError(
                "Передан растровый вариант, но нет таблицы соответствия «класс ЛП → "
                "категория земель». Таблица запрошена у смежной команды (P0-1); "
                "без неё блок 3 алгоритма не запускается."
            )
        return transitions.from_class_shares(
            base, {}, scenario.class_shares, scenario.class_map)
    if scenario.target_areas is not None:
        return transitions.from_target_areas(base, scenario.target_areas)
    if scenario.land_use_shift:
        # Только сдвиг угодий внутри категории: земельный баланс по категориям не меняется.
        n = len(LAND_CATEGORIES)
        return transitions.TransitionMatrix(matrix=np.zeros((n, n)))
    raise ValueError("Вариант не задан: нужны target_areas, class_shares или land_use_shift.")


def land_use_shift_check(mart: DataMart, base_year: int,
                         shift: Optional[dict[str, float]], fraction: float) -> Optional[dict[str, Any]]:
    """Сдвиг угодий в сравнении с историей: насколько он выходит за наблюдавшийся размах.

    Значения по умолчанию для V1–V3 — потенциал зонирования при полной реализации; они
    кратно больше фактических изменений пашни. Это не ошибка ввода, но пользователь
    должен видеть масштаб рядом с результатом.
    """
    if not shift:
        return None
    applied = {k: round(float(v) * fraction, 1) for k, v in shift.items()}
    arable = mart.land_use.get("arable") if mart.land_use else None
    out: dict[str, Any] = {"full": {k: round(float(v), 1) for k, v in shift.items()},
                           "applied": applied, "realized_fraction": round(fraction, 3)}
    if arable is not None and len(arable) >= 2:
        series = np.asarray(arable, dtype=float)
        series = series[~np.isnan(series)]
        base = float(series[-1])
        hist = float(np.max(np.abs(series - series[0])) / series[0]) if len(series) else 0.0
        share = abs(applied.get("arable", 0.0)) / base if base else 0.0

        def pct(x: float) -> str:
            return f"{x * 100:.1f}".replace(".", ",")

        out.update({
            "arable_share": round(share, 4),
            "historical_max_share": round(hist, 4),
            "exceeds_history": share > hist,
            "note": (
                f"Сдвиг пашни {applied.get('arable', 0.0):+.0f} тыс. га — {pct(share)} % площади пашни; "
                f"за 2014–2024 гг. пашня отклонялась от уровня 2014 г. не более чем на {pct(hist)} %. "
                "Это потенциал зонирования, а не план: результат показывает масштаб, а не ожидаемый эффект."
                if share > hist else
                f"Сдвиг пашни {applied.get('arable', 0.0):+.0f} тыс. га — в пределах наблюдавшегося размаха."
            ),
        })
    return out


_PRESETS_FILE = Path(__file__).parent / "reference" / "lp_variant_presets.json"


@lru_cache(maxsize=1)
def variant_presets() -> dict[str, Any]:
    """Сдвиг угодий по умолчанию для вариантов ЛП V1–V3 (справочник наложения)."""
    return json.loads(_PRESETS_FILE.read_text(encoding="utf-8"))


def _region_key(name: str) -> str:
    """Ключ сопоставления районов: названия в справочнике вариантов и в БД пишутся по-разному."""
    low = name.lower().replace("ё", "е")
    for junk in ("район", "им.", "имени", "г.а.", "г. ", "ауданы"):
        low = low.replace(junk, " ")
    return "".join(ch for ch in low if ch.isalpha())[:6]


def district_shift_check(panel: Optional[DistrictPanel], variant_code: Optional[str],
                         shift: Optional[dict[str, float]],
                         fraction: float) -> Optional[dict[str, Any]]:
    """G3 по районам: сдвиг пашни района против размаха его посевной площади × 1,5.

    Районное распределение берётся из справочника вариантов (наложение растров
    на маски земель) и масштабируется под областной сдвиг, который передал
    пользователь: если он уменьшил сдвиг вдвое, районные сдвиги тоже вдвое меньше.
    """
    if panel is None or not shift or not variant_code:
        return None
    preset = variant_presets()["variants"].get(variant_code)
    if not preset or not preset.get("districts") or not preset.get("arable"):
        return None
    scale = float(shift.get("arable", 0.0)) * fraction / float(preset["arable"])
    ranges = {_region_key(k): (k, v) for k, v in panel.sown_range().items()}
    rows, outside = [], []
    for name, d in preset["districts"].items():
        match = ranges.get(_region_key(name))
        if match is None:
            continue  # город: посевов нет, в панель не входит
        region, rng = match
        arable = float(d["arable"]) * scale
        span = rng["max"] - rng["min"]
        row = {"region": region, "arable": round(arable, 1), "span": round(span, 1),
               "limit": round(span * EXTRAPOLATION_MARGIN, 1),
               "share_of_sown": round(arable / rng["last"], 3) if rng["last"] else None}
        row["inside"] = abs(arable) <= row["limit"]
        rows.append(row)
        if not row["inside"]:
            outside.append(row)
    return {"variant_code": variant_code, "scale": round(scale, 4),
            "districts": rows, "outside": outside,
            "inside_count": len(rows) - len(outside), "total": len(rows)}


def _evaluator(mart: DataMart, base_year: int, base_vector: np.ndarray,
               scen_vector: np.ndarray, scenario: ScenarioInput,
               sd_model: sd_index.SDIndexModel, lag: float, fraction: float = 1.0):
    """Замыкание расчёта цепи для одной реализации параметров."""
    base_yields = agro.calibrate_base_yields(mart, base_year)
    # Сдвиг угодий реализуется по тому же графику внедрения, что и переходы категорий.
    shift = ({k: float(v) * fraction for k, v in scenario.land_use_shift.items()}
             if scenario.land_use_shift else None)
    i = mart.year_index(base_year)
    base_agri = float(mart.economy["agri_output"][i])
    base_grp = float(mart.economy["grp"][i])
    bounds = sd_model.reference_bounds

    def evaluate(params: dict[str, float]) -> dict[str, float]:
        base_state = agro.compute_agro(base_vector, base_yields, params,
                                       climate_ratio=1.0, suitability_shift=0.0)
        scen_state = agro.compute_agro(scen_vector, base_yields, params,
                                       climate_ratio=1.0,
                                       suitability_shift=scenario.suitability_shift,
                                       land_use_shift=shift)
        eco = economy.compute_economy(scen_state, base_state, params,
                                      base_agri, base_grp, lag_factor=lag)

        base_components = {
            "agri_output": base_agri, "grp": base_grp,
            "yield_grain": base_yields["grain"],
            "arable": base_state.land_use.arable, "pasture": base_state.land_use.pasture,
            "protected": float(base_vector[LAND_CATEGORIES.index("protected")]),
            "forest": float(base_vector[LAND_CATEGORIES.index("forest")]),
            "reserve": float(base_vector[LAND_CATEGORIES.index("reserve")]),
        }
        scen_components = dict(base_components)
        scen_components.update({
            "agri_output": base_agri + eco.agri_output_abs,
            "grp": base_grp + eco.grp_abs,
            "yield_grain": scen_state.yields["grain"],
            "arable": scen_state.land_use.arable, "pasture": scen_state.land_use.pasture,
            "protected": float(scen_vector[LAND_CATEGORIES.index("protected")]),
            "forest": float(scen_vector[LAND_CATEGORIES.index("forest")]),
            "reserve": float(scen_vector[LAND_CATEGORIES.index("reserve")]),
        })
        base_idx = sd_index.dss_index(base_components, bounds)
        scen_idx = sd_index.dss_index(scen_components, bounds)
        official = sd_index.official_index_delta(
            mart, base_year, eco.grp_rel, eco.agri_output_rel, sd_model.mode)

        return {
            "agri_output_rel": eco.agri_output_rel,
            "agri_output_abs": eco.agri_output_abs,
            "grp_rel": eco.grp_rel,
            "grp_abs": eco.grp_abs,
            "dss_total": scen_idx["total"] - base_idx["total"],
            "dss_social": scen_idx["social"] - base_idx["social"],
            "dss_economic": scen_idx["economic"] - base_idx["economic"],
            "dss_ecological": scen_idx["ecological"] - base_idx["ecological"],
            "official_total": official["total"],
            "official_economic": official["economic"],
        }

    return evaluate


def _trace(sd_model: sd_index.SDIndexModel, calib: Calibration) -> list[dict[str, Any]]:
    """Трассировка: какая метка типа связи стоит за каждым выходным числом."""
    yield_type = calib.yield_link.link_type if calib.yield_link else "Э"
    return [
        {"quantity": "Матрица переходов площадей", "chain": ["Блок 3"], "labels": ["Б"],
         "note": "Нулевая сумма площадей — тождество, проверенное на всех годах окна."},
        {"quantity": "Пашня, пастбища, посевные площади", "chain": ["Блок 3", "Блок 4"],
         "labels": ["Б"],
         "note": "Балансовые доли оценены как средние по окну 2014-2024."},
        {"quantity": "Урожайность зерновых", "chain": ["Блок 4", "Блок 5"],
         "labels": [yield_type],
         "note": ("Связь с климатом прошла гейты и оценена статистически"
                  + (" на районной панели." if (calib.yield_link and calib.yield_link.source)
                     else ".")
                  if yield_type == "С" else
                  "Связь с климатом НЕ прошла гейты значимости и скилла; используется "
                  "экспертный диапазон из размаха jackknife.")},
        {"quantity": "Валовой сбор", "chain": ["Блок 5"], "labels": ["Б"],
         "note": "Сбор = площадь × урожайность — тождество."},
        {"quantity": "Валовая продукция сельского хозяйства", "chain": ["Блок 5"],
         "labels": ["Б", "Э"],
         "note": "Веса выручки по культурам — экспертные относительные цены; уровень цен сокращается."},
        {"quantity": "ВРП", "chain": ["Блок 5", "Блок 6"], "labels": ["Э"],
         "note": "Доля ВДС и мультипликатор смежных отраслей — экспертные диапазоны."},
        {"quantity": "Индексы устойчивого развития", "chain": ["Блок 7"],
         "labels": ["Э"] if sd_model.mode == "B" else ["С", "Э"],
         "note": sd_model.note},
    ]


def run_full_assessment(scenario: ScenarioInput, *, mart: Optional[DataMart] = None,
                        draws: int = uncertainty.DEFAULT_DRAWS,
                        seed: int = uncertainty.DEFAULT_SEED) -> AssessmentResult:
    """Полный прогон алгоритма ЗБ-УР по одному варианту."""
    mart = mart or load_reference_datamart()
    calib = calibrate(mart)
    base_year = scenario.base_year or mart.years[-1]
    base_vector = mart.land_vector(base_year)

    tm = _build_transition(mart, scenario, base_year)
    schedule = get_assumption("implementation_schedule").value
    fraction = transitions.realized_fraction(
        scenario.horizon_years, float(schedule["years_to_full"][1]), schedule["shape"])
    scen_vector = tm.apply(base_vector, fraction)

    sd_model = sd_index.build_model(mart, base_year)
    lag = _lag_factor(scenario.horizon_years)
    evaluate = _evaluator(mart, base_year, base_vector, scen_vector, scenario, sd_model, lag, fraction)

    ranges = _assumption_ranges(calib)
    draws_list = uncertainty.sample_parameters(ranges, draws=draws, seed=seed)
    dists = uncertainty.propagate(evaluate, draws_list)

    base_params = {k: v[1] for k, v in ranges.items()}
    sens = uncertainty.sensitivity(evaluate, base_params, ranges, target="dss_total")
    # G10 спрашивает про ЭКСПЕРТНЫЕ коэффициенты. Структурные доли, оценённые по
    # фактическому окну, — это данные, и их вклад в разброс ограничивать нечем.
    expert_keys = {k for k in ranges if k in list_assumptions()
                   and not list_assumptions()[k].verified}
    expert_sens = {k: v for k, v in sens.items() if k in expert_keys}

    # Порог различимости берётся по индексу DSS независимо от режима: вердикт по
    # вариантам землепользования строится на нём. Официальный индекс к структуре
    # угодий почти нечувствителен по построению, и сравнивать эффект с его
    # порогом бессмысленно — получится «не подтверждено» при любом варианте.
    mde, mde_sigma = _dss_mde(mart, calib, base_year)

    total_dist = dists["dss_total"]
    width_ratio_ok = verdict.width_ok(total_dist, mde)

    panel = load_district_panel()
    district_check = district_shift_check(panel, scenario.variant_code,
                                          scenario.land_use_shift, fraction)
    gates = evaluate_gates(
        mart=mart, calibration=calib, transition=tm, sd_model=sd_model,
        sensitivity_shares=expert_sens or sens, width_ratio_ok=width_ratio_ok, reproducible=True,
        coverage=scenario.coverage, unverified_assumptions=unverified_keys(),
        district_check=district_check, lp_variant=scenario.is_lp_variant,
    )
    v = verdict.decide(
        total=total_dist,
        ecological=dists.get("dss_ecological"),
        mde=mde, mde_sigma=mde_sigma,
        blocking_gates_passed=gates.blocking_passed,
        eco_veto_probability=get_assumption("eco_veto_probability").base,
        index_source="индекс DSS",
    )

    return AssessmentResult(
        scenario=scenario.as_dict(),
        versions={
            "algorithm": ALGORITHM_VERSION,
            "assumptions": assumptions_version(),
            "datamart": reference_version(),
            "district_panel": str(panel_version()),
            "dss_index": sd_index.index_version(),
            "seed": str(seed), "draws": str(draws),
            "base_year": str(base_year),
            "realized_fraction": f"{fraction:.3f}",
        },
        calibration=calib.as_dict(),
        transition={**tm.as_dict(),
                    "land_use_shift": land_use_shift_check(mart, base_year, scenario.land_use_shift, fraction),
                    "district_check": district_check},
        land_balance={
            "base": {c: round(float(base_vector[i]), 2) for i, c in enumerate(LAND_CATEGORIES)},
            "scenario": {c: round(float(scen_vector[i]), 2) for i, c in enumerate(LAND_CATEGORIES)},
            "sources_of_growth": {
                c: tm.sources_of(c) for i, c in enumerate(LAND_CATEGORIES) if tm.net[i] > 1e-6
            },
        },
        economy={
            "agri_output_rel": dists["agri_output_rel"].as_dict(),
            "agri_output_abs": dists["agri_output_abs"].as_dict(),
            "grp_rel": dists["grp_rel"].as_dict(),
            "grp_abs": dists["grp_abs"].as_dict(),
        },
        sd_index={
            "model": sd_model.as_dict(),
            "deltas": {k: dists[k].as_dict() for k in
                       ("dss_total", "dss_social", "dss_economic", "dss_ecological",
                        "official_total", "official_economic") if k in dists},
            "mde": round(mde, 6), "mde_sigma": round(mde_sigma, 6),
            # Порог различимости у двух индексов свой: он выводится из
            # собственных годовых приращений каждого. Один порог на оба сравнивал
            # бы эффект с чужим шумом.
            "official_mde": _official_mde(),
            "index_roles": {
                "dss": (
                    "Индекс DSS — инструмент сравнения вариантов землепользования: "
                    "в его составе площади категорий земель, угодья и урожайность."
                ),
                "official": (
                    "Официальный индекс — инструмент мониторинга по методике "
                    "заказчика. Землепользование затрагивает в нём один показатель "
                    "из четырнадцати, поэтому вердикт по вариантам на нём не строится."
                ),
            },
        },
        distributions={k: d.as_dict() for k, d in dists.items()},
        sensitivity=sens,
        gates=gates.as_dict(),
        verdict=v.as_dict(),
        trace=_trace(sd_model, calib),
        assumptions=[a.as_dict() for a in list_assumptions().values()],
        issues=list(mart.issues),
        created_at=datetime.now(timezone.utc).isoformat(),
    )



def _official_mde() -> Optional[dict[str, float]]:
    """Порог различимости официального индекса по его же опубликованным рядам.

    Возвращает None, если расчётной книги заказчика нет: тогда официального
    индекса не существует и сравнивать не с чем.
    """
    try:
        from app.services.landscape_impact import ur_official
        model = ur_official.load_model()
    except (FileNotFoundError, ImportError):
        return None
    mde = ur_official.mde_official(model)
    return {k: round(v, 6) for k, v in mde.items()}


def _dss_mde(mart: DataMart, calib: Calibration,
             base_year: Optional[int] = None) -> tuple[float, float]:
    """MDE для индекса DSS.

    Индекс заказчика и индекс DSS живут в разных шкалах, поэтому переносить MDE
    напрямую нельзя. Берётся та же логика — половина σ годовых приращений, — но
    посчитанная на историческом ряду самого индекса DSS.
    """
    model = sd_index.build_model(mart, base_year)
    bounds = model.reference_bounds
    series = []
    for i, _year in enumerate(mart.years):
        components = {
            "agri_output": float(mart.economy["agri_output"][i]),
            "grp": float(mart.economy["grp"][i]),
            "yield_grain": float(mart.yields["grain"][i]),
            "arable": float(mart.land_use["arable"][i]),
            "pasture": float(mart.land_use["pasture"][i]),
            "protected": float(mart.land_balance["protected"][i]),
            "forest": float(mart.land_balance["forest"][i]),
            "reserve": float(mart.land_balance["reserve"][i]),
        }
        if any(v != v for v in components.values()):
            continue
        series.append(sd_index.dss_index(components, bounds)["total"])
    if len(series) < 3:
        return calib.mde["total"], calib.mde["total_sigma"]
    d = np.diff(np.asarray(series, dtype=float))
    sigma = float(d.std(ddof=1))
    return float(get_assumption("mde_sigma_fraction").base * sigma), sigma


def estimate_express(scenario: ScenarioInput, *, mart: Optional[DataMart] = None) -> dict[str, Any]:
    """Экспресс-оценка влияния на рост экономики: дельты ВРП и ВП СХ с интервалом.

    Отвечает на второй вопрос заказчика — «как это скажется на росте экономики».
    Вместо полного Монте-Карло берётся аналитическая свёртка границ экспертных
    диапазонов: три прогона цепи (min / base / max) вместо пяти тысяч. Ответ за
    доли секунды, интервал честный, но без вероятностей — для вероятностей
    нужен полный прогон.
    """
    mart = mart or load_reference_datamart()
    calib = calibrate(mart)
    base_year = scenario.base_year or mart.years[-1]
    base_vector = mart.land_vector(base_year)
    tm = _build_transition(mart, scenario, base_year)
    schedule = get_assumption("implementation_schedule").value
    fraction = transitions.realized_fraction(
        scenario.horizon_years, float(schedule["years_to_full"][1]), schedule["shape"])
    scen_vector = tm.apply(base_vector, fraction)

    sd_model = sd_index.build_model(mart, base_year)
    evaluate = _evaluator(mart, base_year, base_vector, scen_vector, scenario, sd_model,
                          _lag_factor(scenario.horizon_years), fraction)
    ranges = _assumption_ranges(calib)

    # Границы берутся не по «все параметры разом в минимум»: при разнонаправленном
    # влиянии такой угол не является ни минимумом, ни максимумом выхода. Считаем
    # 2K + 1 точку — каждый параметр по очереди на своей границе при остальных на
    # базе — и берём огибающую. Для монотонного по каждому параметру отклика это
    # даёт корректный диапазон, а стоит 23 прогона цепи вместо пяти тысяч.
    base_params = {k: v[1] for k, v in ranges.items()}
    probes: list[dict[str, float]] = [base_params]
    for key, (low, _base, high) in ranges.items():
        probes.append(dict(base_params, **{key: low}))
        probes.append(dict(base_params, **{key: high}))
    outputs = [evaluate(p) for p in probes]
    base_output = outputs[0]

    def band(key: str) -> dict[str, float]:
        xs = [o[key] for o in outputs]
        return {"base": round(base_output[key], 6),
                "low": round(min(xs), 6), "high": round(max(xs), 6)}

    return {
        "scenario": scenario.as_dict(),
        "versions": {"algorithm": ALGORITHM_VERSION, "assumptions": assumptions_version(),
                     "datamart": reference_version(), "district_panel": str(panel_version()),
                     "dss_index": sd_index.index_version(),
                     "base_year": str(base_year),
                     "realized_fraction": f"{fraction:.3f}"},
        "land_balance_delta": {c: round(float(scen_vector[i] - base_vector[i]), 2)
                               for i, c in enumerate(LAND_CATEGORIES)},
        "agri_output": {**band("agri_output_abs"), "unit": "млрд тенге", "link": "Б+Э"},
        "agri_output_rel": {**band("agri_output_rel"), "unit": "доля", "link": "Б+Э"},
        "grp": {**band("grp_abs"), "unit": "млрд тенге", "link": "Э"},
        "grp_rel": {**band("grp_rel"), "unit": "доля", "link": "Э"},
        "transition_violations": tm.violations,
        "land_use_shift_check": land_use_shift_check(mart, base_year, scenario.land_use_shift, fraction),
        "district_check": district_shift_check(load_district_panel(), scenario.variant_code,
                                               scenario.land_use_shift, fraction),
        "disclaimer": (
            "Экспресс-оценка: контрфактическая разница относительно базового сценария "
            "(инерции) при заданных экспертных допущениях, а не прогноз абсолютных "
            "значений. Вероятностные характеристики и вердикт по устойчивому развитию "
            "даёт полный прогон."
        ),
        "unverified_assumptions": unverified_keys(),
    }
