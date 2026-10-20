"""Шаг 0 алгоритма ЗБ-УР: сборка витрины четырёх блоков данных за 2014-2024.

Четыре блока в терминах заказчика:
    1) природные ресурсы  — земельный баланс по 7 категориям + угодья и посевные;
    2) климат             — ГТК, суммы осадков и активных температур;
    3) социально-экономические условия — статистика области;
    4) ВРП                — валовой региональный продукт и валовая продукция СХ.
Целевая переменная — индексы устойчивого развития (4 ряда).

Источник по умолчанию — эталонный файл `reference/sko_2014_2024.json`: земельный
баланс по семи категориям и индексы устойчивого развития переданы заказчиком в
Excel и в справочнике DSS отсутствуют как показатели. Там, где ряд есть в
`indicator_values`, витрина может быть построена из БД (`load_datamart(db=...)`),
и расхождение с эталоном фиксируется, а не молча игнорируется.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

_REF_FILE = Path(__file__).parent / "reference" / "sko_2014_2024.json"

# Порядок категорий фиксирован: он же порядок строк и столбцов матрицы переходов.
LAND_CATEGORIES: tuple[str, ...] = (
    "agricultural", "settlements", "industry", "protected", "forest", "water", "reserve",
)

# Переходы, запрещённые нормативно (Земельный кодекс РК: перевод земель ООПТ и
# лесного фонда в земли сельскохозяйственного назначения). Блокируются жёстко.
FORBIDDEN_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("protected", "agricultural"),
    ("forest", "agricultural"),
    ("protected", "industry"),
    ("protected", "settlements"),
})

# Показатели DSS, соответствующие рядам витрины (для сверки с indicator_values).
DB_INDICATOR_MAP: dict[str, str] = {
    "land_balance.agricultural": "Площадь земель сельскохозяйственного назначения",
    "land_balance.reserve": "Площадь земель запаса",
    "land_use.arable": "Площадь пашни",
    "land_use.pasture": "Площадь пастбищ",
    "land_use.sown_total": "Общая посевная площадь",
    "land_use.sown_oil": "Посевная площадь масличных культур",
    "economy.agri_output": "Валовая продукция сельского хозяйства",
    "economy.grp": "Валовой региональный продукт СХ",
}


@dataclass
class DataMart:
    """Витрина четырёх блоков за окно калибровки."""

    years: list[int]
    land_balance: dict[str, np.ndarray]
    land_use: dict[str, np.ndarray]
    yields: dict[str, np.ndarray]
    climate: dict[str, np.ndarray]
    climate_norm: dict[str, float]
    economy: dict[str, np.ndarray]
    sd_index: dict[str, np.ndarray]
    total_land: float
    meta: dict[str, Any] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)

    @property
    def n_years(self) -> int:
        return len(self.years)

    def year_index(self, year: int) -> int:
        try:
            return self.years.index(year)
        except ValueError:
            raise KeyError(f"Год {year} вне окна витрины {self.years[0]}-{self.years[-1]}") from None

    def land_vector(self, year: int) -> np.ndarray:
        """Земельный баланс года как вектор в порядке LAND_CATEGORIES, тыс. га."""
        i = self.year_index(year)
        return np.array([self.land_balance[c][i] for c in LAND_CATEGORIES], dtype=float)

    def check_balance(self, tol_rel: float = 1e-3) -> list[dict[str, Any]]:
        """Гейт G2: сумма семи категорий постоянна и равна объявленному итогу."""
        problems: list[dict[str, Any]] = []
        for i, year in enumerate(self.years):
            total = sum(self.land_balance[c][i] for c in LAND_CATEGORIES)
            rel = abs(total - self.total_land) / self.total_land
            if rel > tol_rel:
                problems.append({
                    "year": year, "sum": round(total, 3),
                    "expected": self.total_land, "rel_error": round(rel, 6),
                })
        return problems


@lru_cache(maxsize=1)
def _reference() -> dict[str, Any]:
    with _REF_FILE.open(encoding="utf-8") as fh:
        return json.load(fh)


def reference_version() -> str:
    return str(_reference()["meta"]["dataset_version"])


def _to_array(values: list[Any]) -> np.ndarray:
    return np.array([np.nan if v is None else float(v) for v in values], dtype=float)


def load_reference_datamart() -> DataMart:
    """Витрина из эталонного файла — источник по умолчанию."""
    ref = _reference()
    mart = DataMart(
        years=list(ref["meta"]["years"]),
        land_balance={k: _to_array(v) for k, v in ref["land_balance"]["series"].items()},
        land_use={k: _to_array(v) for k, v in ref["land_use"]["series"].items()},
        yields={k: _to_array(v) for k, v in ref["yields"]["series"].items()},
        climate={k: _to_array(v) for k, v in ref["climate"]["series"].items()},
        climate_norm=dict(ref["climate"]["norm_2014_2024"]),
        economy={k: _to_array(v) for k, v in ref["economy"]["series"].items()},
        sd_index={k: _to_array(v) for k, v in ref["sd_index"]["series"].items()},
        total_land=float(ref["land_balance"]["total_constant"]),
        meta={
            "source": "reference",
            "dataset_version": ref["meta"]["dataset_version"],
            "sd_methodology_provided": ref["sd_index"]["methodology_provided"],
            "arbitration_required": (
                list(ref["land_use"].get("arbitration_required", []))
                + list(ref["economy"].get("arbitration_required", []))
            ),
        },
    )
    for corr in ref["land_balance"].get("corrections", []):
        mart.issues.append({"kind": "correction", "severity": "info", **corr})
    for sus in ref["land_use"].get("suspect_points", []):
        mart.issues.append({"kind": "suspect", "severity": "warning", **sus})
    for name in mart.meta["arbitration_required"]:
        mart.issues.append({
            "kind": "arbitration_required", "severity": "warning", "series": name,
            "reason": "Источники расходятся; выбран ряд dss_db. Требуется арбитраж заказчика.",
        })
    return mart


async def load_datamart(db: Optional[Any] = None, *, reconcile: bool = True) -> DataMart:
    """Витрина; при переданной сессии БД ряды сверяются с `indicator_values`.

    Сверка не подменяет эталон: расхождение записывается в `issues` и доходит до
    отчёта прогона. Молчаливая подмена источника недопустима — на этих рядах уже
    зафиксированы расхождения до 48 % (аудит статданных, разделы 2.3-2.4).
    """
    mart = load_reference_datamart()
    if db is None or not reconcile:
        return mart
    try:
        db_series = await _fetch_db_series(db, mart.years)
    except Exception as exc:  # noqa: BLE001 — сверка не должна ронять расчёт
        logger.warning("Сверка витрины с dss_db не выполнена: %s", exc)
        mart.issues.append({
            "kind": "reconcile_failed", "severity": "warning", "reason": str(exc),
        })
        return mart

    for path, indicator in DB_INDICATOR_MAP.items():
        block, name = path.split(".")
        ours = getattr(mart, block).get(name)
        theirs = db_series.get(indicator)
        if ours is None or theirs is None:
            continue
        diffs = _compare(mart.years, ours, theirs)
        if diffs:
            mart.issues.append({
                "kind": "db_mismatch", "severity": "warning", "series": path,
                "indicator": indicator, "points": diffs,
                "reason": "Ряд витрины расходится с dss_db более чем на 1 %.",
            })
    mart.meta["reconciled_with_db"] = True
    return mart


def _compare(years: list[int], ours: np.ndarray, theirs: dict[int, float],
             tol_rel: float = 0.01) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, year in enumerate(years):
        a = ours[i]
        b = theirs.get(year)
        if b is None or np.isnan(a) or a == 0:
            continue
        rel = abs(a - b) / abs(a)
        if rel > tol_rel:
            out.append({"year": year, "mart": round(float(a), 4),
                        "db": round(float(b), 4), "rel": round(float(rel), 4)})
    return out


async def _fetch_db_series(db: Any, years: list[int]) -> dict[str, dict[int, float]]:
    """Годовые областные ряды показателей из DB_INDICATOR_MAP."""
    from sqlalchemy import text

    rows = (await db.execute(text("""
        SELECT i.name,
               EXTRACT(YEAR FROM v.period_start)::int AS yr,
               v.value
        FROM indicator_values v
        JOIN indicators i ON i.id = v.indicator_id
        JOIN regions r ON r.id = v.region_id
        WHERE r.level = 1
          AND v.validation_status = 'approved'
          AND v.period_type = 'year'
          AND i.name = ANY(:names)
          AND EXTRACT(YEAR FROM v.period_start) BETWEEN :y0 AND :y1
    """), {"names": list(set(DB_INDICATOR_MAP.values())),
           "y0": years[0], "y1": years[-1]})).fetchall()

    out: dict[str, dict[int, float]] = {}
    for name, yr, value in rows:
        if value is None:
            continue
        out.setdefault(name, {})[int(yr)] = float(value)
    return out
