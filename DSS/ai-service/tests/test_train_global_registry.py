"""Согласованность отбора моделей (`scripts/train_global.py`) с инференсом.

Победитель отбора по MASE должен исполняться инференсом той же функцией, на
которой снимались метрики: раньше naive/drift/theta записывались в реестр как
`linear`, и прогноз строила не та модель, чьё качество показано.
"""
import importlib.util
import pickle
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services import forecasting

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "train_global.py"


def _load_train_global():
    spec = importlib.util.spec_from_file_location("train_global", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["train_global"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_every_candidate_is_executable_by_inference():
    tg = _load_train_global()
    assert set(tg.LOCAL_MODELS) <= set(forecasting.FORECASTERS)
    for name, fit in tg.LOCAL_MODELS.items():
        assert fit is forecasting.FORECASTERS[name]


def test_baseline_forecasters():
    values = [10.0, 12.0, 14.0, 16.0]
    assert forecasting._fit_naive(values, 2) == [16.0, 16.0]
    assert forecasting._fit_drift(values, 2) == [18.0, 20.0]
    theta = forecasting._fit_theta(values, 1)
    assert theta[0] == pytest.approx(18.0, abs=1e-6)  # ряд линейный: drift = linear


@pytest.mark.asyncio
@pytest.mark.parametrize("model_type,expected", [
    ("naive", 400.0),   # последнее значение ряда артефакта
    ("drift", 500.0),   # + средний годовой прирост 100
])
async def test_run_forecast_dispatches_baseline_types(model_type, expected):
    artifact = {"artifact_version": 2, "model_type": model_type,
                "series_tail": [100.0, 200.0, 300.0, 400.0]}
    model = MagicMock()
    model.id = uuid4()
    model.model_type = model_type
    model.indicator_id = uuid4()
    model.model_blob = pickle.dumps(artifact)
    model.metrics = None
    model.version = "2.0"
    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = model
    db.execute.return_value = res

    points = [(2022, 1.0), (2023, 1.0), (2024, 1.0)]
    with patch.object(forecasting, "get_annual_series", AsyncMock(return_value=points)), \
         patch.object(forecasting.xai, "compute_feature_importance_from_series", return_value={}), \
         patch.object(forecasting.xai, "generate_xai_report", AsyncMock(return_value={})):
        forecast = await forecasting.run_forecast(
            db=db, model_id=model.id, region_id=None, horizon_years=1,
        )
    assert forecast.result_values["2025-01-01"] == pytest.approx(expected)
