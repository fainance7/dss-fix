"""Admin-only CRUD ключей для intake-portal."""
from __future__ import annotations

import hashlib
import secrets
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.rbac import require_roles
from app.database import get_db
from app.models.api_key import ApiKey
from app.models.user import User
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreateResponse, ApiKeyOut

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@router.post("", response_model=ApiKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: ApiKeyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    """Создать API-ключ. Plain-ключ возвращается ОДИН раз — далее доступен только хэш."""
    target = (
        await db.execute(select(User).where(User.id == body.user_id))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not target.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot issue API key for inactive user",
        )

    plain_key = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(plain_key.encode()).hexdigest()

    obj = ApiKey(
        key_hash=key_hash,
        user_id=body.user_id,
        name=body.name,
        is_active=True,
        expires_at=body.expires_at,
        created_by=current_user.id,
    )
    db.add(obj)
    await db.flush()

    # ВАЖНО: plain_key в audit НЕ кладём, только префикс хэша.
    await log_action(
        db,
        user_id=current_user.id,
        action="create",
        entity_type="api_key",
        entity_id=obj.id,
        details={
            "name": body.name,
            "user_id": str(body.user_id),
            "key_hash_prefix": key_hash[:8],
        },
    )

    await db.refresh(obj)
    return ApiKeyCreateResponse(
        id=obj.id,
        user_id=obj.user_id,
        name=obj.name,
        is_active=obj.is_active,
        expires_at=obj.expires_at,
        last_used_at=obj.last_used_at,
        created_at=obj.created_at,
        plain_key=plain_key,
    )


@router.get("", response_model=list[ApiKeyOut])
async def list_api_keys(
    user_id: Optional[UUID] = Query(None),
    is_active: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("admin")),
):
    q = select(ApiKey)
    if user_id is not None:
        q = q.where(ApiKey.user_id == user_id)
    if is_active is not None:
        q = q.where(ApiKey.is_active.is_(is_active))
    q = q.order_by(ApiKey.created_at.desc())
    rows = (await db.execute(q)).scalars().all()
    return [ApiKeyOut.model_validate(r) for r in rows]


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    """Soft-revoke: ставит is_active=false (запись остаётся для audit-trail)."""
    obj = (
        await db.execute(select(ApiKey).where(ApiKey.id == key_id))
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    obj.is_active = False
    await log_action(
        db,
        user_id=current_user.id,
        action="revoke",
        entity_type="api_key",
        entity_id=obj.id,
        details={"key_hash_prefix": obj.key_hash[:8]},
    )
