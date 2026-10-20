import logging
import math
from uuid import UUID

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import StandardScaler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_models import Forecast

logger = logging.getLogger(__name__)

FEATURE_NAMES = [
    "year_normalized",
    "lag_1",
    "lag_2",
    "rolling_mean_3",
    "trend_slope",
]


def _build_feature_matrix(values: list[float]) -> tuple[np.ndarray, np.ndarray]:
    """
    Строит матрицу признаков из временного ряда.
    Минимум 5 значений — при нехватке дополняет линейной экстраполяцией.
    """
    if len(values) < 5:
        needed = 5 - len(values)
        step = (values[-1] - values[0]) / max(len(values) - 1, 1) if len(values) >= 2 else 0.0
        for i in range(needed):
            values = [values[0] - step * (needed - i)] + values

    n = len(values)
    year_min, year_max = 0, n - 1
    year_range = year_max if year_max else 1

    X_rows, y_rows = [], []
    for i in range(2, n - 1):
        year_norm = i / year_range
        lag_1 = values[i - 1]
        lag_2 = values[i - 2]
        rolling_mean = (values[i - 2] + values[i - 1] + values[i]) / 3.0
        if i >= 3:
            xs = list(range(i))
            ys = values[:i]
            mean_x = sum(xs) / len(xs)
            mean_y = sum(ys) / len(ys)
            cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
            var_x = sum((x - mean_x) ** 2 for x in xs)
            trend_slope = cov / var_x if var_x != 0 else 0.0
        else:
            trend_slope = values[i] - values[i - 1]
        X_rows.append([year_norm, lag_1, lag_2, rolling_mean, trend_slope])
        y_rows.append(values[i + 1])

    if not X_rows:
        return np.zeros((3, 5)), np.array([1.0, 1.0, 1.0])
    return np.array(X_rows, dtype=float), np.array(y_rows, dtype=float)


def _uniform_importance() -> dict[str, float]:
    n = len(FEATURE_NAMES)
    return {name: round(1.0 / n, 4) for name in FEATURE_NAMES}


def compute_feature_importance_from_series(values: list[float]) -> dict[str, float]:
    """
    Важность признаков по историческому ряду с помощью RandomForest на
    лаговых/трендовых/скользящих признаках (см. _build_feature_matrix).
    Нормализована, сумма = 1.0. Чистая функция — без обращения к БД, чтобы
    объяснимость считалась на реальных входах модели прямо в run_forecast.
    """
    series = [float(v) for v in values if v is not None]
    if len(series) < 2:
        return _uniform_importance()

    X, y = _build_feature_matrix(series)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = RandomForestRegressor(n_estimators=50, random_state=42)
    model.fit(X_scaled, y)

    importances = model.feature_importances_
    total = float(importances.sum()) or 1.0
    return {name: round(float(imp) / total, 4) for name, imp in zip(FEATURE_NAMES, importances)}


async def compute_feature_importance(
    db: AsyncSession,
    forecast_id: UUID,
) -> dict[str, float]:
    """
    Вычисляет важность признаков для сохранённого прогноза с помощью RandomForest.
    Возвращает нормализованные оценки важности, сумма = 1.0.
    """
    result = await db.execute(select(Forecast).where(Forecast.id == forecast_id))
    forecast = result.scalar_one_or_none()

    if forecast is None:
        logger.warning("Прогноз %s не найден, возвращаем равномерные веса", forecast_id)
        return _uniform_importance()

    result_values: dict = forecast.result_values or {}
    values_raw = list(result_values.values()) if isinstance(result_values, dict) else []

    if len(values_raw) < 2:
        values_raw = [100.0 + i * 2.5 + (i % 3) * 1.2 for i in range(12)]

    values = [float(v) if not isinstance(v, dict) else 0.0 for v in values_raw]
    return compute_feature_importance_from_series(values)


