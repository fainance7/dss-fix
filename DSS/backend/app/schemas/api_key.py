from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ApiKeyCreate(BaseModel):
    user_id: UUID = Field(..., description="UUID пользователя, от имени которого будет работать ключ")
    name: str = Field(..., min_length=1, max_length=100, description="Человекочитаемое имя ключа")
    expires_at: Optional[datetime] = Field(None, description="Дата истечения (без TZ — UTC)")


class ApiKeyOut(BaseModel):
    """Публичный вид ключа: без plain и без хэша."""

    id: UUID
    user_id: UUID
    name: str
    is_active: bool
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreateResponse(ApiKeyOut):
    """Ответ на создание: содержит plain-ключ (единственный раз)."""

    plain_key: str = Field(..., description="Plain-ключ. Сохраните — повторно не показывается.")
