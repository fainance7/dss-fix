"""Районная панель для калибровки уравнения урожайности (шаг 1 алгоритма ЗБ-УР).

Областной ряд 2014-2024 даёт 10 первых разностей, и на нём связь «урожайность ~
климат» статистически не отделяется от шума (гейты G5/G6). Районные ряды
урожайности и метеостанций дают панель 13 районов × 13-14 лет — на порядок
больше наблюдений той же величины, и именно на ней связь проверяется.

Оценка ведётся в логарифмах с эффектами района и года (двусторонние
фиксированные эффекты):

    ln Y[r,t] = a[r] + c[t] + b · ln X[r,t] + e[r,t]

  * эффект района снимает постоянные различия почв и агротехники;
  * эффект года снимает общеобластные шоки (цены, сорта, господдержку) и общий
    тренд — остаётся только межрайонная аномалия климата года. Это строже
    модели «район + тренд»: часть климатического сигнала, общая для всей
    области, уходит в эффект года, поэтому коэффициент консервативен.

Проверки те же, что для областного уравнения, в панельной постановке:
  G4 — прогноз на отложенном году (эффект года для него неизвестен, поэтому
       модель «район + климат» против эталона «район»), MASE < 1;
  G5 — jackknife по районам и по годам, знак сохранён, изменение ≤ 50 %;
  G6 — перестановка значений климата между годами внутри района, затем
       поправка Бенджамини-Хохберга по семье кандидатов;
  G7 — знак совпадает с априорным.

Петропавловск исключён: посевы города — единицы тысяч гектаров, его
урожайность — шум малых площадей.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import numpy as np

_PANEL_FILE = Path(__file__).parent / "reference" / "district_panel.json"

# Ряды панели: ключ витрины → показатель indicator_values. Ключи климата совпадают
# с кандидатами областного уравнения (calibration.YIELD_CLIMATE_CANDIDATES), чтобы
# семья гипотез была одна и та же. Для sum_t10 по районам берётся ΣT(5-8): ряд
# «Сумма активных температур >10°C (Σt)» по районам испорчен (≈70 вместо ≈2300).
PANEL_INDICATORS: dict[str, str] = {
    "yield_grain": "Урожайность зерновых культур",
    "sown_grain": "Посевная площадь зерновых культур",
    "harvest_grain": "Валовой сбор зерновых культур",
    "sown_total": "Посевная площадь",
    "gtk_5_8": "Гидротермический коэффициент (ГТК)",
    "precip_5_8": "Сумма осадков за вегетационный период",
    "sum_t10": "Сумма активных температур воздуха выше 10 °C за май–август, ΣT(5-8)",
}
# Одноимённые показатели есть в нескольких направлениях; берётся профильное.
PANEL_DIRECTIONS: dict[str, str] = {
    k: ("climate" if k in ("gtk_5_8", "precip_5_8", "sum_t10") else "agriculture")
    for k in PANEL_INDICATORS
}
PANEL_UNITS: dict[str, str] = {
    "yield_grain": "ц/га", "sown_grain": "тыс. га", "harvest_grain": "т",
    "sown_total": "тыс. га", "gtk_5_8": "безразмерная", "precip_5_8": "мм",
    "sum_t10": "°C·сут",
}
EXCLUDED_REGIONS: frozenset[str] = frozenset({"г. Петропавловск"})
# Урожайность района должна сходиться со «сбор / посевная площадь». В засушливые
# годы статистика районов даёт урожайность с убранной площади (2010: 12,1 ц/га
# против 9,6 по сбору и посевам), область — с посевной. Модуль считает продукцию
# как посевы × урожайность, поэтому при расхождении сильнее допуска берётся
# урожайность с посевной площади; замены перечисляются в справочнике.
YIELD_CONSISTENCY_TOL = 0.10

PANEL_PERMUTATIONS = 2000
PANEL_SEED = 20261011


@dataclass
class DistrictPanel:
    """Районы × годы; NaN — нет утверждённого значения."""

    regions: list[str]
    years: list[int]
    series: dict[str, np.ndarray]          # ключ → массив (n_regions, n_years)
    meta: dict[str, Any] = field(default_factory=dict)

    def long(self, keys: list[str]) -> dict[str, np.ndarray]:
        """Длинный формат по наблюдениям, где все `keys` заданы и положительны."""
        r_idx, t_idx = np.meshgrid(np.arange(len(self.regions)), np.arange(len(self.years)),
                                   indexing="ij")
        cols = {k: self.series[k].ravel() for k in keys}
        mask = np.ones(r_idx.size, dtype=bool)
        for v in cols.values():
            mask &= np.isfinite(v) & (v > 0)
        out = {k: v[mask] for k, v in cols.items()}
        out["region"] = r_idx.ravel()[mask]
        out["year"] = np.asarray(self.years)[t_idx.ravel()[mask]]
        return out

    def sown_range(self) -> dict[str, dict[str, float]]:
        """Исторический размах общей посевной площади по районам, тыс. га."""
        out: dict[str, dict[str, float]] = {}
        for i, name in enumerate(self.regions):
            row = self.series["sown_total"][i]
            row = row[np.isfinite(row)]
            if row.size:
                out[name] = {"min": float(row.min()), "max": float(row.max()),
                             "last": float(row[-1]), "n": int(row.size)}
        return out


def build_panel_json(rows, *, version: str, source: str) -> dict[str, Any]:
    """Справочник панели из строк (показатель, направление, район, год, значение)."""
    by_name = {v: k for k, v in PANEL_INDICATORS.items()}
    series: dict[str, dict[str, dict[str, float]]] = {k: {} for k in PANEL_INDICATORS}
    regions, years = set(), set()
    for name, direction, region, yr, value in rows:
        key = by_name.get(name)
        if (key is None or direction != PANEL_DIRECTIONS[key]
                or region in EXCLUDED_REGIONS or value is None):
            continue
        series[key].setdefault(region, {})[str(int(yr))] = float(value)
        regions.add(region)
        years.add(int(yr))
    replaced = _reconcile_yield(series)
    return {
        "meta": {"version": version, "source": source,
                 "excluded_regions": sorted(EXCLUDED_REGIONS),
                 "yield_consistency_tol": YIELD_CONSISTENCY_TOL,
                 "yield_replaced": replaced,
                 "indicators": PANEL_INDICATORS, "directions": PANEL_DIRECTIONS,
                 "units": PANEL_UNITS},
        "regions": sorted(regions),
        "years": sorted(years),
        "series": series,
    }


def _reconcile_yield(series: dict[str, dict[str, dict[str, float]]]) -> list[dict[str, Any]]:
    """Урожайность, не сходящаяся со сбором и посевами, — на посевную площадь."""
    replaced = []
    for region, by_year in series["yield_grain"].items():
        for yr, reported in sorted(by_year.items()):
            harvest = series["harvest_grain"].get(region, {}).get(yr)
            sown = series["sown_grain"].get(region, {}).get(yr)
            if not harvest or not sown:
                continue
            implied = harvest / sown / 100.0          # т / тыс. га → ц/га
            if abs(reported / implied - 1.0) > YIELD_CONSISTENCY_TOL:
                by_year[yr] = round(implied, 2)
                replaced.append({"region": region, "year": int(yr),
                                 "reported": reported, "per_sown": round(implied, 2)})
    return replaced


@lru_cache(maxsize=1)
def _reference() -> dict[str, Any]:
    with _PANEL_FILE.open(encoding="utf-8") as fh:
        return json.load(fh)


def panel_version() -> Optional[str]:
    try:
        return str(_reference()["meta"]["version"])
    except FileNotFoundError:
        return None


def load_district_panel() -> Optional[DistrictPanel]:
    """Панель из справочника; None, если справочник не собран."""
    try:
        ref = _reference()
    except FileNotFoundError:
        return None
    regions = list(ref["regions"])
    years = [int(y) for y in ref["years"]]
    series: dict[str, np.ndarray] = {}
    for key, by_region in ref["series"].items():
        arr = np.full((len(regions), len(years)), np.nan)
        for i, r in enumerate(regions):
            for y, v in (by_region.get(r) or {}).items():
                if v is not None and int(y) in years:
                    arr[i, years.index(int(y))] = float(v)
        series[key] = arr
    return DistrictPanel(regions=regions, years=years, series=series, meta=dict(ref["meta"]))


# ───────────────────────────── оценка связи ─────────────────────────────

def _dummies(codes: np.ndarray, drop_first: bool) -> np.ndarray:
    levels = np.unique(codes)
    if drop_first:
        levels = levels[1:]
    return (codes[:, None] == levels[None, :]).astype(float)


def _residualizer(region: np.ndarray, year: Optional[np.ndarray]) -> np.ndarray:
    """Матрица M = I − Z(Z'Z)⁺Z' для фиксированных эффектов (теорема Фриша-Во-Ловелла)."""
    parts = [_dummies(region, drop_first=False)]
    if year is not None:
        parts.append(_dummies(year, drop_first=True))
    z = np.hstack(parts)
    return np.eye(z.shape[0]) - z @ np.linalg.pinv(z)


def _fe_slope(m: np.ndarray, x: np.ndarray, y: np.ndarray) -> float:
    xr, yr = m @ x, m @ y
    den = float(xr @ xr)
    return float(xr @ yr) / den if den > 0 else 0.0


def _within_permutations(x: np.ndarray, region: np.ndarray, n: int,
                         rng: np.random.Generator) -> np.ndarray:
    """n перестановок x между годами внутри каждого района, матрица (n, N)."""
    out = np.tile(x, (n, 1))
    for r in np.unique(region):
        idx = np.flatnonzero(region == r)
        if idx.size < 2:
            continue
        perm = np.argsort(rng.random((n, idx.size)), axis=1)
        out[:, idx] = x[idx][perm]
    return out


def _loyo_mase(lx: np.ndarray, ly: np.ndarray, region: np.ndarray,
               year: np.ndarray) -> float:
    """Прогноз на отложенном году: «район + климат» против эталона «район»."""
    err_model, err_naive = [], []
    for t in np.unique(year):
        tr, te = year != t, year == t
        if not te.any() or len(np.unique(region[tr])) < 2:
            continue
        m = _residualizer(region[tr], None)
        b = _fe_slope(m, lx[tr], ly[tr])
        for r in np.unique(region[te]):
            in_r = tr & (region == r)
            if not in_r.any():
                continue
            a = float(np.mean(ly[in_r] - b * lx[in_r]))
            naive = float(np.mean(ly[in_r]))
            for j in np.flatnonzero(te & (region == r)):
                err_model.append(abs(ly[j] - (a + b * lx[j])))
                err_naive.append(abs(ly[j] - naive))
    if not err_naive or np.mean(err_naive) == 0:
        return float("inf")
    return float(np.mean(err_model) / np.mean(err_naive))


def estimate_panel_link(panel: DistrictPanel, regressor: str, *, target: str = "yield_grain",
                        prior_sign: Optional[int] = None,
                        permutations: int = PANEL_PERMUTATIONS,
                        seed: int = PANEL_SEED):
    """Связь ln(target) ~ ln(regressor) на районной панели со всеми гейтами G4-G7.

    Возвращает `calibration.LinkEstimate`, чтобы дальше по цепи панельная и
    областная оценки были взаимозаменяемы.
    """
    from app.services.landscape_impact.calibration import (
        JACKKNIFE_MAX_DEVIATION, MASE_THRESHOLD, SIGNIFICANCE_ALPHA, LinkEstimate,
    )

    d = panel.long([target, regressor])
    region, year = d["region"], d["year"]
    lx, ly = np.log(d[regressor]), np.log(d[target])
    n = lx.size

    m = _residualizer(region, year)
    slope = _fe_slope(m, lx, ly)
    xr, yr = m @ lx, m @ ly
    resid = yr - slope * xr
    ss_tot = float(yr @ yr)
    r2 = 1.0 - float(resid @ resid) / ss_tot if ss_tot > 0 else 0.0
    r = float(np.corrcoef(xr, yr)[0, 1]) if n > 2 else 0.0

    rng = np.random.default_rng(seed)
    xp = _within_permutations(lx, region, permutations, rng) @ m.T     # (n_perm, N)
    den = np.sum(xp * xp, axis=1)
    den[den == 0] = np.inf
    slopes_perm = (xp @ yr) / den
    p_perm = float((np.sum(np.abs(slopes_perm) >= abs(slope) - 1e-12) + 1) / (permutations + 1))

    jk = []
    for mask in ([region != g for g in np.unique(region)]
                 + [year != t for t in np.unique(year)]):
        mj = _residualizer(region[mask], year[mask])
        jk.append(_fe_slope(mj, lx[mask], ly[mask]))
    jk_arr = np.asarray(jk, dtype=float)
    sign_stable = bool(np.all(np.sign(jk_arr) == np.sign(slope))) if slope != 0 else False
    max_dev = float(np.max(np.abs(jk_arr - slope)) / abs(slope)) if slope != 0 else float("inf")

    mase = _loyo_mase(lx, ly, region, year)
    prior_ok = True if prior_sign is None else bool(np.sign(slope) == np.sign(prior_sign))

    est = LinkEstimate(
        name=f"ln({target}) ~ ln({regressor}), районы × годы, эффекты района и года",
        regressor=regressor, slope=slope, intercept=0.0, r=r, r2=r2,
        p_permutation=p_perm, p_adjusted=p_perm,
        mase=mase, skill=1.0 - mase,
        jackknife_min=float(jk_arr.min()), jackknife_max=float(jk_arr.max()),
        jackknife_sign_stable=sign_stable, jackknife_max_deviation=max_dev,
        prior_sign_ok=prior_ok, n_obs=int(n),
    )
    est.gates = {
        "G4_skill": mase < MASE_THRESHOLD,
        "G5_jackknife": sign_stable and max_dev <= JACKKNIFE_MAX_DEVIATION,
        "G6_significance": p_perm < SIGNIFICANCE_ALPHA,
        "G7_prior_sign": prior_ok,
    }
    est.source = {
        "kind": "district_panel",
        "regions": int(len(np.unique(region))), "years": int(len(np.unique(year))),
        "panel_version": panel.meta.get("version"),
    }
    return est