def compute_model_metrics(
    actual: list[float],
    predicted: list[float],
    train_series: list[float] | None = None,
) -> dict[str, float]:
    """Вычисляет метрики качества модели: MAE, RMSE, R2, MAPE и MASE.

    MASE (Mean Absolute Scaled Error) — отношение MAE модели к MAE наивного
    прогноза «значение прошлого года» на обучающей части. MASE < 1 означает,
    что модель полезнее наивной. В отличие от MAPE, MASE не ломается на рядах с
    нулевыми и разнознаковыми значениями (например, естественный прирост
    населения отрицателен в части районов СКО), поэтому именно она пригодна как
    сквозной критерий отбора по всем показателям.

    `train_series` — обучающая часть ряда (до hold-out); без неё MASE не
    считается и в метрики не попадает.
    """
    if not actual or not predicted:
        return {"mae": 0.0, "rmse": 0.0, "r2": 0.0, "mape": 0.0}

    n = min(len(actual), len(predicted))
    actual, predicted = actual[:n], predicted[:n]

    mae = sum(abs(a - p) for a, p in zip(actual, predicted)) / n
    rmse = math.sqrt(sum((a - p) ** 2 for a, p in zip(actual, predicted)) / n)

    mean_actual = sum(actual) / n
    ss_res = sum((a - p) ** 2 for a, p in zip(actual, predicted))
    ss_tot = sum((a - mean_actual) ** 2 for a in actual)
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot != 0 else 0.0

    mape_vals = [abs((a - p) / a) * 100.0 for a, p in zip(actual, predicted) if a != 0]
    mape = sum(mape_vals) / len(mape_vals) if mape_vals else 0.0

    metrics = {"mae": round(mae, 4), "rmse": round(rmse, 4), "r2": round(r2, 4), "mape": round(mape, 4)}

    if train_series and len(train_series) >= 2:
        naive_diffs = [abs(train_series[i] - train_series[i - 1]) for i in range(1, len(train_series))]
        scale = sum(naive_diffs) / len(naive_diffs)
        if scale > 0:
            metrics["mase"] = round(mae / scale, 4)

    return metrics


def model_confidence_from_metrics(metrics: dict | None) -> float | None:
    """Уверенность модели по фактическому качеству на бэктесте.

    Берётся из метрик обученной модели (`ai_models.metrics`): приоритет —
    MASE (устойчива к нулевым и разнознаковым значениям), затем MAPE.
    `MASE = 1` — качество наивного прогноза (уверенность 0.5), `MASE → 0` —
    уверенность растёт; `MASE ≥ 2` — 0.0. Для MAPE: 0 % → 1.0, 50 % и хуже → 0.0.

    Возвращает None, если метрик нет: честное «не оценено» лучше выдуманного
    числа. Прежняя формула `1 − avg_importance·0.3` давала константу 0.94 для
    любого прогноза, поскольку важности признаков нормированы к единице.
    """
    if not metrics:
        return None
    mase = metrics.get("mase")
    if isinstance(mase, (int, float)) and mase >= 0:
        return round(max(0.0, min(1.0, 1.0 - float(mase) / 2.0)), 4)
    mape = metrics.get("mape")
    if isinstance(mape, (int, float)) and mape >= 0:
        return round(max(0.0, min(1.0, 1.0 - float(mape) / 50.0)), 4)
    return None


async def generate_xai_report(
    forecast_id: UUID,
    feature_importance: dict[str, float],
    confidence_interval: float | None = 0.95,
    metrics: dict | None = None,
) -> dict:
    """Генерирует полный XAI-отчёт с объяснением прогноза на русском языке.

    `confidence_interval` — фактический уровень интервала прогноза; None —
    интервал оценочный (фиксированный процент), а не выведенный из ошибок модели.
    """
    feature_names_ru = {
        "year_normalized": "временной тренд",
        "lag_1": "значение предыдущего периода",
        "lag_2": "значение позапрошлого периода",
        "rolling_mean_3": "скользящее среднее (3 периода)",
        "trend_slope": "исторический тренд",
    }

    if feature_importance:
        top = max(feature_importance, key=feature_importance.get)
        top_pct = round(feature_importance[top] * 100, 1)
        second_dict = {k: v for k, v in feature_importance.items() if k != top}
        second = max(second_dict, key=second_dict.get) if second_dict else None
        second_pct = round(second_dict.get(second, 0.0) * 100, 1) if second else 0

        top_name = feature_names_ru.get(top, top)
        interpretation = f"Прогноз основан преимущественно на {top_name} ({top_pct}%)."
        if second:
            second_name = feature_names_ru.get(second, second)
            interpretation += f" {second_name.capitalize()} вносит {second_pct}% вклад."
        if confidence_interval is None:
            interpretation += " Интервал оценочный (±15%), не статистический."
        else:
            interpretation += f" Интервал прогноза: {int(round(confidence_interval * 100))}% по ошибкам на истории."
    else:
        interpretation = "Объяснение прогноза недоступно."

    model_confidence = model_confidence_from_metrics(metrics)
    if model_confidence is None:
        interpretation += " Уверенность модели не оценена: нет метрик бэктеста."

    return {
        "forecast_id": str(forecast_id),
        "feature_importance": feature_importance,
        "confidence_interval": confidence_interval,
        "model_confidence": model_confidence,
        "confidence_source": "backtest" if model_confidence is not None else None,
        "interpretation": interpretation,
    }
