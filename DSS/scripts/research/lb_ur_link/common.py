"""Общие загрузчики и статистика для анализа «земельный баланс → показатели → индекс УР».

Скрипты запускаются в контейнере ai-service (см. README): нужен модуль
`app.services.landscape_impact.ur_official` и выгрузка утверждённых значений `/wlb/panel.csv`.
"""
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, "/app")

import numpy as np
import pandas as pd

from app.services.landscape_impact import ur_official as uo
from app.services.landscape_impact.calibration import benjamini_hochberg

W = "/wlb"
SEED = 20261007
N_PERM = 5000

KEYS = ["петропавл", "айыртау", "акжар", "аккайын", "есиль", "жамбыл", "кызылжар", "мамлют",
        "тайынш", "тимиряз", "уалихан", "жумабаев", "шал акын", "мусрепов"]

# Подписи 14 индикаторов методики НИР (таблица 4) — для отчёта.
LABELS = {
    "birth_rate": "Рождаемость, ‰", "death_rate": "Смертность, ‰", "migration_balance": "Сальдо миграции, ‰",
    "unemployment": "Безработица, %", "ageing_index": "Индекс старения 55-64/25-34",
    "crime_rate": "Преступность на 10 тыс.", "investment_pc": "Инвестиции на душу",
    "industry_pc": "Промышленность на душу", "agri_pc": "ВП СХ на душу", "retail_pc": "Розница на душу",
    "wage_to_subsistence": "ЗП / прожиточный минимум", "emission_sources": "Источники выбросов на площадь",
    "emissions_pc": "Выбросы на душу", "emissions_per_area": "Выбросы на площадь",
    "social": "Индекс: социальный блок", "economic": "Индекс: экономический блок",
    "ecological": "Индекс: экологический блок", "total": "Индекс УР итоговый",
}


def key(name):
    n = str(name).lower().replace("ё", "е")
    return next((k for k in KEYS if k in n), None)


def load_panel():
    d = pd.read_csv(f"{W}/panel.csv")
    d["k"] = d.region.map(key)
    return d


def district_series(d, name, direction=None):
    """Таблица район × год для показателя (только районы, level=2)."""
    x = d[(d.level == 2) & (d.indicator == name)]
    if direction:
        x = x[x.direction == direction]
    return x.pivot_table(index="k", columns="year", values="value", aggfunc="first")


def oblast_series(d, name, direction=None):
    x = d[(d.level == 1) & (d.indicator == name)]
    if direction:
        x = x[x.direction == direction]
    return x.groupby("year").value.first()


# ── Индекс УР по методике НИР ─────────────────────────────────────────

def ur_model():
    return uo.load_model()


def ur_indicator_panel(model):
    """Длинная таблица: территория (ключ) × год × индикатор → приведённое значение (до нормирования)."""
    rows = []
    for block, spec in model.blocks.items():
        for ind in spec["indicators"]:
            for t in model.territories:
                vals = uo.compute_indicator(model, block, ind, t)["transformed"]
                for y, v in zip(model.years, vals):
                    rows.append({"k": key(t), "year": y, "indicator": ind, "block": block, "value": v})
    blocks = uo.compute_all(model)
    for t, b in blocks.items():
        for name, series in b.items():
            for y, v in zip(model.years, series):
                rows.append({"k": key(t), "year": y, "indicator": name, "block": "index", "value": v})
    return pd.DataFrame(rows)


def index_with_overrides(model, year, overrides=None):
    """Блоки и итог по каждой территории за `year`; overrides = {ключ: {индикатор: приведённое значение}}.

    Порядок — по п. 12 методики: фиксированные границы, усечение 0–1, по территориям, затем
    невзвешенное среднее по 14 территориям.
    """
    overrides = overrides or {}
    i = model.years.index(year)
    per_t = {}
    for t in model.territories:
        k = key(t)
        res = {}
        for block, spec in model.blocks.items():
            acc = 0.0
            for ind, ispec in spec["indicators"].items():
                v = overrides.get(k, {}).get(ind)
                if v is None:
                    v = uo.compute_indicator(model, block, ind, t)["transformed"][i]
                nv, _ = uo.normalize(v, ispec["bounds"])
                acc += nv * ispec["weight"]
            res[block] = acc
        res["total"] = sum(res[b] for b in uo.BLOCKS) / 3
        per_t[k] = res
    oblast = {b: float(np.mean([r[b] for r in per_t.values()])) for b in (*uo.BLOCKS, "total")}
    return per_t, oblast


# ── Статистика ───────────────────────────────────────────────────────

def perm_p_corr(x, y, n=N_PERM, seed=SEED):
    """Перестановочный p для |r| (векторизовано)."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(x) < 4 or x.std() == 0 or y.std() == 0:
        return np.nan, np.nan, len(x)
    r0 = float(np.corrcoef(x, y)[0, 1])
    xn = (x - x.mean()) / np.sqrt(((x - x.mean()) ** 2).sum())
    rng = np.random.default_rng(seed)
    ys = y[np.argsort(rng.random((n, len(y))), axis=1)]
    ys = ys - ys.mean(axis=1, keepdims=True)
    r = np.abs(ys @ xn) / np.sqrt((ys * ys).sum(axis=1))
    return r0, (int((r >= abs(r0) - 1e-12).sum()) + 1) / (n + 1), len(x)


def residualize(v, controls):
    """Остаток v после МНК на константу и контрольные переменные."""
    X = np.column_stack([np.ones(len(v))] + [np.asarray(c, float) for c in controls])
    beta, *_ = np.linalg.lstsq(X, v, rcond=None)
    return v - X @ beta


def fe_projector(df):
    """Матрица M = I − D(DᵀD)⁺Dᵀ двустороннего демина (район и год) для несбалансированной панели."""
    D = np.column_stack([pd.get_dummies(df["k"]).values, pd.get_dummies(df["year"]).values]).astype(float)
    return np.eye(len(df)) - D @ np.linalg.pinv(D)


def fe_slope(df, x, y, M=None):
    M = fe_projector(df) if M is None else M
    xd, yd = M @ df[x].values.astype(float), M @ df[y].values.astype(float)
    den = float(xd @ xd)
    return (float(xd @ yd) / den if den > 0 else np.nan), xd, yd


def fe_perm_p(df, x, y, n=2000, seed=SEED, M=None):
    """p для наклона двусторонних ФЭ: перестановка x по годам внутри района (векторно)."""
    M = fe_projector(df) if M is None else M
    b0, _, yd = fe_slope(df, x, y, M)
    rng = np.random.default_rng(seed)
    xv = df[x].values.astype(float)
    Xp = np.empty((len(df), n))
    for g in df["k"].unique():
        idx = np.where(df["k"].values == g)[0]
        Xp[idx] = xv[idx][np.argsort(rng.random((len(idx), n)), axis=0)]
    Xd = M @ Xp
    b = (Xd.T @ yd) / np.einsum("ij,ij->j", Xd, Xd)
    return (int((np.abs(b) >= abs(b0) - 1e-15).sum()) + 1) / (n + 1)


def bh(p):
    return np.asarray(benjamini_hochberg(list(np.nan_to_num(p, nan=1.0))))


def r_crit(n, alpha):
    """Минимальный |r|, значимый при двустороннем alpha (t-приближение)."""
    from scipy import stats
    t = stats.t.ppf(1 - alpha / 2, n - 2)
    return float(t / np.sqrt(n - 2 + t * t))
