"""Тесты области уникальности активной модели (Д4).

`train_batch.py` держит по одной активной модели на КАЖДЫЙ показатель (на проде
это 122 активных модели). Прежняя `activate_model` депрекировала все активные
модели того же типа глобально, поэтому одна активация из UI обнуляла разметку
всего пакета.
"""
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


def _model(model_type="linear", indicator_id=None, status="draft"):
    m = MagicMock()
    m.id = uuid4()
    m.model_type = model_type
    m.indicator_id = indicator_id
    m.status = status
    m.version = "1.0"
    return m


def _db(target, previously_active):
    """select(AIModel) по id → target, второй execute → список активных."""
    db = AsyncMock()
    by_id = MagicMock()
    by_id.scalar_one_or_none.return_value = target
    actives = MagicMock()
    actives.scalars.return_value.all.return_value = previously_active
    db.execute.side_effect = [by_id, actives]
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_activation_scope_is_per_indicator():
    """Активация модели показателя A не трогает активную модель показателя B."""
    from app.services.model_registry import _scope_conditions, activate_model

    ind_a, ind_b = uuid4(), uuid4()
    target = _model(indicator_id=ind_a)
    # Мок сессии вернёт только те модели, которые «нашёл» бы фильтр области;
    # проверяем сам фильтр отдельно ниже.
    same_indicator_active = _model(indicator_id=ind_a, status="active")
    db = _db(target, [same_indicator_active])

    result = await activate_model(db, target.id)

    assert result.status == "active"
    assert same_indicator_active.status == "deprecated"

    # Условия отбора должны включать показатель, а не только тип модели.
    conds = [str(c) for c in _scope_conditions(target)]
    assert any("model_type" in c for c in conds)
    assert any("indicator_id" in c for c in conds)
    assert str(ind_b) not in " ".join(conds)


def test_scope_conditions_handle_null_indicator():
    """Модель без показателя сопоставляется с такими же (IS NULL), а не со всеми."""
    from app.services.model_registry import _scope_conditions

    conds = [str(c) for c in _scope_conditions(_model(indicator_id=None))]
    assert any("IS NULL" in c.upper() for c in conds)


@pytest.mark.asyncio
async def test_rollback_scoped_to_indicator():
    """Откат ищет кандидата в пределах показателя, если он задан."""
    from app.services.model_registry import rollback_model

    ind = uuid4()
    candidate = _model(indicator_id=ind, status="deprecated")
    current_active = _model(indicator_id=ind, status="active")

    db = AsyncMock()
    found = MagicMock()
    found.scalar_one_or_none.return_value = candidate
    actives = MagicMock()
    actives.scalars.return_value.all.return_value = [current_active]
    db.execute.side_effect = [found, actives]
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    result = await rollback_model(db, "linear", indicator_id=ind)

    assert result is candidate
    assert candidate.status == "active"
    assert current_active.status == "deprecated"


def test_llm_narrative_is_valid_model_type():
    """Языковая модель регистрируется в том же реестре (ФИИ.6 одним механизмом)."""
    from app.services.model_registry import VALID_MODEL_TYPES

    assert "llm_narrative" in VALID_MODEL_TYPES
