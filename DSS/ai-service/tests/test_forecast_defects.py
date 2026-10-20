"""Тесты дефектов прогнозного контура: Д2–Д6.

Д2 — годовой горизонт вместо месячного;
Д3 — инференс из артефакта `model_blob`, а не переобучение на лету;
Д5 — уверенность модели из метрик бэктеста, а не константа;
Д6 — запрет молчаливого синтетического ряда.
"""
import pickle
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


def _model(model_type="linear", blob=None, metrics=None, version="1.0"):
    m = MagicMock()
    m.id = uuid4()
    m.model_type = model_type
    m.indicator_id = uuid4()
    m.model_blob = blob
    m.metrics = metrics
    m.version = version
    return m


def _db_with_model(model, series_points):
    """Мок сессии: select(AIModel) → model, затем годовой ряд."""
    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = model
    db.execute.return_value = res
    db.refresh = AsyncMock()
    db.flush = AsyncMock()
    return db


# ── Д2: горизонт в годах ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forecast_keys_are_annual_from_last_fact_year():
    """Ключи прогноза — 1 января лет, следующих за последним фактическим."""
    from app.services import forecasting

    model = _model()
    db = _db_with_model(model, None)
    points = [(2020, 10.0), (2021, 11.0), (2022, 12.0), (2023, 13.0), (2024, 14.0)]

    with patch.object(forecasting, "get_annual_series", AsyncMock(return_value=points)), \
         patch.object(forecasting.xai, "compute_feature_importance_from_series", return_value={}), \
         patch.object(forecasting.xai, "generate_xai_report", AsyncMock(return_value={})):
        forecast = await forecasting.run_forecast(
            db=db, model_id=model.id, region_id=None, horizon_years=3,
        )

    assert list(forecast.result_values.keys()) == ["2025-01-01", "2026-01-01", "2027-01-01"]
    assert forecast.target_date_from.isoformat() == "2025-01-01"
    assert forecast.target_date_to.isoformat() == "2027-12-31"


@pytest.mark.asyncio
async def test_horizon_months_is_deprecated_alias_for_years():
    """horizon_months=5 даёт 5 ГОДОВЫХ шагов, а не 5 месячных."""
    from app.services import forecasting

    model = _model()
    db = _db_with_model(model, None)
    points = [(2022, 1.0), (2023, 2.0), (2024, 3.0)]

    with patch.object(forecasting, "get_annual_series", AsyncMock(return_value=points)), \
         patch.object(forecasting.xai, "compute_feature_importance_from_series", return_value={}), \
         patch.object(forecasting.xai, "generate_xai_report", AsyncMock(return_value={})):
        forecast = await forecasting.run_forecast(
            db=db, model_id=model.id, region_id=None, horizon_months=5,
        )

    assert len(forecast.result_values) == 5
    assert list(forecast.result_values)[0] == "2025-01-01"
    assert list(forecast.result_values)[-1] == "2029-01-01"


# ── Д3: инференс из model_blob ────────────────────────────────────────


def test_load_artifact_reads_supported_version():
    from app.services.forecasting import _load_artifact

    artifact = {"artifact_version": 1, "model_type": "linear", "series_tail": [1.0, 2.0, 3.0]}
    model = _model(blob=pickle.dumps(artifact))
    assert _load_artifact(model) == artifact


def test_load_artifact_rejects_unknown_version_and_garbage():
    from app.services.forecasting import _load_artifact

    assert _load_artifact(_model(blob=pickle.dumps({"artifact_version": 99}))) is None
    assert _load_artifact(_model(blob=b"\x00not-a-pickle")) is None
    assert _load_artifact(_model(blob=None)) is None


