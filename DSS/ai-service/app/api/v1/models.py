from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.ai_models import AIModel
from app.schemas.model import ModelCreate, ModelMetricsUpdate, ModelResponse
from app.services.model_registry import (
    activate_model, deprecate_model, list_models,
    register_model, rollback_model, save_model_metrics,
)

router = APIRouter(prefix="/models", tags=["models"])


async def _get_or_404(db: AsyncSession, model_id: UUID) -> AIModel:
    result = await db.execute(select(AIModel).where(AIModel.id == model_id))
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Модель {model_id} не найдена")
    return obj


@router.get("", response_model=list[ModelResponse])
async def list_models_endpoint(
    model_type: str | None = Query(None),
    model_status: str | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
):
    models = await list_models(db, model_type=model_type, status=model_status)
    return [ModelResponse.model_validate(m) for m in models]


@router.get("/{model_id}", response_model=ModelResponse)
async def get_model(model_id: UUID, db: AsyncSession = Depends(get_db)):
    return ModelResponse.model_validate(await _get_or_404(db, model_id))


@router.post("", response_model=ModelResponse, status_code=status.HTTP_201_CREATED)
async def register_model_endpoint(body: ModelCreate, db: AsyncSession = Depends(get_db)):
    try:
        model = await register_model(
            db=db, name=body.name, version=body.version, model_type=body.model_type,
            category_id=body.category_id, parameters=body.parameters or {}, created_by=body.created_by,
        )
        await db.commit()
        await db.refresh(model)
        return ModelResponse.model_validate(model)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.put("/{model_id}/activate", response_model=ModelResponse)
async def activate_model_endpoint(model_id: UUID, db: AsyncSession = Depends(get_db)):
    try:
        model = await activate_model(db, model_id)
        await db.commit()
        await db.refresh(model)
        return ModelResponse.model_validate(model)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.put("/{model_id}/deprecate", response_model=ModelResponse)
async def deprecate_model_endpoint(model_id: UUID, db: AsyncSession = Depends(get_db)):
    try:
        model = await deprecate_model(db, model_id)
        await db.commit()
        await db.refresh(model)
        return ModelResponse.model_validate(model)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post("/{model_id}/rollback", response_model=ModelResponse)
async def rollback_model_endpoint(
    model_id: UUID,
    model_type: str = Query(...),
    indicator_id: UUID | None = Query(
        default=None,
        description="Показатель, в пределах которого выполняется откат. "
                    "Без него откат ищется по типу модели среди всех показателей.",
    ),
    db: AsyncSession = Depends(get_db),
):
    try:
        model = await rollback_model(db, model_type, indicator_id=indicator_id)
        if model is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Нет deprecated моделей типа '{model_type}'")
        await db.commit()
        await db.refresh(model)
        return ModelResponse.model_validate(model)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.put("/{model_id}/metrics", response_model=ModelResponse)
async def update_metrics_endpoint(model_id: UUID, body: ModelMetricsUpdate, db: AsyncSession = Depends(get_db)):
    try:
        model = await save_model_metrics(db, model_id, {"mae": body.mae, "rmse": body.rmse, "r2": body.r2, "mape": body.mape})
        await db.commit()
        await db.refresh(model)
        return ModelResponse.model_validate(model)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
