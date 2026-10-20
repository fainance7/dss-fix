"""Шаг 1 алгоритма ЗБ-УР: калибровка на историческом окне 2014-2024.

Это то, что заказчик описал как «на этом примере, с 2014 по 2024 год, вырабатываем
алгоритмы». Модуль не подбирает модель перебором: список кандидатов фиксирован до
расчёта, оценка ведётся в первых разностях логарифмов, каждая связь проходит
обязательные проверки и получает метку [С] только если прошла их все. Не прошедшая
связь не выбрасывается — она понижается до [Э] с диапазоном из jackknife, и этот
факт доходит до отчёта прогона и до интерфейса.

Правила при n = 11 (обоснование — docs/Алгоритм_ЗБ_УР_2026-09-09.md, раздел 4):
  * оценка в первых разностях логарифмов, не в уровнях: в уровнях любые два
    трендовых ряда коррелируют с |r| > 0.9 из-за общего тренда, а не связи;
  * не более одного регрессора на уравнение;
  * список кандидатов фиксирован заранее, stepwise и «топ-N по корреляции» запрещены;
  * валидация — скользящее начало, не LOO (LOO на трендовых рядах оптимистичен);
  * множественность контролируется поправкой Бенджамини-Хохберга.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np

from app.services.landscape_impact.datamart import LAND_CATEGORIES, DataMart

logger = logging.getLogger(__name__)

# Кандидаты в регрессоры уравнения урожайности. Список закрыт: расширять его
# после просмотра результатов означает аннулировать p-значения.
YIELD_CLIMATE_CANDIDATES: tuple[str, ...] = ("gtk_5_8", "precip_5_8", "sum_t10")

# Априорные знаки коэффициентов (гейт G7). Больше влаги — выше урожай; больше
# суммы активных температур при дефиците влаги — засуха, урожай ниже.
PRIOR_SIGNS: dict[str, int] = {"gtk_5_8": +1, "precip_5_8": +1, "sum_t10": -1}

PERMUTATIONS = 20_000
RANDOM_SEED = 20260909
MIN_TRAIN_POINTS = 5
JACKKNIFE_MAX_DEVIATION = 0.50   # G5: изменение коэффициента при выбросе года
SIGNIFICANCE_ALPHA = 0.05        # G6
MASE_THRESHOLD = 1.0             # G4


@dataclass
class LinkEstimate:
    """Оценка одной связи с полным протоколом проверок."""

    name: str
    regressor: str
    slope: float
    intercept: float
    r: float
    r2: float
    p_permutation: float
    p_adjusted: float
    mase: float
    skill: float
    jackknife_min: float
    jackknife_max: float
    jackknife_sign_stable: bool
    jackknife_max_deviation: float
    prior_sign_ok: bool
    n_obs: int
    gates: dict[str, bool] = field(default_factory=dict)
    source: Optional[dict[str, Any]] = None   # откуда оценка: областной ряд или районная панель

    @property
    def passed(self) -> bool:
        return all(self.gates.values())

    @property
    def link_type(self) -> str:
        """[С] — если связь прошла все гейты; иначе понижается до [Э]."""
        return "С" if self.passed else "Э"

    @property
    def expert_range(self) -> tuple[float, float, float]:
        """Диапазон для использования связи как экспертной: размах jackknife."""
        return (self.jackknife_min, self.slope, self.jackknife_max)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "regressor": self.regressor,
            "slope": round(self.slope, 5), "intercept": round(self.intercept, 5),
            "r": round(self.r, 4), "r2": round(self.r2, 4),
            "p_permutation": round(self.p_permutation, 4),
            "p_adjusted": round(self.p_adjusted, 4),
            "mase": round(self.mase, 4), "skill": round(self.skill, 4),
            "jackknife": {
                "min": round(self.jackknife_min, 4), "max": round(self.jackknife_max, 4),
                "sign_stable": self.jackknife_sign_stable,
                "max_deviation": round(self.jackknife_max_deviation, 4),
            },
            "prior_sign_ok": self.prior_sign_ok, "n_obs": self.n_obs,
            "gates": self.gates, "passed": self.passed, "link_type": self.link_type,
            "source": self.source or {"kind": "oblast_series"},
        }


@dataclass
class Calibration:
    """Результат шага 1: всё, что алгоритм извлёк из окна 2014-2024."""

    window: tuple[int, int]
    balance_ok: bool
    balance_problems: list[dict[str, Any]]
    structural_shares: dict[str, dict[str, float]]
    yield_link: Optional[LinkEstimate]
    yield_candidates: list[LinkEstimate]
    yield_candidates_oblast: list[LinkEstimate]
    direct_land_links: dict[str, Any]
    sd_aggregation: dict[str, Any]
    mde: dict[str, float]
    vds_share_observed: dict[str, float]
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "window": list(self.window),
            "balance_ok": self.balance_ok,
            "balance_problems": self.balance_problems,
            "structural_shares": self.structural_shares,
            "yield_link": self.yield_link.as_dict() if self.yield_link else None,
            "yield_candidates": [c.as_dict() for c in self.yield_candidates],
            "yield_candidates_oblast": [c.as_dict() for c in self.yield_candidates_oblast],
            "direct_land_links": self.direct_land_links,
            "sd_aggregation": self.sd_aggregation,
            "mde": self.mde,
            "vds_share_observed": self.vds_share_observed,
            "notes": self.notes,
        }


# ─────────────────────────── вспомогательная статистика ───────────────────────────

def _dlog(x: np.ndarray) -> np.ndarray:
    """Первые разности логарифмов. NaN и неположительные значения выбрасываются."""
    x = np.asarray(x, dtype=float)
    return np.diff(np.log(x))


def _clean_pair(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mask = np.isfinite(x) & np.isfinite(y)
    return x[mask], y[mask]


def _permutation_p(x: np.ndarray, y: np.ndarray, n: int = PERMUTATIONS,
                   seed: int = RANDOM_SEED) -> float:
    """p-значение перестановочного теста для |r|. Не опирается на нормальность.

    Перестановки считаются одной матричной операцией: при 36 парах предикторов и
    целевых переменных поэлементный цикл занимал минуту на прогон, что делало
    калибровку непригодной для синхронного эндпоинта.
    """
    x, y = _clean_pair(np.asarray(x, float), np.asarray(y, float))
    m = len(x)
    if m < 4 or np.std(x) == 0 or np.std(y) == 0:
        return 1.0
    r0 = abs(float(np.corrcoef(x, y)[0, 1]))

    xc = x - x.mean()
    xn = xc / np.sqrt(np.sum(xc * xc))

    rng = np.random.default_rng(seed)
    # Матрица перестановок y: argsort равномерного шума даёт независимые
    # случайные перестановки строк без цикла на Python.
    perms = np.argsort(rng.random((n, m)), axis=1)
    ys = y[perms]                                   # (n, m)
    ys = ys - ys.mean(axis=1, keepdims=True)
    norms = np.sqrt(np.sum(ys * ys, axis=1))
    norms[norms == 0] = np.inf
    r = np.abs(ys @ xn) / norms
    count = int(np.sum(r >= r0 - 1e-12))
    return (count + 1) / (n + 1)


def benjamini_hochberg(pvalues: Sequence[float], alpha: float = SIGNIFICANCE_ALPHA) -> list[float]:
    """Поправка Бенджамини-Хохберга: возвращает скорректированные p-значения."""
    p = np.asarray(list(pvalues), dtype=float)
    m = len(p)
    if m == 0:
        return []
    order = np.argsort(p)
    ranked = p[order] * m / (np.arange(m) + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m, dtype=float)
    out[order] = np.minimum(ranked, 1.0)
    return [float(v) for v in out]


def _rolling_origin_mase(x: np.ndarray, y: np.ndarray,
                         min_train: int = MIN_TRAIN_POINTS) -> tuple[float, float]:
    """Бэктест со скользящим началом против наивного эталона «без изменения».

    Возвращает (MASE, Skill). Наивный эталон в первых разностях — нулевое
    приращение: он и есть «случайное блуждание», честная планка для годового ряда.
    """
    x, y = _clean_pair(np.asarray(x, float), np.asarray(y, float))
    if len(y) <= min_train:
        return float("inf"), float("-inf")
    model_err, naive_err = [], []
    for t in range(min_train, len(y)):
        slope, intercept = np.polyfit(x[:t], y[:t], 1)
        model_err.append(abs(y[t] - (slope * x[t] + intercept)))
        naive_err.append(abs(y[t]))
    mae_model = float(np.mean(model_err))
    mae_naive = float(np.mean(naive_err))
    if mae_naive == 0:
        return float("inf"), float("-inf")
    mase = mae_model / mae_naive
    return mase, 1.0 - mase


def _jackknife_slopes(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    x, y = _clean_pair(np.asarray(x, float), np.asarray(y, float))
    slopes = []
    for i in range(len(x)):
        idx = [j for j in range(len(x)) if j != i]
        slopes.append(np.polyfit(x[idx], y[idx], 1)[0])
    return np.asarray(slopes, dtype=float)


# ─────────────────────────────── шаги калибровки ───────────────────────────────

def estimate_link(name: str, regressor: str, x: np.ndarray, y: np.ndarray,
                  prior_sign: Optional[int] = None) -> LinkEstimate:
    """Оценка одной связи `y ~ x` со всем протоколом проверок (G4-G7)."""
    x, y = _clean_pair(np.asarray(x, float), np.asarray(y, float))
    slope, intercept = (float(v) for v in np.polyfit(x, y, 1))
    r = float(np.corrcoef(x, y)[0, 1])
    pred = slope * x + intercept
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - float(np.sum((y - pred) ** 2)) / ss_tot if ss_tot > 0 else 0.0
    p_perm = _permutation_p(x, y)
    mase, skill = _rolling_origin_mase(x, y)
    jk = _jackknife_slopes(x, y)
    sign_stable = bool(np.all(np.sign(jk) == np.sign(slope))) if slope != 0 else False
    max_dev = float(np.max(np.abs(jk - slope) / abs(slope))) if slope != 0 else float("inf")
    prior_ok = True if prior_sign is None else bool(np.sign(slope) == np.sign(prior_sign))

    est = LinkEstimate(
        name=name, regressor=regressor, slope=slope, intercept=intercept, r=r, r2=r2,
        p_permutation=p_perm, p_adjusted=p_perm,  # уточняется в calibrate() по всей семье
        mase=mase, skill=skill,
        jackknife_min=float(jk.min()), jackknife_max=float(jk.max()),
        jackknife_sign_stable=sign_stable, jackknife_max_deviation=max_dev,
        prior_sign_ok=prior_ok, n_obs=len(x),
    )
    est.gates = {
        "G4_skill": mase < MASE_THRESHOLD and skill > 0,
        "G5_jackknife": sign_stable and max_dev <= JACKKNIFE_MAX_DEVIATION,
        "G6_significance": p_perm < SIGNIFICANCE_ALPHA,
        "G7_prior_sign": prior_ok,
    }
    return est


def _structural_shares(mart: DataMart) -> dict[str, dict[str, float]]:
    """Балансовые доли «категория → угодье → посев» [Б]: среднее и разброс по окну."""
    lb, lu = mart.land_balance, mart.land_use
    pairs = {
        "arable_share_of_agricultural": (lu["arable"], lb["agricultural"]),
        "sown_share_of_arable": (lu["sown_total"], lu["arable"]),
        "grain_share_of_sown": (lu["sown_grain"], lu["sown_total"]),
        "oil_share_of_sown": (lu["sown_oil"], lu["sown_total"]),
        "fodder_share_of_sown": (lu["sown_fodder"], lu["sown_total"]),
        "pasture_share_of_agricultural": (lu["pasture"], lb["agricultural"]),
    }
    out: dict[str, dict[str, float]] = {}
    for key, (num, den) in pairs.items():
        ratio = np.asarray(num, float) / np.asarray(den, float)
        ratio = ratio[np.isfinite(ratio)]
        if ratio.size == 0:
            continue
        sd = float(ratio.std(ddof=1)) if ratio.size > 1 else 0.0
        out[key] = {
            "mean": round(float(ratio.mean()), 4), "sd": round(sd, 4),
            "min": round(float(ratio.min()), 4), "max": round(float(ratio.max()), 4),
            "low": round(float(ratio.mean() - 2 * sd), 4),
            "high": round(float(ratio.mean() + 2 * sd), 4),
            "n": int(ratio.size),
        }
    return out


def _direct_land_links(mart: DataMart) -> dict[str, Any]:
    """Проверка прямой связи «земельный баланс → статистика» в первых разностях.

    Это отрицательный контроль алгоритма: если бы прямая эконометрическая связь
    существовала, структурная цепь была бы не нужна. Проверка обязательна и её
    результат публикуется — он определяет, имеет ли право модуль называть
    результат статистической оценкой.
    """
    targets = {
        "sd_total": np.diff(mart.sd_index["total"]),
        "sd_social": np.diff(mart.sd_index["social"]),
        "sd_economic": np.diff(mart.sd_index["economic"]),
        "sd_ecological": np.diff(mart.sd_index["ecological"]),
        "grp": _dlog(mart.economy["grp"]),
        "agri_output": _dlog(mart.economy["agri_output"]),
    }
    tests: list[dict[str, Any]] = []
    for cat in LAND_CATEGORIES:
        dv = np.diff(mart.land_balance[cat])
        if np.allclose(dv, 0):
            tests.append({"predictor": cat, "target": None, "skipped": "нет вариации"})
            continue
        for tname, tv in targets.items():
            xx, yy = _clean_pair(dv, tv)
            if len(xx) < 4 or np.std(xx) == 0:
                continue
            r = float(np.corrcoef(xx, yy)[0, 1])
            tests.append({
                "predictor": cat, "target": tname, "r": round(r, 3),
                "p_permutation": round(_permutation_p(xx, yy), 4),
            })
    real = [t for t in tests if t.get("p_permutation") is not None]
    adjusted = benjamini_hochberg([t["p_permutation"] for t in real])
    for t, pa in zip(real, adjusted):
        t["p_adjusted"] = round(float(pa), 4)
    survivors = [t for t in real if t["p_adjusted"] < SIGNIFICANCE_ALPHA]
    return {
        "n_tests": len(real),
        "min_p": round(min((t["p_permutation"] for t in real), default=1.0), 4),
        "bonferroni_threshold": round(SIGNIFICANCE_ALPHA / len(real), 5) if real else None,
        "survived_fdr": len(survivors),
        "survivors": survivors,
        "tests": tests,
        "conclusion": (
            "Прямая эконометрическая связь «земельный баланс → показатель» на областном "
            "ряду 2014-2024 не подтверждена ни для одной пары. Оценка эффекта варианта "
            "выполняется структурной цепью (баланс → угодья → урожайность → продукция → "
            "ВРП → индексы УР), а не регрессией показателя на площади категорий."
            if not survivors else
            f"Подтверждено связей после FDR: {len(survivors)}. Требуется проверка на "
            "устойчивость и содержательная интерпретация до включения в расчётную цепь."
        ),
    }


def _sd_aggregation(mart: DataMart) -> dict[str, Any]:
    """Проверка правила межблочной свёртки индекса УР."""
    soc, eco, env = mart.sd_index["social"], mart.sd_index["economic"], mart.sd_index["ecological"]
    total = mart.sd_index["total"]
    arithmetic = (soc + eco + env) / 3.0
    geometric = np.cbrt(soc * eco * env)
    max_ar = float(np.max(np.abs(arithmetic - total)))
    max_ge = float(np.max(np.abs(geometric - total)))
    return {
        "rule": "arithmetic_mean" if max_ar <= 0.001 else "unknown",
        "max_abs_deviation_arithmetic": round(max_ar, 5),
        "max_abs_deviation_geometric": round(max_ge, 5),
        "years_checked": mart.n_years,
        "compensatory": max_ar <= 0.001,
        "note": (
            "Итоговый индекс = среднее арифметическое трёх блоков; правило подтверждено "
            "на всех годах окна. Свёртка компенсаторная: падение одного блока полностью "
            "компенсируется ростом другого, поэтому вердикт дополняется вето по "
            "экологическому блоку."
            if max_ar <= 0.001 else
            "Правило межблочной свёртки на данных не восстанавливается."
        ),
    }


def _mde(mart: DataMart, sigma_fraction: float) -> dict[str, float]:
    """Минимальный содержательный эффект: доля σ годовых приращений индекса."""
    out: dict[str, float] = {}
    for name, series in mart.sd_index.items():
        d = np.diff(np.asarray(series, float))
        d = d[np.isfinite(d)]
        sigma = float(d.std(ddof=1)) if d.size > 1 else 0.0
        out[name] = round(sigma_fraction * sigma, 5)
        out[f"{name}_sigma"] = round(sigma, 5)
    return out


def _oblast_yield_candidates(mart: DataMart) -> list[LinkEstimate]:
    """Кандидаты уравнения урожайности на областном ряду (первые разности логарифмов)."""
    dy = _dlog(mart.yields["grain"])
    candidates: list[LinkEstimate] = []
    for name in YIELD_CLIMATE_CANDIDATES:
        dx = _dlog(mart.climate[name])
        candidates.append(estimate_link(
            name=f"Δln(урожайность зерновых) ~ Δln({name})",
            regressor=name, x=dx, y=dy, prior_sign=PRIOR_SIGNS.get(name),
        ))
    return candidates


def _panel_yield_candidates(panel) -> list[LinkEstimate]:
    """Те же кандидаты на районной панели (эффекты района и года)."""
    from app.services.landscape_impact.district_panel import estimate_panel_link

    return [estimate_panel_link(panel, name, prior_sign=PRIOR_SIGNS.get(name))
            for name in YIELD_CLIMATE_CANDIDATES]


def _adjust_family(candidates: list[LinkEstimate]) -> None:
    adjusted = benjamini_hochberg([c.p_permutation for c in candidates])
    for cand, pa in zip(candidates, adjusted):
        cand.p_adjusted = float(pa)
        cand.gates["G6_significance"] = pa < SIGNIFICANCE_ALPHA


def calibrate(mart: DataMart, *, sigma_fraction: float = 0.5,
              use_district_panel: bool = True) -> Calibration:
    """Шаг 1 целиком: всё, что алгоритм извлекает из окна 2014-2024.

    Уравнение урожайности оценивается на районной панели, если её справочник
    собран: на областном ряду из 10 разностей связь с климатом не отделяется от
    шума. Областные оценки сохраняются в отчёте рядом — как контроль.
    """
    from app.services.landscape_impact.district_panel import load_district_panel

    balance_problems = mart.check_balance()
    notes: list[str] = []

    # Уравнение урожайности зерновых: единственное место цепи, где в данных
    # ожидался реальный статистический сигнал.
    oblast = _oblast_yield_candidates(mart)
    _adjust_family(oblast)
    panel = load_district_panel() if use_district_panel else None
    if panel is not None:
        candidates = _panel_yield_candidates(panel)
        _adjust_family(candidates)
        scope = (f"районной панели ({candidates[0].n_obs} наблюдений, "
                 f"{candidates[0].source['regions']} районов × {candidates[0].source['years']} лет)")
    else:
        candidates = oblast
        scope = f"областном ряду ({oblast[0].n_obs} разностей)"

    passed = [c for c in candidates if c.passed]
    if passed:
        # Из прошедших берём лучший по бэктесту, а не по корреляции.
        yield_link = min(passed, key=lambda c: c.mase)
        notes.append(
            f"Уравнение урожайности принято как статистическое [С] на {scope}: регрессор "
            f"{yield_link.regressor}, эластичность {yield_link.slope:.3f} "
            f"[{yield_link.jackknife_min:.3f}; {yield_link.jackknife_max:.3f}], "
            f"MASE = {yield_link.mase:.3f}."
        )
        if panel is not None and not any(c.passed for c in oblast):
            notes.append(
                "На областном ряду та же связь гейты не проходит — областных точек "
                "слишком мало; областные оценки приведены в yield_candidates_oblast."
            )
    else:
        # Ни один кандидат не прошёл — берём наиболее осмысленный (верный априорный
        # знак, лучший бэктест) и понижаем связь до экспертной.
        plausible = [c for c in candidates if c.prior_sign_ok] or candidates
        yield_link = min(plausible, key=lambda c: c.mase)
        failed = [g for g, ok in yield_link.gates.items() if not ok]
        notes.append(
            f"Уравнение урожайности НЕ прошло гейты {', '.join(failed)} на {scope} и "
            f"понижено до экспертной связи [Э] с диапазоном "
            f"[{yield_link.jackknife_min:.3f}; {yield_link.slope:.3f}; "
            f"{yield_link.jackknife_max:.3f}] из размаха jackknife. Климатическая "
            f"изменчивость статистически не отделяется от шума урожайности."
        )

    vds = np.asarray(mart.economy["agri_output"], float) / np.asarray(mart.economy["grp"], float)
    vds = vds[np.isfinite(vds)]

    calib = Calibration(
        window=(mart.years[0], mart.years[-1]),
        balance_ok=not balance_problems,
        balance_problems=balance_problems,
        structural_shares=_structural_shares(mart),
        yield_link=yield_link,
        yield_candidates=candidates,
        yield_candidates_oblast=oblast,
        direct_land_links=_direct_land_links(mart),
        sd_aggregation=_sd_aggregation(mart),
        mde=_mde(mart, sigma_fraction),
        vds_share_observed={
            "mean": round(float(vds.mean()), 4), "sd": round(float(vds.std(ddof=1)), 4),
            "min": round(float(vds.min()), 4), "max": round(float(vds.max()), 4),
        },
        notes=notes,
    )
    if balance_problems:
        calib.notes.append(
            f"Балансовое тождество нарушено в {len(balance_problems)} годах — "
            f"прогон не может быть опубликован (гейт G2)."
        )
    return calib