@pytest.mark.asyncio
async def test_forecast_uses_artifact_series_and_marks_no_refit():
    """Есть артефакт → прогноз считается по сохранённому ряду, refit=False."""
    from app.services import forecasting

    artifact = {"artifact_version": 1, "model_type": "linear",
                "series_tail": [100.0, 200.0, 300.0, 400.0]}
    model = _model(blob=pickle.dumps(artifact))
    db = _db_with_model(model, None)
    # Актуальный ряд намеренно другой — прогноз должен опираться на артефакт.
    points = [(2022, 1.0), (2023, 1.0), (2024, 1.0)]
    captured = {}

    async def _report(fid, fi, metrics=None, **_):
        return {}

    with patch.object(forecasting, "get_annual_series", AsyncMock(return_value=points)), \
         patch.object(forecasting.xai, "compute_feature_importance_from_series", return_value={}), \
         patch.object(forecasting.xai, "generate_xai_report", AsyncMock(side_effect=_report)):
        forecast = await forecasting.run_forecast(
            db=db, model_id=model.id, region_id=None, horizon_years=1,
        )

    meta = forecast.xai_explanation["meta"]
    assert meta["refit_on_the_fly"] is False
    assert meta["n_obs"] == 4
    # Ряд артефакта растёт на 100 за шаг → следующее значение около 500.
    assert forecast.result_values["2025-01-01"] > 450


@pytest.mark.asyncio
async def test_forecast_without_artifact_marks_refit():
    from app.services import forecasting

    model = _model(blob=None)
    db = _db_with_model(model, None)
    points = [(2022, 1.0), (2023, 2.0), (2024, 3.0)]

    with patch.object(forecasting, "get_annual_series", AsyncMock(return_value=points)), \
         patch.object(forecasting.xai, "compute_feature_importance_from_series", return_value={}), \
         patch.object(forecasting.xai, "generate_xai_report", AsyncMock(return_value={})):
        forecast = await forecasting.run_forecast(
            db=db, model_id=model.id, region_id=None, horizon_years=1,
        )

    assert forecast.xai_explanation["meta"]["refit_on_the_fly"] is True


# ── Д5: уверенность модели ────────────────────────────────────────────


def test_model_confidence_from_metrics():
    from app.services.xai import model_confidence_from_metrics

    assert model_confidence_from_metrics(None) is None
    assert model_confidence_from_metrics({}) is None
    # MASE=1 (уровень наивного прогноза) → 0.5; MASE=0 → 1.0; MASE≥2 → 0.0
    assert model_confidence_from_metrics({"mase": 1.0}) == 0.5
    assert model_confidence_from_metrics({"mase": 0.0}) == 1.0
    assert model_confidence_from_metrics({"mase": 3.0}) == 0.0
    # MAPE как запасной вариант
    assert model_confidence_from_metrics({"mape": 0.0}) == 1.0
    assert model_confidence_from_metrics({"mape": 25.0}) == 0.5
    assert model_confidence_from_metrics({"mape": 200.0}) == 0.0


@pytest.mark.asyncio
async def test_xai_report_confidence_varies_with_quality():
    """Уверенность больше не константа 0.94 — она зависит от метрик."""
    from app.services.xai import generate_xai_report

    fi = {"year_normalized": 0.2, "lag_1": 0.2, "lag_2": 0.2,
          "rolling_mean_3": 0.2, "trend_slope": 0.2}

    good = await generate_xai_report(uuid4(), fi, metrics={"mape": 5.0})
    bad = await generate_xai_report(uuid4(), fi, metrics={"mape": 40.0})
    none = await generate_xai_report(uuid4(), fi)

    assert good["model_confidence"] > bad["model_confidence"]
    assert none["model_confidence"] is None
    assert "не оценена" in none["interpretation"]
    assert good["confidence_source"] == "backtest"


# ── Д6: запрет синтетики ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_synthetic_series_forbidden_by_default():
    """Без данных и без indicator_id — ошибка, а не «успешный» прогноз."""
    from app.services import forecasting

    db = AsyncMock()
    res = MagicMock()
    res.fetchall.return_value = []
    db.execute.return_value = res

    assert forecasting.settings.ALLOW_SYNTHETIC_SERIES is False
    with pytest.raises(ValueError, match="ALLOW_SYNTHETIC_SERIES"):
        await forecasting.get_historical_series(db, region_id=None, indicator_id=None)


@pytest.mark.asyncio
async def test_synthetic_series_available_when_enabled():
    """Флаг включён (локальные тесты) — синтетика доступна явно."""
    from app.services import forecasting

    db = AsyncMock()
    res = MagicMock()
    res.fetchall.return_value = []
    db.execute.return_value = res

    with patch.object(forecasting.settings, "ALLOW_SYNTHETIC_SERIES", True):
        values = await forecasting.get_historical_series(db, region_id=None, indicator_id=None, periods=12)

    assert len(values) == 12
