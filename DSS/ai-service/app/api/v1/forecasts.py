import logging
import math
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.ai_models import Forecast
from app.schemas.forecast import (
    ForecastCreate,
    ForecastListResponse,
    ForecastResponse,
    ForecastResultItem,
)
from app.services.forecasting import run_forecast

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/forecasts", tags=["forecasts"])


def _to_response(f: Forecast) -> ForecastResponse:
    return ForecastResponse(
        id=f.id, model_id=f.model_id, indicator_id=f.indicator_id, region_id=f.region_id,
        forecast_date=f.forecast_date, target_date_from=f.target_date_from,
        target_date_to=f.target_date_to, result_values=f.result_values,
        confidence_scores=f.confidence_scores, xai_explanation=f.xai_explanation,
        status=f.status, triggered_by=f.triggered_by, created_at=f.created_at,
    )


async def _get_or_404(db: AsyncSession, forecast_id: UUID) -> Forecast:
    result = await db.execute(select(Forecast).where(Forecast.id == forecast_id))
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Прогноз {forecast_id} не найден")
    return obj


@router.post("", response_model=ForecastResponse, status_code=status.HTTP_201_CREATED)
async def create_forecast(body: ForecastCreate, db: AsyncSession = Depends(get_db)):
    """Создаёт и запускает прогноз. Алгоритм выбирается по типу модели."""
    try:
        forecast = await run_forecast(
            db=db, model_id=body.model_id, region_id=body.region_id,
            horizon_years=body.effective_horizon_years, indicator_id=body.indicator_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        logger.error("Ошибка прогноза: %s", exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Ошибка при выполнении прогноза")
    return _to_response(forecast)


@router.get("", response_model=ForecastListResponse)
async def list_forecasts(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    region_id: Optional[UUID] = None,
    model_id: Optional[UUID] = None,
    forecast_status: Optional[str] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
):
    """Список прогнозов с фильтрацией и пагинацией."""
    q = select(Forecast)
    if region_id is not None:
        q = q.where(Forecast.region_id == region_id)
    if model_id is not None:
        q = q.where(Forecast.model_id == model_id)
    if forecast_status is not None:
        q = q.where(Forecast.status == forecast_status)

    total_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total = total_result.scalar_one()

    items_result = await db.execute(
        q.order_by(Forecast.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    items = items_result.scalars().all()

    return ForecastListResponse(
        items=[_to_response(f) for f in items],
        total=total, page=page, page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )


@router.get("/{forecast_id}", response_model=ForecastResponse)
async def get_forecast(forecast_id: UUID, db: AsyncSession = Depends(get_db)):
    return _to_response(await _get_or_404(db, forecast_id))


@router.get("/{forecast_id}/chart-data", response_model=list[ForecastResultItem])
async def get_chart_data(forecast_id: UUID, db: AsyncSession = Depends(get_db)):
    """Данные прогноза в формате для построения графиков."""
    forecast = await _get_or_404(db, forecast_id)
    if not forecast.result_values:
        return []

    result_values = forecast.result_values
    confidence_scores = forecast.confidence_scores or {}
    chart = []
    for dt_str, value in sorted(result_values.items()):
        if isinstance(value, dict):
            continue
        ci = confidence_scores.get(dt_str, {})
        v = float(value)
        low = float(ci.get("low", v * 0.85)) if isinstance(ci, dict) else v * 0.85
        high = float(ci.get("high", v * 1.15)) if isinstance(ci, dict) else v * 1.15
        chart.append(ForecastResultItem(date=dt_str, value=round(v, 4), low=round(low, 4), high=round(high, 4)))
    return chart
