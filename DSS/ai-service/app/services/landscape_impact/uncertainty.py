"""Монте-Карло: свёртка четырёх источников неопределённости алгоритма ЗБ-УР.

Точечная оценка без интервала на дашборд не выводится нигде. Интервал включает:
  1) диапазоны экспертных коэффициентов [Э] — на первом уровне готовности это
     основной источник, и его вклад контролируется гейтом G10;
  2) неопределённость оценённых коэффициентов [С] — размах jackknife;
  3) неопределённость входа — точность площадей варианта ЛП;
  4) остаточную дисперсию уравнений.

Прогон воспроизводим: seed фиксирован и записывается в результат (гейт G12).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

import numpy as np

DEFAULT_DRAWS = 5000
DEFAULT_SEED = 20260909


@dataclass
class Distribution:
    """Распределение одной выходной величины с интервалами."""

    name: str
    samples: np.ndarray

    @property
    def median(self) -> float:
        return float(np.median(self.samples))

    @property
    def mean(self) -> float:
        return float(np.mean(self.samples))

    def interval(self, level: float) -> tuple[float, float]:
        a = (1.0 - level) / 2.0 * 100.0
        return (float(np.percentile(self.samples, a)),
                float(np.percentile(self.samples, 100.0 - a)))

    @property
    def p_positive(self) -> float:
        return float(np.mean(self.samples > 0.0))

    def as_dict(self) -> dict[str, Any]:
        lo80, hi80 = self.interval(0.80)
        lo95, hi95 = self.interval(0.95)
        return {
            "median": round(self.median, 6), "mean": round(self.mean, 6),
            "ci80": [round(lo80, 6), round(hi80, 6)],
            "ci95": [round(lo95, 6), round(hi95, 6)],
            "p_positive": round(self.p_positive, 4),
            "width80": round(hi80 - lo80, 6),
            "n_draws": int(self.samples.size),
        }


def triangular_sample(rng: np.random.Generator, low: float, base: float, high: float,
                      size: int) -> np.ndarray:
    """Треугольное распределение по тройке [min, base, max].

    Выбрано намеренно: оно не требует знания дисперсии (которой у экспертной
    оценки нет), уважает моду и жёстко ограничено объявленными границами — в
    отличие от нормального, у которого хвосты выходят за экспертный диапазон.
    """
    if high <= low:
        return np.full(size, float(base))
    mode = min(max(float(base), float(low)), float(high))
    return rng.triangular(float(low), mode, float(high), size)


def sample_parameters(assumption_ranges: dict[str, tuple[float, float, float]],
                      draws: int = DEFAULT_DRAWS,
                      seed: int = DEFAULT_SEED) -> list[dict[str, float]]:
    """Набор из `draws` реализаций всех параметров расчёта."""
    rng = np.random.default_rng(seed)
    columns = {
        key: triangular_sample(rng, low, base, high, draws)
        for key, (low, base, high) in assumption_ranges.items()
    }
    return [{k: float(v[i]) for k, v in columns.items()} for i in range(draws)]


def propagate(evaluate: Callable[[dict[str, float]], dict[str, float]],
              parameter_draws: Iterable[dict[str, float]]) -> dict[str, Distribution]:
    """Прогон функции расчёта по всем реализациям параметров."""
    collected: dict[str, list[float]] = {}
    for params in parameter_draws:
        out = evaluate(params)
        for key, value in out.items():
            collected.setdefault(key, []).append(float(value))
    return {k: Distribution(name=k, samples=np.asarray(v, dtype=float))
            for k, v in collected.items()}


def sensitivity(evaluate: Callable[[dict[str, float]], dict[str, float]],
                base_params: dict[str, float],
                assumption_ranges: dict[str, tuple[float, float, float]],
                target: str) -> dict[str, float]:
    """Вклад каждого экспертного коэффициента в размах целевой величины (гейт G10).

    Метод — размах по одному фактору при остальных на базовых значениях,
    нормированный на сумму размахов. Прозрачен и объясним пользователю, в
    отличие от индексов Соболя, которые на 16 параметрах потребовали бы на два
    порядка больше прогонов без выигрыша в интерпретируемости.
    """
    spans: dict[str, float] = {}
    for key, (low, _base, high) in assumption_ranges.items():
        if high <= low:
            spans[key] = 0.0
            continue
        lo_params = dict(base_params, **{key: low})
        hi_params = dict(base_params, **{key: high})
        try:
            spans[key] = abs(float(evaluate(hi_params)[target])
                             - float(evaluate(lo_params)[target]))
        except (KeyError, ValueError, ZeroDivisionError):
            spans[key] = 0.0
    total = sum(spans.values())
    if total <= 0:
        return {k: 0.0 for k in spans}
    return {k: round(v / total, 4) for k, v in spans.items()}
