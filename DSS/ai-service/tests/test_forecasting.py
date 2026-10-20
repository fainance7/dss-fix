"""Тесты для сервиса прогнозирования ai-service."""
import math
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


def test_fit_linear_returns_increasing_trend():
    """_fit_linear с возрастающими данными возвращает возрастающий тренд."""
    from app.services.forecasting import _fit_linear

    values = [10.0 + i for i in range(12)]
    result = _fit_linear(values, 6)

    assert len(result) == 6
    for i in range(len(result) - 1):
        assert result[i] < result[i + 1], f"result[{i}]={result[i]} >= result[{i+1}]={result[i+1]}"
    assert result[0] > values[-1], f"Первое прогнозное {result[0]} должно быть > последнего исторического {values[-1]}"


def test_fit_linear_with_decreasing_data():
    """_fit_linear с убывающими данными возвращает убывающий тренд."""
    from app.services.forecasting import _fit_linear

    values = [100.0 - i * 2 for i in range(10)]
    result = _fit_linear(values, 4)

    assert len(result) == 4
    for i in range(len(result) - 1):
        assert result[i] > result[i + 1]


def test_fit_linear_single_point():
    """_fit_linear с одной точкой возвращает константу."""
    from app.services.forecasting import _fit_linear

    result = _fit_linear([42.0], 3)
    assert len(result) == 3
    assert all(isinstance(v, float) for v in result)


def test_fit_linear_empty():
    """_fit_linear с пустым рядом возвращает нули."""
    from app.services.forecasting import _fit_linear

    result = _fit_linear([], 5)
    assert result == [0.0] * 5


def test_fit_holt_winters_returns_horizon_values():
    """_fit_holt_winters с 12+ точками возвращает ровно horizon значений."""
    from app.services.forecasting import _fit_holt_winters

    values = [100.0 + 10 * math.sin(2 * math.pi * i / 12) + i * 0.5 for i in range(24)]
    result = _fit_holt_winters(values, 12)

    assert len(result) == 12
    assert all(isinstance(v, float) for v in result)


def test_fit_holt_winters_short_series():
    """_fit_holt_winters с коротким рядом (< 24 точек) не падает."""
    from app.services.forecasting import _fit_holt_winters

    values = [10.0, 11.0, 12.0, 13.0, 14.0]
    result = _fit_holt_winters(values, 6)

    assert len(result) == 6
    assert all(isinstance(v, float) for v in result)


def test_fit_holt_winters_positive_input():
    """_fit_holt_winters с положительными данными не возвращает сильно отрицательных значений."""
    from app.services.forecasting import _fit_holt_winters

    values = [50.0 + i for i in range(15)]
    result = _fit_holt_winters(values, 6)

    # Линейный тренд — все значения должны быть положительными
    assert all(v > 0 for v in result), f"Ожидались положительные значения: {result}"


def test_compute_model_metrics_mae():
    """compute_model_metrics корректно вычисляет MAE."""
    from app.services.xai import compute_model_metrics

    actual = [10.0, 20.0, 30.0, 40.0, 50.0]
    predicted = [12.0, 18.0, 31.0, 38.0, 52.0]
    expected_mae = sum(abs(a - p) for a, p in zip(actual, predicted)) / len(actual)

    metrics = compute_model_metrics(actual, predicted)

    assert "mae" in metrics and "rmse" in metrics and "r2" in metrics and "mape" in metrics
    assert abs(metrics["mae"] - expected_mae) < 0.01


def test_compute_model_metrics_perfect():
    """При идеальном прогнозе MAE=0, R2=1."""
    from app.services.xai import compute_model_metrics

    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    metrics = compute_model_metrics(values, values)

    assert metrics["mae"] == pytest.approx(0.0, abs=1e-6)
    assert metrics["r2"] == pytest.approx(1.0, abs=1e-6)


def test_compute_model_metrics_empty():
    """Пустые списки возвращают нули."""
    from app.services.xai import compute_model_metrics

    metrics = compute_model_metrics([], [])
    assert metrics == {"mae": 0.0, "rmse": 0.0, "r2": 0.0, "mape": 0.0}


def test_compute_model_metrics_rmse_gt_mae_for_outlier():
    """RMSE > MAE при наличии выброса."""
    from app.services.xai import compute_model_metrics

    actual = [10.0, 10.0, 10.0, 10.0, 10.0]
    predicted = [10.0, 10.0, 10.0, 10.0, 110.0]
    metrics = compute_model_metrics(actual, predicted)

    assert metrics["rmse"] > metrics["mae"]


@pytest.mark.asyncio
async def test_feature_importance_sums_to_one():
    """Сумма важностей признаков ≈ 1.0."""
    from app.services.xai import FEATURE_NAMES, compute_feature_importance

    mock_forecast = MagicMock()
    mock_forecast.id = uuid4()
    mock_forecast.result_values = {str(i): 100.0 + i * 2.5 for i in range(20)}

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_forecast
    mock_db.execute.return_value = mock_result

    importance = await compute_feature_importance(mock_db, mock_forecast.id)

    assert set(importance.keys()) == set(FEATURE_NAMES)
    assert abs(sum(importance.values()) - 1.0) < 0.01


@pytest.mark.asyncio
async def test_feature_importance_non_negative():
    """Все значения важностей >= 0."""
    from app.services.xai import compute_feature_importance

    mock_forecast = MagicMock()
    mock_forecast.id = uuid4()
    mock_forecast.result_values = {str(i): 50.0 + i for i in range(18)}

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_forecast
    mock_db.execute.return_value = mock_result

    importance = await compute_feature_importance(mock_db, mock_forecast.id)
    assert all(v >= 0.0 for v in importance.values())


@pytest.mark.asyncio
async def test_feature_importance_not_found_returns_uniform():
    """При отсутствии прогноза возвращаются равномерные веса."""
    from app.services.xai import FEATURE_NAMES, compute_feature_importance

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    importance = await compute_feature_importance(mock_db, uuid4())

    assert len(importance) == len(FEATURE_NAMES)
    expected = round(1.0 / len(FEATURE_NAMES), 4)
    for v in importance.values():
        assert abs(v - expected) < 0.001
