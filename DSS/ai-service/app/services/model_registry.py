import logging
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_models import AIModel

logger = logging.getLogger(__name__)

VALID_MODEL_TYPES = frozenset({
    "holt_winters", "arima", "linear", "naive", "drift", "theta", "llm_narrative",
})


def _scope_conditions(model: AIModel):
    """Область уникальности активной модели.

    Активная модель единственна в пределах (тип × показатель × регион), а не
    глобально по типу: пакетное обучение (`scripts/train_batch.py`) держит по
    одной активной модели на КАЖДЫЙ показатель, и глобальная депрекация одним
    вызовом активации сносила бы все остальные (122 модели на проде).
    """
    return [
        AIModel.model_type == model.model_type,
        AIModel.indicator_id.is_(None) if model.indicator_id is None
        else AIModel.indicator_id == model.indicator_id,
    ]


async def register_model(
    db: AsyncSession,
    name: str,
    version: str,
    model_type: str,
    category_id: UUID | None,
    parameters: dict,
    created_by: UUID,
) -> AIModel:
    """Регистрирует новую версию модели в реестре."""
    if model_type not in VALID_MODEL_TYPES:
        raise ValueError(f"Недопустимый тип модели: {model_type}. Допустимые: {VALID_MODEL_TYPES}")

    model = AIModel(
        name=name, version=version, model_type=model_type,
        category_id=category_id, parameters=parameters, status="draft", created_by=created_by,
    )
    db.add(model)
    await db.flush()
    await db.refresh(model)
    logger.info("Модель зарегистрирована: id=%s name=%s version=%s", model.id, model.name, model.version)
    return model


async def activate_model(db: AsyncSession, model_id: UUID) -> AIModel:
    """Активирует модель, депрекируя предыдущую активную модель того же типа
    ДЛЯ ТОГО ЖЕ ПОКАЗАТЕЛЯ (см. `_scope_conditions`)."""
    result = await db.execute(select(AIModel).where(AIModel.id == model_id))
    model = result.scalar_one_or_none()
    if model is None:
        raise ValueError(f"Модель {model_id} не найдена")

    prev = await db.execute(
        select(AIModel).where(
            and_(*_scope_conditions(model), AIModel.status == "active", AIModel.id != model_id)
        )
    )
    for prev_model in prev.scalars().all():
        prev_model.status = "deprecated"

    model.status = "active"
    await db.flush()
    await db.refresh(model)
    logger.info("Модель активирована: id=%s", model.id)
    return model


async def deprecate_model(db: AsyncSession, model_id: UUID) -> AIModel:
    """Переводит модель в статус 'deprecated'."""
    result = await db.execute(select(AIModel).where(AIModel.id == model_id))
    model = result.scalar_one_or_none()
    if model is None:
        raise ValueError(f"Модель {model_id} не найдена")
    model.status = "deprecated"
    await db.flush()
    await db.refresh(model)
    return model


async def rollback_model(
    db: AsyncSession, model_type: str, indicator_id: UUID | None = None,
) -> AIModel | None:
    """Находит последнюю deprecated модель указанного типа (и показателя, если
    задан) и реактивирует её, депрекируя текущую активную в той же области."""
    if model_type not in VALID_MODEL_TYPES:
        raise ValueError(f"Недопустимый тип модели: {model_type}")

    conds = [AIModel.model_type == model_type]
    if indicator_id is not None:
        conds.append(AIModel.indicator_id == indicator_id)

    result = await db.execute(
        select(AIModel)
        .where(and_(*conds, AIModel.status == "deprecated"))
        .order_by(AIModel.trained_at.desc().nullslast(), AIModel.created_at.desc())
        .limit(1)
    )
    candidate = result.scalar_one_or_none()
    if candidate is None:
        return None

    active_result = await db.execute(
        select(AIModel).where(and_(*_scope_conditions(candidate), AIModel.status == "active"))
    )
    for active in active_result.scalars().all():
        active.status = "deprecated"

    candidate.status = "active"
    await db.flush()
    await db.refresh(candidate)
    logger.info("Откат модели: id=%s version=%s", candidate.id, candidate.version)
    return candidate


async def get_active_model(
    db: AsyncSession, model_type: str, indicator_id: UUID | None = None,
) -> AIModel | None:
    """Возвращает текущую активную модель указанного типа (и показателя)."""
    conds = [AIModel.model_type == model_type, AIModel.status == "active"]
    if indicator_id is not None:
        conds.append(AIModel.indicator_id == indicator_id)
    result = await db.execute(
        select(AIModel)
        .where(and_(*conds))
        .order_by(AIModel.trained_at.desc().nullslast(), AIModel.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_models(
    db: AsyncSession,
    model_type: str | None = None,
    status: str | None = None,
) -> list[AIModel]:
    """Возвращает список моделей с опциональной фильтрацией."""
    q = select(AIModel)
    conditions = []
    if model_type is not None:
        conditions.append(AIModel.model_type == model_type)
    if status is not None:
        conditions.append(AIModel.status == status)
    if conditions:
        q = q.where(and_(*conditions))
    result = await db.execute(q.order_by(AIModel.created_at.desc()))
    return list(result.scalars().all())


async def save_model_metrics(db: AsyncSession, model_id: UUID, metrics: dict) -> AIModel:
    """Обновляет метрики модели после оценки."""
    result = await db.execute(select(AIModel).where(AIModel.id == model_id))
    model = result.scalar_one_or_none()
    if model is None:
        raise ValueError(f"Модель {model_id} не найдена")
    model.metrics = metrics
    await db.flush()
    await db.refresh(model)
    return model
