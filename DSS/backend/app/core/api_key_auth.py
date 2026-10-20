"""Авторизация по X-API-Key для intake-portal sub-app.

Подменяет :func:`app.deps.get_current_active_user` через
``intake_portal.dependency_overrides``. Возвращает обычный ``User`` со
связанными ролями — поэтому ``require_roles(...)`` остаётся совместим.

Plain-ключ НИКОГДА не логируем — только prefix хэша (``key_hash[:8]``).
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.api_key import ApiKey
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)


async def get_user_by_api_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Заменитель ``get_current_active_user`` для intake-portal sub-app.

    Алгоритм:
      1. Читаем заголовок ``X-API-Key``.
      2. Считаем SHA-256-хэш, ищем активный ``ApiKey`` с этим хэшем.
      3. Проверяем ``expires_at`` (TZ-aware UTC).
      4. Загружаем ``User`` со связанными ролями (``selectinload``).
      5. Запускаем best-effort обновление ``last_used_at`` (не блокирует ответ).
    """
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    row = (
        await db.execute(
            select(ApiKey).where(
                ApiKey.key_hash == key_hash,
                ApiKey.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    if row.expires_at is not None:
        # Сравниваем как aware-aware (если в БД naive — приведём к UTC).
        exp = row.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key expired",
            )

    user = (
        await db.execute(
            select(User)
            .options(
                selectinload(User.user_roles).selectinload(UserRole.role),
                selectinload(User.user_roles).selectinload(UserRole.region),
            )
            .where(User.id == row.user_id)
        )
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User inactive",
        )

    # best-effort обновление last_used_at в той же сессии (≤1ms).
    # Не создаём отдельную task — иначе её background-engine может пережить
    # event-loop текущего запроса (особенно в тестах).
    try:
        await db.execute(
            update(ApiKey)
            .where(ApiKey.id == row.id)
            .values(last_used_at=datetime.now(timezone.utc))
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("api_key.last_used_at update failed: %s", e)
    return user
