"""Шаг 3 алгоритма ЗБ-УР: вариант ландшафтного планирования → земельный баланс.

Ключевое ограничение, установленное на данных: сумма семи категорий земель СКО
постоянна и равна 9804,3 тыс. га во все годы окна 2014-2024 (отклонение — ноль).
Значит, любое изменение землепользования есть перераспределение с нулевой суммой:
прирост одной категории обязан быть покрыт убылью других. Поэтому вариант ЛП
переводится не в независимые приращения площадей, а в матрицу переходов T[i][j].

Модуль работает и без растров: если по варианту переданы готовые площади категорий
(`target_areas`), матрица переходов восстанавливается из разниц. Это позволяет
запускать алгоритм до появления прогонов ЛП у смежной команды.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from app.services.landscape_impact.datamart import FORBIDDEN_TRANSITIONS, LAND_CATEGORIES

AREA_TOLERANCE = 1e-6  # тыс. га

# Доля территории, перераспределение которой между категориями земель ещё можно
# считать планировочным сценарием. Порог взят не с потолка: по фактическому
# балансу СКО за 2014-2024 максимальное перемещение за год — 3,2 % площади
# (2015-2016), а накопленное за одиннадцать лет нетто-изменение — 1,1 %.
# Десять процентов дают троекратный запас к обоим числам.
#
# Гейт нужен потому, что классы растров ЛП покрывают 99,5-99,9 % области, тогда
# как земли сельскохозяйственного назначения занимают 73,1 %: неверная таблица
# соответствия «класс -> категория» переводит леса, воды и населённые пункты в
# сельхозугодья, расчёт при этом проходит и выдаёт правдоподобные числа. Ошибка
# видна только по объёму перемещений — наивное сопоставление даёт 20,0 %.
IMPLAUSIBLE_MOVE_SHARE = 0.10


@dataclass
class TransitionMatrix:
    """Матрица переходов площадей между категориями земель, тыс. га.

    `matrix[i][j]` — площадь, переходящая из категории i в категорию j.
    Диагональ не заполняется: она подразумевается остатком.
    """

    matrix: np.ndarray
    categories: tuple[str, ...] = LAND_CATEGORIES
    violations: list[dict[str, Any]] = field(default_factory=list)

    @property
    def outflow(self) -> np.ndarray:
        return self.matrix.sum(axis=1)

    @property
    def inflow(self) -> np.ndarray:
        return self.matrix.sum(axis=0)

    @property
    def net(self) -> np.ndarray:
        """Чистое изменение площади каждой категории."""
        return self.inflow - self.outflow

    @property
    def zero_sum_error(self) -> float:
        return float(abs(self.net.sum()))

    @property
    def total_moved(self) -> float:
        return float(self.matrix.sum())

    def apply(self, base: np.ndarray, fraction: float = 1.0) -> np.ndarray:
        """Земельный баланс после реализации доли `fraction` матрицы переходов."""
        return np.asarray(base, float) + self.net * float(fraction)

    def sources_of(self, category: str) -> dict[str, float]:
        """Откуда пришёл каждый гектар прироста категории — для интерфейса."""
        j = self.categories.index(category)
        return {
            self.categories[i]: round(float(self.matrix[i, j]), 3)
            for i in range(len(self.categories)) if self.matrix[i, j] > AREA_TOLERANCE
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "categories": list(self.categories),
            "matrix": [[round(float(v), 4) for v in row] for row in self.matrix],
            "net_change": {c: round(float(v), 4) for c, v in zip(self.categories, self.net)},
            "total_moved": round(self.total_moved, 4),
            "zero_sum_error": round(self.zero_sum_error, 8),
            "violations": self.violations,
        }


def validate(matrix: np.ndarray, base_areas: np.ndarray) -> list[dict[str, Any]]:
    """Проверки матрицы переходов, входящие в гейт G2."""
    problems: list[dict[str, Any]] = []
    n = len(LAND_CATEGORIES)

    if np.any(matrix < -AREA_TOLERANCE):
        idx = np.argwhere(matrix < -AREA_TOLERANCE)
        problems.append({
            "rule": "non_negative", "severity": "blocking",
            "cells": [[LAND_CATEGORIES[i], LAND_CATEGORIES[j]] for i, j in idx],
            "message": "Отрицательные переходы недопустимы.",
        })

    for i in range(n):
        if matrix[i].sum() > base_areas[i] + AREA_TOLERANCE:
            problems.append({
                "rule": "row_sum", "severity": "blocking", "category": LAND_CATEGORIES[i],
                "outflow": round(float(matrix[i].sum()), 4),
                "available": round(float(base_areas[i]), 4),
                "message": "Из категории изымается больше, чем в ней есть.",
            })

    for src, dst in FORBIDDEN_TRANSITIONS:
        i, j = LAND_CATEGORIES.index(src), LAND_CATEGORIES.index(dst)
        if matrix[i, j] > AREA_TOLERANCE:
            problems.append({
                "rule": "forbidden_transition", "severity": "blocking",
                "transition": f"{src}->{dst}", "area": round(float(matrix[i, j]), 4),
                "message": "Переход запрещён нормативно и блокируется жёстко.",
            })

    if abs(matrix.sum(axis=0).sum() - matrix.sum(axis=1).sum()) > AREA_TOLERANCE:
        problems.append({
            "rule": "zero_sum", "severity": "blocking",
            "message": "Матрица переходов не сохраняет суммарную площадь.",
        })

    if np.any(np.abs(np.diag(matrix)) > AREA_TOLERANCE):
        problems.append({
            "rule": "diagonal", "severity": "warning",
            "message": "Диагональ матрицы должна быть нулевой: это остаток, а не переход.",
        })
    return problems


def from_target_areas(base_areas: np.ndarray, target_areas: dict[str, float]) -> TransitionMatrix:
    """Матрица переходов из желаемых площадей категорий.

    Восстановление неоднозначно: по вектору чистых изменений нельзя однозначно
    сказать, какой донор какому реципиенту отдал площадь. Используется
    пропорциональное распределение убыли по приросту — минимальное по количеству
    ненулевых ячеек и не вводящее скрытых допущений о направлении конкретных
    переходов. Когда смежная команда передаст растры вариантов, матрица будет
    строиться кросс-табуляцией пикселей и это приближение уйдёт.
    """
    base = np.asarray(base_areas, dtype=float)
    target = np.array([float(target_areas.get(c, base[i])) for i, c in enumerate(LAND_CATEGORIES)])
    delta = target - base

    # Приводим к нулевой сумме. Пользователь может задать не сбалансированный
    # набор целевых площадей — например, «добавить 100 тыс. га леса», не указав
    # источник. Невязка раскидывается по категориям пропорционально их площади,
    # и это ЯВНО помечается: иначе на экране появятся доноры, которых
    # пользователь не назначал, и он прочтёт их как результат расчёта.
    residual = float(delta.sum())
    rebalanced: Optional[dict[str, Any]] = None
    if abs(residual) > AREA_TOLERANCE:
        delta = delta - residual * base / base.sum()
        rebalanced = {
            "rule": "zero_sum_rebalance", "severity": "warning",
            "residual": round(residual, 4),
            "message": (
                f"Заданные площади не сбалансированы: суммарное изменение "
                f"{residual:+.1f} тыс. га при постоянном итоге {base.sum():.1f} тыс. га. "
                f"Невязка распределена по всем категориям пропорционально площади. "
                f"Чтобы задать источник каждого гектара явно, укажите целевые площади "
                f"всех семи категорий."
            ),
        }

    donors = np.where(delta < -AREA_TOLERANCE)[0]
    receivers = np.where(delta > AREA_TOLERANCE)[0]
    n = len(LAND_CATEGORIES)
    matrix = np.zeros((n, n), dtype=float)
    if donors.size and receivers.size:
        supply = -delta[donors]
        demand = delta[receivers]
        total = float(demand.sum())
        for di, d in enumerate(donors):
            for ri, r in enumerate(receivers):
                matrix[d, r] = supply[di] * demand[ri] / total

    # Запрещённые переходы перенаправляются на разрешённых доноров.
    for src, dst in FORBIDDEN_TRANSITIONS:
        i, j = LAND_CATEGORIES.index(src), LAND_CATEGORIES.index(dst)
        blocked = matrix[i, j]
        if blocked <= AREA_TOLERANCE:
            continue
        matrix[i, j] = 0.0
        allowed = [
            k for k in donors
            if k != i and (LAND_CATEGORIES[k], dst) not in FORBIDDEN_TRANSITIONS
            and matrix[k].sum() + AREA_TOLERANCE < base[k]
        ]
        if allowed:
            headroom = np.array([base[k] - matrix[k].sum() for k in allowed], dtype=float)
            weights = headroom / headroom.sum() if headroom.sum() > 0 else None
            if weights is not None:
                for k, w in zip(allowed, weights):
                    matrix[k, j] += blocked * w

    tm = TransitionMatrix(matrix=matrix)
    tm.violations = validate(matrix, base)
    if rebalanced is not None:
        tm.violations.append(rebalanced)
    return tm


def from_class_shares(base_areas: np.ndarray, base_shares: dict[str, float],
                      scenario_shares: dict[str, float],
                      class_map: dict[str, str]) -> TransitionMatrix:
    """Матрица переходов из долей классов растра ЛП.

    `class_map` — таблица соответствия «класс ЛП → категория земель». Это
    ключевой мост между зонированием и статистикой; он запрошен у смежной
    команды как P0 и без него блок не запускается.
    """
    unknown = sorted(set(scenario_shares) | set(base_shares))
    unknown = [c for c in unknown if c not in class_map]
    if unknown:
        raise ValueError(
            "Нет соответствия «класс ЛП → категория земель» для классов: "
            + ", ".join(unknown)
            + ". Таблица соответствия запрошена у смежной команды (P0-1)."
        )
    total = float(np.asarray(base_areas, float).sum())
    target: dict[str, float] = {c: 0.0 for c in LAND_CATEGORIES}
    for cls, share in scenario_shares.items():
        target[class_map[cls]] += float(share) * total
    tm = from_target_areas(base_areas, target)

    moved_share = tm.total_moved / total if total else 0.0
    if moved_share > IMPLAUSIBLE_MOVE_SHARE:
        worst = sorted(
            zip(LAND_CATEGORIES, tm.net), key=lambda kv: -abs(kv[1]))[:3]
        tm.violations.append({
            "rule": "implausible_class_mapping", "severity": "blocking",
            "moved_share": round(moved_share, 4),
            "limit": IMPLAUSIBLE_MOVE_SHARE,
            "largest_net_changes": {c: round(float(v), 1) for c, v in worst},
            "message": (
                f"Таблица соответствия «класс ЛП -> категория земель» перемещает "
                f"{moved_share * 100:.1f} % площади области при пороге "
                f"{IMPLAUSIBLE_MOVE_SHARE * 100:.0f} %. Это признак неверного "
                f"отображения, а не планировочного сценария: классы ЛП задают "
                f"приоритетное направление использования по всей территории, "
                f"тогда как категория земель у четверти области иная."
            ),
        })
    return tm


def realized_fraction(years_elapsed: float, years_to_full: float, shape: str = "linear") -> float:
    """Доля матрицы переходов, реализованная к году t (график внедрения [Э]).

    Горизонт и график внедрения вариантов заказчиком не переданы; по умолчанию
    принимается линейное внедрение, и это допущение помечается в интерфейсе.
    """
    if years_to_full <= 0:
        return 1.0
    t = max(0.0, float(years_elapsed))
    if shape == "linear":
        return float(min(1.0, t / years_to_full))
    if shape == "s_curve":
        # Логистика, привязанная к точке перегиба в середине горизонта.
        k = 8.0 / years_to_full
        raw = 1.0 / (1.0 + np.exp(-k * (t - years_to_full / 2.0)))
        lo = 1.0 / (1.0 + np.exp(k * years_to_full / 2.0))
        return float(min(1.0, max(0.0, (raw - lo) / (1.0 - 2 * lo))))
    raise ValueError(f"Неизвестная форма графика внедрения: {shape}")
